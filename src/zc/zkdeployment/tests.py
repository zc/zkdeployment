##############################################################################
#
# Copyright (c) 2012 Zope Corporation. All Rights Reserved.
#
# This software is subject to the provisions of the Zope Visible Source
# License, Version 1.0 (ZVSL).  A copy of the ZVSL should accompany this
# distribution.
#
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################
"""Unit tests

"""
__docformat__ = "reStructuredText"

import doctest
import logging
import manuel.capture
import manuel.doctest
import manuel.footnote
import manuel.testing
import mock
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import unittest
import zc.zk.testing
import zc.zkdeployment.agent
import zim.config # XXX zim duz way too much on import. :( Do it now.
import zope.component.testing
import zope.testing.setupstack
import zope.testing.renormalizing

start_with_digit = re.compile('\d').match
stage_build_path = re.compile('(/opt/[-\w]+/stage-build)$').search
role_controller_script = re.compile(r'/opt/\w+(-cf)?-(\d+)-(\d+)-rc/bin/'
                                    r'(start|end)ing-deployments$').search

class TestRecipe:

    def __init__(self, buildout, name, options):
        path, index = name.rsplit('.')
        path = '/'+name.replace(',', '/')
        zk = zc.zk.ZK('zookeeper:2181')
        options.update(zk.properties(path))
        if options.get('fail-in-init'):
            raise ValueError("fail-in-init", options.get('fail-in-init'))
        self.options = options

    def install(self):
        if options.get('fail-in-install'):
            raise ValueError("fail-in-install", options.get('fail-in-install'))
        return ()

    def update(self):
        if options.get('fail-in-update'):
            raise ValueError("fail-in-update", options.get('fail-in-update'))
        return ()

def assert_(cond, mess='Assertion Failed'):
    if not cond:
        raise AssertionError(mess)

initial_file_system = dict(
    etc = dict(
        zmh = dict(
            pxemac = '424242424242\n',
            ),
        zim = dict(
            host_version = '1',
            ),
        z4mmonitor = {
            'cust,someapp,monitor.0.deployed': '',
            },
        z4m = {
            'cust,someapp,cms.0.deployed': '',
            'cust2,someapp,cms.0.deployed': '',
            },
        **{
            'init.d': dict(zimagent=''),
            }),
    opt = dict(
        z4mmonitor = dict(
            bin={'zookeeper-deploy': ''},
            version = '1.1.0',
            ),
        z4m = dict(
            bin={'zookeeper-deploy': ''},
            version = '1.0.0',
            ),
        )
)

def buildfs(tree, base=''):
    for name, value in tree.iteritems():
        path = os.path.join(base, name)
        if isinstance(value, dict):
            if not os.path.isdir(path):
                os.mkdir(path)
            buildfs(value, path)
        else:
            if os.path.exists(path):
                os.remove(path)
            if isinstance(value, tuple):
                with open(path, 'w') as f:
                    f.write(value[0])
                os.chmod(path, value[1])
            else:
                with open(path, 'w') as f:
                    f.write(value)

initial_tree = """
/hosts
  version = 1

/cust
  /someapp
    /cms : z4m
       version = '1.0.0'
       /deploy
          /424242424242
    /monitor : z4mmonitor
       version = '1.1.0'
       /deploy
          /424242424242
/cust2
  /someapp
    /cms : z4m
       version = '1.0.0'
       /deploy
          /424242424242
"""

class FakeSubprocess(object):
    def __init__(self, stdoutdata='', stderrdata='', returncode=0, duration=0):
        self.stdoutdata = stdoutdata
        self.stderrdata = stderrdata
        self.returncode = returncode
        self.duration = duration

    def communicate(self):
        if self.duration != 0:
            time.sleep(self.duration)
        return (self.stdoutdata, self.stderrdata)

    def terminate(self):
        print 'Terminating process'


