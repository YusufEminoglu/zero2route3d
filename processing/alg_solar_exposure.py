"""Processing algorithm for Solar & Shade Exposure Simulation along 3D Route."""
from __future__ import annotations

from typing import Any, Dict

from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterNumber,
    QgsProcessingParameterVectorLayer,
    QgsWkbTypes,
)

from .route_input import extract_route_coords_3d
from .field_utils import DOUBLE, STRING, make_field
from .post_process import finalize_output
from ..core.solar_shadow import compute_shade_exposure_along_route


class SolarExposureAlgorithm(QgsProcessingAlgorithm):
    """Calculates time-of-day solar angle, direct sun percentage, and thermal comfort along 3D route."""

    INPUT_ROUTE = "INPUT_ROUTE"
    SOLAR_HOUR = "SOLAR_HOUR"
    OUTPUT = "OUTPUT"

    def createInstance(self) -> QgsProcessingAlgorithm:
        return SolarExposureAlgorithm()

    def name(self) -> str:
        return "solar_shade_exposure"

    def displayName(self) -> str:
        return "Solar & Shade Exposure Analysis (3D Route)"

    def group(self) -> str:
        return "3D Mobility & Routing"

    def groupId(self) -> str:
        return "route3d"

    def tags(self) -> list[str]:
        return ["solar", "sun", "shade", "thermal", "shadow", "microclimate", "heat", "aspect", "radiation", "comfort"]

    def shortHelpString(self) -> str:
        return "Calculates direct sunlight percentage, shaded length, and solar energy exposure along a 3D route at any given hour."

    def initAlgorithm(self, config: Dict[str, Any] = None) -> None:
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_ROUTE,
                "3D Route Layer (LineStringZ)",
                [QgsProcessing.TypeVectorLine],
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.SOLAR_HOUR,
                "Solar Hour of Day (06:00 to 20:00)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=14.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                "Solar Shade Exposure Summary",
                type=QgsProcessing.TypeVector,
            )
        )

    def processAlgorithm(
        self,
        parameters: Dict[str, Any],
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> Dict[str, Any]:
        layer = self.parameterAsVectorLayer(parameters, self.INPUT_ROUTE, context)
        solar_hour = self.parameterAsDouble(parameters, self.SOLAR_HOUR, context)

        fields = QgsFields()
        fields.append(make_field("solar_hour", DOUBLE))
        fields.append(make_field("azimuth_deg", DOUBLE))
        fields.append(make_field("elevation_deg", DOUBLE))
        fields.append(make_field("direct_sun_pct", DOUBLE))
        fields.append(make_field("shaded_pct", DOUBLE))
        fields.append(make_field("solar_kwh", DOUBLE))
        fields.append(make_field("comfort_class", STRING))

        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            QgsWkbTypes.NoGeometry,
            layer.sourceCrs(),
        )

        coords_3d = extract_route_coords_3d(layer, context, "input route layer")

        report = compute_shade_exposure_along_route(coords_3d, solar_hour=solar_hour)

        feat = QgsFeature(fields)
        feat.setAttributes([
            report.solar_position.solar_hour,
            report.solar_position.azimuth_deg,
            report.solar_position.elevation_deg,
            report.direct_sun_pct,
            report.shaded_pct,
            report.total_solar_exposure_kwh,
            report.comfort_category,
        ])
        sink.addFeature(feat, QgsFeatureSink.FastInsert)

        # Remembered so postProcessAlgorithm can resolve and style the layer.
        self._dest_id = dest_id
        return {self.OUTPUT: dest_id}

    def postProcessAlgorithm(
        self,
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> Dict[str, Any]:
        # The destination layer MUST be resolved through context.getMapLayer().
        # QgsProject.instance().mapLayer() returns None here, which turns every
        # styling and metadata call below into a silent no-op.
        finalize_output(
            context,
            getattr(self, "_dest_id", ""),
            title='Solar Exposure Along Route',
            abstract='Modelled solar geometry along the route for the given hour. Values derive from solar position and route orientation; no building massing is used.',
            aliases={'hour': 'Solar hour', 'sun_alt': 'Sun altitude (deg)', 'sun_az': 'Sun azimuth (deg)'},
            feedback=feedback,
        )
        return {}
