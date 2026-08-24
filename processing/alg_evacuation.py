"""Processing algorithm for Emergency Evacuation & Hazard Avoidance Routing."""
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
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorLayer,
    QgsWkbTypes,
)

from .field_utils import DOUBLE, STRING, make_field
from .post_process import finalize_output
from .crs_utils import point_to_wgs84, wgs84, rect_to_wgs84_bbox, require_matching_crs
from ..core.environmental_raster import EnvironmentalSurfaceSampler
from ..core.evacuation import EvacuationRouter
from ..core.network_source import NetworkSourceManager
from ..core.routing_engine import RoutingEngine3D, Waypoint


def _wgs_muster(feature, source_crs, context):
    """Muster point as a WGS84 waypoint -- the CRS the routing engine expects."""
    point = point_to_wgs84(feature.geometry().asPoint(), source_crs, context)
    return Waypoint(lon=point.x(), lat=point.y(), name=f"Muster {feature.id()}")


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

    def tags(self) -> list[str]:
        return ["evacuation", "emergency", "hazard", "muster point", "shelter", "egress", "disaster", "safety", "fire", "flood", "seismic"]

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
        fields.append(make_field("muster_name", STRING))
        fields.append(make_field("dist_km", DOUBLE))
        fields.append(make_field("time_min", DOUBLE))
        fields.append(make_field("climb_m", DOUBLE))
        fields.append(make_field("risk_score", DOUBLE))

        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            QgsWkbTypes.LineStringZ,
            # The engine emits WGS84 lon/lat, so the sink must declare WGS84.
            wgs84(),
        )

        sampler = EnvironmentalSurfaceSampler(dem_layer=dem_layer)
        net_mgr = NetworkSourceManager()
        require_matching_crs([
            ("origin", source_origin.sourceCrs()),
            ("muster points", source_musters.sourceCrs()),
        ])
        bbox = source_origin.sourceExtent()
        muster_bbox = source_musters.sourceExtent()
        bbox.combineExtentWith(muster_bbox)
        try:
            segments = net_mgr.require_segments(
                vector_layer=net_layer,
                bbox=rect_to_wgs84_bbox(bbox, source_origin.sourceCrs(), context),
            )
        except Exception as exc:
            raise QgsProcessingException(str(exc)) from exc

        engine = RoutingEngine3D(sampler=sampler)
        engine.build_graph(segments)

        router = EvacuationRouter(engine)

        # Get origin
        orig_pt = None
        for f in source_origin.getFeatures():
            p = point_to_wgs84(f.geometry().asPoint(), source_origin.sourceCrs(), context)
            orig_pt = Waypoint(lon=p.x(), lat=p.y(), name="Origin")
            break

        if not orig_pt:
            # Previously returned an empty layer and reported success.
            raise QgsProcessingException(
                "The origin layer contains no point feature to evacuate from."
            )

        musters = [
            _wgs_muster(f, source_musters.sourceCrs(), context)
            for f in source_musters.getFeatures()
            if not f.geometry().isNull()
        ]
        if not musters:
            raise QgsProcessingException(
                "The muster-point layer contains no point feature to evacuate to."
            )
        if feedback.isCanceled():
            return {}
        feedback.setProgress(60)
        feedback.pushInfo(f"Evaluating {len(musters)} muster point(s)...")

        try:
            plan = router.calculate_evacuation_route(orig_pt, musters, profile_key="adult")
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc

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
            title='Evacuation Routes',
            abstract='Shortest egress route from the origin to each muster point across the real network.',
            aliases={'muster': 'Muster point', 'dist_km': 'Distance (km)', 'time_min': 'Travel time (min)'},
            line_color='#dc2626',
            feedback=feedback,
        )
        return {}