def subprocess_popen(args, stdout=None, stderr=None):
    try:
        if stderr is not subprocess.STDOUT:
            raise TypeError('bad subprocess call')
        if stdout is None:
            stdout = sys.stdout
        args = list(args)
        command = args.pop(0)
        if 'zookeeper-deploy' in command:
            app = command.split('/')[-3]
            print app+"/bin/zookeeper-deploy", ' '.join(args)
            if args[0] == '-u':
               args.pop(0)
               uninstall = True
               if app == 'uncranky':
                   print >> stdout, "waaaaaaaaaaaa I don't wanna go"
                   return FakeSubprocess(returncode=1)
            else:
               uninstall = False
               if app == 'cranky':
                   print >> stdout, 'waaaaaaaaaaaa'
                   return FakeSubprocess(returncode=1)
               elif app == 'tooslow':
                   return FakeSubprocess(returncode=1, duration=999)

            if zc.zkdeployment.agent.versioned_app(app):
                app = zc.zkdeployment.agent.versioned_app(app).group(1)

            if args[0] == '-r':
                args = args[2:]

            deployed = os.path.join(
                'etc', app,
                args[0][1:].replace('/', ',')+'.'+args[1]+'.deployed')

            if uninstall:
                if os.path.exists(deployed):
                    os.remove(deployed)
            else:
                try:
                    open(deployed, 'w').close()
                except Exception:
                    print "Couldn't create %r" % deployed

        elif command == 'yum':
            package = args[-1]
            command = args[0]
            if args[0] == '-y' or args[0] == '-q':
                command = args[1]

            print 'yum', ' '.join(args)
            if command in ('install', 'downgrade'):
                package, version = package.rsplit('-', 1)
                if not start_with_digit(version):
                    print >> stdout, "Error: Couldn't find package %s-%s" % (
                        package, version)
                    return FakeSubprocess(returncode=1)
                if package == 'z4m' and version >= '4.0.0':
                    package += '-' + version
                elif version == '666':
                    print >> stdout, "Error: Couldn't find package %s-%s" % (
                        package, version)
                    return FakeSubprocess(returncode=0)

                vpath = os.path.join('opt', package, 'version')
                if os.path.exists(vpath):
                    oldv = open(vpath).read()
                else:
                    oldv = None
                if (oldv is None
                    or
                    (command == 'install' and version >= oldv)
                    or
                    (command == 'downgrade' and version <= oldv)
                    ):
                    if package.endswith('-rc'):
                        _, src, erc, _ = package.rsplit('-', 3)
                        bin = {
                            'starting-deployments': '',
                            'ending-deployments': '',
                            }
                    else:
                        bin = {
                            'zookeeper-deploy': '',
                            }
                    buildfs(
                        dict(
                            opt={
                                package: dict(
                                    bin=bin,
                                    version=version+'-1',
                                    **{'stage-build': ''}
                                    )},
                            ))
            elif command == 'remove':
                if package == 'pywrite':
                    print >> stdout, "Error: No match for argument: pywrite"
                else:
                    shutil.rmtree('opt/%s' % package)
            elif command == 'clean':
                print >> stdout, 'Loaded plugins: downloadonly'
                print >> stdout, 'Cleaning up Everything'
                print >> stdout, 'Cleaning up list of fastest mirrors'
            elif command == 'list':
                if '-q' not in args:
                    print >> stdout, 'Loaded plugins: downloadonly'
                path = os.path.join('opt', package, 'version')
                if os.path.exists(path):
                    print >> stdout, 'Installed Packages'
                    version = open(path).read()
                    print >> stdout, package, '\t', version, '\t', 'installed'
                else:
                    print >> stdout, 'Error: No matching Packages to list'
                    return FakeSubprocess(returncode=1)
            else:
                raise ValueError(command)

        elif command == 'svn':
            if args[0] == 'co' and len(args) == 3:
                svn_path = os.path.join(args[2], '.svn')
                url_path = os.path.join(args[2], 'url')
                if os.path.exists(url_path):
                    with open(url_path) as f:
                        if f.read() != args[1]:
                            raise ValueError('bad svn url')
                if not os.path.exists(svn_path):
                    os.makedirs(svn_path)
                with open(url_path, 'w') as f:
                    f.write(args[1])
                checkout_software(args[2])
            if args[0] == 'info':
                with open(os.path.join(args[1], 'url')) as f:
                    print >> stdout, info_template % f.read()

        elif command == 'git':
            if args[0] == 'clone' and len(args) == 3:
                git_path = os.path.join(args[2], '.git')
                os.makedirs(git_path)
                checkout_software(args[2])

        elif command == 'chmod':
            if args != ['-R', 'a+rX', '.']:
                raise ValueError("Unexpected arguments for chmod")
            print command, ' '.join(args)

        elif command == '/etc/init.d/zimagent':
            print command, ' '.join(args)

        elif stage_build_path(command) and not args:
            assert_(os.getcwd() == os.path.dirname(command))
            print stage_build_path(command).group(1)

        elif role_controller_script(command):
            print command, ' '.join(args)
            m = role_controller_script(command)
            if m.group(4) == "start":
                rc = int(m.group(2))
                if m.group(1):
                    # Trigger to hint that something else in the cluster
                    # causes /hosts version to be set to None.
                    zk = zc.zk.ZK("zookeeper:2181")
                    zk.properties("/hosts").update(version=None)
                    print "*** Simulating deployment failure on another host"
            else:
                rc = int(m.group(3))
            if rc:
                print "Busted!"
            return FakeSubprocess(returncode=rc)

        else:
            raise ValueError("No such command %s %r" % (command, args))
    except:
        traceback.print_exc()
        return FakeSubprocess(returncode=1)
    else:
        return FakeSubprocess(returncode=0)

