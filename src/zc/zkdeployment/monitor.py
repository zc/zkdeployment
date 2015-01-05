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
import sys
import time
import zc.zk

parser = argparse.ArgumentParser(
    description='Check status of a zkdeployment monitor')
parser.add_argument('status',
                    help='Path to the status file')
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
    zk = zc.zk.ZK(args.zookeeper)
    zkversion = zk.properties('/hosts', False).get('version')
    if zkversion is None:
        return warn('Cluster version is None')
    try:
        with open(args.status) as f:
            t, _, version, status = f.read().strip().split(None, 3)
    except IOError, err:
        return error(str(err))
    if status == 'error':
        return error("Error deploying %s" % version)
    if status == 'done' and version == zkversion:
        print version
        return None
    elapsed = time.time() - float(t)
    if elapsed > args.warn:
        message = "Too long deploying %s (%s) %d > %%s" % (
            version, status, elapsed)
        if elapsed > args.error:
            return error(message % args.error)
        else:
            return warn(message % args.warn)
    print status
    return None
