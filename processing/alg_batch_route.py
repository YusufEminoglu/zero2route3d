"""Processing algorithm for Batch 3D Route Planning between Point Layers."""
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
    QgsProcessingException,
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
        fields.append(QgsField("pair_id", 2))
        fields.append(QgsField("profile", 10))
        fields.append(QgsField("dist_km", 6))
        fields.append(QgsField("time_min", 6))
        fields.append(QgsField("climb_m", 6))

        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            QgsWkbTypes.LineStringZ,
            source_origins.sourceCrs(),
        )

        sampler = EnvironmentalSurfaceSampler(dem_layer=dem_layer)
        net_mgr = NetworkSourceManager()
        bbox = source_origins.sourceExtent()
        dest_bbox = source_dests.sourceExtent()
        bbox.combineExtentWith(dest_bbox)
        try:
            segments = net_mgr.require_segments(
                vector_layer=net_layer,
                bbox=(bbox.xMinimum(), bbox.yMinimum(), bbox.xMaximum(), bbox.yMaximum()),
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

        for i in range(count):
            if feedback.isCanceled():
                break

            f_orig = origins[i]
            f_dest = dests[i]
            p1 = f_orig.geometry().asPoint()
            p2 = f_dest.geometry().asPoint()

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

        return {self.OUTPUT: dest_id}
