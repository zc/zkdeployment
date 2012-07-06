import collections
import datetime
import logging
import optparse
import os
import shutil
import signal
import simplejson
import socket
import sys
import time
import zc.thread
import zc.time
import zc.zk
import zc.zkdeployment
import zim.config
import zim.element
import zim.messaging
import zktools.locking

DONT_CARE = object()

PXEMAC_LOCATION = 'etc/zmh/pxemac'

ROLE_LOCATION = 'etc/zim/role'

ZK_LOCATION = 'zookeeper:2181'

logger = logging.getLogger(__name__)

Deployment = collections.namedtuple('Deployment',
    ['app', 'version', 'rpm_name', 'path', 'n'])
UnversionedDeployment = collections.namedtuple('UnversionedDeployment',
    ['app', 'path', 'n'])

class Agent(object):

    def __init__(self, monitor_cb=None, verbose=False, run_once=False):
        self.monitor_cb = monitor_cb
        self.verbose = verbose
        self.root = os.getenv('TEST_ROOT', '/')
        self.zk = zc.zk.ZK(ZK_LOCATION)
        with open(os.path.join(self.root, PXEMAC_LOCATION), 'r') as fi:
            self.host_identifier = fi.readline().strip()
        if self.host_identifier not in self.zk.get_children('/hosts'):
            self.zk.create('/hosts/' + self.host_identifier, '',
                           zc.zk.OPEN_ACL_UNSAFE)
            self.zk.properties(
                '/hosts/' + self.host_identifier).update({'version': None})
        self.host_name = socket.getfqdn()
        self.properties = self.zk.properties('/hosts')
        logger.info('Agent starting, cluster %s, host %s',
                    self.cluster_version, self.version)
        self.role = None
        self.failing = False
        if os.path.exists(self._path(ROLE_LOCATION)):
            with open(self._path(ROLE_LOCATION)) as f:
                self.role = f.read().strip()

        if run_once:
            self.deploy()
        else:
            @self.properties
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

            n = self.zk.get_properties(path).get('n', 1)
            path = path[:path.find('/deploy/')]
            if path in seen:
                raise ValueError(
                    "Conflicting deployments for %s. "
                    "Can't deploy to %s and %s."
                    % (path, self.host_name, self.host_identifier)
                    )
            seen.add(path)
            app = self.zk.get_properties(path)['type'].split()[0]
            rpm_name = app
            properties = self.zk.get_properties(path)
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
                yield Deployment(app, version, rpm_name, path, i)

    def get_installed_deployments(self):
        for app in os.listdir(os.path.join(self.root, 'opt')):
            script = os.path.join(
                self.root, 'opt', app, 'bin', 'zookeeper-deploy')
            if not os.path.exists(script):
                continue
            for name in os.listdir(os.path.join(self.root, 'etc', app)):
                if name.endswith('.deployed'):
                    path, n = ('/' + name[:-9].replace(',', '/')).rsplit(
                        '.', 1)
                    yield UnversionedDeployment(
                        app.decode('utf8'), path, int(n))

    def _path(self, *names):
        return os.path.join(self.root, *names)

    def get_installed_apps(self):
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
                    verbose=self.verbose)
        except RuntimeError:
            return None
        for line in output.splitlines():
            if line.startswith(rpm_name):
                return line.split()[1]

    def _uninstall(self, rpm_name):
        if os.path.exists(self._path('opt', rpm_name)):
            shutil.rmtree(self._path('opt', rpm_name))
        if os.path.exists(self._path('etc', rpm_name)):
            # Note that the directory *should* be empty
            os.rmdir(self._path('etc', rpm_name))

    def uninstall_rpm(self, rpm_name):
        logger.info("Removing RPM " + rpm_name)
        zc.zkdeployment.run_command(['yum', '-y', 'remove', rpm_name],
                verbose=self.verbose)
        self._uninstall(rpm_name)

    def uninstall_something(self, opt_name):
        if os.path.exists(self._path('opt', opt_name, '.svn')):
            # Must be a checkout
            logger.info("Removing svn checkout " + opt_name)
            self._uninstall(opt_name)
        else:
            self.uninstall_rpm(opt_name)

    def update_deployment(self, deployment, remove=False):
        script = os.path.join(self.root, 'opt', deployment.app, 'bin',
            'zookeeper-deploy')
        cmd_list = [script]
        if remove:
            cmd_list.append('-u')
        cmd_list.append(deployment.path)
        cmd_list.append(str(deployment.n))
        action = 'Installing'
        if remove:
            action = 'Removing'
        logger.info(' '.join([action, deployment.app, deployment.path,
                              str(deployment.n)]))
        zc.zkdeployment.run_command(cmd_list,
                verbose=self.verbose)
        if remove:
            deployed = self._path(
                'etc', deployment.app,
                deployment.path[1:].replace('/', ',') +
                (".%s.deployed" % deployment.n))
            if os.path.exists(deployed):
                os.remove(deployed)

    def deploy(self):
        try:
            if self.cluster_version == self.version:
                # Nothing's changed
                return
            logger.info('=' * 60)
            logger.info('Deploying version ' + str(self.cluster_version))
            deployments = set(self.get_deployments())

            # Gather versions to deploy, checking for conflicts:
            deploy_versions = {}
            for deployment in deployments:
                if deployment.rpm_name in deploy_versions:
                    if (deployment.version !=
                            deploy_versions[deployment.rpm_name]):
                        raise ValueError(
                            "Inconsistent versions for %s. %r != %r" %
                            (deployment.rpm_name, deployment.version,
                             deploy_versions[deployment.rpm_name])
                            )
                else:
                    deploy_versions[deployment.rpm_name] = deployment.version

            # Remove installed deployments that aren't in zk
            for deployment in sorted(
                set(self.get_installed_deployments()) -
                set(UnversionedDeployment(deployment.app, deployment.path,
                                          deployment.n)
                    for deployment in deployments)
                ):
                self.update_deployment(deployment, remove=True)

            # update app versions
            clean = False
            for rpm_name, version in sorted(deploy_versions.items()):
                rpm_version = self.get_rpm_version(rpm_name)
                if rpm_version != version:
                    if version is DONT_CARE:
                        if rpm_version is not None:
                            continue # single-version app, is already installed
                    elif version.startswith('svn+ssh://'):
                        # checkout
                        if rpm_version != None:
                            self.uninstall_rpm(rpm_name)
                        logger.info("Checkout %s (%s) " % (rpm_name, version))
                        zc.zkdeployment.run_command(
                            ['svn', 'co', version, self._path('opt', rpm_name)],
                            verbose=self.verbose)
                        zc.zkdeployment.run_command(
                            [self._path('opt', rpm_name, 'stage-build')],
                            verbose=self.verbose)
                        if not os.path.exists(self._path('etc', rpm_name)):
                            os.mkdir(self._path('etc', rpm_name))
                        continue
                    else:
                        rpm_name += '-' + version

                    if not clean:
                        zc.zkdeployment.run_command('yum -y clean all'.split(),
                                verbose=self.verbose)
                        clean = True
                    logger.info("Installing RPM " + rpm_name)
                    zc.zkdeployment.run_command(
                        ['yum', '-y', 'install', rpm_name],
                        verbose=self.verbose)

            # Now update/install the needed deployments
            for deployment in sorted(deployments, key=lambda d: (d.path, d.n)):
                with zktools.locking.ZkLock(
                    self.zk, deployment.path.replace('/', ',')):
                    # The reason for the lock here is to prevent more than one
                    # deployment for an app at a time cluster wide.
                    self.update_deployment(deployment)

            # Uninstall apps we don't have any more:
            for rpm_name in sorted(
                self.get_installed_apps() -
                set(deployment.rpm_name for deployment in deployments)
                ):
                self.uninstall_something(rpm_name)

            logger.info("Restarting zimagent")
            zc.zkdeployment.run_command(['/etc/init.d/zimagent', 'restart'],
                    verbose=self.verbose)
            self.zk.properties('/hosts/' + self.host_identifier).update(
                version=self.cluster_version)
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


