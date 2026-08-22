"""QGIS Processing Provider for 02Route 3D Studio."""
from __future__ import annotations

from typing import List

from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsProcessingAlgorithm, QgsProcessingProvider

from .alg_3d_isochrone import ServiceArea3DAlgorithm
from .alg_3d_route import Compute3DRouteAlgorithm
from .alg_batch_route import Batch3DRouteAlgorithm
from .alg_cost_surface import MultiCriteriaCostSurfaceAlgorithm
from .alg_export_html import ExportStandalone3DHtmlAlgorithm
from .alg_od_matrix import OriginDestinationMatrix3DAlgorithm
from .alg_walkability import WalkabilityAuditAlgorithm


class Route3DProcessingProvider(QgsProcessingProvider):
    """Processing Provider registering 3D route planning, isochrones, and analytics."""

    def loadAlgorithms(self) -> None:
        """Register all 02Route 3D processing algorithms."""
        self.addAlgorithm(Compute3DRouteAlgorithm())
        self.addAlgorithm(ServiceArea3DAlgorithm())
        self.addAlgorithm(OriginDestinationMatrix3DAlgorithm())
        self.addAlgorithm(WalkabilityAuditAlgorithm())
        self.addAlgorithm(Batch3DRouteAlgorithm())
        self.addAlgorithm(MultiCriteriaCostSurfaceAlgorithm())
        self.addAlgorithm(ExportStandalone3DHtmlAlgorithm())

    def id(self) -> str:
        return "zero2route3d"

    def name(self) -> str:
        return "02Route 3D"

    def icon(self) -> QIcon:
        return QIcon()
