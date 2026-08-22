"""Processing Provider for 02Route 3D algorithms."""
from __future__ import annotations

from pathlib import Path
from typing import List

from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsProcessingAlgorithm, QgsProcessingProvider

from .alg_3d_route import Compute3DRouteAlgorithm
from .alg_3d_isochrone import ServiceArea3DAlgorithm


class Route3DProcessingProvider(QgsProcessingProvider):
    """Processing provider exposing 02Route 3D algorithms to QGIS Toolbox and Modeler."""

    def loadAlgorithms(self) -> None:
        self.addAlgorithm(Compute3DRouteAlgorithm())
        self.addAlgorithm(ServiceArea3DAlgorithm())

    def id(self) -> str:
        return "zero2route3d"

    def name(self) -> str:
        return "02Route 3D Studio"

    def icon(self) -> QIcon:
        icon_path = Path(__file__).resolve().parent.parent / "icons" / "icon.png"
        return QIcon(str(icon_path)) if icon_path.exists() else QIcon()

    def longName(self) -> str:
        return "02Route 3D — Multi-Criteria 3D Mobility Studio"
