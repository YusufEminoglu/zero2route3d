"""3D Route Corridor extractor: buffers multi-route paths by 30m and extracts real 3D OSM buildings and 3D volumetric trees."""
from __future__ import annotations

import contextlib
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .kinematics import haversine_distance_2d
from .osm_downloader import OsmBuilding, OsmPark, OsmTree


def point_to_segment_distance_meters(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> float:
    """Distance in meters from point P(px,py) to segment AB in WGS84."""
    if not (math.isfinite(px) and math.isfinite(py) and math.isfinite(ax) and math.isfinite(ay) and math.isfinite(bx) and math.isfinite(by)):
        return float("inf")

    # Convert local dx, dy to approximate meters
    cos_lat = math.cos(math.radians((ay + by) * 0.5))
    dx = (bx - ax) * 111320.0 * cos_lat
    dy = (by - ay) * 110540.0
    seg_len_sq = dx * dx + dy * dy
    if not math.isfinite(seg_len_sq) or seg_len_sq <= 1e-9:
        return haversine_distance_2d((px, py), (ax, ay))

    p_dx = (px - ax) * 111320.0 * cos_lat
    p_dy = (py - ay) * 110540.0
    num = p_dx * dx + p_dy * dy
    if not math.isfinite(num):
        return haversine_distance_2d((px, py), (ax, ay))

    t = max(0.0, min(1.0, num / seg_len_sq))

    proj_x = ax + t * (bx - ax)
    proj_y = ay + t * (by - ay)
    return haversine_distance_2d((px, py), (proj_x, proj_y))


def point_to_linestring_distance_meters(
    px: float, py: float, linestring: Sequence[Tuple[float, float]]
) -> float:
    """Minimum distance in meters from point P to a polyline."""
    if not linestring:
        return float("inf")
    if len(linestring) == 1:
        return haversine_distance_2d((px, py), linestring[0])

    min_d = float("inf")
    for i in range(len(linestring) - 1):
        d = point_to_segment_distance_meters(
            px, py, linestring[i][0], linestring[i][1], linestring[i + 1][0], linestring[i + 1][1]
        )
        if d < min_d:
            min_d = d
    return min_d


def point_to_multi_linestrings_distance_meters(
    px: float, py: float, linestrings: Sequence[Sequence[Tuple[float, float]]]
) -> float:
    """Minimum distance in meters from point P to any polyline in the collection."""
    linestring_list = [line for line in linestrings if line]
    if not linestring_list:
        return float("inf")
    min_d = float("inf")
    for line in linestring_list:
        d = point_to_linestring_distance_meters(px, py, line)
        if d < min_d:
            min_d = d
    return min_d


def get_closest_multi_route_elevation(
    px: float,
    py: float,
    all_routes: Sequence[Sequence[Tuple[float, float, ...]]],
    green_sampler: Optional[Any] = None,
) -> float:
    """Find ground elevation using the green_sampler DEM if available, or closest multi-route elevation."""
    if green_sampler is not None and hasattr(green_sampler, "sample_elevation"):
        with contextlib.suppress(Exception):
            elev = green_sampler.sample_elevation(px, py)
            if elev is None:
                elev = float("nan")
            if math.isfinite(elev) and elev != 0.0:
                return float(elev)

    if not all_routes:
        return 0.0

    min_d = float("inf")
    best_z = 0.0
    for route in all_routes:
        if not route:
            continue
        for i in range(len(route) - 1):
            p1 = route[i]
            p2 = route[i + 1]
            z1 = float(p1[2]) if len(p1) > 2 and math.isfinite(float(p1[2])) else 0.0
            z2 = float(p2[2]) if len(p2) > 2 and math.isfinite(float(p2[2])) else 0.0
            d = point_to_segment_distance_meters(px, py, p1[0], p1[1], p2[0], p2[1])
            if d < min_d:
                min_d = d
                best_z = (z1 + z2) * 0.5
    return best_z


def filter_corridor_assets_multi_route(
    routes_coords: Sequence[Any],
    buildings: Sequence[OsmBuilding],
    buffer_meters: float = 30.0,
    green_sampler: Optional[Any] = None,
    osm_trees: Optional[Sequence[OsmTree]] = None,
    osm_parks: Optional[Sequence[OsmPark]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Multi-route corridor asset extractor.

    Buffers across ALL active route paths (30m buffer default) to extract:
    1. Real 3D OSM buildings within the multi-route buffer with calculated base elevations and levels.
    2. Real & volumetric 3D trees placed along greenery / park / corridor zones within the 30m buffer.

    Returns:
        (corridor_buildings, corridor_trees)
    """
    if not routes_coords:
        return [], []

    # Handle input flexibility: routes_coords can be a list of routes or a single route
    cleaned_routes: List[List[Tuple[float, float, float]]] = []
    if len(routes_coords) > 0:
        first_elem = routes_coords[0]
        if isinstance(first_elem, (list, tuple)) and len(first_elem) > 0 and isinstance(first_elem[0], (int, float)):
            route_list = [routes_coords]
        else:
            route_list = list(routes_coords)
    else:
        return [], []

    for r in route_list:
        if not r or len(r) < 2:
            continue
        valid_pts = []
        for c in r:
            if isinstance(c, (list, tuple)) and len(c) >= 2:
                lon_v = float(c[0])
                lat_v = float(c[1])
                ele_v = float(c[2]) if len(c) > 2 and math.isfinite(float(c[2])) else 0.0
                if math.isfinite(lon_v) and math.isfinite(lat_v):
                    valid_pts.append((lon_v, lat_v, ele_v))
        if len(valid_pts) >= 2:
            cleaned_routes.append(valid_pts)

    if not cleaned_routes:
        return [], []

    buf_m = float(buffer_meters) if math.isfinite(buffer_meters) and buffer_meters >= 0 else 30.0
    all_lines_2d = [[(p[0], p[1]) for p in r] for r in cleaned_routes]

    # Global bounding box across all routes
    all_lons = [p[0] for line in all_lines_2d for p in line]
    all_lats = [p[1] for line in all_lines_2d for p in line]
    margin_deg = (buf_m + 35.0) / 75000.0
    min_rx = min(all_lons) - margin_deg
    max_rx = max(all_lons) + margin_deg
    min_ry = min(all_lats) - margin_deg
    max_ry = max(all_lats) + margin_deg

    # -------------------------------------------------------------
    # 1. Extract 3D Buildings within 30m multi-route buffer
    # -------------------------------------------------------------
    corridor_buildings: List[Dict[str, Any]] = []
    building_centroids: List[Tuple[float, float, float]] = []  # (lon, lat, radius_m)
    seen_building_ids = set()

    for b in (buildings or []):
        if not b.polygon or len(b.polygon) < 3:
            continue

        b_id = str(b.building_id)
        if b_id in seen_building_ids:
            continue

        valid_poly = [
            (float(p[0]), float(p[1]))
            for p in b.polygon
            if len(p) >= 2 and math.isfinite(float(p[0])) and math.isfinite(float(p[1]))
        ]
        if len(valid_poly) < 3:
            continue

        c_lon = sum(p[0] for p in valid_poly) / len(valid_poly)
        c_lat = sum(p[1] for p in valid_poly) / len(valid_poly)

        # Fast AABB skip
        if c_lon < min_rx or c_lon > max_rx or c_lat < min_ry or c_lat > max_ry:
            continue

        d = point_to_multi_linestrings_distance_meters(c_lon, c_lat, all_lines_2d)
        if d <= buf_m + 15.0:  # Include footprint extent
            seen_building_ids.add(b_id)
            base_z = get_closest_multi_route_elevation(c_lon, c_lat, cleaned_routes, green_sampler)
            h_m = float(b.height_m) if math.isfinite(b.height_m) and b.height_m > 0 else 12.0
            lvls = int(b.levels) if b.levels > 0 else 4

            # Calculate approximate footprint radius for tree collision avoidance
            max_poly_rad = max(haversine_distance_2d((c_lon, c_lat), p) for p in valid_poly)
            building_centroids.append((c_lon, c_lat, max_poly_rad + 3.0))

            corridor_buildings.append({
                "id": b_id,
                "coordinates": [[round(p[0], 6), round(p[1], 6)] for p in valid_poly],
                "height_m": round(h_m, 1),
                "levels": lvls,
                "type": b.building_type,
                "base_elevation_m": round(base_z, 2),
                "dimensions_estimated": bool(getattr(b, "dimensions_estimated", True)),
            })

    # -------------------------------------------------------------
    # 2. Extract Real & Volumetric 3D Trees in 30m corridor buffer
    # -------------------------------------------------------------
    corridor_trees: List[Dict[str, Any]] = []
    placed_tree_positions: List[Tuple[float, float]] = []

    # 2.1 First place real OSM trees & tree rows
    if osm_trees:
        for t in osm_trees:
            if not (math.isfinite(t.lon) and math.isfinite(t.lat)):
                continue
            d = point_to_multi_linestrings_distance_meters(t.lon, t.lat, all_lines_2d)
            if d > buf_m + 5.0 or d < 1.0:
                continue

            # Check building collision
            collides_bld = any(haversine_distance_2d((t.lon, t.lat), (b_lon, b_lat)) < b_rad for b_lon, b_lat, b_rad in building_centroids)
            if collides_bld:
                continue

            # Check tree-to-tree spacing
            if any(haversine_distance_2d((t.lon, t.lat), pos) < 5.0 for pos in placed_tree_positions):
                continue

            tree_z = get_closest_multi_route_elevation(t.lon, t.lat, cleaned_routes, green_sampler)
            corridor_trees.append({
                "id": f"osm_tree_{t.tree_id}",
                "coordinates": [round(t.lon, 6), round(t.lat, 6)],
                "base_elevation_m": round(tree_z, 2),
                "height_m": round(t.height_m, 1),
                "canopy_radius_m": round(t.canopy_radius_m, 1),
                "trunk_height_m": round(t.height_m * 0.3, 1),
                "trunk_radius_m": round(max(0.18, t.canopy_radius_m * 0.08), 2),
                "tree_type": t.tree_type,
                "species": t.species,
                # height/canopy are tagged values when OSM had them and typical
                # defaults otherwise; say which, rather than implying a survey.
                "dimensions_estimated": bool(getattr(t, "dimensions_estimated", True)),
            })
            placed_tree_positions.append((t.lon, t.lat))

    # 2.2 Park polygons are carried as park geometry only. A previous revision
    # emitted a 9.0 m tree with a 3.8 m canopy at every park *boundary vertex*;
    # boundary vertices are not tree locations and those dimensions were invented.
    if osm_parks and corridor_trees:
        park_centroids = []
        for p in osm_parks:
            if p.polygon and len(p.polygon) >= 3:
                p_lon = sum(pt[0] for pt in p.polygon) / len(p.polygon)
                p_lat = sum(pt[1] for pt in p.polygon) / len(p.polygon)
                p_rad = max(haversine_distance_2d((p_lon, p_lat), pt) for pt in p.polygon)
                park_centroids.append((p_lon, p_lat, p_rad, p.name or p.park_type))
        for tree_dict in corridor_trees:
            tc = tree_dict["coordinates"]
            for p_lon, p_lat, p_rad, p_label in park_centroids:
                if haversine_distance_2d((tc[0], tc[1]), (p_lon, p_lat)) <= p_rad:
                    tree_dict["park_name"] = p_label
                    break

    # No procedural greenery is generated. A previous revision placed a tree every
    # 18 m at six fixed lateral offsets, with height, canopy, trunk and species
    # derived from abs(hash(coordinate)), and returned them in the same list as
    # real OSM trees -- indistinguishable from surveyed data in the 3D scene and in
    # every export. Only trees that actually exist in OSM are returned.

    return corridor_buildings, corridor_trees


def filter_corridor_parks_multi_route(
    routes_coords: Sequence[Any],
    osm_parks: Optional[Sequence[OsmPark]] = None,
    buffer_meters: float = 30.0,
    green_sampler: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Extract real OSM park and green-area polygons within the multi-route corridor buffer."""
    if not routes_coords or not osm_parks:
        return []

    first_elem = routes_coords[0]
    if isinstance(first_elem, (list, tuple)) and len(first_elem) > 0 and isinstance(first_elem[0], (int, float)):
        route_list = [routes_coords]
    else:
        route_list = list(routes_coords)

    cleaned_routes: List[List[Tuple[float, float, float]]] = []
    for r in route_list:
        if not r or len(r) < 2:
            continue
        valid_pts = [
            (float(c[0]), float(c[1]), float(c[2]) if len(c) > 2 and math.isfinite(float(c[2])) else 0.0)
            for c in r
            if isinstance(c, (list, tuple)) and len(c) >= 2 and math.isfinite(float(c[0])) and math.isfinite(float(c[1]))
        ]
        if len(valid_pts) >= 2:
            cleaned_routes.append(valid_pts)
    if not cleaned_routes:
        return []

    buf_m = float(buffer_meters) if math.isfinite(buffer_meters) and buffer_meters >= 0 else 30.0
    all_lines_2d = [[(p[0], p[1]) for p in r] for r in cleaned_routes]
    corridor_parks: List[Dict[str, Any]] = []
    seen_ids = set()

    for park in osm_parks:
        if not park.polygon or len(park.polygon) < 3:
            continue
        p_id = str(park.park_id)
        if p_id in seen_ids:
            continue
        valid_poly = [
            (float(p[0]), float(p[1]))
            for p in park.polygon
            if len(p) >= 2 and math.isfinite(float(p[0])) and math.isfinite(float(p[1]))
        ]
        if len(valid_poly) < 3:
            continue
        c_lon = sum(p[0] for p in valid_poly) / len(valid_poly)
        c_lat = sum(p[1] for p in valid_poly) / len(valid_poly)
        min_vertex_d = min(
            point_to_multi_linestrings_distance_meters(c_lon, c_lat, all_lines_2d),
            *(point_to_multi_linestrings_distance_meters(vx, vy, all_lines_2d) for vx, vy in valid_poly[::max(1, len(valid_poly) // 8)]),
        )
        if min_vertex_d <= buf_m + 35.0:
            seen_ids.add(p_id)
            base_z = get_closest_multi_route_elevation(c_lon, c_lat, cleaned_routes, green_sampler)
            corridor_parks.append({
                "id": p_id,
                "coordinates": [[round(p[0], 6), round(p[1], 6)] for p in valid_poly],
                "park_type": park.park_type,
                "name": park.name,
                "base_elevation_m": round(base_z, 2),
            })
    return corridor_parks


def filter_buildings_in_corridor(
    route_coords: Sequence[Tuple[float, float, float]],
    buildings: Sequence[OsmBuilding],
    buffer_meters: float = 30.0,
) -> List[Dict[str, Any]]:
    """Extract real OSM buildings that fall within the specified corridor buffer around the route."""
    blds, _ = filter_corridor_assets_multi_route([route_coords], buildings, buffer_meters=buffer_meters)
    return blds
