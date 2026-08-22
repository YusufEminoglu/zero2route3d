"""02Route 3D Main Plugin Entry Point."""
from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any, Optional

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication

from .gui.dock import Route3DStudioDock
from .processing.provider import Route3DProcessingProvider


class Route3DPlugin:
    """Main QGIS plugin coordinator for 02Route 3D Studio."""

    def __init__(self, iface: Any) -> None:
        self.iface = iface
        self.dock: Optional[Route3DStudioDock] = None
        self.action: Optional[QAction] = None
        self.provider: Optional[Route3DProcessingProvider] = None
        self.plugin_dir = Path(__file__).resolve().parent

    def initGui(self) -> None:
        """Initialize plugin GUI action, dock widget, and processing provider."""
        icon_path = self.plugin_dir / "icons" / "icon.png"
        icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()

        self.action = QAction(icon, "02Route 3D Studio", self.iface.mainWindow())
        self.action.setObjectName("actionZero2Route3D")
        self.action.triggered.connect(self.toggle_dock)

        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&02Route 3D", self.action)

        # Initialize Processing Provider
        self.provider = Route3DProcessingProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initProcessing(self) -> None:
        """Called by QGIS when processing is initialized."""
        if self.provider is None:
            self.provider = Route3DProcessingProvider()
            QgsApplication.processingRegistry().addProvider(self.provider)

    def toggle_dock(self) -> None:
        """Open or toggle visibility of the 3D Studio dock panel."""
        _RightDock = getattr(getattr(Qt, "DockWidgetArea", Qt), "RightDockWidgetArea", getattr(Qt, "RightDockWidgetArea", 2))
        if self.dock is None:
            parent_window = self.iface.mainWindow() if self.iface else None
            self.dock = Route3DStudioDock(iface=self.iface, parent=parent_window)
            if self.iface:
                self.iface.addDockWidget(_RightDock, self.dock)
            self.dock.show()
            self.dock.raise_()
            self.dock.activateWindow()
        else:
            should_show = not self.dock.isVisible()
            self.dock.setVisible(should_show)
            if should_show:
                self.dock.show()
                self.dock.raise_()
                self.dock.activateWindow()

    def unload(self) -> None:
        """Tear down GUI elements and deregister processing provider."""
        if self.action is not None:
            self.iface.removePluginMenu("&02Route 3D", self.action)
            self.iface.removeToolBarIcon(self.action)

        if self.dock is not None:
            with contextlib.suppress(Exception):
                self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None

        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
