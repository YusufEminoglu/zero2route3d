"""Unified 5-Tab Studio DockWidget for 02Route 3D."""
from __future__ import annotations

import contextlib
import http.client
import json
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from qgis.PyQt.QtCore import Qt, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QColor, QCursor, QDesktopServices
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
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsFillSymbol,
    QgsGeometry,
    QgsLineSymbol,
    QgsMapLayerProxyModel,
    QgsMarkerSymbol,
    QgsPoint,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
)
from qgis.gui import QgsMapCanvas, QgsMapLayerComboBox

from ..core.ahp_engine import AHPEngine
from ..core.basemap import add_osm_basemap
from ..core.environmental_raster import EnvironmentalSurfaceSampler, MCDAWeights
from ..core.mobility_profiles import PROFILES, MobilityProfile, get_profile, list_profile_keys
from ..core.network_source import NetworkSourceManager, RoadSegment
from ..core.osm_downloader import OsmBuilding, OsmDataFetcher
from ..core.route_corridor_3d import filter_buildings_in_corridor
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
        self.picking_target: str = "A"  # "A" or "B"
        self.current_route_result: Optional[RouteResult3D] = None
        self.cached_osm_buildings: List[OsmBuilding] = []

        self.point_a = Waypoint(lon=27.1428, lat=38.4237, name="Point A (Origin)")
        self.point_b = Waypoint(lon=27.1510, lat=38.4300, name="Point B (Destination)")
        self.waypoints: List[Waypoint] = [self.point_a, self.point_b]

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

        # 1. OSM Basemap & Acquisition Action Card
        card_osm = QFrame()
        card_osm.setProperty("class", "route3dCard")
        osm_layout = QVBoxLayout(card_osm)
        osm_header = QLabel("<b>1. OpenStreetMap Basemap & Layers</b>")
        osm_layout.addWidget(osm_header)

        osm_btn_row = QHBoxLayout()
        self.btn_add_basemap = QPushButton("🗺️ Add OSM Basemap")
        self.btn_add_basemap.setToolTip("Loads OpenStreetMap standard XYZ basemap into project")
        self.btn_add_basemap.clicked.connect(self._on_add_osm_basemap)
        osm_btn_row.addWidget(self.btn_add_basemap)

        self.btn_fetch_osm = QPushButton("⬇️ Fetch Roads & Buildings")
        self.btn_fetch_osm.setToolTip("Downloads real OSM road network and 3D building footprints for current area")
        self.btn_fetch_osm.clicked.connect(self._fetch_osm_layers_for_extent)
        osm_btn_row.addWidget(self.btn_fetch_osm)
        osm_layout.addLayout(osm_btn_row)
        layout.addWidget(card_osm)

        # 2. Point A & Point B Dual Origin/Destination Card
        card_ab = QFrame()
        card_ab.setProperty("class", "route3dCard")
        ab_layout = QVBoxLayout(card_ab)
        ab_layout.addWidget(QLabel("<b>2. Route Points (A & B)</b>"))

        # Point A Box
        grp_a = QGroupBox("📍 Point A (Origin)")
        a_vbox = QVBoxLayout(grp_a)
        a_row = QHBoxLayout()
        self.lbl_coord_a = QLabel(f"{self.point_a.lon:.4f}, {self.point_a.lat:.4f}")
        self.lbl_coord_a.setStyleSheet("font-weight: bold; color: #059669;")
        a_row.addWidget(self.lbl_coord_a)
        a_row.addStretch()

        self.btn_pick_a = QPushButton("📍 Pick on Map")
        self.btn_pick_a.setCheckable(True)
        self.btn_pick_a.clicked.connect(lambda chk: self._toggle_specific_picker("A", chk))
        a_row.addWidget(self.btn_pick_a)
        a_vbox.addLayout(a_row)

        a_layer_row = QHBoxLayout()
        a_layer_row.addWidget(QLabel("Or from Layer:"))
        self.cmb_layer_a = QgsMapLayerComboBox()
        self.cmb_layer_a.setFilters(QgsMapLayerProxyModel.PointLayer | QgsMapLayerProxyModel.PolygonLayer)
        a_layer_row.addWidget(self.cmb_layer_a)
        btn_use_layer_a = QPushButton("Use Layer")
        btn_use_layer_a.clicked.connect(lambda: self._set_point_from_layer("A"))
        a_layer_row.addWidget(btn_use_layer_a)
        a_vbox.addLayout(a_layer_row)
        ab_layout.addWidget(grp_a)

        # Point B Box
        grp_b = QGroupBox("🎯 Point B (Destination)")
        b_vbox = QVBoxLayout(grp_b)
        b_row = QHBoxLayout()
        self.lbl_coord_b = QLabel(f"{self.point_b.lon:.4f}, {self.point_b.lat:.4f}")
        self.lbl_coord_b.setStyleSheet("font-weight: bold; color: #dc2626;")
        b_row.addWidget(self.lbl_coord_b)
        b_row.addStretch()

        self.btn_pick_b = QPushButton("📍 Pick on Map")
        self.btn_pick_b.setCheckable(True)
        self.btn_pick_b.clicked.connect(lambda chk: self._toggle_specific_picker("B", chk))
        b_row.addWidget(self.btn_pick_b)
        b_vbox.addLayout(b_row)

        b_layer_row = QHBoxLayout()
        b_layer_row.addWidget(QLabel("Or from Layer:"))
        self.cmb_layer_b = QgsMapLayerComboBox()
        self.cmb_layer_b.setFilters(QgsMapLayerProxyModel.PointLayer | QgsMapLayerProxyModel.PolygonLayer)
        b_layer_row.addWidget(self.cmb_layer_b)
        btn_use_layer_b = QPushButton("Use Layer")
        btn_use_layer_b.clicked.connect(lambda: self._set_point_from_layer("B"))
        b_layer_row.addWidget(btn_use_layer_b)
        b_vbox.addLayout(b_layer_row)
        ab_layout.addWidget(grp_b)

        # Reverse A & B Button
        btn_rev_ab = QPushButton("⇄ Swap Origin & Destination")
        btn_rev_ab.clicked.connect(self._reverse_waypoints)
        ab_layout.addWidget(btn_rev_ab)

        layout.addWidget(card_ab)

        # 3. Mobility Profile Selector Card
        card_prof = QFrame()
        card_prof.setProperty("class", "route3dCard")
        prof_layout = QVBoxLayout(card_prof)
        prof_layout.addWidget(QLabel("<b>3. User Mobility Profile</b>"))

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

        # 4. Action Card (Compute + 3D Corridor Animation)
        card_act = QFrame()
        card_act.setProperty("class", "route3dCard")
        act_layout = QVBoxLayout(card_act)

        # Primary Compute 3D Route
        self.btn_compute = QPushButton("⚡ Compute 3D Shortest Path")
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

        # Open 3D WebGL Studio Button
        self.btn_open_3d = QPushButton("🎬 Open 3D Animation & 50m Building Corridor (60 FPS)")
        self.btn_open_3d.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #059669, stop:1 #10b981);
                color: #ffffff;
                border: none;
                font-weight: 700;
                font-size: 12px;
                padding: 10px 14px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #047857, stop:1 #059669);
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
        self.btn_export_html.setToolTip("Export standalone 3D HTML report")
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
            "• <b>Corridor Buffer:</b> 50-meter 3D building footprint extrusion along route centerline"
        )
        info_text.setTextFormat(Qt.TextFormat.RichText)
        info_text.setWordWrap(True)
        info_layout.addWidget(info_text)
        layout.addWidget(grp_info)
        layout.addStretch()

    def _on_add_osm_basemap(self) -> None:
        """Add or reveal the OpenStreetMap standard XYZ tile layer."""
        try:
            _, created = add_osm_basemap()
            if self.iface:
                msg = "OpenStreetMap basemap added to project." if created else "OpenStreetMap basemap revealed."
                self.iface.messageBar().pushSuccess("02Route 3D", msg)
        except Exception as e:
            if self.iface:
                self.iface.messageBar().pushWarning("02Route 3D", f"Could not load OSM basemap: {e}")

    def _toggle_specific_picker(self, target: str, checked: bool) -> None:
        if not self.canvas:
            return
        if checked:
            self.picking_target = target
            if target == "A" and self.btn_pick_b.isChecked():
                self.btn_pick_b.setChecked(False)
            elif target == "B" and self.btn_pick_a.isChecked():
                self.btn_pick_a.setChecked(False)

            self.active_tool = RoutePointMapTool(self.canvas)
            self.active_tool.point_captured.connect(self._on_point_captured)
            self.canvas.setMapTool(self.active_tool)
            if self.iface:
                self.iface.messageBar().pushInfo("02Route 3D", f"Click on map canvas to set Point {target}...")
        else:
            if self.active_tool:
                self.canvas.unsetMapTool(self.active_tool)
                self.active_tool = None

    def _on_point_captured(self, point: Any) -> None:
        lon = float(point.x())
        lat = float(point.y())
        if self.picking_target == "A":
            self.point_a = Waypoint(lon=lon, lat=lat, name="Point A (Origin)")
            self.lbl_coord_a.setText(f"{lon:.4f}, {lat:.4f}")
            self.btn_pick_a.setChecked(False)
        else:
            self.point_b = Waypoint(lon=lon, lat=lat, name="Point B (Destination)")
            self.lbl_coord_b.setText(f"{lon:.4f}, {lat:.4f}")
            self.btn_pick_b.setChecked(False)

        if self.active_tool:
            self.canvas.unsetMapTool(self.active_tool)
            self.active_tool = None

        self.waypoints = [self.point_a, self.point_b]
        self._update_point_vector_layers()

        if self.iface:
            self.iface.messageBar().pushSuccess("02Route 3D", f"Point {self.picking_target} updated on map canvas!")

    def _set_point_from_layer(self, target: str) -> None:
        """Extract Point A or B coordinate from selected feature or centroid of chosen QGIS layer."""
        cmb = self.cmb_layer_a if target == "A" else self.cmb_layer_b
        layer = cmb.currentLayer()
        if not layer or not layer.isValid():
            QMessageBox.warning(self, "02Route 3D", f"Please select a valid vector layer for Point {target}.")
            return

        # Check selected features first
        selected = layer.selectedFeatures()
        feat = selected[0] if selected else next(layer.getFeatures(), None)
        if not feat or not feat.geometry():
            QMessageBox.warning(self, "02Route 3D", f"No features found in layer '{layer.name()}'.")
            return

        geom = feat.geometry()
        pt = geom.centroid().asPoint()

        # Transform to WGS84
        crs_layer = layer.crs()
        crs_wgs = QgsCoordinateReferenceSystem("EPSG:4326")
        if crs_layer != crs_wgs:
            transform = QgsCoordinateTransform(crs_layer, crs_wgs, QgsProject.instance())
            pt = transform.transform(pt)

        if target == "A":
            self.point_a = Waypoint(lon=pt.x(), lat=pt.y(), name=f"Point A ({layer.name()})")
            self.lbl_coord_a.setText(f"{pt.x():.4f}, {pt.y():.4f}")
        else:
            self.point_b = Waypoint(lon=pt.x(), lat=pt.y(), name=f"Point B ({layer.name()})")
            self.lbl_coord_b.setText(f"{pt.x():.4f}, {pt.y():.4f}")

        self.waypoints = [self.point_a, self.point_b]
        self._update_point_vector_layers()

        if self.iface:
            self.iface.messageBar().pushSuccess("02Route 3D", f"Point {target} loaded from '{layer.name()}'!")

    def _update_point_vector_layers(self) -> None:
        """Create or update dedicated Point A and Point B pin vector layers on the QGIS canvas."""
        proj = QgsProject.instance()

        # 1. Point A Layer
        layer_a_name = "📍 Route Point A (Origin)"
        layers_a = proj.mapLayersByName(layer_a_name)
        layer_a = layers_a[0] if layers_a else QgsVectorLayer("Point?crs=EPSG:4326", layer_a_name, "memory")
        pr_a = layer_a.dataProvider()
        pr_a.truncate()
        if not layer_a.fields():
            pr_a.addAttributes([QgsField("name", 10), QgsField("lon", 6), QgsField("lat", 6)])
            layer_a.updateFields()
        f_a = QgsFeature()
        f_a.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(self.point_a.lon, self.point_a.lat)))
        f_a.setAttributes(["Point A (Origin)", self.point_a.lon, self.point_a.lat])
        pr_a.addFeatures([f_a])
        layer_a.updateExtents()

        sym_a = QgsMarkerSymbol.createSimple({
            "name": "circle",
            "color": "#059669",
            "outline_color": "#ffffff",
            "outline_width": "0.8",
            "size": "5.0",
        })
        layer_a.setRenderer(QgsSingleSymbolRenderer(sym_a))

        if not layers_a:
            proj.addMapLayer(layer_a)

        # 2. Point B Layer
        layer_b_name = "🎯 Route Point B (Destination)"
        layers_b = proj.mapLayersByName(layer_b_name)
        layer_b = layers_b[0] if layers_b else QgsVectorLayer("Point?crs=EPSG:4326", layer_b_name, "memory")
        pr_b = layer_b.dataProvider()
        pr_b.truncate()
        if not layer_b.fields():
            pr_b.addAttributes([QgsField("name", 10), QgsField("lon", 6), QgsField("lat", 6)])
            layer_b.updateFields()
        f_b = QgsFeature()
        f_b.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(self.point_b.lon, self.point_b.lat)))
        f_b.setAttributes(["Point B (Destination)", self.point_b.lon, self.point_b.lat])
        pr_b.addFeatures([f_b])
        layer_b.updateExtents()

        sym_b = QgsMarkerSymbol.createSimple({
            "name": "circle",
            "color": "#dc2626",
            "outline_color": "#ffffff",
            "outline_width": "0.8",
            "size": "5.0",
        })
        layer_b.setRenderer(QgsSingleSymbolRenderer(sym_b))

        if not layers_b:
            proj.addMapLayer(layer_b)

        if self.canvas:
            self.canvas.refresh()

    def _reverse_waypoints(self) -> None:
        self.point_a, self.point_b = self.point_b, self.point_a
        self.lbl_coord_a.setText(f"{self.point_a.lon:.4f}, {self.point_a.lat:.4f}")
        self.lbl_coord_b.setText(f"{self.point_b.lon:.4f}, {self.point_b.lat:.4f}")
        self.waypoints = [self.point_a, self.point_b]
        self._update_point_vector_layers()

    def _on_profile_changed(self, index: int) -> None:
        key = self.cmb_profile.itemData(index) or "adult"
        p = get_profile(key)
        stairs = "Allowed" if p.stair_allowed else "Prohibited"
        self.lbl_profile_info.setText(f"Speed: {p.base_speed_kmh:.1f} km/h | Max Slope: {p.max_slope_pct:.1f}% | Stairs: {stairs}")

    def _get_active_bbox(self) -> Tuple[float, float, float, float]:
        """Compute WGS84 bounding box from canvas or waypoints."""
        if self.canvas:
            extent = self.canvas.extent()
            crs_canvas = self.canvas.mapSettings().destinationCrs()
            crs_wgs = QgsCoordinateReferenceSystem("EPSG:4326")
            transform = QgsCoordinateTransform(crs_canvas, crs_wgs, QgsProject.instance())
            rect = transform.transformBoundingBox(extent)
            return (rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum())
        if self.waypoints:
            lons = [w.lon for w in self.waypoints]
            lats = [w.lat for w in self.waypoints]
            return (min(lons) - 0.015, min(lats) - 0.015, max(lons) + 0.015, max(lats) + 0.015)
        return (27.13, 38.41, 27.17, 38.45)

    def _fetch_osm_layers_for_extent(self) -> None:
        """Download real OSM roads and building footprints for current area and load into QGIS."""
        bbox = self._get_active_bbox()
        if self.iface:
            self.iface.messageBar().pushInfo("02Route 3D", "Fetching real OpenStreetMap roads and 3D building footprints from Overpass API...")

        roads, buildings = OsmDataFetcher.fetch_roads_and_buildings(bbox)
        self.cached_osm_buildings = buildings

        if not roads and not buildings:
            if self.iface:
                self.iface.messageBar().pushWarning("02Route 3D", "No OSM elements found in current bounding box.")
            return

        # 1. Create Roads Layer
        if roads:
            road_layer = QgsVectorLayer("LineString?crs=EPSG:4326", "OSM Road Network", "memory")
            r_pr = road_layer.dataProvider()
            r_pr.addAttributes([QgsField("name", 10), QgsField("highway", 10), QgsField("oneway", 1)])
            road_layer.updateFields()
            r_feats = []
            for r in roads:
                f = QgsFeature()
                pts = [QgsPointXY(p[0], p[1]) for p in r.geometry]
                f.setGeometry(QgsGeometry.fromPolylineXY(pts))
                f.setAttributes([r.name, r.highway_type, 1 if r.oneway else 0])
                r_feats.append(f)
            r_pr.addFeatures(r_feats)
            road_layer.updateExtents()

            sym_road = QgsLineSymbol.createSimple({
                "line_color": "#475569",
                "line_width": "0.6",
                "line_style": "solid",
            })
            road_layer.setRenderer(QgsSingleSymbolRenderer(sym_road))
            QgsProject.instance().addMapLayer(road_layer)

        # 2. Create Buildings Layer
        if buildings:
            bld_layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "OSM Buildings 3D", "memory")
            b_pr = bld_layer.dataProvider()
            b_pr.addAttributes([QgsField("height_m", 6), QgsField("levels", 2), QgsField("type", 10)])
            bld_layer.updateFields()
            b_feats = []
            for b in buildings:
                f = QgsFeature()
                pts = [QgsPointXY(p[0], p[1]) for p in b.polygon]
                f.setGeometry(QgsGeometry.fromPolygonXY([pts]))
                f.setAttributes([b.height_m, b.levels, b.building_type])
                b_feats.append(f)
            b_pr.addFeatures(b_feats)
            bld_layer.updateExtents()

            sym_bld = QgsFillSymbol.createSimple({
                "color": "#cbd5e166",
                "outline_color": "#64748b",
                "outline_width": "0.3",
            })
            bld_layer.setRenderer(QgsSingleSymbolRenderer(sym_bld))
            QgsProject.instance().addMapLayer(bld_layer)

        if self.iface:
            self.iface.messageBar().pushSuccess("02Route 3D", f"Acquired {len(roads)} OSM roads and {len(buildings)} building footprints!")

    def compute_route(self) -> None:
        """Compute the 3D shortest path, output QGIS line layer, extract 50m corridor buildings, and sync 3D studio."""
        self.waypoints = [self.point_a, self.point_b]

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(20)

        # Bounding box & OSM acquisition
        lons = [w.lon for w in self.waypoints]
        lats = [w.lat for w in self.waypoints]
        bbox = (min(lons) - 0.015, min(lats) - 0.015, max(lons) + 0.015, max(lats) + 0.015)

        if not self.cached_osm_buildings:
            _, buildings = OsmDataFetcher.fetch_roads_and_buildings(bbox)
            self.cached_osm_buildings = buildings

        # Environmental raster sampling
        weights = MCDAWeights(
            weight_slope=self.sld_slope.value() / 100.0,
            weight_heat=self.sld_heat.value() / 100.0,
            weight_green=self.sld_green.value() / 100.0,
        )
        sampler = EnvironmentalSurfaceSampler(
            dem_layer=self.cmb_dem_layer.currentLayer(),
            lst_layer=self.cmb_lst_layer.currentLayer(),
            green_layer=self.cmb_green_layer.currentLayer(),
            weights=weights,
        )

        engine = RoutingEngine3D(sampler=sampler, weights=weights)
        segments = self.network_manager.fetch_osm_network_bbox(bbox)
        if not segments:
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

        # 50m Linear Corridor Building Filter
        corridor_blds = filter_buildings_in_corridor(result.coordinates_3d, self.cached_osm_buildings, buffer_meters=50.0)

        # Convert to GeoJSON Feature with real corridor buildings
        geojson_data = result.to_geojson_feature()
        geojson_data["properties"]["corridor_buildings"] = corridor_blds

        with contextlib.suppress(Exception):
            self.current_route_file.write_text(json.dumps(geojson_data, indent=2), encoding="utf-8")

        # Automatically add the computed route layer to QGIS
        self.add_route_layer_to_qgis()

        self.progress_bar.setValue(100)
        self.progress_bar.setVisible(False)
        self.route_calculated.emit(result)

    def open_3d_studio(self) -> None:
        """Start local HTTP server and launch the 3D WebGL studio with 50m building corridor in browser."""
        server_url = self.local_server.start()
        QDesktopServices.openUrl(QUrl(server_url))

    def _open_profile_editor(self) -> None:
        key = self.cmb_profile.currentData() or "adult"
        prof = get_profile(key)
        dialog = ProfileEditorDialog(prof, parent=self)
        if dialog.exec_():
            updated = dialog.get_updated_profile()
            PROFILES[updated.key] = updated
            self._on_profile_changed(self.cmb_profile.currentIndex())

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

        layer_name = f"Route 3D ({self.current_route_result.profile.name})"
        layer = QgsVectorLayer("LineStringZ?crs=EPSG:4326", layer_name, "memory")
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
            self.current_route_result.profile.key,
            self.current_route_result.statistics.total_distance_km,
            self.current_route_result.statistics.total_duration_min,
            self.current_route_result.statistics.elevation_gain_m,
            self.current_route_result.statistics.max_slope_pct,
            self.current_route_result.statistics.total_calories_kcal,
        ])
        pr.addFeatures([feat])
        layer.updateExtents()

        from ..core.qml_generator import generate_route_qml_style
        qml_xml = generate_route_qml_style(self.current_route_result.profile.key, line_width_mm=1.2)
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
