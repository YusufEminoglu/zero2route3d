"""Emergency evacuation, hazard avoidance, and egress capacity routing engine."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .kinematics import haversine_distance_2d
from .mobility_profiles import get_profile
from .routing_engine import RouteResult3D, RoutingEngine3D, Waypoint


@dataclass
class HazardZone:
    """Geographic hazard polygon or circular danger zone with risk penalty."""

    center_lon: float
    center_lat: float
    radius_m: float
    severity_multiplier: float = 100.0  # Cost multiplier when inside hazard buffer
    hazard_type: str = "Flood / Debris"


@dataclass
class EvacuationPlan:
    """Optimal emergency evacuation route to nearest muster zone."""

    muster_point: Waypoint
    route_result: RouteResult3D
    hazard_clearance_dist_m: float
    egress_time_min: float
    risk_score: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "muster_point": {"name": self.muster_point.name, "lon": self.muster_point.lon, "lat": self.muster_point.lat},
            "distance_km": self.route_result.statistics.total_distance_km,
            "egress_time_min": round(self.egress_time_min, 1),
            "hazard_clearance_dist_m": round(self.hazard_clearance_dist_m, 1),
            "risk_score": round(self.risk_score, 1),
        }


class EvacuationRouter:
    """Computes least-risk evacuation routes avoiding dynamic hazards to designated muster points."""

    def __init__(self, routing_engine: RoutingEngine3D) -> None:
        self.engine = routing_engine
        self.hazards: List[HazardZone] = []

    def add_hazard_zone(self, lon: float, lat: float, radius_m: float, hazard_type: str = "Debris") -> None:
        """Register a dynamic hazard zone."""
        self.hazards.append(HazardZone(lon, lat, radius_m, hazard_type=hazard_type))

    def calculate_evacuation_route(
        self,
        origin: Waypoint,
        muster_points: Sequence[Waypoint],
        profile_key: str = "adult",
    ) -> EvacuationPlan:
        """Find the safest evacuation path to the best accessible muster point."""
        if not muster_points:
            # Fallback direct path
            res = self.engine.calculate_route([origin, origin], profile_key=profile_key)
            return EvacuationPlan(origin, res, 0.0, 0.0, 0.0)

        best_plan: Optional[EvacuationPlan] = None
        min_risk_cost = float("inf")

        for muster in muster_points:
            res = self.engine.calculate_route([origin, muster], profile_key=profile_key, compute_alternatives=False)

            # Evaluate distance to hazards along the computed path
            min_hazard_dist = float("inf")
            risk_penalty = 0.0

            for pt in res.coordinates_3d:
                for h in self.hazards:
                    d = haversine_distance_2d((pt[0], pt[1]), (h.center_lon, h.center_lat))
                    min_hazard_dist = min(min_hazard_dist, d)
                    if d < h.radius_m:
                        risk_penalty += (h.radius_m - d) * h.severity_multiplier

            total_cost = res.statistics.total_duration_min + (risk_penalty / 60.0)

            if total_cost < min_risk_cost:
                min_risk_cost = total_cost
                best_plan = EvacuationPlan(
                    muster_point=muster,
                    route_result=res,
                    hazard_clearance_dist_m=min_hazard_dist if min_hazard_dist != float("inf") else 9999.0,
                    egress_time_min=res.statistics.total_duration_min,
                    risk_score=risk_penalty,
                )

        return best_plan or EvacuationPlan(muster_points[0], res, 0.0, 0.0, 0.0)
