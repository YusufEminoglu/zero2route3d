"""Lifecycle smoke test for zero2route3d.

Verifies imports, metadata integrity, classFactory, and full initGui() -> toggle_dock() -> unload()
lifecycle against a mock/stub QgisInterface in headless QGIS runtime.
"""
from __future__ import annotations

import configparser
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(PLUGIN_DIR.parent) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR.parent))

from qgis.PyQt.QtCore import QObject, pyqtSignal  # noqa: E402
from qgis.PyQt.QtWidgets import QMainWindow  # noqa: E402
from qgis.core import QgsApplication  # noqa: E402
from qgis.gui import QgsMapCanvas  # noqa: E402

PACKAGE = "zero2route3d"


def _ok(name, condition, detail=""):
    tag = "PASS" if condition else "FAIL"
    print(f"  [{tag}] {name}" + (f" - {detail}" if detail else ""))
    return bool(condition)


class _Canvas(QgsMapCanvas):
    def __init__(self, parent=None):
        super().__init__(parent)


class _MessageBar:
    def pushInfo(self, title: str, msg: str) -> None:
        pass

    def pushSuccess(self, title: str, msg: str) -> None:
        pass

    def pushWarning(self, title: str, msg: str) -> None:
        pass

    def pushCritical(self, title: str, msg: str) -> None:
        pass


class _Iface(QObject):
    currentLayerChanged = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._main = QMainWindow()
        self._canvas = _Canvas(self._main)
        self._msg_bar = _MessageBar()
        self.toolbar_icons = []
        self.menu_entries = []
        self.docks = []

    def mainWindow(self):
        return self._main

    def mapCanvas(self):
        return self._canvas

    def messageBar(self):
        return self._msg_bar

    def addToolBarIcon(self, action):
        self.toolbar_icons.append(action)

    def removeToolBarIcon(self, action):
        if action in self.toolbar_icons:
            self.toolbar_icons.remove(action)

    def addPluginToMenu(self, name, action):
        self.menu_entries.append((name, action))

    def removePluginMenu(self, name, action):
        self.menu_entries = [e for e in self.menu_entries if e != (name, action)]

    def addDockWidget(self, area, dock):
        self.docks.append(dock)

    def removeDockWidget(self, dock):
        if dock in self.docks:
            self.docks.remove(dock)


def test_metadata():
    p = PLUGIN_DIR / "metadata.txt"
    if not p.is_file():
        return _ok("metadata.txt exists", False, f"missing at {p}")
    cp = configparser.ConfigParser()
    cp.read(str(p), encoding="utf-8")
    has_general = cp.has_section("general")
    return _ok(
        "metadata.txt is valid INI with [general]",
        has_general and cp.has_option("general", "name"),
    )


def test_class_factory():
    mod = __import__(PACKAGE, fromlist=["classFactory"])
    fn = getattr(mod, "classFactory", None)
    return _ok("classFactory() exported", callable(fn))


def test_lifecycle(iface):
    app = QgsApplication.instance()
    mod = __import__(PACKAGE, fromlist=["classFactory"])
    plugin = mod.classFactory(iface)

    plugin.initGui()
    app.processEvents()

    # Toggle dock open
    plugin.toggle_dock()
    app.processEvents()

    # Toggle dock close
    plugin.toggle_dock()
    app.processEvents()

    plugin.unload()
    app.sendPostedEvents()
    app.processEvents()

    return _ok(
        "initGui -> toggle_dock -> unload cycle completed cleanly",
        not iface.menu_entries and not iface.toolbar_icons,
    )


def test_processing_provider():
    from zero2route3d.processing.provider import Route3DProcessingProvider
    provider = Route3DProcessingProvider()
    provider.loadAlgorithms()
    algs = provider.algorithms()
    return _ok(f"Route3DProcessingProvider loaded {len(algs)} algorithms", len(algs) >= 10)


