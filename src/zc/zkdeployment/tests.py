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
import re
import shutil
import subprocess
import sys
import time
import traceback
import unittest
import zc.zk.testing
import zim.config # XXX zim duz way too much on import. :( Do it now.
import zope.testing.setupstack

start_with_digit = re.compile('\d').match
stage_build_path = re.compile('(/opt/\w+/stage-build)$').search

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

            deployed = os.path.join(
                'etc', app,
                args[0][1:].replace('/', ',')+'.'+args[1]+'.deployed')

            if uninstall:
                if os.path.exists(deployed):
                    os.remove(deployed)
            else:
                open(deployed, 'w').close()
        elif command == 'yum':
            package = args[-1]
            command = args[0]
            if args[0] == '-y' or args[0] == '-q':
                command = args[1]

            print 'yum', ' '.join(args)
            if command == 'install':
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

                buildfs(
                    dict(
                        etc={package: {}},
                        opt={
                            package: dict(
                                bin={'zookeeper-deploy': ''},
                                version=version+'-1',
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
            if not args[0] == 'co' and len(args) == 3:
                raise ValueError("Invalid svn command %r" % args)
            bin_path = os.path.join(args[2], 'bin')
            svn_path = os.path.join(args[2], '.svn')
            if not os.path.exists(bin_path):
                os.makedirs(bin_path)
            if not os.path.exists(svn_path):
                os.makedirs(svn_path)
            with open(os.path.join(args[2], 'bin', 'zookeeper-deploy'), 'w'):
                pass
        elif command == '/etc/init.d/zimagent':
            print command, ' '.join(args)

        elif stage_build_path(command) and not args:
            print stage_build_path(command).group(1)
        else:
            raise ValueError("No such command %s %r" % (command, args))
    except:
        traceback.print_exc()
        return FakeSubprocess(returncode=1)
    else:
        return FakeSubprocess(returncode=0)

def test_run_bad_command():
    """
    If the command passed to run command doesn't exist, we need an error report:

    >>> import zc.zkdeployment
    >>> zc.zkdeployment.run_command(['wtf111111111111'])
    Traceback (most recent call last):
    ...
    OSError: [Errno 2] No such file or directory
    """

def test_legacy_host_entries():
    r"""
    If there's a non-ephemeral host entry. We snag the version, remove
    it and create an ephmeral entry.

    >>> handler = logging.StreamHandler(sys.stdout)
    >>> logger = logging.getLogger('zc.zkdeployment')
    >>> logger.addHandler(handler)
    >>> handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    >>> logger.setLevel(logging.INFO)

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

    >>> logger.removeHandler(handler)
    >>> logger.setLevel(logging.NOTSET)
    """

def test_home_impprovement():
    """The agent is run as root.

    Unfortunately, it seems to be rather hard to run root with a
    proper HOME environment variable. Sigh.  So the agent fixes it up,
    if it must.

    >>> handler = logging.StreamHandler(sys.stdout)
    >>> logger = logging.getLogger('zc.zkdeployment')
    >>> logger.addHandler(handler)
    >>> handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    >>> logger.setLevel(logging.INFO)

    >>> os.environ['HOME'] = '/'
    >>> import zc.zkdeployment.agent
    >>> agent = zc.zkdeployment.agent.Agent()
    WARNING Fixing incorrect home, '/'.
    INFO Agent starting, cluster 1, host 1

    >>> agent.close()
    >>> logger.removeHandler(handler)
    >>> logger.setLevel(logging.NOTSET)
    """

def setUp(test):
    zope.testing.setupstack.setUpDirectory(test)
    zc.zk.testing.setUp(test, initial_tree, connection_string='zookeeper:2181')
    os.environ['TEST_ROOT'] = os.getcwd()
    zope.testing.setupstack.register(
        test, lambda : zc.zk.testing.tearDown(test))
    buildfs(initial_file_system)

    zope.testing.setupstack.context_manager(
        test, mock.patch('socket.getfqdn')
        ).return_value = 'host42'

    old_home = os.environ['HOME']
    zope.testing.setupstack.register(
        test, os.environ.__setitem__, 'HOME', old_home)
    os.environ['HOME'] = '/root'

def test_suite():
    suite = unittest.TestSuite()
    suite.addTest(
        manuel.testing.TestSuite(
            manuel.doctest.Manuel(
                optionflags=doctest.ELLIPSIS|doctest.NORMALIZE_WHITESPACE
                ) +
            manuel.capture.Manuel(),
            'sync.txt', 'agent.txt',
            setUp=setUp,
            tearDown=zope.testing.setupstack.tearDown,
            ))
    suite.addTest(
        doctest.DocTestSuite(
            setUp=setUp,
            tearDown=zope.testing.setupstack.tearDown,
            ))

    return suite