def checkout_software(path):
    """Initialize software checkout aside from VCS-specific details."""
    bin_path = os.path.join(path, 'bin')
    def bin(name):
        return os.path.join(bin_path, name)
    if not os.path.exists(bin_path):
        os.makedirs(bin_path)
    if path.endswith("-rc"):
        with open(bin('starting-deployments'), 'w'): pass
        with open(bin('ending-deployments'), 'w'): pass
    else:
        with open(bin('zookeeper-deploy'), 'w'): pass
    with open(os.path.join(path, 'stage-build'), 'w'): pass

info_template = """Path: .
URL: %s
Repository Root: svn+ssh://svn.zope.com/repos/main
Repository UUID: 32cb22c4-c7e1-0310-b164-a889846e9adb
Revision: 68457
Node Kind: directory
Schedule: normal
Last Changed Author: satchit
Last Changed Rev: 68387
Last Changed Date: 2012-04-11 11:43:24 -0400 (Wed, 11 Apr 2012)
"""

def test_run_bad_command():
    """
    If the command passed to run command doesn't exist, we need an error report:

    >>> import zc.zkdeployment
    >>> zc.zkdeployment.run_command(['wtf111111111111'], return_output=True)
    Traceback (most recent call last):
    ...
    OSError: [Errno 2] No such file or directory
    """

def test_legacy_host_entries():
    r"""
    If there's a non-ephemeral host entry. We snag the version, remove
    it and create an ephmeral entry.

    >>> setup_logging()
    >>> import zc.zk
    >>> zk = zc.zk.ZK('zookeeper:2181')
    >>> zk.import_tree('''
    ... /hosts
    ...   version = 1
    ...   /424242424242
    ...     version = 1
    ... ''')

    >>> import os
    >>> os.remove(os.path.join('etc', 'zim', 'host_version'))

    >>> import zc.zkdeployment.agent
    >>> agent = zc.zkdeployment.agent.Agent()
    INFO Agent starting, cluster 1, host 1

    At this point, the host node we created has been converted to an
    ephemeral node, as we'll see in a minute.

    In fact, if we try to create another agent, we'll get an error
    because the node exists and is ephemeral:

    >>> zc.zkdeployment.agent.Agent()
    Traceback (most recent call last):
    ...
    ValueError: Another agent is running

    Now, if we close the agent, the agent, the node will go away:

    >>> agent.close()
    >>> zk.print_tree('/hosts')
    /hosts
      version = 1
    """

def test_home_impprovement():
    """The agent is run as root.

    Unfortunately, it seems to be rather hard to run root with a
    proper HOME environment variable. Sigh.  So the agent fixes it up,
    if it must.

    >>> setup_logging()
    >>> os.environ['HOME'] = '/'
    >>> import zc.zkdeployment.agent
    >>> agent = zc.zkdeployment.agent.Agent()
    WARNING Fixing incorrect home, '/'.
    INFO Agent starting, cluster 1, host 1

    >>> agent.close()
    """

def test_non_empty_etc():
    """If, for some reason, an etc directory isn't empty when we
    expect it to be, we log an error, but continue:

    >>> setup_logging()
    >>> zk = zc.zk.ZK('zookeeper:2181')
    >>> import zc.zkdeployment.agent
    >>> os.remove(os.path.join('etc', 'zim', 'host_version'))
    >>> with mock.patch('subprocess.Popen', side_effect=subprocess_popen):
    ...     agent = zc.zkdeployment.agent.Agent(); time.sleep(.50)
    INFO Agent starting, cluster 1, host None
    INFO ============================================================
    INFO Deploying version 1
    INFO yum -q list installed z4m
    yum -q list installed z4m
    INFO yum -q list installed z4mmonitor
    yum -q list installed z4mmonitor
    INFO /opt/z4m/bin/zookeeper-deploy /cust/someapp/cms 0
    z4m/bin/zookeeper-deploy /cust/someapp/cms 0
    INFO /opt/z4mmonitor/bin/zookeeper-deploy /cust/someapp/monitor 0
    z4mmonitor/bin/zookeeper-deploy /cust/someapp/monitor 0
    INFO /opt/z4m/bin/zookeeper-deploy /cust2/someapp/cms 0
    z4m/bin/zookeeper-deploy /cust2/someapp/cms 0
    INFO Done deploying version 1

    Now add a garbage file to the etc dir:

    >>> open(os.path.join('etc', 'z4mmonitor', 'junk'), 'w').close()

    >>> zk.import_tree('/cust', trim=True)

    >>> with mock.patch('subprocess.Popen', side_effect=subprocess_popen):
    ...     zk.properties('/hosts').update(version=2); time.sleep(.50)
    ... # doctest: +ELLIPSIS
    INFO ============================================================
    INFO Deploying version 2
    INFO /opt/z4m/bin/zookeeper-deploy -u /cust/someapp/cms 0
    z4m/bin/zookeeper-deploy -u /cust/someapp/cms 0
    INFO /opt/z4mmonitor/bin/zookeeper-deploy -u /cust/someapp/monitor 0
    z4mmonitor/bin/zookeeper-deploy -u /cust/someapp/monitor 0
    INFO yum -q list installed z4m
    yum -q list installed z4m
    INFO /opt/z4m/bin/zookeeper-deploy /cust2/someapp/cms 0
    z4m/bin/zookeeper-deploy /cust2/someapp/cms 0
    INFO yum -y remove z4mmonitor
    yum -y remove z4mmonitor
    ERROR Removing u'/etc/z4mmonitor'
    Traceback (most recent call last):
    ...
    OSError: [Errno ...] Directory not empty: /etc/z4mmonitor'
    INFO Done deploying version 2

    >>> agent.close()
    """

