"""Build a gcx.json snapshot of the current QGIS canvas and export the
project's vector layers to GeoJSON under datasets/."""

import math
import os
import re
from collections import OrderedDict
from urllib.parse import unquote

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsMapLayer,
    QgsPointXY,
    QgsProject,
    QgsRenderContext,
    QgsVectorFileWriter,
    QgsWkbTypes,
)


GCX_FILENAME = "gcx.json"
DATASETS_DIR = "datasets"

# Built-in XYZ basemap URL templates we recognise so the exported gcx.json
# uses a short alias (`"background": "osm"`) instead of the raw URL.
_BACKGROUND_ALIAS_BY_HOST = {
    "tile.openstreetmap.org": "osm",
    "tiles.openfantasymaps.org": "ofm",
}

# Slippy-map zoom calibration: scale denominator at zoom 0 at the equator,
# 256-px tiles, 96 DPI. The same constant Leaflet/Mapbox use.
_SCALE_AT_Z0_EQUATOR = 559082264.028


def _slugify(value):
    value = re.sub(r"[^\w\-]+", "_", value, flags=re.UNICODE).strip("_")
    return value.lower() or "layer"


def _zoom_from_scale(scale_denominator, latitude_deg):
    if not scale_denominator or scale_denominator <= 0:
        return 5
    lat_rad = math.radians(max(-85.0, min(85.0, latitude_deg)))
    z = math.log2(_SCALE_AT_Z0_EQUATOR * math.cos(lat_rad) / scale_denominator)
    return max(0, min(22, int(round(z))))


def _to_wgs84(point_xy, src_crs):
    dst = QgsCoordinateReferenceSystem("EPSG:4326")
    if src_crs == dst:
        return point_xy
    xform = QgsCoordinateTransform(src_crs, dst, QgsProject.instance())
    return xform.transform(QgsPointXY(point_xy.x(), point_xy.y()))


def _primary_color_hex(layer, fallback="#3388ff"):
    """Best-effort: pull the dominant color from a vector layer's renderer."""
    renderer = layer.renderer()
    if renderer is None:
        return fallback
    try:
        symbols = renderer.symbols(QgsRenderContext())
    except Exception:
        symbols = []
    if not symbols and hasattr(renderer, "symbol"):
        try:
            symbols = [renderer.symbol()]
        except Exception:
            symbols = []
    for sym in symbols:
        if sym is None:
            continue
        color = sym.color()
        if color and color.isValid():
            return color.name()
    return fallback


def _style_for_layer(layer):
    """Mapbox-shaped style block keyed off geometry type. The line/polygon
    options here are best-effort — confirm against your gcx renderer."""
    color = _primary_color_hex(layer)
    geom = QgsWkbTypes.geometryType(layer.wkbType())

    if geom == QgsWkbTypes.PointGeometry:
        return OrderedDict([
            ("style", "mapbox"),
            ("mode", "marker"),
            ("markerType", "circle"),
            ("options", OrderedDict([
                ("radius", 4),
                ("fillColor", color),
                ("color", "#000"),
                ("weight", 1),
                ("opacity", 1),
                ("fillOpacity", 0.6),
            ])),
        ])
    if geom == QgsWkbTypes.LineGeometry:
        return OrderedDict([
            ("style", "mapbox"),
            ("mode", "line"),
            ("options", OrderedDict([
                ("color", color),
                ("weight", 2),
                ("opacity", 1),
            ])),
        ])
    # Polygon (and unknown geometries fall through to polygon defaults).
    return OrderedDict([
        ("style", "mapbox"),
        ("mode", "polygon"),
        ("options", OrderedDict([
            ("color", "#000"),
            ("weight", 1),
            ("opacity", 1),
            ("fillColor", color),
            ("fillOpacity", 0.4),
        ])),
    ])


def _xyz_url_from_raster(layer):
    """Pull an `{z}/{x}/{y}` URL template out of a QGIS XYZ raster layer's
    datasource string. Returns None for raster layers that aren't XYZ
    (WMS proper, GeoPackage rasters, local TIFFs, …)."""
    if layer.type() != QgsMapLayer.RasterLayer:
        return None
    if (layer.providerType() or "").lower() != "wms":
        return None
    uri = layer.dataProvider().dataSourceUri() if layer.dataProvider() else ""
    parts = dict(p.split("=", 1) for p in uri.split("&") if "=" in p)
    if parts.get("type", "").lower() != "xyz":
        return None
    raw = parts.get("url")
    if not raw:
        return None
    return unquote(raw)


def _detect_background(layer_tree_root):
    """Find the topmost XYZ raster basemap in the QGIS layer tree and
    convert it to a `background` value. Returns either:

    - a short alias string (`"osm"`, `"ofm"`) when the URL matches a
      known provider, so the exported config stays terse; or
    - an OrderedDict `{ "url": ..., "attribution": ... }` for arbitrary
      tile services; or
    - None when no XYZ basemap is in the project (the frontend will use
      its default style).
    """
    for node in layer_tree_root.findLayers():
        layer = node.layer()
        if layer is None:
            continue
        url = _xyz_url_from_raster(layer)
        if not url:
            continue
        for host, alias in _BACKGROUND_ALIAS_BY_HOST.items():
            if host in url:
                return alias
        spec = OrderedDict([("url", url)])
        attribution = layer.attribution() if hasattr(layer, "attribution") else ""
        if attribution:
            spec["attribution"] = attribution
        return spec
    return None


