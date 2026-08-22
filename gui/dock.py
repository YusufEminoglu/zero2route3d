"""Main Studio DockWidget for 02Route 3D."""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QIcon, QPalette
from qgis.PyQt.QtWidgets import (
    QAction,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from qgis.gui import QgsDoubleSpinBox, QgsMapCanvas, QgsMapLayerComboBox
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsMapLayerProxyModel,
    QgsPoint,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
)

from ..core.environmental_raster import EnvironmentalSurfaceSampler, MCDAWeights
from ..core.mobility_profiles import PROFILES, MobilityProfile, get_profile
from ..core.network_source import NetworkSourceManager
from ..core.profile_stats import densify_3d_linestring
from ..core.routing_engine import RouteResult3D, RoutingEngine3D, Waypoint
from .map_tools import RoutePointMapTool
from .theme import apply_adaptive_theme
from .webview import Studio3DWebViewport


class Route3DStudioDock(QDockWidget):
    """Unified 3D Mobility & Route Planning Studio DockWidget."""

    route_calculated = pyqtSignal(object)

    def __init__(self, iface: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__("02Route 3D Studio", parent)
        self.iface = iface
        self.canvas: Optional[QgsMapCanvas] = iface.mapCanvas() if iface else None
        self.setObjectName("Route3DStudioDock")

        self.network_manager = NetworkSourceManager()
        self.active_tool: Optional[RoutePointMapTool] = None
        self.current_route_result: Optional[RouteResult3D] = None

        # Root Splitter Widget
        self.root_widget = QWidget(self)
        self.root_widget.setObjectName("route3dRoot")
        self.setWidget(self.root_widget)

        self._build_ui()
        apply_adaptive_theme(self.root_widget)

    def _build_ui(self) -> None:
        main_layout = QHBoxLayout(self.root_widget)
        main_layout.setContentsMargins(4, 4, 4, 4)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self.root_widget)
        main_layout.addWidget(self.splitter)

        # -------------------------------------------------------------
        # Left Panel: Controls & Diagnostics (in ScrollArea)
        # -------------------------------------------------------------
        scroll = QScrollArea(self.splitter)
        scroll.setObjectName("route3dScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        controls_container = QWidget()
        self.ctrl_layout = QVBoxLayout(controls_container)
        self.ctrl_layout.setContentsMargins(8, 8, 8, 8)
        self.ctrl_layout.setSpacing(10)

        # Header
        self._build_header_card()

        # Card 1: Origin, Destination & Waypoints
        self._build_waypoints_card()

        # Card 2: Mobility Profiles
        self._build_profile_card()

        # Card 3: Multi-Criteria Weights (MCDA Sliders)
        self._build_mcda_card()

        # Card 4: Data Sources
        self._build_data_sources_card()

        # Card 5: Actions & Live KPI Summary
        self._build_actions_card()

        self.ctrl_layout.addStretch()
        scroll.setWidget(controls_container)
        self.splitter.addWidget(scroll)

        # -------------------------------------------------------------
        # Right Panel: Embedded 3D WebGL Viewport
        # -------------------------------------------------------------
        self.viewport3d = Studio3DWebViewport(self.splitter)
        self.splitter.addWidget(self.viewport3d)

        # Splitter proportion (40% controls, 60% 3D view)
        self.splitter.setSizes([380, 620])

    def _build_header_card(self) -> None:
        hdr = QFrame()
        hdr.setProperty("class", "route3dCard")
        hdr_layout = QHBoxLayout(hdr)
        hdr_layout.setContentsMargins(10, 8, 10, 8)

        v_box = QVBoxLayout()
        title = QLabel("02Route 3D Studio")
        title.setProperty("class", "route3dCardTitle")
        sub = QLabel("Multi-Criteria 3D Mobility & Kinematics")
        sub.setProperty("class", "route3dSubtle")
        v_box.addWidget(title)
        v_box.addWidget(sub)
        hdr_layout.addLayout(v_box)

        hdr_layout.addStretch()

        btn_popout = QPushButton("🌐 Pop-out 3D")
        btn_popout.setProperty("class", "route3dToolBtn")
        btn_popout.setToolTip("Open 3D Studio in external browser (Chrome/Edge)")
        btn_popout.clicked.connect(lambda: self.viewport3d.open_in_external_browser())
        hdr_layout.addWidget(btn_popout)

        self.ctrl_layout.addWidget(hdr)

    def _build_waypoints_card(self) -> None:
        card = QFrame()
        card.setProperty("class", "route3dCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(8)

        lbl = QLabel("1. Route Waypoints")
        lbl.setProperty("class", "route3dCardTitle")
        card_layout.addWidget(lbl)

        grid = QGridLayout()
        grid.setSpacing(6)

        # Point A (Start)
        grid.addWidget(QLabel("Origin A:"), 0, 0)
        self.txt_start = QLineEdit()
        self.txt_start.setPlaceholderText("Lon, Lat (or pick on map)")
        self.txt_start.setText("27.1428, 38.4237")  # Default Izmir center
        grid.addWidget(self.txt_start, 0, 1)

        self.btn_pick_start = QPushButton("📍 Pick A")
        self.btn_pick_start.setProperty("class", "route3dToolBtn")
        self.btn_pick_start.setCheckable(True)
        self.btn_pick_start.clicked.connect(lambda: self._toggle_canvas_picker("start"))
        grid.addWidget(self.btn_pick_start, 0, 2)

        # Point B (Destination)
        grid.addWidget(QLabel("Dest B:"), 1, 0)
        self.txt_end = QLineEdit()
        self.txt_end.setPlaceholderText("Lon, Lat (or pick on map)")
        self.txt_end.setText("27.1650, 38.4380")
        grid.addWidget(self.txt_end, 1, 1)

        self.btn_pick_end = QPushButton("🎯 Pick B")
        self.btn_pick_end.setProperty("class", "route3dToolBtn")
        self.btn_pick_end.setCheckable(True)
        self.btn_pick_end.clicked.connect(lambda: self._toggle_canvas_picker("end"))
        grid.addWidget(self.btn_pick_end, 1, 2)

        card_layout.addLayout(grid)

        # Row with Reverse and Quick Actions
        btn_row = QHBoxLayout()
        self.btn_reverse = QPushButton("⇄ Reverse Route")
        self.btn_reverse.setProperty("class", "route3dToolBtn")
        self.btn_reverse.clicked.connect(self._reverse_endpoints)
        btn_row.addWidget(self.btn_reverse)
        card_layout.addLayout(btn_row)

        self.ctrl_layout.addWidget(card)

    def _build_profile_card(self) -> None:
        card = QFrame()
        card.setProperty("class", "route3dCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(8)

        lbl = QLabel("2. Mobility Profile")
        lbl.setProperty("class", "route3dCardTitle")
        card_layout.addWidget(lbl)

        self.cmb_profile = QComboBox()
        for key, p in PROFILES.items():
            self.cmb_profile.addItem(f"{p.name} ({p.category.capitalize()})", key)
        self.cmb_profile.currentIndexChanged.connect(self._on_profile_changed)
        card_layout.addWidget(self.cmb_profile)

        # Profile Badge & Specs Readout
        self.lbl_profile_specs = QLabel()
        self.lbl_profile_specs.setStyleSheet(
            "background: rgba(56, 189, 248, 0.15); color: #38bdf8; padding: 6px 10px; border-radius: 6px; font-size: 11px; font-weight: 600;"
        )
        card_layout.addWidget(self.lbl_profile_specs)

        self.lbl_profile_desc = QLabel()
        self.lbl_profile_desc.setProperty("class", "route3dSubtle")
        self.lbl_profile_desc.setWordWrap(True)
        card_layout.addWidget(self.lbl_profile_desc)

        self._on_profile_changed(0)
        self.ctrl_layout.addWidget(card)

    def _build_mcda_card(self) -> None:
        group = QGroupBox("3. Multi-Criteria Weights (MCDA)")
        group.setCheckable(True)
        group.setChecked(False)
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(10, 12, 10, 8)
        group_layout.setSpacing(6)

        # Slope Resistance Slider
        group_layout.addWidget(QLabel("Slope Avoidance:"))
        self.slider_slope = QSlider(Qt.Orientation.Horizontal)
        self.slider_slope.setRange(0, 100)
        self.slider_slope.setValue(50)
        group_layout.addWidget(self.slider_slope)

        # Heat (LST) Avoidance Slider
        group_layout.addWidget(QLabel("Thermal / Heat Avoidance:"))
        self.slider_heat = QSlider(Qt.Orientation.Horizontal)
        self.slider_heat.setRange(0, 100)
        self.slider_heat.setValue(40)
        group_layout.addWidget(self.slider_heat)

        # Tree Canopy / Shade Preference
        group_layout.addWidget(QLabel("Tree Canopy & Shade Preference:"))
        self.slider_green = QSlider(Qt.Orientation.Horizontal)
        self.slider_green.setRange(0, 100)
        self.slider_green.setValue(50)
        group_layout.addWidget(self.slider_green)

        self.ctrl_layout.addWidget(group)

    def _build_data_sources_card(self) -> None:
        card = QFrame()
        card.setProperty("class", "route3dCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(8)

        lbl = QLabel("4. Data Sources")
        lbl.setProperty("class", "route3dCardTitle")
        card_layout.addWidget(lbl)

        # Network Source
        self.chk_osm_auto = QCheckBox("Auto-fetch OSM Road Network (Live Bounding Box)")
        self.chk_osm_auto.setChecked(True)
        card_layout.addWidget(self.chk_osm_auto)

        self.cmb_network_layer = QgsMapLayerComboBox()
        self.cmb_network_layer.setFilters(QgsMapLayerProxyModel.LineLayer)
        self.cmb_network_layer.setEnabled(False)
        self.chk_osm_auto.toggled.connect(lambda checked: self.cmb_network_layer.setEnabled(not checked))
        card_layout.addWidget(self.cmb_network_layer)

        # DEM / Terrain Source
        card_layout.addWidget(QLabel("Elevation (DEM) Layer (Optional):"))
        self.cmb_dem_layer = QgsMapLayerComboBox()
        self.cmb_dem_layer.setFilters(QgsMapLayerProxyModel.RasterLayer)
        card_layout.addWidget(self.cmb_dem_layer)

        self.ctrl_layout.addWidget(card)

    def _build_actions_card(self) -> None:
        card = QFrame()
        card.setProperty("class", "route3dCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(10)

        self.btn_compute = QPushButton("🚀 Compute 3D Route")
        self.btn_compute.setProperty("class", "route3dPrimaryBtn")
        self.btn_compute.clicked.connect(self.compute_route)
        card_layout.addWidget(self.btn_compute)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        card_layout.addWidget(self.progress_bar)

        # Diagnostics HUD Grid
        kpi_grid = QGridLayout()
        kpi_grid.setSpacing(6)

        self.kpi_dist = QLabel("—")
        self.kpi_time = QLabel("—")
        self.kpi_climb = QLabel("—")
        self.kpi_slope = QLabel("—")
        self.kpi_kcal = QLabel("—")

        for lbl_widget in [self.kpi_dist, self.kpi_time, self.kpi_climb, self.kpi_slope, self.kpi_kcal]:
            lbl_widget.setStyleSheet("font-weight: 700; color: #38bdf8; font-size: 13px;")

        kpi_grid.addWidget(QLabel("Distance:"), 0, 0)
        kpi_grid.addWidget(self.kpi_dist, 0, 1)

        kpi_grid.addWidget(QLabel("Duration:"), 0, 2)
        kpi_grid.addWidget(self.kpi_time, 0, 3)

        kpi_grid.addWidget(QLabel("Climb:"), 1, 0)
        kpi_grid.addWidget(self.kpi_climb, 1, 1)

        kpi_grid.addWidget(QLabel("Max Slope:"), 1, 2)
        kpi_grid.addWidget(self.kpi_slope, 1, 3)

        kpi_grid.addWidget(QLabel("Calories:"), 2, 0)
        kpi_grid.addWidget(self.kpi_kcal, 2, 1)

        card_layout.addLayout(kpi_grid)

        # Export Buttons Row
        exp_row = QHBoxLayout()
        self.btn_add_layer = QPushButton("➕ 3D Layer")
        self.btn_add_layer.setProperty("class", "route3dToolBtn")
        self.btn_add_layer.clicked.connect(self.add_route_layer_to_qgis)
        self.btn_add_layer.setEnabled(False)
        exp_row.addWidget(self.btn_add_layer)

        self.btn_export_gpx = QPushButton("💾 GPX")
        self.btn_export_gpx.setProperty("class", "route3dToolBtn")
        self.btn_export_gpx.clicked.connect(self.export_gpx)
        self.btn_export_gpx.setEnabled(False)
        exp_row.addWidget(self.btn_export_gpx)

        self.btn_export_geojson = QPushButton("💾 GeoJSON")
        self.btn_export_geojson.setProperty("class", "route3dToolBtn")
        self.btn_export_geojson.clicked.connect(self.export_geojson)
        self.btn_export_geojson.setEnabled(False)
        exp_row.addWidget(self.btn_export_geojson)

        card_layout.addLayout(exp_row)
        self.ctrl_layout.addWidget(card)

    def _on_profile_changed(self, index: int) -> None:
        key = self.cmb_profile.itemData(index) or "adult"
        p = get_profile(key)
        stairs_txt = "Allowed" if p.stair_allowed else "Prohibited"
        self.lbl_profile_specs.setText(
            f"Speed: {p.base_speed_kmh} km/h  |  Max Slope: {p.max_slope_pct}%  |  Stairs: {stairs_txt}"
        )
        self.lbl_profile_desc.setText(p.description)

    def _toggle_canvas_picker(self, point_type: str) -> None:
        if not self.canvas:
            return

        if self.active_tool:
            self.canvas.unsetMapTool(self.active_tool)
            self.active_tool = None
            self.btn_pick_start.setChecked(False)
            self.btn_pick_end.setChecked(False)

        tool = RoutePointMapTool(self.canvas, point_type=point_type, on_picked=self._on_point_picked)
        self.active_tool = tool
        self.canvas.setMapTool(tool)

        if point_type == "start":
            self.btn_pick_start.setChecked(True)
        else:
            self.btn_pick_end.setChecked(True)

    def _on_point_picked(self, lon: float, lat: float, point_type: str) -> None:
        coord_txt = f"{lon:.5f}, {lat:.5f}"
        if point_type == "start":
            self.txt_start.setText(coord_txt)
            self.btn_pick_start.setChecked(False)
        else:
            self.txt_end.setText(coord_txt)
            self.btn_pick_end.setChecked(False)

        if self.canvas and self.active_tool:
            self.canvas.unsetMapTool(self.active_tool)
            self.active_tool = None

    def _reverse_endpoints(self) -> None:
        s = self.txt_start.text()
        self.txt_start.setText(self.txt_end.text())
        self.txt_end.setText(s)

    def _parse_coord(self, txt: str) -> Optional[Waypoint]:
        parts = txt.replace(";", ",").split(",")
        if len(parts) >= 2:
            with contextlib.suppress(ValueError):
                lon = float(parts[0].strip())
                lat = float(parts[1].strip())
                return Waypoint(lon=lon, lat=lat)
        return None

    def compute_route(self) -> None:
        """Compute the 3D route and display in 3D viewport and KPI panel."""
        w1 = self._parse_coord(self.txt_start.text())
        w2 = self._parse_coord(self.txt_end.text())

        if not w1 or not w2:
            QMessageBox.warning(self, "Invalid Coordinates", "Please enter valid Lon, Lat coordinates for Start and Destination.")
            return

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(20)

        # Build Sampler & Weights
        dem_layer = self.cmb_dem_layer.currentLayer()
        sampler = EnvironmentalSurfaceSampler(dem_layer=dem_layer)
        weights = MCDAWeights(
            weight_slope=self.slider_slope.value() / 50.0,
            weight_heat=self.slider_heat.value() / 50.0,
            weight_green=self.slider_green.value() / 50.0,
        )

        engine = RoutingEngine3D(sampler=sampler, weights=weights)

        # Ingest Network
        bbox = (min(w1.lon, w2.lon), min(w1.lat, w2.lat), max(w1.lon, w2.lon), max(w1.lat, w2.lat))
        if self.chk_osm_auto.isChecked():
            segments = self.network_manager.fetch_osm_network_bbox(bbox)
        else:
            net_layer = self.cmb_network_layer.currentLayer()
            if net_layer:
                segments = self.network_manager.extract_from_qgis_layer(net_layer)
            else:
                segments = self.network_manager.generate_synthetic_grid(bbox)

        self.progress_bar.setValue(60)
        engine.build_graph(segments)

        # Compute Route
        profile_key = self.cmb_profile.currentData() or "adult"
        result = engine.calculate_route([w1, w2], profile_key=profile_key)
        self.current_route_result = result

        # Update KPIs
        self.kpi_dist.setText(f"{result.statistics.total_distance_km} km")
        self.kpi_time.setText(f"{result.statistics.total_duration_min} min")
        self.kpi_climb.setText(f"+{result.statistics.elevation_gain_m:.1f} m")
        self.kpi_slope.setText(f"{result.statistics.max_slope_pct:.1f}%")
        self.kpi_kcal.setText(f"{result.statistics.total_calories_kcal:.0f} kcal")

        # Enable Export Buttons
        self.btn_add_layer.setEnabled(True)
        self.btn_export_gpx.setEnabled(True)
        self.btn_export_geojson.setEnabled(True)

        # Transmit to 3D WebEngine
        geojson_feature = result.to_geojson_feature()
        self.viewport3d.send_route(geojson_feature)

        self.progress_bar.setValue(100)
        self.progress_bar.setVisible(False)
        self.route_calculated.emit(result)

    def add_route_layer_to_qgis(self) -> None:
        """Create and add a native 3D LineStringZ vector layer to the current QGIS project."""
        if not self.current_route_result or not self.current_route_result.coordinates_3d:
            return

        res = self.current_route_result
        layer = QgsVectorLayer("LineStringZ?crs=EPSG:4326", f"3D Route - {res.profile.name}", "memory")
        pr = layer.dataProvider()

        pr.addAttributes([
            QgsField("profile", 10),
            QgsField("dist_km", 6),
            QgsField("time_min", 6),
            QgsField("climb_m", 6),
            QgsField("max_slope", 6),
        ])
        layer.updateFields()

        # Build 3D LineString Geometry
        pts = [QgsPoint(c[0], c[1], c[2]) for c in res.coordinates_3d]
        geom = QgsGeometry.fromPolyline(pts)

        feat = QgsFeature()
        feat.setGeometry(geom)
        feat.setAttributes([
            res.profile.name,
            res.statistics.total_distance_km,
            res.statistics.total_duration_min,
            res.statistics.elevation_gain_m,
            res.statistics.max_slope_pct,
        ])
        pr.addFeature(feat)
        layer.updateExtents()

        QgsProject.instance().addMapLayer(layer)
        if self.iface:
            self.iface.messageBar().pushSuccess("02Route 3D", f"Added '{layer.name()}' to project.")

    def export_gpx(self) -> None:
        """Export route to GPX file."""
        if not self.current_route_result:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export GPX 3D Route", "", "GPX Files (*.gpx)")
        if path:
            Path(path).write_text(self.current_route_result.to_gpx(), encoding="utf-8")
            if self.iface:
                self.iface.messageBar().pushSuccess("02Route 3D", f"Exported GPX to {path}")

    def export_geojson(self) -> None:
        """Export route to GeoJSON file."""
        if not self.current_route_result:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export GeoJSON 3D Route", "", "GeoJSON Files (*.geojson)")
        if path:
            geojson_data = {
                "type": "FeatureCollection",
                "features": [self.current_route_result.to_geojson_feature()],
            }
            Path(path).write_text(json.dumps(geojson_data, indent=2), encoding="utf-8")
            if self.iface:
                self.iface.messageBar().pushSuccess("02Route 3D", f"Exported GeoJSON to {path}")