def test_versioned_rpm_names():
    """

We weren't constructing install script paths correctly when using
versioned apps.

We also were cleaning up etc directories when we shouldn't have.

    >>> setup_logging()
    >>> zk = zc.zk.ZK('zookeeper:2181')
    >>> zk.delete_recursive('/cust2')
    >>> zk.import_tree('''
    ... /cust
    ...   /someapp
    ...     /cms : z4m-4.0.0
    ...       /deploy
    ...         /424242424242
    ... ''', trim=True)
    >>> agent = zc.zkdeployment.agent.Agent()
    INFO Agent starting, cluster 1, host 1
    >>> with mock.patch('subprocess.Popen', side_effect=subprocess_popen):
    ...     zk.properties('/hosts').update(version=2); time.sleep(.50)
    INFO ============================================================
    INFO Deploying version 2
    INFO /opt/z4m/bin/zookeeper-deploy -u /cust2/someapp/cms 0
    z4m/bin/zookeeper-deploy -u /cust2/someapp/cms 0
    INFO /opt/z4mmonitor/bin/zookeeper-deploy -u /cust/someapp/monitor 0
    z4mmonitor/bin/zookeeper-deploy -u /cust/someapp/monitor 0
    INFO yum -y clean all
    yum -y clean all
    INFO yum -y install z4m-4.0.0
    yum -y install z4m-4.0.0
    INFO yum -q list installed z4m-4.0.0
    yum -q list installed z4m-4.0.0
    INFO /opt/z4m-4.0.0/bin/zookeeper-deploy /cust/someapp/cms 0
    z4m-4.0.0/bin/zookeeper-deploy /cust/someapp/cms 0
    INFO yum -y remove z4m
    yum -y remove z4m
    INFO yum -y remove z4mmonitor
    yum -y remove z4mmonitor
    INFO Done deploying version 2

Let's switch back for good measure (and to see if we're getting paths right:

    >>> zk.import_tree('''
    ... /cust
    ...   /someapp
    ...     /cms : z4m
    ...       version = '2.0.0'
    ...       /deploy
    ...         /424242424242
    ... ''', trim=True)
    >>> with mock.patch('subprocess.Popen', side_effect=subprocess_popen):
    ...     zk.properties('/hosts').update(version=3); time.sleep(.50)
    INFO ============================================================
    INFO Deploying version 3
    INFO yum -y clean all
    yum -y clean all
    INFO yum -y install z4m-2.0.0
    yum -y install z4m-2.0.0
    INFO yum -q list installed z4m
    yum -q list installed z4m
    INFO /opt/z4m/bin/zookeeper-deploy /cust/someapp/cms 0
    z4m/bin/zookeeper-deploy /cust/someapp/cms 0
    INFO yum -y remove z4m-4.0.0
    yum -y remove z4m-4.0.0
    INFO Done deploying version 3

And finally, remove, which should clean up the etc dir:


    >>> zk.delete_recursive('/cust')
    >>> with mock.patch('subprocess.Popen', side_effect=subprocess_popen):
    ...     zk.properties('/hosts').update(version=4); time.sleep(.50)
    INFO ============================================================
    INFO Deploying version 4
    INFO /opt/z4m/bin/zookeeper-deploy -u /cust/someapp/cms 0
    z4m/bin/zookeeper-deploy -u /cust/someapp/cms 0
    INFO yum -y remove z4m
    yum -y remove z4m
    INFO Done deploying version 4

    >>> os.path.exists(os.path.join('etc', 'z4m'))
    False

    >>> agent.close()
    """

def monitor_only_with_zimagent():
    """
    Normally, with zimagent installed, the agent monitor is started:

    >>> with mock.patch('zc.zkdeployment.agent.Agent') as Agent:
    ...     with mock.patch('zc.zkdeployment.agent.Monitor') as Monitor:
    ...         zc.zkdeployment.agent.main([])
    ...         Agent.assert_called_with(verbose=False, run_once=False)
    ...         Monitor.assert_called_with(Agent.return_value)
    ...         Monitor.return_value.run.assert_called_with()
    ...         assert_(Agent.return_value.monitor_cb is
    ...                 Monitor.return_value.send_state)

    >>> os.remove(os.path.join('etc', 'init.d', 'zimagent'))


    >>> with mock.patch('zc.zkdeployment.agent.Agent') as Agent:
    ...     with mock.patch('zc.zkdeployment.agent.Monitor') as Monitor:
    ...         zc.zkdeployment.agent.main([])
    ...         Agent.assert_called_with(verbose=False, run_once=False)
    ...         assert_(not Monitor.called)
    ...         Agent.return_value.run.assert_called_with()

    """

