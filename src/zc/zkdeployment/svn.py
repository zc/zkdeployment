# Subversion plugin
from zc.zkdeployment.interfaces import IVCS

import logging
import os
import shutil
import zc.zkdeployment
import zope.component
import zope.interface

logger = logging.getLogger()

class Subversion:

    zope.interface.implements(IVCS)

    def is_under_vc(self, path):
        return os.path.exists(os.path.join(path, '.svn'))

    def get_version(self, path, verbose):
        for line in zc.zkdeployment.run_command(
            ['svn', 'info', path],
            verbose=verbose,
            return_output=True,
            ).split('\n'):
            if line.startswith('URL: '):
                return line.split()[1]

    def update(self, path, version, verbose):
        zc.zkdeployment.run_command(
            ['svn', 'co', version, path],
            verbose=verbose, return_output=False)

def register():
    svn = Subversion()
    zope.component.provideUtility(svn, IVCS, 'svn+ssh')
    zope.component.provideUtility(svn, IVCS, 'svn')
