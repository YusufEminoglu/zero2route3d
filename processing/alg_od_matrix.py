"""Processing algorithm for N x M Origin-Destination 3D Cost Matrix calculation."""
from __future__ import annotations

from typing import Any, Dict

from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsField,
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
from ..core.mobility_profiles import list_profile_keys
from ..core.network_source import NetworkSourceManager
from ..core.routing_engine import RoutingEngine3D, Waypoint


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
        fields.append(QgsField("origin_id", 2))
        fields.append(QgsField("dest_id", 2))
        fields.append(QgsField("profile", 10))
        fields.append(QgsField("dist_m", 6))
        fields.append(QgsField("dist_km", 6))
        fields.append(QgsField("time_min", 6))
        fields.append(QgsField("climb_m", 6))
        fields.append(QgsField("kcal", 6))

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

        engine = RoutingEngine3D(sampler=sampler)
        engine.build_graph(segments)

        origins = [
            Waypoint(lon=f.geometry().asPoint().x(), lat=f.geometry().asPoint().y(), name=str(f.id()))
            for f in source_origins.getFeatures()
            if not f.geometry().isNull()
        ]
        dests = [
            Waypoint(lon=f.geometry().asPoint().x(), lat=f.geometry().asPoint().y(), name=str(f.id()))
            for f in source_dests.getFeatures()
            if not f.geometry().isNull()
        ]

        rows = engine.calculate_od_matrix(origins, dests, profile_key=profile_key)
        for r in rows:
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

        return {self.OUTPUT: dest_id}