class Monitor(object):

    def __init__(self, agent):
        self.agent = agent
        config = zim.config.get_config()
        self.hostname = zim.config.get_config().net.hostname
        self.uri = '/zkdeploy/agent'
        self.manager_uri = '/managers/zkdeploymanager'
        self.interval = 300
        self.last_state_change = zc.time.now()
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
        elif (zc.time.now() - self.last_state_change >
                datetime.timedelta(minutes=15)
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

        if val != self._state:
            self.last_state_change = zc.time.now()
        self._state = val


    def shutdown(self):
        self.state = 'INFO'
        uris = [self.uri]
        body = simplejson.dumps({'interval': self.interval, 'uris': uris})
        msg= 'ANNOUNCE: unmanaging ' +  body
        zim.messaging.send_event(self.uri, self.state, msg)


def main():
    parser = optparse.OptionParser()
    parser.add_option(
        '--verbose', '-v', dest='verbose', action='store_true', default=False,
        help='Log all output')
    parser.add_option(
        '--run-once', '-1', dest='run_once', action='store_true',
        default=False, help='Run one deployment, and then exit')
    options, args = parser.parse_args()
    assert not args
    logging.basicConfig(
        level=logging.DEBUG if options.verbose else logging.INFO
        )
    ZK_LOCATION = 'zookeeper:2181'

    agent = Agent(verbose=options.verbose, run_once=options.run_once)
    monitor = Monitor(agent)
    if not options.run_once:
        agent.monitor_cb = monitor.send_state
        monitor.run()

