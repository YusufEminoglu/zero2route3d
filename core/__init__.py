"""02Route 3D Core Analytical Engine."""
from __future__ import annotations

from .kinematics import (
    haversine_distance_2d,
    haversine_distance_3d,
    tobler_walking_speed,
    minetti_energy_cost,
    cyclist_speed,
    scooter_speed,
    vehicle_free_flow_speed,
)
from .mobility_profiles import (
    PROFILES,
    MobilityProfile,
    get_profile,
    list_profile_keys,
)
from .environmental_raster import (
    EnvironmentalSurfaceSampler,
    MCDAWeights,
)
from .network_source import (
    NetworkSourceManager,
    RoadSegment,
)
from .routing_engine import (
    RoutingEngine3D,
    RouteResult3D,
    Waypoint,
)
from .profile_stats import (
    compute_route_statistics,
    densify_3d_linestring,
)

__all__ = [
    "haversine_distance_2d",
    "haversine_distance_3d",
    "tobler_walking_speed",
    "minetti_energy_cost",
    "cyclist_speed",
    "scooter_speed",
    "vehicle_free_flow_speed",
    "PROFILES",
    "MobilityProfile",
    "get_profile",
    "list_profile_keys",
    "EnvironmentalSurfaceSampler",
    "MCDAWeights",
    "NetworkSourceManager",
    "RoadSegment",
    "RoutingEngine3D",
    "RouteResult3D",
    "Waypoint",
    "compute_route_statistics",
    "densify_3d_linestring",
]
