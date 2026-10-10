"""Public transport routing: a GTFS reader and the RAPTOR algorithm.

RAPTOR (Delling, Pajor & Werneck, 2012) finds earliest-arrival journeys
round by round: round k allows k vehicle trips. Walking to the first stop,
between stops and from the last stop comes from the caller (the 3D street
engine supplies real walking times, slopes included); RAPTOR handles the
timetable. Pure Python, no QGIS.

Times are seconds after midnight of the service date (GTFS allows > 24:00).
"""
from __future__ import annotations

import bisect
import csv
import datetime
import io
import math
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

INF = math.inf
# Walking between nearby stops (when transfers.txt does not say otherwise).
TRANSFER_RADIUS_M = 300.0
WALK_SPEED_M_S = 1.2
MIN_TRANSFER_S = 60.0


class GtfsError(ValueError):
    """The feed is missing a required file or column."""


@dataclass
class Stop:
    stop_id: str
    name: str
    lat: float
    lon: float
    wheelchair_boarding: str = ""


@dataclass
class Pattern:
    """Trips that serve the same stop sequence, sorted by departure."""

    route_id: str
    route_name: str
    stops: List[int]
    trips: List[str] = field(default_factory=list)
    # arrivals[t][i], departures[t][i]: trip t at stop position i.
    arrivals: List[List[float]] = field(default_factory=list)
    departures: List[List[float]] = field(default_factory=list)


@dataclass
class TransitNetwork:
    stops: List[Stop]
    patterns: List[Pattern]
    stop_patterns: List[List[Tuple[int, int]]]  # stop -> [(pattern, position)]
    transfers: List[List[Tuple[int, float]]]  # stop -> [(stop, seconds)]
    service_date: datetime.date

    def stops_near(self, lon: float, lat: float, radius_m: float) -> List[Tuple[int, float]]:
        """(stop index, straight-line metres) within radius, nearest first."""
        out = []
        for i, stop in enumerate(self.stops):
            d = _metres(lon, lat, stop.lon, stop.lat)
            if d <= radius_m:
                out.append((i, d))
        return sorted(out, key=lambda item: item[1])


@dataclass
class Leg:
    kind: str  # "walk" or "ride"
    from_stop: Optional[int]
    to_stop: Optional[int]
    depart_s: float
    arrive_s: float
    route_name: str = ""
    trip_id: str = ""
    stop_sequence: List[int] = field(default_factory=list)  # ride: every stop passed


@dataclass
class Journey:
    legs: List[Leg]
    depart_s: float
    arrive_s: float
    rides: int

    @property
    def duration_s(self) -> float:
        return self.arrive_s - self.depart_s


