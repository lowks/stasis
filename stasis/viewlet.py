from pyramid import renderers
from pyramid.compat import string_types
from stasis.interfaces import IViewletMapper


def get_viewlet_mapper(config):
    mapper = config.registry.queryUtility(IViewletMapper)
    if mapper is None:
        mapper = {}
        config.registry.registerUtility(mapper, IViewletMapper)
    return mapper


def add_viewlet(config, name, viewlet=None, attr=None, factory=None, renderer=None):
    mapper = config.get_viewlet_mapper()
    viewlet = config.maybe_dotted(viewlet)
    factory = config.maybe_dotted(factory)
    if isinstance(renderer, string_types):
        renderer = renderers.RendererHelper(
            name=renderer, package=config.package,
            registry=config.registry)
    mapper[name] = dict(
        viewlet=viewlet,
        attr=attr,
        factory=factory,
        renderer=renderer)


class Viewlets(object):
    def __init__(self, request):
        self.request = request

    def __getitem__(self, name):
        mapper = self.request.registry.getUtility(IViewletMapper)
        info = mapper[name]
        viewlet = info['viewlet']
        context = info['factory'](self.request)
        result = viewlet(context, self.request)
        if info['attr']:
            result = getattr(result, info['attr'])()
        renderer = info['renderer']
        response = renderer.render(result, None)
        return response


class viewlet_config(object):
    def __init__(self, name, **settings):
        if not hasattr(self, 'venusian'):
            self.venusian = __import__('venusian')
        self.name = name
        self.settings = settings

    def __call__(self, wrapped):
        def callback(context, name, ob):
            config = context.config.with_package(info.module)
            config.add_viewlet(self.name, viewlet=ob, **self.settings)

        info = self.venusian.attach(wrapped, callback)
        if info.scope == 'class':
            # if the decorator was attached to a method in a class, or
            # otherwise executed at class scope, we need to set an
            # 'attr' into the settings if one isn't already in there
            if self.settings.get('attr') is None:
                self.settings['attr'] = wrapped.__name__
        return wrapped


def includeme(config):
    config.add_directive('get_viewlet_mapper', get_viewlet_mapper)
    config.add_directive('add_viewlet', add_viewlet)
