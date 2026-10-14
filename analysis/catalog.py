"""The sixteen analysis tools and the order the dock presents them in.

Every tool is a QGIS algorithm class internally. That is deliberate: it gives each
tool a validated parameter model, cancellable background execution and native
layer output without a second implementation. They are *not* registered with the
Processing registry, so nothing from this plugin appears in the Processing
Toolbox; the dock's Tools tab is the only entry point.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple, Type

from .alg_3d_isochrone import ServiceArea3DAlgorithm
from .alg_3d_map_matching import MapMatching3DAlgorithm
from .alg_3d_route import Compute3DRouteAlgorithm
from .alg_accessibility_equity import AccessibilityEquityAlgorithm
from .alg_batch_route import Batch3DRouteAlgorithm
from .alg_cost_surface import MultiCriteriaCostSurfaceAlgorithm
from .alg_evacuation import EvacuationRoutingAlgorithm
from .alg_export_dxf import ExportRouteToDxfAlgorithm
from .alg_export_html import ExportStandalone3DHtmlAlgorithm
from .alg_network_audit import RoutingNetworkAuditAlgorithm
from .alg_od_matrix import OriginDestinationMatrix3DAlgorithm
from .alg_pareto_routes import Pareto3DRoutesAlgorithm
from .alg_report_generator import GenerateAnalyticalReportAlgorithm
from .alg_solar_exposure import SolarExposureAlgorithm
from .alg_transit_route import TransitRoute3DAlgorithm
from .alg_walkability import WalkabilityAuditAlgorithm

# (category key, category title, tool classes in display order)
CATEGORIES: Tuple[Tuple[str, str, Tuple[Type[Any], ...]], ...] = (
    (
        "routing",
        "Routing",
        (
            Compute3DRouteAlgorithm,
            TransitRoute3DAlgorithm,
            Pareto3DRoutesAlgorithm,
            Batch3DRouteAlgorithm,
            OriginDestinationMatrix3DAlgorithm,
            EvacuationRoutingAlgorithm,
        ),
    ),
    (
        "accessibility",
        "Accessibility",
        (
            ServiceArea3DAlgorithm,
            AccessibilityEquityAlgorithm,
            WalkabilityAuditAlgorithm,
            SolarExposureAlgorithm,
        ),
    ),
    (
        "network",
        "Network and data",
        (
            RoutingNetworkAuditAlgorithm,
            MultiCriteriaCostSurfaceAlgorithm,
            MapMatching3DAlgorithm,
        ),
    ),
    (
        "reports",
        "Reports and export",
        (
            GenerateAnalyticalReportAlgorithm,
            ExportStandalone3DHtmlAlgorithm,
            ExportRouteToDxfAlgorithm,
        ),
    ),
)


def tool_classes() -> List[Type[Any]]:
    """Every tool class, in dock order."""
    return [cls for _key, _title, classes in CATEGORIES for cls in classes]


def tool_names() -> List[str]:
    """The stable algorithm names of every tool, in dock order."""
    return [cls().name() for cls in tool_classes()]


def category_of(tool_name: str) -> str:
    """The category key a tool is listed under, or an empty string."""
    for key, _title, classes in CATEGORIES:
        if any(cls().name() == tool_name for cls in classes):
            return key
    return ""


def create_tool(tool_name: str) -> Any:
    """A fresh, initialised algorithm instance for ``tool_name``.

    ``create()`` runs ``initAlgorithm`` so the parameter model is populated even
    though the class never went through a Processing provider.
    """
    for cls in tool_classes():
        if cls().name() == tool_name:
            return cls().create()
    raise KeyError(f"unknown analysis tool: {tool_name!r}")


def tools_by_category() -> Dict[str, List[Any]]:
    """Fresh algorithm instances grouped by category key (insertion-ordered)."""
    return {key: [cls().create() for cls in classes] for key, _title, classes in CATEGORIES}
