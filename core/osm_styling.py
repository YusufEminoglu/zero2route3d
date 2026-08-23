"""Native QGIS styling inspired by the 02Agent OSM Downloader atlas theme."""
from __future__ import annotations

from typing import Any


ATLAS = {
    "base": "#566b6f",
    "roads_major": "#2f4858",
    "roads_minor": "#d8d7ce",
    "greens": "#7fb069",
    "water": "#3a86b5",
    "buildings": {
        "residential": "#d9c8b4",
        "commercial": "#a9bdd0",
        "industrial": "#c4b79a",
        "civic": "#b8d5ce",
        "worship": "#d9c7df",
        "other": "#cfd0c8",
    },
}


def _shade(color: str, factor: float) -> str:
    rgb = [int(color[index:index + 2], 16) for index in (1, 3, 5)]
    if factor <= 1.0:
        values = [round(value * factor) for value in rgb]
    else:
        amount = min(1.0, factor - 1.0)
        values = [round(value + (255 - value) * amount) for value in rgb]
    return "#%02x%02x%02x" % tuple(values)


def _fill(color: str, opacity: float = 0.82) -> Any:
    from qgis.core import QgsFillSymbol

    symbol = QgsFillSymbol.createSimple({
        "color": color,
        "outline_color": _shade(color, 0.72),
        "outline_width": "0.18",
        "style": "solid",
    })
    symbol.setOpacity(opacity)
    return symbol


def _line(color: str, width: float, dashed: bool = False) -> Any:
    from qgis.core import QgsLineSymbol

    properties = {
        "color": color,
        "width": str(width),
        "capstyle": "round",
        "joinstyle": "round",
    }
    if dashed:
        properties["line_style"] = "dash"
    return QgsLineSymbol.createSimple(properties)


def _renderer(expression: str, entries: Any) -> Any:
    from qgis.core import QgsCategorizedSymbolRenderer, QgsRendererCategory

    return QgsCategorizedSymbolRenderer(
        expression,
        [QgsRendererCategory(value, symbol, label) for value, label, symbol in entries],
    )


def _building_renderer() -> Any:
    colors = ATLAS["buildings"]
    expression = (
        "CASE"
        " WHEN lower(coalesce(\"building\",'')) IN ('apartments','residential','house','detached','terrace','dormitory','bungalow','semidetached_house','hut') THEN 'building_residential'"
        " WHEN lower(coalesce(\"building\",'')) IN ('commercial','retail','office','supermarket','kiosk','hotel') THEN 'building_commercial'"
        " WHEN lower(coalesce(\"building\",'')) IN ('industrial','warehouse','manufacture','hangar','factory') THEN 'building_industrial'"
        " WHEN lower(coalesce(\"building\",'')) IN ('church','mosque','temple','synagogue','cathedral','chapel') THEN 'building_worship'"
        " WHEN coalesce(\"building\",'') <> '' THEN 'building_civic'"
        " WHEN lower(coalesce(\"type\",'')) IN ('commercial','retail','office') THEN 'building_commercial'"
        " WHEN lower(coalesce(\"type\",'')) IN ('industrial','warehouse') THEN 'building_industrial'"
        " ELSE 'building_other' END"
    )
    labels = {
        "residential": "Residential buildings",
        "commercial": "Commercial buildings",
        "industrial": "Industrial buildings",
        "civic": "Civic buildings",
        "worship": "Worship buildings",
    }
    entries = [
        (f"building_{key}", labels[key], _fill(color))
        for key, color in colors.items()
        if key != "other"
    ]
    entries.append(("building_other", "Other buildings", _fill(colors["other"], 0.66)))
    return _renderer(expression, entries)


def _road_renderer() -> Any:
    major = ATLAS["roads_major"]
    minor = ATLAS["roads_minor"]
    expression = (
        "CASE"
        " WHEN lower(coalesce(\"highway\",'')) IN ('motorway','trunk','motorway_link','trunk_link') THEN 'major'"
        " WHEN lower(coalesce(\"highway\",'')) IN ('primary','primary_link') THEN 'primary'"
        " WHEN lower(coalesce(\"highway\",'')) IN ('secondary','secondary_link') THEN 'secondary'"
        " WHEN lower(coalesce(\"highway\",'')) IN ('tertiary','tertiary_link') THEN 'tertiary'"
        " WHEN lower(coalesce(\"highway\",'')) IN ('residential','unclassified','living_street','road') THEN 'residential'"
        " WHEN lower(coalesce(\"highway\",'')) IN ('service','track') THEN 'service'"
        " WHEN lower(coalesce(\"highway\",'')) IN ('footway','path','pedestrian','steps','corridor','bridleway','cycleway') THEN 'active'"
        " WHEN lower(coalesce(\"type\",'')) IN ('primary','secondary','tertiary') THEN lower(\"type\")"
        " ELSE 'other' END"
    )
    widths = {
        "major": 1.55,
        "primary": 1.28,
        "secondary": 1.05,
        "tertiary": 0.82,
        "residential": 0.66,
        "service": 0.48,
        "active": 0.55,
        "other": 0.58,
    }
    entries = [
        ("major", "Motorway and trunk", _line(major, widths["major"])),
        ("primary", "Primary roads", _line(major, widths["primary"])),
        ("secondary", "Secondary roads", _line(major, widths["secondary"])),
        ("tertiary", "Tertiary roads", _line(major, widths["tertiary"])),
        ("residential", "Residential roads", _line(minor, widths["residential"])),
        ("service", "Service roads", _line(minor, widths["service"])),
        ("active", "Walking and cycling", _line(ATLAS["greens"], widths["active"], True)),
        ("other", "Other lines", _line(ATLAS["base"], widths["other"])),
    ]
    return _renderer(expression, entries)


def apply_osm_atlas_style(layer: Any) -> bool:
    """Apply the sibling downloader's atlas visual language to a vector layer."""
    try:
        geometry_type = int(layer.geometryType())
        from qgis.core import QgsWkbTypes

        if geometry_type == int(QgsWkbTypes.PolygonGeometry):
            renderer = _building_renderer()
        elif geometry_type == int(QgsWkbTypes.LineGeometry):
            renderer = _road_renderer()
        else:
            return False
        layer.setRenderer(renderer)
        layer.setCustomProperty("zero2route3d/style", "02Agent OSM Downloader — Civic Atlas")
        layer.triggerRepaint()
        return True
    except Exception:
        return False


__all__ = ["apply_osm_atlas_style"]
