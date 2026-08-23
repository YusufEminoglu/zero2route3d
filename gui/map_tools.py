"""Map canvas point selection tools for 02Route 3D."""
from __future__ import annotations

import contextlib
from typing import Callable, Optional

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QCursor
from qgis.gui import QgsMapCanvas, QgsMapToolEmitPoint, QgsVertexMarker
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
)


class RoutePointMapTool(QgsMapToolEmitPoint):
    """Interactive canvas tool to click and select Origin, Destination, or Waypoint."""

    point_selected = pyqtSignal(float, float, str)  # (lon, lat, point_type)
    point_captured = pyqtSignal(object)  # QgsPointXY / object

    def __init__(
        self,
        canvas: QgsMapCanvas,
        point_type: str = "start",  # 'start', 'end', 'waypoint'
        on_picked: Optional[Callable[[float, float, str], None]] = None,
    ) -> None:
        super().__init__(canvas)
        self.canvas = canvas
        self.point_type = point_type
        self.on_picked = on_picked
        cursor_shape = getattr(getattr(Qt, "CursorShape", Qt), "CrossCursor", getattr(Qt, "CrossCursor", 2))
        self.setCursor(QCursor(cursor_shape))
        self.marker: Optional[QgsVertexMarker] = None

    def canvasReleaseEvent(self, event) -> None:
        """Handle mouse click release on map canvas."""
        if not self.canvas:
            return
        point = self.toMapCoordinates(event.pos())

        # Transform point from canvas CRS to WGS84 EPSG:4326
        crs_src = self.canvas.mapSettings().destinationCrs()
        crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")

        lon = float(point.x())
        lat = float(point.y())
        pt_wgs84 = point

        if crs_src.isValid() and crs_src != crs_wgs84:
            with contextlib.suppress(Exception):
                transform = QgsCoordinateTransform(crs_src, crs_wgs84, QgsProject.instance())
                if transform.isValid():
                    pt_wgs84 = transform.transform(point)
                    lon = float(pt_wgs84.x())
                    lat = float(pt_wgs84.y())

        # Place visual vertex marker on canvas
        self._update_marker(point)

        self.point_captured.emit(pt_wgs84)
        self.point_selected.emit(lon, lat, self.point_type)
        if self.on_picked:
            self.on_picked(lon, lat, self.point_type)

    def _update_marker(self, canvas_point: QgsPointXY) -> None:
        """Draw or move temporary marker on canvas."""
        if not self.canvas:
            return
        with contextlib.suppress(Exception):
            import sip
            if sip.isdeleted(self.canvas):
                return

        if self.marker is None:
            self.marker = QgsVertexMarker(self.canvas)
            _IconType = getattr(QgsVertexMarker, "IconType", QgsVertexMarker)
            if self.point_type == "start":
                self.marker.setColor(QColor("#10B981"))
                self.marker.setIconType(getattr(_IconType, "ICON_BOX", getattr(QgsVertexMarker, "ICON_BOX", 2)))
            elif self.point_type == "end":
                self.marker.setColor(QColor("#EF4444"))
                self.marker.setIconType(getattr(_IconType, "ICON_X", getattr(QgsVertexMarker, "ICON_X", 3)))
            else:
                self.marker.setColor(QColor("#F59E0B"))
                self.marker.setIconType(getattr(_IconType, "ICON_CROSS", getattr(QgsVertexMarker, "ICON_CROSS", 0)))
            self.marker.setIconSize(10)
            self.marker.setPenWidth(3)

        with contextlib.suppress(Exception):
            self.marker.setCenter(canvas_point)

    def deactivate(self) -> None:
        """Clean up vertex marker when tool is deactivated."""
        if self.marker is not None:
            with contextlib.suppress(Exception):
                self.marker.hide()
            with contextlib.suppress(Exception):
                import sip
                if not sip.isdeleted(self.marker):
                    sip.delete(self.marker)
            self.marker = None
        super().deactivate()
