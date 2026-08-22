"""Biomechanical, vehicle, and microclimate kinematic models for 3D route analysis.

Includes:
- Geodesic 3D distance with elevation delta
- Tobler's Hiking Function for slope-aware pedestrian velocity
- Minetti (2002) metabolic energy expenditure equation
- Aerodynamic drag & wind resistance equations
- Surface rolling resistance coefficients (C_rr)
- Senior fatigue and metabolic degradation over distance
- Solar aspect and dynamic sun angle irradiance model
- UTCI-equivalent thermal comfort and heat stress formulas
- Cyclist climbing power and grade resistance curves
- E-Scooter traction and battery drain models
- Road hierarchy vehicle speed modeling
"""
from __future__ import annotations

import math
from typing import Dict, Sequence, Tuple


# Surface rolling resistance coefficients (C_rr)
ROLLING_RESISTANCE: Dict[str, float] = {
    "asphalt": 0.004,
    "concrete": 0.005,
    "paved": 0.006,
    "paving_stones": 0.012,
    "cobblestone": 0.018,
    "gravel": 0.022,
    "compacted": 0.015,
    "dirt": 0.030,
    "ground": 0.035,
    "grass": 0.045,
    "sand": 0.080,
}


def haversine_distance_2d(coord1: Sequence[float], coord2: Sequence[float]) -> float:
    """Calculate the geodesic 2D distance in meters between two (lon, lat) points."""
    lon1, lat1 = float(coord1[0]), float(coord1[1])
    lon2, lat2 = float(coord2[0]), float(coord2[1])
    radius = 6371008.8  # WGS84 mean earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(max(0.0, min(1.0, a))), math.sqrt(max(0.0, min(1.0, 1.0 - a))))
    return radius * c


def haversine_distance_3d(coord1: Sequence[float], coord2: Sequence[float]) -> float:
    """Calculate the 3D distance in meters considering elevation (Z coordinate)."""
    d_2d = haversine_distance_2d(coord1, coord2)
    z1 = float(coord1[2]) if len(coord1) > 2 else 0.0
    z2 = float(coord2[2]) if len(coord2) > 2 else 0.0
    dz = z2 - z1
    return math.hypot(d_2d, dz)


def tobler_walking_speed(slope_fraction: float, base_speed_kmh: float = 5.0) -> float:
    """Compute walking speed (km/h) as a function of slope (dh / dx) using Tobler's curve.
    
    Standard Tobler formula: W = 6.0 * exp(-3.5 * |s + 0.05|) km/h where s is slope fraction.
    Scaled relative to the specific profile's base walking speed.
    """
    s = max(-0.60, min(0.60, float(slope_fraction)))
    scale = max(0.2, base_speed_kmh / 5.0)
    speed = 6.0 * math.exp(-3.5 * abs(s + 0.05)) * scale
    return max(0.5, min(8.0, speed))


def minetti_energy_cost(
    slope_fraction: float,
    mass_kg: float = 70.0,
    distance_m: float = 100.0,
) -> Tuple[float, float]:
    """Calculate metabolic energy expenditure using Minetti (2002) polynomial.
    
    Returns:
        Tuple of (energy_in_joules, energy_in_kilocalories).
    """
    s = max(-0.50, min(0.50, float(slope_fraction)))
    cost_j_kg_m = (
        280.5 * (s ** 5)
        - 58.7 * (s ** 4)
        - 228.1 * (s ** 3)
        - 10.3 * (s ** 2)
        + 233.5 * s
        + 2.155
    )
    cost_j_kg_m = max(1.5, cost_j_kg_m)
    total_joules = cost_j_kg_m * mass_kg * distance_m
    total_kcal = total_joules / 4184.0
    return total_joules, total_kcal


def senior_fatigue_decay(distance_m: float, accumulated_climb_m: float) -> float:
    """Calculate velocity decay multiplier (1.0 down to 0.45) for elderly pedestrians due to fatigue."""
    dist_km = distance_m / 1000.0
    fatigue_score = (dist_km * 0.12) + (accumulated_climb_m / 100.0 * 0.25)
    decay = math.exp(-0.35 * fatigue_score)
    return max(0.45, min(1.0, decay))


def aerodynamic_drag_power(
    velocity_kmh: float,
    frontal_area_m2: float = 0.55,
    drag_coeff: float = 0.88,
    air_density: float = 1.225,
) -> float:
    """Calculate aerodynamic power resistance in Watts (P_aero = 0.5 * rho * C_d * A * v^3)."""
    v_ms = max(0.0, velocity_kmh / 3.6)
    return 0.5 * air_density * drag_coeff * frontal_area_m2 * (v_ms ** 3)