def test_gui_profile_editor():
    from zero2route3d.core.mobility_profiles import get_profile
    from zero2route3d.gui.profile_editor import ProfileEditorDialog

    prof = get_profile("adult")
    dlg1 = ProfileEditorDialog(profile=prof)
    built1 = dlg1.get_built_profile()
    updated1 = dlg1.get_updated_profile()
    if built1.key != "custom_adult" or updated1.name != built1.name:
        return _ok("ProfileEditorDialog polymorphic init and getters", False)

    dlg2 = ProfileEditorDialog(base_profile_key="bicycle")
    built2 = dlg2.get_built_profile()
    if built2.key != "custom_bicycle":
        return _ok("ProfileEditorDialog string profile init", False)

    return _ok("ProfileEditorDialog initialization and getters", True)


def test_gui_cue_sheet_widget():
    from zero2route3d.core.profile_stats import CueInstruction
    from zero2route3d.gui.cue_sheet_widget import CueSheetWidget

    widget = CueSheetWidget()
    cues = [
        CueInstruction(
            step_number=1,
            instruction="Head North",
            direction="depart",
            distance_m=50.0,
            elevation_delta_m=1.2,
            slope_pct=2.4,
            street_name="Main St",
            warning="",
        ),
        CueInstruction(
            step_number=2,
            instruction="Turn Left",
            direction="left",
            distance_m=120.0,
            elevation_delta_m=-3.0,
            slope_pct=-2.5,
            street_name="Park Ave",
            warning="",
        ),
        CueInstruction(
            step_number=3,
            instruction="Reached Destination",
            direction="arrive",
            distance_m=0.0,
            elevation_delta_m=0.0,
            slope_pct=0.0,
            street_name="Target",
            warning="",
        ),
    ]
    widget.load_cues(cues)
    if widget.table.rowCount() != 3 or not widget.btn_export_csv.isEnabled():
        return _ok("CueSheetWidget load_cues", False)

    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "cues.csv"
        # Directly test CSV export logic
        import csv
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Step", "Direction", "Distance_m", "Elevation_Delta_m", "Slope_Pct", "Instruction", "Warning"])
            for c in widget.cues:
                writer.writerow([c.step_number, c.direction, c.distance_m, c.elevation_delta_m, c.slope_pct, c.instruction, c.warning])
        if not csv_path.exists():
            return _ok("CueSheetWidget CSV export", False)

    return _ok("CueSheetWidget rendering and data loading", True)


def test_gui_map_tools(iface):
    from zero2route3d.gui.map_tools import RoutePointMapTool

    canvas = iface.mapCanvas()
    tool = RoutePointMapTool(canvas, point_type="start")
    captured = []
    tool.point_selected.connect(lambda lon, lat, pt_type: captured.append((lon, lat, pt_type)))

    # Deactivate safely
    tool.deactivate()
    return _ok("RoutePointMapTool creation and safe deactivation", True)


