"""Biomechanical and vehicle kinematic models for 3D route analysis.

Includes:
- Geodesic 3D distance with elevation delta
- Tobler's Hiking Function for slope-aware pedestrian velocity
- Minetti (2002) metabolic energy expenditure equation
- Cyclist climbing power and grade resistance curves
- E-Scooter traction and battery drain models
- Road hierarchy vehicle speed modeling
"""
from __future__ import annotations

import math
from typing import Tuple, Sequence


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
    # Clamp slope to realistic pedestrian bounds [-0.60, 0.60] (up to 60% grade)
    s = max(-0.60, min(0.60, float(slope_fraction)))
    scale = max(0.2, base_speed_kmh / 5.0)
    
    # Peak speed occurs at a slight downhill (~ -0.05 or -5% grade)
    speed = 6.0 * math.exp(-3.5 * abs(s + 0.05)) * scale
    
    # Safe bounds: minimum crawl speed 0.5 km/h, maximum sprint 8.0 km/h
    return max(0.5, min(8.0, speed))


def minetti_energy_cost(
    slope_fraction: float,
    mass_kg: float = 70.0,
    distance_m: float = 100.0
) -> Tuple[float, float]:
    """Calculate metabolic energy expenditure using Minetti (2002) polynomial.
    
    Returns:
        Tuple of (energy_in_joules, energy_in_kilocalories).
    """
    # s is gradient (dh / dx)
    s = max(-0.50, min(0.50, float(slope_fraction)))
    
    # Minetti energy cost per unit body mass and distance: J / (kg * m)
    # C_w(i) = 280.5*s^5 - 58.7*s^4 - 228.1*s^3 - 10.3*s^2 + 233.5*s + 2.155
    cost_j_kg_m = (
        280.5 * (s ** 5)
        - 58.7 * (s ** 4)
        - 228.1 * (s ** 3)
        - 10.3 * (s ** 2)
        + 233.5 * s
        + 2.155
    )
    # Energy cost cannot drop below basal metabolic cost (~1.5 J/(kg*m))
    cost_j_kg_m = max(1.5, cost_j_kg_m)
    
    total_joules = cost_j_kg_m * mass_kg * distance_m
    total_kcal = total_joules / 4184.0
    return total_joules, total_kcal


def cyclist_speed(slope_fraction: float, base_speed_kmh: float = 18.0) -> float:
    """Calculate bicycle speed (km/h) as a function of gradient."""
    s = max(-0.25, min(0.25, float(slope_fraction)))
    if s < 0:
        # Downhill boost with coasting limit
        speed = base_speed_kmh * (1.0 + abs(s) * 2.2)
        return min(45.0, speed)
    # Uphill climbing reduction
    speed = base_speed_kmh / (1.0 + s * 8.5)
    return max(3.5, speed)


def scooter_speed(slope_fraction: float, base_speed_kmh: float = 20.0) -> float:
    """Calculate micromobility/e-scooter speed (km/h) with motor torque reduction on slopes."""
    s = max(-0.20, min(0.20, float(slope_fraction)))
    if s > 0.12:
        # Grade exceeds typical scooter motor capacity -> walking/slow push speed
        return 4.0
    if s > 0:
        speed = base_speed_kmh * max(0.25, 1.0 - (s / 0.14) ** 1.5)
        return max(4.0, speed)
    # Downhill regenerative braking limit
    return min(25.0, base_speed_kmh * (1.0 + abs(s) * 0.5))


def vehicle_free_flow_speed(
    hierarchy_rank: int,
    lanes: int = 2,
    slope_pct: float = 0.0
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
    # Lane width / capacity bonus
    speed += min(10.0, max(0, lanes - 2) * 4.0)
    # Grade deceleration (trucks and cars slow down on steep grades)
    routed_slope = min(35.0, max(0.0, abs(slope_pct)))
    speed *= max(0.50, 1.0 - (routed_slope / 100.0) * 0.85)
    return max(10.0, speed)
