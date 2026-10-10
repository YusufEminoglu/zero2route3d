"""Processing algorithm for Solar & Shade Exposure Simulation along 3D Route."""
from __future__ import annotations

from typing import Any, Dict

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterVectorLayer,
    QgsWkbTypes,
)

from .route_input import extract_route_coords_3d
from .field_utils import DOUBLE, STRING, make_field
from .post_process import finalize_output
from ..core.building_shadow import Building
from ..core.solar_shadow import compute_shade_exposure_along_route

# Height used for a building with no height or level count (three storeys).
DEFAULT_BUILDING_HEIGHT_M = 9.0
HEIGHT_FIELD_GUESSES = ("height", "height_m", "building_height", "bldg_height")
LEVEL_FIELD_GUESSES = ("building:levels", "building_levels", "levels", "floors")


def _number(value: Any) -> Any:
    try:
        text = str(value).strip().lower().replace("m", "").replace(",", ".")
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


class SolarExposureAlgorithm(QgsProcessingAlgorithm):
    """Calculates time-of-day solar angle, direct sun percentage, and thermal comfort along 3D route."""

    INPUT_ROUTE = "INPUT_ROUTE"
    SOLAR_HOUR = "SOLAR_HOUR"
    DAY_OF_YEAR = "DAY_OF_YEAR"
    BUILDINGS = "BUILDINGS"
    HEIGHT_FIELD = "HEIGHT_FIELD"
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
        return (
            "Calculates direct sunlight percentage, shaded length and solar energy "
            "exposure along a 3D route at a given solar hour and day of the year.\n\n"
            "With a building layer, shade is computed geometrically: from every 5 m "
            "of route a ray goes toward the sun, and the point is shaded where a "
            "building it crosses is taller than the sun line at that distance. "
            "Heights come from the chosen field, else a height or building:levels "
            "field (levels x 3 m), else 9 m; the number of assumed heights is "
            "reported. Without buildings, shade is only estimated from street "
            "orientation (the 'method' field says which)."
        )

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
            QgsProcessingParameterNumber(
                self.DAY_OF_YEAR,
                "Day of year (1-365; 172 = 21 June, 355 = 21 December)",
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=172,
                minValue=1,
                maxValue=366,
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.BUILDINGS,
                "Building footprints (optional, for real shadows)",
                [QgsProcessing.TypeVectorPolygon],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.HEIGHT_FIELD,
                "Building height field in metres (optional)",
                parentLayerParameterName=self.BUILDINGS,
                type=QgsProcessingParameterField.Numeric,
                optional=True,
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
        day_of_year = self.parameterAsInt(parameters, self.DAY_OF_YEAR, context) or 172
        building_layer = self.parameterAsVectorLayer(parameters, self.BUILDINGS, context)
        height_field = self.parameterAsString(parameters, self.HEIGHT_FIELD, context)

        fields = QgsFields()
        fields.append(make_field("solar_hour", DOUBLE))
        fields.append(make_field("azimuth_deg", DOUBLE))
        fields.append(make_field("elevation_deg", DOUBLE))
        fields.append(make_field("direct_sun_pct", DOUBLE))
        fields.append(make_field("shaded_pct", DOUBLE))
        fields.append(make_field("solar_kwh", DOUBLE))
        fields.append(make_field("comfort_class", STRING))
        fields.append(make_field("method", STRING))
        fields.append(make_field("buildings", DOUBLE))

        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            QgsWkbTypes.NoGeometry,
            layer.sourceCrs(),
        )

        coords_3d = extract_route_coords_3d(layer, context, "input route layer")

        buildings = self._read_buildings(building_layer, height_field, context, feedback) if building_layer else []
        report = compute_shade_exposure_along_route(
            coords_3d, solar_hour=solar_hour, buildings=buildings or None, day_of_year=day_of_year
        )

        feat = QgsFeature(fields)
        feat.setAttributes([
            report.solar_position.solar_hour,
            report.solar_position.azimuth_deg,
            report.solar_position.elevation_deg,
            report.direct_sun_pct,
            report.shaded_pct,
            report.total_solar_exposure_kwh,
            report.comfort_category,
            report.method,
            float(len(buildings)),
        ])
        sink.addFeature(feat, QgsFeatureSink.FastInsert)

        # Remembered so postProcessAlgorithm can resolve and style the layer.
        self._dest_id = dest_id
        return {self.OUTPUT: dest_id}

    def _read_buildings(self, layer: Any, height_field: str, context: QgsProcessingContext,
                        feedback: QgsProcessingFeedback) -> list:
        """Footprints in WGS84 with heights; reports how many heights were assumed."""
        to_wgs84 = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), context.transformContext())
        names = {f.name().lower(): f.name() for f in layer.fields()}
        height_name = height_field or next((names[n] for n in HEIGHT_FIELD_GUESSES if n in names), None)
        level_name = next((names[n] for n in LEVEL_FIELD_GUESSES if n in names), None)
        buildings, assumed = [], 0
        for feat in layer.getFeatures():
            if feedback.isCanceled():
                break
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            geom.transform(to_wgs84)
            height = _number(feat[height_name]) if height_name else None
            if height is None and level_name:
                levels = _number(feat[level_name])
                height = levels * 3.0 if levels else None
            if height is None:
                height = DEFAULT_BUILDING_HEIGHT_M
                assumed += 1
            parts = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
            for polygon in parts:
                if polygon and len(polygon[0]) >= 3:
                    buildings.append(Building([(p.x(), p.y()) for p in polygon[0]], height))
        if assumed:
            feedback.pushWarning(
                f"{assumed} building(s) had no height or level count; {DEFAULT_BUILDING_HEIGHT_M:.0f} m was assumed."
            )
        return buildings

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
            abstract='Sun and shade along the route for the given solar hour and day. With a building layer, shade comes from building shadows; without, it is a street-orientation estimate (see the method field).',
            aliases={'solar_hour': 'Solar hour', 'elevation_deg': 'Sun altitude (deg)', 'azimuth_deg': 'Sun azimuth (deg)',
                     'method': 'Shade method', 'buildings': 'Buildings used'},
            feedback=feedback,
        )
        return {}
