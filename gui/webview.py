"""Embedded 3D Studio Cockpit and Local WebGL Bridge for 02Route 3D."""
from __future__ import annotations

import contextlib
import json
import os
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional

from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QColor, QDesktopServices, QPainter, QPen
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .server import Route3DLocalServer


class Studio3DWebViewport(QWidget):
    """3D Studio Cockpit Viewport & Hardware-Accelerated WebGL Bridge."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.web_root = Path(__file__).resolve().parent.parent / "web"
        self.data_dir = self.web_root / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.current_route_file = self.data_dir / "current_route.json"

        self.server = Route3DLocalServer(web_root=self.web_root)
        self._current_geojson: Optional[Dict[str, Any]] = None

        self._build_cockpit_ui()

    def _build_cockpit_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(12)

        # Top Hero Card
        hero_card = QFrame()
        hero_card.setObjectName("heroCard")
        hero_card.setStyleSheet("""
            QFrame#heroCard {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0f172a, stop:1 #1e293b);
                border: 1px solid rgba(56, 189, 248, 0.35);
                border-radius: 12px;
                padding: 14px;
            }
        """)
        hero_layout = QVBoxLayout(hero_card)
        hero_layout.setContentsMargins(12, 12, 12, 12)
        hero_layout.setSpacing(8)

        title_row = QHBoxLayout()
        lbl_badge = QLabel("3D STUDIO")
        lbl_badge.setStyleSheet("""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0ea5e9, stop:1 #6366f1);
            color: #ffffff;
            font-weight: 800;
            font-size: 10px;
            padding: 3px 8px;
            border-radius: 6px;
        """)
        title_row.addWidget(lbl_badge)

        lbl_title = QLabel("Three.js 3D WebGL Cockpit")
        lbl_title.setStyleSheet("color: #f8fafc; font-size: 15px; font-weight: 700;")
        title_row.addWidget(lbl_title)
        title_row.addStretch()
        hero_layout.addLayout(title_row)

        lbl_desc = QLabel(
            "Hardware-accelerated 60 FPS 3D terrain viewer with thermal photon ribbons, "
            "kinematic avatars, 24-hr solar illumination, and underground cross-section slicing."
        )
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet("color: #94a3b8; font-size: 12px; line-height: 1.4;")
        hero_layout.addWidget(lbl_desc)

        # Primary Launch Button
        self.btn_launch_3d = QPushButton("🚀 Open 3D WebGL Studio in Browser (60 FPS)")
        self.btn_launch_3d.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_launch_3d.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284c7, stop:1 #2563eb);
                color: #ffffff;
                font-weight: 800;
                font-size: 14px;
                padding: 14px 20px;
                border-radius: 8px;
                border: none;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0369a1, stop:1 #1d4ed8);
            }
            QPushButton:pressed {
                background: #1e40af;
            }
        """)
        self.btn_launch_3d.clicked.connect(self.open_in_external_browser)
        hero_layout.addWidget(self.btn_launch_3d)

        # Auto-open checkbox
        self.chk_auto_open = QCheckBox("Automatically open 3D Studio when new route is computed")
        self.chk_auto_open.setChecked(True)
        self.chk_auto_open.setStyleSheet("color: #cbd5e1; font-size: 11px;")
        hero_layout.addWidget(self.chk_auto_open)

        main_layout.addWidget(hero_card)

        # 3D Route Telemetry Card
        telemetry_card = QFrame()
        telemetry_card.setStyleSheet("""
            QFrame {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 10px;
                padding: 10px;
            }
        """)
        tel_layout = QVBoxLayout(telemetry_card)
        tel_layout.setContentsMargins(8, 8, 8, 8)
        tel_layout.setSpacing(6)

        lbl_tel_header = QLabel("📈 3D Route Elevation & Grade Profile")
        lbl_tel_header.setStyleSheet("color: #0f172a; font-weight: 700; font-size: 13px;")
        tel_layout.addWidget(lbl_tel_header)

        self.lbl_profile_chart = QLabel()
        self.lbl_profile_chart.setMinimumHeight(120)
        self.lbl_profile_chart.setStyleSheet("background: #f8fafc; border: 1px dashed #cbd5e1; border-radius: 6px;")
        self.lbl_profile_chart.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_profile_chart.setText("Compute a 3D Route to view elevation cross-section")
        tel_layout.addWidget(self.lbl_profile_chart)

        # 3D Feature Badges Row
        badge_grid = QGridLayout()
        badge_grid.setSpacing(6)
        features = [
            ("⛰️ 3D Terrain Exaggeration", "#0284c7"),
            ("🔥 Thermal Heat Ribbon", "#e11d48"),
            ("🌙 24-hr Solar Cycle", "#d97706"),
            ("🏃 Kinematic Avatars", "#059669"),
            ("🔪 Sub-surface Slicer", "#7c3aed"),
            ("🔊 Voice Audio Cues", "#0d9488"),
        ]
        for idx, (feat_name, feat_color) in enumerate(features):
            lbl_f = QLabel(feat_name)
            lbl_f.setStyleSheet(f"""
                background: #f1f5f9;
                color: {feat_color};
                font-weight: 600;
                font-size: 11px;
                padding: 5px 8px;
                border-radius: 5px;
                border: 1px solid #e2e8f0;
            """)
            badge_grid.addWidget(lbl_f, idx // 2, idx % 2)

        tel_layout.addLayout(badge_grid)
        main_layout.addWidget(telemetry_card)
        main_layout.addStretch()

    def send_route(self, geojson_data: Dict[str, Any]) -> None:
        """Save computed route GeoJSON and optionally pop the 3D WebGL Studio."""
        self._current_geojson = geojson_data
        with contextlib.suppress(Exception):
            self.current_route_file.write_text(json.dumps(geojson_data, indent=2), encoding="utf-8")

        self._render_qt_elevation_profile(geojson_data)

        if self.chk_auto_open.isChecked():
            self.open_in_external_browser()

    def _render_qt_elevation_profile(self, geojson_data: Dict[str, Any]) -> None:
        coords = geojson_data.get("geometry", {}).get("coordinates", [])
        if len(coords) < 2:
            return

        elevations = [c[2] if len(c) > 2 else 0.0 for c in coords]
        min_e = min(elevations)
        max_e = max(elevations)
        gain = geojson_data.get("properties", {}).get("elevation_gain_m", 0.0)
        dist = geojson_data.get("properties", {}).get("distance_km", 0.0)
        dur = geojson_data.get("properties", {}).get("duration_min", 0.0)

        html_summary = f"""
        <div style='padding: 10px; font-family: sans-serif;'>
            <table width='100%' style='font-size: 12px; color: #334155;'>
                <tr>
                    <td><b>Distance:</b> {dist:.2f} km</td>
                    <td><b>Duration:</b> {dur:.1f} min</td>
                </tr>
                <tr>
                    <td><b>Elevation Gain:</b> +{gain:.1f} m</td>
                    <td><b>Min / Max Altitude:</b> {min_e:.0f} m / {max_e:.0f} m</td>
                </tr>
            </table>
            <p style='color: #0284c7; font-weight: 600; margin-top: 8px;'>
                ✅ 3D Route synchronized. Click the button above to explore in 3D WebGL.
            </p>
        </div>
        """
        self.lbl_profile_chart.setText(html_summary)

    def open_in_external_browser(self) -> None:
        """Start local HTTP server and launch the 3D WebGL studio in default browser."""
        server_url = self.server.start()
        QDesktopServices.openUrl(QUrl(server_url))

    def close(self) -> bool:
        self.server.stop()
        return super().close()
