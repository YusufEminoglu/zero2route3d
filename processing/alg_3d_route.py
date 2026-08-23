"""QGIS Processing Algorithm for Multi-Criteria 3D Route Calculation."""
from __future__ import annotations

from typing import Any, Dict

from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsGeometry,
    QgsPoint,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingException,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterPoint,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorLayer,
    QgsWkbTypes,
)

from ..core.environmental_raster import EnvironmentalSurfaceSampler, MCDAWeights
from .field_utils import DOUBLE, STRING, make_field
from .post_process import finalize_output
from .crs_utils import point_to_wgs84, wgs84
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

    def shortHelpString(self) -> str:
        return (
            "Computes the least-cost 3D route between two points across a real road "
            "network, using the kinematics and constraints of the selected mobility "
            "profile.\n\n"
            "The network comes from the supplied line layer, or from OpenStreetMap for "
            "the extent spanned by the two points when no layer is given. Elevation "
            "comes from the supplied DEM; where no DEM covers a point the segment is "
            "treated as flat rather than being assigned an invented height.\n\n"
            "Travel time uses the Tobler hiking function for pedestrians, a power "
            "balance for cycling, and road-hierarchy free-flow speeds for vehicles. "
            "Energy uses the Minetti (2002) metabolic cost of transport.\n\n"
            "Output is a LineStringZ layer in EPSG:4326 carrying distance, travel "
            "time, cumulative climb, maximum gradient and energy expenditure."
        )

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

        self.profile_keys = list_profile_keys()
        profile_options = [get_profile(key).name for key in self.profile_keys]
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
        # parameterAsPoint returns the point in the *project* CRS. The routing
        # engine works in WGS84 lon/lat, so convert explicitly instead of feeding
        # it projected metres as if they were degrees.
        point_crs = self.parameterAsPointCrs(parameters, self.START_POINT, context)
        p1 = point_to_wgs84(
            self.parameterAsPoint(parameters, self.START_POINT, context), point_crs, context
        )
        p2 = point_to_wgs84(
            self.parameterAsPoint(parameters, self.END_POINT, context), point_crs, context
        )
        prof_idx = self.parameterAsEnum(parameters, self.PROFILE, context)
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM_LAYER, context)
        net_layer = self.parameterAsVectorLayer(parameters, self.NETWORK_LAYER, context)

        profile_keys = list_profile_keys()
        profile_key = profile_keys[max(0, min(prof_idx, len(profile_keys) - 1))]
        profile = get_profile(profile_key)

        feedback.setProgressText("Initializing 3D spatial surfaces and road network...")
        sampler = EnvironmentalSurfaceSampler(dem_layer=dem_layer)
        net_mgr = NetworkSourceManager()

        bbox = (min(p1.x(), p2.x()), min(p1.y(), p2.y()), max(p1.x(), p2.x()), max(p1.y(), p2.y()))
        try:
            segments = net_mgr.require_segments(vector_layer=net_layer, bbox=bbox)
        except Exception as exc:
            raise QgsProcessingException(str(exc)) from exc

        engine = RoutingEngine3D(sampler=sampler, weights=MCDAWeights())
        engine.build_graph(segments)

        feedback.setProgressText(f"Computing 3D path for {profile.name}...")
        w1 = Waypoint(lon=p1.x(), lat=p1.y())
        w2 = Waypoint(lon=p2.x(), lat=p2.y())
        res = engine.calculate_route([w1, w2], profile_key=profile_key)
        if not res.coordinates_3d:
            raise QgsProcessingException(res.status_message or "No route could be found between the selected points.")

        # Output Fields
        fields = QgsFields()
        fields.append(make_field("profile", STRING))
        fields.append(make_field("dist_km", DOUBLE))
        fields.append(make_field("time_min", DOUBLE))
        fields.append(make_field("climb_m", DOUBLE))
        fields.append(make_field("max_slope", DOUBLE))
        fields.append(make_field("calories", DOUBLE))

        # The engine emits WGS84 coordinates, so the sink must declare WGS84.
        crs_wgs84 = wgs84()
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

        # Remembered so postProcessAlgorithm can resolve and style the layer.
        self._dest_id = dest_id
        return {self.OUTPUT: dest_id}

    def name(self) -> str:
        return "compute_3d_route"

    def displayName(self) -> str:
        return "Compute 3D Route (Multi-Profile Kinematics)"

    def group(self) -> str:
        return "3D Mobility & Routing"

    def groupId(self) -> str:
        return "route3d"

    def tags(self) -> list[str]:
        return ["routing", "3d", "kinematics", "tobler", "minetti", "least cost path", "elevation", "slope", "profile", "shortest path", "network"]

    def createInstance(self) -> Compute3DRouteAlgorithm:
        return Compute3DRouteAlgorithm()

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
            title='3D Least-Cost Route',
            abstract='Least-cost 3D route computed by 02Route 3D from a real network and DEM. Attributes carry distance, travel time, cumulative climb, maximum slope and metabolic energy for the selected mobility profile.',
            aliases={'profile': 'Mobility profile', 'dist_km': 'Distance (km)', 'time_min': 'Travel time (min)', 'climb_m': 'Cumulative climb (m)', 'max_slope': 'Maximum slope (%)', 'calories': 'Energy (kcal)'},
            line_color='#0ea5e9',
            feedback=feedback,
        )
        return {}