def rolling_resistance_force(
    mass_kg: float,
    slope_fraction: float,
    surface_type: str = "asphalt",
) -> float:
    """Calculate rolling friction resistance force in Newtons (F_roll = C_rr * m * g * cos(theta))."""
    c_rr = ROLLING_RESISTANCE.get(surface_type.lower(), 0.005)
    theta = math.atan(abs(slope_fraction))
    g = 9.80665
    return c_rr * mass_kg * g * math.cos(theta)


def cyclist_speed(
    slope_fraction: float,
    base_speed_kmh: float = 18.0,
    rider_power_watts: float = 160.0,
    total_mass_kg: float = 85.0,
    surface: str = "asphalt",
) -> float:
    """Calculate physics-based bicycle speed (km/h) accounting for gradient, rolling resistance, and drag."""
    s = max(-0.25, min(0.25, float(slope_fraction)))
    if s < -0.02:
        # Downhill with coasting limit
        speed = base_speed_kmh * (1.0 + abs(s) * 2.2)
        return min(45.0, speed)

    # Uphill power balance: P = (F_gravity + F_roll + F_aero) * v
    g = 9.80665
    f_grav = total_mass_kg * g * math.sin(math.atan(s))
    f_roll = rolling_resistance_force(total_mass_kg, s, surface)
    f_resist = max(1.0, f_grav + f_roll)

    # Effective speed from mechanical power: v = P / F
    v_ms = rider_power_watts / f_resist
    speed_kmh = v_ms * 3.6
    return max(3.5, min(base_speed_kmh * 1.5, speed_kmh))


def scooter_speed(slope_fraction: float, base_speed_kmh: float = 20.0) -> float:
    """Calculate micromobility/e-scooter speed (km/h) with motor torque reduction on slopes."""
    s = max(-0.20, min(0.20, float(slope_fraction)))
    if s > 0.12:
        return 4.0  # grade exceeds motor climbing capability
    if s > 0:
        speed = base_speed_kmh * max(0.25, 1.0 - (s / 0.14) ** 1.5)
        return max(4.0, speed)
    return min(25.0, base_speed_kmh * (1.0 + abs(s) * 0.5))


def vehicle_free_flow_speed(
    hierarchy_rank: int,
    lanes: int = 2,
    slope_pct: float = 0.0,
) -> float:
    """Modelled vehicle speed in km/h based on functional road classification, lanes, and grade."""
    base_speeds = {
        1: 80.0,  # Motorway / Primary Arterial
        2: 60.0,  # Secondary Arterial
        3: 45.0,  # Collector / Major Urban
        4: 30.0,  # Local Residential Street
        5: 20.0,  # Narrow Alley / Service Way
    }
    speed = base_speeds.get(hierarchy_rank, 30.0)
    speed += min(10.0, max(0, lanes - 2) * 4.0)
    routed_slope = min(35.0, max(0.0, abs(slope_pct)))
    speed *= max(0.50, 1.0 - (routed_slope / 100.0) * 0.85)
    return max(10.0, speed)


def solar_irradiance_aspect_factor(
    aspect_deg: float,
    slope_pct: float,
    sun_azimuth_deg: float = 180.0,  # Solar noon south
    sun_elevation_deg: float = 55.0,
) -> float:
    """Calculate relative solar irradiance factor (0.0 to 1.0) based on slope angle and sun position."""
    slope_rad = math.atan(abs(slope_pct) / 100.0)
    aspect_rad = math.radians(aspect_deg)
    sun_az_rad = math.radians(sun_azimuth_deg)
    sun_el_rad = math.radians(sun_elevation_deg)

    # Cosine of incidence angle
    cos_inc = (
        math.sin(sun_el_rad) * math.cos(slope_rad)
        + math.cos(sun_el_rad) * math.sin(slope_rad) * math.cos(sun_az_rad - aspect_rad)
    )
    return max(0.0, min(1.0, cos_inc))


def universal_thermal_comfort_utci(
    temp_c: float,
    relative_humidity_pct: float = 50.0,
    wind_speed_ms: float = 1.0,
    mean_radiant_temp_c: float = 35.0,
) -> float:
    """Approximation of Universal Thermal Climate Index (UTCI in Celsius).
    
    Returns normalized thermal strain score (0.0 = comfortable/cool, 1.0 = severe heat stress).
    """
    # Simplified empirical UTCI offset
    utci = temp_c + 0.33 * (mean_radiant_temp_c - temp_c) - 0.70 * math.sqrt(max(0.1, wind_speed_ms))
    # Normalize: 18C = 0.0 (no stress), 38C+ = 1.0 (extreme heat stress)
    norm_stress = (utci - 18.0) / 20.0
    return max(0.0, min(1.0, norm_stress))
