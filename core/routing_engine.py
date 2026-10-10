"""High-performance 3D topological graph engine with Bidirectional A*, TSP, and OD Matrix."""
from __future__ import annotations

import heapq
import math
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .environmental_raster import EnvironmentalSurfaceSampler, MCDAWeights
from .input_validation import deduplicate_adjacent_coordinates, validate_waypoint_coordinates
from .kinematics import haversine_distance_2d
from .mobility_profiles import MobilityProfile, get_profile
from .network_source import RoadSegment
from .network_policy import evaluate_edge_access, surface_quality
from .profile_stats import (
    RouteStatistics,
    compute_route_statistics,
    densify_3d_linestring_indexed,
)
from .tsp_solver import solve_tsp_order


# Shortest horizontal run (m) over which an edge slope is measured. DEM heights
# are smooth over a few cells only; vertex heights from a 3D network are exact.
DEM_MIN_RUN_M = 10.0
VERTEX_MIN_RUN_M = 1.0
# Cost multiplier on the primary route's edges when searching an alternative.
ALTERNATIVE_PENALTY = 4.0

# Spatial hash for snapping: buckets of 1/300 degree (about 370 m north-south).
BUCKETS_PER_DEGREE = 300
# Metres per degree of latitude on the sphere used by haversine_distance_2d.
METRES_PER_DEGREE = 6371008.8 * math.pi / 180.0

# Built graphs kept in memory, keyed by network content and raster stack:
# routing the same network again (another profile, the OD matrix, a second
# click) skips graph building, DEM sampling and edge-cost evaluation. One
# graph only: a 100k-edge network holds about 140 MB. Plugin unload clears it.
GRAPH_CACHE_SIZE = 1
_GRAPH_CACHE: "OrderedDict[Tuple[Any, ...], Dict[str, Any]]" = OrderedDict()


def clear_graph_cache() -> None:
    """Forget every cached graph (tests, or after editing layers in place)."""
    _GRAPH_CACHE.clear()


def segments_digest(segments: Sequence[RoadSegment]) -> Tuple[int, int]:
    """Content key of a segment list: every attribute that changes the graph."""
    keys = []
    for seg in segments:
        try:
            keys.append(
                (
                    tuple(seg.p1),
                    tuple(seg.p2),
                    seg.length_m,
                    seg.highway_type,
                    seg.hierarchy_rank,
                    seg.lanes,
                    seg.is_steps,
                    seg.surface,
                    seg.is_oneway,
                    seg.name,
                    seg.access,
                    seg.foot,
                    seg.bicycle,
                    seg.motor_vehicle,
                    seg.lit,
                    seg.sidewalk,
                    seg.maxspeed_kmh,
                    seg.oneway_bicycle,
                    seg.oneway_foot,
                )
            )
        except AttributeError:
            keys.append(("unhashable", id(seg)))
    return len(keys), hash(tuple(keys))


def _distinct_route(primary, alternative, max_shared: float = 0.9) -> bool:
    """True when less than max_shared of the alternative's length repeats the primary."""
    def edges(coords):
        keys = set()
        for a, b in zip(coords, coords[1:]):
            ka = (round(a[0], 5), round(a[1], 5))
            kb = (round(b[0], 5), round(b[1], 5))
            keys.add((min(ka, kb), max(ka, kb)))
        return keys

    shared_keys = edges(primary)
    total = shared = 0.0
    for a, b in zip(alternative, alternative[1:]):
        d = haversine_distance_2d(a, b)
        total += d
        ka = (round(a[0], 5), round(a[1], 5))
        kb = (round(b[0], 5), round(b[1], 5))
        if (min(ka, kb), max(ka, kb)) in shared_keys:
            shared += d
    return total > 0 and shared / total < max_shared


@dataclass
class Waypoint:
    """Geographic stop point along the route."""

    lon: float
    lat: float
    name: str = ""
    elevation_m: Optional[float] = None


def _empty_statistics() -> RouteStatistics:
    """Return a consistent zero-result statistics object for failed routes."""
    return RouteStatistics(
        total_distance_m=0.0,
        total_duration_s=0.0,
        elevation_gain_m=0.0,
        elevation_loss_m=0.0,
        min_elevation_m=0.0,
        max_elevation_m=0.0,
        max_slope_pct=0.0,
        avg_slope_pct=0.0,
        total_calories_kcal=0.0,
        thermal_comfort_score=None,
    )


@dataclass
class RouteResult3D:
    """Complete 3D path result with densified geometry, statistics, and GeoJSON export."""

    coordinates_3d: List[Tuple[float, float, float]]
    statistics: RouteStatistics
    profile: MobilityProfile
    waypoints: List[Waypoint] = field(default_factory=list)
    is_network_matched: bool = True
    status_message: str = "Route computed successfully."
    alternative_routes: List[Dict[str, Any]] = field(default_factory=list)
    routing_diagnostics: Dict[str, Any] = field(default_factory=dict)

    @property
    def profile_key(self) -> str:
        """Convenience property to access profile key."""
        return self.profile.key

    @property
    def profile_name(self) -> str:
        """Convenience property to access profile display name."""
        return self.profile.name

    def to_geojson_feature(self) -> Dict[str, Any]:
        """Convert route to standard GeoJSON Feature with 3D LineString geometry and rich properties."""
        from .mobility_profiles import get_profile_color

        return {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [round(c[0], 6), round(c[1], 6), round(c[2], 2)]
                    for c in self.coordinates_3d
                ],
            },
            "properties": {
                "profile_name": self.profile.name,
                "profile_key": self.profile.key,
                "profile_category": self.profile.category,
                "base_speed_kmh": self.profile.base_speed_kmh,
                "profile_color": get_profile_color(self.profile.key),
                "distance_km": self.statistics.total_distance_km,
                "distance_m": self.statistics.total_distance_m,
                "duration_min": self.statistics.total_duration_min,
                "elevation_gain_m": self.statistics.elevation_gain_m,
                "elevation_loss_m": self.statistics.elevation_loss_m,
                "max_slope_pct": self.statistics.max_slope_pct,
                "avg_slope_pct": self.statistics.avg_slope_pct,
                "calories_kcal": self.statistics.total_calories_kcal,
                "thermal_comfort_score": self.statistics.thermal_comfort_score,
                "ada_compliant": self.statistics.ada_compliant,
                "slope_distribution": self.statistics.slope_distribution,
                "is_network_matched": self.is_network_matched,
                "status_message": self.status_message,
                "cue_sheet": [c.to_dict() for c in self.statistics.cue_sheet],
                "elevation_profile": self.statistics.elevation_profile,
                "alternative_count": len(self.alternative_routes),
                "routing_diagnostics": self.routing_diagnostics,
            },
        }

    def to_gpx(self) -> str:
        """Export route to standardized GPX 1.1 XML string."""
        trkpts = []
        for c in self.coordinates_3d:
            trkpts.append(
                f'      <trkpt lat="{c[1]:.6f}" lon="{c[0]:.6f}"><ele>{c[2]:.2f}</ele></trkpt>'
            )
        pts_xml = "\n".join(trkpts)
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="02Route 3D - QGIS" xmlns="http://www.topografix.com/GPX/1/1">
  <metadata>
    <name>02Route 3D - {self.profile.name}</name>
  </metadata>
  <trk>
    <name>3D Route ({self.profile.name})</name>
    <trkseg>
{pts_xml}
    </trkseg>
  </trk>
