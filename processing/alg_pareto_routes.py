"""Processing algorithm for Multi-Objective Pareto Frontier 3D Route Planning."""
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
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorLayer,
    QgsWkbTypes,
)

from ..core.environmental_raster import EnvironmentalSurfaceSampler
from .field_utils import DOUBLE, STRING, make_field
from .post_process import finalize_output
from .crs_utils import point_to_wgs84, wgs84, rect_to_wgs84_bbox
from ..core.mobility_profiles import list_profile_keys
from ..core.network_source import NetworkSourceManager
from ..core.pareto_router import ParetoMultiObjectiveRouter
from ..core.routing_engine import RoutingEngine3D


class Pareto3DRoutesAlgorithm(QgsProcessingAlgorithm):
    """Computes non-dominated Pareto frontier route alternatives (Fastest, Flattest, Coolest, Least Effort)."""

    INPUT_POINTS = "INPUT_POINTS"
    INPUT_NETWORK = "INPUT_NETWORK"
    INPUT_DEM = "INPUT_DEM"
    INPUT_LST = "INPUT_LST"
    PARAM_PROFILE = "PARAM_PROFILE"
    OUTPUT_ROUTES = "OUTPUT_ROUTES"

    def createInstance(self) -> QgsProcessingAlgorithm:
        return Pareto3DRoutesAlgorithm()

    def name(self) -> str:
        return "pareto_3d_routes"

    def displayName(self) -> str:
        return "Multi-Objective Pareto 3D Routes (NAMOA* Frontier)"

    def group(self) -> str:
        return "3D Mobility & Routing"

    def groupId(self) -> str:
        return "route3d"

    def tags(self) -> list[str]:
        return ["pareto", "multi-objective", "namoa", "frontier", "trade-off", "alternative routes", "3d", "climb", "heat", "calories", "optimization"]

    def shortHelpString(self) -> str:
        return "Generates non-dominated Pareto alternative routes optimizing Time vs Slope vs Thermal Heat vs Calories."

    def initAlgorithm(self, config: Dict[str, Any] = None) -> None:
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_POINTS,
                "Origin & Destination Points Layer (2 Points)",
                [QgsProcessing.TypeVectorPoint],
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_NETWORK,
                "Road Network (Line Layer)",
                [QgsProcessing.TypeVectorLine],
                optional=True,
            )
        )
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
                "Thermal Heat (LST) Layer",
                optional=True,
            )
        )
        self.profiles = list_profile_keys()
        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAM_PROFILE,
                "Mobility Profile",
                options=[p.capitalize() for p in self.profiles],
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_ROUTES,
                "Pareto Frontier Alternatives (LineStringZ)",
                type=QgsProcessing.TypeVectorLine,
            )
        )

    def processAlgorithm(
        self,
        parameters: Dict[str, Any],
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> Dict[str, Any]:
        source_pts = self.parameterAsSource(parameters, self.INPUT_POINTS, context)
        net_layer = self.parameterAsVectorLayer(parameters, self.INPUT_NETWORK, context)
        dem_layer = self.parameterAsRasterLayer(parameters, self.INPUT_DEM, context)
        lst_layer = self.parameterAsRasterLayer(parameters, self.INPUT_LST, context)
        profile_idx = self.parameterAsEnum(parameters, self.PARAM_PROFILE, context)
        profile_key = self.profiles[max(0, min(profile_idx, len(self.profiles) - 1))]

        fields = QgsFields()
        fields.append(make_field("archetype", STRING))
        fields.append(make_field("dist_km", DOUBLE))
        fields.append(make_field("time_min", DOUBLE))
        fields.append(make_field("ascent_m", DOUBLE))
        fields.append(make_field("kcal", DOUBLE))

        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT_ROUTES,
            context,
            fields,
            QgsWkbTypes.LineStringZ,
            # The engine emits WGS84 lon/lat, so the sink must declare WGS84.
            wgs84(),
        )

        sampler = EnvironmentalSurfaceSampler(dem_layer=dem_layer, lst_layer=lst_layer)
        net_mgr = NetworkSourceManager()
        bbox = source_pts.sourceExtent()
        try:
            segments = net_mgr.require_segments(
                vector_layer=net_layer,
                bbox=rect_to_wgs84_bbox(bbox, source_pts.sourceCrs(), context),
            )
        except Exception as exc:
            raise QgsProcessingException(str(exc)) from exc

        if feedback.isCanceled():
            return {}
        feedback.setProgress(40)
        engine = RoutingEngine3D(sampler=sampler)
        engine.build_graph(segments)

        pareto_router = ParetoMultiObjectiveRouter(engine.nodes, engine.adj, sampler)
        node_keys = list(engine.nodes.keys())
        if len(node_keys) >= 2:
            points = [
                point_to_wgs84(f.geometry().asPoint(), source_pts.sourceCrs(), context)
                for f in source_pts.getFeatures()
                if not f.geometry().isNull()
            ]
            if len(points) < 2:
                raise QgsProcessingException("The input points layer must contain at least two valid points.")
            start_node = engine.find_nearest_node((points[0].x(), points[0].y()))
            end_node = engine.find_nearest_node((points[1].x(), points[1].y()))
            if start_node is None or end_node is None:
                raise QgsProcessingException("Could not snap the input points to the selected network.")
            res = pareto_router.solve_pareto_frontier(start_node, end_node, profile_key=profile_key)
            if res.search_truncated:
                feedback.pushWarning(
                    "The Pareto search reached its iteration limit: the routes below are an "
                    "approximation of the trade-off frontier, not the complete set."
                )
            for sol in res.solutions:
                pts = [QgsPoint(c[0], c[1], c[2]) for c in sol.coordinates_3d]
                geom = QgsGeometry.fromPolyline(pts)
                feat = QgsFeature(fields)
                feat.setGeometry(geom)
                feat.setAttributes([
                    sol.label,
                    sol.statistics.total_distance_km,
                    sol.statistics.total_duration_min,
                    sol.statistics.elevation_gain_m,
                    sol.statistics.total_calories_kcal,
                ])
                sink.addFeature(feat, QgsFeatureSink.FastInsert)

        # Remembered so postProcessAlgorithm can resolve and style the layer.
        self._dest_id = dest_id
        return {self.OUTPUT_ROUTES: dest_id}

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
            title='Pareto Route Frontier',
            abstract='Non-dominated route alternatives across travel time, cumulative climb, heat exposure and metabolic energy.',
            aliases={'archetype': 'Archetype', 'dist_km': 'Distance (km)', 'time_min': 'Travel time (min)', 'climb_m': 'Cumulative climb (m)'},
            line_color='#7c3aed',
            feedback=feedback,
        )
        return {}
