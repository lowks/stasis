from ConfigParser import RawConfigParser
from contextlib import contextmanager
from imp import new_module
from pyramid.config import Configurator as BaseConfigurator
from pyramid.interfaces import IRequestExtensions, IRootFactory, IStaticURLInfo
from pyramid.interfaces import ITweens
from pyramid.path import caller_package
from pyramid.resource import abspath_from_resource_spec
from pyramid.router import Router
from pyramid.scripting import _make_request
from pyramid.traversal import traverse
from pyramid.util import action_method
from stasis.events import PreBuild
from stasis.interfaces import IConfigFactory
import dirtools
import logging
import os
import sys


log = logging.getLogger('Stasis')


class DefaultConfigFactory(dict):
    def __init__(self, registry):
        self.read_config(os.path.join(registry['path'], 'site.cfg'))

    def read_config(self, path):
        config = RawConfigParser()
        config.optionxform = lambda s: s
        config.read(path)
        return self.update(
            (x, dict(
                (y, config.get(x, y))
                for y in config.options(x)))
            for x in config.sections())


class Configurator(BaseConfigurator):
    @action_method
    def set_config_factory(self, factory):
        factory = self.maybe_dotted(factory)

        def register():
            self.registry.registerUtility(factory, IConfigFactory)

        intr = self.introspectable('config factories',
                                   None,
                                   self.object_description(factory),
                                   'config factory')
        intr['factory'] = factory
        self.action(IConfigFactory, register, introspectables=(intr,))


def relroute_path(request, *args, **kw):
    path = request.route_path(*args, **kw)
    return os.path.relpath(path, os.path.dirname(request.path))


def static_path(request, path, **kw):
    if not os.path.isabs(path):
        if not ':' in path:
            package = caller_package()
            path = '%s:%s' % (package.__name__, path)
    kw['_app_url'] = ''
    path = request.static_url(path, **kw)
    return os.path.relpath(path, os.path.dirname(request.path))


@contextmanager
def main_module(module):
    sys.modules['__main__'] = module
    yield
    for name in sys.modules.keys():
        if name.startswith('__main__'):
            del sys.modules[name]


class Site(object):
    def __init__(self, path):
        config_py = os.path.join(path, 'config.py')
        if not os.path.lexists(config_py):
            raise ValueError("No config.py found at '%s'." % path)
        self.site = new_module('__main__')
        self.site.__file__ = os.path.join(path, '__init__.py')
        self.site.__path__ = [path]
        with main_module(self.site):
            __import__("__main__.config")
            config = self.site.config.config
            self.registry = config.registry
            config.add_request_method(static_path)
            config.add_request_method(relroute_path)
            config.commit()
            self.registry['path'] = path
            self.siteconfig = config.registry.queryUtility(
                IConfigFactory,
                default=DefaultConfigFactory)(self.registry)
            self.siteconfig.setdefault('site', {})
            self.siteconfig['site'].setdefault('outpath', 'output')
            self.registry['root'] = config.registry.queryUtility(IRootFactory)
            self.registry['siteconfig'] = self.siteconfig
            self.registry.registerUtility(lambda h, r: h, ITweens)

    def get_paths(self):
        paths = set()
        root = self.registry['root']
        request = _make_request('/', registry=self.registry)
        if root:
            excludes = self.siteconfig['site'].get('excludes', '').split('\n')
            excludes.extend([
                '.*',
                '/config.py*',
                '/site.cfg',
                '/%s' % self.siteconfig['site']['outpath']])
            relpaths = dirtools.Dir(
                root.abspath,
                excludes=excludes).files()
            for relpath in relpaths:
                traverse(root, relpath)
                if root:
                    paths.add('/%s' % relpath)
        visited_routes = set()
        info = self.registry.queryUtility(IStaticURLInfo)
        if info:
            for (url, spec, route_name) in info._get_registrations(self.registry):
                visited_routes.add(route_name)
                path = abspath_from_resource_spec(spec)
                relpaths = dirtools.Dir(path).files()
                for relpath in relpaths:
                    paths.add(
                        request.route_path(route_name, subpath=relpath))
        routelist = self.site.config.config.get_routes_mapper().routelist
        for route in routelist:
            if route.factory is not None:
                matches = route.factory.matches(self.registry)
                paths = paths.union(route.generate(x) for x in matches)
            elif route.name not in visited_routes:
                paths.add(route.generate({}))
                visited_routes.add(route.name)
        return list(sorted(paths))

    def write(self, relpath, response):
        fn = os.path.join(self.siteconfig['site']['outpath'], relpath[1:])
        dirname = os.path.dirname(fn)
        if not os.path.lexists(dirname):
            os.makedirs(dirname)
        if os.path.lexists(fn):
            with open(fn, 'rb') as f:
                if f.read() == response.body:
                    log.info("Skipping up to date '%s'." % relpath)
                    return
        log.info("Writing '%s'." % relpath)
        with open(fn, 'wb') as f:
            f.write(response.body)

    def build(self):
        with main_module(self.site):
            self.registry.notify(PreBuild(self))
            paths = self.get_paths()
            router = Router(self.registry)
            extensions = self.registry.queryUtility(IRequestExtensions)
            for path in paths:
                request = _make_request(path, registry=self.registry)
                if extensions is not None:
                    request._set_extensions(extensions)
                response = router.handle_request(request)
                self.write(path, response)
