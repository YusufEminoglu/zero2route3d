"""Unified 5-Tab Studio DockWidget for 02Route 3D."""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from qgis.PyQt.QtCore import Qt, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QCursor, QDesktopServices
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
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
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
from qgis.gui import QgsMapCanvas, QgsMapLayerComboBox

from ..core.ahp_engine import AHPEngine
from ..core.environmental_raster import EnvironmentalSurfaceSampler, MCDAWeights
from ..core.mobility_profiles import PROFILES, MobilityProfile, get_profile, list_profile_keys
from ..core.network_source import NetworkSourceManager
from ..core.routing_engine import RouteResult3D, RoutingEngine3D, Waypoint
from .cue_sheet_widget import CueSheetWidget
from .map_tools import RoutePointMapTool
from .profile_editor import ProfileEditorDialog
from .server import Route3DLocalServer
from .theme import apply_adaptive_theme


class Route3DStudioDock(QDockWidget):
    """Next-generation 3D Mobility & Route Planning Studio Dock."""

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

        self.web_root = Path(__file__).resolve().parent.parent / "web"
        self.data_dir = self.web_root / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.current_route_file = self.data_dir / "current_route.json"
        self.local_server = Route3DLocalServer(web_root=self.web_root)

        # Root Widget & Layout
        self.root_widget = QWidget(self)
        self.root_widget.setObjectName("route3dRoot")
        self.setWidget(self.root_widget)

        self._build_ui()
        apply_adaptive_theme(self.root_widget)

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self.root_widget)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # -------------------------------------------------------------
        # 5-Tab Multi-Studio Panel
        # -------------------------------------------------------------
        self.tab_widget = QTabWidget(self.root_widget)
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

        main_layout.addWidget(self.tab_widget)

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

        wp_header = QHBoxLayout()
        lbl_wp_title = QLabel("<b>Waypoints & Stops</b>")
        wp_header.addWidget(lbl_wp_title)
        wp_header.addStretch()

        self.btn_pick_map = QPushButton("📍 Pick Map")
        self.btn_pick_map.setCheckable(True)
        self.btn_pick_map.clicked.connect(self._toggle_map_picker)
        wp_header.addWidget(self.btn_pick_map)

        self.btn_tsp = QPushButton("✨ TSP Tour")
        self.btn_tsp.setToolTip("Optimize stop order via Traveling Salesperson algorithm")
        self.btn_tsp.clicked.connect(self._optimize_stops_tsp)
        wp_header.addWidget(self.btn_tsp)
        wp_layout.addLayout(wp_header)

        self.lst_waypoints = QListWidget()
        self.lst_waypoints.setFixedHeight(95)
        self._refresh_waypoint_list()
        wp_layout.addWidget(self.lst_waypoints)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("➕ Add Stop")
        btn_add.clicked.connect(self._add_waypoint_dialog)
        btn_row.addWidget(btn_add)

        btn_remove = QPushButton("➖ Remove")
        btn_remove.clicked.connect(self._remove_selected_waypoint)
        btn_row.addWidget(btn_remove)

        btn_reverse = QPushButton("⇄ Reverse")
        btn_reverse.clicked.connect(self._reverse_waypoints)
        btn_row.addWidget(btn_reverse)
        wp_layout.addLayout(btn_row)
        layout.addWidget(card_wp)

        # Mobility Profile Selector Card
        card_prof = QFrame()
        card_prof.setProperty("class", "route3dCard")
        prof_layout = QVBoxLayout(card_prof)
        prof_layout.addWidget(QLabel("<b>Mobility Profile</b>"))

        self.cmb_profile = QComboBox()
        for key in list_profile_keys():
            p = get_profile(key)
            self.cmb_profile.addItem(p.name, key)
        self.cmb_profile.currentIndexChanged.connect(self._on_profile_changed)
        prof_layout.addWidget(self.cmb_profile)

        self.lbl_profile_info = QLabel("Speed: 5.0 km/h | Max Slope: 25.0% | Stairs: Allowed")
        self.lbl_profile_info.setProperty("class", "route3dBadgeInfo")
        prof_layout.addWidget(self.lbl_profile_info)
        layout.addWidget(card_prof)

        # Execution & Action Card
        card_act = QFrame()
        card_act.setProperty("class", "route3dCard")
        act_layout = QVBoxLayout(card_act)

        # Primary Compute 3D Route
        self.btn_compute = QPushButton("🚀 Compute 3D Route")
        self.btn_compute.setObjectName("primaryButton")
        self.btn_compute.setStyleSheet("""
            QPushButton#primaryButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284c7, stop:1 #2563eb);
                color: #ffffff;
                font-weight: 800;
                font-size: 13px;
                padding: 11px;
                border-radius: 6px;
            }
            QPushButton#primaryButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0369a1, stop:1 #1d4ed8);
            }
        """)
        self.btn_compute.clicked.connect(self.compute_route)
        act_layout.addWidget(self.btn_compute)

        # Auto-open 3D WebGL Studio in Browser checkbox
        self.chk_auto_open = QCheckBox("🌐 Auto-open 3D WebGL Studio in browser upon calculation")
        self.chk_auto_open.setChecked(True)
        self.chk_auto_open.setStyleSheet("color: #0369a1; font-weight: 600; font-size: 11px;")
        act_layout.addWidget(self.chk_auto_open)

        # Open 3D Studio Button
        self.btn_open_3d = QPushButton("🌐 Open 3D WebGL Studio in Browser (60 FPS)")
        self.btn_open_3d.setStyleSheet("""
            QPushButton {
                background: #f1f5f9;
                color: #0f172a;
                border: 1px solid #cbd5e1;
                font-weight: 700;
                font-size: 12px;
                padding: 7px 12px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background: #e2e8f0;
            }
        """)
        self.btn_open_3d.clicked.connect(self.open_3d_studio)
        act_layout.addWidget(self.btn_open_3d)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        act_layout.addWidget(self.progress_bar)

        # KPI Summary Grid
        kpi_grid = QGridLayout()
        self.kpi_dist = QLabel("—")
        self.kpi_time = QLabel("—")
        self.kpi_climb = QLabel("—")
        self.kpi_slope = QLabel("—")
        self.kpi_kcal = QLabel("—")

        for lbl in (self.kpi_dist, self.kpi_time, self.kpi_climb, self.kpi_slope, self.kpi_kcal):
            lbl.setProperty("class", "route3dKpi")

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
        scroll = QScrollArea(parent)
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        # Layer bindings
        grp_layers = QGroupBox("Environmental Raster Surfaces")
        lay_grid = QGridLayout(grp_layers)

        lay_grid.addWidget(QLabel("Elevation DEM:"), 0, 0)
        self.cmb_dem_layer = QgsMapLayerComboBox()
        self.cmb_dem_layer.setFilters(QgsMapLayerProxyModel.RasterLayer)
        lay_grid.addWidget(self.cmb_dem_layer, 0, 1)

        lay_grid.addWidget(QLabel("Thermal Heat LST:"), 1, 0)
        self.cmb_lst_layer = QgsMapLayerComboBox()
        self.cmb_lst_layer.setFilters(QgsMapLayerProxyModel.RasterLayer)
        lay_grid.addWidget(self.cmb_lst_layer, 1, 1)

        lay_grid.addWidget(QLabel("Tree Canopy / Greenery:"), 2, 0)
        self.cmb_green_layer = QgsMapLayerComboBox()
        self.cmb_green_layer.setFilters(QgsMapLayerProxyModel.RasterLayer)
        lay_grid.addWidget(self.cmb_green_layer, 2, 1)
        layout.addWidget(grp_layers)

        # AHP Sliders
        grp_weights = QGroupBox("MCDA Resistance Weights (AHP)")
        w_layout = QVBoxLayout(grp_weights)

        # Slope slider
        row_slope = QHBoxLayout()
        row_slope.addWidget(QLabel("Slope Incline Penalty:"))
        self.sld_slope = QSlider(Qt.Orientation.Horizontal)
        self.sld_slope.setRange(0, 100)
        self.sld_slope.setValue(45)
        self.lbl_val_slope = QLabel("45%")
        self.sld_slope.valueChanged.connect(lambda v: self.lbl_val_slope.setText(f"{v}%"))
        row_slope.addWidget(self.sld_slope)
        row_slope.addWidget(self.lbl_val_slope)
        w_layout.addLayout(row_slope)

        # Thermal slider
        row_heat = QHBoxLayout()
        row_heat.addWidget(QLabel("Heat Stress Avoidance:"))
        self.sld_heat = QSlider(Qt.Orientation.Horizontal)
        self.sld_heat.setRange(0, 100)
        self.sld_heat.setValue(30)
        self.lbl_val_heat = QLabel("30%")
        self.sld_heat.valueChanged.connect(lambda v: self.lbl_val_heat.setText(f"{v}%"))
        row_heat.addWidget(self.sld_heat)
        row_heat.addWidget(self.lbl_val_heat)
        w_layout.addLayout(row_heat)

        # Greenery slider
        row_green = QHBoxLayout()
        row_green.addWidget(QLabel("Shade / Green Preference:"))
        self.sld_green = QSlider(Qt.Orientation.Horizontal)
        self.sld_green.setRange(0, 100)
        self.sld_green.setValue(25)
        self.lbl_val_green = QLabel("25%")
        self.sld_green.valueChanged.connect(lambda v: self.lbl_val_green.setText(f"{v}%"))
        row_green.addWidget(self.sld_green)
        row_green.addWidget(self.lbl_val_green)
        w_layout.addLayout(row_green)

        layout.addWidget(grp_weights)
        layout.addStretch()

        scroll.setWidget(container)
        parent_layout = QVBoxLayout(parent)
        parent_layout.setContentsMargins(0, 0, 0, 0)
        parent_layout.addWidget(scroll)

    def _build_tab_od(self, parent: QWidget) -> None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(QLabel("<b>Multi-Point Origin-Destination Cost Matrix</b>"))

        self.table_od = QTableWidget()
        self.table_od.setColumnCount(5)
        self.table_od.setHorizontalHeaderLabels(["Origin", "Destination", "Dist (km)", "Time (min)", "Climb (m)"])
        self.table_od.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table_od)

        btn_run_od = QPushButton("📊 Compute Full OD Matrix")
        btn_run_od.clicked.connect(self._compute_od_matrix)
        layout.addWidget(btn_run_od)

    def _build_tab_settings(self, parent: QWidget) -> None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(QLabel("<b>Mobility Profile Configuration</b>"))

        self.btn_edit_profile = QPushButton("⚙️ Open Profile Editor Dialog")
        self.btn_edit_profile.clicked.connect(self._open_profile_editor)
        layout.addWidget(self.btn_edit_profile)

        grp_info = QGroupBox("Kinematic Equations & Literature Standards")
        info_layout = QVBoxLayout(grp_info)
        info_text = QLabel(
            "• <b>Pedestrian:</b> Tobler's Hyperbolic Hiking Function (1993)<br>"
            "• <b>Energy:</b> Minetti Metabolic Cost Polynomial (2002)<br>"
            "• <b>Cyclist:</b> Cycling Power Balance (Aerodynamic Drag + Rolling Resistance)<br>"
            "• <b>Comfort:</b> UTCI (Universal Thermal Climate Index) Stress Penalty<br>"
            "• <b>ADA Accessibility:</b> Wheelchair Maximum 6.0% Slope Incline Barrier"
        )
        info_text.setTextFormat(Qt.TextFormat.RichText)
        info_text.setWordWrap(True)
        info_layout.addWidget(info_text)
        layout.addWidget(grp_info)
        layout.addStretch()

    def _refresh_waypoint_list(self) -> None:
        self.lst_waypoints.clear()
        for idx, wp in enumerate(self.waypoints, start=1):
            item = QListWidgetItem(f"{idx}. {wp.name} ({wp.lon:.4f}, {wp.lat:.4f})")
            self.lst_waypoints.addItem(item)

    def _toggle_map_picker(self, checked: bool) -> None:
        if not self.canvas:
            return
        if checked:
            self.active_tool = RoutePointMapTool(self.canvas)
            self.active_tool.point_captured.connect(self._on_point_captured)
            self.canvas.setMapTool(self.active_tool)
        else:
            if self.active_tool:
                self.canvas.unsetMapTool(self.active_tool)
                self.active_tool = None

    def _on_point_captured(self, point: QgsPoint) -> None:
        name = f"Point {len(self.waypoints) + 1}"
        self.waypoints.append(Waypoint(lon=point.x(), lat=point.y(), name=name))
        self._refresh_waypoint_list()
        if self.btn_pick_map.isChecked():
            self.btn_pick_map.setChecked(False)
            self._toggle_map_picker(False)

    def _add_waypoint_dialog(self) -> None:
        name = f"Stop {len(self.waypoints) + 1}"
        if self.waypoints:
            last = self.waypoints[-1]
            self.waypoints.append(Waypoint(lon=last.lon + 0.005, lat=last.lat + 0.005, name=name))
        else:
            self.waypoints.append(Waypoint(lon=27.1428, lat=38.4237, name="Izmir Point"))
        self._refresh_waypoint_list()

    def _remove_selected_waypoint(self) -> None:
        row = self.lst_waypoints.currentRow()
        if 0 <= row < len(self.waypoints):
            self.waypoints.pop(row)
            self._refresh_waypoint_list()

    def _reverse_waypoints(self) -> None:
        self.waypoints.reverse()
        self._refresh_waypoint_list()

    def _optimize_stops_tsp(self) -> None:
        if len(self.waypoints) < 3:
            return
        from ..core.tsp_solver import solve_tsp_order
        coords = [(w.lon, w.lat, 0.0) for w in self.waypoints]
        order = solve_tsp_order(coords, fix_start=True, fix_end=False)
        self.waypoints = [self.waypoints[i] for i in order]
        self._refresh_waypoint_list()

    def _on_profile_changed(self, index: int) -> None:
        key = self.cmb_profile.itemData(index) or "adult"
        p = get_profile(key)
        stairs = "Allowed" if p.stair_allowed else "Prohibited"
        self.lbl_profile_info.setText(f"Speed: {p.base_speed_kmh:.1f} km/h | Max Slope: {p.max_slope_pct:.1f}% | Stairs: {stairs}")

    def _on_search_location(self) -> None:
        query = self.txt_search.text().strip()
        if not query:
            return
        import http.client
        import urllib.parse
        try:
            encoded_query = urllib.parse.quote(query)
            conn = http.client.HTTPSConnection("photon.komoot.io", timeout=4)
            headers = {"User-Agent": "02Route3D-QGIS-Plugin"}
            conn.request("GET", f"/api/?q={encoded_query}&limit=1", headers=headers)
            response = conn.getresponse()
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                features = data.get("features", [])
                if features:
                    coords = features[0]["geometry"]["coordinates"]
                    name = features[0].get("properties", {}).get("name", query)
                    self.waypoints.append(Waypoint(lon=coords[0], lat=coords[1], name=name))
                    self._refresh_waypoint_list()
                    if self.iface:
                        self.iface.messageBar().pushSuccess("02Route 3D", f"Found & Added: {name}")
                else:
                    if self.iface:
                        self.iface.messageBar().pushWarning("02Route 3D", "No results found for location.")
            conn.close()
        except Exception:
            if self.iface:
                self.iface.messageBar().pushWarning("02Route 3D", "Photon geocoding service unavailable.")

    def _open_profile_editor(self) -> None:
        key = self.cmb_profile.currentData() or "adult"
        prof = get_profile(key)
        dialog = ProfileEditorDialog(prof, parent=self)
        if dialog.exec_():
            updated = dialog.get_updated_profile()
            PROFILES[updated.key] = updated
            self._on_profile_changed(self.cmb_profile.currentIndex())

    def compute_route(self) -> None:
        """Compute the 3D shortest/least-cost route and optionally pop the 3D WebGL Studio."""
        if len(self.waypoints) < 2:
            QMessageBox.warning(self, "02Route 3D", "Please provide at least 2 waypoints (Origin & Destination).")
            return

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(20)

        # Environmental raster sampling
        weights = MCDAWeights(
            slope=self.sld_slope.value() / 100.0,
            heat=self.sld_heat.value() / 100.0,
            green=self.sld_green.value() / 100.0,
        )
        sampler = EnvironmentalSurfaceSampler(
            dem_layer=self.cmb_dem_layer.currentLayer(),
            lst_layer=self.cmb_lst_layer.currentLayer(),
            green_layer=self.cmb_green_layer.currentLayer(),
            weights=weights,
        )

        engine = RoutingEngine3D(sampler=sampler)
        lons = [w.lon for w in self.waypoints]
        lats = [w.lat for w in self.waypoints]
        bbox = (min(lons) - 0.02, min(lats) - 0.02, max(lons) + 0.02, max(lats) + 0.02)
        segments = self.network_manager.generate_synthetic_grid(bbox, grid_steps=10)
        engine.build_graph(segments)

        self.progress_bar.setValue(60)

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

        # Enable Export Buttons
        self.btn_add_layer.setEnabled(True)
        self.btn_export_gpx.setEnabled(True)
        self.btn_export_geojson.setEnabled(True)
        self.btn_export_html.setEnabled(True)
        self.btn_export_dxf.setEnabled(True)

        # Save route JSON for 3D Studio auto-fetch
        geojson_data = result.to_geojson_feature()
        with contextlib.suppress(Exception):
            self.current_route_file.write_text(json.dumps(geojson_data, indent=2), encoding="utf-8")

        self.progress_bar.setValue(100)
        self.progress_bar.setVisible(False)
        self.route_calculated.emit(result)

        if self.chk_auto_open.isChecked():
            self.open_3d_studio()

    def open_3d_studio(self) -> None:
        """Start local HTTP server and launch the 3D WebGL studio in default browser."""
        server_url = self.local_server.start()
        QDesktopServices.openUrl(QUrl(server_url))

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

        layer = QgsVectorLayer("LineStringZ?crs=EPSG:4326", "02Route 3D Path", "memory")
        pr = layer.dataProvider()
        pr.addAttributes([
            QgsField("profile", 10),
            QgsField("dist_km", 6),
            QgsField("time_min", 6),
            QgsField("climb_m", 6),
            QgsField("max_slope", 6),
            QgsField("calories", 6),
        ])
        layer.updateFields()

        pts = [QgsPoint(c[0], c[1], c[2]) for c in self.current_route_result.coordinates_3d]
        geom = QgsGeometry.fromPolyline(pts)
        feat = QgsFeature()
        feat.setGeometry(geom)
        feat.setAttributes([
            self.current_route_result.profile_key,
            self.current_route_result.statistics.total_distance_km,
            self.current_route_result.statistics.total_duration_min,
            self.current_route_result.statistics.elevation_gain_m,
            self.current_route_result.statistics.max_slope_pct,
            self.current_route_result.statistics.total_calories_kcal,
        ])
        pr.addFeatures([feat])
        layer.updateExtents()

        from ..core.qml_generator import generate_route_qml_style
        qml_xml = generate_route_qml_style(self.current_route_result.profile_key, line_width_mm=1.2)
        qml_path = self.web_root / "temp_route_style.qml"
        with contextlib.suppress(Exception):
            qml_path.write_text(qml_xml, encoding="utf-8")
            layer.loadNamedStyle(str(qml_path))

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
            bundler = StandaloneHtmlBundler(self.web_root)
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
        if self.local_server:
            self.local_server.stop()
        super().closeEvent(event)
