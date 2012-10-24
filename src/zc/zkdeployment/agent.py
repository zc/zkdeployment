import collections
import json
import logging
import optparse
import os
import re
import shutil
import signal
import simplejson
import socket
import sys
import time
import zc.thread
import zc.zk
import zc.zkdeployment
import zim.messaging
import zktools.locking
import zookeeper

parser = optparse.OptionParser()
parser.add_option(
    '--verbose', '-v', dest='verbose', action='store_true', default=False,
    help='Log all output')
parser.add_option(
    '--run-once', '-1', dest='run_once', action='store_true',
    default=False, help='Run one deployment, and then exit')

# Hack, zktools.locking calls zookeeper.set_log_stream, which messes up zk.
zookeeper.set_log_stream = lambda f: None

DONT_CARE = object()

PXEMAC_LOCATION = 'etc/zmh/pxemac'

ROLE_LOCATION = 'etc/zim/role'

VERSION_LOCATION = 'etc/zim/host_version'

ZK_LOCATION = 'zookeeper:2181'

logger = logging.getLogger(__name__)

# The rpm name is also the name of the directory in /opt
Deployment = collections.namedtuple('Deployment',
    ['app', 'subtype', 'version', 'rpm_name', 'path', 'n'])
UnversionedDeployment = collections.namedtuple('UnversionedDeployment',
    ['app', 'rpm_name', 'path', 'n'])

versioned_app = re.compile('(\S+)-\d+([.]\d+)*$').match

def path2name(path, *extensions):
    name = path[1:].replace('/', ',')
    for ext in extensions:
        name += '.%s' % ext
    return name

def name2path(name):
    return '/'+name.replace(',', '/')

