"""Coordinate handling shared by the Processing algorithms.

The routing engine works exclusively in WGS84 longitude/latitude: distances go
through the haversine formula and OSM bounding boxes are geographic by
definition. Nothing converted into it, so every algorithm pushed raw source-CRS
coordinates straight into Waypoint(lon=..., lat=...). With a projected project
CRS such as EPSG:3857 those are metres, which the engine then read as degrees --
producing routes near the Gulf of Guinea and Overpass queries that matched
nothing.

These helpers make the conversion explicit and are the only place the assumption
lives.
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsPointXY,
    QgsProcessingContext,
    QgsProcessingException,
    QgsRectangle,
)

WGS84_AUTHID = "EPSG:4326"


def wgs84() -> QgsCoordinateReferenceSystem:
    """The CRS the routing engine works in."""
    return QgsCoordinateReferenceSystem(WGS84_AUTHID)


def transform_to_wgs84(
    source_crs: QgsCoordinateReferenceSystem,
    context: QgsProcessingContext,
) -> Optional[QgsCoordinateTransform]:
    """A transform into WGS84, or None when the source is already WGS84."""
    if source_crs is None or not source_crs.isValid():
        return None
    if source_crs.authid() == WGS84_AUTHID:
        return None
    return QgsCoordinateTransform(source_crs, wgs84(), context.transformContext())


def point_to_wgs84(
    point: QgsPointXY,
    source_crs: QgsCoordinateReferenceSystem,
    context: QgsProcessingContext,
) -> QgsPointXY:
    """Reproject a single point into WGS84 lon/lat."""
    transform = transform_to_wgs84(source_crs, context)
    if transform is None:
        return point
    try:
        return transform.transform(point)
    except Exception as exc:  # pragma: no cover - depends on the CRS pair
        raise QgsProcessingException(
            f"Could not reproject a point from {source_crs.authid()} to WGS84: {exc}"
        ) from exc


def geometry_to_wgs84(
    geometry: QgsGeometry,
    source_crs: QgsCoordinateReferenceSystem,
    context: QgsProcessingContext,
) -> QgsGeometry:
    """Return a copy of the geometry in WGS84."""
    transform = transform_to_wgs84(source_crs, context)
    if transform is None:
        return geometry
    clone = QgsGeometry(geometry)
    if clone.transform(transform) != 0:
        raise QgsProcessingException(
            f"Could not reproject a geometry from {source_crs.authid()} to WGS84."
        )
    return clone


def rect_to_wgs84_bbox(
    rect: QgsRectangle,
    source_crs: QgsCoordinateReferenceSystem,
    context: QgsProcessingContext,
) -> Tuple[float, float, float, float]:
    """Convert an extent into the geographic bbox the Overpass API expects."""
    transform = transform_to_wgs84(source_crs, context)
    if transform is not None:
        try:
            rect = transform.transformBoundingBox(rect)
        except Exception as exc:  # pragma: no cover - depends on the CRS pair
            raise QgsProcessingException(
                f"Could not reproject the extent from {source_crs.authid()} to WGS84: {exc}"
            ) from exc
    return (rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum())


def require_matching_crs(
    layers: Sequence[Tuple[str, QgsCoordinateReferenceSystem]],
) -> None:
    """Reject silently-mixed CRSs across several inputs.

    Combining extents from layers in different CRSs produced a meaningless union
    rectangle, which then became the Overpass bbox.
    """
    valid = [(name, crs) for name, crs in layers if crs is not None and crs.isValid()]
    if len(valid) < 2:
        return
    first_name, first_crs = valid[0]
    for name, crs in valid[1:]:
        if crs != first_crs:
            raise QgsProcessingException(
                f"'{name}' is in {crs.authid()} but '{first_name}' is in "
                f"{first_crs.authid()}. Reproject the inputs to a common CRS first."
            )
