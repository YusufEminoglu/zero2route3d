"""Unified 5-Tab Studio DockWidget for 02Route 3D."""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from qgis.PyQt.QtCore import Qt, QUrl, QVariant, pyqtSignal
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
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
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QListWidget,
    QListWidgetItem,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsMapLayerProxyModel,
    QgsMarkerSymbol,
    QgsPoint,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
)
from qgis.gui import QgsMapCanvas, QgsMapLayerComboBox

from ..core.basemap import add_osm_basemap
from ..core.copernicus_dem import CopernicusDemError, CopernicusDemTileSource
from ..core.environmental_raster import EnvironmentalSurfaceSampler, MCDAWeights
from ..core.mobility_profiles import (
    PROFILES,
    get_profile,
    list_profile_keys,
    list_profile_keys_for_group,
)
from ..core.network_source import NetworkSourceError, NetworkSourceManager
from ..core.osm_downloader import OsmBuilding, OsmDataFetcher
from ..core.osm_styling import apply_osm_atlas_style
from ..core.qml_generator import apply_multiprofile_categorized_renderer
from ..core.route_corridor_3d import filter_buildings_in_corridor, filter_corridor_assets_multi_route
from ..core.routing_engine import RouteResult3D, RoutingEngine3D, Waypoint
from .canvas_animator import Route2DCanvasAnimator
from .cue_sheet_widget import CueSheetWidget
from .map_tools import RoutePointMapTool
from .profile_editor import ProfileEditorDialog
from .server import Route3DLocalServer
from .theme import apply_adaptive_theme


def _vector_filters() -> Any:
    if hasattr(Qgis, "LayerFilter") and hasattr(Qgis, "LayerFilters"):
        return Qgis.LayerFilters(Qgis.LayerFilter.PointLayer | Qgis.LayerFilter.PolygonLayer)
    if hasattr(QgsMapLayerProxyModel, "PointLayer") and hasattr(QgsMapLayerProxyModel, "PolygonLayer"):
        return QgsMapLayerProxyModel.PointLayer | QgsMapLayerProxyModel.PolygonLayer
    return getattr(QgsMapLayerProxyModel, "VectorLayer", 1)


def _raster_filters() -> Any:
    if hasattr(Qgis, "LayerFilter") and hasattr(Qgis, "LayerFilters"):
        return Qgis.LayerFilters(Qgis.LayerFilter.RasterLayer)
    return getattr(QgsMapLayerProxyModel, "RasterLayer", 2)


def _line_filters() -> Any:
    if hasattr(Qgis, "LayerFilter") and hasattr(Qgis, "LayerFilters"):
        return Qgis.LayerFilters(Qgis.LayerFilter.LineLayer)
    return getattr(QgsMapLayerProxyModel, "LineLayer", 4)


