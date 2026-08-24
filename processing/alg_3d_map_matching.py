"""Processing algorithm for 3D GPS Track & GPX Map Matching (HMM / Viterbi)."""
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

from .field_utils import DOUBLE, INT, STRING, make_field
from .post_process import finalize_output
from .crs_utils import point_to_wgs84, wgs84, rect_to_wgs84_bbox
from ..core.map_matching_3d import GPXPoint, HMMMapMatcher3D
from ..core.micro_elevation import MicroElevationEngine
from ..core.network_source import NetworkSourceManager
from ..core.routing_engine import RoutingEngine3D


class MapMatching3DAlgorithm(QgsProcessingAlgorithm):
    """Snaps noisy GPS track points onto 3D topological road network with DEM elevation reconstruction."""

    INPUT_TRACK = "INPUT_TRACK"
    INPUT_NETWORK = "INPUT_NETWORK"
    INPUT_DEM = "INPUT_DEM"
    OUTPUT_LINE = "OUTPUT_LINE"

    def createInstance(self) -> QgsProcessingAlgorithm:
        return MapMatching3DAlgorithm()

    def name(self) -> str:
        return "map_match_3d_track"

    def displayName(self) -> str:
        return "3D GPS Track Map Matching (HMM / Viterbi Snapping)"

    def group(self) -> str:
        return "3D Mobility & Routing"

    def groupId(self) -> str:
        return "route3d"

    def tags(self) -> list[str]:
        return ["map matching", "gps", "gpx", "hmm", "hidden markov model", "viterbi", "snapping", "track", "elevation profile", "trajectory"]

    def shortHelpString(self) -> str:
        return "Snaps noisy GPS points onto road networks using Hidden Markov Models (HMM) and reconstructs smooth 3D elevation profiles."

    def initAlgorithm(self, config: Dict[str, Any] = None) -> None:
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_TRACK,
                "Raw GPS Track Points Layer",
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
                self.OUTPUT_LINE,
                "Snapped 3D Route (LineStringZ)",
                type=QgsProcessing.TypeVectorLine,
            )
        )

    def processAlgorithm(
        self,
        parameters: Dict[str, Any],
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> Dict[str, Any]:
        source_track = self.parameterAsSource(parameters, self.INPUT_TRACK, context)
        net_layer = self.parameterAsVectorLayer(parameters, self.INPUT_NETWORK, context)
        dem_layer = self.parameterAsRasterLayer(parameters, self.INPUT_DEM, context)

        fields = QgsFields()
        fields.append(make_field("raw_pts", INT))
        fields.append(make_field("dist_km", DOUBLE))
        fields.append(make_field("climb_m", DOUBLE))
        fields.append(make_field("mean_err_m", DOUBLE))
        fields.append(make_field("status", STRING))

        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT_LINE,
            context,
            fields,
            QgsWkbTypes.LineStringZ,
            # The engine emits WGS84 lon/lat, so the sink must declare WGS84.
            wgs84(),
        )

        net_mgr = NetworkSourceManager()
        bbox = source_track.sourceExtent()
        try:
            segments = net_mgr.require_segments(
                vector_layer=net_layer,
                bbox=rect_to_wgs84_bbox(bbox, source_track.sourceCrs(), context),
            )
        except Exception as exc:
            raise QgsProcessingException(str(exc)) from exc

        engine = RoutingEngine3D()
        engine.build_graph(segments)

        micro_ele = MicroElevationEngine(dem_layer=dem_layer)
        matcher = HMMMapMatcher3D(engine.nodes, engine.adj, micro_elevation=micro_ele)

        gpx_pts = []
        feedback.setProgress(30)
        for f in source_track.getFeatures():
            if feedback.isCanceled():
                break
            p = point_to_wgs84(f.geometry().asPoint(), source_track.sourceCrs(), context)
            gpx_pts.append(GPXPoint(lon=p.x(), lat=p.y()))

        res = matcher.match_gps_track(gpx_pts)

        if res.matched_points:
            pts = [QgsPoint(p.lon, p.lat, p.elevation_m) for p in res.matched_points]
            geom = QgsGeometry.fromPolyline(pts)
            feat = QgsFeature(fields)
            feat.setGeometry(geom)
            feat.setAttributes([
                res.total_raw_points,
                res.total_matched_distance_m / 1000.0,
                res.elevation_gain_m,
                res.mean_snapping_error_m,
                res.status_message,
            ])
            sink.addFeature(feat, QgsFeatureSink.FastInsert)

        # Remembered so postProcessAlgorithm can resolve and style the layer.
        self._dest_id = dest_id
        return {self.OUTPUT_LINE: dest_id}

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
            title='Map-Matched 3D Track',
            abstract='GPS track snapped onto the road network with elevation sampled from the DEM.',
            aliases={'points': 'Matched points', 'mean_err_m': 'Mean snapping error (m)'},
            line_color='#f97316',
            feedback=feedback,
        )
        return {}
