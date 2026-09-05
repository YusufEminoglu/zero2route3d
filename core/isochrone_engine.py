"""3D Network-based Isochrone and Accessibility Catchment Engine."""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Tuple

from .input_validation import normalize_time_intervals
from .mobility_profiles import MobilityProfile, get_profile
from .network_policy import evaluate_edge_access, surface_quality
from .routing_engine import RoutingEngine3D, Waypoint


@dataclass
class IsochroneBand:
    """Individual time contour band (e.g. 0-5 min, 5-10 min)."""

    time_cutoff_min: float
    reachable_node_count: int
    approx_area_ha: float
    boundary_points: List[Tuple[float, float]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "time_cutoff_min": self.time_cutoff_min,
            "reachable_node_count": self.reachable_node_count,
            "approx_area_ha": round(self.approx_area_ha, 2),
            "boundary_points_count": len(self.boundary_points),
        }


@dataclass
class IsochroneResult:
    """Complete multi-tier 3D accessibility service area result."""

    origin: Waypoint
    profile: MobilityProfile
    bands: List[IsochroneBand]
    total_reachable_nodes: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "origin": {"lon": self.origin.lon, "lat": self.origin.lat, "name": self.origin.name},
            "profile_name": self.profile.name,
            "total_reachable_nodes": self.total_reachable_nodes,
            "bands": [b.to_dict() for b in self.bands],
        }


class IsochroneEngine3D:
    """Generates 3D multi-interval travel-time catchment zones from a topological graph."""

    def __init__(self, routing_engine: RoutingEngine3D) -> None:
        self.engine = routing_engine

    def compute_isochrones(
        self,
        origin: Waypoint,
        profile_key: str = "adult",
        time_intervals_min: Sequence[float] = (5.0, 10.0, 15.0, 20.0, 30.0),
    ) -> IsochroneResult:
        """Propagate Dijkstra wavefront along 3D graph up to maximum time interval."""
        profile = get_profile(profile_key)
        intervals_min = normalize_time_intervals(time_intervals_min)
        if not intervals_min:
            return IsochroneResult(origin, profile, [], 0)
        start_node = self.engine.find_nearest_node(
            (origin.lon, origin.lat),
            max_search_radius_m=self.engine.max_snap_distance_m,
        )

        if start_node is None or not self.engine.nodes:
            return IsochroneResult(origin, profile, [], 0)

        max_cutoff_s = max(intervals_min) * 60.0
        sorted_intervals_s = [t * 60.0 for t in intervals_min]

        # Dijkstra queue: (travel_time_seconds, node_id)
        pq: List[Tuple[float, int]] = [(0.0, start_node)]
        min_times: Dict[int, float] = {start_node: 0.0}
        visited = set()

        while pq:
            t_curr, u = heapq.heappop(pq)
            if u in visited:
                continue
            visited.add(u)

            if t_curr > max_cutoff_s:
                continue

            for v, seg_len, slope_pct, meta in self.engine.adj.get(u, []):
                if v in visited:
                    continue

                access_decision = evaluate_edge_access(profile, meta)
                if not access_decision.allowed:
                    continue

                cost = profile.calculate_edge_resistance(
                    length_m=seg_len,
                    slope_pct=slope_pct,
                    is_steps=meta.get("is_steps", False),
                    surface_quality=surface_quality(meta.get("surface")),
                    hierarchy_rank=meta.get("hierarchy", 4),
                )
                if not math.isfinite(cost):
                    continue

                seg_time_s = profile.travel_time_seconds(
                    seg_len,
                    slope_pct=slope_pct,
                    hierarchy_rank=meta.get("hierarchy", 4),
                )
                tentative_t = t_curr + seg_time_s * access_decision.penalty

                if tentative_t < min_times.get(v, float("inf")) and tentative_t <= max_cutoff_s:
                    min_times[v] = tentative_t
                    heapq.heappush(pq, (tentative_t, v))

        bands: List[IsochroneBand] = []
        for cutoff_s in sorted_intervals_s:
            cutoff_min = cutoff_s / 60.0
            nodes_in_band = [nid for nid, t in min_times.items() if t <= cutoff_s]
            pts = [(self.engine.nodes[nid][0], self.engine.nodes[nid][1]) for nid in nodes_in_band]

            boundary = _convex_hull(pts)
            area_ha = _polygon_area_ha(boundary)

            bands.append(
                IsochroneBand(
                    time_cutoff_min=cutoff_min,
                    reachable_node_count=len(nodes_in_band),
                    approx_area_ha=area_ha,
                    boundary_points=boundary,
                )
            )

        return IsochroneResult(
            origin=origin,
            profile=profile,
            bands=bands,
            total_reachable_nodes=len(min_times),
        )


def _convex_hull(points: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Return an ordered convex boundary using Andrew's monotonic chain."""
    unique = sorted(set(points))
    if len(unique) <= 2:
        return unique

    def cross(o: Tuple[float, float], a: Tuple[float, float], b: Tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: List[Tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: List[Tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def _polygon_area_ha(points: Sequence[Tuple[float, float]]) -> float:
    """Measure a small WGS84 hull in a local equirectangular metre plane."""
    if len(points) < 3:
        return 0.0
    mean_lat = math.radians(sum(point[1] for point in points) / len(points))
    metres_lon = 111320.0 * math.cos(mean_lat)
    metres_lat = 110574.0
    area_twice = 0.0
    for index, point in enumerate(points):
        nxt = points[(index + 1) % len(points)]
        x1, y1 = point[0] * metres_lon, point[1] * metres_lat
        x2, y2 = nxt[0] * metres_lon, nxt[1] * metres_lat
        area_twice += x1 * y2 - x2 * y1
    return abs(area_twice) * 0.5 / 10000.0
