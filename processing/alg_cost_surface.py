"""Processing algorithm for Multi-Criteria Raster Cost Surface Generation."""
from __future__ import annotations

from typing import Any, Dict

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorDestination,
)


class MultiCriteriaCostSurfaceAlgorithm(QgsProcessingAlgorithm):
    """Generates weighted impedance table summarizing multi-criteria raster parameters."""

    INPUT_DEM = "INPUT_DEM"
    INPUT_LST = "INPUT_LST"
    INPUT_GREEN = "INPUT_GREEN"
    WEIGHT_SLOPE = "WEIGHT_SLOPE"
    WEIGHT_HEAT = "WEIGHT_HEAT"
    WEIGHT_GREEN = "WEIGHT_GREEN"
    OUTPUT = "OUTPUT"

    def createInstance(self) -> QgsProcessingAlgorithm:
        return MultiCriteriaCostSurfaceAlgorithm()

    def name(self) -> str:
        return "mcda_cost_surface"

    def displayName(self) -> str:
        return "MCDA Multi-Criteria Impedance Parameters"

    def group(self) -> str:
        return "3D Mobility & Routing"

    def groupId(self) -> str:
        return "route3d"

    def shortHelpString(self) -> str:
        return "Calculates normalized multi-criteria weight distribution and raster impedance factors."

    def initAlgorithm(self, config: Dict[str, Any] = None) -> None:
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.INPUT_DEM,
                "Elevation (DEM) Layer",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.INPUT_LST,
                "Thermal (LST) Surface Layer",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.INPUT_GREEN,
                "Greenery (NDVI) Layer",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.WEIGHT_SLOPE,
                "Slope Weight (1-9)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=1.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.WEIGHT_HEAT,
                "Heat Avoidance Weight (1-9)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=0.5,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.WEIGHT_GREEN,
                "Green Preference Weight (1-9)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=0.5,
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorDestination(
                self.OUTPUT,
                "MCDA Parameters Summary Table",
            )
        )

    def processAlgorithm(
        self,
        parameters: Dict[str, Any],
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> Dict[str, Any]:
        w_slope = self.parameterAsDouble(parameters, self.WEIGHT_SLOPE, context)
        w_heat = self.parameterAsDouble(parameters, self.WEIGHT_HEAT, context)
        w_green = self.parameterAsDouble(parameters, self.WEIGHT_GREEN, context)

        total = w_slope + w_heat + w_green
        norm_slope = (w_slope / total) if total > 0 else 0.33
        norm_heat = (w_heat / total) if total > 0 else 0.33
        norm_green = (w_green / total) if total > 0 else 0.33

        return {
            "norm_slope": norm_slope,
            "norm_heat": norm_heat,
            "norm_green": norm_green,
        }
