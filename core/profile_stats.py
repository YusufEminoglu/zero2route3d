"""Route diagnostics, elevation profiles, kinematic statistics, and turn-by-turn cue sheets."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .kinematics import (
    cyclist_speed,
    haversine_distance_2d,
    minetti_energy_cost,
    scooter_speed,
    senior_fatigue_decay,
    tobler_walking_speed,
    vehicle_free_flow_speed,
)
from .mobility_profiles import MobilityProfile, get_profile


@dataclass
class CueInstruction:
    """Single step in a turn-by-turn navigation sheet."""

    step_number: int
    instruction: str
    direction: str  # 'depart', 'left', 'right', 'slight_left', 'slight_right', 'straight', 'arrive'
    distance_m: float
    elevation_delta_m: float
    slope_pct: float
    street_name: str
    warning: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_number": self.step_number,
            "instruction": self.instruction,
            "direction": self.direction,
            "distance_m": round(self.distance_m, 1),
            "elevation_delta_m": round(self.elevation_delta_m, 1),
            "slope_pct": round(self.slope_pct, 1),
            "street_name": self.street_name,
            "warning": self.warning,
        }


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
    ada_compliant: bool = True
    ada_violations_count: int = 0
    slope_distribution: Dict[str, float] = field(default_factory=dict)
    elevation_profile: List[Dict[str, Any]] = field(default_factory=list)
    cue_sheet: List[CueInstruction] = field(default_factory=list)

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
            "ada_compliant": self.ada_compliant,
            "ada_violations_count": self.ada_violations_count,
            "slope_distribution": self.slope_distribution,
            "elevation_profile": self.elevation_profile,
            "cue_sheet": [c.to_dict() for c in self.cue_sheet],
        }


def densify_3d_linestring(
    coords: Sequence[Sequence[float]],
    sample_interval_m: float = 8.0,
    max_points: int = 100_000,
) -> List[Tuple[float, float, float]]:
    """Densify a 3D coordinate sequence with a hard memory-safe point cap."""
    valid_coords = [
        (float(c[0]), float(c[1]), float(c[2]) if len(c) > 2 and math.isfinite(float(c[2])) else 0.0)
        for c in coords
        if c and len(c) >= 2 and math.isfinite(float(c[0])) and math.isfinite(float(c[1]))
    ]
    if len(valid_coords) < 2:
        return valid_coords

    interval = max(0.5, float(sample_interval_m)) if math.isfinite(sample_interval_m) and sample_interval_m > 0 else 8.0
    point_limit = max(1_000, min(1_000_000, int(max_points))) if max_points > 0 else 100_000
    total_distance = sum(
        haversine_distance_2d(valid_coords[index], valid_coords[index + 1])
        for index in range(len(valid_coords) - 1)
    )
    estimated_points = total_distance / interval if interval > 0 else float("inf")
    if estimated_points > point_limit:
        interval = max(interval, total_distance / point_limit)
    densified: List[Tuple[float, float, float]] = []

    for i in range(len(valid_coords) - 1):
        p1 = valid_coords[i]
        p2 = valid_coords[i + 1]
        z1 = p1[2]
        z2 = p2[2]

        seg_dist = haversine_distance_2d(p1, p2)
        steps = max(1, int(math.ceil(seg_dist / interval)))

        for step in range(steps):
            frac = step / steps
            lon = p1[0] + (p2[0] - p1[0]) * frac
            lat = p1[1] + (p2[1] - p1[1]) * frac
            z = z1 + (z2 - z1) * frac
            densified.append((lon, lat, z))

    last = valid_coords[-1]
    densified.append((last[0], last[1], last[2]))
    return densified


def smooth_elevation_series(elevations: Sequence[float], window_size: int = 5) -> List[float]:
    """Apply moving Gaussian-weighted smoothing to filter DEM quantization noise."""
    if not elevations:
        return []
    n = len(elevations)
    w = max(1, int(window_size)) if math.isfinite(window_size) and window_size > 0 else 5
    if n < w or w <= 1:
        return [float(e) if math.isfinite(float(e)) else 0.0 for e in elevations]

    clean_elev = [float(e) if math.isfinite(float(e)) else 0.0 for e in elevations]
    smoothed = []
    half = w // 2
    for i in range(n):
        sub = clean_elev[max(0, i - half) : min(n, i + half + 1)]
        smoothed.append(sum(sub) / len(sub))
    return smoothed


def compute_turn_angle_and_direction(
    p_prev: Sequence[float],
    p_curr: Sequence[float],
    p_next: Sequence[float],
) -> Tuple[float, str]:
    """Compute turn angle in degrees and turn direction string."""
    if not p_prev or not p_curr or not p_next or len(p_prev) < 2 or len(p_curr) < 2 or len(p_next) < 2:
        return 0.0, "straight"

    dx1 = float(p_curr[0]) - float(p_prev[0])
    dy1 = float(p_curr[1]) - float(p_prev[1])
    dx2 = float(p_next[0]) - float(p_curr[0])
    dy2 = float(p_next[1]) - float(p_curr[1])

    if not (math.isfinite(dx1) and math.isfinite(dy1) and math.isfinite(dx2) and math.isfinite(dy2)):
        return 0.0, "straight"

    b1 = (math.degrees(math.atan2(dx1, dy1)) + 360.0) % 360.0
    b2 = (math.degrees(math.atan2(dx2, dy2)) + 360.0) % 360.0
    diff = (b2 - b1 + 180.0) % 360.0 - 180.0

    if abs(diff) < 15.0:
        return diff, "straight"
    if diff > 45.0:
        return diff, "right"
    if diff > 15.0:
        return diff, "slight_right"
    if diff < -45.0:
        return diff, "left"
    return diff, "slight_left"


def generate_cue_sheet(
    raw_coords_3d: Sequence[Sequence[float]],
    profile: Optional[MobilityProfile] = None,
) -> List[CueInstruction]:
    """Generate human-readable turn-by-turn navigation instructions."""
    if not raw_coords_3d or len(raw_coords_3d) < 2:
        return []

    prof = profile if profile is not None else get_profile("adult")
    cues: List[CueInstruction] = []
    step_num = 1

    # Start Departure
    cues.append(
        CueInstruction(
            step_number=step_num,
            instruction="Depart from origin point heading forward.",
            direction="depart",
            distance_m=0.0,
            elevation_delta_m=0.0,
            slope_pct=0.0,
            street_name="Starting Point",
        )
    )
    step_num += 1

    accum_dist = 0.0
    accum_dz = 0.0

    for i in range(1, len(raw_coords_3d) - 1):
        p_prev = raw_coords_3d[i - 1]
        p_curr = raw_coords_3d[i]
        p_next = raw_coords_3d[i + 1]

        z_prev = float(p_prev[2]) if len(p_prev) > 2 and math.isfinite(float(p_prev[2])) else 0.0
        z_curr = float(p_curr[2]) if len(p_curr) > 2 and math.isfinite(float(p_curr[2])) else 0.0

        d = haversine_distance_2d(p_prev, p_curr)
        dz = z_curr - z_prev
        accum_dist += d
        accum_dz += dz

        angle, direction = compute_turn_angle_and_direction(p_prev, p_curr, p_next)

        if direction != "straight" or accum_dist > 400.0:
            slope = (accum_dz / max(0.1, accum_dist)) * 100.0
            if not math.isfinite(slope):
                slope = 0.0
            warning = ""
            max_s = prof.max_slope_pct if math.isfinite(prof.max_slope_pct) else 25.0
            if abs(slope) > max_s:
                warning = f"Warning: Steep gradient ({slope:.1f}%) exceeds profile recommendation!"

            dir_text = {
                "left": "Turn left",
                "right": "Turn right",
                "slight_left": "Bear slightly left",
                "slight_right": "Bear slightly right",
                "straight": "Continue straight",
            }.get(direction, "Continue")

            instr = f"{dir_text} along path for {accum_dist:.0f}m ({accum_dz:+.1f}m elevation)."
            cues.append(
                CueInstruction(
                    step_number=step_num,
                    instruction=instr,
                    direction=direction,
                    distance_m=accum_dist,
                    elevation_delta_m=accum_dz,
                    slope_pct=slope,
                    street_name="Urban Path",
                    warning=warning,
                )
            )
            step_num += 1
            accum_dist = 0.0
            accum_dz = 0.0

    # Arrival at Destination
    last_p1 = raw_coords_3d[-2]
    last_p2 = raw_coords_3d[-1]
    last_z1 = float(last_p1[2]) if len(last_p1) > 2 and math.isfinite(float(last_p1[2])) else 0.0
    last_z2 = float(last_p2[2]) if len(last_p2) > 2 and math.isfinite(float(last_p2[2])) else 0.0

    last_d = haversine_distance_2d(last_p1, last_p2)
    last_dz = last_z2 - last_z1
    cues.append(
        CueInstruction(
            step_number=step_num,
            instruction="Arrive at destination.",
            direction="arrive",
            distance_m=accum_dist + last_d,
            elevation_delta_m=accum_dz + last_dz,
            slope_pct=0.0,
            street_name="Destination Point",
        )
    )

    return cues


def compute_route_statistics(
    coords_3d: Sequence[Sequence[float]],
    profile: Optional[MobilityProfile] = None,
    lst_samples: Optional[Sequence[float]] = None,
) -> RouteStatistics:
    """Compute comprehensive kinematic, topographic, and thermal statistics along 3D route."""
    if not coords_3d:
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

    if len(coords_3d) == 1:
        ele = float(coords_3d[0][2]) if len(coords_3d[0]) > 2 and math.isfinite(float(coords_3d[0][2])) else 0.0
        return RouteStatistics(
            total_distance_m=0.0,
            total_duration_s=0.0,
            elevation_gain_m=0.0,
            elevation_loss_m=0.0,
            min_elevation_m=ele,
            max_elevation_m=ele,
            max_slope_pct=0.0,
            avg_slope_pct=0.0,
            total_calories_kcal=0.0,
            thermal_comfort_score=1.0,
        )

    prof = profile if profile is not None else get_profile("adult")

    # Densify coordinates
    dense_pts = densify_3d_linestring(coords_3d, sample_interval_m=6.0)
    if len(dense_pts) < 2:
        ele = float(coords_3d[0][2]) if len(coords_3d[0]) > 2 and math.isfinite(float(coords_3d[0][2])) else 0.0
        return RouteStatistics(
            total_distance_m=0.0,
            total_duration_s=0.0,
            elevation_gain_m=0.0,
            elevation_loss_m=0.0,
            min_elevation_m=ele,
            max_elevation_m=ele,
            max_slope_pct=0.0,
            avg_slope_pct=0.0,
            total_calories_kcal=0.0,
            thermal_comfort_score=1.0,
        )

    cumulative_dist = 0.0
    total_time_s = 0.0
    elevation_gain = 0.0
    elevation_loss = 0.0
    total_calories = 0.0
    slope_sum = 0.0
    ada_violations = 0

    elevations = [p[2] for p in dense_pts]
    min_elev = min(elevations) if elevations else 0.0
    max_elev = max(elevations) if elevations else 0.0
    max_slope = 0.0

    slope_bins = {
        "flat_0_3": 0.0,
        "gentle_3_6": 0.0,
        "moderate_6_10": 0.0,
        "steep_10_15": 0.0,
        "extreme_over_15": 0.0,
    }

    profile_list: List[Dict[str, Any]] = []
    thermal_sum = 0.0
    category = prof.category
    base_spd = prof.base_speed_kmh if math.isfinite(prof.base_speed_kmh) and prof.base_speed_kmh > 0 else 5.0

    for i in range(len(dense_pts) - 1):
        p1 = dense_pts[i]
        p2 = dense_pts[i + 1]

        d_2d = haversine_distance_2d(p1, p2)
        dz = p2[2] - p1[2]
        d_3d = math.hypot(d_2d, dz)

        if not math.isfinite(d_3d) or d_3d < 0.01:
            continue

        slope_pct = (dz / max(0.1, d_2d)) * 100.0 if d_2d >= 0.01 else 0.0
        if not math.isfinite(slope_pct):
            slope_pct = 0.0
        abs_slope = abs(slope_pct)
        max_slope = max(max_slope, abs_slope)
        slope_sum += abs_slope * d_3d

        if abs_slope > 8.33:
            ada_violations += 1

        if dz > 0:
            elevation_gain += dz
        else:
            elevation_loss += abs(dz)

        # Slope bins
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

        # Kinematic calculation
        slope_frac = dz / max(0.1, d_2d) if d_2d >= 0.01 else 0.0
        if not math.isfinite(slope_frac):
            slope_frac = 0.0

        if category == "pedestrian":
            fatigue_mult = (
                senior_fatigue_decay(cumulative_dist, elevation_gain)
                if profile.key == "senior"
                else 1.0
            )
            speed_kmh = tobler_walking_speed(slope_frac, base_speed_kmh=base_spd) * fatigue_mult
            _j, kcal = minetti_energy_cost(slope_frac, mass_kg=70.0, distance_m=d_3d)
            total_calories += kcal
        elif profile.key in {"bicycle", "mtb"}:
            speed_kmh = cyclist_speed(slope_frac, base_speed_kmh=base_spd)
            total_calories += (d_3d / 1000.0) * 28.0
        elif profile.key == "scooter":
            speed_kmh = scooter_speed(slope_frac, base_speed_kmh=base_spd)
        else:
            speed_kmh = vehicle_free_flow_speed(hierarchy_rank=4, lanes=2, slope_pct=slope_pct)

        if not math.isfinite(speed_kmh) or speed_kmh <= 0:
            speed_kmh = base_spd

        spd_ms = max(0.1, speed_kmh * 1000.0 / 3600.0)
        seg_time_s = d_3d / spd_ms
        if math.isfinite(seg_time_s):
            total_time_s += seg_time_s

        lst_val = 0.5
        if lst_samples and i < len(lst_samples):
            v_lst = float(lst_samples[i])
            if math.isfinite(v_lst):
                lst_val = v_lst
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

    if dense_pts:
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

    slope_dist_pct = {
        k: round((v / cumulative_dist) * 100.0, 1) if cumulative_dist > 0 else 0.0
        for k, v in slope_bins.items()
    }

    cues = generate_cue_sheet(coords_3d, profile)

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
        ada_compliant=(ada_violations == 0),
        ada_violations_count=ada_violations,
        slope_distribution=slope_dist_pct,
        elevation_profile=profile_list,
        cue_sheet=cues,
    )