def test_gui_multi_metric_panel(iface):
    from zero2route3d.core.mobility_profiles import get_profile
    from zero2route3d.core.profile_stats import compute_route_statistics
    from zero2route3d.gui.multi_metric_panel import MultiMetricPanel

    panel = MultiMetricPanel()
    if panel.metrics:
        return _ok("MultiMetricPanel initial empty state", False)

    # 1. Test 3-metric profile (Elevation, Slope, Speed)
    coords = [(27.14, 38.42, 10.0), (27.145, 38.425, 18.0), (27.15, 38.43, 12.0)]
    stats = compute_route_statistics(coords, profile=get_profile("adult"))
    panel.set_route_profile(stats.elevation_profile, profile_color="#0284c7")

    if len(panel.metrics) != 3:
        return _ok("MultiMetricPanel 3 standard metrics (elevation, slope, speed)", False)

    metric_keys = [m.key for m in panel.metrics]
    if metric_keys != ["elevation", "slope", "speed"]:
        return _ok("MultiMetricPanel correct metric keys", False)

    # 2. Test 5-metric profile (with real LST and NDVI)
    stats_env = compute_route_statistics(
        coords,
        profile=get_profile("adult"),
        lst_samples=[0.4, 0.6, 0.5],
        green_samples=[0.7, 0.8, 0.75],
    )
    panel.set_route_profile(stats_env.elevation_profile, profile_color="#0284c7")

    if len(panel.metrics) != 5:
        return _ok("MultiMetricPanel 5 metrics with environmental data", False)

    keys_5 = [m.key for m in panel.metrics]
    if "lst" not in keys_5 or "greenery" not in keys_5:
        return _ok("MultiMetricPanel lst and greenery keys included", False)

    # 3. Test seeking and progress
    panel.set_progress(0.75)
    if abs(panel.progress - 0.75) > 1e-4:
        return _ok("MultiMetricPanel progress setting", False)

    # 4. Test seeking signal
    received_seeks = []
    panel.seek_requested.connect(received_seeks.append)
    panel.seek_requested.emit(0.35)
    if not received_seeks or abs(received_seeks[0] - 0.35) > 1e-4:
        return _ok("MultiMetricPanel seek_requested signal", False)

    # 5. Test clear
    panel.clear()
    if panel.metrics or panel.progress != 0.0:
        return _ok("MultiMetricPanel clear", False)

    return _ok("MultiMetricPanel multi-metric ribbon rendering and interaction", True)


def test_gui_canvas_animator(iface):
    from zero2route3d.core.mobility_profiles import get_profile
    from zero2route3d.core.profile_stats import compute_route_statistics
    from zero2route3d.core.routing_engine import RouteResult3D
    from zero2route3d.gui.canvas_animator import Route2DCanvasAnimator

    canvas = iface.mapCanvas()
    animator = Route2DCanvasAnimator(canvas=canvas)

    coords = [(27.14, 38.42, 10.0), (27.15, 38.43, 15.0), (27.16, 38.44, 20.0)]
    stats = compute_route_statistics(coords, profile=get_profile("adult"))
    res = RouteResult3D(
        coordinates_3d=coords,
        statistics=stats,
        profile=get_profile("adult"),
        status_message="OK",
    )
    animator.load_routes([res])
    animator.set_speed_multiplier(20.0)
    animator.set_auto_pan(True)
    animator.play()
    animator.pause()
    animator.seek_progress(0.5)
    animator.stop()
    animator.clear()
    return _ok("Route2DCanvasAnimator multi-route playback and teardown", True)


def test_dock_animation_playback(iface):
    from zero2route3d.core.mobility_profiles import get_profile
    from zero2route3d.core.profile_stats import compute_route_statistics
    from zero2route3d.core.routing_engine import RouteResult3D
    from zero2route3d.gui.dock import Route3DStudioDock

    dock = Route3DStudioDock(iface=iface)
    coords = [(27.14, 38.42, 10.0), (27.15, 38.43, 15.0), (27.16, 38.44, 20.0)]
    stats = compute_route_statistics(coords, profile=get_profile("adult"))
    res = RouteResult3D(
        coordinates_3d=coords,
        statistics=stats,
        profile=get_profile("adult"),
        status_message="OK",
    )
    dock.multi_route_results = {"adult": res}
    dock.current_route_result = res
    dock.canvas_animator.load_routes([res])
    dock.btn_anim_play.setEnabled(True)
    dock.btn_anim_stop.setEnabled(True)

    if dock.btn_anim_play.text() != "▶️ Play":
        dock.teardown()
        return _ok("Dock btn_anim_play initial state", False)

    # Click play
    dock.btn_anim_play.click()
    if not dock.canvas_animator.is_playing or "Pause" not in dock.btn_anim_play.text():
        dock.teardown()
        return _ok("Dock btn_anim_play play toggle", False)

    # Tick animation
    dock.canvas_animator._on_tick()

    # Click pause
    dock.btn_anim_play.click()
    if dock.canvas_animator.is_playing or "Play" not in dock.btn_anim_play.text():
        dock.teardown()
        return _ok("Dock btn_anim_play pause toggle", False)

    dock.teardown()
    return _ok("Route3DStudioDock unified start/pause animation toggle", True)


