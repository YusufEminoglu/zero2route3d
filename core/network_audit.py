"""Routing-network readiness diagnostics for a selected mobility profile."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

from .mobility_profiles import get_profile
from .network_policy import evaluate_edge_access, surface_quality
from .network_source import RoadSegment
from .routing_engine import RoutingEngine3D


@dataclass
class NetworkAuditReport:
    """Summary and per-segment findings for a routable line network."""

    profile_key: str
    segment_count: int
    node_count: int
    directed_edge_count: int
    component_count: int
    largest_component_pct: float
    allowed_segments: int
    blocked_segments: int
    one_way_segments: int
    access_tagged_segments: int
    unknown_surface_segments: int
    findings: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return (
            self.allowed_segments > 0
            and self.node_count >= 2
            and self.largest_component_pct >= 80.0
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile_key": self.profile_key,
            "segment_count": self.segment_count,
            "node_count": self.node_count,
            "directed_edge_count": self.directed_edge_count,
            "component_count": self.component_count,
            "largest_component_pct": self.largest_component_pct,
            "allowed_segments": self.allowed_segments,
            "blocked_segments": self.blocked_segments,
            "one_way_segments": self.one_way_segments,
            "access_tagged_segments": self.access_tagged_segments,
            "unknown_surface_segments": self.unknown_surface_segments,
            "ready": self.ready,
        }


def audit_network(
    segments: Sequence[RoadSegment],
    profile_key: str = "adult",
) -> NetworkAuditReport:
    """Audit topology and modal accessibility without calculating a route."""
    profile = get_profile(profile_key)
    engine = RoutingEngine3D()
    engine.build_graph(segments)

    findings: List[Dict[str, Any]] = []
    accessible_segments: List[RoadSegment] = []
    allowed = 0
    blocked = 0
    unknown_surface = 0
    for index, segment in enumerate(segments):
        metadata = {
            "highway": segment.highway_type,
            "access": segment.access,
            "foot": segment.foot,
            "bicycle": segment.bicycle,
            "motor_vehicle": segment.motor_vehicle,
            "lit": segment.lit,
        }
        decision = evaluate_edge_access(profile, metadata)
        if decision.allowed:
            allowed += 1
            accessible_segments.append(segment)
        else:
            blocked += 1
        if not str(segment.surface or "").strip():
            unknown_surface += 1

        node = engine.coord_to_node.get(
            (round(float(segment.p1[0]), 5), round(float(segment.p1[1]), 5))
        )
        findings.append(
            {
                "segment_id": index + 1,
                "allowed": decision.allowed,
                "reason": decision.reason,
                "penalty": decision.penalty,
                "surface_quality": surface_quality(segment.surface),
                "component": engine.component_by_node.get(node, -1),
            }
        )

    graph = engine.graph_diagnostics
    accessible_engine = RoutingEngine3D()
    accessible_engine.build_graph(accessible_segments)
    accessible_graph = accessible_engine.graph_diagnostics
    node_count = int(graph.get("node_count", 0))
    largest = int(accessible_graph.get("largest_component_nodes", 0))
    largest_pct = (100.0 * largest / node_count) if node_count else 0.0
    return NetworkAuditReport(
        profile_key=profile_key,
        segment_count=len(segments),
        node_count=node_count,
        directed_edge_count=int(graph.get("directed_edge_count", 0)),
        component_count=int(accessible_graph.get("component_count", 0)),
        largest_component_pct=largest_pct,
        allowed_segments=allowed,
        blocked_segments=blocked,
        one_way_segments=sum(1 for segment in segments if segment.is_oneway),
        access_tagged_segments=sum(
            1
            for segment in segments
            if any((segment.access, segment.foot, segment.bicycle, segment.motor_vehicle))
        ),
        unknown_surface_segments=unknown_surface,
        findings=findings,
    )
