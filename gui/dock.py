"""Unified 5-Tab Studio DockWidget for 02Route 3D."""
from __future__ import annotations

import contextlib
import datetime
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from qgis.PyQt.QtCore import Qt, QUrl, QVariant
from qgis.PyQt.QtGui import QColor, QDesktopServices, QFont
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
    QgsFontMarkerSymbolLayer,
    QgsGeometry,
    QgsMapLayerProxyModel,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsPoint,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsSimpleMarkerSymbolLayer,
    QgsSingleSymbolRenderer,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.gui import QgsMapCanvas, QgsMapLayerComboBox

from ..core.basemap import add_osm_basemap
from .dock_state import (
    apply_inputs,
    apply_layers,
    collect_inputs,
    collect_layers,
    restore_project_layers,
    restore_settings,
    save_settings,
)
from .tasks import FunctionTask, TaskContext, cancel_tasks, snapshot, start_task
from ..core.copernicus_eo_suite import (
    CorridorElevationSuite,
    apply_environmental_raster_symbology,
    plan_dem_grid,
)
from ..core.dem_fetcher import GlobalDemFetcher
from ..core.environmental_raster import EnvironmentalSurfaceSampler, MCDAWeights
from ..core.mobility_profiles import (
    PROFILES,
    get_profile,
    get_profile_color,
    list_profile_keys,
    list_profile_keys_for_group,
)
from ..core.network_source import NetworkSourceError, NetworkSourceManager
from ..core.osm_downloader import OsmBuilding, OsmDataFetcher, OsmPark, OsmTree
from ..core.osm_styling import (
    apply_osm_atlas_style,
    apply_osm_theme_style,
    list_osm_themes,
)
from ..core.qml_generator import apply_multiprofile_categorized_renderer
from ..core.route_corridor_3d import filter_corridor_assets_multi_route
from ..core.routing_engine import RouteResult3D, RoutingEngine3D, Waypoint
from ..core.scenario_io import (
    ScenarioError,
    build_scenario,
    compare_runs,
    load_scenario,
    save_scenario,
    summarize_statistics,
)
from .canvas_animator import Route2DCanvasAnimator
from .cue_sheet_widget import CueSheetWidget
from .map_tools import RoutePointMapTool
from .multi_metric_panel import MultiMetricPanel
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


def _create_route_point_marker_symbol(letter: str, main_color_hex: str, ring_color_hex: str) -> QgsMarkerSymbol:
    """Create a high-visibility geolocator pin marker with embedded letter A or B."""
    sym = QgsMarkerSymbol()

    # 1. Outer halo / accent drop ring
    outer = QgsSimpleMarkerSymbolLayer()
    outer.setShape(QgsSimpleMarkerSymbolLayer.Circle)
    outer.setSize(8.8)
    outer.setColor(QColor(ring_color_hex))
    outer.setStrokeColor(QColor("#ffffff"))
    outer.setStrokeWidth(0.6)
    sym.changeSymbolLayer(0, outer)

    # 2. Main circular pin badge
    inner = QgsSimpleMarkerSymbolLayer()
    inner.setShape(QgsSimpleMarkerSymbolLayer.Circle)
    inner.setSize(7.0)
    inner.setColor(QColor(main_color_hex))
    inner.setStrokeColor(QColor("#ffffff"))
    inner.setStrokeWidth(0.8)
    sym.appendSymbolLayer(inner)

    # 3. Bold Centered Letter A / B
    font_layer = QgsFontMarkerSymbolLayer("Arial", letter, 4.0)
    font_layer.setColor(QColor("#ffffff"))
    sym.appendSymbolLayer(font_layer)

    return sym


def _apply_route_point_labeling(layer: QgsVectorLayer, label_text: str, text_color_hex: str) -> None:
    """Apply crisp high-contrast text labeling with white halo buffer."""
    settings = QgsPalLayerSettings()
    settings.fieldName = f"'{label_text}'"
    settings.isExpression = True

    tf = QgsTextFormat()
    tf.setSize(9.5)
    tf.setColor(QColor(text_color_hex))
    font = QFont("Segoe UI", 9)
    font.setBold(True)
    tf.setFont(font)

    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(1.2)
    buf.setColor(QColor("#ffffff"))
    tf.setBuffer(buf)
    settings.setFormat(tf)

    settings.placement = getattr(QgsPalLayerSettings, "AroundPoint", 0)
    settings.dist = 4.0

    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)



# Elevation downloads above this many requests ask for confirmation first.
DEM_CONFIRM_REQUESTS = 40

