"""High-performance 3D topological graph engine with Bidirectional A*, TSP, and OD Matrix."""
from __future__ import annotations

import heapq
import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .ahp_engine import AHPEngine, AHPResult
from .environmental_raster import EnvironmentalSurfaceSampler, MCDAWeights
from .kinematics import haversine_distance_2d, haversine_distance_3d
from .mobility_profiles import MobilityProfile, get_profile
from .network_source import NetworkSourceManager, RoadSegment
from .profile_stats import CueInstruction, RouteStatistics, compute_route_statistics, densify_3d_linestring
from .tsp_solver import solve_tsp_order


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
    alternative_routes: List[Dict[str, Any]] = field(default_factory=list)

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
                "ada_compliant": self.statistics.ada_compliant,
                "slope_distribution": self.statistics.slope_distribution,
                "is_network_matched": self.is_network_matched,
                "status_message": self.status_message,
                "cue_sheet": [c.to_dict() for c in self.statistics.cue_sheet],
                "alternative_count": len(self.alternative_routes),
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

        # BFS Connected Components
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
        max_search_radius_m: float = 2500.0,
    ) -> Optional[int]:
        """Find the nearest graph node with spatial bucket optimization."""
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
        """Snap origin and destination to the largest shared connected component."""
        if not self.nodes or not self.component_sizes:
            return None, None

        largest_comp = max(self.component_sizes, key=self.component_sizes.get)
        start_node = self.find_nearest_node(origin, target_component=largest_comp)
        end_node = self.find_nearest_node(destination, target_component=largest_comp)

        if start_node is not None and end_node is not None:
            return start_node, end_node

        return self.find_nearest_node(origin), self.find_nearest_node(destination)

    def compute_segment_route(
        self,
        start_pt: Tuple[float, float],
        end_pt: Tuple[float, float],
        profile: MobilityProfile,
        avoid_edges: Optional[set] = None,
    ) -> Tuple[List[Tuple[float, float, float]], bool]:
        """Compute A* least-cost path between single origin and destination pair."""
        if not self.nodes:
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
        avoid_set = avoid_edges or set()

        def heuristic(u_coord: Tuple[float, float, float]) -> float:
            return haversine_distance_2d(u_coord, dest_coord)

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
                if (u, v) in avoid_set:
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
        z_start = self.sampler.sample_elevation(start_pt[0], start_pt[1])
        z_end = self.sampler.sample_elevation(end_pt[0], end_pt[1])
        final_path = [(start_pt[0], start_pt[1], z_start)] + path + [(end_pt[0], end_pt[1], z_end)]
        return final_path, True

    def calculate_route(
        self,
        waypoints: Sequence[Waypoint],
        profile_key: str = "adult",
        optimize_tsp: bool = False,
        compute_alternatives: bool = True,
    ) -> RouteResult3D:
        """Compute complete multi-stop 3D route traversing all waypoints."""
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
                status_message="At least 2 waypoints are required.",
            )

        profile = get_profile(profile_key)
        wp_list = list(waypoints)

        # Optional TSP optimization for >2 waypoints
        if optimize_tsp and len(wp_list) > 2:
            pts_tuples = [(w.lon, w.lat, self.sampler.sample_elevation(w.lon, w.lat)) for w in wp_list]
            ordered_indices = solve_tsp_order(pts_tuples, fix_start=True, fix_end=True)
            wp_list = [wp_list[idx] for idx in ordered_indices]

        all_coords: List[Tuple[float, float, float]] = []
        matched_all = True

        for i in range(len(wp_list) - 1):
            w1 = wp_list[i]
            w2 = wp_list[i + 1]
            seg_coords, matched = self.compute_segment_route(
                (w1.lon, w1.lat),
                (w2.lon, w2.lat),
                profile,
            )
            if not matched:
                matched_all = False

            if all_coords:
                all_coords.extend(seg_coords[1:])
            else:
                all_coords.extend(seg_coords)

        stats = compute_route_statistics(all_coords, profile)

        # Compute Alternative Route (e.g. Flattest or Coolest)
        alternatives: List[Dict[str, Any]] = []
        if compute_alternatives and len(wp_list) == 2 and matched_all:
            # Build penalty set along primary path to find genuine alternative
            edge_set = {
                (self.coord_to_node.get((round(all_coords[k][0], 5), round(all_coords[k][1], 5))),
                 self.coord_to_node.get((round(all_coords[k+1][0], 5), round(all_coords[k+1][1], 5))))
                for k in range(len(all_coords) - 1)
            }
            alt_coords, alt_matched = self.compute_segment_route(
                (wp_list[0].lon, wp_list[0].lat),
                (wp_list[1].lon, wp_list[1].lat),
                get_profile("sightseer"),
                avoid_edges=edge_set,
            )
            if alt_matched and len(alt_coords) > 2:
                alt_stats = compute_route_statistics(alt_coords, get_profile("sightseer"))
                alternatives.append(
                    {
                        "name": "Alternative Scenic / Ridge Path",
                        "distance_km": alt_stats.total_distance_km,
                        "duration_min": alt_stats.total_duration_min,
                        "elevation_gain_m": alt_stats.elevation_gain_m,
                        "coordinates": alt_coords,
                    }
                )

        msg = (
            "3D Route calculated successfully."
            if matched_all
            else "Partial network coverage; beeline connection used for disconnected stops."
        )

        return RouteResult3D(
            coordinates_3d=all_coords,
            statistics=stats,
            profile=profile,
            waypoints=wp_list,
            is_network_matched=matched_all,
            status_message=msg,
            alternative_routes=alternatives,
        )

    def calculate_od_matrix(
        self,
        origins: Sequence[Waypoint],
        destinations: Sequence[Waypoint],
        profile_key: str = "adult",
    ) -> List[Dict[str, Any]]:
        """Compute complete N x M Origin-Destination 3D cost matrix."""
        matrix_rows = []
        profile = get_profile(profile_key)

        for i, orig in enumerate(origins):
            for j, dest in enumerate(destinations):
                res = self.calculate_route([orig, dest], profile_key=profile_key, compute_alternatives=False)
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
