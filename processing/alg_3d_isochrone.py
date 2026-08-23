"""QGIS Processing Algorithm for 3D Travel-Time Service Areas / Isochrones."""
from __future__ import annotations

import math
from typing import Any, Dict

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingException,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterNumber,
    QgsProcessingParameterPoint,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorLayer,
    QgsWkbTypes,
)

from ..core.environmental_raster import EnvironmentalSurfaceSampler
from ..core.isochrone_engine import IsochroneEngine3D
from ..core.mobility_profiles import list_profile_keys, get_profile
from ..core.network_source import NetworkSourceManager
from ..core.routing_engine import RoutingEngine3D, Waypoint


class ServiceArea3DAlgorithm(QgsProcessingAlgorithm):
    """Generates 3D kinematic travel-time service area buffers."""

    CENTER_POINT = "CENTER_POINT"
    PROFILE = "PROFILE"
    TIME_MINUTES = "TIME_MINUTES"
    DEM_LAYER = "DEM_LAYER"
    NETWORK_LAYER = "NETWORK_LAYER"
    OUTPUT = "OUTPUT"

    def initAlgorithm(self, config: Dict[str, Any] = None) -> None:
        self.addParameter(
            QgsProcessingParameterPoint(
                self.CENTER_POINT,
                "Origin Facility Point",
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.TIME_MINUTES,
                "Travel Time Limit (Minutes)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=10.0,
                minValue=1.0,
                maxValue=120.0,
            )
        )

        profile_options = [
            "Standard Adult (5 km/h)",
            "Senior / Elderly (3.2 km/h)",
            "Child / Safe Walk",
            "Stroller / Pram (Max 6% slope)",
            "Wheelchair / Barrier-Free (Max 5% slope)",
            "City Bicycle (18 km/h)",
            "E-Scooter (20 km/h)",
            "Passenger Car",
            "Heavy Freight Truck",
        ]
        self.addParameter(
            QgsProcessingParameterEnum(
                self.PROFILE,
                "Mobility Profile",
                options=profile_options,
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.DEM_LAYER,
                "DEM Raster (Optional)",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.NETWORK_LAYER,
                "Road Network (Line Layer)",
                [QgsProcessing.TypeVectorLine],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                "3D Isochrone Polygon",
                type=QgsProcessing.TypeVectorPolygon,
            )
        )

    def processAlgorithm(
        self,
        parameters: Dict[str, Any],
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> Dict[str, Any]:
        center = self.parameterAsPoint(parameters, self.CENTER_POINT, context)
        time_min = self.parameterAsDouble(parameters, self.TIME_MINUTES, context)
        prof_idx = self.parameterAsEnum(parameters, self.PROFILE, context)
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM_LAYER, context)
        net_layer = self.parameterAsVectorLayer(parameters, self.NETWORK_LAYER, context)

        profile_keys = list_profile_keys()
        profile_key = profile_keys[max(0, min(prof_idx, len(profile_keys) - 1))]
        profile = get_profile(profile_key)

        feedback.setProgressText("Loading the real road network...")
        speed_m_per_min = (profile.base_speed_kmh * 1000.0) / 60.0
        max_radius_m = max(100.0, time_min * speed_m_per_min)
        lat_rad = math.radians(center.y())
        deg_lat = max_radius_m / 110574.0
        deg_lon = max_radius_m / max(1.0, 111320.0 * math.cos(lat_rad))
        bbox = (center.x() - deg_lon, center.y() - deg_lat, center.x() + deg_lon, center.y() + deg_lat)

        try:
            segments = NetworkSourceManager().require_segments(vector_layer=net_layer, bbox=bbox)
        except Exception as exc:
            raise QgsProcessingException(str(exc)) from exc

        engine = RoutingEngine3D(sampler=EnvironmentalSurfaceSampler(dem_layer=dem_layer))
        engine.build_graph(segments)
        iso_result = IsochroneEngine3D(engine).compute_isochrones(
            Waypoint(center.x(), center.y(), name="Origin"),
            profile_key=profile_key,
            time_intervals_min=(time_min,),
        )

        fields = QgsFields()
        fields.append(QgsField("profile", 10))
        fields.append(QgsField("time_min", 6))
        fields.append(QgsField("reach_km", 6))

        crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            QgsWkbTypes.Polygon,
            crs_wgs84,
        )

        for band in iso_result.bands:
            if len(band.boundary_points) < 3:
                continue
            points = [QgsPointXY(x, y) for x, y in band.boundary_points]
            geom = QgsGeometry.fromMultiPointXY(points).convexHull()
            if geom.isNull() or geom.isEmpty():
                continue
            feat = QgsFeature(fields)
            feat.setGeometry(geom)
            reach_km = math.sqrt(max(0.0, band.approx_area_ha) * 10000.0 / math.pi) / 1000.0
            feat.setAttributes([profile.name, band.time_cutoff_min, round(reach_km, 2)])
            sink.addFeature(feat, QgsFeatureSink.FastInsert)

        return {self.OUTPUT: dest_id}

    def name(self) -> str:
        return "generate_3d_isochrone"

    def displayName(self) -> str:
        return "Generate 3D Isochrone (Travel-Time Service Area)"

    def group(self) -> str:
        return "3D Mobility & Routing"

    def groupId(self) -> str:
        return "mobility3d"

    def createInstance(self) -> ServiceArea3DAlgorithm:
        return ServiceArea3DAlgorithm()
