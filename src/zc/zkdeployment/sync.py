import logging
import optparse
import urlparse
import zc.zk
import zc.zkdeployment
import zktools.locking
import zookeeper

ZK_LOCATION = 'zookeeper:2181'

logger = logging.getLogger(__name__)

# Hack, zktools.locking calls zookeeper.set_log_stream, which messes up zk.
zookeeper.set_log_stream = lambda f: None



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

        # Import changes
        for fi in zkfiles + zkxfiles:
            contents = self('cat', '%s/%s' % (self.url,  fi))
            yield (fi, contents)

def get_zk_version(zk):
    try:
        return zk.get_properties('/hosts')['version']
    except zookeeper.NoNodeException:
        zk.import_tree('/hosts\n  version="initial"')
        return "initial"

def sync_with_canonical(url, dry_run=False, force=False):
    zk = zc.zk.ZK(ZK_LOCATION)
    zk_version = get_zk_version(zk)
    if zk_version is None:
        logger.critical("ALL STOP, cluster version is None")
        if not force:
            return

    vcs = SVN(url)
    logger.info("VCS Version: " + str(vcs.version))
    logger.info("ZK Version: " + str(zk_version))
    if zk_version != vcs.version:
        if not force:
            for child in zk.get_children('/hosts'):
                host_version = zk.properties('/hosts/' + child)['version']
                if host_version != zk_version:
                    logger.error(
                        "Version mismatch detected, can't resync since " +
                        "host %s has not converged (%s -> %s)" % (
                            child, host_version, zk_version))
                    return

        cluster_lock = zktools.locking.ZkLock(zk, ',hosts')
        try:
            if cluster_lock.acquire(0):
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
            else:
                logger.error("Refused to update zookeeper tree, "
                    "couldn't obtain cluster lock")
        finally:
            cluster_lock.release()


def main():
    logging.basicConfig(level=logging.WARNING)
    parser = optparse.OptionParser()
    parser.add_option('-d', '--dry-run', dest='dry_run',
        action='store_true',
        help="Don't actually modify the zookeeper db")
    parser.add_option('-f', '--force', dest='force',
        action='store_true',
        help="Force tree update, even if we detect errors")
    parser.add_option('-u', '--url', dest='url',
        default=None, help="URL to sync")
    (options, args) = parser.parse_args()
    dry_run = options.dry_run
    force = options.force
    url = options.url
    sync_with_canonical(url, dry_run, force)


if __name__ == '__main__':
    main()

