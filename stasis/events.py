from stasis.interfaces import IPreBuild
from zope.interface import implementer


@implementer(IPreBuild)
class PreBuild(object):
    def __init__(self, site):
        self.site = site.registry
