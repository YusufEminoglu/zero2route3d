"""Processing algorithm for Emergency Evacuation & Hazard Avoidance Routing."""
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
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorLayer,
    QgsWkbTypes,
)

from ..core.environmental_raster import EnvironmentalSurfaceSampler
from ..core.evacuation import EvacuationRouter
from ..core.network_source import NetworkSourceManager
from ..core.routing_engine import RoutingEngine3D, Waypoint


class EvacuationRoutingAlgorithm(QgsProcessingAlgorithm):
    """Computes least-risk emergency evacuation 3D path from origin point to designated muster zones."""

    INPUT_ORIGIN = "INPUT_ORIGIN"
    INPUT_MUSTERS = "INPUT_MUSTERS"
    INPUT_NETWORK = "INPUT_NETWORK"
    INPUT_DEM = "INPUT_DEM"
    OUTPUT = "OUTPUT"

    def createInstance(self) -> QgsProcessingAlgorithm:
        return EvacuationRoutingAlgorithm()

    def name(self) -> str:
        return "emergency_evacuation_3d"

    def displayName(self) -> str:
        return "Emergency Evacuation & Hazard Avoidance (3D Route)"

    def group(self) -> str:
        return "3D Mobility & Routing"

    def groupId(self) -> str:
        return "route3d"

    def shortHelpString(self) -> str:
        return "Calculates safest emergency evacuation route to the optimal muster/assembly point avoiding hazard zones."

    def initAlgorithm(self, config: Dict[str, Any] = None) -> None:
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_ORIGIN,
                "Evacuation Origin Point",
                [QgsProcessing.TypeVectorPoint],
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_MUSTERS,
                "Safe Muster / Assembly Points Layer",
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
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                "Safest Evacuation Route (LineStringZ)",
                type=QgsProcessing.TypeVectorLine,
            )
        )

    def processAlgorithm(
        self,
        parameters: Dict[str, Any],
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> Dict[str, Any]:
        source_origin = self.parameterAsSource(parameters, self.INPUT_ORIGIN, context)
        source_musters = self.parameterAsSource(parameters, self.INPUT_MUSTERS, context)
        net_layer = self.parameterAsVectorLayer(parameters, self.INPUT_NETWORK, context)
        dem_layer = self.parameterAsRasterLayer(parameters, self.INPUT_DEM, context)

        fields = QgsFields()
        fields.append(QgsField("muster_name", 10))
        fields.append(QgsField("dist_km", 6))
        fields.append(QgsField("time_min", 6))
        fields.append(QgsField("climb_m", 6))
        fields.append(QgsField("risk_score", 6))

        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            QgsWkbTypes.LineStringZ,
            source_origin.sourceCrs(),
        )

        sampler = EnvironmentalSurfaceSampler(dem_layer=dem_layer)
        net_mgr = NetworkSourceManager()
        if net_layer:
            segments = net_mgr.extract_from_qgis_layer(net_layer)
        else:
            bbox = source_origin.sourceExtent()
            segments = net_mgr.generate_synthetic_grid((bbox.xMinimum(), bbox.yMinimum(), bbox.xMaximum(), bbox.yMaximum()))

        engine = RoutingEngine3D(sampler=sampler)
        engine.build_graph(segments)

        router = EvacuationRouter(engine)

        # Get origin
        orig_pt = None
        for f in source_origin.getFeatures():
            p = f.geometry().asPoint()
            orig_pt = Waypoint(lon=p.x(), lat=p.y(), name="Origin")
            break

        if not orig_pt:
            return {self.OUTPUT: dest_id}

        musters = [
            Waypoint(lon=f.geometry().asPoint().x(), lat=f.geometry().asPoint().y(), name=f"Muster {f.id()}")
            for f in source_musters.getFeatures()
            if not f.geometry().isNull()
        ]

        plan = router.calculate_evacuation_route(orig_pt, musters, profile_key="adult")

        if plan.route_result.coordinates_3d:
            pts = [QgsPoint(c[0], c[1], c[2]) for c in plan.route_result.coordinates_3d]
            geom = QgsGeometry.fromPolyline(pts)
            out_f = QgsFeature(fields)
            out_f.setGeometry(geom)
            out_f.setAttributes([
                plan.muster_point.name,
                plan.route_result.statistics.total_distance_km,
                plan.egress_time_min,
                plan.route_result.statistics.elevation_gain_m,
                plan.risk_score,
            ])
            sink.addFeature(out_f, QgsFeatureSink.FastInsert)

        return {self.OUTPUT: dest_id}
