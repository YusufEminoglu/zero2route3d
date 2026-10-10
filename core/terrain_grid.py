"""A regular elevation grid around the routes, for the 3D viewer's terrain.

The viewer used to invent its terrain: it fitted a surface through about 180
points sampled along the route, so relief away from the route was not real.
This module samples the actual elevation source (a QGIS DEM layer, or cached
Open-Elevation values) on a grid covering the routes and their corridor, and
hands the viewer the heights. Pure Python: the sampler is any callable.
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Sequence, Tuple

METRES_PER_DEGREE_LAT = 111_195.0
# Below this share of real samples the grid is not sent; the viewer then shows
# its approximate route-fitted surface and says so.
MIN_COVERAGE = 0.6


def grid_bbox(
    routes: Sequence[Sequence[Sequence[float]]],
    buffer_m: float = 60.0,
) -> Optional[Tuple[float, float, float, float]]:
    """(min_lon, min_lat, max_lon, max_lat) of every route vertex plus a buffer in metres."""
    lons: List[float] = []
    lats: List[float] = []
    for coords in routes:
        for c in coords or ():
            try:
                lon, lat = float(c[0]), float(c[1])
            except (IndexError, TypeError, ValueError):
                continue
            if math.isfinite(lon) and math.isfinite(lat):
                lons.append(lon)
                lats.append(lat)
    if not lons:
        return None
    mid_lat = (min(lats) + max(lats)) / 2.0
    d_lat = buffer_m / METRES_PER_DEGREE_LAT
    d_lon = buffer_m / (METRES_PER_DEGREE_LAT * max(0.05, math.cos(math.radians(mid_lat))))
    return (min(lons) - d_lon, min(lats) - d_lat, max(lons) + d_lon, max(lats) + d_lat)


def sample_terrain_grid(
    sample: Callable[[float, float], Optional[float]],
    bbox: Tuple[float, float, float, float],
    target_cell_m: float = 10.0,
    max_cells_axis: int = 160,
    min_coverage: float = MIN_COVERAGE,
) -> Optional[Dict[str, object]]:
    """Sample heights on a regular lon/lat grid; None when coverage is too low.

    Returns {"bbox": [w, s, e, n], "cols", "rows", "heights" (row-major, north
    row first, metres rounded to 0.1), "cell_m", "coverage"}. Missing samples
    are filled from the nearest known neighbours, so the mesh has no holes, and
    the share of real samples is reported as ``coverage``.
    """
    west, south, east, north = (float(v) for v in bbox)
    if not all(math.isfinite(v) for v in (west, south, east, north)) or east <= west or north <= south:
        return None
    mid_lat = (south + north) / 2.0
    width_m = (east - west) * METRES_PER_DEGREE_LAT * max(0.05, math.cos(math.radians(mid_lat)))
    height_m = (north - south) * METRES_PER_DEGREE_LAT
    cell = max(1.0, float(target_cell_m))
    cell = max(cell, width_m / max(2, max_cells_axis - 1), height_m / max(2, max_cells_axis - 1))
    cols = max(2, min(max_cells_axis, int(math.ceil(width_m / cell)) + 1))
    rows = max(2, min(max_cells_axis, int(math.ceil(height_m / cell)) + 1))

    values: List[Optional[float]] = []
    known = 0
    for r in range(rows):
        lat = north - (north - south) * r / (rows - 1)
        for c in range(cols):
            lon = west + (east - west) * c / (cols - 1)
            try:
                z = sample(lon, lat)
            except Exception:  # noqa: BLE001 - a failing sample is just missing
                z = None
            if z is not None and math.isfinite(float(z)):
                values.append(float(z))
                known += 1
            else:
                values.append(None)

    coverage = known / float(rows * cols)
    if known == 0 or coverage < min_coverage:
        return None
    filled = _fill_gaps(values, cols, rows)
    return {
        "bbox": [west, south, east, north],
        "cols": cols,
        "rows": rows,
        "heights": [round(z, 1) for z in filled],
        "cell_m": round(max(width_m / (cols - 1), height_m / (rows - 1)), 2),
        "coverage": round(coverage, 3),
    }


def _fill_gaps(values: List[Optional[float]], cols: int, rows: int) -> List[float]:
    """Fill missing cells ring by ring with the mean of known 4-neighbours."""
    grid = list(values)
    missing = [i for i, v in enumerate(grid) if v is None]
    while missing:
        resolved: Dict[int, float] = {}
        for i in missing:
            r, c = divmod(i, cols)
            neighbours = []
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                rr, cc = r + dr, c + dc
                if 0 <= rr < rows and 0 <= cc < cols:
                    v = grid[rr * cols + cc]
                    if v is not None:
                        neighbours.append(v)
            if neighbours:
                resolved[i] = sum(neighbours) / len(neighbours)
        if not resolved:
            break
        for i, z in resolved.items():
            grid[i] = z
        missing = [i for i in missing if i not in resolved]
    fallback = next((v for v in grid if v is not None), 0.0)
    return [v if v is not None else fallback for v in grid]
