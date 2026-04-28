def classFactory(iface):
    from .plugin import GeoContextSyncPlugin
    return GeoContextSyncPlugin(iface)
