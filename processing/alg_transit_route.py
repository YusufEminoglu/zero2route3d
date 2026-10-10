"""QGIS Processing algorithm: public transport + walking route from a GTFS feed."""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Tuple

from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsGeometry,
    QgsPoint,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
    QgsProcessingParameterPoint,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
    QgsProcessingParameterVectorLayer,
    QgsWkbTypes,
)

from .crs_utils import point_to_wgs84, wgs84
from .field_utils import DOUBLE, INT, STRING, make_field
from .post_process import finalize_output
from ..core.environmental_raster import EnvironmentalSurfaceSampler
from ..core.mobility_profiles import get_profile, list_profile_keys
from ..core.network_source import NetworkSourceManager
from ..core.routing_engine import RoutingEngine3D, Waypoint
from ..core.terrain_grid import grid_bbox
from ..core.transit_raptor import GtfsError, Journey, Leg, format_time, load_gtfs, raptor

# Stops considered for walking to the first and from the last vehicle.
MAX_ACCESS_STOPS = 20


class TransitRoute3DAlgorithm(QgsProcessingAlgorithm):
    """Walk + public transport journey: GTFS timetable (RAPTOR) and real 3D walking legs."""

    GTFS = "GTFS"
    START_POINT = "START_POINT"
    END_POINT = "END_POINT"
    DATE = "DATE"
    TIME = "TIME"
    WALK_PROFILE = "WALK_PROFILE"
    MAX_WALK_M = "MAX_WALK_M"
    MAX_RIDES = "MAX_RIDES"
    NETWORK_LAYER = "NETWORK_LAYER"
    DEM_LAYER = "DEM_LAYER"
    OUTPUT = "OUTPUT"

    def name(self) -> str:
        return "transit_route_gtfs"

    def displayName(self) -> str:
        return "Transit + Walk Route (GTFS, 3D walking legs)"

    def group(self) -> str:
        return "3D Mobility & Routing"

    def groupId(self) -> str:
        return "route3d"

    def tags(self) -> list[str]:
        return ["transit", "public transport", "gtfs", "raptor", "multimodal", "bus", "metro", "walk", "accessibility"]

    def createInstance(self) -> "TransitRoute3DAlgorithm":
        return TransitRoute3DAlgorithm()

    def shortHelpString(self) -> str:
        return (
            "Finds the earliest-arrival journey by public transport and walking, using "
            "a GTFS feed (zip or folder) and the RAPTOR algorithm.\n\n"
            "Walking to the first stop, from the last stop, and walking all the way are "
            "routed on the street network with the chosen walking profile, so slopes, "
            "stairs and access rules count (a wheelchair profile only uses stops and "
            "trips the feed marks as wheelchair accessible). Transfers between stops use "
            "the feed's transfers.txt, or stops within 300 m at walking pace with a 1.3 "
            "detour factor. Only trips running on the chosen date are used.\n\n"
            "Output: one line per leg (walk / ride) with route, stops, departure and "
            "arrival times. Ride legs are drawn stop to stop (shapes.txt is not used). "
            "When walking all the way is faster, the result is a single walk leg."
        )

    def initAlgorithm(self, config: Dict[str, Any] = None) -> None:
        self.addParameter(QgsProcessingParameterFile(self.GTFS, "GTFS feed (zip file)", extension="zip"))
        self.addParameter(QgsProcessingParameterPoint(self.START_POINT, "Origin"))
        self.addParameter(QgsProcessingParameterPoint(self.END_POINT, "Destination"))
        self.addParameter(QgsProcessingParameterString(
            self.DATE, "Travel date (YYYY-MM-DD)", defaultValue=datetime.date.today().isoformat()))
        self.addParameter(QgsProcessingParameterString(self.TIME, "Departure time (HH:MM)", defaultValue="08:00"))
        self.walk_keys = [k for k in list_profile_keys() if get_profile(k).category == "pedestrian"]
        self.addParameter(QgsProcessingParameterEnum(
            self.WALK_PROFILE, "Walking profile", options=[get_profile(k).name for k in self.walk_keys], defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_WALK_M, "Maximum walk to or from a stop (m)", type=QgsProcessingParameterNumber.Double,
            defaultValue=800.0, minValue=50.0, maxValue=5000.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_RIDES, "Maximum number of vehicles", type=QgsProcessingParameterNumber.Integer,
            defaultValue=4, minValue=1, maxValue=8))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.NETWORK_LAYER, "Street network for walking (optional; else OpenStreetMap)",
            [QgsProcessing.TypeVectorLine], optional=True))
        self.addParameter(QgsProcessingParameterRasterLayer(self.DEM_LAYER, "DEM (optional)", optional=True))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, "Journey legs", type=QgsProcessing.TypeVectorLine))

    def processAlgorithm(self, parameters: Dict[str, Any], context: QgsProcessingContext,
                         feedback: QgsProcessingFeedback) -> Dict[str, Any]:
        point_crs = self.parameterAsPointCrs(parameters, self.START_POINT, context)
        origin = point_to_wgs84(self.parameterAsPoint(parameters, self.START_POINT, context), point_crs, context)
        dest = point_to_wgs84(self.parameterAsPoint(parameters, self.END_POINT, context), point_crs, context)
        try:
            date = datetime.date.fromisoformat(self.parameterAsString(parameters, self.DATE, context).strip())
            hh, mm = (int(v) for v in self.parameterAsString(parameters, self.TIME, context).strip().split(":")[:2])
        except ValueError as exc:
            raise QgsProcessingException(f"Use YYYY-MM-DD for the date and HH:MM for the time ({exc}).") from exc
        depart_s = float(hh * 3600 + mm * 60)
        keys = getattr(self, "walk_keys", None) or [k for k in list_profile_keys() if get_profile(k).category == "pedestrian"]
        profile_key = keys[max(0, min(self.parameterAsEnum(parameters, self.WALK_PROFILE, context), len(keys) - 1))]
        max_walk = self.parameterAsDouble(parameters, self.MAX_WALK_M, context)
        max_rides = self.parameterAsInt(parameters, self.MAX_RIDES, context)

        feedback.setProgressText("Reading the GTFS timetable...")
        try:
            network = load_gtfs(self.parameterAsFile(parameters, self.GTFS, context), date,
                                wheelchair_only=profile_key == "wheelchair")
        except (GtfsError, OSError, KeyError) as exc:
            raise QgsProcessingException(f"The GTFS feed could not be read: {exc}") from exc
        if not network.patterns:
            feedback.pushWarning(f"No trips run on {date.isoformat()} in this feed; only walking is possible.")
        if feedback.isCanceled():
            return {}
        feedback.setProgress(20)

        access_stops = network.stops_near(origin.x(), origin.y(), max_walk)[:MAX_ACCESS_STOPS]
        egress_stops = network.stops_near(dest.x(), dest.y(), max_walk)[:MAX_ACCESS_STOPS]
        points = [(origin.x(), origin.y()), (dest.x(), dest.y())]
        points += [(network.stops[i].lon, network.stops[i].lat) for i, _ in access_stops + egress_stops]
        bbox = grid_bbox([points], buffer_m=300.0)

        feedback.setProgressText("Loading the street network for the walking legs...")
        try:
            segments = NetworkSourceManager().require_segments(
                vector_layer=self.parameterAsVectorLayer(parameters, self.NETWORK_LAYER, context),
                bbox=bbox, is_canceled=feedback.isCanceled)
        except Exception as exc:
            raise QgsProcessingException(str(exc)) from exc
        engine = RoutingEngine3D(sampler=EnvironmentalSurfaceSampler(
            dem_layer=self.parameterAsRasterLayer(parameters, self.DEM_LAYER, context)))
        engine.build_graph(segments)
        feedback.setProgress(40)

        here = Waypoint(origin.x(), origin.y(), name="Origin")
        there = Waypoint(dest.x(), dest.y(), name="Destination")

        def stop_point(i: int) -> Waypoint:
            return Waypoint(network.stops[i].lon, network.stops[i].lat, name=network.stops[i].name)

        feedback.setProgressText("Walking times to and from the stops...")
        access = self._walk_times(engine, [here], [stop_point(i) for i, _ in access_stops], profile_key,
                                  [i for i, _ in access_stops], from_origin=True)
        egress = self._walk_times(engine, [stop_point(i) for i, _ in egress_stops], [there], profile_key,
                                  [i for i, _ in egress_stops], from_origin=False)
        direct = engine.calculate_route([here, there], profile_key=profile_key, compute_alternatives=False)
        direct_s = direct.statistics.total_duration_s if direct.coordinates_3d else float("inf")
        feedback.setProgress(60)
        if feedback.isCanceled():
            return {}

        feedback.setProgressText("Searching the timetable (RAPTOR)...")
        journey = raptor(network, depart_s, access, egress, max_rides=max_rides, direct_walk_s=direct_s)
        if journey is None:
            if not direct.coordinates_3d:
                raise QgsProcessingException(
                    "No journey found: no stop within reach on both ends and no walking route between the points.")
            feedback.pushInfo("Walking all the way is the fastest option.")
            journey = Journey([Leg("walk", None, None, depart_s, depart_s + direct_s)], depart_s, depart_s + direct_s, 0)
        feedback.setProgress(80)

        fields = QgsFields()
        for name, kind in (("leg", INT), ("mode", STRING), ("route", STRING), ("from_stop", STRING),
                           ("to_stop", STRING), ("depart", STRING), ("arrive", STRING), ("minutes", DOUBLE),
                           ("trip_id", STRING)):
            fields.append(make_field(name, kind))
        sink, dest_id = self.parameterAsSink(parameters, self.OUTPUT, context, fields, QgsWkbTypes.LineStringZ, wgs84())

        for number, leg in enumerate(journey.legs, start=1):
            coords = self._leg_coords(engine, network, leg, here, there, profile_key, direct)
            if len(coords) < 2:
                continue
            feat = QgsFeature(fields)
            feat.setGeometry(QgsGeometry.fromPolyline([QgsPoint(c[0], c[1], c[2]) for c in coords]))
            stop_name = (lambda i: network.stops[i].name if i is not None else "")
            feat.setAttributes([
                number, leg.kind, leg.route_name, stop_name(leg.from_stop) or "Origin",
                stop_name(leg.to_stop) or "Destination", format_time(leg.depart_s), format_time(leg.arrive_s),
                round((leg.arrive_s - leg.depart_s) / 60.0, 1), leg.trip_id,
            ])
            sink.addFeature(feat, QgsFeatureSink.FastInsert)

        feedback.pushInfo(
            f"Depart {format_time(journey.depart_s)}, arrive {format_time(journey.arrive_s)} "
            f"({journey.duration_s / 60.0:.0f} min, {journey.rides} vehicle(s))."
        )
        self._dest_id = dest_id
        return {self.OUTPUT: dest_id}

    @staticmethod
    def _walk_times(engine: RoutingEngine3D, origins: List[Waypoint], destinations: List[Waypoint],
                    profile_key: str, stop_ids: List[int], from_origin: bool) -> Dict[int, float]:
        """Network walking seconds between the origin (or destination) and each stop."""
        if not stop_ids:
            return {}
        rows = engine.calculate_od_matrix(origins, destinations, profile_key=profile_key)
        times: Dict[int, float] = {}
        for row in rows:
            if not row["is_matched"]:
                continue
            index = (row["dest_id"] if from_origin else row["origin_id"]) - 1
            times[stop_ids[index]] = float(row["duration_min"]) * 60.0
        return times

    @staticmethod
    def _leg_coords(engine: RoutingEngine3D, network: Any, leg: Leg, here: Waypoint, there: Waypoint,
                    profile_key: str, direct: Any) -> List[Tuple[float, float, float]]:
        def wp(i: Optional[int], fallback: Waypoint) -> Waypoint:
            if i is None:
                return fallback
            return Waypoint(network.stops[i].lon, network.stops[i].lat)

        if leg.kind == "ride":
            return [(network.stops[i].lon, network.stops[i].lat, 0.0) for i in leg.stop_sequence]
        if leg.from_stop is None and leg.to_stop is None:
            return list(direct.coordinates_3d)
        walk = engine.calculate_route([wp(leg.from_stop, here), wp(leg.to_stop, there)],
                                      profile_key=profile_key, compute_alternatives=False)
        if walk.coordinates_3d:
            return list(walk.coordinates_3d)
        a, b = wp(leg.from_stop, here), wp(leg.to_stop, there)
        return [(a.lon, a.lat, 0.0), (b.lon, b.lat, 0.0)]

    def postProcessAlgorithm(self, context: QgsProcessingContext, feedback: QgsProcessingFeedback) -> Dict[str, Any]:
        finalize_output(
            context,
            getattr(self, "_dest_id", ""),
            title="Transit + walk journey",
            abstract="Earliest-arrival journey from a GTFS timetable (RAPTOR) with walking legs routed on the street network.",
            aliases={"leg": "Leg", "mode": "Mode", "route": "Route", "from_stop": "From", "to_stop": "To",
                     "depart": "Departs", "arrive": "Arrives", "minutes": "Minutes", "trip_id": "GTFS trip"},
            line_color="#0ea5e9",
            feedback=feedback,
        )
        return {}

