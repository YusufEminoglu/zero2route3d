"""Processing algorithm for Batch 3D Route Planning between Point Layers."""
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
from .field_utils import DOUBLE, INT, STRING, make_field
from .post_process import finalize_output
from .crs_utils import point_to_wgs84, wgs84, rect_to_wgs84_bbox, require_matching_crs
from ..core.mobility_profiles import list_profile_keys
from ..core.network_source import NetworkSourceManager
from ..core.routing_engine import RoutingEngine3D, Waypoint


class Batch3DRouteAlgorithm(QgsProcessingAlgorithm):
    """Computes batch 3D least-cost paths between pairwise features of two point layers."""

    INPUT_ORIGINS = "INPUT_ORIGINS"
    INPUT_DESTS = "INPUT_DESTS"
    INPUT_NETWORK = "INPUT_NETWORK"
    INPUT_DEM = "INPUT_DEM"
    PARAM_PROFILE = "PARAM_PROFILE"
    OUTPUT = "OUTPUT"

    def createInstance(self) -> QgsProcessingAlgorithm:
        return Batch3DRouteAlgorithm()

    def name(self) -> str:
        return "batch_3d_routes"

    def displayName(self) -> str:
        return "Batch 3D Route Planner (Layer to Layer)"

    def group(self) -> str:
        return "3D Mobility & Routing"

    def groupId(self) -> str:
        return "route3d"

    def tags(self) -> list[str]:
        return ["batch", "points", "origin destination", "many to many", "multi route", "layer routing", "3d paths", "automated routing"]

    def shortHelpString(self) -> str:
        return "Computes 3D paths connecting corresponding features in origin and destination point layers."

    def initAlgorithm(self, config: Dict[str, Any] = None) -> None:
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_ORIGINS,
                "Origin Points Layer",
                [QgsProcessing.TypeVectorPoint],
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_DESTS,
                "Destination Points Layer",
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
                "Digital Elevation Model (DEM Raster)",
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
                self.OUTPUT,
                "Batch 3D Routes (LineStringZ)",
                type=QgsProcessing.TypeVectorLine,
            )
        )

    def processAlgorithm(
        self,
        parameters: Dict[str, Any],
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> Dict[str, Any]:
        source_origins = self.parameterAsSource(parameters, self.INPUT_ORIGINS, context)
        source_dests = self.parameterAsSource(parameters, self.INPUT_DESTS, context)
        net_layer = self.parameterAsVectorLayer(parameters, self.INPUT_NETWORK, context)
        dem_layer = self.parameterAsRasterLayer(parameters, self.INPUT_DEM, context)
        profile_idx = self.parameterAsEnum(parameters, self.PARAM_PROFILE, context)
        profile_key = self.profiles[max(0, min(profile_idx, len(self.profiles) - 1))]

        fields = QgsFields()
        fields.append(make_field("pair_id", INT))
        fields.append(make_field("profile", STRING))
        fields.append(make_field("dist_km", DOUBLE))
        fields.append(make_field("time_min", DOUBLE))
        fields.append(make_field("climb_m", DOUBLE))

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
        # Combining two extents only means anything when both are in the same CRS.
        require_matching_crs([
            ("origins", source_origins.sourceCrs()),
            ("destinations", source_dests.sourceCrs()),
        ])
        bbox = source_origins.sourceExtent()
        bbox.combineExtentWith(source_dests.sourceExtent())
        try:
            segments = net_mgr.require_segments(
                vector_layer=net_layer,
                bbox=rect_to_wgs84_bbox(bbox, source_origins.sourceCrs(), context),
            )
        except Exception as exc:
            raise QgsProcessingException(str(exc)) from exc

        if not segments:
            raise QgsProcessingException("The selected network contains no usable segments.")

        engine = RoutingEngine3D(sampler=sampler)
        engine.build_graph(segments)

        origins = list(source_origins.getFeatures())
        dests = list(source_dests.getFeatures())
        count = min(len(origins), len(dests))
        if count == 0:
            raise QgsProcessingException(
                "Both the origins and the destinations layer must contain at least "
                "one point feature."
            )
        if len(origins) != len(dests):
            # Pairing is positional; say so rather than silently truncating.
            feedback.pushWarning(
                f"{len(origins)} origin(s) and {len(dests)} destination(s) were "
                f"supplied. Features are paired in order, so only the first {count} "
                f"pair(s) will be routed."
            )

        for i in range(count):
            if feedback.isCanceled():
                break

            f_orig = origins[i]
            f_dest = dests[i]
            p1 = point_to_wgs84(f_orig.geometry().asPoint(), source_origins.sourceCrs(), context)
            p2 = point_to_wgs84(f_dest.geometry().asPoint(), source_dests.sourceCrs(), context)

            w1 = Waypoint(lon=p1.x(), lat=p1.y())
            w2 = Waypoint(lon=p2.x(), lat=p2.y())

            res = engine.calculate_route([w1, w2], profile_key=profile_key, compute_alternatives=False)

            if res.coordinates_3d:
                pts = [QgsPoint(c[0], c[1], c[2]) for c in res.coordinates_3d]
                geom = QgsGeometry.fromPolyline(pts)
                out_f = QgsFeature(fields)
                out_f.setGeometry(geom)
                out_f.setAttributes([
                    i + 1,
                    res.profile.name,
                    res.statistics.total_distance_km,
                    res.statistics.total_duration_min,
                    res.statistics.elevation_gain_m,
                ])
                sink.addFeature(out_f, QgsFeatureSink.FastInsert)

            if count > 0:
                feedback.setProgress(int(((i + 1) / count) * 100.0))

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
            title='Batch 3D Routes',
            abstract='One 3D route per origin/destination pair, paired positionally in feature order.',
            aliases={'pair_id': 'Pair', 'dist_km': 'Distance (km)', 'time_min': 'Travel time (min)', 'climb_m': 'Cumulative climb (m)'},
            line_color='#0ea5e9',
            feedback=feedback,
        )
        return {}
