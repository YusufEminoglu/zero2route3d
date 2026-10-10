"""QGIS Processing Algorithm for 3D Travel-Time Service Areas / Isochrones."""
from __future__ import annotations

import math
from typing import Any, Dict

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingException,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterNumber,
    QgsProcessingParameterPoint,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorLayer,
    QgsWkbTypes,
)

from .field_utils import DOUBLE, STRING, make_field
from .post_process import finalize_output
from .crs_utils import point_to_wgs84
from ..core.environmental_raster import EnvironmentalSurfaceSampler
from ..core.isochrone_engine import DEFAULT_BUFFER_M, IsochroneEngine3D
from ..core.mobility_profiles import list_profile_keys, get_profile
from ..core.network_source import NetworkSourceManager
from ..core.routing_engine import RoutingEngine3D, Waypoint


class ServiceArea3DAlgorithm(QgsProcessingAlgorithm):
    """Generates 3D kinematic travel-time service area buffers."""

    CENTER_POINT = "CENTER_POINT"
    PROFILE = "PROFILE"
    TIME_MINUTES = "TIME_MINUTES"
    DEM_LAYER = "DEM_LAYER"
    NETWORK_LAYER = "NETWORK_LAYER"
    BUFFER_M = "BUFFER_M"
    OUTPUT = "OUTPUT"

    def shortHelpString(self) -> str:
        return (
            "Computes the area reachable from a centre point within a travel-time "
            "budget, across a real road network and under the selected mobility "
            "profile.\n\n"
            "The result is the land within the corridor width (default 50 m) of every "
            "street reachable in time, including the reachable part of streets the "
            "time limit cuts off. Unlike a hull around the reachable nodes it follows "
            "the network: rivers, rail lines, blocks without access and one-way "
            "detours stay outside.\n\n"
            "Provide a DEM to have gradient affect travel speed; without one the "
            "network is treated as flat."
        )

    def initAlgorithm(self, config: Dict[str, Any] = None) -> None:
        self.addParameter(
            QgsProcessingParameterPoint(
                self.CENTER_POINT,
                "Origin Facility Point",
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.TIME_MINUTES,
                "Travel Time Limit (Minutes)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=10.0,
                minValue=1.0,
                maxValue=120.0,
            )
        )

        # Derived from the profile registry, never hand-listed: a hard-coded list
        # of 9 labels was being indexed into the 15-entry registry, so choosing
        # "Passenger Car" silently routed as a mountain bike and 6 profiles were
        # unreachable.
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
                "DEM Raster (Optional)",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.NETWORK_LAYER,
                "Road Network (Line Layer)",
                [QgsProcessing.TypeVectorLine],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.BUFFER_M,
                "Corridor width either side of reachable streets (m)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=DEFAULT_BUFFER_M,
                minValue=5.0,
                maxValue=500.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                "3D Isochrone Polygon",
                type=QgsProcessing.TypeVectorPolygon,
            )
        )

    def processAlgorithm(
        self,
        parameters: Dict[str, Any],
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> Dict[str, Any]:
        center_crs = self.parameterAsPointCrs(parameters, self.CENTER_POINT, context)
        center = point_to_wgs84(
            self.parameterAsPoint(parameters, self.CENTER_POINT, context),
            center_crs,
            context,
        )
        time_min = self.parameterAsDouble(parameters, self.TIME_MINUTES, context)
        prof_idx = self.parameterAsEnum(parameters, self.PROFILE, context)
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM_LAYER, context)
        net_layer = self.parameterAsVectorLayer(parameters, self.NETWORK_LAYER, context)
        buffer_m = self.parameterAsDouble(parameters, self.BUFFER_M, context) or DEFAULT_BUFFER_M

        keys = getattr(self, "profile_keys", None) or list_profile_keys()
        profile_key = keys[max(0, min(prof_idx, len(keys) - 1))]
        profile = get_profile(profile_key)

        feedback.setProgressText("Loading the real road network...")
        speed_m_per_min = (profile.base_speed_kmh * 1000.0) / 60.0
        max_radius_m = max(100.0, time_min * speed_m_per_min)
        lat_rad = math.radians(center.y())
        deg_lat = max_radius_m / 110574.0
        deg_lon = max_radius_m / max(1.0, 111320.0 * math.cos(lat_rad))
        bbox = (center.x() - deg_lon, center.y() - deg_lat, center.x() + deg_lon, center.y() + deg_lat)

        try:
            segments = NetworkSourceManager().require_segments(
                vector_layer=net_layer, bbox=bbox, is_canceled=getattr(feedback, "isCanceled", None)
            )
        except Exception as exc:
            raise QgsProcessingException(str(exc)) from exc

        if feedback.isCanceled():
            return {}
        feedback.setProgress(45)
        engine = RoutingEngine3D(sampler=EnvironmentalSurfaceSampler(dem_layer=dem_layer))
        engine.build_graph(segments)
        iso_result = IsochroneEngine3D(engine).compute_isochrones(
            Waypoint(center.x(), center.y(), name="Origin"),
            profile_key=profile_key,
            time_intervals_min=(time_min,),
            buffer_m=buffer_m,
        )

        fields = QgsFields()
        fields.append(make_field("profile", STRING))
        fields.append(make_field("time_min", DOUBLE))
        fields.append(make_field("area_ha", DOUBLE))
        fields.append(make_field("reach_km", DOUBLE))

        crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            QgsWkbTypes.MultiPolygon,
            crs_wgs84,
        )

        # Buffer in metres: a local azimuthal equidistant projection centred on
        # the origin is accurate to well under 1 % over any isochrone's extent.
        local_crs = QgsCoordinateReferenceSystem.fromProj(
            f"+proj=aeqd +lat_0={center.y():.7f} +lon_0={center.x():.7f} +datum=WGS84 +units=m +no_defs"
        )
        to_local = QgsCoordinateTransform(crs_wgs84, local_crs, QgsProject.instance())
        to_wgs84 = QgsCoordinateTransform(local_crs, crs_wgs84, QgsProject.instance())

        for band in iso_result.bands:
            if feedback.isCanceled():
                return {}
            lines = [
                [QgsPointXY(*a), QgsPointXY(*b)]
                for a, b in band.reached_segments
                if a != b
            ]
            if not lines:
                feedback.pushWarning(
                    f"No street is reachable within {band.time_cutoff_min:g} min of the origin for this profile."
                )
                continue
            geom = QgsGeometry.fromMultiPolylineXY(lines)
            geom.transform(to_local)
            geom = geom.buffer(buffer_m, 6)
            if geom.isNull() or geom.isEmpty():
                continue
            area_ha = geom.area() / 10000.0
            geom.transform(to_wgs84)
            geom.convertToMultiType()
            feat = QgsFeature(fields)
            feat.setGeometry(geom)
            reach_km = math.sqrt(max(0.0, area_ha) * 10000.0 / math.pi) / 1000.0
            feat.setAttributes([profile.name, band.time_cutoff_min, round(area_ha, 2), round(reach_km, 2)])
            sink.addFeature(feat, QgsFeatureSink.FastInsert)

        # Remembered so postProcessAlgorithm can resolve and style the layer.
        self._dest_id = dest_id
        return {self.OUTPUT: dest_id}

    def name(self) -> str:
        return "generate_3d_isochrone"

    def displayName(self) -> str:
        return "Generate 3D Isochrone (Travel-Time Service Area)"

    def group(self) -> str:
        return "3D Mobility & Routing"

    def groupId(self) -> str:
        return "route3d"

    def tags(self) -> list[str]:
        return ["isochrone", "service area", "catchment", "travel time", "accessibility", "3d", "wavefront", "tobler", "contour", "15 minute city"]

    def createInstance(self) -> ServiceArea3DAlgorithm:
        return ServiceArea3DAlgorithm()

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
            title='3D Isochrone Catchment',
            abstract='Land within the corridor width of every street reachable within the travel time (including partly reachable streets), for the selected mobility profile.',
            aliases={'profile': 'Mobility profile', 'time_min': 'Cutoff (min)', 'area_ha': 'Area (ha)', 'reach_km': 'Equivalent-circle radius (km)'},
            line_color='#8b5cf6',
            feedback=feedback,
        )
        return {}