def agent_run():
    """
    >>> import signal
    >>> agent = zc.zkdeployment.agent.Agent()
    >>> agent.zk.client.state
    'CONNECTED'
    >>> with mock.patch('signal.signal'):
    ...   with mock.patch('zc.zkdeployment.agent.signallableblock') as block:
    ...      agent.run()
    ...      block.assert_called_with()
    ...      num, func = signal.signal.call_args[0]
    ...      assert_(num == signal.SIGTERM)
    ...      try: func(None, None)
    ...      except SystemExit: pass
    ...      else: assert_(False)

    The agent's zk connection is closed
    >>> agent.zk.client.state
    'LOST'
    """

def switching_subversion_urls():
    """We need to be able to change subversion URLs.

Set up with one url:

    >>> setup_logging()
    >>> zc.zkdeployment.agent.register()
    >>> zk = zc.zk.ZK('zookeeper:2181')
    >>> zk.delete_recursive('/cust2')
    >>> zk.import_tree('''
    ... /cust
    ...   /someapp
    ...     /cms : z4m
    ...       version = 'svn+ssh://svn.zope.com/repos/main/z4m/trunk'
    ...       /deploy
    ...         /424242424242
    ... ''', trim=True)
    >>> agent = zc.zkdeployment.agent.Agent()
    INFO Agent starting, cluster 1, host 1
    >>> with mock.patch('subprocess.Popen', side_effect=subprocess_popen):
    ...     zk.properties('/hosts').update(version=2); time.sleep(.50)
    INFO ============================================================
    INFO Deploying version 2
    INFO /opt/z4m/bin/zookeeper-deploy -u /cust2/someapp/cms 0
    z4m/bin/zookeeper-deploy -u /cust2/someapp/cms 0
    INFO /opt/z4mmonitor/bin/zookeeper-deploy -u /cust/someapp/monitor 0
    z4mmonitor/bin/zookeeper-deploy -u /cust/someapp/monitor 0
    INFO yum -q list installed z4m
    yum -q list installed z4m
    INFO yum -y remove z4m
    yum -y remove z4m
    INFO svn co svn+ssh://svn.zope.com/repos/main/z4m/trunk /opt/z4m
    INFO Build z4m (svn+ssh://svn.zope.com/repos/main/z4m/trunk)
    INFO /opt/z4m/stage-build
    /opt/z4m/stage-build
    INFO chmod -R a+rX .
    chmod -R a+rX .
    INFO /opt/z4m/bin/zookeeper-deploy /cust/someapp/cms 0
    z4m/bin/zookeeper-deploy /cust/someapp/cms 0
    INFO yum -y remove z4mmonitor
    yum -y remove z4mmonitor
    INFO Done deploying version 2

Then switch to another:

    >>> zk.import_tree('''
    ... /cust
    ...   /someapp
    ...     /cms : z4m
    ...       version = 'svn+ssh://svn.zope.com/repos/main/z4m/branches/x'
    ...       /deploy
    ...         /424242424242
    ... ''', trim=True)

    >>> with mock.patch('subprocess.Popen', side_effect=subprocess_popen):
    ...     zk.properties('/hosts').update(version=3); time.sleep(.50)
    ... # doctest: +NORMALIZE_WHITESPACE
    INFO ============================================================
    INFO Deploying version 3
    INFO svn info /opt/z4m
    INFO Removing conflicting checkout 'svn+ssh://svn.zope.com/repos/main/z4m/trunk' != u'svn+ssh://svn.zope.com/repos/main/z4m/branches/x'
    INFO svn co svn+ssh://svn.zope.com/repos/main/z4m/branches/x /opt/z4m
    INFO Build z4m (svn+ssh://svn.zope.com/repos/main/z4m/branches/x)
    INFO /opt/z4m/stage-build
    /opt/z4m/stage-build
    INFO chmod -R a+rX .
    chmod -R a+rX .
    INFO /opt/z4m/bin/zookeeper-deploy /cust/someapp/cms 0
    z4m/bin/zookeeper-deploy /cust/someapp/cms 0
    INFO Done deploying version 3

    """

def monitor_last_good_time():
    """A Monitor's last_good_time is update when it's state is set to 'INFO'.

    >>> import zc.zkdeployment.agent
    >>> with mock.patch('time.time', return_value=1):
    ...     monitor = zc.zkdeployment.agent.Monitor(None)
    >>> monitor.last_good_time
    1

    >>> monitor.state = 'CRITICAL'
    >>> monitor.last_good_time
    1

    >>> monitor.state = 'WARNING'
    >>> monitor.last_good_time
    1

    >>> with mock.patch('time.time', return_value=2):
    ...     monitor.state = 'INFO'
    >>> monitor.last_good_time
    2

    """

