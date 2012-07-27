import logging
import os
import subprocess
import tempfile
import zc.thread

logger = logging.getLogger(__name__)

def run_command(cmd_list, verbose=False):
    tfile = tempfile.NamedTemporaryFile('w', delete=False,
                                        prefix='zkdeployment-run_command')
    process = subprocess.Popen(
            cmd_list, stdout=tfile, stderr=subprocess.STDOUT)
    process.communicate()
    tfile.close()
    with open(tfile.name) as f:
        output = f.read()
    os.remove(tfile.name)
    if process.returncode != 0:
        logger.error("Command failed: %r\n  %s" %
        (cmd_list,
         output.replace('\n', '\n  ').strip()))
        raise RuntimeError('Command failed: ' + ' '.join(cmd_list))
    elif verbose:
        logger.info("Command succeeded: %r\n  %s" %
                    (cmd_list,
                     output.replace('\n', '\n  ').strip()))

    return output
