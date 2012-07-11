import logging
import os
import subprocess
import tempfile
import zc.thread

TIMEOUT_INTERVAL = 900 # seconds

logger = logging.getLogger(__name__)


def run_command(cmd_list, timeout=None, verbose=False):
    timeout = timeout or TIMEOUT_INTERVAL
    tfile = tempfile.NamedTemporaryFile('w', delete=False,
                                        prefix='zkdeployment-run_command')
    process = []
    @zc.thread.Thread
    def worker():
        process.append(subprocess.Popen(
            cmd_list, stdout=tfile, stderr=subprocess.STDOUT))
        process[0].communicate()
    worker.join(timeout)
    if worker.is_alive():
        process[0].terminate()
        raise RuntimeError('Command failed: ' + ' '.join(cmd_list) +
                ', took too long')
    tfile.close()
    with open(tfile.name) as f:
        output = f.read()
    os.remove(tfile.name)

    if worker.exception:
        raise worker.exception

    if process:
        if process[0].returncode != 0:
            logger.error("Command failed: %r\n  %s" %
                            (cmd_list,
                             output.replace('\n', '\n  ').strip()))
            raise RuntimeError('Command failed: ' + ' '.join(cmd_list))
        elif verbose:
            logger.info("Command succeeded: %r\n  %s" %
                    (cmd_list,
                     output.replace('\n', '\n  ').strip()))

    return output