class Agent(object):

    def __init__(self, monitor_cb=None, verbose=False, run_once=False):
        self.monitor_cb = monitor_cb
        self.verbose = verbose
        self.root = os.getenv('TEST_ROOT', '/')
        self.zk = zc.zk.ZK(ZK_LOCATION)
        with open(os.path.join(self.root, PXEMAC_LOCATION), 'r') as fi:
            self.host_identifier = fi.readline().strip()

        if os.path.exists(os.path.join(self.root, VERSION_LOCATION)):
            with open(os.path.join(self.root, VERSION_LOCATION), 'r') as fi:
                version = json.loads(fi.readline().strip())
        else:
            version = None

        host_path = '/hosts/'+self.host_identifier
        if self.host_identifier in self.zk.get_children('/hosts'):
            _, meta = self.zk.get(host_path)
            if meta.get('ephemeralOwner'):
                raise ValueError('Another agent is running')
            version = self.zk.get_properties(
                '/hosts/' + self.host_identifier).get(
                'version', version)
            self.zk.delete(host_path)

        self.zk.create(
            host_path, '', zc.zk.OPEN_ACL_UNSAFE, zookeeper.EPHEMERAL)

        self.host_name = socket.getfqdn()

        host_properties = self.zk.properties(host_path)
        host_properties.update(
            name = self.host_name,
            version = version,
            )

        if os.path.exists(self._path(ROLE_LOCATION)):
            with open(self._path(ROLE_LOCATION)) as f:
                self.role = f.read().strip()
            host_properties.update(role=self.role)
        else:
            self.role = None

        if os.environ['HOME'] != '/root':
            logger.warning('Fixing incorrect home, %r.', os.environ['HOME'])
            os.environ['HOME'] = '/root'

        logger.info('Agent starting, cluster %s, host %s',
                    self.cluster_version, self.version)
        self.failing = False

        if run_once:
            self.deploy()
            time.sleep(.1)
            self.close()
        else:
            self.hosts_properties = self.zk.properties('/hosts')

            @self.hosts_properties
            def cluster_changed(properties):
                zc.thread.Thread(self.deploy)
                # import warnings; warnings.warn('Undebug')
                # self.deploy()

    def close(self):
        self.zk.close()

    @property
    def version(self):
        return self.zk.get_properties(
            '/hosts/' + self.host_identifier)['version']

    @property
    def cluster_version(self):
        return self.zk.get_properties(
            '/hosts')['version']

    def get_deployments(self):
        seen = set()
        for path in self.zk.walk():
            if self.role:
                if not path.endswith('/deploy/' + self.role):
                    if (path.endswith('/deploy/' + self.host_identifier) or
                        path.endswith('/deploy/' + self.host_name)
                        ):
                        if self.role:
                            raise ValueError(
                                'Found a host-based deployment at %s but '
                                'the host has a role, %s.' % (path, self.role))
                    continue
            else:
                if not (path.endswith('/deploy/' + self.host_identifier) or
                        path.endswith('/deploy/' + self.host_name)
                        ):
                    continue

            properties = self.zk.properties(path)
            n = properties.get('n', 1)
            path = path[:path.find('/deploy/')]
            if path in seen:
                raise ValueError(
                    "Conflicting deployments for %s. "
                    "Can't deploy to %s and %s."
                    % (path, self.host_name, self.host_identifier)
                    )
            seen.add(path)
            properties = self.zk.properties(path)
            app = properties['type'].split()
            if len(app) == 1:
                [app] = app
                subtype = None
            elif len(app) == 2:
                app, subtype = app
            else:
                raise ValueError("Invalud node type: %r" % properties['type'])

            rpm_name = app
            try:
                version = properties['version']
            except KeyError:
                try:
                    version = properties['svn_location']
                except KeyError:
                    if '-' not in app:
                        raise ValueError("No version found for " + path)
                    else:
                        app = rpm_name.rsplit('-', 1)[0]
                        version = DONT_CARE

            for i in range(n):
                yield Deployment(app, subtype, version, rpm_name, path, i)

    def get_installed_deployments(self):
        for rpm_name in os.listdir(os.path.join(self.root, 'opt')):
            script = os.path.join(
                self.root, 'opt', rpm_name, 'bin', 'zookeeper-deploy')
            if not os.path.exists(script):
                continue

            if versioned_app(rpm_name):
                app = versioned_app(rpm_name).group(1)
            else:
                app = rpm_name
            etcpath = os.path.join(self.root, 'etc', app)
            if not os.path.isdir(etcpath):
                continue

            for name in os.listdir(etcpath):
                if name.endswith('.deployed'):
                    path, n = name2path(name[:-9]).rsplit('.', 1)
                    scriptpath = os.path.join(etcpath, name[:-8]+'script')
                    if os.path.isfile(scriptpath):
                        with open(scriptpath) as f:
                            if f.read() != script:
                                continue

                    yield UnversionedDeployment(
                        app.decode('utf8'), rpm_name.decode('utf8'),
                        path, int(n))

    def _path(self, *names):
        return os.path.join(self.root, *names)

    def get_installed_opts(self):
        return set(
            name
            for name in os.listdir(self._path('opt'))
            if os.path.exists(
                self._path('opt', name, 'bin', 'zookeeper-deploy')
            ))

    def get_rpm_version(self, rpm_name):
        if not os.path.exists(self._path('opt', rpm_name)):
            return None
        if os.path.exists(self._path('opt', rpm_name, '.svn')):
            # SVN checkout, doesn't have a version
            return None
        try:
            output = zc.zkdeployment.run_command(
                    ('yum -q list installed '+rpm_name).split(),
                    verbose=self.verbose, return_output=True)
        except RuntimeError:
            return None
        for line in output.splitlines():
            if line.startswith(rpm_name):
                return line.split()[1].split('-', 1)[0]

    def get_svn_version(self, rpm_name, default=None):
        install_dir = self._path('opt', rpm_name)
        if not os.path.exists(install_dir):
            return default
        if not os.path.exists(self._path('opt', rpm_name, '.svn')):
            # SVN checkout, doesn't have a version
            return default
        for line in zc.zkdeployment.run_command(
            ['svn', 'info', install_dir],
            verbose=self.verbose, return_output=True
            ).split('\n'):
            if line.startswith('URL: '):
                return line.split()[1]

        return default

    def _uninstall(self, rpm_name):
        if os.path.exists(self._path('opt', rpm_name)):
            shutil.rmtree(self._path('opt', rpm_name))

        if versioned_app(rpm_name):
            rpm_name = versioned_app(rpm_name).group(1)

    def uninstall_rpm(self, rpm_name):
        zc.zkdeployment.run_command(['yum', '-y', 'remove', rpm_name],
                verbose=self.verbose, return_output=False)
        self._uninstall(rpm_name)

    def uninstall_something(self, opt_name):
        if os.path.exists(self._path('opt', opt_name, '.svn')):
            # Must be a checkout
            logger.info("Removing svn checkout " + opt_name)
            self._uninstall(opt_name)
        else:
            self.uninstall_rpm(opt_name)

    def remove_deployment(self, deployment):
        script = self._path(
            'opt', deployment.rpm_name, 'bin', 'zookeeper-deploy')
        zc.zkdeployment.run_command(
            [script, '-u', deployment.path, str(deployment.n)],
            verbose=self.verbose, return_output=False)
        deployed = self._path(
            'etc', deployment.app,
            path2name(deployment.path, deployment.n, "deployed"))
        if os.path.exists(deployed):
            os.remove(deployed)
        scriptpath = deployed[:-8]+'script'
        if os.path.exists(scriptpath):
            os.remove(scriptpath)

    def install_deployment(self, deployment):
        app_name = deployment.app
        if not os.path.exists(self._path('etc', app_name)):
            os.mkdir(self._path('etc', app_name))
        script = self._path(
            'opt', deployment.rpm_name, 'bin', 'zookeeper-deploy')
        command = [script, deployment.path, str(deployment.n)]
        if deployment.subtype:
            command[1:1] = ['-r', deployment.subtype]
        zc.zkdeployment.run_command(
            command, verbose=self.verbose, return_output=False)
        with open(
            self._path('etc', app_name,
                       path2name(deployment.path, deployment.n, 'script')
                       ),
            'w') as f:
            f.write(script)

    def deploy(self):
        try:
            if self.cluster_version == self.version:
                # Nothing's changed
                return
            logger.info('=' * 60)
            logger.info('Deploying version ' + str(self.cluster_version))

            deployments = list(self.get_deployments())

            logger.info("DEBUG: got deployments")

            ############################################################
            # Gather versions to deploy, checking for conflicts.  Note
            # that conflicts boil down to trying to install 2
            # different things in the same directory in /opt.
            # Otherwise, we don't really care about conflicting
            # versions.
            deploy_versions = {} # {rpm_name -> versions

            # Also gather the apps we'll have installed
            apps = set()         # {app}

            # Also gather deployments to install:
            to_deploy = set()    # {(app, path, n)}

            for deployment in deployments:
                if deployment.rpm_name in deploy_versions:
                    # Note that the rpm_name is most importantly the
                    # name of the directory in /opt.  We can't have
                    # more than one version for a given opt dir.
                    if (deployment.version !=
                            deploy_versions[deployment.rpm_name]):
                        raise ValueError(
                            "Inconsistent versions for %s. %r != %r" %
                            (deployment.rpm_name, deployment.version,
                             deploy_versions[deployment.rpm_name])
                            )
                else:
                    deploy_versions[deployment.rpm_name] = deployment.version

                apps.add(deployment.app)
                to_deploy.add((deployment.app, deployment.path, deployment.n))
            #
            ############################################################

            logger.info("DEBUG: remove old deployments")

            # Remove installed deployments that aren't in zk
            installed_apps = set()
            for deployment in sorted(self.get_installed_deployments()):
                installed_apps.add(deployment.app)
                if ((deployment.app, deployment.path, deployment.n)
                    not in to_deploy):
                    self.remove_deployment(deployment)


            logger.info("DEBUG: update software")

            # update app software, if necessary
            clean = False
            for rpm_package_name, version in sorted(deploy_versions.items()):
                rpm_version = self.get_rpm_version(rpm_package_name)
                if rpm_version != version:
                    # Note that we always get here for svn installs,
                    # since they have no rpm version.
                    rpm_name = rpm_package_name
                    if version is DONT_CARE:
                        if rpm_version is not None:
                            continue # single-version app, is already installed
                    elif version.startswith('svn+ssh://'):
                        # checkout
                        if rpm_version is not None:
                            self.uninstall_rpm(rpm_name)
                        elif self.get_svn_version(rpm_name, version) != version:
                            logger.info("Removing conflicting checkout %r != %r"
                                        % (self.get_svn_version(rpm_name),
                                           version))
                            self._uninstall(rpm_name)

                        zc.zkdeployment.run_command(
                            ['svn', 'co', version, self._path('opt', rpm_name)],
                            verbose=self.verbose, return_output=False)

                        logger.info("Build %s (%s)" % (rpm_name, version))
                        here = os.getcwd()
                        os.chdir(self._path('opt', rpm_name))
                        try:
                            zc.zkdeployment.run_command(
                                [self._path('opt', rpm_name, 'stage-build')],
                                verbose=self.verbose, return_output=False)
                        finally:
                            os.chdir(here)
                        continue
                    else:
                        rpm_name += '-' + version

                    if os.path.exists(
                        self._path('opt', rpm_package_name, '.svn')):
                        # We used svn before. Clean it up.
                        logger.info("Removing svn checkout " + rpm_package_name)
                        shutil.rmtree(self._path('opt', rpm_package_name))

                    if not clean:
                        zc.zkdeployment.run_command('yum -y clean all'.split(),
                                verbose=self.verbose, return_output=False)
                        clean = True

                    zc.zkdeployment.run_command(
                        ['yum', '-y', 'install', rpm_name],
                        verbose=self.verbose, return_output=False)

                    rpm_version = self.get_rpm_version(rpm_package_name)
                    if (rpm_version != version) and (version is not DONT_CARE):
                        raise SystemError(
                            "Failed to install %s (installed: %s)" %
                            (rpm_name, rpm_version))

            # Now update/install the needed deployments
            for deployment in sorted(deployments, key=lambda d: (d.path, d.n)):
                with zktools.locking.ZkLock(
                    self.zk, path2name(deployment.path)
                    ):
                    # The reason for the lock here is to prevent more than one
                    # deployment for an app at a time cluster wide.
                    self.install_deployment(deployment)

            # Uninstall software we don't have any more:
            for rpm_name in sorted(
                self.get_installed_opts() -
                set(deployment.rpm_name for deployment in deployments)
                ):
                self.uninstall_something(rpm_name)

            # remove etc directories we don't need any moe
            for app_name in sorted(installed_apps - apps):
                if os.path.exists(self._path('etc', app_name)):
                    # Note that the directory *should* be empty
                    try:
                        os.rmdir(self._path('etc', app_name))
                    except Exception:
                        logger.exception('Removing %r', '/etc/' + app_name)

            if os.path.exists(self._path('etc', 'init.d', 'zimagent')):
                zc.zkdeployment.run_command(
                    ['/etc/init.d/zimagent', 'restart'],
                    verbose=self.verbose, return_output=False)
            else:
                logger.warning("No zimagent. I hope you're screwing around. :)")

            self.zk.properties('/hosts/' + self.host_identifier).update(
                version=self.cluster_version)
            with open(os.path.join(self.root, VERSION_LOCATION), 'w') as fi:
                fi.write(json.dumps(self.cluster_version))

        except:
            logger.exception('deploying')
            logger.critical('FAILED deploying version %s' %
                            self.cluster_version)
            self.failing = True

            if self.monitor_cb:
                self.monitor_cb()
        else:
            logger.info('Done deploying version ' + str(self.cluster_version))
            self.failing = False

    def run(self):
        def handle_signal(*args):
            self.close()
            sys.exit(0)
        signal.signal(signal.SIGTERM, handle_signal)
        signallableblock()

