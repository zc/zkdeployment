##############################################################################
#
# Copyright (c) 2011 Zope Foundation and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.1 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################

import argparse
import kazoo.exceptions
import os.path
import sys
import time
import zc.zk

parser = argparse.ArgumentParser(
    description='Check status of a zkdeployment monitor')
parser.add_argument('configuration',
                    help='Path to the agent configuration file')
parser.add_argument('--warn', '-w', type=int, default=200,
                    help='Delay (seconds) in activity after which to warn.')
parser.add_argument('--error', '-e', type=int, default=600,
                    help='Delay (seconds) in activity after which to error.')
parser.add_argument('--zookeeper', '-z', default='zookeeper:2181',
                    help='ZooKeeper connection string.')

def warn(message):
    print message
    return 1

def error(message):
    print message
    return 2

def main(args=None):
    if args is None:
        args = sys.argv[1:]

    args = parser.parse_args(args)
    config = zc.zkdeployment.agent.Configuration(args.configuration)
    zk = zc.zk.ZK(args.zookeeper)
    try:
        host_properties = dict(zk.properties('/hosts/' + config.host_id))
    except kazoo.exceptions.NoNodeError:
        return error('Host not registered')
    zkversion = zk.properties('/hosts', False).get('version')
    zk.close()
    if zkversion is None:
        return warn('Cluster version is None')
    try:
        with open(os.path.join(config.run_directory, 'status')) as f:
            t, _, version, status = f.read().strip().split(None, 3)
    except IOError, err:
        return error(str(err))
    if status == 'error':
        return error("Error deploying %s" % version)
    if status == 'done' and version == str(zkversion):
        # Looks ok, but double-check that this matches the live tree.
        if 'version' not in host_properties:
            return error('No version information for host')
        host_version = str(host_properties['version'])
        if host_version != version:
            return error('Version mismatch (status: %s, zk: %s)'
                         % (version, host_version))
        print version
        return None
    elapsed = time.time() - float(t)
    if elapsed > args.warn:
        message = "Too long deploying %s (%s; %d > %%s)" % (
            version, status, elapsed)
        if elapsed > args.error:
            return error(message % args.error)
        else:
            return warn(message % args.warn)
    print status
    return None
