"""02Route 3D Core Analytical Engine."""
from __future__ import annotations

from .ahp_engine import AHPEngine, AHPResult
from .environmental_raster import EnvironmentalSurfaceSampler, MCDAWeights
from .html_bundler import StandaloneHtmlBundler
from .isochrone_engine import IsochroneBand, IsochroneEngine3D, IsochroneResult
from .kinematics import (
    aerodynamic_drag_power,
    cyclist_speed,
    haversine_distance_2d,
    haversine_distance_3d,
    minetti_energy_cost,
    rolling_resistance_force,
    scooter_speed,
    senior_fatigue_decay,
    solar_irradiance_aspect_factor,
    tobler_walking_speed,
    universal_thermal_comfort_utci,
    vehicle_free_flow_speed,
)
from .mobility_profiles import (
    PROFILES,
    MobilityProfile,
    get_profile,
    list_profile_keys,
    load_custom_profile_json,
    save_custom_profile_json,
)
from .multimodal import MultiModalJourney, MultiModalLeg, MultiModalRouter
from .network_source import NetworkSourceManager, RoadSegment
from .profile_stats import (
    CueInstruction,
    RouteStatistics,
    compute_route_statistics,
    densify_3d_linestring,
    generate_cue_sheet,
    smooth_elevation_series,
)
from .routing_engine import RouteResult3D, RoutingEngine3D, Waypoint
from .tsp_solver import solve_tsp_order

__all__ = [
    "AHPEngine",
    "AHPResult",
    "EnvironmentalSurfaceSampler",
    "MCDAWeights",
    "StandaloneHtmlBundler",
    "IsochroneBand",
    "IsochroneEngine3D",
    "IsochroneResult",
    "MultiModalJourney",
    "MultiModalLeg",
    "MultiModalRouter",
    "aerodynamic_drag_power",
    "cyclist_speed",
    "haversine_distance_2d",
    "haversine_distance_3d",
    "minetti_energy_cost",
    "rolling_resistance_force",
    "scooter_speed",
    "senior_fatigue_decay",
    "solar_irradiance_aspect_factor",
    "tobler_walking_speed",
    "universal_thermal_comfort_utci",
    "vehicle_free_flow_speed",
    "PROFILES",
    "MobilityProfile",
    "get_profile",
    "list_profile_keys",
    "load_custom_profile_json",
    "save_custom_profile_json",
    "NetworkSourceManager",
    "RoadSegment",
    "CueInstruction",
    "RouteStatistics",
    "compute_route_statistics",
    "densify_3d_linestring",
    "generate_cue_sheet",
    "smooth_elevation_series",
    "RouteResult3D",
    "RoutingEngine3D",
    "Waypoint",
    "solve_tsp_order",
]
