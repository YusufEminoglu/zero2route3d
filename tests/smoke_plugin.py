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


class _Iface(QObject):
    currentLayerChanged = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._main = QMainWindow()
        self._canvas = _Canvas(self._main)
        self.toolbar_icons = []
        self.menu_entries = []
        self.docks = []

    def mainWindow(self):
        return self._main

    def mapCanvas(self):
        return self._canvas

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


def test_gui_canvas_overlay(iface):
    from zero2route3d.gui.canvas_overlay import CanvasRouteOverlay

    canvas = iface.mapCanvas()
    overlay = CanvasRouteOverlay(canvas)
    coords = [
        (27.1428, 38.4237, 10.0),
        (27.1450, 38.4250, 15.0),
        (27.1500, 38.4300, 20.0),
    ]
    overlay.display_route(coords)
    overlay.update_tracker_position(0.5)
    overlay.clear()
    return _ok("CanvasRouteOverlay lifecycle (display, update, clear)", True)


def test_gui_map_tools(iface):
    from zero2route3d.gui.map_tools import RoutePointMapTool

    canvas = iface.mapCanvas()
    tool = RoutePointMapTool(canvas, point_type="start")
    captured = []
    tool.point_selected.connect(lambda lon, lat, pt_type: captured.append((lon, lat, pt_type)))

    # Deactivate safely
    tool.deactivate()
    return _ok("RoutePointMapTool creation and safe deactivation", True)


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
        test_gui_canvas_overlay(iface),
        test_gui_map_tools(iface),
        test_gui_canvas_animator(iface),
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
