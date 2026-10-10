"""Building shadows along a route, from footprints and heights.

The solar analysis used to guess shade from the street's orientation relative
to the sun. With a building layer, this module decides shade geometrically:
from every sample point along the route a ray goes toward the sun; the point
is in shadow when a building wall it crosses is taller than the sun line at
that distance (height > distance x tan(sun elevation)). Points inside a
footprint (arcades, passages) count as shaded. Terrain is not considered.

Pure Python: a local metre plane around the route and a grid index of walls.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

EYE_HEIGHT_M = 1.5
GRID_M = 20.0
MAX_RAY_M = 600.0


@dataclass
class Building:
    footprint: Sequence[Tuple[float, float]]  # (lon, lat) ring
    height_m: float


@dataclass
class ShadeResult:
    shaded_length_m: float
    total_length_m: float
    samples: int
    shaded_samples: int

    @property
    def shaded_fraction(self) -> float:
        return self.shaded_length_m / self.total_length_m if self.total_length_m > 0 else 0.0


class ShadowCaster:
    """Buildings indexed for repeated sun-ray queries in one local plane."""

    def __init__(self, buildings: Sequence[Building], origin_lat: float, origin_lon: float) -> None:
        self.lat0 = origin_lat
        self.lon0 = origin_lon
        self.kx = 111320.0 * math.cos(math.radians(origin_lat))
        self.ky = 110574.0
        self.polygons: List[List[Tuple[float, float]]] = []
        self.heights: List[float] = []
        self.walls: List[Tuple[float, float, float, float, int]] = []
        self.grid: Dict[Tuple[int, int], List[int]] = {}
        self.max_height = 0.0
        for building in buildings:
            height = float(building.height_m or 0.0)
            ring = [self._xy(lon, lat) for lon, lat in building.footprint]
            if len(ring) < 3 or not math.isfinite(height) or height <= EYE_HEIGHT_M:
                continue
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            index = len(self.polygons)
            self.polygons.append(ring)
            self.heights.append(height)
            self.max_height = max(self.max_height, height)
            for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
                wall_id = len(self.walls)
                self.walls.append((x1, y1, x2, y2, index))
                for cell in self._cells_on_segment(x1, y1, x2, y2):
                    self.grid.setdefault(cell, []).append(wall_id)

    def _xy(self, lon: float, lat: float) -> Tuple[float, float]:
        return ((lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky)

    @staticmethod
    def _cells_on_segment(x1: float, y1: float, x2: float, y2: float):
        length = math.hypot(x2 - x1, y2 - y1)
        steps = max(1, int(length / (GRID_M / 2)) + 1)
        seen = set()
        for i in range(steps + 1):
            t = i / steps
            cell = (math.floor((x1 + (x2 - x1) * t) / GRID_M), math.floor((y1 + (y2 - y1) * t) / GRID_M))
            if cell not in seen:
                seen.add(cell)
                yield cell

    def _inside(self, x: float, y: float, polygon: Sequence[Tuple[float, float]]) -> bool:
        inside = False
        for (x1, y1), (x2, y2) in zip(polygon, polygon[1:]):
            if (y1 > y) != (y2 > y):
                xin = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
                if xin > x:
                    inside = not inside
        return inside

    def is_shaded(self, lon: float, lat: float, sun_azimuth_deg: float, sun_elevation_deg: float) -> bool:
        if sun_elevation_deg <= 0.0:
            return True  # no direct sun at all
        x, y = self._xy(lon, lat)
        tan_el = math.tan(math.radians(sun_elevation_deg))
        reach = min(MAX_RAY_M, (self.max_height - EYE_HEIGHT_M) / tan_el) if tan_el > 0 else MAX_RAY_M
        if reach <= 0:
            return False
        az = math.radians(sun_azimuth_deg)
        dx, dy = math.sin(az), math.cos(az)  # azimuth clockwise from north
        candidates = set()
        for cell in self._cells_on_segment(x, y, x + dx * reach, y + dy * reach):
            for cx in (cell[0] - 1, cell[0], cell[0] + 1):
                for cy in (cell[1] - 1, cell[1], cell[1] + 1):
                    candidates.update(self.grid.get((cx, cy), ()))
        checked_polygons = set()
        for wall_id in candidates:
            x1, y1, x2, y2, index = self.walls[wall_id]
            if index not in checked_polygons:
                checked_polygons.add(index)
                if self._inside(x, y, self.polygons[index]):
                    return True
            # Ray (x, y) + t (dx, dy), t >= 0, against the wall segment.
            ex, ey = x2 - x1, y2 - y1
            denom = dx * ey - dy * ex
            if abs(denom) < 1e-12:
                continue
            t = ((x1 - x) * ey - (y1 - y) * ex) / denom
            u = ((x1 - x) * dy - (y1 - y) * dx) / denom
            if t <= 0 or u < 0 or u > 1:
                continue
            if self.heights[index] - EYE_HEIGHT_M > t * tan_el:
                return True
        return False


def shade_along_route(
    coords: Sequence[Sequence[float]],
    buildings: Sequence[Building],
    sun_azimuth_deg: float,
    sun_elevation_deg: float,
    sample_m: float = 5.0,
    caster: Optional[ShadowCaster] = None,
) -> ShadeResult:
    """Shaded length along a route, sampling every ``sample_m`` metres."""
    if len(coords) < 2:
        return ShadeResult(0.0, 0.0, 0, 0)
    if caster is None:
        lat0 = sum(c[1] for c in coords) / len(coords)
        lon0 = sum(c[0] for c in coords) / len(coords)
        caster = ShadowCaster(buildings, lat0, lon0)
    shaded = total = 0.0
    samples = shaded_samples = 0
    for a, b in zip(coords, coords[1:]):
        ax, ay = caster._xy(a[0], a[1])
        bx, by = caster._xy(b[0], b[1])
        length = math.hypot(bx - ax, by - ay)
        if length <= 0:
            continue
        steps = max(1, int(math.ceil(length / sample_m)))
        piece = length / steps
        for i in range(steps):
            t = (i + 0.5) / steps
            lon = a[0] + (b[0] - a[0]) * t
            lat = a[1] + (b[1] - a[1]) * t
            samples += 1
            total += piece
            if caster.is_shaded(lon, lat, sun_azimuth_deg, sun_elevation_deg):
                shaded += piece
                shaded_samples += 1
    return ShadeResult(shaded, total, samples, shaded_samples)
