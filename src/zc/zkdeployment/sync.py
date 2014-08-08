import kazoo.exceptions
import logging
import optparse
import os
import sys
import time
import zc.lockfile
import zc.zk
import zc.zkdeployment

MAX_VCS_RETRIES = 3
ZK_LOCATION = 'zookeeper:2181'

logger = logging.getLogger(__name__)

def svn_cmd(cmd, url): # This exists to be mocked
    return zc.zkdeployment.run_command(['svn', cmd, url], return_output=True)

class SVN:

    def __init__(self, url):
        self.url = url
        self.version = self.get_version()

    def __call__(self, cmd, url=None):
        return svn_cmd(cmd, url or self.url)

    def get_version(self):
        for line in self('info').splitlines():
            if line.startswith('Last Changed Rev:'):
                return int(line.split()[-1])

    def __iter__(self):
        allfiles = [fi for fi in self('ls').strip().split('\n')]
        zkfiles = [fi for fi in allfiles if fi.endswith('.zk')]
        zkxfiles = [fi for fi in allfiles if fi.endswith('.zkx')]

        for fi in zkfiles + zkxfiles:
            contents = self('cat', '%s/%s' % (self.url,  fi))
            yield (fi, contents)


def git_cmd(*args): # This exists to be mocked
    return zc.zkdeployment.run_command(('git', )+args, return_output=True)

class GIT:

    def __init__(self, url, tree_directory):
        self.trees = tree_directory
        if not os.path.exists(self.trees):
            git_cmd('clone', url, self.trees)

        self('fetch')
        self('merge', 'origin/master')
        log = self('log', '-1')
        self.version = log.split()[1]

    def __call__(self, *args):
        return git_cmd(
            '--git-dir=%s/.git' % self.trees,
            '--work-tree=%s' % self.trees,
            *args)

    def __iter__(self):
        allfiles = sorted(os.listdir(self.trees))
        zkfiles = [fi for fi in allfiles if fi.endswith('.zk')]
        zkxfiles = [fi for fi in allfiles if fi.endswith('.zkx')]

        for fi in zkfiles + zkxfiles:
            with open(os.path.join(self.trees, fi)) as f:
                contents = f.read()

            yield (fi, contents)

def get_zk_version(zk):
    try:
        return zk.get_properties('/hosts')['version']
    except kazoo.exceptions.NoNodeException:
        zk.import_tree('/hosts\n  version="initial"')
        return "initial"

def sync_with_canonical(url, dry_run=False, force=False, tree_directory=None):
    zk = zc.zk.ZK(ZK_LOCATION)
    zk_version = get_zk_version(zk)
    if zk_version is None:
        logger.critical("ALL STOP, cluster version is None")
        if not force:
            return

    retries = 0
    while True:
        try:
            if url.startswith('svn') or url.startswith('file://'):
                vcs = SVN(url)
            else:
                vcs = GIT(url, tree_directory)
            break
        except RuntimeError:
            retries += 1
            if retries > MAX_VCS_RETRIES:
                raise
            time.sleep(3)


    logger.info("VCS Version: " + str(vcs.version))
    logger.info("ZK Version: " + str(zk_version))
    if zk_version != vcs.version:
        if not (force or zk_version is False):
            for child in zk.get_children('/hosts'):
                host_version = zk.properties('/hosts/' + child)['version']
                if host_version != zk_version:
                    logger.error(
                        "Version mismatch detected, can't resync since " +
                        "host %s has not converged (%s -> %s)" % (
                            child, host_version, zk_version))
                    return

        cluster_lock = zk.client.Lock('/hosts-lock', str(os.getpid()))
        if cluster_lock.acquire(0):
            try:
                logger.info("Version mismatch detected, resyncing")

                # Import changes
                for fi, contents in vcs:
                    output = ' '.join(('Importing', fi))
                    if dry_run:
                        output += ' (dry run, no action taken)'
                    logger.info(output)
                    if not dry_run:
                        zk.import_tree(contents, trim=fi.endswith('.zk'))
                # bump version number
                if not dry_run:
                    zk.properties('/hosts').update(version=vcs.version)
            finally:
                cluster_lock.release()
        else:
            logger.error("Refused to update zookeeper tree, "
                         "couldn't obtain cluster lock")

    zk.close()

def main():
    tombstone = "/usr/share/zkdeployment/tombstone"
    logging.basicConfig(level=logging.WARNING)
    parser = optparse.OptionParser()
    parser.add_option('-d', '--dry-run', action='store_true',
        help="Don't actually modify the zookeeper db")
    parser.add_option('-f', '--force', action='store_true',
        help="Force tree update, even if we detect errors")
    parser.add_option('-u', '--url', default=None, help="URL to sync")
    parser.add_option('-t', '--tree-directory', default=None,
                      help="Working directiry for git repository")
    (options, args) = parser.parse_args()
    lock_file = "/var/tmp/zkdeployment_vcs_lock_"
    try:
        os.stat(os.path.dirname(tombstone))
    except OSError:
        os.makedirs(os.path.dirname(tombstone))
    try:
        lock = zc.lockfile.LockFile(lock_file)
        open(tombstone, "w").write(str(os.getpid()) + " acquired lock\n")
    except zc.lockfile.LockError:
        # die a silent death, leaving our tombstone behind
        if not os.path.exists(tombstone):
            open(tombstone, "w").write("failed to acquire lock\n")
        sys.exit(0)
    try:
        sync_with_canonical(
            options.url, options.dry_run, options.force, options.tree_directory)
    except Exception as e:
        if not os.path.exists(tombstone):
            open(tombstone, "w").write("sync failed %s.%s: %s\n" %
                                       (e.__class__.__module__,
                                       e.__class__.__name__, e))
    else:
        os.unlink(tombstone)
    finally:
        lock.close()


if __name__ == '__main__':
    main()