def _is_interactive(layer):
    """Map QGIS's per-layer `Identifiable` flag to gcx's `interactive`
    field. A layer the user marked non-identifiable in QGIS (Layer →
    Properties → Rendering → "Identifiable") is exported as visual
    context only — no popup, no click handler in the frontend."""
    flags_attr = getattr(layer, "flags", None)
    if not callable(flags_attr):
        return True
    try:
        return bool(flags_attr() & QgsMapLayer.Identifiable)
    except (AttributeError, TypeError):
        return True


def _project_title():
    project = QgsProject.instance()
    if project.title():
        return project.title()
    fname = project.fileName()
    if fname:
        return os.path.splitext(os.path.basename(fname))[0]
    return "Untitled map"


def _write_geojson(layer, target_path, target_crs):
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GeoJSON"
    options.fileEncoding = "UTF-8"
    options.ct = QgsCoordinateTransform(layer.crs(), target_crs, QgsProject.instance())
    if hasattr(QgsVectorFileWriter, "writeAsVectorFormatV3"):
        err, msg, _, _ = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer, target_path, QgsProject.instance().transformContext(), options
        )
    else:
        err, msg = QgsVectorFileWriter.writeAsVectorFormatV2(
            layer, target_path, QgsProject.instance().transformContext(), options
        )
    if err != QgsVectorFileWriter.NoError:
        raise RuntimeError(f"GeoJSON export failed for {layer.name()}: {msg}")


def build_snapshot(iface, output_dir, geojson_crs="EPSG:4326",
                   minzoom=1, maxzoom=20):
    """Generate gcx.json + datasets/*.geojson under output_dir.

    Returns (snapshot_dict, [(repo_relative_path, absolute_path), ...]).
    GeoJSON files are emitted to datasets/<slug>.geojson; the gcx.json
    itself is NOT included in the returned files list (callers add it).
    """
    canvas = iface.mapCanvas()
    project = QgsProject.instance()
    canvas_crs = canvas.mapSettings().destinationCrs()

    target_crs = QgsCoordinateReferenceSystem(geojson_crs)
    if not target_crs.isValid():
        target_crs = QgsCoordinateReferenceSystem("EPSG:4326")

    # Center in WGS84 and serialised as [lat, lon] to match the gcx sample.
    center_native = canvas.center()
    center_wgs = _to_wgs84(center_native, canvas_crs)
    center_latlon = [round(center_wgs.y(), 6), round(center_wgs.x(), 6)]
    startzoom = _zoom_from_scale(canvas.scale(), center_wgs.y())

    os.makedirs(os.path.join(output_dir, DATASETS_DIR), exist_ok=True)

    layer_tree_root = project.layerTreeRoot()
    ordered_layers = [
        node.layer()
        for node in layer_tree_root.findLayers()
        if node.layer() is not None and node.layer().type() == QgsMapLayer.VectorLayer
    ]

    used_slugs = set()
    datasources = []
    layers_meta = []
    files_to_upload = []

    for layer in ordered_layers:
        base_slug = _slugify(layer.name())
        slug = base_slug
        i = 2
        while slug in used_slugs:
            slug = f"{base_slug}_{i}"
            i += 1
        used_slugs.add(slug)

        rel_path = f"{DATASETS_DIR}/{slug}.geojson"
        abs_path = os.path.join(output_dir, rel_path)
        _write_geojson(layer, abs_path, target_crs)
        files_to_upload.append((rel_path, abs_path))

        datasources.append(OrderedDict([
            ("name", slug),
            ("type", "geojson+http+remote"),
            ("conf", OrderedDict([("source", rel_path)])),
        ]))

        entry = OrderedDict([
            ("name", slug),
            ("type", "features"),
            ("datasource", slug),
        ])
        # QGIS's `Identifiable` flag is the user's existing way of saying
        # "this layer is context, don't probe it" — we surface that intent
        # in gcx as `"interactive": false`. Only emit when explicitly off
        # so default exports stay clean.
        if not _is_interactive(layer):
            entry["interactive"] = False
        entry["style"] = _style_for_layer(layer)
        layers_meta.append(entry)

    snapshot = OrderedDict([
        ("title", _project_title()),
        ("type", "2d"),
        ("center", center_latlon),
        ("minzoom", minzoom),
        ("startzoom", startzoom),
        ("maxzoom", maxzoom),
    ])
    background = _detect_background(layer_tree_root)
    if background is not None:
        snapshot["background"] = background
    snapshot["datasources"] = datasources
    snapshot["layers"] = layers_meta
    return snapshot, files_to_upload