def _polygon_filters() -> Any:
    if hasattr(Qgis, "LayerFilter") and hasattr(Qgis, "LayerFilters"):
        return Qgis.LayerFilters(Qgis.LayerFilter.PolygonLayer)
    return getattr(QgsMapLayerProxyModel, "PolygonLayer", 8)


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
        self.multi_route_results: Dict[str, RouteResult3D] = {}
        self.cached_osm_buildings: List[OsmBuilding] = []
        self.copernicus_dem_layers: List[Any] = []
        self._managed_route_layer_ids: set[str] = set()
        self._handling_layer_removal = False

        self.point_a: Optional[Waypoint] = None
        self.point_b: Optional[Waypoint] = None
        self.waypoints: List[Waypoint] = []

        self.web_root = Path(__file__).resolve().parent.parent / "web"
        self.data_dir = self.web_root / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.current_route_file = self.data_dir / "current_route.json"
        with contextlib.suppress(OSError):
            self.current_route_file.unlink()
        self.local_server = Route3DLocalServer(web_root=self.web_root)

        # 2D Map Canvas Real-Time Animator
        self.canvas_animator = Route2DCanvasAnimator(canvas=self.canvas, parent=self)
        self.canvas_animator.frame_updated.connect(self._on_anim_frame_updated)
        self.canvas_animator.playback_state_changed.connect(self._on_anim_state_changed)

        # Root Widget & Layout
        self.root_widget = QWidget(self)
        self.root_widget.setObjectName("route3dRoot")
        self.setWidget(self.root_widget)

        self._build_ui()
        apply_adaptive_theme(self.root_widget)
        QgsProject.instance().layersWillBeRemoved.connect(self._on_project_layers_removed)

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
        _ScrollBarAlwaysOff = getattr(getattr(Qt, "ScrollBarPolicy", Qt), "ScrollBarAlwaysOff", 1)
        scroll.setHorizontalScrollBarPolicy(_ScrollBarAlwaysOff)

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

        source_grid = QGridLayout()
        source_grid.addWidget(QLabel("Route road layer (optional):"), 0, 0)
        self.cmb_route_road_layer = QgsMapLayerComboBox()
        self.cmb_route_road_layer.setFilters(_line_filters())
        self.cmb_route_road_layer.setAllowEmptyLayer(True)
        self.cmb_route_road_layer.setToolTip("Use this line layer instead of downloading an OSM network.")
        source_grid.addWidget(self.cmb_route_road_layer, 0, 1)
        source_grid.addWidget(QLabel("3D building layer (optional):"), 1, 0)
        self.cmb_route_building_layer = QgsMapLayerComboBox()
        self.cmb_route_building_layer.setFilters(_polygon_filters())
        self.cmb_route_building_layer.setAllowEmptyLayer(True)
        self.cmb_route_building_layer.setToolTip("Use this polygon layer for QGIS styling and WebGL corridor buildings.")
        source_grid.addWidget(self.cmb_route_building_layer, 1, 1)
        btn_style_sources = QPushButton("Apply Civic Atlas style")
        btn_style_sources.clicked.connect(self._style_selected_source_layers)
        source_grid.addWidget(btn_style_sources, 2, 0, 1, 2)
        osm_layout.addLayout(source_grid)
        source_note = QLabel("If selected, external road/building layers replace the corresponding OSM source.")
        source_note.setStyleSheet("color: #64748b; font-size: 11px;")
        source_note.setWordWrap(True)
        osm_layout.addWidget(source_note)
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
        self.lbl_coord_a = QLabel("📍 Not selected (Pick on map or choose layer)")
        self.lbl_coord_a.setStyleSheet("font-style: italic; color: #64748b; font-size: 11px;")
        a_row.addWidget(self.lbl_coord_a)
        a_row.addStretch()

        self.btn_pick_a = QPushButton("📍 Pick on Map")
        self.btn_pick_a.setCheckable(True)
        self.btn_pick_a.clicked.connect(lambda chk: self._toggle_specific_picker("A", chk))
        a_row.addWidget(self.btn_pick_a)
        btn_clear_a = QPushButton("Clear")
        btn_clear_a.clicked.connect(lambda: self._clear_route_point("A"))
        a_row.addWidget(btn_clear_a)
        a_vbox.addLayout(a_row)

        a_layer_row = QHBoxLayout()
        a_layer_row.addWidget(QLabel("Or from Layer:"))
        self.cmb_layer_a = QgsMapLayerComboBox()
        self.cmb_layer_a.setFilters(_vector_filters())
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
        self.lbl_coord_b = QLabel("🎯 Not selected (Pick on map or choose layer)")
        self.lbl_coord_b.setStyleSheet("font-style: italic; color: #64748b; font-size: 11px;")
        b_row.addWidget(self.lbl_coord_b)
        b_row.addStretch()

        self.btn_pick_b = QPushButton("📍 Pick on Map")
        self.btn_pick_b.setCheckable(True)
        self.btn_pick_b.clicked.connect(lambda chk: self._toggle_specific_picker("B", chk))
        b_row.addWidget(self.btn_pick_b)
        btn_clear_b = QPushButton("Clear")
        btn_clear_b.clicked.connect(lambda: self._clear_route_point("B"))
        b_row.addWidget(btn_clear_b)
        b_vbox.addLayout(b_row)

        b_layer_row = QHBoxLayout()
        b_layer_row.addWidget(QLabel("Or from Layer:"))
        self.cmb_layer_b = QgsMapLayerComboBox()
        self.cmb_layer_b.setFilters(_vector_filters())
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

        # 3. Mobility Profile Selector & Multi-Modal Mode Scope Card
        card_prof = QFrame()
        card_prof.setProperty("class", "route3dCard")
        prof_layout = QVBoxLayout(card_prof)
        prof_layout.addWidget(QLabel("<b>3. User Mobility Profile & Mode Scope</b>"))

        # Mode Scope Selector (Single vs Category vs All)
        scope_row = QHBoxLayout()
        scope_row.addWidget(QLabel("Mode Scope:"))
        self.cmb_mode_scope = QComboBox()
        self.cmb_mode_scope.addItem("🔘 Single Profile Only", "single")
        self.cmb_mode_scope.addItem("🌐 All 15 Profiles (Full Multi-Modal Suite)", "all")
        self.cmb_mode_scope.addItem("🚶 Pedestrian Modes (6 Profiles)", "pedestrian")
        self.cmb_mode_scope.addItem("♿ Accessibility Modes (2 Profiles)", "accessibility")
        self.cmb_mode_scope.addItem("🚲 Micromobility Modes (3 Profiles)", "micromobility")
        self.cmb_mode_scope.addItem("🚗 Vehicle Modes (4 Profiles)", "vehicle")
        scope_row.addWidget(self.cmb_mode_scope)
        self.cmb_mode_scope.currentIndexChanged.connect(self._on_mode_scope_changed)
        prof_layout.addLayout(scope_row)

        prof_row = QHBoxLayout()
        prof_row.addWidget(QLabel("Focus Profile:"))
        self.cmb_profile = QComboBox()
        for key in list_profile_keys():
            p = get_profile(key)
            self.cmb_profile.addItem(p.name, key)
        self.cmb_profile.currentIndexChanged.connect(self._on_profile_changed)
        prof_row.addWidget(self.cmb_profile)
        prof_layout.addLayout(prof_row)

        self.lbl_profile_info = QLabel("Speed: 5.0 km/h | Max Slope: 25.0% | Stairs: Allowed")
        self.lbl_profile_info.setProperty("class", "route3dBadgeInfo")
        prof_layout.addWidget(self.lbl_profile_info)
        layout.addWidget(card_prof)

        # 4. Action Card (Compute + 3D Corridor Animation)
        card_act = QFrame()
        card_act.setProperty("class", "route3dCard")
        act_layout = QVBoxLayout(card_act)

        # Primary Compute 3D Route
        self.btn_compute = QPushButton("⚡ Compute 3D Shortest Path(s)")
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
        self.btn_open_3d = QPushButton("🎬 Open 3D WebGL Studio & 50m Building Corridor (60 FPS)")
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

        # 5. Real-Time 2D QGIS Canvas Animation Card
        card_anim = QFrame()
        card_anim.setProperty("class", "route3dCard")
        anim_layout = QVBoxLayout(card_anim)
        anim_layout.addWidget(QLabel("<b>5. Real-Time 2D Canvas Animation (QGIS Tuvali)</b>"))

        # Row 1: Playback Controls + Speed
        anim_ctrl_row = QHBoxLayout()
        self.btn_anim_play = QPushButton("▶️ Play")
        self.btn_anim_play.setEnabled(False)
        self.btn_anim_play.setToolTip("Play or pause real-time 2D canvas route animation")
        self.btn_anim_play.clicked.connect(self.canvas_animator.toggle_play)
        anim_ctrl_row.addWidget(self.btn_anim_play)

        self.btn_anim_stop = QPushButton("⏹️ Reset")
        self.btn_anim_stop.setEnabled(False)
        self.btn_anim_stop.setToolTip("Rewind animation to start")
        self.btn_anim_stop.clicked.connect(self.canvas_animator.stop)
        anim_ctrl_row.addWidget(self.btn_anim_stop)

        self.btn_reset_session = QPushButton("🧹 New Route")
        self.btn_reset_session.setToolTip("Clear animation, Web Guide payload, output layers, and A/B pins")
        self.btn_reset_session.clicked.connect(self._reset_route_session)
        anim_ctrl_row.addWidget(self.btn_reset_session)

        anim_ctrl_row.addWidget(QLabel("Speed:"))
        self.cmb_anim_speed = QComboBox()
        self.cmb_anim_speed.addItems(["1x", "2x", "5x", "10x", "25x", "50x", "100x"])
        self.cmb_anim_speed.setCurrentIndex(3)  # Default 10x
        self.cmb_anim_speed.currentIndexChanged.connect(self._on_anim_speed_changed)
        anim_ctrl_row.addWidget(self.cmb_anim_speed)
        anim_layout.addLayout(anim_ctrl_row)

        # Row 2: Interactive Progress Scrubber Slider
        _Horizontal = getattr(getattr(Qt, "Orientation", Qt), "Horizontal", 1)
        self.sld_anim_progress = QSlider(_Horizontal)
        self.sld_anim_progress.setRange(0, 1000)
        self.sld_anim_progress.setValue(0)
        self.sld_anim_progress.setEnabled(False)
        self.sld_anim_progress.sliderMoved.connect(self._on_anim_slider_moved)
        anim_layout.addWidget(self.sld_anim_progress)

        # Row 3: Status & Auto-Pan
        anim_status_row = QHBoxLayout()
        self.chk_anim_autopan = QCheckBox("🎯 Auto-pan canvas")
        self.chk_anim_autopan.setChecked(False)
        self.chk_anim_autopan.toggled.connect(self.canvas_animator.set_auto_pan)
        anim_status_row.addWidget(self.chk_anim_autopan)

        self.lbl_anim_status = QLabel("00:00 / 00:00 (0%) | Ready to animate")
        self.lbl_anim_status.setStyleSheet("color: #64748b; font-size: 11px;")
        anim_status_row.addWidget(self.lbl_anim_status)
        anim_status_row.addStretch()
        anim_layout.addLayout(anim_status_row)
        layout.addWidget(card_anim)

        # 6. Multi-Profile Comparison & Benchmarking Table Card
        card_comp = QFrame()
        card_comp.setProperty("class", "route3dCard")
        comp_layout = QVBoxLayout(card_comp)
        comp_layout.addWidget(QLabel("<b>6. Multi-Profile Comparison & Benchmarking</b>"))

        self.table_comparison = QTableWidget(0, 7)
        self.table_comparison.setHorizontalHeaderLabels([
            "Mode", "Category", "Distance", "Duration", "Climb", "Max Slope", "Energy / Status"
        ])
        _ResizeToContents = getattr(getattr(QHeaderView, "ResizeMode", QHeaderView), "ResizeToContents", 3)
        self.table_comparison.horizontalHeader().setSectionResizeMode(_ResizeToContents)
        _SelectRows = getattr(getattr(QTableWidget, "SelectionBehavior", QTableWidget), "SelectRows", 1)
        self.table_comparison.setSelectionBehavior(_SelectRows)
        self.table_comparison.cellClicked.connect(self._on_comparison_row_clicked)
        self.table_comparison.setMinimumHeight(160)
        comp_layout.addWidget(self.table_comparison)
        layout.addWidget(card_comp)
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
        self.cmb_dem_layer.setFilters(_raster_filters())
        lay_grid.addWidget(self.cmb_dem_layer, 0, 1)

        lay_grid.addWidget(QLabel("Thermal Heat LST:"), 1, 0)
        self.cmb_lst_layer = QgsMapLayerComboBox()
        self.cmb_lst_layer.setFilters(_raster_filters())
        lay_grid.addWidget(self.cmb_lst_layer, 1, 1)

        lay_grid.addWidget(QLabel("Tree Canopy / Greenery:"), 2, 0)
        self.cmb_green_layer = QgsMapLayerComboBox()
        self.cmb_green_layer.setFilters(_raster_filters())
        lay_grid.addWidget(self.cmb_green_layer, 2, 1)

        lay_grid.addWidget(QLabel("Additional MCDA rasters:"), 3, 0)
        extra_row = QHBoxLayout()
        self.cmb_extra_raster = QgsMapLayerComboBox()
        self.cmb_extra_raster.setFilters(_raster_filters())
        extra_row.addWidget(self.cmb_extra_raster, 1)
        btn_add_extra = QPushButton("Add")
        btn_add_extra.clicked.connect(self._add_extra_raster_layer)
        extra_row.addWidget(btn_add_extra)
        btn_remove_extra = QPushButton("Remove")
        btn_remove_extra.clicked.connect(self._remove_extra_raster_layer)
        extra_row.addWidget(btn_remove_extra)
        lay_grid.addLayout(extra_row, 3, 1)

        self.lst_extra_rasters = QListWidget()
        self.lst_extra_rasters.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.lst_extra_rasters.setMaximumHeight(92)
        lay_grid.addWidget(self.lst_extra_rasters, 4, 1)

        btn_fetch_dem = QPushButton("🌐 Fetch Real Copernicus 30m Topography for Extent")
        btn_fetch_dem.clicked.connect(self._on_fetch_global_dem_clicked)
        lay_grid.addWidget(btn_fetch_dem, 5, 0, 1, 2)

        lbl_dem_note = QLabel("ℹ️ <i>Add any number of raster layers. Each is normalized from its real statistics and unavailable layers are skipped. Copernicus loads official GLO-30 COG tiles only after a real network response; failed requests create no layer.</i>")
        lbl_dem_note.setStyleSheet("color: #64748b; font-size: 11px;")
        lbl_dem_note.setWordWrap(True)
        lay_grid.addWidget(lbl_dem_note, 6, 0, 1, 2)
        layout.addWidget(grp_layers)

        # AHP Sliders
        grp_weights = QGroupBox("MCDA Resistance Weights (AHP)")
        w_layout = QVBoxLayout(grp_weights)
        _Horizontal = getattr(getattr(Qt, "Orientation", Qt), "Horizontal", 1)

        # Slope slider
        row_slope = QHBoxLayout()
        row_slope.addWidget(QLabel("Slope Incline Penalty:"))
        self.sld_slope = QSlider(_Horizontal)
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
        self.sld_heat = QSlider(_Horizontal)
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
        self.sld_green = QSlider(_Horizontal)
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
        _Stretch = getattr(getattr(QHeaderView, "ResizeMode", QHeaderView), "Stretch", 1)
        self.table_od.horizontalHeader().setSectionResizeMode(_Stretch)
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
        _RichText = getattr(getattr(Qt, "TextFormat", Qt), "RichText", 1)
        info_text.setTextFormat(_RichText)
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

    def _style_selected_source_layers(self) -> None:
        """Apply the sibling downloader's Civic Atlas style to selected sources."""
        styled = 0
        for combo in (self.cmb_route_road_layer, self.cmb_route_building_layer):
            layer = combo.currentLayer()
            if layer is not None and apply_osm_atlas_style(layer):
                styled += 1
        if self.iface:
            if styled:
                self.iface.messageBar().pushSuccess("02Route 3D", f"Applied Civic Atlas styling to {styled} source layer(s).")
            else:
                self.iface.messageBar().pushWarning("02Route 3D", "Select a road or building layer first.")

    def _clear_route_point(self, target: str) -> None:
        """Clear one endpoint and remove its managed QGIS pin immediately."""
        if target == "A":
            self.point_a = None
            self.lbl_coord_a.setText("📍 Not selected (Pick on map or choose layer)")
            self.lbl_coord_a.setStyleSheet("font-style: italic; color: #64748b; font-size: 11px;")
            self.btn_pick_a.setChecked(False)
        else:
            self.point_b = None
            self.lbl_coord_b.setText("🎯 Not selected (Pick on map or choose layer)")
            self.lbl_coord_b.setStyleSheet("font-style: italic; color: #64748b; font-size: 11px;")
            self.btn_pick_b.setChecked(False)
        if self.active_tool is not None and self.canvas is not None:
            with contextlib.suppress(Exception):
                self.canvas.unsetMapTool(self.active_tool)
        self.active_tool = None
        self.waypoints = [point for point in (self.point_a, self.point_b) if point is not None]
        self._update_point_vector_layers()

    def _extract_buildings_from_layer(self, layer: Any) -> List[OsmBuilding]:
        """Convert any polygon layer into real corridor building records."""
        if layer is None or not layer.isValid():
            return []
        buildings: List[OsmBuilding] = []
        source_crs = layer.crs()
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        transform = None
        with contextlib.suppress(Exception):
            if source_crs.isValid() and source_crs != wgs84:
                transform = QgsCoordinateTransform(source_crs, wgs84, QgsProject.instance())
        fields = {name.lower(): name for name in layer.fields().names()}

        def _attribute(feature: Any, names: Tuple[str, ...], default: Any) -> Any:
            for name in names:
                field_name = fields.get(name.lower())
                if field_name:
                    value = feature[field_name]
                    if value not in (None, ""):
                        return value
            return default

        for feature in layer.getFeatures():
            geometry = QgsGeometry(feature.geometry())
            if geometry.isNull() or geometry.isEmpty():
                continue
            if transform is not None:
                with contextlib.suppress(Exception):
                    geometry.transform(transform)
            polygons = geometry.asMultiPolygon() if geometry.isMultipart() else [geometry.asPolygon()]
            for polygon in polygons:
                if not polygon or not polygon[0] or len(polygon[0]) < 3:
                    continue
                outer = [(float(point.x()), float(point.y())) for point in polygon[0]]
                try:
                    height = float(_attribute(feature, ("height_m", "height", "building_height"), 12.0))
                except (TypeError, ValueError):
                    height = 12.0
                try:
                    levels = max(1, int(float(_attribute(feature, ("levels", "building_levels", "floors"), 4))))
                except (TypeError, ValueError):
                    levels = 4
                buildings.append(
                    OsmBuilding(
                        building_id=str(feature.id()),
                        polygon=outer,
                        height_m=max(1.0, height),
                        levels=levels,
                        building_type=str(_attribute(feature, ("building", "type", "class"), "yes")),
                    )
                )
        return buildings

    def _clear_animation_state(self) -> None:
        """Stop and remove every transient animation/Web Guide artifact."""
        with contextlib.suppress(Exception):
            self.canvas_animator.stop()
            self.canvas_animator.clear()
        self.current_route_result = None
        self.multi_route_results = {}
        with contextlib.suppress(OSError):
            self.current_route_file.unlink()
        if hasattr(self, "table_comparison"):
            self.table_comparison.setRowCount(0)
        for label in (getattr(self, "kpi_dist", None), getattr(self, "kpi_time", None), getattr(self, "kpi_climb", None), getattr(self, "kpi_slope", None), getattr(self, "kpi_kcal", None)):
            if label is not None:
                label.setText("—")
        if hasattr(self, "lbl_anim_status"):
            self.lbl_anim_status.setText("00:00 / 00:00 (0%) | Ready to animate")
        for button in (getattr(self, "btn_anim_play", None), getattr(self, "btn_anim_stop", None)):
            if button is not None:
                button.setEnabled(False)
        if hasattr(self, "btn_anim_play") and self.btn_anim_play:
            self.btn_anim_play.setText("▶️ Play")
        if hasattr(self, "sld_anim_progress"):
            self.sld_anim_progress.setValue(0)
            self.sld_anim_progress.setEnabled(False)
        if hasattr(self, "btn_add_layer"):
            self.btn_add_layer.setEnabled(False)
        for button in (getattr(self, "btn_export_gpx", None), getattr(self, "btn_export_geojson", None), getattr(self, "btn_export_html", None), getattr(self, "btn_export_dxf", None)):
            if button is not None:
                button.setEnabled(False)

    def _reset_route_session(self) -> None:
        """Start a genuinely empty route session, including A/B point pins."""
        self._clear_animation_state()
        self._handling_layer_removal = True
        try:
            self._remove_transient_project_layers()
        finally:
            self._handling_layer_removal = False
        self._clear_route_point("A")
        self._clear_route_point("B")
        self.cached_osm_buildings = []
        if self.iface:
            self.iface.messageBar().pushInfo("02Route 3D", "Route session cleared. Select new Point A and Point B.")

    def _on_project_layers_removed(self, layer_ids: Any) -> None:
        """Remove transient animation state when its QGIS source/output disappears."""
        if self._handling_layer_removal:
            return
        removed = {str(layer_id) for layer_id in layer_ids}
        point_a_removed = False
        point_b_removed = False
        project = QgsProject.instance()
        for layer_id in removed:
            layer = project.mapLayer(layer_id)
            if layer is None:
                continue
            layer_name = layer.name()
            if "Route Point A" in layer_name or layer.customProperty("zero2route3d/route_point", "") == "A":
                point_a_removed = True
            if "Route Point B" in layer_name or layer.customProperty("zero2route3d/route_point", "") == "B":
                point_b_removed = True
        road_layer = self.cmb_route_road_layer.currentLayer()
        building_layer = self.cmb_route_building_layer.currentLayer()
        source_removed = bool(road_layer and road_layer.id() in removed) or bool(building_layer and building_layer.id() in removed)
        route_layers_removed = bool(removed & self._managed_route_layer_ids)
        if source_removed:
            self.cached_osm_buildings = []
        if point_a_removed:
            self.point_a = None
            self.lbl_coord_a.setText("📍 Not selected (Pick on map or choose layer)")
        if point_b_removed:
            self.point_b = None
            self.lbl_coord_b.setText("🎯 Not selected (Pick on map or choose layer)")
        if point_a_removed or point_b_removed:
            self.waypoints = [point for point in (self.point_a, self.point_b) if point is not None]
        if route_layers_removed or source_removed:
            self._handling_layer_removal = True
            try:
                self._clear_animation_state()
            finally:
                self._handling_layer_removal = False

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
            self.lbl_coord_a.setText(f"{lon:.5f}, {lat:.5f}")
            self.lbl_coord_a.setStyleSheet("font-weight: bold; color: #059669; font-size: 11px;")
            self.btn_pick_a.setChecked(False)
        else:
            self.point_b = Waypoint(lon=lon, lat=lat, name="Point B (Destination)")
            self.lbl_coord_b.setText(f"{lon:.5f}, {lat:.5f}")
            self.lbl_coord_b.setStyleSheet("font-weight: bold; color: #dc2626; font-size: 11px;")
            self.btn_pick_b.setChecked(False)

        if self.active_tool:
            self.canvas.unsetMapTool(self.active_tool)
            self.active_tool = None

        self.waypoints = [w for w in (self.point_a, self.point_b) if w is not None]
        self._update_point_vector_layers()

        if self.iface:
            self.iface.messageBar().pushSuccess("02Route 3D", f"Point {self.picking_target} set to ({lon:.5f}, {lat:.5f})!")

    def _set_point_from_layer(self, target: str) -> None:
        """Extract Point A or B coordinate from selected feature or centroid of chosen QGIS layer."""
        cmb = self.cmb_layer_a if target == "A" else self.cmb_layer_b
        layer = cmb.currentLayer()
        if not layer or not layer.isValid():
            QMessageBox.warning(self, "02Route 3D", f"Please choose a valid vector layer for Point {target} in the dropdown.")
            return

        # Check selected features first
        selected = layer.selectedFeatures()
        if selected:
            feat = selected[0]
            feat_desc = f"Selected feature (ID: {feat.id()})"
        else:
            feat = next(layer.getFeatures(), None)
            feat_desc = f"First feature in '{layer.name()}'"

        if not feat or not feat.hasGeometry() or feat.geometry().isEmpty():
            QMessageBox.warning(self, "02Route 3D", f"No valid feature with geometry found in layer '{layer.name()}'.")
            return

        geom = feat.geometry()
        centroid_geom = geom.centroid()
        if centroid_geom.isEmpty():
            QMessageBox.warning(self, "02Route 3D", f"Could not determine centroid for feature in '{layer.name()}'.")
            return

        pt = centroid_geom.asPoint()

        # Transform to WGS84
        crs_layer = layer.crs()
        crs_wgs = QgsCoordinateReferenceSystem("EPSG:4326")
        if crs_layer.isValid() and crs_layer != crs_wgs:
            try:
                transform = QgsCoordinateTransform(crs_layer, crs_wgs, QgsProject.instance())
                if transform.isValid():
                    pt = transform.transform(pt)
            except Exception as e:
                QMessageBox.warning(self, "02Route 3D", f"Coordinate transformation failed: {e}")
                return

        lon = float(pt.x())
        lat = float(pt.y())

        if target == "A":
            self.point_a = Waypoint(lon=lon, lat=lat, name=f"Point A ({layer.name()})")
            self.lbl_coord_a.setText(f"{lon:.5f}, {lat:.5f} ({layer.name()})")
            self.lbl_coord_a.setStyleSheet("font-weight: bold; color: #059669; font-size: 11px;")
        else:
            self.point_b = Waypoint(lon=lon, lat=lat, name=f"Point B ({layer.name()})")
            self.lbl_coord_b.setText(f"{lon:.5f}, {lat:.5f} ({layer.name()})")
            self.lbl_coord_b.setStyleSheet("font-weight: bold; color: #dc2626; font-size: 11px;")

        self.waypoints = [w for w in (self.point_a, self.point_b) if w is not None]
        self._update_point_vector_layers()

        if self.iface:
            self.iface.messageBar().pushSuccess("02Route 3D", f"Point {target} loaded from {feat_desc} ({lon:.5f}, {lat:.5f})!")

    def _update_point_vector_layers(self) -> None:
        """Create or update dedicated Point A and Point B pin vector layers on the QGIS canvas."""
        proj = QgsProject.instance()

        def remove_managed_point_layer(layer_name: str) -> None:
            for layer in list(proj.mapLayersByName(layer_name)):
                if layer.customProperty("zero2route3d/route_point", False) or layer_name in ("📍 Route Point A (Origin)", "🎯 Route Point B (Destination)"):
                    proj.removeMapLayer(layer.id())

        if self.point_a is None:
            remove_managed_point_layer("📍 Route Point A (Origin)")
        if self.point_b is None:
            remove_managed_point_layer("🎯 Route Point B (Destination)")

        # 1. Point A Layer
        if self.point_a is not None:
            layer_a_name = "📍 Route Point A (Origin)"
            layers_a = proj.mapLayersByName(layer_a_name)
            if layers_a:
                layer_a = layers_a[0]
                with contextlib.suppress(Exception):
                    layer_a.startEditing()
                    layer_a.deleteFeatures(layer_a.allFeatureIds())
                    f_a = QgsFeature(layer_a.fields())
                    f_a.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(self.point_a.lon, self.point_a.lat)))
                    f_a.setAttributes(["Point A (Origin)", float(self.point_a.lon), float(self.point_a.lat)])
                    layer_a.addFeatures([f_a])
                    layer_a.commitChanges()
                    layer_a.updateExtents()
                    layer_a.triggerRepaint()
            else:
                layer_a = QgsVectorLayer(
                    "Point?crs=EPSG:4326&field=name:string&field=lon:double&field=lat:double",
                    layer_a_name,
                    "memory",
                )
                layer_a.setCustomProperty("zero2route3d/route_point", "A")
                f_a = QgsFeature(layer_a.fields())
                f_a.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(self.point_a.lon, self.point_a.lat)))
                f_a.setAttributes(["Point A (Origin)", float(self.point_a.lon), float(self.point_a.lat)])
                layer_a.dataProvider().addFeatures([f_a])
                layer_a.updateExtents()
                sym_a = QgsMarkerSymbol.createSimple({
                    "name": "circle",
                    "color": "#059669",
                    "outline_color": "#ffffff",
                    "outline_width": "0.8",
                    "size": "5.0",
                })
                layer_a.setRenderer(QgsSingleSymbolRenderer(sym_a))
                proj.addMapLayer(layer_a)

        # 2. Point B Layer
        if self.point_b is not None:
            layer_b_name = "🎯 Route Point B (Destination)"
            layers_b = proj.mapLayersByName(layer_b_name)
            if layers_b:
                layer_b = layers_b[0]
                with contextlib.suppress(Exception):
                    layer_b.startEditing()
                    layer_b.deleteFeatures(layer_b.allFeatureIds())
                    f_b = QgsFeature(layer_b.fields())
                    f_b.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(self.point_b.lon, self.point_b.lat)))
                    f_b.setAttributes(["Point B (Destination)", float(self.point_b.lon), float(self.point_b.lat)])
                    layer_b.addFeatures([f_b])
                    layer_b.commitChanges()
                    layer_b.updateExtents()
                    layer_b.triggerRepaint()
            else:
                layer_b = QgsVectorLayer(
                    "Point?crs=EPSG:4326&field=name:string&field=lon:double&field=lat:double",
                    layer_b_name,
                    "memory",
                )
                layer_b.setCustomProperty("zero2route3d/route_point", "B")
                f_b = QgsFeature(layer_b.fields())
                f_b.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(self.point_b.lon, self.point_b.lat)))
                f_b.setAttributes(["Point B (Destination)", float(self.point_b.lon), float(self.point_b.lat)])
                layer_b.dataProvider().addFeatures([f_b])
                layer_b.updateExtents()
                sym_b = QgsMarkerSymbol.createSimple({
                    "name": "circle",
                    "color": "#dc2626",
                    "outline_color": "#ffffff",
                    "outline_width": "0.8",
                    "size": "5.0",
                })
                layer_b.setRenderer(QgsSingleSymbolRenderer(sym_b))
                proj.addMapLayer(layer_b)

        if self.canvas:
            self.canvas.refresh()

    def _reverse_waypoints(self) -> None:
        if not self.point_a or not self.point_b:
            if self.iface:
                self.iface.messageBar().pushInfo("02Route 3D", "Please select both Point A and Point B first to swap them.")
            return
        self.point_a, self.point_b = self.point_b, self.point_a
        self.lbl_coord_a.setText(f"{self.point_a.lon:.5f}, {self.point_a.lat:.5f}")
        self.lbl_coord_a.setStyleSheet("font-weight: bold; color: #059669; font-size: 11px;")
        self.lbl_coord_b.setText(f"{self.point_b.lon:.5f}, {self.point_b.lat:.5f}")
        self.lbl_coord_b.setStyleSheet("font-weight: bold; color: #dc2626; font-size: 11px;")
        self.waypoints = [self.point_a, self.point_b]
        self._update_point_vector_layers()

    def _on_profile_changed(self, index: int) -> None:
        key = self.cmb_profile.itemData(index) or "adult"
        p = get_profile(key)
        stairs = "Allowed" if p.stair_allowed else "Prohibited"
        self.lbl_profile_info.setText(f"Speed: {p.base_speed_kmh:.1f} km/h | Max Slope: {p.max_slope_pct:.1f}% | Stairs: {stairs}")

    def _on_mode_scope_changed(self, index: int) -> None:
        """Restrict Focus Profile to the selected mode scope."""
        scope_key = self.cmb_mode_scope.itemData(index) or "single"
        allowed = list_profile_keys() if scope_key == "single" else list_profile_keys_for_group(scope_key)
        if not allowed:
            return
        current_key = self.cmb_profile.currentData()
        selected_key = current_key if current_key in allowed else allowed[0]
        self.cmb_profile.blockSignals(True)
        try:
            self.cmb_profile.clear()
            for key in allowed:
                profile = get_profile(key)
                self.cmb_profile.addItem(profile.name, key)
            target_index = self.cmb_profile.findData(selected_key)
            self.cmb_profile.setCurrentIndex(max(0, target_index))
        finally:
            self.cmb_profile.blockSignals(False)
        self._on_profile_changed(self.cmb_profile.currentIndex())

    def _selected_extra_raster_layers(self) -> List[Any]:
        """Return the current unlimited MCDA raster stack in list-widget order."""
        layers: List[Any] = []
        project = QgsProject.instance()
        user_role = getattr(getattr(Qt, "ItemDataRole", Qt), "UserRole", 32)
        for row in range(self.lst_extra_rasters.count()):
            item = self.lst_extra_rasters.item(row)
            layer_id = item.data(user_role)
            layer = project.mapLayer(str(layer_id)) if layer_id else None
            if layer is not None and layer.isValid():
                layers.append(layer)
        return layers

    def _add_extra_raster_layer(self) -> None:
        """Add the selected raster once; there is intentionally no fixed layer limit."""
        layer = self.cmb_extra_raster.currentLayer()
        if layer is None or not layer.isValid():
            self._show_route_error("Select a valid raster layer before adding it to the MCDA stack.")
            return
        existing_ids = {
            str(self.lst_extra_rasters.item(row).data(getattr(getattr(Qt, "ItemDataRole", Qt), "UserRole", 32)))
            for row in range(self.lst_extra_rasters.count())
        }
        if str(layer.id()) in existing_ids:
            return
        user_role = getattr(getattr(Qt, "ItemDataRole", Qt), "UserRole", 32)
        item = QListWidgetItem(layer.name())
        item.setData(user_role, layer.id())
        self.lst_extra_rasters.addItem(item)

    def _remove_extra_raster_layer(self) -> None:
        row = self.lst_extra_rasters.currentRow()
        if row >= 0:
            self.lst_extra_rasters.takeItem(row)

    def _get_active_bbox(self) -> Tuple[float, float, float, float]:
        """Compute WGS84 bounding box from canvas or waypoints."""
        if self.canvas:
            with contextlib.suppress(Exception):
                extent = self.canvas.extent()
                crs_canvas = self.canvas.mapSettings().destinationCrs()
                crs_wgs = QgsCoordinateReferenceSystem("EPSG:4326")
                if crs_canvas.isValid() and crs_canvas != crs_wgs:
                    transform = QgsCoordinateTransform(crs_canvas, crs_wgs, QgsProject.instance())
                    if transform.isValid():
                        rect = transform.transformBoundingBox(extent)
                        return (rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum())
                elif crs_canvas.isValid() and crs_canvas == crs_wgs:
                    return (extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum())

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
            r_pr.addAttributes([
                QgsField("name", QVariant.String),
                QgsField("highway", QVariant.String),
                QgsField("oneway", QVariant.Int),
            ])
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
            road_layer.setCustomProperty("zero2route3d/source", "osm")
            apply_osm_atlas_style(road_layer)
            QgsProject.instance().addMapLayer(road_layer)
            self.cmb_route_road_layer.setLayer(road_layer)

        # 2. Create Buildings Layer
        if buildings:
            bld_layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "OSM Buildings 3D", "memory")
            b_pr = bld_layer.dataProvider()
            b_pr.addAttributes([
                QgsField("height_m", QVariant.Double),
                QgsField("levels", QVariant.Int),
                QgsField("type", QVariant.String),
            ])
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
            bld_layer.setCustomProperty("zero2route3d/source", "osm")
            apply_osm_atlas_style(bld_layer)
            QgsProject.instance().addMapLayer(bld_layer)
            self.cmb_route_building_layer.setLayer(bld_layer)

        if self.iface:
            self.iface.messageBar().pushSuccess("02Route 3D", f"Acquired {len(roads)} OSM roads and {len(buildings)} building footprints!")

    def _on_fetch_global_dem_clicked(self) -> None:
        """Load official public Copernicus DEM COG tiles for a small real extent."""
        if self.point_a and self.point_b:
            lons = [self.point_a.lon, self.point_b.lon]
            lats = [self.point_a.lat, self.point_b.lat]
            lon_pad = min(0.01, max(0.001, (max(lons) - min(lons)) * 0.25))
            lat_pad = min(0.01, max(0.001, (max(lats) - min(lats)) * 0.25))
            bbox = (min(lons) - lon_pad, min(lats) - lat_pad, max(lons) + lon_pad, max(lats) + lat_pad)
        else:
            bbox = self._get_active_bbox()

        try:
            tile_specs = CopernicusDemTileSource.tiles_for_bbox(bbox, max_tiles=16)
        except CopernicusDemError as exc:
            message = f"Copernicus DEM request was not started: {exc}"
            if self.iface:
                self.iface.messageBar().pushWarning("02Route 3D", message)
            else:
                QMessageBox.warning(self, "02Route 3D", message)
            return

        existing_urls = {
            str(layer.customProperty("zero2route3d/copernicus_url", ""))
            for layer in QgsProject.instance().mapLayers().values()
        }
        loaded_layers: List[Any] = []
        failed_tiles: List[str] = []
        for lon_index, lat_index, url in tile_specs:
            if url in existing_urls:
                for layer in QgsProject.instance().mapLayers().values():
                    if layer.customProperty("zero2route3d/copernicus_url", "") == url:
                        loaded_layers.append(layer)
                        break
                continue
            tile_name = CopernicusDemTileSource.tile_id(lon_index + 0.1, lat_index + 0.1)
            layer = QgsRasterLayer(f"/vsicurl/{url}", f"Copernicus GLO-30 {tile_name}", "gdal")
            if not layer.isValid():
                failed_tiles.append(tile_name)
                continue
            layer.setCustomProperty("zero2route3d/copernicus_url", url)
            layer.setCustomProperty("zero2route3d/copernicus_dem", True)
            QgsProject.instance().addMapLayer(layer)
            loaded_layers.append(layer)

        if loaded_layers:
            self.copernicus_dem_layers = loaded_layers
            with contextlib.suppress(Exception):
                self.cmb_dem_layer.setLayer(loaded_layers[0])

        if failed_tiles:
            message = f"Copernicus DEM loaded {len(loaded_layers)} real tile(s); failed: {', '.join(failed_tiles)}."
            if self.iface:
                self.iface.messageBar().pushWarning("02Route 3D", message)
            else:
                QMessageBox.warning(self, "02Route 3D", message)
        elif loaded_layers:
            message = f"Loaded {len(loaded_layers)} verified Copernicus GLO-30 tile(s) into QGIS."
            if self.iface:
                self.iface.messageBar().pushSuccess("02Route 3D", message)
            else:
                QMessageBox.information(self, "02Route 3D", message)
        else:
            message = "Copernicus returned no usable raster tile; no DEM layer was created."
            if self.iface:
                self.iface.messageBar().pushCritical("02Route 3D", message)
            else:
                QMessageBox.critical(self, "02Route 3D", message)

    def compute_route(self) -> None:
        """Compute 3D shortest path(s) for active profile or multi-profile groups, output unified QGIS layer, and load canvas animation."""
        if not self.point_a or not self.point_b:
            msg = "Please select both Point A (Origin) and Point B (Destination) first using 'Pick on Map' or 'Use Layer'."
            if self.iface:
                self.iface.messageBar().pushWarning("02Route 3D", msg)
            else:
                QMessageBox.warning(self, "02Route 3D", msg)
            return

        self.waypoints = [self.point_a, self.point_b]

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(15)

        # Bounding box calculation scaled to route extent
        lons = [w.lon for w in self.waypoints]
        lats = [w.lat for w in self.waypoints]
        d_lon = max(0.001, max(lons) - min(lons))
        d_lat = max(0.001, max(lats) - min(lats))
        buf_lon = min(0.006, max(0.0025, d_lon * 0.35))
        buf_lat = min(0.006, max(0.0025, d_lat * 0.35))
        bbox = (min(lons) - buf_lon, min(lats) - buf_lat, max(lons) + buf_lon, max(lats) + buf_lat)

        self.progress_bar.setValue(35)

        # Environmental raster sampling
        weights = MCDAWeights(
            weight_slope=self.sld_slope.value() / 100.0,
            weight_heat=self.sld_heat.value() / 100.0,
            weight_green=self.sld_green.value() / 100.0,
        )
        sampler = EnvironmentalSurfaceSampler(
            dem_layer=self.cmb_dem_layer.currentLayer(),
            dem_layers=self.copernicus_dem_layers,
            lst_layer=self.cmb_lst_layer.currentLayer(),
            green_layer=self.cmb_green_layer.currentLayer(),
            additional_layers=self._selected_extra_raster_layers(),
            weights=weights,
        )

        engine = RoutingEngine3D(sampler=sampler, weights=weights)
        selected_road_layer = self.cmb_route_road_layer.currentLayer()
        selected_building_layer = self.cmb_route_building_layer.currentLayer()
        for source_layer in (selected_road_layer, selected_building_layer):
            if source_layer is not None and source_layer.isValid():
                apply_osm_atlas_style(source_layer)
        try:
            segments = self.network_manager.require_segments(
                vector_layer=selected_road_layer if selected_road_layer and selected_road_layer.isValid() else None,
                bbox=bbox,
            )
        except NetworkSourceError as exc:
            self.progress_bar.setVisible(False)
            self._show_route_error(str(exc))
            return
        try:
            engine.build_graph(segments)
        except Exception as exc:
            self.progress_bar.setVisible(False)
            self._show_route_error(f"Could not build the route network: {exc}")
            return

        if not engine.nodes:
            self.progress_bar.setVisible(False)
            self._show_route_error("The selected extent contains no usable network nodes.")
            return

        self.progress_bar.setValue(55)

        # Determine which profile keys to calculate
        scope_key = self.cmb_mode_scope.currentData() or "single"
        if scope_key == "single":
            target_keys = [self.cmb_profile.currentData() or "adult"]
        else:
            target_keys = list_profile_keys_for_group(scope_key)

        self.multi_route_results = {}
        for p_key in target_keys:
            try:
                res = engine.calculate_route(self.waypoints, profile_key=p_key, optimize_tsp=False)
            except Exception as exc:
                self.progress_bar.setVisible(False)
                self._show_route_error(f"Route calculation failed for {p_key}: {exc}")
                return
            self.multi_route_results[p_key] = res

        primary_key = self.cmb_profile.currentData() or target_keys[0]
        if primary_key not in self.multi_route_results:
            primary_key = target_keys[0]
        result = self.multi_route_results[primary_key]
        self.current_route_result = result

        if not result.coordinates_3d:
            self.progress_bar.setVisible(False)
            self._show_route_error(result.status_message or "No route could be found between the selected points.")
            return

        self.progress_bar.setValue(80)

        # Update KPIs
        self._update_kpi_display(result)

        # Cue Sheet
        self.cue_widget.load_cues(result.statistics.cue_sheet)

        # Populate Multi-Profile Comparison Table
        self._update_comparison_table()

        # Enable Export Buttons & Canvas Animator Controls
        self.btn_add_layer.setEnabled(True)
        self.btn_export_gpx.setEnabled(True)
        self.btn_export_geojson.setEnabled(True)
        self.btn_export_html.setEnabled(True)
        self.btn_export_dxf.setEnabled(True)
        self.btn_anim_play.setText("▶️ Play")
        self.btn_anim_play.setEnabled(True)
        self.btn_anim_stop.setEnabled(True)
        self.sld_anim_progress.setEnabled(True)

        # Load 2D canvas animator with all calculated routes
        self.canvas_animator.load_routes(list(self.multi_route_results.values()))

        # 50m Linear Corridor Building Filter & 3D WebGL data sync
        if selected_building_layer is not None and selected_building_layer.isValid():
            self.cached_osm_buildings = self._extract_buildings_from_layer(selected_building_layer)
        geojson_data = self._build_web_route_payload(result)

        with contextlib.suppress(Exception):
            self.current_route_file.write_text(json.dumps(geojson_data, indent=2), encoding="utf-8")

        # Automatically add the unified categorized multi-profile route layer to QGIS
        self.add_route_layer_to_qgis()

        self.progress_bar.setValue(100)
        self.progress_bar.setVisible(False)
        self.route_calculated.emit(result)
        if self.iface:
            self.iface.messageBar().pushSuccess(
                "02Route 3D",
                f"Calculated {len(self.multi_route_results)} profile route(s)! 2D Canvas Animation & unified layer ready.",
            )

    def _show_route_error(self, message: str) -> None:
        """Present a concise actionable route error without exposing a traceback."""
        if self.iface:
            self.iface.messageBar().pushCritical("02Route 3D", message)
        else:
            QMessageBox.critical(self, "02Route 3D", message)

    def _update_kpi_display(self, result: RouteResult3D) -> None:
        self.kpi_dist.setText(f"{result.statistics.total_distance_km} km")
        self.kpi_time.setText(f"{result.statistics.total_duration_min} min")
        self.kpi_climb.setText(f"+{result.statistics.elevation_gain_m:.1f} m")
        self.kpi_slope.setText(f"{result.statistics.max_slope_pct:.1f}%")
        self.kpi_kcal.setText(f"{result.statistics.total_calories_kcal:.0f} kcal")

    def _update_comparison_table(self) -> None:
        if not self.multi_route_results:
            self.table_comparison.setRowCount(0)
            return

        self.table_comparison.setRowCount(len(self.multi_route_results))
        for row, (key, res) in enumerate(self.multi_route_results.items()):
            icon = "🚗" if res.profile.category == "vehicle" else ("🚲" if res.profile.category == "micromobility" else ("♿" if key in ("wheelchair", "stroller") else "🚶"))
            item_name = QTableWidgetItem(f"{icon} {res.profile.name}")
            item_cat = QTableWidgetItem(res.profile.category.capitalize())
            item_dist = QTableWidgetItem(f"{res.statistics.total_distance_km:.2f} km")
            item_time = QTableWidgetItem(f"{res.statistics.total_duration_min:.1f} min")
            item_climb = QTableWidgetItem(f"+{res.statistics.elevation_gain_m:.1f} m")
            item_slope = QTableWidgetItem(f"{res.statistics.max_slope_pct:.1f}%")
            item_status = QTableWidgetItem(f"{res.statistics.total_calories_kcal:.0f} kcal" if res.profile.category != "vehicle" else f"{res.statistics.total_duration_min * 1.2:.1f} min (Drive)")

            _ItemIsEditable = getattr(getattr(Qt, "ItemFlag", Qt), "ItemIsEditable", 1)
            for it in (item_name, item_cat, item_dist, item_time, item_climb, item_slope, item_status):
                it.setFlags(it.flags() & ~_ItemIsEditable)

            self.table_comparison.setItem(row, 0, item_name)
            self.table_comparison.setItem(row, 1, item_cat)
            self.table_comparison.setItem(row, 2, item_dist)
            self.table_comparison.setItem(row, 3, item_time)
            self.table_comparison.setItem(row, 4, item_climb)
            self.table_comparison.setItem(row, 5, item_slope)
            self.table_comparison.setItem(row, 6, item_status)

    def _on_comparison_row_clicked(self, row: int, _col: int) -> None:
        keys = list(self.multi_route_results.keys())
        if 0 <= row < len(keys):
            key = keys[row]
            res = self.multi_route_results[key]
            self.current_route_result = res
            self._update_kpi_display(res)
            self.cue_widget.load_cues(res.statistics.cue_sheet)

    def _on_anim_frame_updated(self, current_time_s: float, max_time_s: float, progress: float) -> None:
        self.sld_anim_progress.blockSignals(True)
        self.sld_anim_progress.setValue(int(progress * 1000))
        self.sld_anim_progress.blockSignals(False)

        cur_min, cur_sec = divmod(int(current_time_s), 60)
        tot_min, tot_sec = divmod(int(max_time_s), 60)
        pct = int(progress * 100)
        mode_count = len(self.canvas_animator.avatars)
        self.lbl_anim_status.setText(f"{cur_min:02d}:{cur_sec:02d} / {tot_min:02d}:{tot_sec:02d} ({pct}%) | 🚗 {mode_count} Modes Active")

    def _on_anim_state_changed(self, is_playing: bool) -> None:
        self.btn_anim_play.setText("⏸️ Pause" if is_playing else "▶️ Play")
        self.btn_anim_play.setEnabled(bool(self.canvas_animator.avatars))

    def _on_anim_slider_moved(self, val: int) -> None:
        fraction = val / 1000.0
        self.canvas_animator.seek_progress(fraction)

    def _on_anim_speed_changed(self, _index: int) -> None:
        speed_str = self.cmb_anim_speed.currentText().replace("x", "")
        with contextlib.suppress(ValueError):
            self.canvas_animator.set_speed_multiplier(float(speed_str))

    def open_3d_studio(self) -> None:
        """Start local HTTP server and launch the 3D WebGL studio with 50m building corridor in browser."""
        if self.current_route_result is not None or self.multi_route_results:
            geojson_data = self._build_web_route_payload(self.current_route_result)
            with contextlib.suppress(Exception):
                self.current_route_file.write_text(json.dumps(geojson_data, indent=2), encoding="utf-8")
        server_url = self.local_server.start()
        QDesktopServices.openUrl(QUrl(server_url))

    def _open_profile_editor(self) -> None:
        key = self.cmb_profile.currentData() or "adult"
        prof = get_profile(key)
        dialog = ProfileEditorDialog(profile=prof, parent=self)
        if dialog.exec_():
            updated = dialog.get_updated_profile()
            PROFILES[updated.key] = updated
            existing_idx = self.cmb_profile.findData(updated.key)
            if existing_idx >= 0:
                self.cmb_profile.setItemText(existing_idx, updated.name)
                self.cmb_profile.setCurrentIndex(existing_idx)
            else:
                self.cmb_profile.addItem(updated.name, updated.key)
                self.cmb_profile.setCurrentIndex(self.cmb_profile.count() - 1)
            self._on_mode_scope_changed(self.cmb_mode_scope.currentIndex())
            self._on_profile_changed(self.cmb_profile.currentIndex())

    def _compute_od_matrix(self) -> None:
        if len(self.waypoints) < 2:
            return
        sampler = EnvironmentalSurfaceSampler(
            dem_layer=self.cmb_dem_layer.currentLayer(),
            dem_layers=self.copernicus_dem_layers,
            lst_layer=self.cmb_lst_layer.currentLayer(),
            green_layer=self.cmb_green_layer.currentLayer(),
            additional_layers=self._selected_extra_raster_layers(),
        )
        engine = RoutingEngine3D(sampler=sampler)
        lons = [w.lon for w in self.waypoints]
        lats = [w.lat for w in self.waypoints]
        bbox = (min(lons), min(lats), max(lons), max(lats))
        selected_road_layer = self.cmb_route_road_layer.currentLayer()
        try:
            segments = self.network_manager.require_segments(
                vector_layer=selected_road_layer if selected_road_layer and selected_road_layer.isValid() else None,
                bbox=bbox,
            )
        except NetworkSourceError as exc:
            self._show_route_error(str(exc))
            return
        engine.build_graph(segments)

        rows = engine.calculate_od_matrix(self.waypoints, self.waypoints, profile_key=self.cmb_profile.currentData() or "adult")
        self.table_od.setRowCount(len(rows))
        for idx, r in enumerate(rows):
            self.table_od.setItem(idx, 0, QTableWidgetItem(r["origin_name"]))
            self.table_od.setItem(idx, 1, QTableWidgetItem(r["dest_name"]))
            self.table_od.setItem(idx, 2, QTableWidgetItem(f"{r['distance_km']:.2f}"))
            self.table_od.setItem(idx, 3, QTableWidgetItem(f"{r['duration_min']:.1f}"))
            self.table_od.setItem(idx, 4, QTableWidgetItem(f"{r['climb_m']:.1f}"))

    def _create_surface_sampler(self, weights: Optional[MCDAWeights] = None) -> EnvironmentalSurfaceSampler:
        """Create surface sampler from active dock raster layers and MCDA weights."""
        mcda_w = weights or MCDAWeights(
            weight_slope=self.sld_slope.value() / 100.0 if hasattr(self, "sld_slope") else 1.0,
            weight_heat=self.sld_heat.value() / 100.0 if hasattr(self, "sld_heat") else 0.5,
            weight_green=self.sld_green.value() / 100.0 if hasattr(self, "sld_green") else 0.5,
        )
        return EnvironmentalSurfaceSampler(
            dem_layer=self.cmb_dem_layer.currentLayer() if hasattr(self, "cmb_dem_layer") else None,
            dem_layers=getattr(self, "copernicus_dem_layers", []),
            lst_layer=self.cmb_lst_layer.currentLayer() if hasattr(self, "cmb_lst_layer") else None,
            green_layer=self.cmb_green_layer.currentLayer() if hasattr(self, "cmb_green_layer") else None,
            additional_layers=self._selected_extra_raster_layers() if hasattr(self, "_selected_extra_raster_layers") else [],
            weights=mcda_w,
        )

    def _build_web_route_payload(self, primary_result: Optional[RouteResult3D] = None) -> Dict[str, Any]:
        """Build the shared multi-profile payload consumed by QGIS and Web Guide."""
        primary = primary_result or self.current_route_result
        route_results = list(self.multi_route_results.values()) or ([primary] if primary else [])
        if not route_results:
            return {"type": "FeatureCollection", "features": [], "properties": {"route_count": 0}}

        routes_coords = [r.coordinates_3d for r in route_results if r and r.coordinates_3d]
        if not routes_coords and primary and primary.coordinates_3d:
            routes_coords = [primary.coordinates_3d]

        sampler = self._create_surface_sampler()
        corridor_blds, corridor_trees = filter_corridor_assets_multi_route(
            routes_coords,
            self.cached_osm_buildings,
            buffer_meters=30.0,
            green_sampler=sampler,
        )

        features = []
        for route_result in route_results:
            feature = route_result.to_geojson_feature()
            feature["properties"]["corridor_buildings"] = corridor_blds
            feature["properties"]["corridor_trees"] = corridor_trees
            features.append(feature)
        return {
            "type": "FeatureCollection",
            "features": features,
            "properties": {
                "primary_profile_key": primary.profile.key if primary else features[0]["properties"].get("profile_key"),
                "route_count": len(features),
                "source": "02Route 3D QGIS multi-profile result",
                "corridor_buildings": corridor_blds,
                "corridor_trees": corridor_trees,
            },
        }

    def add_route_layer_to_qgis(self) -> None:
        if not self.multi_route_results and not self.current_route_result:
            return

        proj = QgsProject.instance()
        layer_name = "🛣️ 02Route 3D - Multi-Profile Routes"
        layers = proj.mapLayersByName(layer_name)

        if layers:
            layer = layers[0]
        else:
            layer = QgsVectorLayer(
                "LineStringZ?crs=EPSG:4326&field=mode_key:string&field=mode_name:string&field=category:string"
                "&field=dist_km:double&field=dist_m:double&field=time_min:double&field=climb_m:double"
                "&field=loss_m:double&field=max_slope:double&field=avg_slope:double&field=calories:double"
                "&field=ada_ok:int&field=status:string",
                layer_name,
                "memory",
            )
            layer.setCustomProperty("zero2route3d/route_layer", True)
            self._managed_route_layer_ids.add(layer.id())
            if layer.fields().isEmpty():
                pr = layer.dataProvider()
                pr.addAttributes([
                    QgsField("mode_key", QVariant.String),
                    QgsField("mode_name", QVariant.String),
                    QgsField("category", QVariant.String),
                    QgsField("dist_km", QVariant.Double),
                    QgsField("dist_m", QVariant.Double),
                    QgsField("time_min", QVariant.Double),
                    QgsField("climb_m", QVariant.Double),
                    QgsField("loss_m", QVariant.Double),
                    QgsField("max_slope", QVariant.Double),
                    QgsField("avg_slope", QVariant.Double),
                    QgsField("calories", QVariant.Double),
                    QgsField("ada_ok", QVariant.Int),
                    QgsField("status", QVariant.String),
                ])
                layer.updateFields()
        layer.setCustomProperty("zero2route3d/route_layer", True)
        self._managed_route_layer_ids.add(layer.id())

        feats = []
        routes_to_add = list(self.multi_route_results.values()) if self.multi_route_results else [self.current_route_result]

        for r in routes_to_add:
            if not r or not r.coordinates_3d:
                continue
            pts = [QgsPoint(c[0], c[1], c[2]) for c in r.coordinates_3d]
            geom = QgsGeometry.fromPolyline(pts)
            feat = QgsFeature(layer.fields())
            feat.setGeometry(geom)
            feat.setAttributes([
                r.profile.key,
                r.profile.name,
                r.profile.category,
                r.statistics.total_distance_km,
                r.statistics.total_distance_m,
                r.statistics.total_duration_min,
                r.statistics.elevation_gain_m,
                r.statistics.elevation_loss_m,
                r.statistics.max_slope_pct,
                r.statistics.avg_slope_pct,
                r.statistics.total_calories_kcal,
                1 if r.statistics.ada_compliant else 0,
                r.status_message,
            ])
            feats.append(feat)

        active_keys = [r.profile.key for r in routes_to_add if r and r.profile]

        if layers:
            with contextlib.suppress(Exception):
                layer.startEditing()
                layer.deleteFeatures(layer.allFeatureIds())
                layer.addFeatures(feats)
                layer.commitChanges()
            layer.updateExtents()
            apply_multiprofile_categorized_renderer(layer, field_name="mode_key", active_keys=active_keys)
            layer.triggerRepaint()
        else:
            pr = layer.dataProvider()
            pr.addFeatures(feats)
            layer.updateExtents()
            apply_multiprofile_categorized_renderer(layer, field_name="mode_key", active_keys=active_keys)
            proj.addMapLayer(layer)

        if self.iface:
            self.iface.messageBar().pushSuccess("02Route 3D", f"Added '{layer.name()}' ({len(feats)} modes) to project.")

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
            geojson_data = self._build_web_route_payload(self.current_route_result)
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
            bundler.export_standalone_html(self._build_web_route_payload(self.current_route_result), Path(path))
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

    def _remove_transient_project_layers(self) -> None:
        """Remove only layers created by this plugin's route/animation session."""
        project = QgsProject.instance()
        transient_ids = set(self._managed_route_layer_ids)
        with contextlib.suppress(Exception):
            if self.canvas_animator.avatar_layer is not None:
                transient_ids.add(self.canvas_animator.avatar_layer.id())
        for layer in project.mapLayers().values():
            if layer.customProperty("zero2route3d/route_point", False) or layer.customProperty("zero2route3d/route_layer", False) or layer.customProperty("zero2route3d/animated_avatar_layer", False):
                transient_ids.add(layer.id())
        for layer_id in transient_ids:
            with contextlib.suppress(Exception):
                project.removeMapLayer(layer_id)
        self._managed_route_layer_ids.clear()

    def teardown(self) -> None:
        """Clean up active tools, canvas animator markers, and background server."""
        if self.active_tool is not None and self.canvas is not None:
            with contextlib.suppress(Exception):
                self.canvas.unsetMapTool(self.active_tool)
                self.active_tool.deactivate()
            self.active_tool = None

        if hasattr(self, "btn_pick_a") and self.btn_pick_a:
            self.btn_pick_a.setChecked(False)
        if hasattr(self, "btn_pick_b") and self.btn_pick_b:
            self.btn_pick_b.setChecked(False)

        with contextlib.suppress(Exception):
            QgsProject.instance().layersWillBeRemoved.disconnect(self._on_project_layers_removed)
        self._clear_animation_state()
        self._remove_transient_project_layers()

        if hasattr(self, "local_server") and self.local_server:
            with contextlib.suppress(Exception):
                self.local_server.stop()

        with contextlib.suppress(OSError):
            self.current_route_file.unlink()

    def closeEvent(self, event: Any) -> None:
        self.teardown()
        super().closeEvent(event)
