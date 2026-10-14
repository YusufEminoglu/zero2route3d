"""Shared route-layer input handling for the export and reporting algorithms.

Four algorithms used to carry byte-identical copies of this extraction loop. Each
copy had the same two defects: it read Z from the flat 2D vertex list, whose
elements are QgsPointXY and therefore expose neither z() nor is3D(), and it
substituted a hard-coded two-point line in Izmir whenever extraction produced
nothing -- silently reporting distance, climb and calorie figures for a route the
user never asked for.

Vertices are read through constGet().vertices(), which preserves Z and handles
multi-part geometry without special cases.
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProcessingContext,
    QgsProcessingException,
)

WGS84 = "EPSG:4326"


def extract_route_coords_3d(
    layer: Any,
    context: QgsProcessingContext,
    parameter_label: str = "route layer",
) -> List[Tuple[float, float, float]]:
    """Return the first feature's vertices as WGS84 (lon, lat, z) tuples.

    Z is read from the real geometry via ``constGet().vertices()`` so LineStringZ
    input keeps its elevation. Raises QgsProcessingException when the layer holds
    no usable geometry -- never falls back to invented coordinates.
    """
    if layer is None:
        raise QgsProcessingException(
            f"The {parameter_label} could not be loaded. Select a valid 3D line layer."
        )

    transform: Optional[QgsCoordinateTransform] = None
    source_crs = layer.crs()
    if source_crs.isValid() and source_crs.authid() != WGS84:
        transform = QgsCoordinateTransform(
            source_crs,
            QgsCoordinateReferenceSystem(WGS84),
            context.transformContext(),
        )

    coords_3d: List[Tuple[float, float, float]] = []
    for feature in layer.getFeatures():
        geom = feature.geometry()
        if geom.isNull() or geom.isEmpty():
            continue

        abstract = geom.constGet()
        if abstract is None:
            continue

        for vertex in abstract.vertices():
            x, y = vertex.x(), vertex.y()
            if transform is not None:
                point = transform.transform(x, y)
                x, y = point.x(), point.y()
            z = vertex.z() if vertex.is3D() else 0.0
            coords_3d.append((float(x), float(y), float(z) if z == z else 0.0))

        if coords_3d:
            break

    if len(coords_3d) < 2:
        raise QgsProcessingException(
            f"The {parameter_label} contains no usable 3D geometry. Provide a "
            f"LineString/LineStringZ layer with at least two vertices."
        )

    return coords_3d


def has_real_z(coords_3d: List[Tuple[float, float, float]]) -> bool:
    """True when at least one vertex carries a non-zero elevation."""
    return any(abs(c[2]) > 1e-9 for c in coords_3d)
