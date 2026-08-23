"""3D Route Corridor extractor: buffers route by 50m and extracts real 3D OSM buildings with matched base elevation."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence, Tuple

from .kinematics import haversine_distance_2d
from .osm_downloader import OsmBuilding


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


def get_closest_route_elevation(
    px: float, py: float, route_coords: Sequence[Tuple[float, float, float]]
) -> float:
    """Find the ground elevation of the closest point along the 3D route polyline."""
    if not route_coords:
        return 0.0
    if len(route_coords) == 1:
        return float(route_coords[0][2]) if len(route_coords[0]) > 2 and math.isfinite(float(route_coords[0][2])) else 0.0

    min_d = float("inf")
    closest_z = float(route_coords[0][2]) if len(route_coords[0]) > 2 and math.isfinite(float(route_coords[0][2])) else 0.0

    for i in range(len(route_coords) - 1):
        p1 = route_coords[i]
        p2 = route_coords[i + 1]
        z1 = float(p1[2]) if len(p1) > 2 and math.isfinite(float(p1[2])) else 0.0
        z2 = float(p2[2]) if len(p2) > 2 and math.isfinite(float(p2[2])) else 0.0
        d = point_to_segment_distance_meters(px, py, p1[0], p1[1], p2[0], p2[1])
        if d < min_d:
            min_d = d
            closest_z = (z1 + z2) * 0.5
    return closest_z


def filter_buildings_in_corridor(
    route_coords: Sequence[Tuple[float, float, float]],
    buildings: Sequence[OsmBuilding],
    buffer_meters: float = 50.0,
) -> List[Dict[str, Any]]:
    """Extract real OSM buildings that fall within the specified corridor buffer around the route with matched base elevation."""
    if not route_coords or len(route_coords) < 2:
        return []

    buf_m = float(buffer_meters) if math.isfinite(buffer_meters) and buffer_meters >= 0 else 50.0
    line_2d = [(c[0], c[1]) for c in route_coords if len(c) >= 2 and math.isfinite(c[0]) and math.isfinite(c[1])]
    if len(line_2d) < 2:
        return []

    # Fast AABB bounding box pre-filter
    margin_deg = (buf_m + 30.0) / 75000.0
    min_rx = min(p[0] for p in line_2d) - margin_deg
    max_rx = max(p[0] for p in line_2d) + margin_deg
    min_ry = min(p[1] for p in line_2d) - margin_deg
    max_ry = max(p[1] for p in line_2d) + margin_deg

    corridor_buildings: List[Dict[str, Any]] = []

    for b in buildings:
        if not b.polygon or len(b.polygon) < 3:
            continue

        poly = b.polygon
        valid_poly = [p for p in poly if len(p) >= 2 and math.isfinite(p[0]) and math.isfinite(p[1])]
        if len(valid_poly) < 3:
            continue

        c_lon = sum(p[0] for p in valid_poly) / len(valid_poly)
        c_lat = sum(p[1] for p in valid_poly) / len(valid_poly)

        # Skip buildings outside route corridor AABB instantly
        if c_lon < min_rx or c_lon > max_rx or c_lat < min_ry or c_lat > max_ry:
            continue

        d = point_to_linestring_distance_meters(c_lon, c_lat, line_2d)
        if d <= buf_m + 15.0:  # Include footprint extent
            base_z = get_closest_route_elevation(c_lon, c_lat, route_coords)
            h_m = float(b.height_m) if math.isfinite(b.height_m) and b.height_m > 0 else 12.0
            lvls = int(b.levels) if b.levels > 0 else 4
            corridor_buildings.append({
                "id": str(b.building_id),
                "coordinates": [[round(p[0], 6), round(p[1], 6)] for p in valid_poly],
                "height_m": round(h_m, 1),
                "levels": lvls,
                "type": b.building_type,
                "base_elevation_m": round(base_z, 2),
            })

    return corridor_buildings