class Route3DStudioDock(QDockWidget):
    """Next-generation 3D Mobility & Route Planning Studio Dock."""

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
        self.cached_osm_trees: List[OsmTree] = []
        self.cached_osm_parks: List[OsmPark] = []
        self._managed_route_layer_ids: set[str] = set()
        self._handling_layer_removal = False
        self._run_counter: int = 0

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
        # Inputs from the last session; layer choices from the project file.
        restore_settings(self)
        QgsProject.instance().readProject.connect(self._on_project_read)
        QgsProject.instance().writeProject.connect(self._on_project_write)

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self.root_widget)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # -------------------------------------------------------------
        # 5-Tab Multi-Studio Panel (Quick Mode First)
        # -------------------------------------------------------------
        self.tab_widget = QTabWidget(self.root_widget)
        self.tab_widget.setObjectName("route3dTabs")

        # Tab 1: Quick Mode
        self.tab_quick = QWidget()
        self._build_tab_quick(self.tab_quick)
        self.tab_widget.addTab(self.tab_quick, "⚡ Quick Mode")

        # Tab 2: Advanced Lab
        self.tab_advanced = QWidget()
        self._build_tab_advanced(self.tab_advanced)
        self.tab_widget.addTab(self.tab_advanced, "🔬 Advanced Lab")

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

    def _build_tab_quick(self, parent: QWidget) -> None:
        scroll = QScrollArea(parent)
        scroll.setWidgetResizable(True)
        _ScrollBarAlwaysOff = getattr(getattr(Qt, "ScrollBarPolicy", Qt), "ScrollBarAlwaysOff", 1)
        scroll.setHorizontalScrollBarPolicy(_ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        # Quick Banner Card
        card_header = QFrame()
        card_header.setProperty("class", "route3dCard")
        h_box = QVBoxLayout(card_header)
        h_title = QLabel("<b>⚡ Quick 3D Route Studio</b>")
        h_title.setStyleSheet("font-size: 13px; font-weight: bold; color: #0284c7;")
        h_sub = QLabel("1-Click Instant 3D Route Planning & Simulation. Pick 2 points on the map, choose your transport mode, and launch.")
        h_sub.setStyleSheet("color: #64748b; font-size: 11px;")
        h_sub.setWordWrap(True)
        h_box.addWidget(h_title)
        h_box.addWidget(h_sub)
        layout.addWidget(card_header)

        # 1. Point A & Point B Pickers Card
        card_ab = QFrame()
        card_ab.setProperty("class", "route3dCard")
        ab_layout = QVBoxLayout(card_ab)
        ab_layout.addWidget(QLabel("<b>1. Route Points (A ➔ B)</b>"))

        # Point A
        row_a = QHBoxLayout()
        row_a.addWidget(QLabel("📍 <b>Origin (A):</b>"))
        self.lbl_quick_coord_a = QLabel("Not selected")
        self.lbl_quick_coord_a.setStyleSheet("font-style: italic; color: #64748b; font-size: 11px;")
        row_a.addWidget(self.lbl_quick_coord_a, 1)
        self.btn_quick_pick_a = QPushButton("📍 Pick on Map")
        self.btn_quick_pick_a.setCheckable(True)
        self.btn_quick_pick_a.clicked.connect(lambda chk: self._toggle_specific_picker("A", chk))
        row_a.addWidget(self.btn_quick_pick_a)
        btn_quick_clear_a = QPushButton("Clear")
        btn_quick_clear_a.clicked.connect(lambda: self._clear_route_point("A"))
        row_a.addWidget(btn_quick_clear_a)
        ab_layout.addLayout(row_a)

        # Point B
        row_b = QHBoxLayout()
        row_b.addWidget(QLabel("🎯 <b>Dest (B):</b>"))
        self.lbl_quick_coord_b = QLabel("Not selected")
        self.lbl_quick_coord_b.setStyleSheet("font-style: italic; color: #64748b; font-size: 11px;")
        row_b.addWidget(self.lbl_quick_coord_b, 1)
        self.btn_quick_pick_b = QPushButton("🎯 Pick on Map")
        self.btn_quick_pick_b.setCheckable(True)
        self.btn_quick_pick_b.clicked.connect(lambda chk: self._toggle_specific_picker("B", chk))
        row_b.addWidget(self.btn_quick_pick_b)
        btn_quick_clear_b = QPushButton("Clear")
        btn_quick_clear_b.clicked.connect(lambda: self._clear_route_point("B"))
        row_b.addWidget(btn_quick_clear_b)
        ab_layout.addLayout(row_b)

        # Action row
        act_pts_row = QHBoxLayout()
        btn_swap = QPushButton("⇄ Swap A ↔ B")
        btn_swap.clicked.connect(self._reverse_waypoints)
        act_pts_row.addWidget(btn_swap)
        btn_clear_all = QPushButton("🗑️ Clear Points")
        btn_clear_all.clicked.connect(self._reset_route_session)
        act_pts_row.addWidget(btn_clear_all)
        ab_layout.addLayout(act_pts_row)
        layout.addWidget(card_ab)

        # 2. Mode Scope & Focus Profile Card
        card_prof = QFrame()
        card_prof.setProperty("class", "route3dCard")
        prof_layout = QVBoxLayout(card_prof)
        prof_layout.addWidget(QLabel("<b>2. Mode Scope & Transport Profile</b>"))

        scope_row = QHBoxLayout()
        scope_row.addWidget(QLabel("Mode Scope:"))
        self.cmb_quick_mode_scope = QComboBox()
        self.cmb_quick_mode_scope.addItem("🔘 Single Profile Only", "single")
        self.cmb_quick_mode_scope.addItem("🌐 All 15 Profiles (Full Suite)", "all")
        self.cmb_quick_mode_scope.addItem("🚶 Pedestrian Modes (6 Profiles)", "pedestrian")
        self.cmb_quick_mode_scope.addItem("♿ Accessibility Modes (2 Profiles)", "accessibility")
        self.cmb_quick_mode_scope.addItem("🚲 Micromobility Modes (3 Profiles)", "micromobility")
        self.cmb_quick_mode_scope.addItem("🚗 Vehicle Modes (4 Profiles)", "vehicle")
        scope_row.addWidget(self.cmb_quick_mode_scope)
        self.cmb_quick_mode_scope.currentIndexChanged.connect(self._on_quick_mode_scope_changed)
        prof_layout.addLayout(scope_row)

        prof_row = QHBoxLayout()
        prof_row.addWidget(QLabel("Focus Profile:"))
        self.cmb_quick_profile = QComboBox()
        for key in list_profile_keys():
            p = get_profile(key)
            self.cmb_quick_profile.addItem(p.name, key)
        self.cmb_quick_profile.currentIndexChanged.connect(self._on_quick_profile_changed)
        prof_row.addWidget(self.cmb_quick_profile)
        prof_layout.addLayout(prof_row)

        self.lbl_quick_profile_info = QLabel("Select a mobility profile to see its speed, gradient limit and stair policy.")
        self.lbl_quick_profile_info.setProperty("class", "route3dBadgeInfo")
        prof_layout.addWidget(self.lbl_quick_profile_info)
        layout.addWidget(card_prof)

        # 3. Quick Action Launch Card
        card_act = QFrame()
        card_act.setProperty("class", "route3dCard")
        act_layout = QVBoxLayout(card_act)

        self.btn_quick_compute = QPushButton("🚀 Quick Compute Route & 3D Studio")
        self.btn_quick_compute.setObjectName("primaryButton")
        self.btn_quick_compute.setStyleSheet("""
            QPushButton#primaryButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284c7, stop:1 #2563eb);
                color: #ffffff;
                font-weight: 800;
                font-size: 13px;
                padding: 12px;
                border-radius: 6px;
            }
            QPushButton#primaryButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0369a1, stop:1 #1d4ed8);
            }
        """)
        self.btn_quick_compute.clicked.connect(self.quick_compute_route)
        act_layout.addWidget(self.btn_quick_compute)

        self.quick_progress_bar = QProgressBar()
        self.quick_progress_bar.setVisible(False)
        act_layout.addWidget(self.quick_progress_bar)

        # Quick KPIs
        kpi_grid = QGridLayout()
        self.quick_kpi_dist = QLabel("—")
        self.quick_kpi_time = QLabel("—")
        self.quick_kpi_climb = QLabel("—")
        self.quick_kpi_slope = QLabel("—")
        self.quick_kpi_kcal = QLabel("—")

        for lbl in (self.quick_kpi_dist, self.quick_kpi_time, self.quick_kpi_climb, self.quick_kpi_slope, self.quick_kpi_kcal):
            lbl.setProperty("class", "route3dKpi")

        kpi_grid.addWidget(QLabel("Distance:"), 0, 0)
        kpi_grid.addWidget(self.quick_kpi_dist, 0, 1)
        kpi_grid.addWidget(QLabel("Duration:"), 0, 2)
        kpi_grid.addWidget(self.quick_kpi_time, 0, 3)
        kpi_grid.addWidget(QLabel("Climb:"), 1, 0)
        kpi_grid.addWidget(self.quick_kpi_climb, 1, 1)
        kpi_grid.addWidget(QLabel("Max Slope:"), 1, 2)
        kpi_grid.addWidget(self.quick_kpi_slope, 1, 3)
        kpi_grid.addWidget(QLabel("Calories:"), 2, 0)
        kpi_grid.addWidget(self.quick_kpi_kcal, 2, 1)
        act_layout.addLayout(kpi_grid)
        layout.addWidget(card_act)

        # 4. Canvas Playback & 3D Web Studio Card
        card_play = QFrame()
        card_play.setProperty("class", "route3dCard")
        play_layout = QVBoxLayout(card_play)
        play_layout.addWidget(QLabel("<b>3. Canvas Animation & 3D WebGL Studio</b>"))

        self.quick_multi_metric_panel = MultiMetricPanel(card_play)
        self.quick_multi_metric_panel.seek_requested.connect(self.canvas_animator.seek_progress)
        play_layout.addWidget(self.quick_multi_metric_panel)

        ctrl_row = QHBoxLayout()
        self.btn_quick_play = QPushButton("▶️ Play")
        self.btn_quick_play.setEnabled(False)
        self.btn_quick_play.clicked.connect(self.canvas_animator.toggle_play)
        ctrl_row.addWidget(self.btn_quick_play)

        self.btn_quick_stop = QPushButton("⏹️ Reset")
        self.btn_quick_stop.setEnabled(False)
        self.btn_quick_stop.clicked.connect(self.canvas_animator.stop)
        ctrl_row.addWidget(self.btn_quick_stop)

        ctrl_row.addWidget(QLabel("Speed:"))
        self.cmb_quick_speed = QComboBox()
        self.cmb_quick_speed.addItems(["1x", "2x", "5x", "10x", "25x", "50x", "100x"])
        self.cmb_quick_speed.setCurrentIndex(3)
        self.cmb_quick_speed.currentIndexChanged.connect(self._on_anim_speed_changed)
        ctrl_row.addWidget(self.cmb_quick_speed)
        play_layout.addLayout(ctrl_row)

        _Horizontal = getattr(getattr(Qt, "Orientation", Qt), "Horizontal", 1)
        self.sld_quick_progress = QSlider(_Horizontal)
        self.sld_quick_progress.setRange(0, 1000)
        self.sld_quick_progress.setValue(0)
        self.sld_quick_progress.setEnabled(False)
        self.sld_quick_progress.sliderMoved.connect(self._on_anim_slider_moved)
        play_layout.addWidget(self.sld_quick_progress)

        status_row = QHBoxLayout()
        self.chk_quick_autopan = QCheckBox("🎯 Auto-pan")
        self.chk_quick_autopan.toggled.connect(self.canvas_animator.set_auto_pan)
        status_row.addWidget(self.chk_quick_autopan)
        self.lbl_quick_anim_status = QLabel("Ready to animate")
        self.lbl_quick_anim_status.setStyleSheet("color: #64748b; font-size: 11px;")
        status_row.addWidget(self.lbl_quick_anim_status)
        status_row.addStretch()
        play_layout.addLayout(status_row)

        self.btn_quick_open_3d = QPushButton("🎬 Open 3D WebGL Studio & Diorama")
        self.btn_quick_open_3d.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #059669, stop:1 #10b981);
                color: #ffffff;
                font-weight: 700;
                font-size: 12px;
                padding: 10px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #047857, stop:1 #059669);
            }
        """)
        self.btn_quick_open_3d.clicked.connect(self.open_3d_studio)
        play_layout.addWidget(self.btn_quick_open_3d)
        layout.addWidget(card_play)

        # 5. Quick Export Card
        card_exp = QFrame()
        card_exp.setProperty("class", "route3dCard")
        exp_layout = QVBoxLayout(card_exp)
        exp_layout.addWidget(QLabel("<b>4. Export Formats</b>"))
        exp_row = QHBoxLayout()

        self.btn_quick_export_gpx = QPushButton("💾 GPX")
        self.btn_quick_export_gpx.clicked.connect(self.export_gpx)
        self.btn_quick_export_gpx.setEnabled(False)
        exp_row.addWidget(self.btn_quick_export_gpx)

        self.btn_quick_export_geojson = QPushButton("💾 GeoJSON")
        self.btn_quick_export_geojson.clicked.connect(self.export_geojson)
        self.btn_quick_export_geojson.setEnabled(False)
        exp_row.addWidget(self.btn_quick_export_geojson)

        self.btn_quick_export_html = QPushButton("🌐 3D HTML")
        self.btn_quick_export_html.clicked.connect(self.export_standalone_html)
        self.btn_quick_export_html.setEnabled(False)
        exp_row.addWidget(self.btn_quick_export_html)

        self.btn_quick_export_dxf = QPushButton("📐 3D DXF")
        self.btn_quick_export_dxf.clicked.connect(self.export_dxf)
        self.btn_quick_export_dxf.setEnabled(False)
        exp_row.addWidget(self.btn_quick_export_dxf)
        exp_layout.addLayout(exp_row)
        layout.addWidget(card_exp)
        layout.addStretch()

        scroll.setWidget(container)
        parent_layout = QVBoxLayout(parent)
        parent_layout.setContentsMargins(0, 0, 0, 0)
        parent_layout.addWidget(scroll)

    def _build_tab_advanced(self, parent: QWidget) -> None:
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
        osm_header = QLabel("<b>1. OpenStreetMap Basemap & Vector Sources</b>")
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

        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Cartographic Style:"))
        self.cmb_carto_theme = QComboBox()
        self.cmb_carto_theme.setToolTip("Select a cartographic palette for OSM roads, 3D buildings, and trees (inspired by 02Agent OSM Downloader).")
        theme_icons = {
            "atlas": "🎨",
            "cyber": "⚡",
            "paper": "📜",
            "frost": "❄️",
            "noir": "🖤",
            "mediterranean": "🏖️",
            "nightprint": "🌌",
            "default": "🌿",
        }
        for key, label in list_osm_themes():
            self.cmb_carto_theme.addItem(f"{theme_icons.get(key, '🎨')} {label}", key)
        theme_row.addWidget(self.cmb_carto_theme, 1)

        btn_style_sources = QPushButton("Apply Style")
        btn_style_sources.setToolTip("Apply chosen cartographic theme to road, building, and tree layers in QGIS.")
        btn_style_sources.clicked.connect(self._style_selected_source_layers)
        theme_row.addWidget(btn_style_sources)
        source_grid.addLayout(theme_row, 2, 0, 1, 2)
        osm_layout.addLayout(source_grid)
        source_note = QLabel("If selected, external road/building layers replace the corresponding OSM source.")
        source_note.setStyleSheet("color: #64748b; font-size: 11px;")
        source_note.setWordWrap(True)
        osm_layout.addWidget(source_note)
        layout.addWidget(card_osm)

        # 2. Corridor Elevation Raster Card
        card_copernicus = QFrame()
        card_copernicus.setProperty("class", "route3dCard")
        cop_layout = QVBoxLayout(card_copernicus)
        cop_layout.addWidget(QLabel("<b>2. Corridor Elevation Acquisition & Clipping</b>"))

        cop_grid = QGridLayout()
        cop_grid.addWidget(QLabel("Elevation DEM:"), 0, 0)
        self.cmb_dem_layer = QgsMapLayerComboBox()
        self.cmb_dem_layer.setFilters(_raster_filters())
        cop_grid.addWidget(self.cmb_dem_layer, 0, 1)

        cop_grid.addWidget(QLabel("Thermal Heat LST:"), 1, 0)
        self.cmb_lst_layer = QgsMapLayerComboBox()
        self.cmb_lst_layer.setFilters(_raster_filters())
        cop_grid.addWidget(self.cmb_lst_layer, 1, 1)

        cop_grid.addWidget(QLabel("Tree Canopy / Greenery:"), 2, 0)
        self.cmb_green_layer = QgsMapLayerComboBox()
        self.cmb_green_layer.setFilters(_raster_filters())
        cop_grid.addWidget(self.cmb_green_layer, 2, 1)

        cop_grid.addWidget(QLabel("Additional MCDA rasters:"), 3, 0)
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
        cop_grid.addLayout(extra_row, 3, 1)

        self.lst_extra_rasters = QListWidget()
        self.lst_extra_rasters.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.lst_extra_rasters.setMaximumHeight(92)
        cop_grid.addWidget(self.lst_extra_rasters, 4, 1)

        btn_fetch_dem = QPushButton("⛰️ Fetch Full Map Extent Elevation DEM")
        btn_fetch_dem.setToolTip("Query real elevation for the active full map canvas extent from the Open-Elevation API and load into QGIS.")
        btn_fetch_dem.clicked.connect(self._on_fetch_global_dem_clicked)
        cop_grid.addWidget(btn_fetch_dem, 5, 0, 1, 2)

        lbl_dem_note = QLabel(
            "ℹ️ <i>Elevation is queried for the full active map extent from the "
            "Open-Elevation API and loaded into QGIS as a continuous GeoTIFF DEM. Points the "
            "service cannot resolve are written as NoData, never as zero.<br>For heat (LST) or "
            "greenery (NDVI) criteria, load your own real rasters in the selectors above — this "
            "plugin does not synthesise them.</i>"
        )
        lbl_dem_note.setStyleSheet("color: #64748b; font-size: 11px;")
        lbl_dem_note.setWordWrap(True)
        cop_grid.addWidget(lbl_dem_note, 6, 0, 1, 2)
        cop_layout.addLayout(cop_grid)
        layout.addWidget(card_copernicus)

        # 3. Point A & Point B Dual Origin/Destination Card
        card_ab = QFrame()
        card_ab.setProperty("class", "route3dCard")
        ab_layout = QVBoxLayout(card_ab)
        ab_layout.addWidget(QLabel("<b>3. Route Points (A & B)</b>"))

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

        btn_rev_ab = QPushButton("⇄ Swap Origin & Destination")
        btn_rev_ab.clicked.connect(self._reverse_waypoints)
        ab_layout.addWidget(btn_rev_ab)
        layout.addWidget(card_ab)

        # 4. Mobility Profile Selector & Multi-Modal Mode Scope Card
        card_prof = QFrame()
        card_prof.setProperty("class", "route3dCard")
        prof_layout = QVBoxLayout(card_prof)
        prof_layout.addWidget(QLabel("<b>4. User Mobility Profile & Mode Scope</b>"))

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

        self.lbl_profile_info = QLabel("Select a mobility profile to see its speed, gradient limit and stair policy.")
        self.lbl_profile_info.setProperty("class", "route3dBadgeInfo")
        prof_layout.addWidget(self.lbl_profile_info)
        layout.addWidget(card_prof)

        # 5. AHP Sliders Card
        grp_weights = QGroupBox("5. MCDA Resistance Weights (AHP)")
        w_layout = QVBoxLayout(grp_weights)
        _Horizontal = getattr(getattr(Qt, "Orientation", Qt), "Horizontal", 1)

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

        # 6. Action Card (Compute + 3D Corridor Animation)
        card_act = QFrame()
        card_act.setProperty("class", "route3dCard")
        act_layout = QVBoxLayout(card_act)
        act_layout.addWidget(QLabel("<b>6. Multi-Criteria 3D Compute & Actions</b>"))

        # Primary Compute 3D Route
        self.btn_compute = QPushButton("⚡ Compute Multi-Criteria 3D Path(s)")
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
        self.btn_open_3d = QPushButton("🎬 Open 3D WebGL Studio (30 m building corridor)")
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

        scenario_row = QHBoxLayout()
        self.btn_save_scenario = QPushButton("💾 Save Scenario")
        self.btn_save_scenario.setToolTip(
            "Save points, profiles, weights, layer choices and the current results to a JSON file."
        )
        self.btn_save_scenario.clicked.connect(self.save_scenario_dialog)
        scenario_row.addWidget(self.btn_save_scenario)
        self.btn_load_scenario = QPushButton("📂 Load Scenario")
        self.btn_load_scenario.setToolTip(
            "Restore a saved scenario. If routes are computed, they are compared with the saved run."
        )
        self.btn_load_scenario.clicked.connect(self.load_scenario_dialog)
        scenario_row.addWidget(self.btn_load_scenario)
        act_layout.addLayout(scenario_row)

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

        # 7. Real-Time 2D QGIS Canvas Animation Card
        card_anim = QFrame()
        card_anim.setProperty("class", "route3dCard")
        anim_layout = QVBoxLayout(card_anim)
        anim_layout.addWidget(QLabel("<b>7. Real-Time 2D Canvas Animation (QGIS Canvas)</b>"))

        self.advanced_multi_metric_panel = MultiMetricPanel(card_anim)
        self.advanced_multi_metric_panel.seek_requested.connect(self.canvas_animator.seek_progress)
        anim_layout.addWidget(self.advanced_multi_metric_panel)

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
        self.btn_reset_session.setToolTip("Reset current origin/destination points for a new run (keeps all previous layers in QGIS)")
        self.btn_reset_session.clicked.connect(self._reset_route_session)
        anim_ctrl_row.addWidget(self.btn_reset_session)

        anim_ctrl_row.addWidget(QLabel("Speed:"))
        self.cmb_anim_speed = QComboBox()
        self.cmb_anim_speed.addItems(["1x", "2x", "5x", "10x", "25x", "50x", "100x"])
        self.cmb_anim_speed.setCurrentIndex(3)  # Default 10x
        self.cmb_anim_speed.currentIndexChanged.connect(self._on_anim_speed_changed)
        anim_ctrl_row.addWidget(self.cmb_anim_speed)
        anim_layout.addLayout(anim_ctrl_row)

        _Horizontal = getattr(getattr(Qt, "Orientation", Qt), "Horizontal", 1)
        self.sld_anim_progress = QSlider(_Horizontal)
        self.sld_anim_progress.setRange(0, 1000)
        self.sld_anim_progress.setValue(0)
        self.sld_anim_progress.setEnabled(False)
        self.sld_anim_progress.sliderMoved.connect(self._on_anim_slider_moved)
        anim_layout.addWidget(self.sld_anim_progress)

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

        # 8. Multi-Profile Comparison & Benchmarking Table Card
        card_comp = QFrame()
        card_comp.setProperty("class", "route3dCard")
        comp_layout = QVBoxLayout(card_comp)
        comp_layout.addWidget(QLabel("<b>8. Multi-Profile Comparison & Benchmarking</b>"))

        self.table_comparison = QTableWidget(0, 7)
        self.table_comparison.setHorizontalHeaderLabels([
            "Mode", "Category", "Distance", "Duration", "Climb", "Max Slope", "Energy / Status"
        ])
        resize_mode_enum = getattr(QHeaderView, "ResizeMode", QHeaderView)
        resize_contents = getattr(resize_mode_enum, "ResizeToContents", 3)
        self.table_comparison.horizontalHeader().setSectionResizeMode(resize_contents)
        select_behavior_enum = getattr(QAbstractItemView, "SelectionBehavior", getattr(QTableWidget, "SelectionBehavior", QTableWidget))
        select_rows = getattr(select_behavior_enum, "SelectRows", 1)
        self.table_comparison.setSelectionBehavior(select_rows)
        self.table_comparison.cellClicked.connect(self._on_comparison_row_clicked)
        self.table_comparison.setMinimumHeight(160)
        comp_layout.addWidget(self.table_comparison)
        layout.addWidget(card_comp)
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
        resize_mode_enum = getattr(QHeaderView, "ResizeMode", QHeaderView)
        stretch_mode = getattr(resize_mode_enum, "Stretch", 1)
        self.table_od.horizontalHeader().setSectionResizeMode(stretch_mode)
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
            "• <b>Corridor Buffer:</b> 30-metre 3D building footprint extrusion along the route centreline"
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
        """Apply selected cartographic palette (8 themes) to road, building, and tree layers."""
        theme_key = self.cmb_carto_theme.currentData() or "atlas"
        theme_name = self.cmb_carto_theme.currentText()
        styled = 0
        proj = QgsProject.instance()

        # 1. Combo-selected layers
        for combo in (self.cmb_route_road_layer, self.cmb_route_building_layer):
            layer = combo.currentLayer()
            if layer is not None and apply_osm_theme_style(layer, theme_key):
                styled += 1

        # 2. Managed OSM layers in project
        for name in ("OSM Road Network", "OSM Buildings 3D", "OSM Trees & Greenery"):
            for lyr in proj.mapLayersByName(name):
                if lyr.isValid() and apply_osm_theme_style(lyr, theme_key):
                    styled += 1

        if self.iface:
            if styled:
                self.iface.messageBar().pushSuccess("02Route 3D", f"Applied '{theme_name}' style to {styled} layer(s).")
            else:
                self.iface.messageBar().pushWarning("02Route 3D", "Select or download a road/building layer first.")

    def _update_point_labels(self) -> None:
        """Synchronize Point A and B coordinate labels across Quick Mode and Advanced Lab."""
        if self.point_a is not None:
            txt_a = f"{self.point_a.lon:.5f}, {self.point_a.lat:.5f}"
            self.lbl_coord_a.setText(txt_a)
            self.lbl_coord_a.setStyleSheet("font-weight: bold; color: #059669; font-size: 11px;")
            if hasattr(self, "lbl_quick_coord_a"):
                self.lbl_quick_coord_a.setText(txt_a)
                self.lbl_quick_coord_a.setStyleSheet("font-weight: bold; color: #059669; font-size: 11px;")
        else:
            self.lbl_coord_a.setText("📍 Not selected (Pick on map or choose layer)")
            self.lbl_coord_a.setStyleSheet("font-style: italic; color: #64748b; font-size: 11px;")
            if hasattr(self, "lbl_quick_coord_a"):
                self.lbl_quick_coord_a.setText("Not selected")
                self.lbl_quick_coord_a.setStyleSheet("font-style: italic; color: #64748b; font-size: 11px;")

        if self.point_b is not None:
            txt_b = f"{self.point_b.lon:.5f}, {self.point_b.lat:.5f}"
            self.lbl_coord_b.setText(txt_b)
            self.lbl_coord_b.setStyleSheet("font-weight: bold; color: #dc2626; font-size: 11px;")
            if hasattr(self, "lbl_quick_coord_b"):
                self.lbl_quick_coord_b.setText(txt_b)
                self.lbl_quick_coord_b.setStyleSheet("font-weight: bold; color: #dc2626; font-size: 11px;")
        else:
            self.lbl_coord_b.setText("🎯 Not selected (Pick on map or choose layer)")
            self.lbl_coord_b.setStyleSheet("font-style: italic; color: #64748b; font-size: 11px;")
            if hasattr(self, "lbl_quick_coord_b"):
                self.lbl_quick_coord_b.setText("Not selected")
                self.lbl_quick_coord_b.setStyleSheet("font-style: italic; color: #64748b; font-size: 11px;")

    def _clear_route_point(self, target: str) -> None:
        """Clear one endpoint and remove its managed QGIS pin immediately."""
        if target == "A":
            self.point_a = None
            self.btn_pick_a.setChecked(False)
            if hasattr(self, "btn_quick_pick_a"):
                self.btn_quick_pick_a.setChecked(False)
        else:
            self.point_b = None
            self.btn_pick_b.setChecked(False)
            if hasattr(self, "btn_quick_pick_b"):
                self.btn_quick_pick_b.setChecked(False)
        if self.active_tool is not None and self.canvas is not None:
            with contextlib.suppress(Exception):
                self.canvas.unsetMapTool(self.active_tool)
        self.active_tool = None
        self.waypoints = [point for point in (self.point_a, self.point_b) if point is not None]
        self._update_point_labels()
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
        if hasattr(self, "quick_multi_metric_panel"):
            self.quick_multi_metric_panel.clear()
        if hasattr(self, "advanced_multi_metric_panel"):
            self.advanced_multi_metric_panel.clear()
        # Both widget sets must be reset. Only the Advanced labels were cleared,
        # so after "New Route" the Quick tab still showed the previous run's
        # distance, time and calories.
        for name in (
            "kpi_dist", "kpi_time", "kpi_climb", "kpi_slope", "kpi_kcal",
            "quick_kpi_dist", "quick_kpi_time", "quick_kpi_climb",
            "quick_kpi_slope", "quick_kpi_kcal",
        ):
            label = getattr(self, name, None)
            if label is not None:
                label.setText("—")

        # Likewise the export buttons: they stayed enabled and silently no-opped
        # against a cleared result.
        for name in (
            "btn_export_gpx", "btn_export_geojson", "btn_export_html", "btn_export_dxf",
            "btn_quick_export_gpx", "btn_quick_export_geojson",
            "btn_quick_export_html", "btn_quick_export_dxf",
        ):
            button = getattr(self, name, None)
            if button is not None:
                button.setEnabled(False)
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
        self.cached_osm_trees = []
        self.cached_osm_parks = []
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
            self.cached_osm_trees = []
            self.cached_osm_parks = []
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

    def _release_active_tool(self) -> None:
        """Unset and dispose the current map tool before another replaces it.

        Each picker toggle used to construct a fresh RoutePointMapTool and connect
        its signal without ever unsetting or deleting the previous one, leaving an
        orphaned vertex marker painted on the canvas and a Python object eligible
        for garbage collection while QGIS still held the C++ tool.
        """
        tool = self.active_tool
        if tool is None:
            return
        with contextlib.suppress(Exception):
            if self.canvas is not None:
                self.canvas.unsetMapTool(tool)
            tool.deactivate()
        with contextlib.suppress(Exception):
            tool.point_captured.disconnect()
        with contextlib.suppress(Exception):
            tool.deleteLater()
        self.active_tool = None

    def _toggle_specific_picker(self, target: str, checked: bool) -> None:
        if not self.canvas:
            return
        if checked:
            self.picking_target = target
            if target == "A" and self.btn_pick_b.isChecked():
                self.btn_pick_b.setChecked(False)
            elif target == "B" and self.btn_pick_a.isChecked():
                self.btn_pick_a.setChecked(False)

            # Dispose the previous tool first; arming B while A was armed used to
            # leave A's tool and its vertex marker behind.
            self._release_active_tool()
            # point_type drives the marker style; it was never passed, so Point B
            # always drew Point A's green origin marker.
            self.active_tool = RoutePointMapTool(
                self.canvas, point_type="start" if target == "A" else "end"
            )
            self.active_tool.point_captured.connect(self._on_point_captured)
            self.canvas.setMapTool(self.active_tool)
            if self.iface:
                self.iface.messageBar().pushInfo("02Route 3D", f"Click on map canvas to set Point {target}...")
        else:
            self._release_active_tool()

    def _on_point_captured(self, point: Any) -> None:
        lon = float(point.x())
        lat = float(point.y())
        if self.picking_target == "A":
            self.point_a = Waypoint(lon=lon, lat=lat, name="Point A (Origin)")
            self.btn_pick_a.setChecked(False)
            if hasattr(self, "btn_quick_pick_a"):
                self.btn_quick_pick_a.setChecked(False)
        else:
            self.point_b = Waypoint(lon=lon, lat=lat, name="Point B (Destination)")
            self.btn_pick_b.setChecked(False)
            if hasattr(self, "btn_quick_pick_b"):
                self.btn_quick_pick_b.setChecked(False)

        self._release_active_tool()

        self.waypoints = [w for w in (self.point_a, self.point_b) if w is not None]
        self._update_point_labels()
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
        else:
            self.point_b = Waypoint(lon=lon, lat=lat, name=f"Point B ({layer.name()})")

        self.waypoints = [w for w in (self.point_a, self.point_b) if w is not None]
        self._update_point_labels()
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
                    if layer_a.fields().count() >= 4:
                        f_a.setAttributes(["A", "Point A (Origin)", float(self.point_a.lon), float(self.point_a.lat)])
                    else:
                        f_a.setAttributes(["Point A (Origin)", float(self.point_a.lon), float(self.point_a.lat)])
                    layer_a.addFeatures([f_a])
                    layer_a.commitChanges()
                    layer_a.updateExtents()
                    layer_a.triggerRepaint()
            else:
                layer_a = QgsVectorLayer(
                    "Point?crs=EPSG:4326&field=label:string&field=name:string&field=lon:double&field=lat:double",
                    layer_a_name,
                    "memory",
                )
                layer_a.setCustomProperty("zero2route3d/route_point", "A")
                f_a = QgsFeature(layer_a.fields())
                f_a.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(self.point_a.lon, self.point_a.lat)))
                f_a.setAttributes(["A", "Point A (Origin)", float(self.point_a.lon), float(self.point_a.lat)])
                layer_a.dataProvider().addFeatures([f_a])
                layer_a.updateExtents()
                sym_a = _create_route_point_marker_symbol("A", "#059669", "#047857")
                layer_a.setRenderer(QgsSingleSymbolRenderer(sym_a))
                _apply_route_point_labeling(layer_a, "📍 Point A (Origin)", "#064e3b")
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
                    if layer_b.fields().count() >= 4:
                        f_b.setAttributes(["B", "Point B (Destination)", float(self.point_b.lon), float(self.point_b.lat)])
                    else:
                        f_b.setAttributes(["Point B (Destination)", float(self.point_b.lon), float(self.point_b.lat)])
                    layer_b.addFeatures([f_b])
                    layer_b.commitChanges()
                    layer_b.updateExtents()
                    layer_b.triggerRepaint()
            else:
                layer_b = QgsVectorLayer(
                    "Point?crs=EPSG:4326&field=label:string&field=name:string&field=lon:double&field=lat:double",
                    layer_b_name,
                    "memory",
                )
                layer_b.setCustomProperty("zero2route3d/route_point", "B")
                f_b = QgsFeature(layer_b.fields())
                f_b.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(self.point_b.lon, self.point_b.lat)))
                f_b.setAttributes(["B", "Point B (Destination)", float(self.point_b.lon), float(self.point_b.lat)])
                layer_b.dataProvider().addFeatures([f_b])
                layer_b.updateExtents()
                sym_b = _create_route_point_marker_symbol("B", "#dc2626", "#991b1b")
                layer_b.setRenderer(QgsSingleSymbolRenderer(sym_b))
                _apply_route_point_labeling(layer_b, "🎯 Point B (Destination)", "#7f1d1d")
                proj.addMapLayer(layer_b)

        if self.canvas:
            self.canvas.refresh()

    def _reverse_waypoints(self) -> None:
        if not self.point_a or not self.point_b:
            if self.iface:
                self.iface.messageBar().pushInfo("02Route 3D", "Please select both Point A and Point B first to swap them.")
            return
        self.point_a, self.point_b = self.point_b, self.point_a
        self.waypoints = [self.point_a, self.point_b]
        self._update_point_labels()
        self._update_point_vector_layers()

    def _on_profile_changed(self, index: int) -> None:
        key = self.cmb_profile.itemData(index) or "adult"
        p = get_profile(key)
        stairs = "Allowed" if p.stair_allowed else "Prohibited"
        info_txt = f"Speed: {p.base_speed_kmh:.1f} km/h | Max Slope: {p.max_slope_pct:.1f}% | Stairs: {stairs}"
        self.lbl_profile_info.setText(info_txt)

        if hasattr(self, "cmb_quick_profile") and self.cmb_quick_profile.currentIndex() != index:
            self.cmb_quick_profile.blockSignals(True)
            self.cmb_quick_profile.setCurrentIndex(index)
            self.cmb_quick_profile.blockSignals(False)

        if hasattr(self, "lbl_quick_profile_info"):
            self.lbl_quick_profile_info.setText(info_txt)

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

        if hasattr(self, "cmb_quick_mode_scope") and self.cmb_quick_mode_scope.currentIndex() != index:
            self.cmb_quick_mode_scope.blockSignals(True)
            self.cmb_quick_mode_scope.setCurrentIndex(index)
            self.cmb_quick_mode_scope.blockSignals(False)

        if hasattr(self, "cmb_quick_profile"):
            self.cmb_quick_profile.blockSignals(True)
            self.cmb_quick_profile.clear()
            for key in allowed:
                profile = get_profile(key)
                self.cmb_quick_profile.addItem(profile.name, key)
            target_idx_q = self.cmb_quick_profile.findData(selected_key)
            self.cmb_quick_profile.setCurrentIndex(max(0, target_idx_q))
            self.cmb_quick_profile.blockSignals(False)

        self._on_profile_changed(self.cmb_profile.currentIndex())

    def _on_quick_mode_scope_changed(self, index: int) -> None:
        self.cmb_mode_scope.setCurrentIndex(index)

    def _on_quick_profile_changed(self, index: int) -> None:
        self.cmb_profile.setCurrentIndex(index)

    def quick_compute_route(self) -> None:
        """1-Click workflow: basemap, real OSM environment, 3D routes and the 3D Studio.

        The OSM environment download and the routing run together in one
        background task (compute_route with fetch_environment=True).
        """
        if not self.point_a or not self.point_b:
            msg = "Please select both Point A (Origin) and Point B (Destination) first using 'Pick on Map'."
            if self.iface:
                self.iface.messageBar().pushWarning("02Route 3D", msg)
            else:
                QMessageBox.warning(self, "02Route 3D", msg)
            return

        self._on_add_osm_basemap()
        need_environment = not self.cached_osm_buildings or not self.cached_osm_trees
        if need_environment:
            self._notify("info", "⚡ Quick Mode: acquiring real 3D buildings, trees and parks, then routing...")
        self.compute_route(fetch_environment=need_environment)

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

    def _warn_no_extent(self) -> None:
        """Tell the user an extent is required instead of inventing one."""
        message = (
            "No area to work with. Zoom the map canvas to your area of interest, "
            "or set Point A and Point B first."
        )
        if self.iface:
            self.iface.messageBar().pushWarning("02Route 3D", message)
        else:
            QMessageBox.warning(self, "02Route 3D", message)

    def _write_route_payload(self, geojson_data: Any) -> bool:
        """Write current_route.json atomically, reporting failure instead of hiding it.

        The 3D studio polls this file every 1.5 s. Writing in place meant a poll
        landing mid-write read truncated JSON, which the viewer swallowed, leaving
        a stale scene with no explanation.
        """
        tmp_path = self.current_route_file.with_suffix(".json.tmp")
        try:
            tmp_path.write_text(json.dumps(geojson_data, indent=2), encoding="utf-8")
            tmp_path.replace(self.current_route_file)
            return True
        except OSError as exc:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
            message = (
                f"Could not hand the route to the 3D studio: {exc}. "
                f"The plugin folder may be read-only."
            )
            if self.iface:
                self.iface.messageBar().pushWarning("02Route 3D", message)
            return False

    def _get_active_bbox(self) -> Optional[Tuple[float, float, float, float]]:
        """WGS84 bounding box from the canvas or the waypoints, or None if neither.

        Returns None rather than a default city: silently downloading data for a
        hard-coded extent is worse than telling the user to set one.
        """
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
        # No canvas extent and no waypoints: there is nothing to derive a bounding
        # box from. Returning a hard-coded Izmir box silently downloaded data for a
        # city the user may never have opened.
        return None

    def _load_osm_layers_into_qgis(
        self,
        roads: List[Any],
        buildings: List[OsmBuilding],
        trees: Optional[List[OsmTree]] = None,
        parks: Optional[List[OsmPark]] = None,
    ) -> None:
        """Create or update dedicated OSM Road Network, 3D Buildings, and Parks/Trees layers in QGIS."""
        proj = QgsProject.instance()
        theme_key = getattr(self, "cmb_carto_theme", None).currentData() if hasattr(self, "cmb_carto_theme") and self.cmb_carto_theme else "atlas"

        # 1. Roads Layer
        if roads:
            road_layers = proj.mapLayersByName("OSM Road Network")
            if road_layers:
                road_layer = road_layers[0]
                with contextlib.suppress(Exception):
                    road_layer.startEditing()
                    road_layer.deleteFeatures(road_layer.allFeatureIds())
                    r_feats = []
                    for r in roads:
                        f = QgsFeature(road_layer.fields())
                        pts = [QgsPointXY(p[0], p[1]) for p in r.geometry]
                        f.setGeometry(QgsGeometry.fromPolylineXY(pts))
                        f.setAttributes([r.name, r.highway_type, 1 if r.oneway else 0])
                        r_feats.append(f)
                    road_layer.addFeatures(r_feats)
                    road_layer.commitChanges()
                    road_layer.updateExtents()
                    apply_osm_theme_style(road_layer, theme_key)
            else:
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
                apply_osm_theme_style(road_layer, theme_key)
                proj.addMapLayer(road_layer)
            self.cmb_route_road_layer.setLayer(road_layer)

        # 2. Buildings Layer
        if buildings:
            bld_layers = proj.mapLayersByName("OSM Buildings 3D")
            if bld_layers:
                bld_layer = bld_layers[0]
                with contextlib.suppress(Exception):
                    bld_layer.startEditing()
                    bld_layer.deleteFeatures(bld_layer.allFeatureIds())
                    b_feats = []
                    for b in buildings:
                        f = QgsFeature(bld_layer.fields())
                        pts = [QgsPointXY(p[0], p[1]) for p in b.polygon]
                        f.setGeometry(QgsGeometry.fromPolygonXY([pts]))
                        f.setAttributes([b.height_m, b.levels, b.building_type])
                        b_feats.append(f)
                    bld_layer.addFeatures(b_feats)
                    bld_layer.commitChanges()
                    bld_layer.updateExtents()
                    apply_osm_theme_style(bld_layer, theme_key)
            else:
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
                apply_osm_theme_style(bld_layer, theme_key)
                proj.addMapLayer(bld_layer)
            self.cmb_route_building_layer.setLayer(bld_layer)

        # 3. Trees & Parks Layer
        if trees:
            tree_layers = proj.mapLayersByName("OSM Trees & Greenery")
            if tree_layers:
                tree_layer = tree_layers[0]
                with contextlib.suppress(Exception):
                    tree_layer.startEditing()
                    tree_layer.deleteFeatures(tree_layer.allFeatureIds())
                    t_feats = []
                    for t in trees:
                        f = QgsFeature(tree_layer.fields())
                        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(t.lon, t.lat)))
                        f.setAttributes([t.species, t.height_m, t.canopy_radius_m, t.tree_type])
                        t_feats.append(f)
                    tree_layer.addFeatures(t_feats)
                    tree_layer.commitChanges()
                    tree_layer.updateExtents()
                    apply_osm_theme_style(tree_layer, theme_key)
            else:
                tree_layer = QgsVectorLayer("Point?crs=EPSG:4326", "OSM Trees & Greenery", "memory")
                t_pr = tree_layer.dataProvider()
                t_pr.addAttributes([
                    QgsField("species", QVariant.String),
                    QgsField("height_m", QVariant.Double),
                    QgsField("canopy_r", QVariant.Double),
                    QgsField("tree_type", QVariant.String),
                ])
                tree_layer.updateFields()
                t_feats = []
                for t in trees:
                    f = QgsFeature()
                    f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(t.lon, t.lat)))
                    f.setAttributes([t.species, t.height_m, t.canopy_radius_m, t.tree_type])
                    t_feats.append(f)
                t_pr.addFeatures(t_feats)
                tree_layer.updateExtents()
                apply_osm_theme_style(tree_layer, theme_key)
                proj.addMapLayer(tree_layer)

    def _fetch_osm_layers_for_extent(self) -> None:
        """Download real OSM roads, 3D buildings, trees and parks in the background."""
        bbox = self._get_active_bbox()
        if bbox is None:
            self._warn_no_extent()
            return
        if getattr(self, "_osm_task_running", False):
            self._notify("info", "An OpenStreetMap download is already running.")
            return
        self._notify("info", "Fetching OSM roads, buildings, trees and parks in the background...")

        def work(ctx: TaskContext) -> Any:
            ctx.check()
            data = OsmDataFetcher.fetch_full_urban_environment(bbox)
            return data, OsmDataFetcher.last_error

        def done(outcome: Any) -> None:
            self._osm_task_running = False
            (roads, buildings, trees, parks), reason = outcome
            self.cached_osm_buildings = buildings
            self.cached_osm_trees = trees
            self.cached_osm_parks = parks
            if not roads and not buildings and not trees:
                self._notify(
                    "warning",
                    f"OpenStreetMap download failed: {reason}" if reason
                    else "No OSM elements found in current bounding box.",
                )
                return
            self._load_osm_layers_into_qgis(roads, buildings, trees, parks)
            self._notify(
                "success",
                f"Acquired {len(roads)} OSM roads, {len(buildings)} buildings, {len(trees)} trees, and {len(parks)} parks!",
            )

        def failed(message: str) -> None:
            self._osm_task_running = False
            self._notify("warning", f"OpenStreetMap download failed: {message}")

        def cancelled() -> None:
            self._osm_task_running = False
            self._notify("info", "OpenStreetMap download cancelled.")

        self._osm_task_running = True
        start_task(self, FunctionTask("02Route 3D: OpenStreetMap download", work, done, failed, cancelled))

    def _on_fetch_global_dem_clicked(self) -> None:
        """Download a real elevation raster for the map extent in the background.

        The grid is budgeted (plan_dem_grid): large extents get coarser cells
        instead of thousands of requests, and the user confirms big downloads.
        """
        bbox = self._get_active_bbox()
        if bbox is None:
            self._warn_no_extent()
            return
        if getattr(self, "_dem_task_running", False):
            self._notify("info", "An elevation download is already running.")
            return

        plan = plan_dem_grid(bbox)
        if plan["requests"] > DEM_CONFIRM_REQUESTS or (
            self.cmb_dem_layer.currentLayer() is not None
        ):
            existing = self.cmb_dem_layer.currentLayer()
            lines = [
                f"This downloads {plan['points']:,} elevation samples "
                f"({plan['requests']} requests to Open-Elevation) at about "
                f"{plan['res_m']:.0f} m resolution."
            ]
            if plan["coarsened"]:
                lines.append("The extent is large, so the cells are coarser than 30 m.")
            if existing is not None:
                lines.append(
                    f"A DEM layer is already selected ({existing.name()}); routing uses it."
                )
            lines.append("Continue?")
            answer = QMessageBox.question(self, "02Route 3D", "\n\n".join(lines))
            if answer != QMessageBox.StandardButton.Yes:
                return

        self._notify(
            "info",
            f"Downloading elevation ({plan['points']:,} samples, ~{plan['res_m']:.0f} m) in the background. "
            "Cancel it from the QGIS task bar.",
        )

        def work(ctx: TaskContext) -> Any:
            results = CorridorElevationSuite.fetch_and_clip_corridor_elevation(
                bbox,
                corridor_coords=None,
                progress=ctx.progress,
                is_canceled=ctx.is_canceled,
            )
            ctx.check()
            return results, GlobalDemFetcher.last_error

        def done(outcome: Any) -> None:
            self._dem_task_running = False
            results, reason = outcome
            self._load_dem_results(results, reason)

        def failed(message: str) -> None:
            self._dem_task_running = False
            self._notify("warning", f"Elevation acquisition failed: {message}")

        def cancelled() -> None:
            self._dem_task_running = False
            self._notify("info", "Elevation download cancelled.")

        self._dem_task_running = True
        start_task(self, FunctionTask("02Route 3D: elevation download", work, done, failed, cancelled))

    def _load_dem_results(self, results: List[Any], reason: str = "") -> None:
        """Add downloaded elevation rasters to the project (main thread)."""
        if not results:
            self._notify(
                "warning",
                "No elevation layer could be generated for the active extent"
                + (f" (Open-Elevation: {reason})." if reason else "."),
            )
            return

        project = QgsProject.instance()
        root = project.layerTreeRoot()
        group_name = "Elevation DEM"
        group = root.findGroup(group_name) if root is not None else None
        if group is None and root is not None:
            group = root.insertGroup(1, group_name)

        loaded_count = 0
        for res in results:
            layer = QgsRasterLayer(str(res.file_path), res.name, "gdal")
            if not layer.isValid():
                continue
            layer.setCustomProperty("zero2route3d/corridor_raster", res.key)
            apply_environmental_raster_symbology(layer, res.color_palette, res.min_val, res.max_val)
            project.addMapLayer(layer, False)
            if group is not None:
                group.addLayer(layer)
            else:
                project.addMapLayer(layer, True)
            loaded_count += 1
            if res.key == "dem":
                self.cmb_dem_layer.setLayer(layer)

        if loaded_count > 0:
            self._notify("success", f"Loaded the elevation DEM ({results[0].name}) into QGIS.")
        else:
            self._notify("warning", "The elevation raster was written but could not be loaded into QGIS.")

    def _notify(self, level: str, message: str) -> None:
        """Message bar when running inside QGIS, a dialog otherwise."""
        if self.iface:
            bar = self.iface.messageBar()
            push = {"success": bar.pushSuccess, "warning": bar.pushWarning, "critical": bar.pushCritical}
            push.get(level, bar.pushInfo)("02Route 3D", message)
        elif level == "warning":
            QMessageBox.warning(self, "02Route 3D", message)
        elif level == "critical":
            QMessageBox.critical(self, "02Route 3D", message)

    def _set_compute_busy(self, busy: bool) -> None:
        """Disable the compute buttons while a computation is running.

        A second click while the background task runs would start a duplicate.
        """
        for name in ("btn_compute", "btn_quick_compute"):
            button = getattr(self, name, None)
            if button is not None:
                button.setEnabled(not busy)
                if busy:
                    button.setText("Computing...")
                elif getattr(self, "_compute_button_labels", None):
                    original = self._compute_button_labels.get(name)
                    if original:
                        button.setText(original)
        if busy and not getattr(self, "_compute_button_labels", None):
            self._compute_button_labels = {
                name: getattr(self, name).text()
                for name in ("btn_compute", "btn_quick_compute")
                if getattr(self, name, None) is not None
            }

    def compute_route(self, fetch_environment: bool = False) -> None:
        """Compute 3D route(s) in the background, then show them (layer, animation, 3D).

        The main thread only reads the inputs (points, weights, layers) and,
        when the task finishes, updates the UI; downloads, graph building and
        routing run in a QgsTask that can be cancelled from the task bar.
        """
        if getattr(self, "_computing", False):
            self._notify("info", "A route calculation is already running.")
            return
        if not self.point_a or not self.point_b:
            msg = "Please select both Point A (Origin) and Point B (Destination) first using 'Pick on Map' or 'Use Layer'."
            if self.iface:
                self.iface.messageBar().pushWarning("02Route 3D", msg)
            else:
                QMessageBox.warning(self, "02Route 3D", msg)
            return

        job = self._prepare_route_job(fetch_environment)
        if job is None:
            return

        self._computing = True
        self._set_compute_busy(True)
        self._set_route_progress(15)

        def work(ctx: TaskContext) -> Any:
            return self._route_work(job, ctx)

        def done(outcome: Any) -> None:
            self._end_route_task()
            self._finish_route(job, outcome)

        def failed(message: str) -> None:
            self._end_route_task()
            self._show_route_error(message)

        def cancelled() -> None:
            self._end_route_task()
            self._notify("info", "Route calculation cancelled.")

        self._route_task = start_task(
            self, FunctionTask("02Route 3D: route calculation", work, done, failed, cancelled)
        )
        self._route_task.progressChanged.connect(self._set_route_progress)

    def _set_route_progress(self, value: float) -> None:
        for bar_name in ("progress_bar", "quick_progress_bar"):
            bar = getattr(self, bar_name, None)
            if bar is not None:
                bar.setVisible(True)
                bar.setValue(int(value))

    def _end_route_task(self) -> None:
        self._computing = False
        self._set_compute_busy(False)
        for bar_name in ("progress_bar", "quick_progress_bar"):
            bar = getattr(self, bar_name, None)
            if bar is not None:
                bar.setVisible(False)

    def _prepare_route_job(self, fetch_environment: bool) -> Optional[Dict[str, Any]]:
        """Read every input the routing needs on the main thread.

        Vector layers are read into plain segments here; raster layers are
        snapshotted (cloned providers), so the task never touches live layers.
        """
        waypoints = [self.point_a, self.point_b]
        lons = [w.lon for w in waypoints]
        lats = [w.lat for w in waypoints]
        d_lon = max(0.001, max(lons) - min(lons))
        d_lat = max(0.001, max(lats) - min(lats))
        buf_lon = min(0.006, max(0.0025, d_lon * 0.35))
        buf_lat = min(0.006, max(0.0025, d_lat * 0.35))
        bbox = (min(lons) - buf_lon, min(lats) - buf_lat, max(lons) + buf_lon, max(lats) + buf_lat)
        env_lons_buf = min(0.008, max(0.0035, d_lon * 0.45))
        env_lats_buf = min(0.008, max(0.0035, d_lat * 0.45))
        env_bbox = (
            min(lons) - env_lons_buf, min(lats) - env_lats_buf,
            max(lons) + env_lons_buf, max(lats) + env_lats_buf,
        )

        weights = MCDAWeights(
            weight_slope=self.sld_slope.value() / 100.0,
            weight_heat=self.sld_heat.value() / 100.0,
            weight_green=self.sld_green.value() / 100.0,
        )
        sampler = EnvironmentalSurfaceSampler(
            dem_layer=snapshot(self.cmb_dem_layer.currentLayer()),
            lst_layer=snapshot(self.cmb_lst_layer.currentLayer()),
            green_layer=snapshot(self.cmb_green_layer.currentLayer()),
            additional_layers=[
                snap for snap in (snapshot(layer) for layer in self._selected_extra_raster_layers()) if snap
            ],
            weights=weights,
        )

        selected_road_layer = self.cmb_route_road_layer.currentLayer()
        selected_building_layer = self.cmb_route_building_layer.currentLayer()
        for source_layer in (selected_road_layer, selected_building_layer):
            if source_layer is not None and source_layer.isValid():
                apply_osm_atlas_style(source_layer)

        layer_segments = None
        if selected_road_layer is not None and selected_road_layer.isValid():
            try:
                layer_segments = self.network_manager.require_segments(vector_layer=selected_road_layer)
            except NetworkSourceError as exc:
                self._show_route_error(str(exc))
                return None

        buildings_from_layer = None
        if selected_building_layer is not None and selected_building_layer.isValid():
            buildings_from_layer = self._extract_buildings_from_layer(selected_building_layer)

        scope_key = self.cmb_mode_scope.currentData() or "single"
        if scope_key == "single":
            target_keys = [self.cmb_profile.currentData() or "adult"]
        else:
            target_keys = list_profile_keys_for_group(scope_key)
        primary_key = self.cmb_profile.currentData() or target_keys[0]

        return {
            "waypoints": waypoints,
            "bbox": bbox,
            "env_bbox": env_bbox,
            "weights": weights,
            "sampler": sampler,
            "layer_segments": layer_segments,
            "buildings_from_layer": buildings_from_layer,
            "target_keys": target_keys,
            "primary_key": primary_key,
            # Download buildings/trees/parks too: Quick Mode, or no building
            # layer and nothing cached yet.
            "fetch_environment": bool(fetch_environment)
            or (buildings_from_layer is None and not self.cached_osm_buildings),
        }

    def _route_work(self, job: Dict[str, Any], ctx: TaskContext) -> Dict[str, Any]:
        """Background part of a route calculation: no widgets, no live layers."""
        outcome: Dict[str, Any] = {"environment": None, "environment_error": ""}
        if job["fetch_environment"]:
            ctx.progress(5)
            outcome["environment"] = OsmDataFetcher.fetch_full_urban_environment(job["env_bbox"])
            outcome["environment_error"] = OsmDataFetcher.last_error
            ctx.check()

        ctx.progress(20)
        segments = job["layer_segments"]
        if segments is None:
            segments = self.network_manager.require_segments(bbox=job["bbox"], is_canceled=ctx.is_canceled)
        ctx.check()

        ctx.progress(35)
        engine = RoutingEngine3D(sampler=job["sampler"], weights=job["weights"])
        engine.build_graph(segments)
        if not engine.nodes:
            raise NetworkSourceError("The selected extent contains no usable network nodes.")
        ctx.check()

        results: Dict[str, RouteResult3D] = {}
        keys = job["target_keys"]
        for index, key in enumerate(keys):
            ctx.progress(55 + 35 * index / max(1, len(keys)))
            results[key] = engine.calculate_route(job["waypoints"], profile_key=key, optimize_tsp=False)
            ctx.check()
        outcome["results"] = results
        ctx.progress(95)
        return outcome

    def _finish_route(self, job: Dict[str, Any], outcome: Dict[str, Any]) -> None:
        """Main-thread part: store results and update panels, layers and 3D data."""
        self.waypoints = list(job["waypoints"])
        if outcome.get("environment") is not None:
            roads, buildings, trees, parks = outcome["environment"]
            if buildings:
                self.cached_osm_buildings = buildings
            if trees:
                self.cached_osm_trees = trees
            if parks:
                self.cached_osm_parks = parks
            if roads or buildings or trees or parks:
                self._load_osm_layers_into_qgis(roads, buildings, trees, parks)
            elif outcome.get("environment_error"):
                self._notify(
                    "warning",
                    f"OpenStreetMap buildings/trees could not be downloaded: {outcome['environment_error']}",
                )
        if job["buildings_from_layer"] is not None:
            self.cached_osm_buildings = job["buildings_from_layer"]

        self.multi_route_results = dict(outcome["results"])
        keys = job["target_keys"]
        primary_key = job["primary_key"] if job["primary_key"] in self.multi_route_results else keys[0]
        result = self.multi_route_results[primary_key]
        self.current_route_result = result

        if not result.coordinates_3d:
            self._show_route_error(result.status_message or "No route could be found between the selected points.")
            return

        self._show_route_results(result)
        self._finish_scenario_comparison()

    def _show_route_results(self, result: RouteResult3D) -> None:
        """Show computed routes: KPIs, ribbons, cue sheet, layer, animation, 3D data."""
        # Update KPIs
        self._update_kpi_display(result)

        # Update Multi-Metric Ribbon Panels
        prof_list = result.statistics.elevation_profile
        prof_col = get_profile_color(result.profile.key)
        if hasattr(self, "quick_multi_metric_panel"):
            self.quick_multi_metric_panel.set_route_profile(prof_list, prof_col)
        if hasattr(self, "advanced_multi_metric_panel"):
            self.advanced_multi_metric_panel.set_route_profile(prof_list, prof_col)

        # Cue Sheet
        self.cue_widget.load_cues(result.statistics.cue_sheet)

        # Populate Multi-Profile Comparison Table
        self._update_comparison_table()

        # Load 2D canvas animator with all calculated routes
        self.canvas_animator.load_routes(list(self.multi_route_results.values()))

        geojson_data = self._build_web_route_payload(result)

        with contextlib.suppress(Exception):
            self._write_route_payload(geojson_data)

        # Automatically add the unified categorized multi-profile route layer to QGIS
        self.add_route_layer_to_qgis()
        self.canvas_animator.bring_avatar_layer_to_top()

        # Enable Export Buttons & Canvas Animator Controls
        self.btn_add_layer.setEnabled(True)
        self.btn_export_gpx.setEnabled(True)
        self.btn_export_geojson.setEnabled(True)
        self.btn_export_html.setEnabled(True)
        self.btn_export_dxf.setEnabled(True)
        if hasattr(self, "btn_quick_export_gpx"):
            self.btn_quick_export_gpx.setEnabled(True)
            self.btn_quick_export_geojson.setEnabled(True)
            self.btn_quick_export_html.setEnabled(True)
            self.btn_quick_export_dxf.setEnabled(True)

        self.btn_anim_play.setText("▶️ Play")
        self.btn_anim_play.setEnabled(True)
        self.btn_anim_stop.setEnabled(True)
        self.sld_anim_progress.setEnabled(True)

        if hasattr(self, "btn_quick_play"):
            self.btn_quick_play.setText("▶️ Play")
            self.btn_quick_play.setEnabled(True)
            self.btn_quick_stop.setEnabled(True)
            self.sld_quick_progress.setEnabled(True)

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
        dist_str = f"{result.statistics.total_distance_km} km"
        time_str = f"{result.statistics.total_duration_min} min"
        climb_str = f"+{result.statistics.elevation_gain_m:.1f} m"
        slope_str = f"{result.statistics.max_slope_pct:.1f}%"
        kcal_str = f"{result.statistics.total_calories_kcal:.0f} kcal"

        self.kpi_dist.setText(dist_str)
        self.kpi_time.setText(time_str)
        self.kpi_climb.setText(climb_str)
        self.kpi_slope.setText(slope_str)
        self.kpi_kcal.setText(kcal_str)

        segment_diagnostics = result.routing_diagnostics.get("segments") or []
        if segment_diagnostics:
            diag = segment_diagnostics[0]
            diagnostic_text = (
                f"Network snap: A {diag.get('start_snap_m', 0.0):.1f} m, "
                f"B {diag.get('end_snap_m', 0.0):.1f} m\n"
                f"Expanded nodes: {diag.get('expanded_nodes', 0)}\n"
                f"Access-blocked edges: {diag.get('blocked_by_access', 0)}"
            )
            for label in (
                self.kpi_dist,
                self.kpi_time,
                self.kpi_climb,
                self.kpi_slope,
                self.kpi_kcal,
            ):
                label.setToolTip(diagnostic_text)

        if hasattr(self, "quick_kpi_dist"):
            self.quick_kpi_dist.setText(dist_str)
            self.quick_kpi_time.setText(time_str)
            self.quick_kpi_climb.setText(climb_str)
            self.quick_kpi_slope.setText(slope_str)
            self.quick_kpi_kcal.setText(kcal_str)
            if segment_diagnostics:
                for label in (
                    self.quick_kpi_dist,
                    self.quick_kpi_time,
                    self.quick_kpi_climb,
                    self.quick_kpi_slope,
                    self.quick_kpi_kcal,
                ):
                    label.setToolTip(diagnostic_text)

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
            prof_list = res.statistics.elevation_profile
            prof_col = get_profile_color(res.profile.key)
            if hasattr(self, "quick_multi_metric_panel"):
                self.quick_multi_metric_panel.set_route_profile(prof_list, prof_col)
            if hasattr(self, "advanced_multi_metric_panel"):
                self.advanced_multi_metric_panel.set_route_profile(prof_list, prof_col)

    def _on_anim_frame_updated(self, current_time_s: float, max_time_s: float, progress: float) -> None:
        self.sld_anim_progress.blockSignals(True)
        self.sld_anim_progress.setValue(int(progress * 1000))
        self.sld_anim_progress.blockSignals(False)

        if hasattr(self, "sld_quick_progress"):
            self.sld_quick_progress.blockSignals(True)
            self.sld_quick_progress.setValue(int(progress * 1000))
            self.sld_quick_progress.blockSignals(False)

        if hasattr(self, "quick_multi_metric_panel"):
            self.quick_multi_metric_panel.set_progress(progress)
        if hasattr(self, "advanced_multi_metric_panel"):
            self.advanced_multi_metric_panel.set_progress(progress)

        cur_min, cur_sec = divmod(int(current_time_s), 60)
        tot_min, tot_sec = divmod(int(max_time_s), 60)
        pct = int(progress * 100)
        mode_count = len(self.canvas_animator.avatars)
        msg = f"{cur_min:02d}:{cur_sec:02d} / {tot_min:02d}:{tot_sec:02d} ({pct}%) | 🚗 {mode_count} Modes Active"
        self.lbl_anim_status.setText(msg)
        if hasattr(self, "lbl_quick_anim_status"):
            self.lbl_quick_anim_status.setText(msg)

    def _on_anim_state_changed(self, is_playing: bool) -> None:
        txt = "⏸️ Pause" if is_playing else "▶️ Play"
        has_routes = bool(self.canvas_animator.avatars) or bool(self.multi_route_results)
        self.btn_anim_play.setText(txt)
        self.btn_anim_play.setEnabled(has_routes)
        if hasattr(self, "btn_quick_play"):
            self.btn_quick_play.setText(txt)
            self.btn_quick_play.setEnabled(has_routes)
        if hasattr(self, "btn_quick_stop"):
            self.btn_quick_stop.setEnabled(has_routes)
        if hasattr(self, "sld_quick_progress"):
            self.sld_quick_progress.setEnabled(has_routes)

    def _on_anim_slider_moved(self, val: int) -> None:
        fraction = val / 1000.0
        if hasattr(self, "quick_multi_metric_panel"):
            self.quick_multi_metric_panel.set_progress(fraction)
        if hasattr(self, "advanced_multi_metric_panel"):
            self.advanced_multi_metric_panel.set_progress(fraction)
        self.canvas_animator.seek_progress(fraction)

    def _on_anim_speed_changed(self, _index: int) -> None:
        sender = self.sender()
        speed_str = sender.currentText().replace("x", "") if sender else "10"
        with contextlib.suppress(ValueError):
            self.canvas_animator.set_speed_multiplier(float(speed_str))

    def open_3d_studio(self) -> None:
        """Start the local HTTP server and open the 3D WebGL studio (30 m building corridor)."""
        if self.current_route_result is not None or self.multi_route_results:
            geojson_data = self._build_web_route_payload(self.current_route_result)
            with contextlib.suppress(Exception):
                self._write_route_payload(geojson_data)
        server_url = self.local_server.start()
        QDesktopServices.openUrl(QUrl(server_url))

    def _open_profile_editor(self) -> None:
        key = self.cmb_profile.currentData() or "adult"
        prof = get_profile(key)
        dialog = ProfileEditorDialog(profile=prof, parent=self)
        if dialog.exec():
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
        """Compute the waypoint OD matrix in a background task."""
        if len(self.waypoints) < 2:
            return
        if getattr(self, "_od_task_running", False):
            self._notify("info", "An OD matrix calculation is already running.")
            return
        lons = [w.lon for w in self.waypoints]
        lats = [w.lat for w in self.waypoints]
        bbox = (min(lons), min(lats), max(lons), max(lats))
        layer_segments = None
        selected_road_layer = self.cmb_route_road_layer.currentLayer()
        if selected_road_layer is not None and selected_road_layer.isValid():
            try:
                layer_segments = self.network_manager.require_segments(vector_layer=selected_road_layer)
            except NetworkSourceError as exc:
                self._show_route_error(str(exc))
                return
        sampler = EnvironmentalSurfaceSampler(
            dem_layer=snapshot(self.cmb_dem_layer.currentLayer()),
            lst_layer=snapshot(self.cmb_lst_layer.currentLayer()),
            green_layer=snapshot(self.cmb_green_layer.currentLayer()),
            additional_layers=[
                snap for snap in (snapshot(layer) for layer in self._selected_extra_raster_layers()) if snap
            ],
        )
        waypoints = list(self.waypoints)
        profile_key = self.cmb_profile.currentData() or "adult"
        manager = self.network_manager

        def work(ctx: TaskContext) -> Any:
            segments = layer_segments
            if segments is None:
                segments = manager.require_segments(bbox=bbox, is_canceled=ctx.is_canceled)
            ctx.check()
            engine = RoutingEngine3D(sampler=sampler)
            engine.build_graph(segments)

            def report(done: int, total: int) -> bool:
                ctx.progress(100.0 * done / max(1, total))
                return not ctx.is_canceled()

            rows = engine.calculate_od_matrix(waypoints, waypoints, profile_key=profile_key, progress_callback=report)
            ctx.check()
            return rows

        def done(rows: Any) -> None:
            self._od_task_running = False
            self.table_od.setRowCount(len(rows))
            for idx, r in enumerate(rows):
                self.table_od.setItem(idx, 0, QTableWidgetItem(r["origin_name"]))
                self.table_od.setItem(idx, 1, QTableWidgetItem(r["dest_name"]))
                self.table_od.setItem(idx, 2, QTableWidgetItem(f"{r['distance_km']:.2f}"))
                self.table_od.setItem(idx, 3, QTableWidgetItem(f"{r['duration_min']:.1f}"))
                self.table_od.setItem(idx, 4, QTableWidgetItem(f"{r['climb_m']:.1f}"))

        def failed(message: str) -> None:
            self._od_task_running = False
            self._show_route_error(message)

        def cancelled() -> None:
            self._od_task_running = False
            self._notify("info", "OD matrix calculation cancelled.")

        self._od_task_running = True
        start_task(self, FunctionTask("02Route 3D: OD matrix", work, done, failed, cancelled))

    def _create_surface_sampler(self, weights: Optional[MCDAWeights] = None) -> EnvironmentalSurfaceSampler:
        """Create surface sampler from active dock raster layers and MCDA weights."""
        mcda_w = weights or MCDAWeights(
            weight_slope=self.sld_slope.value() / 100.0 if hasattr(self, "sld_slope") else 1.0,
            weight_heat=self.sld_heat.value() / 100.0 if hasattr(self, "sld_heat") else 0.5,
            weight_green=self.sld_green.value() / 100.0 if hasattr(self, "sld_green") else 0.5,
        )
        return EnvironmentalSurfaceSampler(
            dem_layer=self.cmb_dem_layer.currentLayer() if hasattr(self, "cmb_dem_layer") else None,
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
            osm_trees=self.cached_osm_trees,
            osm_parks=self.cached_osm_parks,
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

        self._run_counter += 1
        proj = QgsProject.instance()
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        routes_to_add = list(self.multi_route_results.values()) if self.multi_route_results else [self.current_route_result]
        mode_count = len(routes_to_add)
        primary_name = routes_to_add[0].profile.name if routes_to_add and routes_to_add[0] and routes_to_add[0].profile else "Route"
        if mode_count > 1:
            layer_name = f"🛣️ 02Route 3D — Run #{self._run_counter} ({primary_name} +{mode_count-1} modes, {now_str})"
        else:
            layer_name = f"🛣️ 02Route 3D — Run #{self._run_counter} ({primary_name}, {now_str})"

        layer = QgsVectorLayer(
            "LineStringZ?crs=EPSG:4326&field=mode_key:string&field=mode_name:string&field=category:string"
            "&field=dist_km:double&field=dist_m:double&field=time_min:double&field=climb_m:double"
            "&field=loss_m:double&field=max_slope:double&field=avg_slope:double&field=calories:double"
            "&field=ada_ok:int&field=status:string",
            layer_name,
            "memory",
        )
        layer.setCustomProperty("zero2route3d/route_layer", True)
        layer.setCustomProperty("zero2route3d/run_id", self._run_counter)
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

        feats = []
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
        pr = layer.dataProvider()
        pr.addFeatures(feats)
        layer.updateExtents()
        apply_multiprofile_categorized_renderer(layer, field_name="mode_key", active_keys=active_keys)

        # Place into dedicated scenarios group in QGIS Layer Tree
        root = proj.layerTreeRoot()
        scenarios_group_name = "🛣️ 02Route 3D Scenarios & Runs"
        scenarios_group = root.findGroup(scenarios_group_name) if root is not None else None
        if scenarios_group is None and root is not None:
            scenarios_group = root.insertGroup(1, scenarios_group_name)

        proj.addMapLayer(layer, False)
        if scenarios_group is not None:
            scenarios_group.insertLayer(0, layer)
        else:
            proj.addMapLayer(layer, True)

        if self.iface:
            self.iface.messageBar().pushSuccess("02Route 3D", f"Added '{layer.name()}' ({len(feats)} modes) to QGIS project.")

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

    def teardown(self, remove_layers: bool = True) -> None:
        """Release map tools, animator markers and the background server.

        remove_layers=False is used when the dock is merely hidden: closing the
        panel used to delete the route layers the user had just added to their
        project, without asking, and permanently disconnect the project signal so
        the reopened dock no longer tracked layer removal at all.
        """
        # On plugin unload, running downloads/routing are cancelled and must not
        # call back into the deleted dock. Merely hiding the panel lets them finish.
        save_settings(self)
        if remove_layers:
            cancel_tasks(self)
        if self.active_tool is not None and self.canvas is not None:
            with contextlib.suppress(Exception):
                self.canvas.unsetMapTool(self.active_tool)
                self.active_tool.deactivate()
            self.active_tool = None

        if hasattr(self, "btn_pick_a") and self.btn_pick_a:
            self.btn_pick_a.setChecked(False)
        if hasattr(self, "btn_pick_b") and self.btn_pick_b:
            self.btn_pick_b.setChecked(False)

        self._clear_animation_state()
        if remove_layers:
            with contextlib.suppress(Exception):
                QgsProject.instance().layersWillBeRemoved.disconnect(
                    self._on_project_layers_removed
                )
            with contextlib.suppress(Exception):
                QgsProject.instance().readProject.disconnect(self._on_project_read)
            with contextlib.suppress(Exception):
                QgsProject.instance().writeProject.disconnect(self._on_project_write)
            self._remove_transient_project_layers()

        if hasattr(self, "local_server") and self.local_server:
            with contextlib.suppress(Exception):
                self.local_server.stop()

        with contextlib.suppress(OSError):
            self.current_route_file.unlink()

    # ------------------------------------------------------------------
    # Settings and scenarios
    # ------------------------------------------------------------------
    def _on_project_read(self, *_args: Any) -> None:
        restore_project_layers(self)

    def _on_project_write(self, *_args: Any) -> None:
        # Runs before QGIS writes the project properties, so the entry is saved.
        save_settings(self)

    def _results_summary(self) -> Dict[str, Dict[str, Any]]:
        return {
            key: summarize_statistics(result.profile.name, result.statistics)
            for key, result in self.multi_route_results.items()
            if result is not None and result.coordinates_3d
        }

    def build_current_scenario(self, name: str = "") -> Dict[str, Any]:
        def point(waypoint: Optional[Waypoint]) -> Optional[Dict[str, Any]]:
            if waypoint is None:
                return None
            return {"lon": waypoint.lon, "lat": waypoint.lat, "name": waypoint.name}

        return build_scenario(
            inputs=collect_inputs(self),
            layers=collect_layers(self),
            points={"A": point(self.point_a), "B": point(self.point_b)},
            results=self._results_summary(),
            name=name,
        )

    def apply_scenario(self, scenario: Dict[str, Any]) -> None:
        """Restore the inputs of a parsed scenario (results are not recomputed)."""
        apply_inputs(self, scenario.get("inputs") or {})
        apply_layers(self, scenario.get("layers") or {})
        points = scenario.get("points") or {}
        for key in ("A", "B"):
            value = points.get(key)
            waypoint = Waypoint(lon=value["lon"], lat=value["lat"], name=value["name"]) if value else None
            if key == "A":
                self.point_a = waypoint
            else:
                self.point_b = waypoint
        self.waypoints = [w for w in (self.point_a, self.point_b) if w is not None]
        self._update_point_labels()
        self._update_point_vector_layers()

    def save_scenario_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save 02Route 3D Scenario", "", "02Route 3D Scenario (*.route3d.json *.json)"
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".route3d.json"
        try:
            save_scenario(Path(path), self.build_current_scenario(name=Path(path).stem))
        except OSError as exc:
            self._notify("critical", f"The scenario could not be saved: {exc}")
            return
        self._notify("success", f"Scenario saved to {path}")

    def load_scenario_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load 02Route 3D Scenario", "", "02Route 3D Scenario (*.route3d.json *.json)"
        )
        if not path:
            return
        try:
            scenario = load_scenario(Path(path))
        except ScenarioError as exc:
            self._notify("critical", str(exc))
            return
        current = self._results_summary()
        self.apply_scenario(scenario)
        if scenario["results"] and current:
            self.show_scenario_comparison(scenario, current)
        else:
            self._notify(
                "success",
                f"Scenario '{scenario['name'] or Path(path).stem}' loaded. Compute the route to compare with it.",
            )
            self._loaded_scenario = scenario

    def _finish_scenario_comparison(self) -> None:
        """After a route finishes, compare it with a scenario loaded before the run."""
        scenario = getattr(self, "_loaded_scenario", None)
        if scenario and scenario.get("results"):
            self._loaded_scenario = None
            self.show_scenario_comparison(scenario, self._results_summary())

    def show_scenario_comparison(self, scenario: Dict[str, Any], current: Dict[str, Dict[str, Any]]) -> QDialog:
        rows = compare_runs(scenario.get("results") or {}, current)
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Scenario comparison: {scenario.get('name') or 'saved run'}")
        layout = QVBoxLayout(dialog)
        saved_at = scenario.get("saved_at") or "unknown time"
        layout.addWidget(QLabel(f"Saved run ({saved_at}) compared with the current routes."))
        table = QTableWidget(len(rows), 5, dialog)
        table.setHorizontalHeaderLabels(["Profile", "Metric", "Saved", "Current", "Change"])

        def fmt(value: Any, unit: str) -> str:
            return "—" if value is None else f"{value:.2f} {unit}"

        for index, row in enumerate(rows):
            change = "—"
            if row["delta"] is not None:
                change = f"{row['delta']:+.2f} {row['unit']}"
                if row["delta_pct"] is not None:
                    change += f" ({row['delta_pct']:+.1f}%)"
            cells = (row["profile"], row["label"], fmt(row["saved"], row["unit"]), fmt(row["current"], row["unit"]), change)
            for column, text in enumerate(cells):
                table.setItem(index, column, QTableWidgetItem(text))
        stretch = getattr(getattr(QHeaderView, "ResizeMode", QHeaderView), "Stretch", 1)
        table.horizontalHeader().setSectionResizeMode(stretch)
        layout.addWidget(table)
        close_button = getattr(getattr(QDialogButtonBox, "StandardButton", QDialogButtonBox), "Close")
        buttons = QDialogButtonBox(close_button, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(640, 420)
        dialog.show()
        self._comparison_dialog = dialog
        return dialog

    def closeEvent(self, event: Any) -> None:
        # Hiding the panel must not destroy the user's work. Full teardown only
        # happens on plugin unload, which calls teardown() directly.
        self.teardown(remove_layers=False)
        super().closeEvent(event)
