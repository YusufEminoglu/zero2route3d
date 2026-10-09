"""Profile-aware road access policy and surface interpretation.

Routing cost alone cannot make an illegal or physically incompatible road safe.
This module separates hard modal access decisions from soft impedance weights so
cars cannot use footways, pedestrians cannot use motorways, and explicit OSM
access tags always win over inferred defaults.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .mobility_profiles import MobilityProfile


_DENIED = {"no", "private", "customers"}
_ALLOWED = {"yes", "designated", "permissive", "official"}
_PEDESTRIAN_ONLY = {"footway", "pedestrian", "steps", "corridor"}
_NON_MOTORISED = _PEDESTRIAN_ONLY | {"path", "cycleway", "bridleway"}
_CONTROLLED_ACCESS = {"motorway", "motorway_link", "trunk", "trunk_link"}

_SURFACE_QUALITY = {
    "paved": 0.90,
    "asphalt": 1.00,
    "concrete": 0.95,
    "concrete:plates": 0.85,
    "paving_stones": 0.82,
    "sett": 0.68,
    "cobblestone": 0.55,
    "compacted": 0.62,
    "fine_gravel": 0.58,
    "gravel": 0.42,
    "pebblestone": 0.38,
    "ground": 0.30,
    "dirt": 0.22,
    "earth": 0.22,
    "mud": 0.08,
    "sand": 0.08,
    "grass": 0.18,
    "wood": 0.72,
    "metal": 0.70,
}


@dataclass(frozen=True)
class EdgeAccessDecision:
    """Result of evaluating one directed edge for one mobility profile."""

    allowed: bool
    reason: str = "allowed"
    penalty: float = 1.0


def normalize_tag(value: Any) -> str:
    """Return a stable lower-case OSM-style tag value."""
    if value is None:
        return ""
    return str(value).strip().lower()


def surface_quality(surface: Any) -> float:
    """Convert common OSM surface tags to a 0..1 smoothness score.

    Unknown but present surfaces receive a conservative middle score. Missing
    surface data remains neutral instead of being treated as known asphalt.
    """
    value = normalize_tag(surface)
    if not value:
        return 0.65
    return _SURFACE_QUALITY.get(value, 0.50)


def _mode(profile: MobilityProfile) -> str:
    if profile.category == "pedestrian":
        return "foot"
    if profile.category == "micromobility":
        return "bicycle"
    return "motor_vehicle"


def evaluate_edge_access(
    profile: MobilityProfile,
    metadata: Mapping[str, Any],
) -> EdgeAccessDecision:
    """Evaluate explicit tags and conservative highway defaults.

    Explicit modal permission overrides a highway-class default. General and
    modal prohibitions are hard blocks; soft preferences continue to belong to
    the profile's impedance model.
    """
    mode = _mode(profile)
    direction = _direction_decision(mode, metadata)
    if direction is not None:
        return direction
    highway = normalize_tag(metadata.get("highway")) or "unclassified"
    general = normalize_tag(metadata.get("access"))
    modal = normalize_tag(metadata.get(mode))

    if modal in _DENIED:
        return EdgeAccessDecision(False, f"{mode}={modal}")
    if not modal and general in _DENIED:
        return EdgeAccessDecision(False, f"access={general}")
    if modal in _ALLOWED:
        return _night_penalty(profile, metadata)

    if mode == "motor_vehicle" and highway in _NON_MOTORISED:
        return EdgeAccessDecision(False, f"{highway} excludes motor vehicles")
    if mode in {"foot", "bicycle"} and highway in _CONTROLLED_ACCESS:
        return EdgeAccessDecision(False, f"{highway} excludes {mode}")
    if mode == "bicycle" and highway in {"steps", "corridor"}:
        return EdgeAccessDecision(False, f"{highway} excludes bicycles")

    return _night_penalty(profile, metadata)


def _direction_decision(mode: str, metadata: Mapping[str, Any]):
    """Apply one-way rules per mode.

    Edges carry ``against_oneway`` when they run against a one-way street.
    Motor vehicles never use them; bicycles may when ``oneway:bicycle=no``;
    pedestrians may unless ``oneway:foot=yes``. ``oneway:<mode>=yes`` also
    makes a two-way street one-way for that mode (its reverse edge is then
    blocked). Returns None when direction does not decide the edge.
    """
    oneway = bool(metadata.get("oneway"))
    against = bool(metadata.get("against_oneway"))
    if mode == "motor_vehicle":
        if against:
            return EdgeAccessDecision(False, "against one-way traffic")
        return None
    modal_tag = normalize_tag(metadata.get("oneway_bicycle" if mode == "bicycle" else "oneway_foot"))
    if against:
        if mode == "bicycle" and modal_tag != "no":
            return EdgeAccessDecision(False, "against one-way traffic")
        if mode == "foot" and modal_tag in {"yes", "1", "true"}:
            return EdgeAccessDecision(False, "oneway:foot=yes")
        return None
    if not oneway and modal_tag in {"yes", "1", "true"} and metadata.get("reverse_of_two_way"):
        return EdgeAccessDecision(False, f"oneway:{'bicycle' if mode == 'bicycle' else 'foot'}=yes")
    return None


def _night_penalty(
    profile: MobilityProfile,
    metadata: Mapping[str, Any],
) -> EdgeAccessDecision:
    """Prefer illuminated links for the dedicated night-walk profile."""
    if profile.key == "night_walk" and normalize_tag(metadata.get("lit")) == "no":
        return EdgeAccessDecision(True, "unlit night-walk link", 2.0)
    return EdgeAccessDecision(True)

