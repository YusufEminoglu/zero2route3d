"""Multi-criteria environmental raster cost surfaces and spatial sampling.

Supports sampling digital elevation models (DEM), slope/aspect derivation,
Land Surface Temperature (LST), and tree canopy/greenery indices.
Includes graceful fallback if raster layers are not present in the QGIS session.
"""
from __future__ import annotations

import contextlib
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Sequence

from .kinematics import haversine_distance_2d


@dataclass
class MCDAWeights:
    """Weights configuration for Multi-Criteria Decision Analysis (AHP)."""

    weight_distance: float = 1.0
    weight_slope: float = 1.0
    weight_heat: float = 0.5
    weight_green: float = 0.5
    weight_safety: float = 0.5

    def normalized_dict(self) -> Dict[str, float]:
        """Return dictionary of normalized weight coefficients summing to 1.0."""
        total = (
            self.weight_distance
            + self.weight_slope
            + self.weight_heat
            + self.weight_green
            + self.weight_safety
        )
        if total <= 0:
            return {
                "distance": 0.2,
                "slope": 0.2,
                "heat": 0.2,
                "green": 0.2,
                "safety": 0.2,
            }
        return {
            "distance": self.weight_distance / total,
            "slope": self.weight_slope / total,
            "heat": self.weight_heat / total,
            "green": self.weight_green / total,
            "safety": self.weight_safety / total,
        }


class EnvironmentalSurfaceSampler:
    """Samples environmental parameters from active QGIS raster layers or fallback interpolators."""

    def __init__(
        self,
        dem_layer: Optional[Any] = None,
        lst_layer: Optional[Any] = None,
        green_layer: Optional[Any] = None,
    ) -> None:
        self.dem_layer = dem_layer
        self.lst_layer = lst_layer
        self.green_layer = green_layer
        self._dem_cache: Dict[Tuple[float, float], float] = {}

    def sample_elevation(self, lon: float, lat: float) -> float:
        """Sample elevation in meters at given WGS84 coordinate."""
        coord_key = (round(lon, 5), round(lat, 5))
        if coord_key in self._dem_cache:
            return self._dem_cache[coord_key]

        elevation = 0.0

        # Attempt to sample from QGIS raster layer if available
        if self.dem_layer is not None:
            with contextlib.suppress(Exception):
                from qgis.core import (
                    QgsPointXY,
                    QgsCoordinateReferenceSystem,
                    QgsCoordinateTransform,
                    QgsProject,
                )

                pt = QgsPointXY(lon, lat)
                crs_src = QgsCoordinateReferenceSystem("EPSG:4326")
                crs_dest = self.dem_layer.crs()
                if crs_src != crs_dest:
                    transform = QgsCoordinateTransform(crs_src, crs_dest, QgsProject.instance())
                    pt = transform.transform(pt)

                val, success = self.dem_layer.dataProvider().sample(pt, 1)
                if success and val is not None and not math.isnan(val) and val > -9999:
                    elevation = float(val)
                    self._dem_cache[coord_key] = elevation
                    return elevation

        # Fallback gentle synthetic topography for offline preview/testing
        # Produces deterministic, smooth hills based on coordinates
        elevation = 15.0 + 35.0 * math.sin(lon * 80.0) * math.cos(lat * 80.0)
        self._dem_cache[coord_key] = elevation
        return elevation

    def sample_slope_and_aspect(
        self,
        p1: Sequence[float],
        p2: Sequence[float]
    ) -> Tuple[float, float]:
        """Compute directional slope (%) and aspect angle (degrees) between two points."""
        dist_2d = haversine_distance_2d(p1, p2)
        if dist_2d < 0.1:
            return 0.0, 0.0

        z1 = float(p1[2]) if len(p1) > 2 else self.sample_elevation(p1[0], p1[1])
        z2 = float(p2[2]) if len(p2) > 2 else self.sample_elevation(p2[0], p2[1])

        dz = z2 - z1
        slope_pct = (dz / dist_2d) * 100.0

        # Compass bearing from p1 to p2
        dx = (p2[0] - p1[0]) * math.cos(math.radians((p1[1] + p2[1]) * 0.5))
        dy = p2[1] - p1[1]
        bearing_rad = math.atan2(dx, dy)
        aspect_deg = (math.degrees(bearing_rad) + 360.0) % 360.0

        return slope_pct, aspect_deg

    def sample_lst(self, lon: float, lat: float) -> float:
        """Sample Land Surface Temperature (normalized 0.0 = cool, 1.0 = hot)."""
        if self.lst_layer is not None:
            with contextlib.suppress(Exception):
                from qgis.core import (
                    QgsPointXY,
                    QgsCoordinateReferenceSystem,
                    QgsCoordinateTransform,
                    QgsProject,
                )

                pt = QgsPointXY(lon, lat)
                crs_src = QgsCoordinateReferenceSystem("EPSG:4326")
                crs_dest = self.lst_layer.crs()
                if crs_src != crs_dest:
                    transform = QgsCoordinateTransform(crs_src, crs_dest, QgsProject.instance())
                    pt = transform.transform(pt)

                val, success = self.lst_layer.dataProvider().sample(pt, 1)
                if success and val is not None and not math.isnan(val):
                    # Assume typical urban LST range ~ 20 to 50 Celsius
                    normalized = (float(val) - 20.0) / 30.0
                    return max(0.0, min(1.0, normalized))

        # Default neutral thermal index
        return 0.5

    def sample_greenery(self, lon: float, lat: float) -> float:
        """Sample green tree canopy / NDVI (normalized 0.0 = bare/concrete, 1.0 = lush canopy)."""
        if self.green_layer is not None:
            with contextlib.suppress(Exception):
                from qgis.core import (
                    QgsPointXY,
                    QgsCoordinateReferenceSystem,
                    QgsCoordinateTransform,
                    QgsProject,
                )

                pt = QgsPointXY(lon, lat)
                crs_src = QgsCoordinateReferenceSystem("EPSG:4326")
                crs_dest = self.green_layer.crs()
                if crs_src != crs_dest:
                    transform = QgsCoordinateTransform(crs_src, crs_dest, QgsProject.instance())
                    pt = transform.transform(pt)

                val, success = self.green_layer.dataProvider().sample(pt, 1)
                if success and val is not None and not math.isnan(val):
                    return max(0.0, min(1.0, float(val)))

        # Default moderate greenery
        return 0.4
