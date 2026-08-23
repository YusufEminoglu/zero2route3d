"""Processing algorithm to Export 3D Route to AutoCAD DXF Format."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterVectorLayer,
)

from .route_input import extract_route_coords_3d
from ..core.profile_dxf import export_route_to_dxf_3d


class ExportRouteToDxfAlgorithm(QgsProcessingAlgorithm):
    """Exports a 3D Route layer to standard AutoCAD DXF format with vertical profile."""

    INPUT_ROUTE = "INPUT_ROUTE"
    INCLUDE_PROFILE = "INCLUDE_PROFILE"
    V_SCALE = "V_SCALE"
    OUTPUT_DXF = "OUTPUT_DXF"

    def createInstance(self) -> QgsProcessingAlgorithm:
        return ExportRouteToDxfAlgorithm()

    def name(self) -> str:
        return "export_3d_route_dxf"

    def displayName(self) -> str:
        return "Export 3D Route to AutoCAD DXF (3D Polyline + Profile)"

    def group(self) -> str:
        return "3D Mobility & Routing"

    def groupId(self) -> str:
        return "route3d"

    def tags(self) -> list[str]:
        return ["dxf", "autocad", "cad", "export", "3d polyline", "elevation profile", "cross section", "vector export", "engineering"]

    def shortHelpString(self) -> str:
        return "Exports 3D LineStringZ route geometries and cross-sectional elevation profile drawings into AutoCAD DXF format."

    def initAlgorithm(self, config: Dict[str, Any] = None) -> None:
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_ROUTE,
                "3D Route Layer (LineStringZ)",
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.INCLUDE_PROFILE,
                "Include Longitudinal Profile Drawing",
                defaultValue=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.V_SCALE,
                "Profile Vertical Exaggeration Scale",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=5.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.OUTPUT_DXF,
                "Output DXF File",
                fileFilter="AutoCAD DXF Files (*.dxf)",
            )
        )

    def processAlgorithm(
        self,
        parameters: Dict[str, Any],
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> Dict[str, Any]:
        layer = self.parameterAsVectorLayer(parameters, self.INPUT_ROUTE, context)
        include_prof = self.parameterAsBool(parameters, self.INCLUDE_PROFILE, context)
        v_scale = self.parameterAsDouble(parameters, self.V_SCALE, context)
        out_path = self.parameterAsFileOutput(parameters, self.OUTPUT_DXF, context)

        coords_3d = extract_route_coords_3d(layer, context, "input route layer")

        export_route_to_dxf_3d(
            coords_3d=coords_3d,
            target_path=Path(out_path),
            include_profile_section=include_prof,
            profile_vertical_scale=v_scale,
        )

        return {self.OUTPUT_DXF: out_path}
