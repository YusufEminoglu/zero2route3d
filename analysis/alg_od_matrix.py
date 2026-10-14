"""Processing algorithm for N x M Origin-Destination 3D Cost Matrix calculation."""
from __future__ import annotations

from typing import Any, Dict

from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
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
from .crs_utils import point_to_wgs84, rect_to_wgs84_bbox, require_matching_crs
from ..core.mobility_profiles import list_profile_keys
from ..core.network_source import NetworkSourceManager
from ..core.routing_engine import RoutingEngine3D, Waypoint


def _wgs_waypoint(feature, source_crs, context):
    """Feature point as a WGS84 waypoint -- the CRS the routing engine expects."""
    point = point_to_wgs84(feature.geometry().asPoint(), source_crs, context)
    return Waypoint(lon=point.x(), lat=point.y(), name=str(feature.id()))


class OriginDestinationMatrix3DAlgorithm(QgsProcessingAlgorithm):
    """Compute N x M Origin-Destination 3D distance, travel-time, climb, and metabolic cost table."""

    INPUT_ORIGINS = "INPUT_ORIGINS"
    INPUT_DESTS = "INPUT_DESTS"
    INPUT_NETWORK = "INPUT_NETWORK"
    INPUT_DEM = "INPUT_DEM"
    PARAM_PROFILE = "PARAM_PROFILE"
    OUTPUT = "OUTPUT"

    def createInstance(self) -> QgsProcessingAlgorithm:
        return OriginDestinationMatrix3DAlgorithm()

    def name(self) -> str:
        return "od_matrix_3d"

    def displayName(self) -> str:
        return "Origin-Destination (OD) 3D Cost Matrix"

    def group(self) -> str:
        return "3D Mobility & Routing"

    def groupId(self) -> str:
        return "route3d"

    def tags(self) -> list[str]:
        return ["od matrix", "origin destination", "cost matrix", "distance matrix", "travel time matrix", "n by m", "network analysis", "transport planning"]

    def shortHelpString(self) -> str:
        return "Calculates pairwise 3D distance, travel time, elevation gain, and caloric expenditure between an origin point layer and destination point layer."

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
                "OD Cost Matrix Table",
                type=QgsProcessing.TypeVector,
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
        fields.append(make_field("origin_id", INT))
        fields.append(make_field("dest_id", INT))
        fields.append(make_field("profile", STRING))
        fields.append(make_field("dist_m", DOUBLE))
        fields.append(make_field("dist_km", DOUBLE))
        fields.append(make_field("time_min", DOUBLE))
        fields.append(make_field("climb_m", DOUBLE))
        fields.append(make_field("kcal", DOUBLE))

        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            QgsWkbTypes.NoGeometry,
            source_origins.sourceCrs(),
        )

        # Build Graph
        sampler = EnvironmentalSurfaceSampler(dem_layer=dem_layer)
        net_mgr = NetworkSourceManager()
        require_matching_crs([
            ("origins", source_origins.sourceCrs()),
            ("destinations", source_dests.sourceCrs()),
        ])
        bbox = source_origins.sourceExtent()
        dest_bbox = source_dests.sourceExtent()
        bbox.combineExtentWith(dest_bbox)
        try:
            segments = net_mgr.require_segments(
                vector_layer=net_layer,
                bbox=rect_to_wgs84_bbox(bbox, source_origins.sourceCrs(), context),
                is_canceled=getattr(feedback, "isCanceled", None),
            )
        except Exception as exc:
            raise QgsProcessingException(str(exc)) from exc

        engine = RoutingEngine3D(sampler=sampler)
        engine.build_graph(segments)

        origins = [
            _wgs_waypoint(f, source_origins.sourceCrs(), context)
            for f in source_origins.getFeatures()
            if not f.geometry().isNull()
        ]
        dests = [
            _wgs_waypoint(f, source_dests.sourceCrs(), context)
            for f in source_dests.getFeatures()
            if not f.geometry().isNull()
        ]

        if not origins or not dests:
            raise QgsProcessingException(
                "Both the origins and the destinations layer must contain at least "
                "one point feature."
            )

        def _progress(done, total):
            if feedback.isCanceled():
                return False
            feedback.setProgress(int((done / total) * 100.0))
            return True

        feedback.pushInfo(
            f"Computing a {len(origins)} x {len(dests)} matrix "
            f"({len(origins) * len(dests)} routes)..."
        )
        rows = engine.calculate_od_matrix(
            origins, dests, profile_key=profile_key, progress_callback=_progress
        )
        for r in rows:
            if feedback.isCanceled():
                break
            feat = QgsFeature(fields)
            feat.setAttributes([
                r["origin_id"],
                r["dest_id"],
                r["profile"],
                r["distance_m"],
                r["distance_km"],
                r["duration_min"],
                r["climb_m"],
                r["calories_kcal"],
            ])
            sink.addFeature(feat, QgsFeatureSink.FastInsert)

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
            title='3D Origin-Destination Matrix',
            abstract='Network distance and travel time for every origin/destination pair.',
            aliases={'origin_name': 'Origin', 'dest_name': 'Destination', 'dist_km': 'Distance (km)', 'time_min': 'Travel time (min)'},
            feedback=feedback,
        )
        return {}
