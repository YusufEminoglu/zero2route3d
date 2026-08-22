"""Unified 6-Tab Studio DockWidget for 02Route 3D."""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QCursor
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qgis.gui import QgsMapCanvas, QgsMapLayerComboBox
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsMapLayerProxyModel,
    QgsPoint,
    QgsProject,
    QgsVectorLayer,
)

from ..core.ahp_engine import AHPEngine
from ..core.environmental_raster import EnvironmentalSurfaceSampler, MCDAWeights
from ..core.mobility_profiles import PROFILES, MobilityProfile, get_profile, list_profile_keys
from ..core.network_source import NetworkSourceManager
from ..core.routing_engine import RouteResult3D, RoutingEngine3D, Waypoint
from .cue_sheet_widget import CueSheetWidget
from .map_tools import RoutePointMapTool
from .profile_editor import ProfileEditorDialog
from .theme import apply_adaptive_theme
from .webview import Studio3DWebViewport


class Route3DStudioDock(QDockWidget):
    """Next-generation 6-Tab 3D Mobility & Route Planning Studio Dock."""

    route_calculated = pyqtSignal(object)

    def __init__(self, iface: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__("02Route 3D Studio", parent)
        self.iface = iface
        self.canvas: Optional[QgsMapCanvas] = iface.mapCanvas() if iface else None
        self.setObjectName("Route3DStudioDock")

        self.network_manager = NetworkSourceManager()
        self.active_tool: Optional[RoutePointMapTool] = None
        self.current_route_result: Optional[RouteResult3D] = None
        self.waypoints: List[Waypoint] = [
            Waypoint(lon=27.1428, lat=38.4237, name="Izmir Center (Origin A)"),
            Waypoint(lon=27.1650, lat=38.4380, name="Alsancak (Dest B)"),
        ]

        # Root Widget & Layout
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
        # Left Panel: 6-Tab Multi-Studio
        # -------------------------------------------------------------
        self.tab_widget = QTabWidget(self.splitter)
        self.tab_widget.setObjectName("route3dTabs")

        # Tab 1: Route Planner
        self.tab_planner = QWidget()
        self._build_tab_planner(self.tab_planner)
        self.tab_widget.addTab(self.tab_planner, "🗺️ Planner")

        # Tab 2: AHP & MCDA Matrix
        self.tab_mcda = QWidget()
        self._build_tab_mcda(self.tab_mcda)
        self.tab_widget.addTab(self.tab_mcda, "⚖️ MCDA Lab")

        # Tab 3: Turn-by-Turn Cue Sheet
        self.cue_widget = CueSheetWidget()
        self.tab_widget.addTab(self.cue_widget, "📋 Cue Sheet")

        # Tab 4: OD Matrix Studio
        self.tab_od = QWidget()
        self._build_tab_od(self.tab_od)
        self.tab_widget.addTab(self.tab_od, "📊 OD Matrix")

        # Tab 5: Settings & Profiles
        self.tab_settings = QWidget()
        self._build_tab_settings(self.tab_settings)
        self.tab_widget.addTab(self.tab_settings, "⚙️ Profiles")

        self.splitter.addWidget(self.tab_widget)

        # -------------------------------------------------------------
        # Right Panel: Embedded 3D WebGL Viewport
        # -------------------------------------------------------------
        self.viewport3d = Studio3DWebViewport(self.splitter)
        self.splitter.addWidget(self.viewport3d)

        # Proportions: 45% left tools, 55% 3D studio
        self.splitter.setSizes([460, 560])

    def _build_tab_planner(self, parent: QWidget) -> None:
        scroll = QScrollArea(parent)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        # Search Bar
        search_row = QHBoxLayout()
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("🔍 Search address or landmark (Photon)...")
        self.txt_search.returnPressed.connect(self._on_search_location)
        search_row.addWidget(self.txt_search)

        btn_search = QPushButton("Find")
        btn_search.clicked.connect(self._on_search_location)
        search_row.addWidget(btn_search)
        layout.addLayout(search_row)

        # Waypoints List Card
        card_wp = QFrame()
        card_wp.setProperty("class", "route3dCard")
        wp_layout = QVBoxLayout(card_wp)
        wp_layout.setContentsMargins(10, 10, 10, 10)
        wp_layout.setSpacing(6)

        wp_hdr = QHBoxLayout()
        lbl_wp = QLabel("Waypoints & Stops")
        lbl_wp.setProperty("class", "route3dCardTitle")
        wp_hdr.addWidget(lbl_wp)
        wp_hdr.addStretch()

        self.btn_pick_canvas = QPushButton("📍 Pick Map")
        self.btn_pick_canvas.setCheckable(True)
        self.btn_pick_canvas.clicked.connect(self._toggle_canvas_picker)
        wp_hdr.addWidget(self.btn_pick_canvas)

        self.btn_tsp = QPushButton("✨ TSP Tour")
        self.btn_tsp.setToolTip("Optimize waypoint order for shortest 3D distance (2-Opt TSP)")
        self.btn_tsp.clicked.connect(self._optimize_tsp_order)
        wp_hdr.addWidget(self.btn_tsp)

        wp_layout.addLayout(wp_hdr)

        self.list_waypoints = QListWidget()
        self.list_waypoints.setFixedHeight(110)
        self._refresh_waypoints_list()
        wp_layout.addWidget(self.list_waypoints)

        wp_btn_row = QHBoxLayout()
        btn_add_wp = QPushButton("➕ Add Stop")
        btn_add_wp.clicked.connect(self._add_waypoint_prompt)
        wp_btn_row.addWidget(btn_add_wp)

        btn_del_wp = QPushButton("➖ Remove")
        btn_del_wp.clicked.connect(self._remove_selected_waypoint)
        wp_btn_row.addWidget(btn_del_wp)

        btn_rev_wp = QPushButton("⇄ Reverse")
        btn_rev_wp.clicked.connect(self._reverse_waypoints)
        wp_btn_row.addWidget(btn_rev_wp)

        wp_layout.addLayout(wp_btn_row)
        layout.addWidget(card_wp)

        # Profile Selector Card
        card_prof = QFrame()
        card_prof.setProperty("class", "route3dCard")
        prof_layout = QVBoxLayout(card_prof)
        prof_layout.setContentsMargins(10, 10, 10, 10)
        prof_layout.setSpacing(6)

        lbl_prof = QLabel("Mobility Profile")
        lbl_prof.setProperty("class", "route3dCardTitle")
        prof_layout.addWidget(lbl_prof)

        self.cmb_profile = QComboBox()
        for key in list_profile_keys():
            p = get_profile(key)
            self.cmb_profile.addItem(f"{p.name} ({p.category.capitalize()})", key)
        self.cmb_profile.currentIndexChanged.connect(self._on_profile_changed)
        prof_layout.addWidget(self.cmb_profile)

        self.lbl_profile_specs = QLabel()
        self.lbl_profile_specs.setStyleSheet(
            "background: rgba(56, 189, 248, 0.15); color: #38bdf8; padding: 5px 8px; border-radius: 6px; font-size: 11px; font-weight: 600;"
        )
        prof_layout.addWidget(self.lbl_profile_specs)
        self._on_profile_changed(0)
        layout.addWidget(card_prof)

        # Action & KPI Card
        card_act = QFrame()
        card_act.setProperty("class", "route3dCard")
        act_layout = QVBoxLayout(card_act)
        act_layout.setContentsMargins(10, 10, 10, 10)
        act_layout.setSpacing(8)

        self.btn_compute = QPushButton("🚀 Compute 3D Route")
        self.btn_compute.setProperty("class", "route3dPrimaryBtn")
        self.btn_compute.clicked.connect(self.compute_route)
        act_layout.addWidget(self.btn_compute)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        act_layout.addWidget(self.progress_bar)

        # KPI HUD Grid
        kpi_grid = QGridLayout()
        kpi_grid.setSpacing(6)
        self.kpi_dist = QLabel("—")
        self.kpi_time = QLabel("—")
        self.kpi_climb = QLabel("—")
        self.kpi_slope = QLabel("—")
        self.kpi_kcal = QLabel("—")
        for lbl in [self.kpi_dist, self.kpi_time, self.kpi_climb, self.kpi_slope, self.kpi_kcal]:
            lbl.setStyleSheet("font-weight: 700; color: #38bdf8; font-size: 13px;")

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
        act_layout.addLayout(kpi_grid)

        # Export Actions
        exp_row = QHBoxLayout()
        self.btn_add_layer = QPushButton("➕ 3D Layer")
        self.btn_add_layer.clicked.connect(self.add_route_layer_to_qgis)
        self.btn_add_layer.setEnabled(False)
        exp_row.addWidget(self.btn_add_layer)

        self.btn_export_gpx = QPushButton("💾 GPX")
        self.btn_export_gpx.setToolTip("Export 3D Route to GPS Exchange Format (.gpx)")
        self.btn_export_gpx.clicked.connect(self.export_gpx)
        self.btn_export_gpx.setEnabled(False)
        exp_row.addWidget(self.btn_export_gpx)

        self.btn_export_geojson = QPushButton("💾 GeoJSON")
        self.btn_export_geojson.setToolTip("Export 3D Route to GeoJSON (.geojson)")
        self.btn_export_geojson.clicked.connect(self.export_geojson)
        self.btn_export_geojson.setEnabled(False)
        exp_row.addWidget(self.btn_export_geojson)

        self.btn_export_html = QPushButton("🌐 3D HTML")
        self.btn_export_html.setToolTip("Export self-contained standalone 3D HTML report (opens anywhere offline)")
        self.btn_export_html.clicked.connect(self.export_standalone_html)
        self.btn_export_html.setEnabled(False)
        exp_row.addWidget(self.btn_export_html)

        self.btn_export_dxf = QPushButton("📐 3D DXF")
        self.btn_export_dxf.setToolTip("Export AutoCAD 3D Polyline and Longitudinal Profile (.dxf)")
        self.btn_export_dxf.clicked.connect(self.export_dxf)
        self.btn_export_dxf.setEnabled(False)
        exp_row.addWidget(self.btn_export_dxf)
        act_layout.addLayout(exp_row)

        layout.addWidget(card_act)
        layout.addStretch()

        scroll.setWidget(container)
        parent_layout = QVBoxLayout(parent)
        parent_layout.setContentsMargins(0, 0, 0, 0)
        parent_layout.addWidget(scroll)

    def _build_tab_mcda(self, parent: QWidget) -> None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        lbl = QLabel("Analytic Hierarchy Process (AHP) & Multi-Criteria Weights")
        lbl.setStyleSheet("font-weight: 700; color: #38bdf8; font-size: 13px;")
        layout.addWidget(lbl)

        # Sliders
        form = QVBoxLayout()
        form.addWidget(QLabel("1. Slope & Grade Avoidance:"))
        self.slider_slope = QSlider(Qt.Orientation.Horizontal)
        self.slider_slope.setRange(0, 100)
        self.slider_slope.setValue(60)
        form.addWidget(self.slider_slope)

        form.addWidget(QLabel("2. Thermal / Heat Exposure Avoidance:"))
        self.slider_heat = QSlider(Qt.Orientation.Horizontal)
        self.slider_heat.setRange(0, 100)
        self.slider_heat.setValue(40)
        form.addWidget(self.slider_heat)

        form.addWidget(QLabel("3. Tree Canopy & Greenery Preference:"))
        self.slider_green = QSlider(Qt.Orientation.Horizontal)
        self.slider_green.setRange(0, 100)
        self.slider_green.setValue(50)
        form.addWidget(self.slider_green)

        form.addWidget(QLabel("4. Solar Shade & Microclimate Preference:"))
        self.slider_solar = QSlider(Qt.Orientation.Horizontal)
        self.slider_solar.setRange(0, 100)
        self.slider_solar.setValue(30)
        form.addWidget(self.slider_solar)

        layout.addLayout(form)

        # AHP Consistency Check Button
        self.lbl_ahp_status = QLabel("AHP Status: Ready (Pairwise consistent)")
        self.lbl_ahp_status.setStyleSheet("color: #10b981; font-weight: 600; font-size: 11px;")
        layout.addWidget(self.lbl_ahp_status)

        layout.addStretch()

    def _build_tab_od(self, parent: QWidget) -> None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        lbl = QLabel("Origin-Destination (OD) 3D Cost Matrix Studio")
        lbl.setStyleSheet("font-weight: 700; color: #38bdf8; font-size: 13px;")
        layout.addWidget(lbl)

        self.btn_run_od = QPushButton("⚡ Compute N x M OD Matrix")
        self.btn_run_od.clicked.connect(self._compute_od_matrix)
        layout.addWidget(self.btn_run_od)

        self.table_od = QTableWidget()
        self.table_od.setColumnCount(5)
        self.table_od.setHorizontalHeaderLabels(["Origin", "Dest", "Dist (km)", "Time (min)", "Climb (m)"])
        self.table_od.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table_od)

    def _build_tab_settings(self, parent: QWidget) -> None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        lbl = QLabel("Data Sources & Custom Mobility Profiles")
        lbl.setStyleSheet("font-weight: 700; color: #38bdf8; font-size: 13px;")
        layout.addWidget(lbl)

        # Custom Profile Editor Launcher
        btn_open_editor = QPushButton("🛠️ Open Profile Builder & Preset Editor")
        btn_open_editor.clicked.connect(self._open_profile_editor)
        layout.addWidget(btn_open_editor)

        # Network Source
        self.chk_osm_auto = QCheckBox("Auto-fetch OpenStreetMap Road Network (Live BBOX)")
        self.chk_osm_auto.setChecked(True)
        layout.addWidget(self.chk_osm_auto)

        self.cmb_network_layer = QgsMapLayerComboBox()
        self.cmb_network_layer.setFilters(QgsMapLayerProxyModel.LineLayer)
        self.cmb_network_layer.setEnabled(False)
        self.chk_osm_auto.toggled.connect(lambda checked: self.cmb_network_layer.setEnabled(not checked))
        layout.addWidget(self.cmb_network_layer)

        # DEM Layer
        layout.addWidget(QLabel("Elevation (DEM) Raster Layer:"))
        self.cmb_dem_layer = QgsMapLayerComboBox()
        self.cmb_dem_layer.setFilters(QgsMapLayerProxyModel.RasterLayer)
        layout.addWidget(self.cmb_dem_layer)

        layout.addStretch()

    def _refresh_waypoints_list(self) -> None:
        self.list_waypoints.clear()
        for idx, wp in enumerate(self.waypoints):
            name_txt = wp.name or f"Point {chr(65 + idx)}"
            self.list_waypoints.addItem(f"{idx+1}. {name_txt} ({wp.lon:.4f}, {wp.lat:.4f})")

    def _add_waypoint_prompt(self) -> None:
        lon, lat = 27.1500, 38.4300
        self.waypoints.append(Waypoint(lon=lon, lat=lat, name=f"Stop {len(self.waypoints)+1}"))
        self._refresh_waypoints_list()

    def _remove_selected_waypoint(self) -> None:
        row = self.list_waypoints.currentRow()
        if 0 <= row < len(self.waypoints) and len(self.waypoints) > 2:
            self.waypoints.pop(row)
            self._refresh_waypoints_list()

    def _reverse_waypoints(self) -> None:
        self.waypoints.reverse()
        self._refresh_waypoints_list()

    def _optimize_tsp_order(self) -> None:
        if len(self.waypoints) <= 2:
            return
        sampler = EnvironmentalSurfaceSampler(dem_layer=self.cmb_dem_layer.currentLayer())
        pts = [(w.lon, w.lat, sampler.sample_elevation(w.lon, w.lat)) for w in self.waypoints]
        from ..core.tsp_solver import solve_tsp_order

        new_order = solve_tsp_order(pts, fix_start=True, fix_end=True)
        self.waypoints = [self.waypoints[idx] for idx in new_order]
        self._refresh_waypoints_list()

    def _on_profile_changed(self, index: int) -> None:
        key = self.cmb_profile.itemData(index) or "adult"
        p = get_profile(key)
        stairs_txt = "Allowed" if p.stair_allowed else "Prohibited"
        self.lbl_profile_specs.setText(
            f"Speed: {p.base_speed_kmh} km/h | Max Slope: {p.max_slope_pct}% | Stairs: {stairs_txt}"
        )

    def _toggle_canvas_picker(self) -> None:
        if not self.canvas:
            return
        if self.active_tool:
            self.canvas.unsetMapTool(self.active_tool)
            self.active_tool = None
            self.btn_pick_canvas.setChecked(False)
            return

        tool = RoutePointMapTool(self.canvas, point_type="waypoint", on_picked=self._on_canvas_point_picked)
        self.active_tool = tool
        self.canvas.setMapTool(tool)
        self.btn_pick_canvas.setChecked(True)

    def _on_canvas_point_picked(self, lon: float, lat: float, _point_type: str) -> None:
        if len(self.waypoints) < 2:
            self.waypoints.append(Waypoint(lon=lon, lat=lat, name=f"Stop {len(self.waypoints)+1}"))
        else:
            self.waypoints[-1] = Waypoint(lon=lon, lat=lat, name=f"Destination ({lon:.4f}, {lat:.4f})")
        self._refresh_waypoints_list()
        if self.canvas and self.active_tool:
            self.canvas.unsetMapTool(self.active_tool)
            self.active_tool = None
            self.btn_pick_canvas.setChecked(False)

    def _on_search_location(self) -> None:
        q = self.txt_search.text().strip()
        if not q:
            return
        res = self.network_manager.search_place_photon(q, limit=1)
        if res:
            first = res[0]
            if len(self.waypoints) >= 2:
                self.waypoints[-1] = Waypoint(lon=first["lon"], lat=first["lat"], name=first["label"])
            self._refresh_waypoints_list()
            QMessageBox.information(self, "Location Found", f"Matched: {first['label']}")

    def _open_profile_editor(self) -> None:
        curr_key = self.cmb_profile.currentData() or "adult"
        dlg = ProfileEditorDialog(self, base_profile_key=curr_key)
        if dlg.exec():
            built_p = dlg.get_built_profile()
            PROFILES[built_p.key] = built_p
            self.cmb_profile.addItem(f"{built_p.name} (Custom)", built_p.key)
            self.cmb_profile.setCurrentIndex(self.cmb_profile.count() - 1)

    def compute_route(self) -> None:
        if len(self.waypoints) < 2:
            QMessageBox.warning(self, "Waypoints Required", "At least 2 stops are required to compute a route.")
            return

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(20)

        # Sampler & Weights
        dem_layer = self.cmb_dem_layer.currentLayer()
        sampler = EnvironmentalSurfaceSampler(dem_layer=dem_layer)
        weights = MCDAWeights(
            weight_slope=self.slider_slope.value() / 50.0,
            weight_heat=self.slider_heat.value() / 50.0,
            weight_green=self.slider_green.value() / 50.0,
            weight_solar=self.slider_solar.value() / 50.0,
        )

        engine = RoutingEngine3D(sampler=sampler, weights=weights)

        # BBOX
        lons = [w.lon for w in self.waypoints]
        lats = [w.lat for w in self.waypoints]
        bbox = (min(lons), min(lats), max(lons), max(lats))

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

        profile_key = self.cmb_profile.currentData() or "adult"
        result = engine.calculate_route(self.waypoints, profile_key=profile_key, optimize_tsp=False)
        self.current_route_result = result

        # Update KPIs
        self.kpi_dist.setText(f"{result.statistics.total_distance_km} km")
        self.kpi_time.setText(f"{result.statistics.total_duration_min} min")
        self.kpi_climb.setText(f"+{result.statistics.elevation_gain_m:.1f} m")
        self.kpi_slope.setText(f"{result.statistics.max_slope_pct:.1f}%")
        self.kpi_kcal.setText(f"{result.statistics.total_calories_kcal:.0f} kcal")

        # Cue Sheet
        self.cue_widget.load_cues(result.statistics.cue_sheet)

        # Enable Buttons
        self.btn_add_layer.setEnabled(True)
        self.btn_export_gpx.setEnabled(True)
        self.btn_export_geojson.setEnabled(True)
        self.btn_export_html.setEnabled(True)
        self.btn_export_dxf.setEnabled(True)

        # 3D Viewport Transmit
        self.viewport3d.send_route(result.to_geojson_feature())

        self.progress_bar.setValue(100)
        self.progress_bar.setVisible(False)
        self.route_calculated.emit(result)

    def _compute_od_matrix(self) -> None:
        if len(self.waypoints) < 2:
            return
        sampler = EnvironmentalSurfaceSampler(dem_layer=self.cmb_dem_layer.currentLayer())
        engine = RoutingEngine3D(sampler=sampler)
        lons = [w.lon for w in self.waypoints]
        lats = [w.lat for w in self.waypoints]
        bbox = (min(lons), min(lats), max(lons), max(lats))
        segments = self.network_manager.generate_synthetic_grid(bbox)
        engine.build_graph(segments)

        rows = engine.calculate_od_matrix(self.waypoints, self.waypoints, profile_key=self.cmb_profile.currentData() or "adult")
        self.table_od.setRowCount(len(rows))
        for idx, r in enumerate(rows):
            self.table_od.setItem(idx, 0, QTableWidgetItem(r["origin_name"]))
            self.table_od.setItem(idx, 1, QTableWidgetItem(r["dest_name"]))
            self.table_od.setItem(idx, 2, QTableWidgetItem(f"{r['distance_km']:.2f}"))
            self.table_od.setItem(idx, 3, QTableWidgetItem(f"{r['duration_min']:.1f}"))
            self.table_od.setItem(idx, 4, QTableWidgetItem(f"{r['climb_m']:.1f}"))

    def add_route_layer_to_qgis(self) -> None:
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
        if not self.current_route_result:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export GPX 3D Route", "", "GPX Files (*.gpx)")
        if path:
            Path(path).write_text(self.current_route_result.to_gpx(), encoding="utf-8")
            if self.iface:
                self.iface.messageBar().pushSuccess("02Route 3D", f"Exported GPX to {path}")

    def export_geojson(self) -> None:
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

    def export_standalone_html(self) -> None:
        if not self.current_route_result:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Standalone 3D HTML Report", "", "HTML Files (*.html)")
        if path:
            from ..core.html_bundler import StandaloneHtmlBundler
            web_dir = Path(__file__).resolve().parent.parent / "web"
            bundler = StandaloneHtmlBundler(web_dir)
            bundler.export_standalone_html(self.current_route_result, Path(path))
            if self.iface:
                self.iface.messageBar().pushSuccess("02Route 3D", f"Exported Standalone 3D HTML to {path}")

    def export_dxf(self) -> None:
        if not self.current_route_result or not self.current_route_result.coordinates_3d:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export AutoCAD 3D Route (DXF)", "", "AutoCAD DXF Files (*.dxf)")
        if path:
            from ..core.profile_dxf import export_route_to_dxf_3d
            export_route_to_dxf_3d(self.current_route_result.coordinates_3d, Path(path))
            if self.iface:
                self.iface.messageBar().pushSuccess("02Route 3D", f"Exported 3D DXF to {path}")

    def closeEvent(self, event: Any) -> None:
        if self.viewport3d:
            self.viewport3d.close()
        super().closeEvent(event)


