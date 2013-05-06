# Git plugin
from zc.zkdeployment.interfaces import IVCS

import os
import zc.zkdeployment
import zope.component
import zope.interface

class Git:

    zope.interface.implements(IVCS)

    def is_under_vc(self, path):
        return os.path.exists(os.path.join(path, '.git'))

    def get_version(self, path, verbose):
        with open(os.path.join(path, '.git', '.zkdeployment')) as f:
            return f.read().strip()

    def update(self, path, version, verbose):
        # git://REPO#VER
        here = os.getcwd()
        try:
            if os.path.exists(path):
                os.chdir(path)
                zc.zkdeployment.run_command(
                    'git pull origin -a'.split(),
                    verbose=verbose, return_output=False)
            else:
                repo, co = version[6:].rsplit('#', 1)
                zc.zkdeployment.run_command(
                    ['git', 'clone', repo, path],
                    verbose=verbose, return_output=False)
                os.chdir(path)
                with open(os.path.join('.git', '.zkdeployment'), 'w') as f:
                    f.write(version)

                zc.zkdeployment.run_command(
                    ['git', 'checkout', co],
                    verbose=verbose, return_output=False)

        finally:
            os.chdir(here)

def register():
    zope.component.provideUtility(Git(), IVCS, 'git')