def assert_zookeeper_address():
    """
    There's a safety belt to make sure zookeeper's address is what you
    think it is (especially for staging).

    >>> with mock.patch('socket.gethostbyname', side_effect=lambda n: n+'.42'):
    ...     zc.zkdeployment.agent.main([
    ...         '-z127.0.0.1'])
    Traceback (most recent call last):
    ...
    AssertionError: ('Invalid zookeeper address', 'zookeeper.42', '127.0.0.1')

    >>> with mock.patch('socket.gethostbyname', side_effect=lambda n: n+'.42'):
    ...     zc.zkdeployment.agent.main([
    ...         '-zzookeeper.42', '-1'])


    """

def no_HOME():
    """
    >>> old = os.environ.pop('HOME', None)
    >>> setup_logging()
    >>> agent = zc.zkdeployment.agent.Agent()
    WARNING Fixing incorrect home, None.
    INFO Agent starting, cluster 1, host 1

    >>> if old is not None:
    ...     os.environ['HOME'] = old

    >>> agent.close()
    """

def agent_refuse_to_update_to_None():
    r"""

    The agent won't try to deploy if the cluster version is None

    >>> setup_logging()
    >>> import zc.zk
    >>> zk = zc.zk.ZK('zookeeper:2181')
    >>> zk.import_tree('''
    ... /hosts
    ... ''')

    >>> import zc.zkdeployment.agent
    >>> agent = zc.zkdeployment.agent.Agent()
    INFO Agent starting, cluster None, host 1

    >>> zk.import_tree('''
    ... /hosts
    ...     version = None
    ... ''')
    extra path not trimmed: /hosts/424242424242

    >>> agent.close()
    >>> agent = zc.zkdeployment.agent.Agent()
    INFO Agent starting, cluster None, host 1
    """

def agent_bails_on_None():
    r"""

    If an agent becomes unblocked by lock releasing and finds the
    cluster version to be None, it will abandon the update.

    >>> setup_logging()
    >>> import zc.zk
    >>> zk = zc.zk.ZK('zookeeper:2181')
    >>> zk.import_tree('''
    ... /cust
    ... /cust2
    ... /hosts
    ...    version = 1
    ... ''', trim=True)
    >>> agent = zc.zkdeployment.agent.Agent()
    INFO Agent starting, cluster 1, host 1

    >>> lock = zk.client.Lock('/agent-locks/app', '42')
    >>> lock.acquire()
    True

    >>> zk.import_tree('''
    ... /app : foo
    ...     version = '1'
    ...     /deploy
    ...       /424242424242
    ... ''')
    >>> with mock.patch('subprocess.Popen', side_effect=subprocess_popen):
    ...     zk.properties('/hosts').update(version=2); time.sleep(.1)
    ...     # doctest: +ELLIPSIS
    INFO ============================================================
    INFO Deploying version 2
    ...
    yum -q list installed foo
    >>> zk.properties('/hosts').update(version=None)
    >>> _ = lock.release(); time.sleep(.1) # doctest: +ELLIPSIS
    WARNING Abandoning deployment because cluster version is None...

    >>> with mock.patch('subprocess.Popen', side_effect=subprocess_popen):
    ...     zk.properties('/hosts').update(version=2); time.sleep(.1)
    INFO ============================================================
    INFO Deploying version 2
    INFO DEBUG: got deployments
    INFO DEBUG: remove old deployments
    INFO DEBUG: update software
    INFO yum -q list installed foo
    yum -q list installed foo
    INFO /tmp/tmphOApCN/TEST_ROOT/opt/foo/bin/zookeeper-deploy /app 0
    foo/bin/zookeeper-deploy /app 0
    INFO yum -y remove z4m
    yum -y remove z4m
    INFO yum -y remove z4mmonitor
    yum -y remove z4mmonitor
    INFO Done deploying version 2

    >>> agent.close()
    >>> zk.close()
    """

