"""Live QGIS 2D Map Canvas Rubberband and Tracking Marker Overlay."""
from __future__ import annotations

import contextlib
from typing import List, Optional, Sequence, Tuple

from qgis.PyQt.QtGui import QColor
from qgis.gui import QgsMapCanvas, QgsRubberBand, QgsVertexMarker
from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
    QgsWkbTypes,
)


class CanvasRouteOverlay:
    """Manages visual 2D polyline overlay and animated tracking pin on QGIS map canvas."""

    def __init__(self, canvas: QgsMapCanvas) -> None:
        self.canvas = canvas
        self.rubber_band: Optional[QgsRubberBand] = None
        self.tracker_pin: Optional[QgsVertexMarker] = None
        self.coords_wgs84: List[Tuple[float, float, float]] = []

    def display_route(self, coords_3d: Sequence[Tuple[float, float, float]]) -> None:
        """Render route geometry on QGIS map canvas with glowing styling."""
        self.clear()
        if not self.canvas or len(coords_3d) < 2:
            return

        with contextlib.suppress(Exception):
            import sip
            if sip.isdeleted(self.canvas):
                return

        self.coords_wgs84 = list(coords_3d)
        geom_type = getattr(getattr(Qgis, "GeometryType", Qgis), "Line", getattr(QgsWkbTypes, "LineGeometry", 1))
        self.rubber_band = QgsRubberBand(self.canvas, geom_type)
        self.rubber_band.setColor(QColor(56, 189, 248, 200))
        self.rubber_band.setWidth(4)

        crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        crs_canvas = self.canvas.mapSettings().destinationCrs()
        transform = None
        if crs_canvas.isValid() and crs_canvas != crs_wgs84:
            with contextlib.suppress(Exception):
                transform = QgsCoordinateTransform(crs_wgs84, crs_canvas, QgsProject.instance())

        for c in self.coords_wgs84:
            pt = QgsPointXY(c[0], c[1])
            if transform is not None and transform.isValid():
                with contextlib.suppress(Exception):
                    pt = transform.transform(pt)
            self.rubber_band.addPoint(pt, True)

        self.rubber_band.show()

        # Create tracking pin
        self.tracker_pin = QgsVertexMarker(self.canvas)
        _IconType = getattr(QgsVertexMarker, "IconType", QgsVertexMarker)
        self.tracker_pin.setIconType(getattr(_IconType, "ICON_CIRCLE", getattr(QgsVertexMarker, "ICON_CIRCLE", 1)))
        self.tracker_pin.setColor(QColor(244, 63, 94))
        self.tracker_pin.setIconSize(12)
        self.tracker_pin.setPenWidth(3)

        p_start = QgsPointXY(self.coords_wgs84[0][0], self.coords_wgs84[0][1])
        if transform is not None and transform.isValid():
            with contextlib.suppress(Exception):
                p_start = transform.transform(p_start)
        self.tracker_pin.setCenter(p_start)
        self.tracker_pin.show()

    def update_tracker_position(self, fraction: float) -> None:
        """Update tracking pin position (fraction from 0.0 to 1.0) along route."""
        if not self.tracker_pin or len(self.coords_wgs84) < 2 or not self.canvas:
            return

        with contextlib.suppress(Exception):
            import sip
            if sip.isdeleted(self.canvas) or sip.isdeleted(self.tracker_pin):
                return

        idx = int(fraction * (len(self.coords_wgs84) - 1))
        idx = max(0, min(len(self.coords_wgs84) - 1, idx))
        c = self.coords_wgs84[idx]

        crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        crs_canvas = self.canvas.mapSettings().destinationCrs()
        pt = QgsPointXY(c[0], c[1])
        if crs_canvas.isValid() and crs_canvas != crs_wgs84:
            with contextlib.suppress(Exception):
                transform = QgsCoordinateTransform(crs_wgs84, crs_canvas, QgsProject.instance())
                if transform.isValid():
                    pt = transform.transform(pt)

        with contextlib.suppress(Exception):
            self.tracker_pin.setCenter(pt)

    def clear(self) -> None:
        """Remove overlays and reset canvas markers safely."""
        if self.rubber_band is not None:
            with contextlib.suppress(Exception):
                self.rubber_band.hide()
            with contextlib.suppress(Exception):
                self.rubber_band.reset()
            with contextlib.suppress(Exception):
                import sip
                if not sip.isdeleted(self.rubber_band):
                    sip.delete(self.rubber_band)
            self.rubber_band = None

        if self.tracker_pin is not None:
            with contextlib.suppress(Exception):
                self.tracker_pin.hide()
            with contextlib.suppress(Exception):
                import sip
                if not sip.isdeleted(self.tracker_pin):
                    sip.delete(self.tracker_pin)
            self.tracker_pin = None

        self.coords_wgs84.clear()
