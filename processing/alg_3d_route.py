"""QGIS Processing Algorithm for Multi-Criteria 3D Route Calculation."""
from __future__ import annotations

from typing import Any, Dict

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPoint,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterPoint,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorLayer,
    QgsWkbTypes,
)

from ..core.environmental_raster import EnvironmentalSurfaceSampler, MCDAWeights
from ..core.mobility_profiles import list_profile_keys, get_profile
from ..core.network_source import NetworkSourceManager
from ..core.routing_engine import RoutingEngine3D, Waypoint


class Compute3DRouteAlgorithm(QgsProcessingAlgorithm):
    """Computes a multi-criteria 3D least-cost route between two points."""

    START_POINT = "START_POINT"
    END_POINT = "END_POINT"
    PROFILE = "PROFILE"
    DEM_LAYER = "DEM_LAYER"
    NETWORK_LAYER = "NETWORK_LAYER"
    OUTPUT = "OUTPUT"

    def initAlgorithm(self, config: Dict[str, Any] = None) -> None:
        self.addParameter(
            QgsProcessingParameterPoint(
                self.START_POINT,
                "Start Point (Origin A)",
            )
        )
        self.addParameter(
            QgsProcessingParameterPoint(
                self.END_POINT,
                "Destination Point (Dest B)",
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
            "Heavy Freight Truck (Max 7% slope)",
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
                "Digital Elevation Model (DEM Raster)",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.NETWORK_LAYER,
                "Custom Road Network Layer",
                types=[QgsProcessing.TypeVectorLine],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                "3D Route Layer",
                type=QgsProcessing.TypeVectorLine,
            )
        )

    def processAlgorithm(
        self,
        parameters: Dict[str, Any],
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> Dict[str, Any]:
        p1 = self.parameterAsPoint(parameters, self.START_POINT, context)
        p2 = self.parameterAsPoint(parameters, self.END_POINT, context)
        prof_idx = self.parameterAsEnum(parameters, self.PROFILE, context)
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM_LAYER, context)
        net_layer = self.parameterAsVectorLayer(parameters, self.NETWORK_LAYER, context)

        profile_keys = list_profile_keys()
        profile_key = profile_keys[prof_idx] if prof_idx < len(profile_keys) else "adult"
        profile = get_profile(profile_key)

        feedback.setProgressText("Initializing 3D spatial surfaces and road network...")
        sampler = EnvironmentalSurfaceSampler(dem_layer=dem_layer)
        net_mgr = NetworkSourceManager()

        bbox = (min(p1.x(), p2.x()), min(p1.y(), p2.y()), max(p1.x(), p2.x()), max(p1.y(), p2.y()))
        if net_layer:
            segments = net_mgr.extract_from_qgis_layer(net_layer)
        else:
            segments = net_mgr.fetch_osm_network_bbox(bbox)

        engine = RoutingEngine3D(sampler=sampler, weights=MCDAWeights())
        engine.build_graph(segments)

        feedback.setProgressText(f"Computing 3D path for {profile.name}...")
        w1 = Waypoint(lon=p1.x(), lat=p1.y())
        w2 = Waypoint(lon=p2.x(), lat=p2.y())
        res = engine.calculate_route([w1, w2], profile_key=profile_key)

        # Output Fields
        fields = QgsFields()
        fields.append(QgsField("profile", 10))
        fields.append(QgsField("dist_km", 6))
        fields.append(QgsField("time_min", 6))
        fields.append(QgsField("climb_m", 6))
        fields.append(QgsField("max_slope", 6))
        fields.append(QgsField("calories", 6))

        crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            QgsWkbTypes.LineStringZ,
            crs_wgs84,
        )

        pts = [QgsPoint(c[0], c[1], c[2]) for c in res.coordinates_3d]
        geom = QgsGeometry.fromPolyline(pts)

        feat = QgsFeature(fields)
        feat.setGeometry(geom)
        feat.setAttributes([
            profile.name,
            res.statistics.total_distance_km,
            res.statistics.total_duration_min,
            res.statistics.elevation_gain_m,
            res.statistics.max_slope_pct,
            res.statistics.total_calories_kcal,
        ])
        sink.addFeature(feat, QgsFeatureSink.FastInsert)

        return {self.OUTPUT: dest_id}

    def name(self) -> str:
        return "compute_3d_route"

    def displayName(self) -> str:
        return "Compute 3D Route (Multi-Profile Kinematics)"

    def group(self) -> str:
        return "3D Mobility & Routing"

    def groupId(self) -> str:
        return "mobility3d"

    def createInstance(self) -> Compute3DRouteAlgorithm:
        return Compute3DRouteAlgorithm()