def test_downgrade():
    """
    >>> setup_logging()
    >>> import zc.zk
    >>> zk = zc.zk.ZK('zookeeper:2181')
    >>> zk.import_tree('''
    ... /cust
    ...   /cms : z4m
    ...      version = u'0.9.0'
    ...      /deploy
    ...        /424242424242
    ... /cust2
    ... ''', trim=True)
    >>> agent = zc.zkdeployment.agent.Agent()
    INFO Agent starting, cluster 1, host 1
    >>> with mock.patch('subprocess.Popen', side_effect=subprocess_popen):
    ...     zk.properties('/hosts').update(version=2); time.sleep(.1)
    ...     # doctest: +ELLIPSIS
    INFO ============================================================
    INFO Deploying version 2
    INFO DEBUG: got deployments
    INFO DEBUG: remove old deployments
    INFO /opt/z4m/bin/zookeeper-deploy -u /cust/someapp/cms 0
    z4m/bin/zookeeper-deploy -u /cust/someapp/cms 0
    INFO /opt/z4m/bin/zookeeper-deploy -u /cust2/someapp/cms 0
    z4m/bin/zookeeper-deploy -u /cust2/someapp/cms 0
    INFO /opt/z4mmonitor/bin/zookeeper-deploy -u /cust/someapp/monitor 0
    z4mmonitor/bin/zookeeper-deploy -u /cust/someapp/monitor 0
    INFO DEBUG: update software
    INFO yum -q list installed z4m
    yum -q list installed z4m
    INFO yum -y clean all
    yum -y clean all
    INFO yum -y install z4m-0.9.0
    yum -y install z4m-0.9.0
    INFO yum -q list installed z4m
    yum -q list installed z4m
    INFO yum -y downgrade z4m-0.9.0
    yum -y downgrade z4m-0.9.0
    INFO yum -q list installed z4m
    yum -q list installed z4m
    INFO /tmp/tmpa53YeB/TEST_ROOT/opt/z4m/bin/zookeeper-deploy /cust/cms 0
    z4m/bin/zookeeper-deploy /cust/cms 0
    INFO yum -y remove z4mmonitor
    yum -y remove z4mmonitor
    INFO Done deploying version 2

    >>> agent.close()
    >>> zk.close()
    """

def test_role_controller_addition():
    """
    >>> setup_logging()
    >>> setup_role('my.role')

    Let's start with a traditional installation (no role-controller):

    >>> import zc.zk
    >>> zk = zc.zk.ZK('zookeeper:2181')
    >>> zk.import_tree('''
    ... /cust
    ...   /cms : z4m
    ...      version = u'0.9.0'
    ...      /deploy
    ...        /my.role
    ... /cust2
    ... ''', trim=True)

    >>> agent = zc.zkdeployment.agent.Agent()
    INFO Agent starting, cluster 1, host 1

    >>> with mock.patch('subprocess.Popen', side_effect=subprocess_popen):
    ...     zk.properties('/hosts').update(version=2); time.sleep(.5)
    INFO ============================================================
    INFO Deploying version 2
    INFO DEBUG: got deployments
    INFO DEBUG: remove old deployments
    INFO /opt/z4m/bin/zookeeper-deploy -u /cust/someapp/cms 0
    z4m/bin/zookeeper-deploy -u /cust/someapp/cms 0
    INFO /opt/z4m/bin/zookeeper-deploy -u /cust2/someapp/cms 0
    z4m/bin/zookeeper-deploy -u /cust2/someapp/cms 0
    INFO /opt/z4mmonitor/bin/zookeeper-deploy -u /cust/someapp/monitor 0
    z4mmonitor/bin/zookeeper-deploy -u /cust/someapp/monitor 0
    INFO yum -q list installed z4m
    yum -q list installed z4m
    INFO yum -y clean all
    yum -y clean all
    INFO yum -y install z4m-0.9.0
    yum -y install z4m-0.9.0
    INFO yum -q list installed z4m
    yum -q list installed z4m
    INFO yum -y downgrade z4m-0.9.0
    yum -y downgrade z4m-0.9.0
    INFO yum -q list installed z4m
    yum -q list installed z4m
    INFO /opt/z4m/bin/zookeeper-deploy /cust/cms 0
    z4m/bin/zookeeper-deploy /cust/cms 0
    INFO yum -y remove z4mmonitor
    yum -y remove z4mmonitor
    INFO Done deploying version 2

    If we add a role-controller, it will be installed:

    >>> zk.import_tree('''
    ... /roles
    ...   /my.role : my-0-0-rc
    ...      version = '1.0.0'
    ... /cust
    ...   /cms : z4m
    ...      version = u'0.9.0'
    ...      /deploy
    ...        /my.role
    ... /role-locks
    ...   /my.role
    ... ''', trim=True)

    >>> with mock.patch('subprocess.Popen', side_effect=subprocess_popen):
    ...     zk.properties('/hosts').update(version=3); time.sleep(.5)
    INFO ============================================================
    INFO Deploying version 3
    INFO yum -y clean all
    yum -y clean all
    INFO yum -y install my-0-0-rc-1.0.0
    yum -y install my-0-0-rc-1.0.0
    INFO yum -q list installed my-0-0-rc
    yum -q list installed my-0-0-rc
    INFO /opt/my-0-0-rc/bin/starting-deployments /roles/my.role
    /opt/my-0-0-rc/bin/starting-deployments /roles/my.role
    INFO DEBUG: got deployments
    INFO DEBUG: remove old deployments
    INFO DEBUG: update software
    INFO yum -q list installed z4m
    yum -q list installed z4m
    INFO /tmp/tmpcwTGRH/TEST_ROOT/opt/z4m/bin/zookeeper-deploy /cust/cms 0
    z4m/bin/zookeeper-deploy /cust/cms 0
    INFO /opt/my-0-0-rc/bin/ending-deployments /roles/my.role
    /opt/my-0-0-rc/bin/ending-deployments /roles/my.role
    INFO Done deploying version 3

    >>> agent.close()
    >>> zk.close()
    """

