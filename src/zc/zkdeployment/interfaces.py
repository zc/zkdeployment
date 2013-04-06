import zope.interface

class IVCS(zope.interface.Interface):

    def is_under_vc(path):
        """Test whether the given path is under version control.

        Returns: bool
        """

    def get_version(path):
        """Get the VCS version of the given path."""

    def uninstall(path):
        """Uninstall"""

    def update(path, version, verbose):
        """Update the given path for the given url"""