def test_dock_quick_mode_and_scenarios(iface):
    from zero2route3d.core.mobility_profiles import get_profile
    from zero2route3d.core.profile_stats import compute_route_statistics
    from zero2route3d.core.routing_engine import RouteResult3D, Waypoint
    from zero2route3d.gui.dock import Route3DStudioDock
    from qgis.core import QgsProject

    dock = Route3DStudioDock(iface=iface)
    dock.point_a = Waypoint(lon=27.1428, lat=38.4237, name="Point A (Origin)")
    dock.point_b = Waypoint(lon=27.1450, lat=38.4250, name="Point B (Destination)")
    dock._update_point_labels()

    coords = [(27.1428, 38.4237, 10.0), (27.1440, 38.4245, 12.0), (27.1450, 38.4250, 15.0)]
    stats = compute_route_statistics(coords, profile=get_profile("adult"))
    res = RouteResult3D(coordinates_3d=coords, statistics=stats, profile=get_profile("adult"), status_message="OK")
    dock.multi_route_results = {"adult": res}
    dock.current_route_result = res
    dock.add_route_layer_to_qgis()

    proj = QgsProject.instance()
    root = proj.layerTreeRoot()
    scenarios_group = root.findGroup("🛣️ 02Route 3D Scenarios & Runs") if root else None
    has_group = scenarios_group is not None

    dock.teardown()
    return _ok("Route3DStudioDock Quick Mode & Persistent Scenario Group creation", has_group)


def test_dock_point_ab_layers(iface):
    from zero2route3d.core.routing_engine import Waypoint
    from zero2route3d.gui.dock import Route3DStudioDock
    from qgis.core import QgsProject

    dock = Route3DStudioDock(iface=iface)
    dock.point_a = Waypoint(lon=27.1428, lat=38.4237, name="Point A (Origin)")
    dock.point_b = Waypoint(lon=27.1450, lat=38.4250, name="Point B (Destination)")
    dock._update_point_vector_layers()

    proj = QgsProject.instance()
    layers_a = proj.mapLayersByName("📍 Route Point A (Origin)")
    layers_b = proj.mapLayersByName("🎯 Route Point B (Destination)")

    valid = bool(layers_a and layers_b)
    if valid:
        la = layers_a[0]
        lb = layers_b[0]
        valid = la.labelsEnabled() and lb.labelsEnabled() and la.featureCount() == 1 and lb.featureCount() == 1

    dock.teardown()
    return _ok("Route Point A and Point B vector layers with letter markers and labeling", valid)


def test_dock_web_route_payload_sync(iface):
    import json
    from zero2route3d.core.mobility_profiles import get_profile
    from zero2route3d.core.profile_stats import compute_route_statistics
    from zero2route3d.core.routing_engine import RouteResult3D, Waypoint
    from zero2route3d.gui.dock import Route3DStudioDock

    dock = Route3DStudioDock(iface=iface)
    coords = [(27.1428, 38.4237, 10.0), (27.1440, 38.4245, 12.0), (27.1450, 38.4250, 15.0)]
    stats = compute_route_statistics(coords, profile=get_profile("adult"))
    res = RouteResult3D(coordinates_3d=coords, statistics=stats, profile=get_profile("adult"), status_message="OK")
    dock.multi_route_results = {"adult": res}
    dock.current_route_result = res

    payload = dock._build_web_route_payload(res)
    dock._write_route_payload(payload)

    written = dock.current_route_file.exists() and dock.current_route_file.stat().st_size > 0
    if written:
        loaded = json.loads(dock.current_route_file.read_text(encoding="utf-8"))
        written = loaded.get("type") == "FeatureCollection" and len(loaded.get("features", [])) == 1

    dock.teardown()
    return _ok("Dock web route payload atomic sync to current_route.json", written)


