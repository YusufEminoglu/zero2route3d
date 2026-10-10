"""Turn costs: the time a mode loses turning at a junction.

A node-based graph charges nothing for turning, so a car route happily makes
three left turns across traffic to save 20 m, and a truck takes a 160 degree
hairpin. The routing engine searches over (node, previous node) states when a
profile has a turn model, and charges the delay below at every junction.

Angles are measured in a local metre plane: 0 straight on, positive to the
right, negative to the left, +-180 a U-turn. With traffic on the right, a left
turn crosses oncoming traffic and costs more; ``drive_on_left`` mirrors that.
Pure Python, no QGIS.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

from .mobility_profiles import MobilityProfile

# A change of direction smaller than this is "straight on" (road geometry).
STRAIGHT_DEG = 30.0
SHARP_DEG = 120.0
UTURN_DEG = 165.0


@dataclass(frozen=True)
class TurnModel:
    """Seconds lost per manoeuvre; math.inf forbids it."""

    left_s: float
    right_s: float
    sharp_extra_s: float
    uturn_s: float
    dead_end_uturn_s: float


# Delays are typical signal-free junction values; the point is the ordering
# (left over right, U-turns rare), not traffic-model precision.
_MODELS = {
    "car": TurnModel(left_s=6.0, right_s=2.0, sharp_extra_s=4.0, uturn_s=math.inf, dead_end_uturn_s=30.0),
    "paramedic": TurnModel(left_s=4.0, right_s=1.5, sharp_extra_s=3.0, uturn_s=20.0, dead_end_uturn_s=15.0),
    "delivery_van": TurnModel(left_s=8.0, right_s=3.0, sharp_extra_s=8.0, uturn_s=math.inf, dead_end_uturn_s=45.0),
    # Heavy goods vehicles cannot take hairpins at junctions at all.
    "truck": TurnModel(left_s=12.0, right_s=6.0, sharp_extra_s=math.inf, uturn_s=math.inf, dead_end_uturn_s=90.0),
    "bicycle": TurnModel(left_s=3.0, right_s=1.0, sharp_extra_s=2.0, uturn_s=10.0, dead_end_uturn_s=6.0),
    "mtb": TurnModel(left_s=3.0, right_s=1.0, sharp_extra_s=2.0, uturn_s=10.0, dead_end_uturn_s=6.0),
    "scooter": TurnModel(left_s=3.0, right_s=1.0, sharp_extra_s=2.0, uturn_s=10.0, dead_end_uturn_s=6.0),
    # Manoeuvring a wheelchair or a stroller through a sharp corner or a
    # turnaround takes time; ordinary turns do not.
    "wheelchair": TurnModel(left_s=0.0, right_s=0.0, sharp_extra_s=3.0, uturn_s=6.0, dead_end_uturn_s=6.0),
    "stroller": TurnModel(left_s=0.0, right_s=0.0, sharp_extra_s=2.0, uturn_s=4.0, dead_end_uturn_s=4.0),
}


def turn_model_for(profile: MobilityProfile) -> Optional[TurnModel]:
    """The profile's turn model, or None when turning costs it nothing."""
    model = _MODELS.get(profile.key)
    if model is not None:
        return model
    if profile.category == "vehicle":
        return _MODELS["car"]
    if profile.category == "micromobility":
        return _MODELS["bicycle"]
    return None


def turn_angle_deg(a: Sequence[float], b: Sequence[float], c: Sequence[float]) -> float:
    """Signed change of direction at b when going a -> b -> c (positive: right)."""
    mid_lat = math.radians(float(b[1]))
    kx = math.cos(mid_lat)
    x1, y1 = (b[0] - a[0]) * kx, b[1] - a[1]
    x2, y2 = (c[0] - b[0]) * kx, c[1] - b[1]
    if (x1 == 0 and y1 == 0) or (x2 == 0 and y2 == 0):
        return 0.0
    # Bearings clockwise from north; the difference is the turn.
    h1 = math.atan2(x1, y1)
    h2 = math.atan2(x2, y2)
    diff = math.degrees(h2 - h1)
    while diff > 180.0:
        diff -= 360.0
    while diff < -180.0:
        diff += 360.0
    return diff


def turn_delay_s(
    model: TurnModel,
    angle_deg: float,
    junction: bool,
    dead_end: bool,
    drive_on_left: bool = False,
) -> float:
    """Seconds lost for one manoeuvre (math.inf when it is not allowed)."""
    magnitude = abs(angle_deg)
    if magnitude >= UTURN_DEG:
        return model.dead_end_uturn_s if dead_end else model.uturn_s
    if not junction or magnitude < STRAIGHT_DEG:
        return 0.0  # a bend in the road, or straight across
    crosses_traffic = angle_deg > 0 if drive_on_left else angle_deg < 0
    delay = model.left_s if crosses_traffic else model.right_s
    if magnitude >= SHARP_DEG:
        delay += model.sharp_extra_s
    return delay
