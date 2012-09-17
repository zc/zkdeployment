import logging
import os
import subprocess
import sys
import tempfile
import zc.thread

logger = logging.getLogger(__name__)

def run_command(cmd_list, verbose=False, return_output=False):
    logger.info("%s", " ".join(cmd_list))
    if return_output or not verbose:
        tfile = tempfile.NamedTemporaryFile('w', delete=False,
                                            prefix='zkdeployment-run_command')
    else:
        tfile = None

    process = subprocess.Popen(cmd_list, stdout=tfile, stderr=subprocess.STDOUT)
    process.communicate()

    output = ''
    if tfile is not None:
        tfile.close()
        with open(tfile.name) as f:
            output = f.read()
        os.remove(tfile.name)

    if process.returncode != 0:
        if output:
            print output.strip()
        logger.error("FAILURE")
        raise RuntimeError('Command failed: ' + ' '.join(cmd_list))
    elif verbose:
        if output:
            print output.strip()
        logger.info('SUCCESS')

    if return_output:
        return output
