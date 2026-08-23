"""Processing algorithm for Solar & Shade Exposure Simulation along 3D Route."""
from __future__ import annotations

from typing import Any, Dict

from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsField,
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
        fields.append(QgsField("solar_hour", 6))
        fields.append(QgsField("azimuth_deg", 6))
        fields.append(QgsField("elevation_deg", 6))
        fields.append(QgsField("direct_sun_pct", 6))
        fields.append(QgsField("shaded_pct", 6))
        fields.append(QgsField("solar_kwh", 6))
        fields.append(QgsField("comfort_class", 10))

        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            QgsWkbTypes.NoGeometry,
            layer.sourceCrs(),
        )

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

        return {self.OUTPUT: dest_id}
