"""Processing algorithm for Multi-Objective Pareto Frontier 3D Route Planning."""
from __future__ import annotations

from typing import Any, Dict

from qgis.core import (
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
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorLayer,
    QgsWkbTypes,
)

from ..core.environmental_raster import EnvironmentalSurfaceSampler
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
        profile_key = self.profiles[profile_idx]

        fields = QgsFields()
        fields.append(QgsField("archetype", 10))
        fields.append(QgsField("dist_km", 6))
        fields.append(QgsField("time_min", 6))
        fields.append(QgsField("ascent_m", 6))
        fields.append(QgsField("kcal", 6))

        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT_ROUTES,
            context,
            fields,
            QgsWkbTypes.LineStringZ,
            source_pts.sourceCrs(),
        )

        sampler = EnvironmentalSurfaceSampler(dem_layer=dem_layer, lst_layer=lst_layer)
        net_mgr = NetworkSourceManager()
        if net_layer:
            segments = net_mgr.extract_from_qgis_layer(net_layer)
        else:
            bbox = source_pts.sourceExtent()
            segments = net_mgr.generate_synthetic_grid((bbox.xMinimum(), bbox.yMinimum(), bbox.xMaximum(), bbox.yMaximum()))

        engine = RoutingEngine3D(sampler=sampler)
        engine.build_graph(segments)

        pareto_router = ParetoMultiObjectiveRouter(engine.nodes, engine.adj, sampler)
        node_keys = list(engine.nodes.keys())
        if len(node_keys) >= 2:
            res = pareto_router.solve_pareto_frontier(node_keys[0], node_keys[-1], profile_key=profile_key)
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

        return {self.OUTPUT_ROUTES: dest_id}
