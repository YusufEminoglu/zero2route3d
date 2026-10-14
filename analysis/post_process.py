"""Shared output styling and metadata for the Processing algorithms.

Every algorithm used to ship its output completely unstyled: no renderer, no
field aliases, no layer abstract. The identical action taken from the dock
produced a styled layer, so headless and Modeler users got a strictly worse
result from the same code.

The critical detail is the lookup. Inside ``postProcessAlgorithm`` the destination
layer must be fetched with ``context.getMapLayer(layer_id)``. Using
``QgsProject.instance().mapLayer(id)`` there silently returns None, so every
styling and metadata call becomes a no-op that raises nothing and logs nothing.
"""
from __future__ import annotations

import contextlib
from typing import Any, Dict, Optional

from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsSingleSymbolRenderer,
)


def resolve_output_layer(
    context: QgsProcessingContext,
    layer_id: str,
) -> Optional[Any]:
    """The destination layer for a sink id, or None if it is not available.

    Always goes through context.getMapLayer -- see the module docstring for why
    QgsProject.instance().mapLayer() must not be used here.
    """
    if not layer_id:
        return None
    return context.getMapLayer(layer_id)


def apply_field_aliases(layer: Any, aliases: Dict[str, str]) -> None:
    """Give the output fields readable names in the attribute table."""
    if layer is None or not hasattr(layer, "fields"):
        return
    names = layer.fields().names()
    for field_name, alias in aliases.items():
        if field_name in names:
            with contextlib.suppress(Exception):
                layer.setFieldAlias(names.index(field_name), alias)


def set_layer_metadata(layer: Any, title: str, abstract: str) -> None:
    """Record what produced the layer and what its values mean."""
    if layer is None:
        return
    with contextlib.suppress(Exception):
        metadata = layer.metadata()
        metadata.setTitle(title)
        metadata.setAbstract(abstract)
        layer.setMetadata(metadata)


def style_route_line(layer: Any, color_hex: str = "#0ea5e9", width_mm: float = 1.0) -> None:
    """Apply a single-symbol line renderer to a routed output."""
    if layer is None:
        return
    with contextlib.suppress(Exception):
        symbol = QgsLineSymbol.createSimple({})
        symbol.setColor(QColor(color_hex))
        symbol.setWidth(width_mm)
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        layer.triggerRepaint()


def style_points(layer: Any, color_hex: str = "#ef4444", size_mm: float = 2.6) -> None:
    """Apply a single-symbol marker renderer to a point output."""
    if layer is None:
        return
    with contextlib.suppress(Exception):
        symbol = QgsMarkerSymbol.createSimple({})
        symbol.setColor(QColor(color_hex))
        symbol.setSize(size_mm)
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        layer.triggerRepaint()


def finalize_output(
    context: QgsProcessingContext,
    layer_id: str,
    title: str,
    abstract: str,
    aliases: Optional[Dict[str, str]] = None,
    line_color: Optional[str] = None,
    point_color: Optional[str] = None,
    feedback: Optional[QgsProcessingFeedback] = None,
) -> None:
    """Style and document one destination layer. Safe to call when it is absent."""
    layer = resolve_output_layer(context, layer_id)
    if layer is None:
        # A destination that is not being added to the project is normal (a file
        # sink, a Modeler intermediate); it is not an error.
        return
    if aliases:
        apply_field_aliases(layer, aliases)
    set_layer_metadata(layer, title, abstract)
    if line_color:
        style_route_line(layer, line_color)
    if point_color:
        style_points(layer, point_color)
    if feedback is not None:
        feedback.pushInfo(f"Styled output layer: {title}")