def _metres(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    kx = 111320.0 * math.cos(math.radians((lat1 + lat2) / 2.0))
    return math.hypot((lon2 - lon1) * kx, (lat2 - lat1) * 110574.0)


def parse_gtfs_time(text: str) -> float:
    """'HH:MM:SS' (hours may exceed 23) to seconds; NaN for blanks."""
    text = (text or "").strip()
    if not text:
        return math.nan
    parts = text.split(":")
    if len(parts) != 3:
        raise GtfsError(f"Invalid GTFS time: {text!r}")
    h, m, s = (int(p) for p in parts)
    return float(h * 3600 + m * 60 + s)


def format_time(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}"


class _Feed:
    """Reads GTFS tables from a zip file or a folder."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.zip = zipfile.ZipFile(self.path) if self.path.is_file() else None

    def rows(self, name: str, required: bool = True) -> Iterable[Dict[str, str]]:
        try:
            if self.zip is not None:
                member = next((n for n in self.zip.namelist() if n.split("/")[-1] == name), None)
                if member is None:
                    raise KeyError(name)
                raw = self.zip.read(member)
            else:
                raw = (self.path / name).read_bytes()
        except (KeyError, FileNotFoundError):
            if required:
                raise GtfsError(f"The GTFS feed has no {name}") from None
            return []
        text = raw.decode("utf-8-sig")
        return list(csv.DictReader(io.StringIO(text)))


def _active_services(feed: _Feed, date: datetime.date) -> set:
    weekday = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][date.weekday()]
    key = date.strftime("%Y%m%d")
    active = set()
    calendar = feed.rows("calendar.txt", required=False)
    for row in calendar:
        if row.get("start_date", "") <= key <= row.get("end_date", "") and row.get(weekday, "0").strip() == "1":
            active.add(row["service_id"])
    exceptions = feed.rows("calendar_dates.txt", required=False)
    if not calendar and not exceptions:
        raise GtfsError("The GTFS feed has neither calendar.txt nor calendar_dates.txt")
    for row in exceptions:
        if row.get("date") == key:
            if row.get("exception_type", "").strip() == "1":
                active.add(row["service_id"])
            elif row.get("exception_type", "").strip() == "2":
                active.discard(row["service_id"])
    return active


def load_gtfs(path: Path, date: datetime.date, wheelchair_only: bool = False) -> TransitNetwork:
    """Load the trips running on ``date``.

    ``wheelchair_only`` keeps trips marked wheelchair_accessible=1 and stops
    not marked wheelchair_boarding=2.
    """
    feed = _Feed(path)
    stops: List[Stop] = []
    stop_index: Dict[str, int] = {}
    for row in feed.rows("stops.txt"):
        try:
            lat, lon = float(row["stop_lat"]), float(row["stop_lon"])
        except (KeyError, ValueError):
            continue
        if row.get("location_type", "0").strip() not in ("", "0"):
            continue  # stations, entrances: not boarding points
        if wheelchair_only and row.get("wheelchair_boarding", "").strip() == "2":
            continue
        stop_index[row["stop_id"]] = len(stops)
        stops.append(Stop(row["stop_id"], row.get("stop_name", row["stop_id"]), lat, lon,
                          row.get("wheelchair_boarding", "")))

    route_names = {
        r["route_id"]: (r.get("route_short_name") or r.get("route_long_name") or r["route_id"]).strip()
        for r in feed.rows("routes.txt")
    }
    services = _active_services(feed, date)
    trips: Dict[str, str] = {}
    for row in feed.rows("trips.txt"):
        if row.get("service_id") not in services:
            continue
        if wheelchair_only and row.get("wheelchair_accessible", "").strip() != "1":
            continue
        trips[row["trip_id"]] = row["route_id"]

    times: Dict[str, List[Tuple[int, int, float, float]]] = {}
    for row in feed.rows("stop_times.txt"):
        trip_id = row.get("trip_id")
        if trip_id not in trips or row.get("stop_id") not in stop_index:
            continue
        arr = parse_gtfs_time(row.get("arrival_time", ""))
        dep = parse_gtfs_time(row.get("departure_time", ""))
        if math.isnan(arr):
            arr = dep
        if math.isnan(dep):
            dep = arr
        times.setdefault(trip_id, []).append((int(row.get("stop_sequence", 0)), stop_index[row["stop_id"]], arr, dep))

    patterns: Dict[Tuple[str, Tuple[int, ...]], Pattern] = {}
    for trip_id, entries in times.items():
        entries.sort()
        # Untimed intermediate stops are skipped rather than interpolated.
        entries = [e for e in entries if not math.isnan(e[2])]
        if len(entries) < 2:
            continue
        seq = tuple(e[1] for e in entries)
        route_id = trips[trip_id]
        key = (route_id, seq)
        pattern = patterns.get(key)
        if pattern is None:
            pattern = patterns[key] = Pattern(route_id, route_names.get(route_id, route_id), list(seq))
        pattern.trips.append(trip_id)
        pattern.arrivals.append([e[2] for e in entries])
        pattern.departures.append([e[3] for e in entries])

    ordered: List[Pattern] = []
    for pattern in patterns.values():
        order = sorted(range(len(pattern.trips)), key=lambda t: pattern.departures[t][0])
        pattern.trips = [pattern.trips[t] for t in order]
        pattern.arrivals = [pattern.arrivals[t] for t in order]
        pattern.departures = [pattern.departures[t] for t in order]
        ordered.append(pattern)

    stop_patterns: List[List[Tuple[int, int]]] = [[] for _ in stops]
    for p_index, pattern in enumerate(ordered):
        for position, stop in enumerate(pattern.stops):
            stop_patterns[stop].append((p_index, position))

    transfers = _transfers(feed, stops, stop_index)
    return TransitNetwork(stops, ordered, stop_patterns, transfers, date)


def _transfers(feed: _Feed, stops: List[Stop], stop_index: Dict[str, int]) -> List[List[Tuple[int, float]]]:
    """Walking links between stops: transfers.txt, else stops within 300 m."""
    out: List[List[Tuple[int, float]]] = [[] for _ in stops]
    given = feed.rows("transfers.txt", required=False)
    for row in given:
        a, b = stop_index.get(row.get("from_stop_id", "")), stop_index.get(row.get("to_stop_id", ""))
        if a is None or b is None or a == b or row.get("transfer_type", "").strip() == "3":
            continue
        secs = float(row.get("min_transfer_time") or 0) or _metres(
            stops[a].lon, stops[a].lat, stops[b].lon, stops[b].lat) / WALK_SPEED_M_S
        out[a].append((b, max(MIN_TRANSFER_S, secs)))
    if given:
        return out
    cell = TRANSFER_RADIUS_M / 111_000.0
    grid: Dict[Tuple[int, int], List[int]] = {}
    for i, stop in enumerate(stops):
        grid.setdefault((int(stop.lon // cell), int(stop.lat // cell)), []).append(i)
    for i, stop in enumerate(stops):
        cx, cy = int(stop.lon // cell), int(stop.lat // cell)
        for dx in (-2, -1, 0, 1, 2):
            for dy in (-1, 0, 1):
                for j in grid.get((cx + dx, cy + dy), ()):
                    if j == i:
                        continue
                    d = _metres(stop.lon, stop.lat, stops[j].lon, stops[j].lat)
                    if d <= TRANSFER_RADIUS_M:
                        # Walking detour factor 1.3 over the straight line.
                        out[i].append((j, max(MIN_TRANSFER_S, d * 1.3 / WALK_SPEED_M_S)))
    return out


def raptor(
    network: TransitNetwork,
    depart_s: float,
    access: Dict[int, float],
    egress: Dict[int, float],
    max_rides: int = 4,
    min_change_s: float = MIN_TRANSFER_S,
    direct_walk_s: float = INF,
) -> Optional[Journey]:
    """Earliest-arrival journey.

    ``access``: stop -> walking seconds from the origin; ``egress``: stop ->
    walking seconds to the destination; ``direct_walk_s``: walking all the
    way. Ties in arrival go to the journey with fewer rides.
    """
    n = len(network.stops)
    rounds = max_rides + 1
    tau = [[INF] * n for _ in range(rounds)]
    best = [INF] * n
    parent: List[Dict[int, tuple]] = [dict() for _ in range(rounds)]
    marked = set()
    for stop, secs in access.items():
        t = depart_s + secs
        if t < tau[0][stop]:
            tau[0][stop] = best[stop] = t
            parent[0][stop] = ("access", secs)
            marked.add(stop)
    # Walking transfers from the access stops in round 0.
    _relax_footpaths(network, tau[0], best, parent[0], marked)

    def target_bound() -> float:
        bound = depart_s + direct_walk_s
        for stop, secs in egress.items():
            bound = min(bound, best[stop] + secs)
        return bound

    for k in range(1, rounds):
        if not marked:
            break
        queue: Dict[int, int] = {}
        for stop in marked:
            for p_index, position in network.stop_patterns[stop]:
                if position < queue.get(p_index, 1 << 30):
                    queue[p_index] = position
        marked = set()
        bound = target_bound()
        prev_tau = tau[k - 1]
        # tau_k starts as tau_{k-1}: a stop reached in an earlier round can
        # still be boarded from, even if this round did not improve it.
        tau[k] = list(prev_tau)
        cur = tau[k]
        for p_index, start in queue.items():
            pattern = network.patterns[p_index]
            trip = -1
            board = -1
            for position in range(start, len(pattern.stops)):
                stop = pattern.stops[position]
                if trip >= 0:
                    arr = pattern.arrivals[trip][position]
                    if arr < min(best[stop], bound):
                        cur[stop] = best[stop] = arr
                        parent[k][stop] = ("ride", p_index, trip, board, position)
                        marked.add(stop)
                ready = prev_tau[stop] + (min_change_s if k > 1 else 0.0)
                if ready < INF and (trip < 0 or ready <= pattern.departures[trip][position]):
                    candidate = _earliest_trip(pattern, position, ready)
                    if candidate is not None and (trip < 0 or candidate < trip):
                        trip = candidate
                        board = position
        _relax_footpaths(network, cur, best, parent[k], marked)

    # Best arrival at the destination over all rounds (fewer rides win ties).
    result = None
    best_arrival = depart_s + direct_walk_s
    for k in range(1, rounds):
        for stop, secs in egress.items():
            t = tau[k][stop] + secs
            if t < best_arrival - 1e-9:
                best_arrival = t
                result = (k, stop, secs)
    if result is None:
        return None
    return _reconstruct(network, parent, tau, depart_s, *result)


def _earliest_trip(pattern: Pattern, position: int, ready: float) -> Optional[int]:
    deps = [d[position] for d in pattern.departures]
    i = bisect.bisect_left(deps, ready)
    return i if i < len(deps) else None


def _relax_footpaths(network, tau_k, best, parent_k, marked) -> None:
    for stop in list(marked):
        base = tau_k[stop]
        for other, secs in network.transfers[stop]:
            t = base + secs
            if t < tau_k[other] and t < best[other]:
                tau_k[other] = best[other] = t
                parent_k[other] = ("walk", stop, secs)
                marked.add(other)


def _reconstruct(network, parent, tau, depart_s, k, stop, egress_s) -> Journey:
    legs: List[Leg] = []
    arrive = tau[k][stop] + egress_s
    legs.append(Leg("walk", stop, None, tau[k][stop], arrive))
    rides = 0
    while True:
        # A value copied forward from an earlier round has its label there.
        while k > 0 and stop not in parent[k]:
            k -= 1
        entry = parent[k].get(stop)
        if entry is None:
            break
        if entry[0] == "access":
            legs.append(Leg("walk", None, stop, depart_s, depart_s + entry[1]))
            break
        if entry[0] == "walk":
            _, from_stop, secs = entry
            legs.append(Leg("walk", from_stop, stop, tau[k][stop] - secs, tau[k][stop]))
            stop = from_stop
            continue
        _, p_index, trip, board, alight = entry
        pattern = network.patterns[p_index]
        legs.append(Leg(
            "ride", pattern.stops[board], stop,
            pattern.departures[trip][board], pattern.arrivals[trip][alight],
            route_name=pattern.route_name, trip_id=pattern.trips[trip],
            stop_sequence=pattern.stops[board:alight + 1],
        ))
        rides += 1
        stop = pattern.stops[board]
        k -= 1
    legs.reverse()
    return Journey(legs, depart_s, arrive, rides)
