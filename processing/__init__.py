"""02Route 3D Processing Package."""
from __future__ import annotations

from .alg_3d_isochrone import ServiceArea3DAlgorithm
from .alg_3d_route import Compute3DRouteAlgorithm
from .alg_batch_route import Batch3DRouteAlgorithm
from .alg_cost_surface import MultiCriteriaCostSurfaceAlgorithm
from .alg_evacuation import EvacuationRoutingAlgorithm
from .alg_export_dxf import ExportRouteToDxfAlgorithm
from .alg_export_html import ExportStandalone3DHtmlAlgorithm
from .alg_od_matrix import OriginDestinationMatrix3DAlgorithm
from .alg_report_generator import GenerateAnalyticalReportAlgorithm
from .alg_solar_exposure import SolarExposureAlgorithm
from .alg_walkability import WalkabilityAuditAlgorithm
from .provider import Route3DProcessingProvider

__all__ = [
    "Batch3DRouteAlgorithm",
    "Compute3DRouteAlgorithm",
    "EvacuationRoutingAlgorithm",
    "ExportRouteToDxfAlgorithm",
    "ExportStandalone3DHtmlAlgorithm",
    "GenerateAnalyticalReportAlgorithm",
    "MultiCriteriaCostSurfaceAlgorithm",
    "OriginDestinationMatrix3DAlgorithm",
    "Route3DProcessingProvider",
    "ServiceArea3DAlgorithm",
    "SolarExposureAlgorithm",
    "WalkabilityAuditAlgorithm",
]
