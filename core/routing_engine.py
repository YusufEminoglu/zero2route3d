"""High-performance 3D topological graph engine with Spatial Grid Bucketing and A* Routing."""
from __future__ import annotations

import heapq
import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .environmental_raster import EnvironmentalSurfaceSampler, MCDAWeights
from .kinematics import haversine_distance_2d, haversine_distance_3d
from .mobility_profiles import MobilityProfile, get_profile
from .network_source import NetworkSourceManager, RoadSegment
from .profile_stats import RouteStatistics, compute_route_statistics, densify_3d_linestring


@dataclass
class Waypoint:
    """Geographic stop point along the route."""

    lon: float
    lat: float
    name: str = ""
    elevation_m: Optional[float] = None


@dataclass
class RouteResult3D:
    """Complete 3D path result with densified geometry, statistics, and GeoJSON export."""

    coordinates_3d: List[Tuple[float, float, float]]
    statistics: RouteStatistics
    profile: MobilityProfile
    waypoints: List[Waypoint]
    is_network_matched: bool = True
    status_message: str = "Route computed successfully."

    def to_geojson_feature(self) -> Dict[str, Any]:
        """Convert route to standard GeoJSON Feature with 3D LineString geometry and rich properties."""
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
                "distance_km": self.statistics.total_distance_km,
                "distance_m": self.statistics.total_distance_m,
                "duration_min": self.statistics.total_duration_min,
                "elevation_gain_m": self.statistics.elevation_gain_m,
                "elevation_loss_m": self.statistics.elevation_loss_m,
                "max_slope_pct": self.statistics.max_slope_pct,
                "avg_slope_pct": self.statistics.avg_slope_pct,
                "calories_kcal": self.statistics.total_calories_kcal,
                "thermal_comfort_score": self.statistics.thermal_comfort_score,
                "slope_distribution": self.statistics.slope_distribution,
                "is_network_matched": self.is_network_matched,
                "status_message": self.status_message,
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
    """Topological graph builder and A* Least-Cost 3D Path engine."""

    def __init__(
        self,
        sampler: Optional[EnvironmentalSurfaceSampler] = None,
        weights: Optional[MCDAWeights] = None,
    ) -> None:
        self.sampler = sampler or EnvironmentalSurfaceSampler()
        self.weights = weights or MCDAWeights()
        self.nodes: Dict[int, Tuple[float, float, float]] = {}
        self.coord_to_node: Dict[Tuple[float, float], int] = {}
        self.adj: Dict[int, List[Tuple[int, float, float, Dict[str, Any]]]] = {}
        self.grid_buckets: Dict[Tuple[int, int], List[int]] = {}
        self.component_by_node: Dict[int, int] = {}
        self.component_sizes: Dict[int, int] = {}
        self._built = False

    def build_graph(self, segments: Sequence[RoadSegment]) -> None:
        """Build topological graph with spatial hash grid from road segments."""
        self.nodes.clear()
        self.coord_to_node.clear()
        self.adj.clear()
        self.grid_buckets.clear()
        self.component_by_node.clear()
        self.component_sizes.clear()

        node_counter = 0

        def get_or_create_node(pt: Tuple[float, float, float]) -> int:
            nonlocal node_counter
            key = (round(pt[0], 5), round(pt[1], 5))
            if key not in self.coord_to_node:
                z = pt[2] if pt[2] != 0.0 else self.sampler.sample_elevation(pt[0], pt[1])
                self.coord_to_node[key] = node_counter
                self.nodes[node_counter] = (pt[0], pt[1], z)
                self.adj[node_counter] = []

                # Spatial grid bucket (~300m cell at mid-latitudes)
                bx = int(pt[0] * 300)
                by = int(pt[1] * 300)
                b_key = (bx, by)
                if b_key not in self.grid_buckets:
                    self.grid_buckets[b_key] = []
                self.grid_buckets[b_key].append(node_counter)

                node_counter += 1
            return self.coord_to_node[key]

        for seg in segments:
            u = get_or_create_node(seg.p1)
            v = get_or_create_node(seg.p2)
            if u == v:
                continue

            # Directional slope between u and v
            p1_z = self.nodes[u][2]
            p2_z = self.nodes[v][2]
            dz = p2_z - p1_z
            dist_2d = haversine_distance_2d(self.nodes[u], self.nodes[v])
            slope_pct = (dz / max(0.1, dist_2d)) * 100.0 if dist_2d > 0.1 else 0.0

            meta_forward = {
                "length_m": seg.length_m,
                "slope_pct": slope_pct,
                "highway": seg.highway_type,
                "hierarchy": seg.hierarchy_rank,
                "is_steps": seg.is_steps,
                "surface": seg.surface,
            }
            self.adj[u].append((v, seg.length_m, slope_pct, meta_forward))

            if not seg.is_oneway:
                meta_reverse = dict(meta_forward)
                meta_reverse["slope_pct"] = -slope_pct
                self.adj[v].append((u, seg.length_m, -slope_pct, meta_reverse))

        # Compute connected components (BFS)
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
                for neighbour, *_rest in self.adj.get(curr, []):
                    if neighbour not in self.component_by_node:
                        self.component_by_node[neighbour] = comp_id
                        stack.append(neighbour)
            self.component_sizes[comp_id] = size
            comp_id += 1

        self._built = True

    def find_nearest_node(
        self,
        coord: Tuple[float, float],
        target_component: Optional[int] = None,
        max_search_radius_m: float = 2000.0
    ) -> Optional[int]:
        """Find the nearest graph node to given coordinate with optional component constraint."""
        if not self.nodes:
            return None

        bx = int(coord[0] * 300)
        by = int(coord[1] * 300)
        best_node = None
        min_dist = float("inf")

        span = max(2, int(math.ceil(max_search_radius_m / 300.0)))
        for dx in range(-span, span + 1):
            for dy in range(-span, span + 1):
                b_key = (bx + dx, by + dy)
                for nid in self.grid_buckets.get(b_key, []):
                    if target_component is not None and self.component_by_node.get(nid) != target_component:
                        continue
                    d = haversine_distance_2d(coord, self.nodes[nid])
                    if d < min_dist and d <= max_search_radius_m:
                        min_dist = d
                        best_node = nid

        if best_node is not None:
            return best_node

        # Fallback linear search across all nodes
        for nid, n_coord in self.nodes.items():
            if target_component is not None and self.component_by_node.get(nid) != target_component:
                continue
            d = haversine_distance_2d(coord, n_coord)
            if d < min_dist:
                min_dist = d
                best_node = nid
        return best_node

    def find_compatible_nodes(
        self,
        origin: Tuple[float, float],
        destination: Tuple[float, float],
    ) -> Tuple[Optional[int], Optional[int]]:
        """Snap origin and destination to the nearest shared connected component."""
        if not self.nodes or not self.component_sizes:
            return None, None

        # Find the largest non-trivial connected backbone
        largest_comp = max(self.component_sizes, key=self.component_sizes.get)
        start_node = self.find_nearest_node(origin, target_component=largest_comp)
        end_node = self.find_nearest_node(destination, target_component=largest_comp)

        if start_node is not None and end_node is not None:
            return start_node, end_node

        # Fallback: unconstrained nearest
        return self.find_nearest_node(origin), self.find_nearest_node(destination)

    def compute_segment_route(
        self,
        start_pt: Tuple[float, float],
        end_pt: Tuple[float, float],
        profile: MobilityProfile,
    ) -> Tuple[List[Tuple[float, float, float]], bool]:
        """Compute A* least-cost path between single origin and destination pair."""
        if not self.nodes:
            # Direct beeline fallback
            z1 = self.sampler.sample_elevation(start_pt[0], start_pt[1])
            z2 = self.sampler.sample_elevation(end_pt[0], end_pt[1])
            return [(start_pt[0], start_pt[1], z1), (end_pt[0], end_pt[1], z2)], False

        start_node, end_node = self.find_compatible_nodes(start_pt, end_pt)

        if start_node is None or end_node is None or start_node == end_node:
            z1 = self.sampler.sample_elevation(start_pt[0], start_pt[1])
            z2 = self.sampler.sample_elevation(end_pt[0], end_pt[1])
            return [(start_pt[0], start_pt[1], z1), (end_pt[0], end_pt[1], z2)], False

        dest_coord = self.nodes[end_node]
        w_dict = self.weights.normalized_dict()

        def heuristic(u_coord: Tuple[float, float, float]) -> float:
            d_2d = haversine_distance_2d(u_coord, dest_coord)
            return d_2d

        # A* Priority Queue: (f_score, g_cost, u)
        h_start = heuristic(self.nodes[start_node])
        pq: List[Tuple[float, float, int]] = [(h_start, 0.0, start_node)]
        g_scores: Dict[int, float] = {start_node: 0.0}
        prev_map: Dict[int, int] = {}
        visited = set()

        max_iters = min(150_000, len(self.nodes) * 3)
        iters = 0

        while pq and iters < max_iters:
            iters += 1
            _f, cost, u = heapq.heappop(pq)

            if u in visited:
                continue
            visited.add(u)

            if u == end_node:
                break

            u_coord = self.nodes[u]

            for v, seg_len, slope_pct, meta in self.adj.get(u, []):
                if v in visited:
                    continue

                v_coord = self.nodes[v]
                lst_val = self.sampler.sample_lst(v_coord[0], v_coord[1])
                green_val = self.sampler.sample_greenery(v_coord[0], v_coord[1])

                edge_cost = profile.calculate_edge_resistance(
                    length_m=seg_len,
                    slope_pct=slope_pct,
                    is_steps=meta.get("is_steps", False),
                    surface_quality=0.9 if meta.get("surface") == "asphalt" else 0.4,
                    hierarchy_rank=meta.get("hierarchy", 4),
                    lst_normalized=lst_val,
                    green_normalized=green_val,
                    custom_weights=w_dict,
                )

                if math.isinf(edge_cost):
                    continue

                tentative_g = cost + edge_cost
                if tentative_g < g_scores.get(v, float("inf")):
                    g_scores[v] = tentative_g
                    prev_map[v] = u
                    h_v = heuristic(v_coord)
                    heapq.heappush(pq, (tentative_g + h_v, tentative_g, v))

        if end_node not in prev_map and start_node != end_node:
            z1 = self.sampler.sample_elevation(start_pt[0], start_pt[1])
            z2 = self.sampler.sample_elevation(end_pt[0], end_pt[1])
            return [(start_pt[0], start_pt[1], z1), (end_pt[0], end_pt[1], z2)], False

        # Reconstruct path
        path: List[Tuple[float, float, float]] = []
        curr: Optional[int] = end_node
        while curr is not None:
            path.append(self.nodes[curr])
            curr = prev_map.get(curr)

        path.reverse()

        # Add exact start and end coordinates
        z_start = self.sampler.sample_elevation(start_pt[0], start_pt[1])
        z_end = self.sampler.sample_elevation(end_pt[0], end_pt[1])
        final_path = [(start_pt[0], start_pt[1], z_start)] + path + [(end_pt[0], end_pt[1], z_end)]
        return final_path, True

    def calculate_route(
        self,
        waypoints: Sequence[Waypoint],
        profile_key: str = "adult",
    ) -> RouteResult3D:
        """Compute complete multi-stop 3D route traversing all waypoints in order."""
        if len(waypoints) < 2:
            return RouteResult3D(
                coordinates_3d=[],
                statistics=RouteStatistics(
                    total_distance_m=0.0,
                    total_duration_s=0.0,
                    elevation_gain_m=0.0,
                    elevation_loss_m=0.0,
                    min_elevation_m=0.0,
                    max_elevation_m=0.0,
                    max_slope_pct=0.0,
                    avg_slope_pct=0.0,
                    total_calories_kcal=0.0,
                    thermal_comfort_score=1.0,
                ),
                profile=get_profile(profile_key),
                waypoints=list(waypoints),
                is_network_matched=False,
                status_message="At least 2 waypoints (origin and destination) are required.",
            )

        profile = get_profile(profile_key)
        all_coords: List[Tuple[float, float, float]] = []
        matched_all = True

        for i in range(len(waypoints) - 1):
            w1 = waypoints[i]
            w2 = waypoints[i + 1]
            seg_coords, matched = self.compute_segment_route(
                (w1.lon, w1.lat),
                (w2.lon, w2.lat),
                profile,
            )
            if not matched:
                matched_all = False

            if all_coords:
                # Avoid duplicate point at waypoint junction
                all_coords.extend(seg_coords[1:])
            else:
                all_coords.extend(seg_coords)

        # Compute full route statistics
        stats = compute_route_statistics(all_coords, profile)

        msg = (
            "3D Route calculated successfully."
            if matched_all
            else "Partial network coverage; beeline connection used for disconnected stops."
        )

        return RouteResult3D(
            coordinates_3d=all_coords,
            statistics=stats,
            profile=profile,
            waypoints=list(waypoints),
            is_network_matched=matched_all,
            status_message=msg,
        )
