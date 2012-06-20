import os
import subprocess
import tempfile
import logging

logger = logging.getLogger(__name__)

def run_command(cmd_list):
    tfile = tempfile.NamedTemporaryFile('w', delete=False)
    retval = subprocess.call(cmd_list, stdout=tfile, stderr=subprocess.STDOUT)
    tfile.close()
    with open(tfile.name) as f:
        output = f.read()
    os.remove(tfile.name)
    if retval != 0:
        logger.error("Comand failed: %r\n  %s" %
                        (cmd_list,
                         output.replace('\n', '\n  ').strip()))
        raise RuntimeError('Command failed: ' + ' '.join(cmd_list))
    return output