class TestStream:

    def write(self, text):
        sys.stdout.write(text)

class Lock:

    random = random.Random(0)
    locks = {}

    def __init__(self, client, path, identifier):
        self.client = client
        self.path = path
        self.identifier = identifier

    def acquire(self, blocking=1):
        self.client.ensure_path(self.path)
        self.rpath = self.path + '/' + str(random.randint(1<<30, 1<<31))
        self.client.create(self.rpath, self.identifier)
        lock = self.locks.setdefault(self.path, threading.Lock())
        acquired = lock.acquire(blocking)
        if not acquired:
            self.client.delete(self.rpath)
        return acquired

    def release(self):
        self.client.delete(self.rpath)
        lock = self.locks[self.path]
        lock.release()

    def __enter__(self):
        self.acquire()

    def __exit__(self, *a):
        self.release()

def lock(self, *a):
    return Lock(self, *a)

zc.zk.testing.Client.Lock = lock

def setUp(test, initial_tree=initial_tree,
          initial_file_system=initial_file_system):
    zope.testing.setupstack.setUpDirectory(test)
    zope.component.testing.setUp()
    zope.testing.setupstack.register(test, zope.component.testing.tearDown)
    zc.zk.testing.setUp(test, initial_tree, connection_string='zookeeper:2181')
    os.mkdir('TEST_ROOT')
    os.chdir('TEST_ROOT')
    os.environ['TEST_ROOT'] = os.getcwd()
    zope.testing.setupstack.register(
        test, lambda : zc.zk.testing.tearDown(test))
    buildfs(initial_file_system)

    role_path = os.path.join(os.environ['TEST_ROOT'],
                             zc.zkdeployment.agent.ROLE_LOCATION)

    def setup_role(role):
        with open(role_path, 'w') as f:
            f.write(role)

    def clear_role():
        if os.path.exists(role_path):
            os.unlink(role_path)

    zope.testing.setupstack.register(test, clear_role)
    zope.testing.setupstack.context_manager(
        test, mock.patch('socket.getfqdn')
        ).return_value = 'host42'

    old_home = os.environ['HOME']
    zope.testing.setupstack.register(
        test, os.environ.__setitem__, 'HOME', old_home)
    os.environ['HOME'] = '/root'

    handler = logging.StreamHandler(TestStream())
    logger = logging.getLogger('zc.zkdeployment')
    logger.addHandler(handler)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.setLevel(logging.INFO)
    test.globs['setup_logging'] = lambda : logger.setLevel(logging.INFO)
    test.globs['setup_role'] = setup_role

    zope.testing.setupstack.register(
        test,
        lambda test:
        logger.removeHandler(handler), logger.setLevel(logging.NOTSET)
        )

def setup_sync(test):
    setUp(test, initial_tree=' ')

role_controller_file_system = dict(
    etc = dict(
        zmh = dict(
            pxemac = '424242424242\n',
            ),
        zim = dict(
            host_version = '1',
            ),
        **{
            'init.d': dict(zimagent=''),
            }),
    opt = dict(),
    )

def test_suite():
    suite = unittest.TestSuite()
    checker = zope.testing.renormalizing.RENormalizing([
        (re.compile(r'\S+TEST_ROOT'), ''),
        (re.compile(r'INFO DEBUG: [^\n]+\n'), ''),
        (re.compile(r"u'/"), "'/"),
        ])
    m = manuel.doctest.Manuel(
        checker=checker,
        optionflags=doctest.ELLIPSIS|doctest.NORMALIZE_WHITESPACE
        ) + manuel.capture.Manuel()
    suite.addTest(
        manuel.testing.TestSuite(
            m,
            'agent.txt', 'git.txt', 'monitor.txt',
            setUp=setUp,
            tearDown=zope.testing.setupstack.tearDown,
            ))
    suite.addTest(
        manuel.testing.TestSuite(
            m,
            'persistent-lock.txt',
            'role-controller.txt',
            setUp=(lambda t: setUp(
                t,
                initial_tree='/hosts\n version=1',
                initial_file_system=role_controller_file_system)),
            tearDown=zope.testing.setupstack.tearDown,
            ))
    suite.addTest(
        manuel.testing.TestSuite(
            m,
            'sync.txt', 'syncgit.txt',
            setUp=setup_sync,
            tearDown=zope.testing.setupstack.tearDown,
            ))
    suite.addTest(
        doctest.DocTestSuite(
            checker=checker, setUp=setUp,
            tearDown=zope.testing.setupstack.tearDown,
            ))
    suite.addTest(doctest.DocTestSuite('zc.zkdeployment.kazoofilter'))

    return suite
