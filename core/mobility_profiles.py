"""Mobility profiles catalog and parameter specifications for 3D route planning.

Provides detailed profiles across pedestrian, accessibility, micromobility,
and vehicular transport modes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class MobilityProfile:
    """Configuration and physiological/kinematic constraints for a mobility mode."""

    key: str
    name: str
    category: str  # 'pedestrian', 'micromobility', 'vehicle'
    base_speed_kmh: float
    max_slope_pct: float  # Slope beyond which path is strictly impassable or heavily penalised
    stair_allowed: bool
    stair_penalty: float  # Resistance multiplier for stairs (1.0 = normal, 100.0+ = forbidden)
    slope_sensitivity: float  # Exponent/multiplier for slope resistance
    heat_sensitivity: float  # 0.0 to 1.0: priority on avoiding high surface temperature (LST)
    green_preference: float  # 0.0 to 1.0: bonus for tree shade and greenery
    surface_smoothness_req: float  # 0.0 (all-terrain) to 1.0 (requires smooth paved surface)
    hierarchy_weights: Dict[int, float] = field(default_factory=dict)  # Hierarchy rank (1-5) multipliers
    description: str = ""
    icon_name: str = "adult"

    def calculate_edge_resistance(
        self,
        length_m: float,
        slope_pct: float,
        is_steps: bool = False,
        surface_quality: float = 0.8,  # 0.0 = rough dirt/cobble, 1.0 = smooth asphalt
        hierarchy_rank: int = 4,
        lst_normalized: float = 0.5,  # 0.0 = cool/shaded, 1.0 = extreme heat island
        green_normalized: float = 0.5,  # 0.0 = no canopy, 1.0 = dense tree canopy
        custom_weights: Optional[Dict[str, float]] = None,
    ) -> float:
        """Calculate generalized impedance (cost) for traversing a segment under this profile."""
        weights = custom_weights or {}
        w_slope = weights.get("slope", self.slope_sensitivity)
        w_heat = weights.get("heat", self.heat_sensitivity)
        w_green = weights.get("green", self.green_preference)

        # 1. Slope Penalty
        abs_slope = abs(slope_pct)
        if abs_slope > self.max_slope_pct:
            if not self.category == "pedestrian":
                # Impassable for vehicle / wheelchair
                return float("inf")
            # Severe exponential penalty for pedestrians walking on extreme slope
            slope_mult = 1.0 + (abs_slope / max(1.0, self.max_slope_pct)) ** 3 * 20.0 * w_slope
        else:
            slope_mult = 1.0 + (abs_slope / 10.0) * w_slope * 1.5

        # 2. Steps / Stairs Penalty
        if is_steps:
            if not self.stair_allowed:
                return float("inf")
            stair_mult = self.stair_penalty
        else:
            stair_mult = 1.0

        # 3. Surface Smoothness Penalty
        if surface_quality < self.surface_smoothness_req:
            roughness_gap = self.surface_smoothness_req - surface_quality
            smooth_mult = 1.0 + roughness_gap * 4.0
        else:
            smooth_mult = 1.0

        # 4. Thermal & Environmental Microclimate Resistance
        # Hot surface adds resistance; tree canopy reduces resistance
        thermal_cost = 1.0 + (lst_normalized * w_heat * 1.8) - (green_normalized * w_green * 0.4)
        thermal_mult = max(0.6, thermal_cost)

        # 5. Road Hierarchy Multiplier
        hier_mult = self.hierarchy_weights.get(hierarchy_rank, 1.0)

        # Total Generalized Resistance Cost
        cost = length_m * slope_mult * stair_mult * smooth_mult * thermal_mult * hier_mult
        return max(0.01, cost)


# -------------------------------------------------------------------------
# Standard Profiles Registry
# -------------------------------------------------------------------------

PROFILES: Dict[str, MobilityProfile] = {
    "adult": MobilityProfile(
        key="adult",
        name="Standard Adult",
        category="pedestrian",
        base_speed_kmh=5.0,
        max_slope_pct=25.0,
        stair_allowed=True,
        stair_penalty=1.2,
        slope_sensitivity=1.0,
        heat_sensitivity=0.4,
        green_preference=0.4,
        surface_smoothness_req=0.2,
        hierarchy_weights={1: 2.5, 2: 1.8, 3: 1.2, 4: 1.0, 5: 0.9},
        description="Standard adult pedestrian with normal walking speed and moderate slope tolerance.",
        icon_name="adult",
    ),
    "senior": MobilityProfile(
        key="senior",
        name="Senior / Elderly",
        category="pedestrian",
        base_speed_kmh=3.2,
        max_slope_pct=10.0,
        stair_allowed=True,
        stair_penalty=8.0,
        slope_sensitivity=2.8,
        heat_sensitivity=0.85,
        green_preference=0.9,
        surface_smoothness_req=0.6,
        hierarchy_weights={1: 4.0, 2: 2.5, 3: 1.5, 4: 1.0, 5: 0.8},
        description="Reduced walking pace, high slope avoidance, strong preference for shaded and cool paths.",
        icon_name="senior",
    ),
    "child": MobilityProfile(
        key="child",
        name="Child / Family Walk",
        category="pedestrian",
        base_speed_kmh=3.5,
        max_slope_pct=12.0,
        stair_allowed=True,
        stair_penalty=2.0,
        slope_sensitivity=1.5,
        heat_sensitivity=0.7,
        green_preference=0.8,
        surface_smoothness_req=0.4,
        hierarchy_weights={1: 8.0, 2: 4.0, 3: 1.8, 4: 1.0, 5: 0.7},
        description="Prioritizes quiet residential streets, green parks, and avoids heavy arterial traffic.",
        icon_name="child",
    ),
    "stroller": MobilityProfile(
        key="stroller",
        name="Stroller / Pram (Bebek Arabalı)",
        category="pedestrian",
        base_speed_kmh=4.0,
        max_slope_pct=6.0,
        stair_allowed=False,
        stair_penalty=100.0,
        slope_sensitivity=3.0,
        heat_sensitivity=0.6,
        green_preference=0.7,
        surface_smoothness_req=0.8,
        hierarchy_weights={1: 3.5, 2: 2.0, 3: 1.3, 4: 1.0, 5: 0.9},
        description="Strictly avoids steps and high curbs; enforces smooth pavement and gentle ramps (max 6% grade).",
        icon_name="stroller",
    ),
    "wheelchair": MobilityProfile(
        key="wheelchair",
        name="Wheelchair / Barrier-Free",
        category="pedestrian",
        base_speed_kmh=3.8,
        max_slope_pct=5.0,
        stair_allowed=False,
        stair_penalty=1000.0,
        slope_sensitivity=4.0,
        heat_sensitivity=0.6,
        green_preference=0.6,
        surface_smoothness_req=0.9,
        hierarchy_weights={1: 3.0, 2: 1.8, 3: 1.2, 4: 1.0, 5: 0.9},
        description="Strict universal accessibility: zero stairs, max 5% slope tolerance, smooth continuous surfaces.",
        icon_name="wheelchair",
    ),
    "bicycle": MobilityProfile(
        key="bicycle",
        name="City & Commuter Bicycle",
        category="micromobility",
        base_speed_kmh=18.0,
        max_slope_pct=15.0,
        stair_allowed=False,
        stair_penalty=50.0,
        slope_sensitivity=1.8,
        heat_sensitivity=0.3,
        green_preference=0.5,
        surface_smoothness_req=0.6,
        hierarchy_weights={1: 10.0, 2: 1.6, 3: 1.1, 4: 0.9, 5: 1.0},
        description="Grade-aware cycling routing prioritizing dedicated paths, secondary streets, and moderate slopes.",
        icon_name="bicycle",
    ),
    "scooter": MobilityProfile(
        key="scooter",
        name="E-Scooter / Micromobility",
        category="micromobility",
        base_speed_kmh=20.0,
        max_slope_pct=12.0,
        stair_allowed=False,
        stair_penalty=100.0,
        slope_sensitivity=2.5,
        heat_sensitivity=0.3,
        green_preference=0.3,
        surface_smoothness_req=0.85,
        hierarchy_weights={1: 10.0, 2: 2.0, 3: 1.2, 4: 0.9, 5: 1.0},
        description="Requires smooth pavement and low curb transitions; limits climbing angles to motor torque bounds.",
        icon_name="scooter",
    ),
    "car": MobilityProfile(
        key="car",
        name="Passenger Car",
        category="vehicle",
        base_speed_kmh=50.0,
        max_slope_pct=25.0,
        stair_allowed=False,
        stair_penalty=1000.0,
        slope_sensitivity=0.5,
        heat_sensitivity=0.0,
        green_preference=0.0,
        surface_smoothness_req=0.5,
        hierarchy_weights={1: 0.6, 2: 0.8, 3: 1.0, 4: 1.5, 5: 2.5},
        description="Vehicular routing prioritizing high-capacity arterials and motorways with realistic speed hierarchy.",
        icon_name="car",
    ),
    "truck": MobilityProfile(
        key="truck",
        name="Heavy Goods / Logistics Truck",
        category="vehicle",
        base_speed_kmh=40.0,
        max_slope_pct=7.0,
        stair_allowed=False,
        stair_penalty=1000.0,
        slope_sensitivity=3.5,
        heat_sensitivity=0.0,
        green_preference=0.0,
        surface_smoothness_req=0.7,
        hierarchy_weights={1: 0.5, 2: 0.7, 3: 1.2, 4: 3.0, 5: 10.0},
        description="Heavy commercial vehicles: strict 7% grade limit, wide turning radii, avoids narrow urban corridors.",
        icon_name="truck",
    ),
}


def get_profile(key: str) -> MobilityProfile:
    """Retrieve profile by key, falling back to 'adult' if unknown."""
    return PROFILES.get(key.lower(), PROFILES["adult"])


def list_profile_keys() -> List[str]:
    """Return all registered profile keys."""
    return list(PROFILES.keys())