def signallableblock():
    while 1:
        time.sleep(99999)

class Monitor(object):

    def __init__(self, agent):
        self.agent = agent
        self.uri = '/zkdeploy/agent'
        self.manager_uri = '/managers/zkdeploymanager'
        self.interval = 300
        self.last_good_time = time.time()
        self._state = 'INFO'


    def run(self):

        def handle_signal(signum, frame):
            self.shutdown()
            sys.exit(0)

        signal.signal(signal.SIGTERM, handle_signal)

        try:
            self.startup()
            while True:
                self.report_presence()
                self.send_state()
                time.sleep(self.interval)
        except KeyboardInterrupt:
            self.shutdown()

    def startup(self):
        self.state = 'INFO'
        uris = [self.uri]
        body = simplejson.dumps({'interval': self.interval, 'uris': uris})
        msg= 'ANNOUNCE: managing ' +  body
        zim.messaging.send_event(self.uri, self.state, msg)

    def report_presence(self):
        zim.messaging.send_event(self.manager_uri, 'INFO', 'Running')

    def send_state(self):
        if self.agent.failing:
            self.state = 'CRITICAL'
            msg = 'Host exception on deploy()'
        elif (time.time() - self.last_good_time > 900
              and self.agent.version != self.agent.cluster_version):
            self.state = 'CRITICAL'
            msg = 'Host and cluster are more than 15 minutes out of sync'
        elif self.agent.version == self.agent.cluster_version:
            self.state = 'INFO'
            msg = 'Host and cluster are in sync'
        else:
            self.state = 'WARNING'
            msg = ('Host and cluster are out of sync (host: %s, cluster: %s)' %
                (self.agent.version, self.agent.cluster_version))
        zim.messaging.send_event(self.uri, self.state, msg)

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, val):
        if val == 'INFO':
            self.last_good_time = time.time()
        self._state = val

    def shutdown(self):
        self.agent.close()
        self.state = 'INFO'
        uris = [self.uri]
        body = simplejson.dumps({'interval': self.interval, 'uris': uris})
        msg= 'ANNOUNCE: unmanaging ' +  body
        zim.messaging.send_event(self.uri, self.state, msg)

def main(args=None):
    if args is None:
        args = sys.argv[1:]

    options, args = parser.parse_args(args)
    assert not args
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(name)s %(levelname)s %(message)s'
        )
    ZK_LOCATION = 'zookeeper:2181'

    agent = Agent(verbose=options.verbose, run_once=options.run_once)

    if os.path.exists(
        os.path.join(
            os.getenv('TEST_ROOT', '/'),'etc', 'init.d', 'zimagent')
        ):
        monitor = Monitor(agent)
        if not options.run_once:
            agent.monitor_cb = monitor.send_state
            monitor.run()
    else:
        agent.run()

