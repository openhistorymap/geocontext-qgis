"""Build a gcx.json snapshot of the current QGIS canvas and export the
project's vector layers to GeoJSON under datasets/."""

import math
import os
import re
from collections import OrderedDict

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

        layers_meta.append(OrderedDict([
            ("name", slug),
            ("type", "features"),
            ("datasource", slug),
            ("style", _style_for_layer(layer)),
        ]))

    snapshot = OrderedDict([
        ("title", _project_title()),
        ("type", "2d"),
        ("center", center_latlon),
        ("minzoom", minzoom),
        ("startzoom", startzoom),
        ("maxzoom", maxzoom),
        ("datasources", datasources),
        ("layers", layers_meta),
    ])
    return snapshot, files_to_upload
