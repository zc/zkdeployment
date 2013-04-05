from zc.zkdeployment.interfaces import IVCS

import collections
import json
import logging
import optparse
import os
import Queue
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
import zope.component
import zookeeper

parser = optparse.OptionParser()
parser.add_option(
    '--verbose', '-v', dest='verbose', action='store_true', default=False,
    help='Log all output')
parser.add_option(
    '--run-once', '-1', dest='run_once', action='store_true',
    default=False, help='Run one deployment, and then exit')
parser.add_option(
    '--assert-zookeeper-address', '-z',
    help=
    "Assert that the name 'zookeeper' resolves to the given address.\n"
    "This is useful when staging to make sure you don't accidentally connect\n"
    "to a production ZooKeeper server.")

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

vcs_prefix = re.compile(r"([a-zA-Z+]+):").match

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
        with open(os.path.join(self.root, PXEMAC_LOCATION), 'r') as fi:
            self.host_identifier = fi.readline().strip()

        if os.path.exists(os.path.join(self.root, VERSION_LOCATION)):
            with open(os.path.join(self.root, VERSION_LOCATION), 'r') as fi:
                version = json.loads(fi.readline().strip())
        else:
            version = None

        host_path = '/hosts/'+self.host_identifier
        self.zk = zc.zk.ZK(ZK_LOCATION)
        try:
            if self.host_identifier in self.zk.get_children('/hosts'):
                _, meta = self.zk.get(host_path)
                if meta.get('ephemeralOwner'):
                    raise ValueError('Another agent is running')
                version = self.zk.get_properties(
                    '/hosts/' + self.host_identifier).get(
                    'version', version)
                self.zk.delete(host_path)

            self.version = version

            self.zk.create(
                host_path, '', zc.zk.OPEN_ACL_UNSAFE, zookeeper.EPHEMERAL)

            self.host_name = socket.getfqdn()

            host_properties = self.zk.properties(host_path)
            self.host_properties = host_properties
            host_properties.set(
                name = self.host_name,
                version = version,
                )

            if os.path.exists(self._path(ROLE_LOCATION)):
                with open(self._path(ROLE_LOCATION)) as f:
                    self.role = f.read().strip()
                host_properties.update(role=self.role)
            else:
                self.role = None

            if os.environ.get('HOME') != '/root':
                logger.warning(
                    'Fixing incorrect home, %r.', os.environ.get('HOME'))
                os.environ['HOME'] = '/root'

            self.hosts_properties = self.zk.properties('/hosts')
            self.cluster_version = self.hosts_properties.get('version')

            logger.info('Agent starting, cluster %s, host %s',
                        self.cluster_version, self.version)
            self.failing = False

            if run_once:
                self.deploy()
                time.sleep(.1)
                self.close()
            else:
                self.queue = queue = Queue.Queue()

                @zc.thread.Thread
                def deploy_thread():
                    while queue.get():
                        self.deploy()

                self.deploy_thread = deploy_thread

                @self.hosts_properties
                def cluster_changed(properties):
                    self.cluster_version = properties.get('version')
                    if self.cluster_version is not None:
                        queue.put(True)
                    # import warnings; warnings.warn('Undebug')
                    # self.deploy()

        except:
            self.close()
            raise

    def close(self):
        if hasattr(self, 'deploy_thread'):
            self.queue.put(False)
            self.deploy_thread.join(33)
        if self.zk.handle is not None:
            self.zk.close()

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

    def is_under_vc(self, *path):
        path = self._path(*path)
        for _, vcs in zope.component.getUtilitiesFor(IVCS):
            if vcs.is_under_vc(path):
                return True
        return False

    def get_rpm_version(self, rpm_name):
        if not os.path.exists(self._path('opt', rpm_name)):
            return None

        if self.is_under_vc('opt', rpm_name):
            return None # Checkout, no rpm version

        try:
            output = zc.zkdeployment.run_command(
                    ('yum -q list installed '+rpm_name).split(),
                    verbose=self.verbose, return_output=True)
        except RuntimeError:
            return None

        for line in output.splitlines():
            if line.startswith(rpm_name):
                return line.split()[1].split('-', 1)[0]

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
        if self.is_under_vc('opt', opt_name):
            # Must be a checkout
            logger.info("Removing checkout " + opt_name)
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
            cluster_version = self.cluster_version
            if cluster_version is None:
                logger.warning('Not deploying because cluster version is None')
                return # all stop

            # Clear error, if necessary:
            if 'error' in self.host_properties:
                props = dict(self.host_properties)
                del props['error']
                self.host_properties.set(props)

            if cluster_version == self.version:
                return # Nothing's changed
            logger.info('=' * 60)
            logger.info('Deploying version ' + str(cluster_version))

            try:
                # We often hang here agthering deployment info.
                # Try setting an alarm here ti exit if we take too long.
                # This probably won't work because we'll probably
                # be in the bowels of C where signals have no effect,
                # but that would at least be informative.
                signal.alarm(99)
                deployments = list(self.get_deployments())
            finally:
                signal.alarm(0)

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
                if self.cluster_version is None:
                    raise Abandon
                installed_apps.add(deployment.app)
                if ((deployment.app, deployment.path, deployment.n)
                    not in to_deploy):
                    self.remove_deployment(deployment)


            logger.info("DEBUG: update software")

            # update app software, if necessary
            clean = False
            for rpm_package_name, version in sorted(deploy_versions.items()):
                if self.cluster_version is None:
                    raise Abandon
                rpm_version = self.get_rpm_version(rpm_package_name)
                if rpm_version != version:
                    # Note that we always get here for VCS installs,
                    # since they have no rpm version.
                    rpm_name = rpm_package_name
                    if version is DONT_CARE:
                        if rpm_version is not None:
                            continue # single-version app, is already installed
                    else:
                        m = vcs_prefix(version)
                        if m:
                            install_dir = self._path('opt', rpm_name)
                            vcs = zope.component.getUtility(IVCS, m.group(1))
                            if rpm_version is not None:
                                self.uninstall_rpm(rpm_name)
                            else:
                                if os.path.exists(install_dir):
                                    if vcs.get_version(
                                        install_dir, self.verbose) != version:
                                        logger.info(
                                            "Removing conflicting checkout"
                                            " %r != %r"
                                            % (vcs.get_version(install_dir,
                                                               self.verbose),
                                               version))
                                        self._uninstall(rpm_name)

                            vcs.update(install_dir, version, self.verbose)

                            logger.info("Build %s (%s)" % (rpm_name, version))
                            here = os.getcwd()
                            os.chdir(self._path('opt', rpm_name))
                            try:
                                zc.zkdeployment.run_command(
                                    [self._path(
                                        'opt', rpm_name, 'stage-build')],
                                    verbose=self.verbose, return_output=False)
                            finally:
                                os.chdir(here)
                            continue
                        else:
                            rpm_name += '-' + version

                    if self.is_under_vc('opt', rpm_package_name):
                        # We used VCS before. Clean it up.
                        logger.info("Removing checkout " + rpm_package_name)
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
                        if rpm_version:
                            # Yum is a disaster. Try downgrade
                            zc.zkdeployment.run_command(
                                ['yum', '-y', 'downgrade', rpm_name],
                                verbose=self.verbose, return_output=False)
                            rpm_version = self.get_rpm_version(rpm_package_name)
                        if rpm_version != version:
                            raise SystemError(
                                "Failed to install %s (installed: %s)" %
                                (rpm_name, rpm_version))

            # Now update/install the needed deployments
            for deployment in sorted(deployments, key=lambda d: (d.path, d.n)):
                with zktools.locking.ZkLock(
                    self.zk, path2name(deployment.path)
                    ):
                    # The reason for the lock here is to prevent
                    # more than one deployment for an app at a
                    # time cluster wide.
                    if self.cluster_version is None:
                        raise Abandon

                    try:
                        self.install_deployment(deployment)
                    except:
                        # We errored deploying.  We don't want the
                        # error to propigate to other nodes, so we set
                        # the cluster version to None.  We do this
                        # before releasng the lock, and we do it later
                        # as well to handle other failures.
                        self.hosts_properties.update(version=None)
                        raise

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

            self.version = cluster_version
            self.zk.properties('/hosts/' + self.host_identifier).update(
                version=cluster_version)
            with open(os.path.join(self.root, VERSION_LOCATION), 'w') as fi:
                fi.write(json.dumps(cluster_version))

        except Abandon:
            logger.warning('Abandoning deployment because cluster version '
                           'is None')
        except:
            self.hosts_properties.update(version=None)
            self.host_properties.update(error=str(sys.exc_info()[1]))
            logger.exception('deploying')
            logger.critical('FAILED deploying version %s', cluster_version)
            self.failing = True

            if self.monitor_cb:
                self.monitor_cb()
        else:
            logger.info('Done deploying version %s', + cluster_version)
            self.failing = False

    def run(self):
        def handle_signal(*args):
            self.close()
            sys.exit(0)
        signal.signal(signal.SIGTERM, handle_signal)
        signallableblock()

class Abandon(Exception):
    "A deployment is abandoned due to a cluster deployment error"

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

def register():
    import zc.zkdeployment.svn
    zc.zkdeployment.svn.register()

def main(args=None):
    if args is None:
        args = sys.argv[1:]

    register()

    options, args = parser.parse_args(args)
    assert not args

    if (options.assert_zookeeper_address and
        socket.gethostbyname('zookeeper') != options.assert_zookeeper_address
        ):
        raise AssertionError("Invalid zookeeper address",
                             socket.gethostbyname('zookeeper'),
                             options.assert_zookeeper_address)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(name)s %(levelname)s %(message)s'
        )
    ZK_LOCATION = 'zookeeper:2181'

    agent = Agent(verbose=options.verbose, run_once=options.run_once)
    try:
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
    finally:
        agent.close()
