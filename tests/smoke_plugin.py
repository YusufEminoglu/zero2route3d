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
from qgis.PyQt.QtWidgets import QMainWindow, QToolBar  # noqa: E402
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


def run_all(iface):
    print("=" * 60)
    print(" zero2route3d - lifecycle smoke")
    print("=" * 60)
    results = [
        test_metadata(),
        test_class_factory(),
        test_lifecycle(iface),
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