</gpx>"""


class RoutingEngine3D:
    """Topological graph builder, Bidirectional A*, and Multi-Criteria 3D Path engine."""

    def __init__(
        self,
        sampler: Optional[EnvironmentalSurfaceSampler] = None,
        weights: Optional[MCDAWeights] = None,
        max_snap_distance_m: float = 1000.0,
    ) -> None:
        self.sampler = sampler or EnvironmentalSurfaceSampler()
        self.weights = weights or MCDAWeights()
        self.nodes: Dict[int, Tuple[float, float, float]] = {}
        self.coord_to_node: Dict[Tuple[float, float], int] = {}
        self.adj: Dict[int, List[Tuple[int, float, float, Dict[str, Any]]]] = {}
        self.grid_buckets: Dict[Tuple[int, int], List[int]] = {}
        self.component_by_node: Dict[int, int] = {}
        self.component_sizes: Dict[int, int] = {}
        self.max_snap_distance_m = max(10.0, float(max_snap_distance_m))
        self.graph_diagnostics: Dict[str, Any] = {}
        self.last_segment_diagnostics: Dict[str, Any] = {}
        # Per-graph memo of edge costs, keyed by profile and weights; shared by
        # every engine that reuses the same cached graph.
        self._edge_cost_cache: Dict[Tuple[Any, ...], Dict[int, List[Optional[Tuple[float, str, str]]]]] = {}
        self._planar_kx = METRES_PER_DEGREE
        self._planar_ky = METRES_PER_DEGREE
        self._hierarchy_ranks: frozenset = frozenset()
        self._built = False

    _GRAPH_ATTRS = (
        "nodes",
        "coord_to_node",
        "adj",
        "grid_buckets",
        "component_by_node",
        "component_sizes",
        "graph_diagnostics",
        "_edge_cost_cache",
        "_planar_kx",
        "_planar_ky",
        "_hierarchy_ranks",
    )

    def build_graph(self, segments: Sequence[RoadSegment], use_cache: bool = True) -> None:
        """Build topological graph with spatial hash grid from road segments.

        With ``use_cache`` a graph built earlier from the same segments and the
        same raster stack is reused instead of rebuilt (see GRAPH_CACHE_SIZE).
        """
        cache_key: Optional[Tuple[Any, ...]] = None
        if use_cache:
            try:
                signature = self.sampler.signature()
                cache_key = (segments_digest(segments), signature) if signature is not None else None
            except Exception:  # noqa: BLE001 - an exotic sampler just skips the cache
                cache_key = None
            cached = _GRAPH_CACHE.get(cache_key) if cache_key is not None else None
            if cached is not None:
                _GRAPH_CACHE.move_to_end(cache_key)
                for name in self._GRAPH_ATTRS:
                    setattr(self, name, cached[name])
                self.graph_diagnostics = dict(cached["graph_diagnostics"], graph_cache="hit")
                self._built = True
                return

        # Fresh containers, never .clear(): a cached graph may share them.
        self.nodes = {}
        self.coord_to_node = {}
        self.adj = {}
        self.grid_buckets = {}
        self.component_by_node = {}
        self.component_sizes = {}
        self._edge_cost_cache = {}

        node_counter = 0
        skipped_invalid = 0
        skipped_duplicate = 0
        one_way_count = 0
        access_tagged_count = 0
        weak_adj: Dict[int, set] = {}

        def get_or_create_node(pt: Tuple[float, float, float]) -> int:
            nonlocal node_counter
            try:
                lon_value = float(pt[0])
                lat_value = float(pt[1])
                z_value = float(pt[2]) if len(pt) > 2 else 0.0
            except (IndexError, TypeError, ValueError, OverflowError):
                lon_value, lat_value, z_value = 0.0, 0.0, 0.0
            lon = lon_value if math.isfinite(lon_value) else 0.0
            lat = lat_value if math.isfinite(lat_value) else 0.0
            raw_z = z_value if math.isfinite(z_value) else 0.0

            key = (round(lon, 5), round(lat, 5))
            if key not in self.coord_to_node:
                # A height carried by the network's own 3D vertex wins; the DEM
                # is only sampled for nodes without one.
                sampled_z = self.sampler.sample_elevation(lon, lat) if raw_z == 0.0 else None
                if raw_z != 0.0:
                    z, source = raw_z, "vertex"
                elif sampled_z is not None and math.isfinite(sampled_z):
                    z, source = float(sampled_z), "dem"
                else:
                    # No elevation for this node: filled from its neighbours
                    # below instead of 0 m, which turned the edge of DEM
                    # coverage into cliffs.
                    z, source = 0.0, "missing"
                z_source[node_counter] = source
                self.coord_to_node[key] = node_counter
                self.nodes[node_counter] = (lon, lat, z)
                self.adj[node_counter] = []
                weak_adj[node_counter] = set()

                b_key = (math.floor(lon * BUCKETS_PER_DEGREE), math.floor(lat * BUCKETS_PER_DEGREE))
                if b_key not in self.grid_buckets:
                    self.grid_buckets[b_key] = []
                self.grid_buckets[b_key].append(node_counter)

                node_counter += 1
            return self.coord_to_node[key]

        # First pass: create every node, then fill nodes without elevation from
        # their neighbours, so edge slopes below are computed on final heights.
        z_source: Dict[int, str] = {}
        endpoint_pairs: List[Tuple[int, int]] = []
        valid_segments: List[Tuple[RoadSegment, int, int]] = []
        for seg in segments:
            if not seg or len(seg.p1) < 2 or len(seg.p2) < 2:
                skipped_invalid += 1
                continue
            try:
                if not all(math.isfinite(float(v)) for v in (*seg.p1[:2], *seg.p2[:2])):
                    skipped_invalid += 1
                    continue
            except (TypeError, ValueError):
                skipped_invalid += 1
                continue
            pair = (get_or_create_node(seg.p1), get_or_create_node(seg.p2))
            endpoint_pairs.append(pair)
            valid_segments.append((seg, pair[0], pair[1]))
        missing_elevation = self._fill_missing_elevation(z_source, endpoint_pairs)

        edge_keys = set()
        for seg, u, v in valid_segments:
            if u == v:
                skipped_invalid += 1
                continue

            # Parallel roads between the same rounded nodes can carry different
            # direction, access, surface or hierarchy attributes. Collapsing on
            # (u, v) alone silently discarded valid alternatives and could even
            # remove the reverse direction supplied by a later two-way feature.
            edge_key = (
                u,
                v,
                str(seg.highway_type),
                int(seg.hierarchy_rank),
                bool(seg.is_oneway),
                str(seg.surface),
                str(getattr(seg, "name", "")),
                str(getattr(seg, "access", "")),
                str(getattr(seg, "foot", "")),
                str(getattr(seg, "bicycle", "")),
                str(getattr(seg, "motor_vehicle", "")),
            )
            if edge_key in edge_keys:
                skipped_duplicate += 1
                continue
            edge_keys.add(edge_key)

            p1_z = self.nodes[u][2]
            p2_z = self.nodes[v][2]
            dz = p2_z - p1_z
            dist_2d = haversine_distance_2d(self.nodes[u], self.nodes[v])
            # Heights sampled from a DEM (or filled) are only meaningful over a
            # few raster cells: over a 1 m segment, half a metre of DEM noise
            # is a 50 % "slope" that blocks wheelchairs and vehicles. Measure
            # such slopes over at least DEM_MIN_RUN_M; heights carried by the
            # network's own 3D vertices are trusted over shorter runs.
            measured = z_source.get(u) == "vertex" and z_source.get(v) == "vertex"
            run = max(dist_2d, VERTEX_MIN_RUN_M if measured else DEM_MIN_RUN_M)
            slope_pct = (dz / run) * 100.0 if dist_2d > 0.1 else 0.0
            if not math.isfinite(slope_pct):
                slope_pct = 0.0

            seg_len = float(seg.length_m) if math.isfinite(seg.length_m) and seg.length_m > 0 else max(0.1, dist_2d)

            meta_forward = {
                "length_m": seg_len,
                "slope_pct": slope_pct,
                "highway": seg.highway_type,
                "hierarchy": seg.hierarchy_rank,
                "lanes": getattr(seg, "lanes", None),
                "name": getattr(seg, "name", "") or "",
                "is_steps": seg.is_steps,
                "surface": seg.surface,
                "access": getattr(seg, "access", ""),
                "foot": getattr(seg, "foot", ""),
                "bicycle": getattr(seg, "bicycle", ""),
                "motor_vehicle": getattr(seg, "motor_vehicle", ""),
                "lit": getattr(seg, "lit", ""),
                "sidewalk": getattr(seg, "sidewalk", ""),
                "maxspeed_kmh": getattr(seg, "maxspeed_kmh", None),
                "oneway": bool(seg.is_oneway),
                "oneway_bicycle": getattr(seg, "oneway_bicycle", ""),
                "oneway_foot": getattr(seg, "oneway_foot", ""),
                "against_oneway": False,
            }
            self.adj[u].append((v, seg_len, slope_pct, meta_forward))
            weak_adj[u].add(v)
            weak_adj[v].add(u)

            if seg.is_oneway:
                one_way_count += 1
            if any(
                getattr(seg, tag, "")
                for tag in ("access", "foot", "bicycle", "motor_vehicle")
            ):
                access_tagged_count += 1

            # The reverse edge always exists: whether a mode may use it against
            # a one-way street is a per-profile access decision
            # (network_policy.evaluate_edge_access). Dropping it here made every
            # one-way street one-way for pedestrians too, and ignored
            # oneway:bicycle=no contra-flow lanes.
            meta_reverse = dict(meta_forward)
            meta_reverse["slope_pct"] = -slope_pct
            meta_reverse["against_oneway"] = bool(seg.is_oneway)
            meta_reverse["reverse_of_two_way"] = not seg.is_oneway
            self.adj[v].append((u, seg_len, -slope_pct, meta_reverse))

        # Weakly connected components are used only for snapping. Traversability
        # remains directed in A*. The previous outgoing-only BFS made component
        # membership depend on feature order for converging one-way streets.
        comp_id = 0
        for seed in self.nodes:
            if seed in self.component_by_node:
                continue
            stack = [seed]
            self.component_by_node[seed] = comp_id
            size = 0
            while stack:
                curr = stack.pop()
                size += 1
                for neighbour in weak_adj.get(curr, set()):
                    if neighbour not in self.component_by_node:
                        self.component_by_node[neighbour] = comp_id
                        stack.append(neighbour)
            self.component_sizes[comp_id] = size
            comp_id += 1

        directed_edges = sum(len(edges) for edges in self.adj.values())
        self.graph_diagnostics = {
            "input_segments": len(segments),
            "node_count": len(self.nodes),
            "directed_edge_count": directed_edges,
            "component_count": len(self.component_sizes),
            "largest_component_nodes": max(self.component_sizes.values(), default=0),
            "one_way_segments": one_way_count,
            "access_tagged_segments": access_tagged_count,
            "skipped_invalid_segments": skipped_invalid,
            "skipped_duplicate_segments": skipped_duplicate,
            "elevation_filled_nodes": missing_elevation["filled"],
            "elevation_missing_nodes": missing_elevation["unresolved"],
            "graph_cache": "miss" if cache_key is not None else "off",
        }
        self._prepare_planar_heuristic()
        self._hierarchy_ranks = frozenset(
            meta.get("hierarchy", 4) for edges in self.adj.values() for _v, _l, _s, meta in edges
        )
        self._built = True
        if cache_key is not None:
            _GRAPH_CACHE[cache_key] = {name: getattr(self, name) for name in self._GRAPH_ATTRS}
            while len(_GRAPH_CACHE) > GRAPH_CACHE_SIZE:
                _GRAPH_CACHE.popitem(last=False)

    def _prepare_planar_heuristic(self) -> None:
        """Scale factors for a cheap straight-line distance that never exceeds haversine.

        Longitude degrees shrink toward the poles; using the cosine of the
        graph's highest latitude makes every east-west distance an
        under-estimate, and 0.999 absorbs the curvature term. An admissible
        A* heuristic needs exactly that, without trigonometry per node.
        """
        max_abs_lat = max((abs(c[1]) for c in self.nodes.values()), default=0.0)
        self._planar_ky = METRES_PER_DEGREE * 0.999
        self._planar_kx = METRES_PER_DEGREE * 0.999 * math.cos(math.radians(min(89.9, max_abs_lat)))

    def planar_distance_lower_bound(self, a: Sequence[float], b: Sequence[float]) -> float:
        """Metres between two (lon, lat) points, never more than the great-circle distance."""
        dx = (a[0] - b[0]) * self._planar_kx
        dy = (a[1] - b[1]) * self._planar_ky
        return math.sqrt(dx * dx + dy * dy)

    def _fill_missing_elevation(
        self,
        z_source: Dict[int, str],
        pairs: Sequence[Tuple[int, int]],
    ) -> Dict[str, int]:
        """Give nodes without elevation the mean height of known neighbours.

        Spreads outward ring by ring from nodes with a vertex or DEM height. A
        component with no height anywhere stays at 0 m (flat), which is the
        only honest choice without data; the count is reported in the graph
        diagnostics.
        """
        missing = {n for n, src in z_source.items() if src == "missing"}
        if not missing:
            return {"filled": 0, "unresolved": 0}
        neighbours: Dict[int, List[int]] = {}
        for a, b in pairs:
            if a == b:
                continue
            neighbours.setdefault(a, []).append(b)
            neighbours.setdefault(b, []).append(a)
        filled = 0
        frontier = missing
        while frontier:
            resolved: Dict[int, float] = {}
            for node in frontier:
                known = [self.nodes[n][2] for n in neighbours.get(node, []) if n not in missing]
                if known:
                    resolved[node] = sum(known) / len(known)
            if not resolved:
                break
            for node, z in resolved.items():
                lon, lat, _ = self.nodes[node]
                self.nodes[node] = (lon, lat, z)
                z_source[node] = "filled"
                missing.discard(node)
            filled += len(resolved)
            frontier = set(missing)
        return {"filled": filled, "unresolved": len(missing)}

    def _nearby_nodes(self, lon: float, lat: float, radius_m: float) -> Iterable[int]:
        """Nodes in every hash bucket that can lie within radius_m of (lon, lat)."""
        bucket_m = METRES_PER_DEGREE / BUCKETS_PER_DEGREE
        cos_lat = max(0.01, math.cos(math.radians(min(89.0, abs(lat) + radius_m / METRES_PER_DEGREE))))
        span_y = int(math.ceil(radius_m / bucket_m)) + 1
        span_x = int(math.ceil(radius_m / (bucket_m * cos_lat))) + 1
        bx = math.floor(lon * BUCKETS_PER_DEGREE)
        by = math.floor(lat * BUCKETS_PER_DEGREE)
        buckets = self.grid_buckets
        # Few nodes but a huge search window (sparse rural graphs): scanning
        # the nodes is cheaper than visiting thousands of empty buckets.
        if (2 * span_x + 1) * (2 * span_y + 1) > len(self.nodes):
            yield from self.nodes
            return
        for dx in range(-span_x, span_x + 1):
            for dy in range(-span_y, span_y + 1):
                yield from buckets.get((bx + dx, by + dy), ())

    def _nearest_by_component(
        self, coord: Tuple[float, float], radius_m: float
    ) -> Dict[int, Tuple[float, int]]:
        """Nearest node within radius_m in each connected component: {component: (metres, node)}.

        Candidates are ranked by a local flat-earth distance (exact to well
        under 0.1 % at snapping range); only each component's winner gets the
        great-circle distance that callers see.
        """
        lon, lat = float(coord[0]), float(coord[1])
        best: Dict[int, Tuple[float, int]] = {}
        if not (math.isfinite(lon) and math.isfinite(lat)):
            return best
        nodes = self.nodes
        components = self.component_by_node
        kx = METRES_PER_DEGREE * math.cos(math.radians(lat))
        ky = METRES_PER_DEGREE
        limit = (radius_m * 1.001) ** 2
        for nid in self._nearby_nodes(lon, lat, radius_m):
            c = nodes[nid]
            dx = (c[0] - lon) * kx
            dy = (c[1] - lat) * ky
            d2 = dx * dx + dy * dy
            if d2 > limit:
                continue
            comp = components.get(nid, -1)
            current = best.get(comp)
            if current is None or d2 < current[0] or (d2 == current[0] and nid < current[1]):
                best[comp] = (d2, nid)
        result: Dict[int, Tuple[float, int]] = {}
        for comp, (_d2, nid) in best.items():
            d = haversine_distance_2d((lon, lat), nodes[nid])
            if d <= radius_m:
                result[comp] = (d, nid)
        return result

    def find_nearest_node(
        self,
        coord: Tuple[float, float],
        target_component: Optional[int] = None,
        max_search_radius_m: float = 2500.0,
    ) -> Optional[int]:
        """Nearest graph node within max_search_radius_m (optionally in one component)."""
        if not self.nodes or not coord or len(coord) < 2:
            return None
        best = self._nearest_by_component(coord, max_search_radius_m)
        if target_component is not None:
            hit = best.get(target_component)
            return hit[1] if hit else None
        if not best:
            return None
        return min(best.values())[1]

    def find_compatible_nodes(
        self,
        origin: Tuple[float, float],
        destination: Tuple[float, float],
    ) -> Tuple[Optional[int], Optional[int]]:
        """Snap origin and destination into one shared connected component.

        Picks the component with the smallest total snap distance (ties: the
        larger component). The search radius doubles from 64 m up to the snap
        limit and stops once a shared component's total is within the radius:
        any component not yet seen is farther than that for at least one
        point, so it cannot do better. The old code ran a separate full search
        per component, O(components x nodes).
        """
        if not self.nodes or not self.component_sizes:
            return None, None
        memo_key = (
            round(float(origin[0]), 7), round(float(origin[1]), 7),
            round(float(destination[0]), 7), round(float(destination[1]), 7),
        )
        memo = self.__dict__.setdefault("_snap_memo", {})
        if memo.get("graph") is not self.adj:
            memo.clear()
            memo["graph"] = self.adj
        if memo_key in memo:
            return memo[memo_key]

        limit = self.max_snap_distance_m
        radius = min(64.0, limit)
        while True:
            near_origin = self._nearest_by_component(origin, radius)
            near_destination = self._nearest_by_component(destination, radius)
            shared = [
                (d_o + near_destination[comp][0], -self.component_sizes.get(comp, 0), n_o, near_destination[comp][1])
                for comp, (d_o, n_o) in near_origin.items()
                if comp in near_destination
            ]
            if shared and min(shared)[0] <= radius:
                break
            if radius >= limit:
                break
            radius = min(limit, radius * 2.0)

        if shared:
            _score, _size, start_node, end_node = min(shared)
            answer: Tuple[Optional[int], Optional[int]] = (start_node, end_node)
        else:
            answer = (
                min(near_origin.values())[1] if near_origin else None,
                min(near_destination.values())[1] if near_destination else None,
            )
        if len(memo) > 4096:
            memo.clear()
            memo["graph"] = self.adj
        memo[memo_key] = answer
        return answer

    def _edge_table(self, profile: MobilityProfile) -> Dict[int, List[Optional[Tuple[float, str, str]]]]:
        """Memo of evaluated edges for this profile and weight set (see _evaluate_edge)."""
        w_dict = self.weights.normalized_dict()
        key = (
            repr(profile),
            tuple(sorted(w_dict.items())),
            float(self.weights.weight_extra),
        )
        table = self._edge_cost_cache.get(key)
        if table is None:
            table = {}
            self._edge_cost_cache[key] = table
        return table

    def _evaluate_edge(
        self,
        edge: Tuple[int, float, float, Dict[str, Any]],
        profile: MobilityProfile,
        w_dict: Dict[str, float],
    ) -> Tuple[float, str, str]:
        """Cost of one directed edge for this profile: (cost, status, reason).

        status is "ok", "access" (blocked by modal access rules, reason set)
        or "profile" (the profile cannot traverse it). A cost depends only on
        the edge, the profile, the weights and the rasters at the edge's end,
        so it is computed once per graph and memoised (see _edge_table).
        """
        v, seg_len, slope_pct, meta = edge
        access_decision = evaluate_edge_access(profile, meta)
        if not access_decision.allowed:
            return (math.inf, "access", access_decision.reason)
        v_coord = self.nodes[v]
        lst_val = self.sampler.sample_lst(v_coord[0], v_coord[1])
        green_val = self.sampler.sample_greenery(v_coord[0], v_coord[1])
        extra_values = self.sampler.sample_additional_resistance(v_coord[0], v_coord[1])
        edge_cost = profile.calculate_edge_resistance(
            length_m=seg_len,
            slope_pct=slope_pct,
            is_steps=meta.get("is_steps", False),
            surface_quality=surface_quality(meta.get("surface")),
            hierarchy_rank=meta.get("hierarchy", 4),
            lst_normalized=lst_val,
            green_normalized=green_val,
            custom_weights=w_dict,
            maxspeed_kmh=meta.get("maxspeed_kmh"),
        )
        if not math.isfinite(edge_cost) or edge_cost < 0:
            return (math.inf, "profile", "")
        edge_cost *= access_decision.penalty
        # Every additional raster contributes its real normalized value.
        # The mean keeps the factor stable when the user adds many layers;
        # unavailable layers are omitted by the sampler, never fabricated.
        if extra_values:
            extra_mean = sum(extra_values) / len(extra_values)
            edge_cost *= 1.0 + max(0.0, min(1.0, extra_mean)) * self.weights.weight_extra
        return (edge_cost, "ok", "")

    def _search(
        self,
        start_node: int,
        profile: MobilityProfile,
        targets: Set[int],
        goal: Optional[int] = None,
        avoid_edges: Optional[set] = None,
        penalize_edges: Optional[set] = None,
        penalty_factor: float = 1.0,
    ) -> Tuple[Dict[int, int], Set[int], Dict[str, Any]]:
        """Least-cost search from start_node until every target is settled.

        With a single ``goal`` this is A* with an admissible straight-line
        heuristic; with several targets it is a one-to-many Dijkstra, which is
        how the OD matrix answers a whole row with one search.
        Returns (predecessors, settled nodes, counters).
        """
        nodes = self.nodes
        adj = self.adj
        table = self._edge_table(profile)
        w_dict = self.weights.normalized_dict()
        evaluate = self._evaluate_edge
        avoid_set = avoid_edges or set()
        penalized = penalize_edges or set()
        penalty = max(1.0, float(penalty_factor)) if math.isfinite(penalty_factor) else 1.0

        # A* is only optimal when the heuristic never over-estimates the
        # remaining cost. g accumulates impedance, not metres, and impedance
        # can be well below 1.0 per metre (a car on a motorway, a shaded edge),
        # so the distance is scaled by the profile's own cost floor.
        if goal is not None:
            gx, gy = nodes[goal][0], nodes[goal][1]
            floor = profile.min_cost_per_metre(
                hierarchy_ranks=self._hierarchy_ranks or None,
                thermal_bonus=getattr(self.sampler, "green_layer", True) is not None,
            )
            kx = self._planar_kx * floor
            ky = self._planar_ky * floor

            def heuristic(nid: int) -> float:
                c = nodes[nid]
                dx = (c[0] - gx) * kx
                dy = (c[1] - gy) * ky
                return math.sqrt(dx * dx + dy * dy)
        else:
            def heuristic(nid: int) -> float:
                return 0.0

        remaining = set(targets)
        pq: List[Tuple[float, float, int]] = [(heuristic(start_node), 0.0, start_node)]
        g_scores: Dict[int, float] = {start_node: 0.0}
        prev_map: Dict[int, int] = {}
        visited: Set[int] = set()
        max_iters = min(150_000, len(nodes) * 3) if goal is not None else len(nodes) * 3 + 10
        iters = 0
        blocked_by_access = 0
        blocked_by_profile = 0
        access_reasons: Dict[str, int] = {}

        while pq and iters < max_iters and remaining:
            iters += 1
            _f, cost, u = heapq.heappop(pq)
            if u in visited:
                continue
            visited.add(u)
            remaining.discard(u)
            if not remaining:
                break
            edges = adj.get(u, ())
            row = table.get(u)
            if row is None:
                row = [None] * len(edges)
                table[u] = row
            for index, edge in enumerate(edges):
                v = edge[0]
                if v in visited:
                    continue
                entry = row[index]
                if entry is None:
                    entry = row[index] = evaluate(edge, profile, w_dict)
                edge_cost, status, reason = entry
                if status != "ok":
                    if status == "access":
                        blocked_by_access += 1
                        access_reasons[reason] = access_reasons.get(reason, 0) + 1
                    else:
                        blocked_by_profile += 1
                    continue
                if avoid_set and (u, v) in avoid_set:
                    continue
                if penalized and ((u, v) in penalized or (v, u) in penalized):
                    edge_cost *= penalty
                tentative_g = cost + edge_cost
                if tentative_g < g_scores.get(v, math.inf):
                    g_scores[v] = tentative_g
                    prev_map[v] = u
                    heapq.heappush(pq, (tentative_g + heuristic(v), tentative_g, v))

        counters = {
            "expanded_nodes": len(visited),
            "blocked_by_access": blocked_by_access,
            "blocked_by_profile": blocked_by_profile,
            "access_reasons": access_reasons,
        }
        return prev_map, visited, counters

    def compute_segment_route(
        self,
        start_pt: Tuple[float, float],
        end_pt: Tuple[float, float],
        profile: MobilityProfile,
        avoid_edges: Optional[set] = None,
        penalize_edges: Optional[set] = None,
        penalty_factor: float = 1.0,
        search_trees: Optional[Dict[int, Tuple[Dict[int, int], Set[int], Dict[str, Any]]]] = None,
    ) -> Tuple[List[Tuple[float, float, float]], bool]:
        """Compute A* least-cost path between single origin and destination pair.

        ``avoid_edges`` are forbidden; ``penalize_edges`` (either direction)
        cost ``penalty_factor`` times more, which is how alternatives are found
        without failing where the primary route uses the only bridge.
        ``search_trees`` maps a start node to a finished one-to-many search
        (see _search); the OD matrix passes it to reuse one search per origin.
        """
        if not self.nodes:
            self.last_segment_diagnostics = {"status": "empty_graph"}
            return [], False

        start_node, end_node = self.find_compatible_nodes(start_pt, end_pt)

        if start_node is None or end_node is None:
            self.last_segment_diagnostics = {
                "status": "snap_failed",
                "max_snap_distance_m": self.max_snap_distance_m,
            }
            return [], False

        start_snap_m = haversine_distance_2d(start_pt, self.nodes[start_node])
        end_snap_m = haversine_distance_2d(end_pt, self.nodes[end_node])

        if start_node == end_node:
            z1 = self._endpoint_elevation(start_pt, start_node)
            dist_d = haversine_distance_2d(start_pt, end_pt)
            if dist_d < 0.1:
                self.last_segment_diagnostics = {
                    "status": "coincident",
                    "start_snap_m": start_snap_m,
                    "end_snap_m": end_snap_m,
                }
                return [(start_pt[0], start_pt[1], z1)], True
            self.last_segment_diagnostics = {
                "status": "same_network_node",
                "start_snap_m": start_snap_m,
                "end_snap_m": end_snap_m,
            }
            return [], False

        tree = search_trees.get(start_node) if search_trees else None
        if tree is not None and end_node in tree[1] and not avoid_edges and not penalize_edges:
            prev_map, _visited, counters = tree
        else:
            prev_map, _visited, counters = self._search(
                start_node,
                profile,
                {end_node},
                goal=end_node,
                avoid_edges=avoid_edges,
                penalize_edges=penalize_edges,
                penalty_factor=penalty_factor,
            )

        if end_node not in prev_map:
            self.last_segment_diagnostics = {
                "status": "no_directed_path",
                "start_snap_m": start_snap_m,
                "end_snap_m": end_snap_m,
                **counters,
            }
            return [], False

        # Reconstruct path
        path: List[Tuple[float, float, float]] = []
        curr: Optional[int] = end_node
        while curr is not None:
            path.append(self.nodes[curr])
            curr = prev_map.get(curr) if curr != start_node else None

        path.reverse()
        z_start = self._endpoint_elevation(start_pt, start_node)
        z_end = self._endpoint_elevation(end_pt, end_node)

        final_path: List[Tuple[float, float, float]] = []
        p_start_3d = (start_pt[0], start_pt[1], z_start)
        if not path or haversine_distance_2d(start_pt, path[0]) >= 0.1:
            final_path.append(p_start_3d)

        final_path.extend(path)

        p_end_3d = (end_pt[0], end_pt[1], z_end)
        if not final_path or haversine_distance_2d(final_path[-1], end_pt) >= 0.1:
            final_path.append(p_end_3d)

        self.last_segment_diagnostics = {
            "status": "matched",
            "start_snap_m": start_snap_m,
            "end_snap_m": end_snap_m,
            **counters,
        }
        return final_path, True

    def _endpoint_elevation(self, point: Tuple[float, float], snapped_node: int) -> float:
        """Height of a route end point: the DEM there, else its snapped node's height.

        It used to fall back to 0 m, so without a DEM (heights from a 3D
        network) the short link to the first node became a cliff: a 40 m
        node 20 m away read as a 200 % slope in the route statistics.
        """
        sampled = self.sampler.sample_elevation(point[0], point[1])
        if sampled is not None and math.isfinite(sampled):
            return float(sampled)
        return float(self.nodes[snapped_node][2])

    def _sample_series(
        self,
        coords: Sequence[Sequence[float]],
        sampler_fn: Any,
    ) -> Optional[List[Optional[float]]]:
        """Sample one environmental surface along the densified route.

        Returns None when the surface yielded no real value anywhere, so callers can
        drop the criterion entirely rather than average in a fabricated constant.
        """
        if not coords:
            return None
        # No raster behind this surface: nothing to sample, skip densifying.
        owner = getattr(sampler_fn, "__self__", None)
        layer_attr = {"sample_lst": "lst_layer", "sample_greenery": "green_layer"}.get(
            getattr(sampler_fn, "__name__", ""), ""
        )
        if owner is not None and layer_attr and getattr(owner, layer_attr, True) is None:
            return None
        dense, _src = densify_3d_linestring_indexed(coords, sample_interval_m=6.0)
        values: List[Optional[float]] = []
        found_any = False
        for pt in dense:
            value = sampler_fn(pt[0], pt[1])
            if value is not None:
                found_any = True
            values.append(value)
        return values if found_any else None

    def _segment_metadata_for(
        self,
        coords: Sequence[Sequence[float]],
    ) -> Optional[List[Dict[str, Any]]]:
        """Recover each route segment's real road attributes from the graph.

        Hierarchy, lane count and the OSM street name are carried on the edge; before
        this they were dropped during densification and replaced with constants.
        """
        if len(coords) < 2:
            return None
        meta: List[Dict[str, Any]] = []
        found_any = False
        for index in range(len(coords) - 1):
            key_a = (round(coords[index][0], 5), round(coords[index][1], 5))
            key_b = (round(coords[index + 1][0], 5), round(coords[index + 1][1], 5))
            node_a = self.coord_to_node.get(key_a)
            node_b = self.coord_to_node.get(key_b)
            entry: Dict[str, Any] = {}
            if node_a is not None and node_b is not None:
                for v, _seg_len, _slope, edge_meta in self.adj.get(node_a, []):
                    if v == node_b:
                        entry = {
                            "hierarchy": edge_meta.get("hierarchy", 4),
                            "lanes": edge_meta.get("lanes"),
                            "street_name": edge_meta.get("name"),
                            "surface": edge_meta.get("surface"),
                        }
                        found_any = True
                        break
            meta.append(entry)
        return meta if found_any else None

    def calculate_route(
        self,
        waypoints: Sequence[Waypoint],
        profile_key: str = "adult",
        optimize_tsp: bool = False,
        compute_alternatives: bool = True,
        search_trees: Optional[Dict[int, Tuple[Dict[int, int], Set[int], Dict[str, Any]]]] = None,
        detail: bool = True,
    ) -> RouteResult3D:
        """Compute complete multi-stop 3D route traversing all waypoints.

        ``detail=False`` returns totals only (no elevation profile, cue sheet
        or environmental samples), for matrix-style callers.
        """
        profile = get_profile(profile_key)
        validation_error = validate_waypoint_coordinates(waypoints)
        if validation_error:
            return RouteResult3D(
                coordinates_3d=[],
                statistics=_empty_statistics(),
                profile=profile,
                waypoints=list(waypoints),
                is_network_matched=False,
                status_message=validation_error,
            )
        if not waypoints or len(waypoints) < 2:
            coords = []
            stats = _empty_statistics()
            if waypoints and len(waypoints) == 1:
                w0 = waypoints[0]
                z0 = (
                    w0.elevation_m
                    if w0.elevation_m is not None and math.isfinite(w0.elevation_m)
                    else (self.sampler.sample_elevation(w0.lon, w0.lat) or 0.0)
                )
                coords = [(w0.lon, w0.lat, z0)]
                stats.min_elevation_m = z0
                stats.max_elevation_m = z0
            return RouteResult3D(
                coordinates_3d=coords,
                statistics=stats,
                profile=profile,
                waypoints=list(waypoints) if waypoints else [],
                is_network_matched=False,
                status_message="At least 2 waypoints are required." if len(waypoints) < 2 else "Route computed successfully.",
            )

        wp_list = list(waypoints)

        # Optional TSP optimization for >2 waypoints
        if optimize_tsp and len(wp_list) > 2:
            pts_tuples = [
                (w.lon, w.lat, self.sampler.sample_elevation(w.lon, w.lat) or 0.0)
                for w in wp_list
            ]
            ordered_indices = solve_tsp_order(pts_tuples, fix_start=True, fix_end=True)
            wp_list = [wp_list[idx] for idx in ordered_indices]

        all_coords: List[Tuple[float, float, float]] = []
        matched_all = True
        segment_diagnostics: List[Dict[str, Any]] = []

        for i in range(len(wp_list) - 1):
            w1 = wp_list[i]
            w2 = wp_list[i + 1]
            seg_coords, matched = self.compute_segment_route(
                (w1.lon, w1.lat),
                (w2.lon, w2.lat),
                profile,
                search_trees=search_trees,
            )
            segment_diagnostics.append(dict(self.last_segment_diagnostics))
            if not matched:
                matched_all = False
                diagnostic_status = self.last_segment_diagnostics.get("status")
                if diagnostic_status == "snap_failed":
                    failure_message = (
                        "No network node lies within "
                        f"{self.max_snap_distance_m:.0f} m of both route points. "
                        "Move the points closer to the network or increase the snap limit."
                    )
                elif self.last_segment_diagnostics.get("blocked_by_access", 0) > 0:
                    failure_message = (
                        f"No legal {profile.name} route was found; "
                        f"{self.last_segment_diagnostics['blocked_by_access']} explored "
                        "edges were excluded by modal access rules."
                    )
                else:
                    failure_message = (
                        f"No connected network route was found between "
                        f"'{w1.name or 'the origin'}' and "
                        f"'{w2.name or 'the destination'}'."
                    )
                return RouteResult3D(
                    coordinates_3d=[],
                    statistics=_empty_statistics(),
                    profile=profile,
                    waypoints=wp_list,
                    is_network_matched=False,
                    status_message=failure_message,
                    routing_diagnostics={
                        "graph": dict(self.graph_diagnostics),
                        "segments": segment_diagnostics,
                    },
                )

            if all_coords:
                all_coords.extend(seg_coords[1:])
            else:
                all_coords.extend(seg_coords)

        all_coords = deduplicate_adjacent_coordinates(all_coords)
        stats = compute_route_statistics(
            all_coords,
            profile,
            lst_samples=self._sample_series(all_coords, self.sampler.sample_lst) if detail else None,
            green_samples=self._sample_series(all_coords, self.sampler.sample_greenery) if detail else None,
            segment_metadata=self._segment_metadata_for(all_coords),
            detail=detail,
        )

        # Compute Alternative Route (e.g. Flattest or Coolest)
        alternatives: List[Dict[str, Any]] = []
        if compute_alternatives and len(wp_list) == 2 and matched_all:
            # Build penalty set along primary path to find genuine alternative
            edge_set = {
                (self.coord_to_node.get((round(all_coords[k][0], 5), round(all_coords[k][1], 5))),
                 self.coord_to_node.get((round(all_coords[k+1][0], 5), round(all_coords[k+1][1], 5))))
                for k in range(len(all_coords) - 1)
                if self.coord_to_node.get((round(all_coords[k][0], 5), round(all_coords[k][1], 5))) is not None
                and self.coord_to_node.get((round(all_coords[k+1][0], 5), round(all_coords[k+1][1], 5))) is not None
            }
            # The alternative must use the *requested* profile: routing a wheelchair
            # request as a scenic pedestrian path produced an "alternative" that could
            # cross stairs the primary profile forbids.
            # Penalty method: edges of the primary route cost several times more
            # instead of being forbidden, so an alternative is still found where
            # the primary route crosses the only bridge, and it shares as little
            # of the primary route as is reasonable.
            alt_coords, alt_matched = self.compute_segment_route(
                (wp_list[0].lon, wp_list[0].lat),
                (wp_list[1].lon, wp_list[1].lat),
                profile,
                penalize_edges=edge_set,
                penalty_factor=ALTERNATIVE_PENALTY,
            )
            if alt_matched and len(alt_coords) > 2 and _distinct_route(all_coords, alt_coords):
                alt_stats = compute_route_statistics(alt_coords, profile)
                alternatives.append(
                    {
                        "name": "Alternative Route",
                        "distance_km": alt_stats.total_distance_km,
                        "duration_min": alt_stats.total_duration_min,
                        "elevation_gain_m": alt_stats.elevation_gain_m,
                        "coordinates": alt_coords,
                    }
                )

        msg = (
            "3D Route calculated successfully."
            if matched_all
            else "3D Route calculated with partial network matching."
        )

        return RouteResult3D(
            coordinates_3d=all_coords,
            statistics=stats,
            profile=profile,
            waypoints=wp_list,
            is_network_matched=matched_all,
            status_message=msg,
            alternative_routes=alternatives,
            routing_diagnostics={
                "graph": dict(self.graph_diagnostics),
                "segments": segment_diagnostics,
            },
        )

    def calculate_od_matrix(
        self,
        origins: Sequence[Waypoint],
        destinations: Sequence[Waypoint],
        profile_key: str = "adult",
        progress_callback: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """Compute complete N x M Origin-Destination 3D cost matrix.

        progress_callback(done, total) is invoked after each pair and may return
        False to abort.

        Each origin is searched once, one-to-many, until all of its
        destinations are settled (N searches instead of N x M); every pair
        then reads its path from that search tree.
        """
        matrix_rows = []
        profile = get_profile(profile_key)
        total_pairs = max(1, len(origins) * len(destinations))
        done_pairs = 0

        # Snap every pair first, so each origin node knows all of its targets.
        targets_by_start: Dict[int, Set[int]] = {}
        for orig in origins:
            for dest in destinations:
                start_node, end_node = self.find_compatible_nodes((orig.lon, orig.lat), (dest.lon, dest.lat))
                if start_node is not None and end_node is not None and start_node != end_node:
                    targets_by_start.setdefault(start_node, set()).add(end_node)
        search_trees: Dict[int, Tuple[Dict[int, int], Set[int], Dict[str, Any]]] = {}

        for i, orig in enumerate(origins):
            for j, dest in enumerate(destinations):
                if progress_callback is not None:
                    if progress_callback(done_pairs, total_pairs) is False:
                        return matrix_rows
                done_pairs += 1
                start_node, _end = self.find_compatible_nodes((orig.lon, orig.lat), (dest.lon, dest.lat))
                if start_node in targets_by_start and start_node not in search_trees:
                    search_trees[start_node] = self._search(start_node, profile, targets_by_start[start_node])
                res = self.calculate_route(
                    [orig, dest],
                    profile_key=profile_key,
                    compute_alternatives=False,
                    search_trees=search_trees,
                    detail=False,
                )
                matrix_rows.append(
                    {
                        "origin_id": i + 1,
                        "origin_name": orig.name or f"Origin {i+1}",
                        "origin_lon": orig.lon,
                        "origin_lat": orig.lat,
                        "dest_id": j + 1,
                        "dest_name": dest.name or f"Dest {j+1}",
                        "dest_lon": dest.lon,
                        "dest_lat": dest.lat,
                        "profile": profile.name,
                        "distance_m": res.statistics.total_distance_m,
                        "distance_km": res.statistics.total_distance_km,
                        "duration_min": res.statistics.total_duration_min,
                        "climb_m": res.statistics.elevation_gain_m,
                        "calories_kcal": res.statistics.total_calories_kcal,
                        "is_matched": res.is_network_matched,
                    }
                )
        return matrix_rows
