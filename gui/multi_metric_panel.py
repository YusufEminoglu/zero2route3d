"""Multi-Metric Longitudinal Profile Ribbon Panel for 02Route 3D.

Renders stacked tapered-ribbon ('worm') profiles along the route's length
(Elevation, Slope, Speed, and optionally Heat/LST and Greenery) using plain
QPainter in QGIS native dock without third-party charting libraries.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from qgis.PyQt.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import (
    QBrush,
    QColor,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
)
from qgis.PyQt.QtWidgets import QSizePolicy, QWidget


class MetricSeries:
    """Represents one metric series along a computed 3D route."""

    def __init__(
        self,
        key: str,
        name: str,
        icon: str,
        color_hex: str,
        values: List[float],
    ) -> None:
        self.key = key
        self.name = name
        self.icon = icon
        self.color_hex = color_hex
        self.values = values
        clean_vals = [v for v in values if v is not None and math.isfinite(v)]
        self.min_val = min(clean_vals) if clean_vals else 0.0
        self.max_val = max(clean_vals) if clean_vals else 1.0

    def format_value(self, val: Optional[float]) -> str:
        if val is None or not math.isfinite(val):
            return "—"
        if self.key == "elevation":
            return f"{val:.1f} m"
        elif self.key == "slope":
            sign = "+" if val > 0 else ""
            return f"{sign}{val:.1f}%"
        elif self.key == "speed":
            return f"{val:.1f} km/h"
        elif self.key in ("lst", "greenery"):
            return f"{val * 100.0:.0f}%"
        return f"{val:.1f}"


class MultiMetricPanel(QWidget):
    """Stacked multi-metric tapered ribbon profile panel widget."""

    seek_requested = pyqtSignal(float)  # Emits progress fraction [0.0, 1.0]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("MultiMetricPanel")
        self.metrics: List[MetricSeries] = []
        self.progress: float = 0.0
        self.label_width: float = 120.0
        self.right_margin: float = 12.0
        self.setMouseTracking(True)
        size_policy_horizontal = getattr(getattr(QSizePolicy, "Policy", QSizePolicy), "Expanding", 7)
        size_policy_vertical = getattr(getattr(QSizePolicy, "Policy", QSizePolicy), "Preferred", 5)
        self.setSizePolicy(size_policy_horizontal, size_policy_vertical)

    def sizeHint(self) -> QSize:
        row_count = max(1, len(self.metrics))
        return QSize(280, int(row_count * 26 + 8))

    def minimumSizeHint(self) -> QSize:
        row_count = max(1, len(self.metrics))
        return QSize(220, int(row_count * 22 + 6))

    def clear(self) -> None:
        """Clear all active metric rows and reset playback progress."""
        self.metrics = []
        self.progress = 0.0
        self.updateGeometry()
        self.update()

    def set_progress(self, progress_fraction: float) -> None:
        """Update live progress indicator needle and metric numeric readouts."""
        safe_prog = max(0.0, min(1.0, float(progress_fraction)))
        if abs(safe_prog - self.progress) > 1e-4 or not math.isfinite(self.progress):
            self.progress = safe_prog
            self.update()

    def set_route_profile(
        self,
        elevation_profile: List[Dict[str, Any]],
        profile_color: str = "#0284c7",
    ) -> None:
        """Load vertex metrics from RouteStatistics.elevation_profile."""
        if not elevation_profile or len(elevation_profile) < 2:
            self.clear()
            return

        new_metrics: List[MetricSeries] = []

        # 1. Elevation (always available)
        elev_vals = [
            float(p["elevation_m"])
            if "elevation_m" in p and p["elevation_m"] is not None and math.isfinite(float(p["elevation_m"]))
            else 0.0
            for p in elevation_profile
        ]
        new_metrics.append(
            MetricSeries(
                key="elevation",
                name="Elevation",
                icon="📈",
                color_hex=profile_color or "#0284c7",
                values=elev_vals,
            )
        )

        # 2. Slope (always available)
        slope_vals = [
            float(p["slope_pct"])
            if "slope_pct" in p and p["slope_pct"] is not None and math.isfinite(float(p["slope_pct"]))
            else 0.0
            for p in elevation_profile
        ]
        new_metrics.append(
            MetricSeries(
                key="slope",
                name="Slope",
                icon="📐",
                color_hex="#f59e0b",
                values=slope_vals,
            )
        )

        # 3. Speed (always available)
        speed_vals = [
            float(p["speed_kmh"])
            if "speed_kmh" in p and p["speed_kmh"] is not None and math.isfinite(float(p["speed_kmh"]))
            else 5.0
            for p in elevation_profile
        ]
        new_metrics.append(
            MetricSeries(
                key="speed",
                name="Speed",
                icon="⚡",
                color_hex="#6366f1",
                values=speed_vals,
            )
        )

        # 4. Heat / LST (optional -- only when real raster was supplied)
        has_lst = any("lst_normalized" in p and p["lst_normalized"] is not None for p in elevation_profile)
        if has_lst:
            lst_vals = [
                float(p["lst_normalized"])
                if "lst_normalized" in p and p["lst_normalized"] is not None and math.isfinite(float(p["lst_normalized"]))
                else 0.0
                for p in elevation_profile
            ]
            new_metrics.append(
                MetricSeries(
                    key="lst",
                    name="Heat (LST)",
                    icon="🌡️",
                    color_hex="#ef4444",
                    values=lst_vals,
                )
            )

        # 5. Greenery / NDVI (optional -- only when real raster was supplied)
        has_green = any("ndvi_normalized" in p and p["ndvi_normalized"] is not None for p in elevation_profile)
        if has_green:
            green_vals = [
                float(p["ndvi_normalized"])
                if "ndvi_normalized" in p and p["ndvi_normalized"] is not None and math.isfinite(float(p["ndvi_normalized"]))
                else 0.0
                for p in elevation_profile
            ]
            new_metrics.append(
                MetricSeries(
                    key="greenery",
                    name="Greenery",
                    icon="🌳",
                    color_hex="#10b981",
                    values=green_vals,
                )
            )

        self.metrics = new_metrics
        self.progress = 0.0
        self.updateGeometry()
        self.update()

    def mousePressEvent(self, event: Any) -> None:
        self._handle_mouse_seek(event)

    def mouseMoveEvent(self, event: Any) -> None:
        _LeftButton = getattr(getattr(Qt, "MouseButton", Qt), "LeftButton", 1)
        if event.buttons() & _LeftButton:
            self._handle_mouse_seek(event)

    def _handle_mouse_seek(self, event: Any) -> None:
        if not self.metrics:
            return
        ribbon_left = self.label_width + 6.0
        ribbon_right = self.width() - self.right_margin
        ribbon_width = max(10.0, ribbon_right - ribbon_left)
        pos_x = event.pos().x() if hasattr(event, "pos") else event.x()
        fraction = max(0.0, min(1.0, (pos_x - ribbon_left) / ribbon_width))
        self.set_progress(fraction)
        self.seek_requested.emit(fraction)

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self)
        _Antialiasing = getattr(getattr(QPainter, "RenderHint", QPainter), "Antialiasing", 1)
        painter.setRenderHint(_Antialiasing, True)

        w = float(self.width())
        h = float(self.height())

        # Theme detection
        _Window = getattr(getattr(QPalette, "ColorRole", QPalette), "Window", 10)
        is_dark = self.palette().color(_Window).lightness() < 128
        text_color = QColor("#f1f5f9") if is_dark else QColor("#334155")
        track_bg = QColor("#1e293b") if is_dark else QColor("#f8fafc")
        track_border = QColor("#334155") if is_dark else QColor("#e2e8f0")
        divider_color = QColor("#334155") if is_dark else QColor("#f1f5f9")

        if not self.metrics:
            painter.setPen(QColor("#94a3b8"))
            font = painter.font()
            font.setPointSize(9)
            font.setItalic(True)
            painter.setFont(font)
            _AlignCenter = getattr(getattr(Qt, "AlignmentFlag", Qt), "AlignCenter", 0x0084)
            painter.drawText(self.rect(), _AlignCenter, "Compute a route to view multi-metric profile ribbons")
            return

        ribbon_left = self.label_width + 6.0
        ribbon_right = w - self.right_margin
        ribbon_width = max(10.0, ribbon_right - ribbon_left)

        row_count = len(self.metrics)
        row_height = max(20.0, (h - 8.0) / row_count)

        font = painter.font()
        font.setPointSize(8)
        font.setBold(True)

        needle_x = ribbon_left + self.progress * ribbon_width

        _AlignLeft = getattr(getattr(Qt, "AlignmentFlag", Qt), "AlignLeft", 0x0001)
        _AlignRight = getattr(getattr(Qt, "AlignmentFlag", Qt), "AlignRight", 0x0002)
        _AlignVCenter = getattr(getattr(Qt, "AlignmentFlag", Qt), "AlignVCenter", 0x0080)

        for row_idx, metric in enumerate(self.metrics):
            row_y = 4.0 + row_idx * row_height
            y_mid = row_y + row_height / 2.0

            # 1. Metric Label & Live Value on the left
            painter.setFont(font)
            painter.setPen(text_color)
            n_pts = len(metric.values)
            val_idx = min(n_pts - 1, max(0, int(self.progress * (n_pts - 1))))
            live_val = metric.values[val_idx] if val_idx < n_pts else None
            live_val_str = metric.format_value(live_val)

            # Name with icon
            name_rect = QRectF(4.0, row_y, self.label_width - 46.0, row_height)
            painter.drawText(name_rect, _AlignLeft | _AlignVCenter, f"{metric.icon} {metric.name}")

            # Live numeric readout
            painter.setPen(QColor(metric.color_hex))
            val_rect = QRectF(self.label_width - 44.0, row_y, 44.0, row_height)
            painter.drawText(val_rect, _AlignRight | _AlignVCenter, live_val_str)

            # 2. Track Background
            track_rect = QRectF(ribbon_left, row_y + 2.0, ribbon_width, row_height - 4.0)
            painter.setPen(track_border)
            painter.setBrush(track_bg)
            painter.drawRoundedRect(track_rect, 3.0, 3.0)

            # 3. Tapered Ribbon Polygon
            values = metric.values
            min_v = metric.min_val
            max_v = metric.max_val
            rng = max(0.001, max_v - min_v)
            max_half_t = max(2.0, min(8.0, (row_height - 6.0) / 2.0))
            min_half_t = 1.4

            top_points: List[QPointF] = []
            bottom_points: List[QPointF] = []

            for i, val in enumerate(values):
                s = i / max(1, n_pts - 1)
                x = ribbon_left + s * ribbon_width
                u = max(0.0, min(1.0, (val - min_v) / rng))
                env = math.sin(math.pi * s) ** 0.65
                half_t = (min_half_t + u * (max_half_t - min_half_t)) * env
                top_points.append(QPointF(x, y_mid - half_t))
                bottom_points.append(QPointF(x, y_mid + half_t))

            path = QPainterPath()
            if top_points:
                path.moveTo(top_points[0])
                for pt in top_points[1:]:
                    path.lineTo(pt)
                for pt in reversed(bottom_points):
                    path.lineTo(pt)
                path.closeSubpath()

                m_color = QColor(metric.color_hex)
                fill_color = QColor(m_color.red(), m_color.green(), m_color.blue(), 205)
                painter.setPen(QPen(m_color, 1.0))
                painter.setBrush(QBrush(fill_color))
                painter.drawPath(path)

            # 4. End Dot marker at right edge
            dot_color = QColor(metric.color_hex)
            painter.setPen(QPen(QColor("#ffffff"), 1.0))
            painter.setBrush(QBrush(dot_color))
            painter.drawEllipse(QPointF(ribbon_right, y_mid), 3.0, 3.0)

            # 5. Row divider line
            if row_idx < row_count - 1:
                painter.setPen(divider_color)
                painter.drawLine(QPointF(4.0, row_y + row_height), QPointF(w - 4.0, row_y + row_height))

        # 6. Shared Needle spanning across all rows
        if self.metrics:
            needle_pen = QPen(QColor("#ef4444"), 2.0)
            painter.setPen(needle_pen)
            painter.drawLine(QPointF(needle_x, 2.0), QPointF(needle_x, h - 2.0))
