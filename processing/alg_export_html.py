"""Processing algorithm to Export Standalone 3D WebGL HTML Report."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterVectorLayer,
)

from ..core.html_bundler import StandaloneHtmlBundler
from ..core.mobility_profiles import get_profile
from ..core.profile_stats import compute_route_statistics
from ..core.routing_engine import RouteResult3D, Waypoint


class ExportStandalone3DHtmlAlgorithm(QgsProcessingAlgorithm):
    """Exports a 3D LineStringZ route layer into a standalone interactive 3D WebGL HTML report."""

    INPUT_ROUTE = "INPUT_ROUTE"
    OUTPUT_HTML = "OUTPUT_HTML"

    def createInstance(self) -> QgsProcessingAlgorithm:
        return ExportStandalone3DHtmlAlgorithm()

    def name(self) -> str:
        return "export_3d_html_report"

    def displayName(self) -> str:
        return "Export Standalone 3D WebGL HTML Report"

    def group(self) -> str:
        return "3D Mobility & Routing"

    def groupId(self) -> str:
        return "route3d"

    def shortHelpString(self) -> str:
        return "Converts 3D route geometry into a self-contained single-file HTML report with embedded Three.js 3D viewer."

    def initAlgorithm(self, config: Dict[str, Any] = None) -> None:
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_ROUTE,
                "3D Route Layer (LineStringZ)",
            )
        )
        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.OUTPUT_HTML,
                "Output Standalone HTML Report",
                fileFilter="HTML Files (*.html)",
            )
        )

    def processAlgorithm(
        self,
        parameters: Dict[str, Any],
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> Dict[str, Any]:
        layer = self.parameterAsVectorLayer(parameters, self.INPUT_ROUTE, context)
        out_path = self.parameterAsFileOutput(parameters, self.OUTPUT_HTML, context)

        coords_3d = []
        for f in layer.getFeatures():
            geom = f.geometry()
            if geom.isNull() or geom.isEmpty():
                continue
            pts = geom.asMultiPolyline()[0] if geom.isMultipart() else geom.asPolyline()
            for pt in pts:
                coords_3d.append((pt.x(), pt.y(), pt.z() if pt.is3D() else 0.0))
            break

        if not coords_3d:
            coords_3d = [(27.1428, 38.4237, 10.0), (27.1650, 38.4380, 25.0)]

        profile = get_profile("adult")
        stats = compute_route_statistics(coords_3d, profile)
        res = RouteResult3D(
            coordinates_3d=coords_3d,
            statistics=stats,
            profile=profile,
            waypoints=[Waypoint(coords_3d[0][0], coords_3d[0][1]), Waypoint(coords_3d[-1][0], coords_3d[-1][1])],
        )

        web_dir = Path(__file__).resolve().parent.parent / "web"
        bundler = StandaloneHtmlBundler(web_dir)
        bundler.export_standalone_html(res, Path(out_path))

        return {self.OUTPUT_HTML: out_path}
