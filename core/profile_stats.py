"""Route diagnostics, elevation profiles, and kinematic statistics calculator."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .kinematics import (
    haversine_distance_2d,
    haversine_distance_3d,
    minetti_energy_cost,
    tobler_walking_speed,
    vehicle_free_flow_speed,
    cyclist_speed,
    scooter_speed,
)
from .mobility_profiles import MobilityProfile


@dataclass
class ElevationPoint:
    """Sample point along route with distance, elevation, and gradient."""

    distance_m: float
    elevation_m: float
    slope_pct: float
    lon: float
    lat: float
    speed_kmh: float = 5.0
    lst_normalized: float = 0.5


@dataclass
class RouteStatistics:
    """Comprehensive KPIs and diagnostics for a computed 3D route."""

    total_distance_m: float
    total_duration_s: float
    elevation_gain_m: float
    elevation_loss_m: float
    min_elevation_m: float
    max_elevation_m: float
    max_slope_pct: float
    avg_slope_pct: float
    total_calories_kcal: float
    thermal_comfort_score: float  # 0.0 (extreme heat) to 1.0 (ideal cool shade)
    slope_distribution: Dict[str, float] = field(default_factory=dict)
    elevation_profile: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def total_distance_km(self) -> float:
        return round(self.total_distance_m / 1000.0, 2)

    @property
    def total_duration_min(self) -> float:
        return round(self.total_duration_s / 60.0, 1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_distance_m": round(self.total_distance_m, 1),
            "total_distance_km": self.total_distance_km,
            "total_duration_s": round(self.total_duration_s, 1),
            "total_duration_min": self.total_duration_min,
            "elevation_gain_m": round(self.elevation_gain_m, 1),
            "elevation_loss_m": round(self.elevation_loss_m, 1),
            "min_elevation_m": round(self.min_elevation_m, 1),
            "max_elevation_m": round(self.max_elevation_m, 1),
            "max_slope_pct": round(self.max_slope_pct, 1),
            "avg_slope_pct": round(self.avg_slope_pct, 1),
            "total_calories_kcal": round(self.total_calories_kcal, 1),
            "thermal_comfort_score": round(self.thermal_comfort_score, 2),
            "slope_distribution": self.slope_distribution,
            "elevation_profile": self.elevation_profile,
        }


def densify_3d_linestring(
    coords: Sequence[Sequence[float]],
    sample_interval_m: float = 8.0
) -> List[Tuple[float, float, float]]:
    """Densify a 3D coordinate sequence by interpolating points every N meters."""
    if len(coords) < 2:
        return [
            (c[0], c[1], c[2] if len(c) > 2 else 0.0)
            for c in coords
        ]

    densified: List[Tuple[float, float, float]] = []

    for i in range(len(coords) - 1):
        p1 = coords[i]
        p2 = coords[i + 1]
        z1 = float(p1[2]) if len(p1) > 2 else 0.0
        z2 = float(p2[2]) if len(p2) > 2 else 0.0

        seg_dist = haversine_distance_2d(p1, p2)
        steps = max(1, int(math.ceil(seg_dist / sample_interval_m)))

        for step in range(steps):
            frac = step / steps
            lon = p1[0] + (p2[0] - p1[0]) * frac
            lat = p1[1] + (p2[1] - p1[1]) * frac
            z = z1 + (z2 - z1) * frac
            densified.append((lon, lat, z))

    last = coords[-1]
    densified.append((last[0], last[1], float(last[2]) if len(last) > 2 else 0.0))
    return densified


def compute_route_statistics(
    coords_3d: Sequence[Sequence[float]],
    profile: MobilityProfile,
    lst_samples: Optional[Sequence[float]] = None,
) -> RouteStatistics:
    """Compute comprehensive kinematic, topographic, and thermal statistics along 3D route."""
    if len(coords_3d) < 2:
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
            thermal_comfort_score=1.0,
        )

    # Densify coordinates for high-resolution sampling
    dense_pts = densify_3d_linestring(coords_3d, sample_interval_m=6.0)

    cumulative_dist = 0.0
    total_time_s = 0.0
    elevation_gain = 0.0
    elevation_loss = 0.0
    total_calories = 0.0
    slope_sum = 0.0

    elevations = [p[2] for p in dense_pts]
    min_elev = min(elevations)
    max_elev = max(elevations)
    max_slope = 0.0

    # Categorized slope distances (in meters)
    slope_bins = {
        "flat_0_3": 0.0,
        "gentle_3_6": 0.0,
        "moderate_6_10": 0.0,
        "steep_10_15": 0.0,
        "extreme_over_15": 0.0,
    }

    profile_list: List[Dict[str, Any]] = []
    thermal_sum = 0.0

    # Base profile configuration
    category = profile.category
    base_spd = profile.base_speed_kmh

    for i in range(len(dense_pts) - 1):
        p1 = dense_pts[i]
        p2 = dense_pts[i + 1]

        d_2d = haversine_distance_2d(p1, p2)
        dz = p2[2] - p1[2]
        d_3d = math.hypot(d_2d, dz)

        if d_3d < 0.01:
            continue

        slope_pct = (dz / max(0.1, d_2d)) * 100.0
        abs_slope = abs(slope_pct)
        max_slope = max(max_slope, abs_slope)
        slope_sum += abs_slope * d_3d

        if dz > 0:
            elevation_gain += dz
        else:
            elevation_loss += abs(dz)

        # Categorize slope
        if abs_slope < 3.0:
            slope_bins["flat_0_3"] += d_3d
        elif abs_slope < 6.0:
            slope_bins["gentle_3_6"] += d_3d
        elif abs_slope < 10.0:
            slope_bins["moderate_6_10"] += d_3d
        elif abs_slope < 15.0:
            slope_bins["steep_10_15"] += d_3d
        else:
            slope_bins["extreme_over_15"] += d_3d

        # Speed calculation along segment
        slope_frac = dz / max(0.1, d_2d)
        if category == "pedestrian":
            speed_kmh = tobler_walking_speed(slope_frac, base_speed_kmh=base_spd)
            _j, kcal = minetti_energy_cost(slope_frac, mass_kg=70.0, distance_m=d_3d)
            total_calories += kcal
        elif profile.key == "bicycle":
            speed_kmh = cyclist_speed(slope_frac, base_speed_kmh=base_spd)
            total_calories += (d_3d / 1000.0) * 25.0  # ~25 kcal per km cycling
        elif profile.key == "scooter":
            speed_kmh = scooter_speed(slope_frac, base_speed_kmh=base_spd)
        else:  # Vehicle / Truck
            speed_kmh = vehicle_free_flow_speed(hierarchy_rank=4, lanes=2, slope_pct=slope_pct)

        seg_time_s = d_3d / max(0.1, (speed_kmh * 1000.0 / 3600.0))
        total_time_s += seg_time_s

        lst_val = 0.5
        if lst_samples and i < len(lst_samples):
            lst_val = lst_samples[i]
        thermal_sum += lst_val * d_3d

        profile_list.append(
            {
                "distance_m": round(cumulative_dist, 1),
                "elevation_m": round(p1[2], 1),
                "slope_pct": round(slope_pct, 1),
                "speed_kmh": round(speed_kmh, 1),
                "lon": round(p1[0], 6),
                "lat": round(p1[1], 6),
            }
        )
        cumulative_dist += d_3d

    # Append the last point
    last_pt = dense_pts[-1]
    profile_list.append(
        {
            "distance_m": round(cumulative_dist, 1),
            "elevation_m": round(last_pt[2], 1),
            "slope_pct": 0.0,
            "speed_kmh": round(base_spd, 1),
            "lon": round(last_pt[0], 6),
            "lat": round(last_pt[1], 6),
        }
    )

    avg_slope = (slope_sum / cumulative_dist) if cumulative_dist > 0 else 0.0
    mean_lst = (thermal_sum / cumulative_dist) if cumulative_dist > 0 else 0.5
    thermal_comfort = max(0.0, min(1.0, 1.0 - mean_lst))

    # Convert slope bins to percentages
    slope_dist_pct = {
        k: round((v / cumulative_dist) * 100.0, 1) if cumulative_dist > 0 else 0.0
        for k, v in slope_bins.items()
    }

    return RouteStatistics(
        total_distance_m=cumulative_dist,
        total_duration_s=total_time_s,
        elevation_gain_m=elevation_gain,
        elevation_loss_m=elevation_loss,
        min_elevation_m=min_elev,
        max_elevation_m=max_elev,
        max_slope_pct=max_slope,
        avg_slope_pct=avg_slope,
        total_calories_kcal=total_calories,
        thermal_comfort_score=thermal_comfort,
        slope_distribution=slope_dist_pct,
        elevation_profile=profile_list,
    )
