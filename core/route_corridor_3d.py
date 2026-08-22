"""3D Route Corridor extractor: buffers route by 50m and extracts real 3D OSM buildings."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .kinematics import haversine_distance_2d
from .osm_downloader import OsmBuilding


def point_to_segment_distance_meters(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> float:
    """Distance in meters from point P(px,py) to segment AB in WGS84."""
    # Convert local dx, dy to approximate meters
    cos_lat = math.cos(math.radians((ay + by) * 0.5))
    dx = (bx - ax) * 111320.0 * cos_lat
    dy = (by - ay) * 110540.0
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq <= 1e-9:
        return haversine_distance_2d((px, py), (ax, ay))

    p_dx = (px - ax) * 111320.0 * cos_lat
    p_dy = (py - ay) * 110540.0
    t = max(0.0, min(1.0, (p_dx * dx + p_dy * dy) / seg_len_sq))

    proj_x = ax + t * (bx - ax)
    proj_y = ay + t * (by - ay)
    return haversine_distance_2d((px, py), (proj_x, proj_y))


def point_to_linestring_distance_meters(
    px: float, py: float, linestring: Sequence[Tuple[float, float]]
) -> float:
    """Minimum distance in meters from point P to a polyline."""
    if len(linestring) < 2:
        return float("inf")
    min_d = float("inf")
    for i in range(len(linestring) - 1):
        d = point_to_segment_distance_meters(
            px, py, linestring[i][0], linestring[i][1], linestring[i + 1][0], linestring[i + 1][1]
        )
        if d < min_d:
            min_d = d
    return min_d


def filter_buildings_in_corridor(
    route_coords: Sequence[Tuple[float, float, float]],
    buildings: Sequence[OsmBuilding],
    buffer_meters: float = 50.0,
) -> List[Dict[str, Any]]:
    """Extract real OSM buildings that fall within the specified corridor buffer around the route."""
    if len(route_coords) < 2:
        return []

    line_2d = [(c[0], c[1]) for c in route_coords]
    corridor_buildings: List[Dict[str, Any]] = []

    for b in buildings:
        if not b.polygon:
            continue

        # Check if building centroid or any vertex is within buffer_meters
        poly = b.polygon
        c_lon = sum(p[0] for p in poly) / len(poly)
        c_lat = sum(p[1] for p in poly) / len(poly)

        d = point_to_linestring_distance_meters(c_lon, c_lat, line_2d)
        if d <= buffer_meters + 15.0:  # Include footprint extent
            corridor_buildings.append({
                "id": b.building_id,
                "coordinates": [[round(p[0], 6), round(p[1], 6)] for p in b.polygon],
                "height_m": round(b.height_m, 1),
                "levels": b.levels,
                "type": b.building_type,
            })

    return corridor_buildings