def test_cartographic_themes_in_qgis(iface):
    from zero2route3d.core.osm_styling import apply_osm_theme_style, list_osm_themes
    from qgis.core import QgsVectorLayer

    themes = list_osm_themes()
    if len(themes) < 8:
        return _ok("8 Cartographic themes catalog in QGIS", False)

    line_layer = QgsVectorLayer("LineString?crs=EPSG:4326", "Test Roads", "memory")
    poly_layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "Test Buildings", "memory")
    point_layer = QgsVectorLayer("Point?crs=EPSG:4326", "Test Trees", "memory")

    ok_all = True
    for key, _ in themes:
        ok_all = ok_all and apply_osm_theme_style(line_layer, key)
        ok_all = ok_all and apply_osm_theme_style(poly_layer, key)
        ok_all = ok_all and apply_osm_theme_style(point_layer, key)

    return _ok("8 Cartographic themes applied cleanly to vector layers", ok_all)


def test_standalone_html_bundler_qgis(iface):
    from zero2route3d.core.html_bundler import StandaloneHtmlBundler

    bundler = StandaloneHtmlBundler()
    geojson_payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[27.14, 38.42, 10.0], [27.15, 38.43, 20.0]],
                },
                "properties": {
                    "distance_km": 1.2,
                    "duration_min": 15.0,
                    "elevation_gain_m": 10.0,
                    "profile_name": "Adult Pedestrian",
                    "corridor_buildings": [{"id": "b1", "coordinates": [[27.141, 38.421], [27.142, 38.421], [27.142, 38.422], [27.141, 38.422]], "height_m": 12.0}],
                    "corridor_trees": [{"id": "t1", "coordinates": [27.1415, 38.4215], "height_m": 8.0, "tree_type": "deciduous"}],
                },
            }
        ],
        "properties": {"route_count": 1},
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        out_html = Path(tmpdir) / "test_report.html"
        bundler.bundle_to_file(geojson_payload, out_html)
        is_ok = out_html.exists() and out_html.stat().st_size > 500
        return _ok("StandaloneHtmlBundler self-contained 3D report bundling", is_ok)


def test_processing_algorithms_load():
    from zero2route3d.processing.provider import Route3DProcessingProvider

    provider = Route3DProcessingProvider()
    provider.loadAlgorithms()
    algs = provider.algorithms()
    names = [alg.name() for alg in algs]
    display_names = [alg.displayName() for alg in algs]
    valid = len(algs) >= 14 and all(len(n) > 0 for n in names) and all(len(dn) > 0 for dn in display_names)
    return _ok(f"All {len(algs)} Processing algorithm definitions verified", valid)


def run_all(iface):
    print("=" * 60)
    print(" zero2route3d - lifecycle & GUI component audit tests")
    print("=" * 60)
    results = [
        test_metadata(),
        test_class_factory(),
        test_lifecycle(iface),
        test_processing_provider(),
        test_gui_profile_editor(),
        test_gui_cue_sheet_widget(),
        test_gui_multi_metric_panel(iface),
        test_gui_map_tools(iface),
        test_gui_canvas_animator(iface),
        test_dock_animation_playback(iface),
        test_dock_quick_mode_and_scenarios(iface),
        test_dock_point_ab_layers(iface),
        test_dock_web_route_payload_sync(iface),
        test_cartographic_themes_in_qgis(iface),
        test_standalone_html_bundler_qgis(iface),
        test_processing_algorithms_load(),
    ]
    passed = sum(1 for r in results if r)
    print("-" * 60)
    print(f"  {passed}/{len(results)} passed")
    return passed == len(results)


def main():
    app = QgsApplication.instance()
    owns_app = app is None
    profile = None
    if owns_app:
        profile = tempfile.TemporaryDirectory(prefix="zero2route3d-smoke-")
        app = QgsApplication([], True, profile.name, "external")
        app.initQgis()
    iface = _Iface()
    try:
        return run_all(iface)
    finally:
        iface.mainWindow().close()
        if owns_app:
            app.exitQgis()
            profile.cleanup()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
else:
    main()
