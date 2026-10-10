"""Route scenarios as JSON: the inputs of a run plus a summary of its results.

A scenario stores what is needed to repeat a calculation (origin and
destination, profile scope, MCDA weights, layer choices) and the headline
numbers of the routes it produced, so a later run can be compared with it.
Pure Python: no QGIS imports, so it is unit-tested in CI.
"""
from __future__ import annotations

import datetime
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

SCENARIO_FORMAT = "zero2route3d-scenario"
SCENARIO_VERSION = 1

# Metrics stored per profile and shown in the comparison, with display units.
SUMMARY_METRICS = (
    ("distance_km", "Distance", "km"),
    ("duration_min", "Duration", "min"),
    ("climb_m", "Climb", "m"),
    ("max_slope_pct", "Max slope", "%"),
    ("calories_kcal", "Calories", "kcal"),
)


class ScenarioError(ValueError):
    """The file is not a readable 02Route 3D scenario."""


def summarize_statistics(profile_name: str, stats: Any) -> Dict[str, Any]:
    """Headline numbers of one route (a RouteStatistics-like object)."""
    return {
        "profile": profile_name,
        "distance_km": float(stats.total_distance_km),
        "duration_min": float(stats.total_duration_min),
        "climb_m": float(stats.elevation_gain_m),
        "max_slope_pct": float(stats.max_slope_pct),
        "calories_kcal": float(stats.total_calories_kcal),
    }


def build_scenario(
    inputs: Mapping[str, Any],
    points: Mapping[str, Optional[Mapping[str, Any]]],
    results: Optional[Mapping[str, Mapping[str, Any]]] = None,
    layers: Optional[Mapping[str, str]] = None,
    name: str = "",
) -> Dict[str, Any]:
    """Assemble a scenario dictionary ready for :func:`save_scenario`."""
    return {
        "format": SCENARIO_FORMAT,
        "version": SCENARIO_VERSION,
        "name": str(name or ""),
        "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "inputs": dict(inputs),
        "layers": dict(layers or {}),
        "points": {key: (dict(value) if value else None) for key, value in points.items()},
        "results": {key: dict(value) for key, value in (results or {}).items()},
    }


def _point(value: Any, label: str) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ScenarioError(f"Point {label} is not an object.")
    try:
        lon = float(value["lon"])
        lat = float(value["lat"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ScenarioError(f"Point {label} needs numeric 'lon' and 'lat'.") from exc
    if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
        raise ScenarioError(f"Point {label} is outside WGS84 longitude/latitude bounds.")
    return {"lon": lon, "lat": lat, "name": str(value.get("name") or label)}


def parse_scenario(data: Any) -> Dict[str, Any]:
    """Validate a loaded scenario and return it in normalized form."""
    if not isinstance(data, Mapping) or data.get("format") != SCENARIO_FORMAT:
        raise ScenarioError("This file is not a 02Route 3D scenario.")
    try:
        version = int(data.get("version", 0))
    except (TypeError, ValueError) as exc:
        raise ScenarioError("The scenario version is not a number.") from exc
    if version < 1 or version > SCENARIO_VERSION:
        raise ScenarioError(
            f"Scenario version {version} is not supported; this plugin reads up to version {SCENARIO_VERSION}."
        )
    inputs = data.get("inputs") or {}
    layers = data.get("layers") or {}
    points = data.get("points") or {}
    results = data.get("results") or {}
    if not all(isinstance(part, Mapping) for part in (inputs, layers, points, results)):
        raise ScenarioError("Scenario sections must be JSON objects.")

    clean_results: Dict[str, Dict[str, Any]] = {}
    for key, summary in results.items():
        if not isinstance(summary, Mapping):
            continue
        row: Dict[str, Any] = {"profile": str(summary.get("profile") or key)}
        for metric, _label, _unit in SUMMARY_METRICS:
            try:
                value = float(summary.get(metric))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                row[metric] = value
        clean_results[str(key)] = row

    return {
        "format": SCENARIO_FORMAT,
        "version": version,
        "name": str(data.get("name") or ""),
        "saved_at": str(data.get("saved_at") or ""),
        "inputs": dict(inputs),
        "layers": {str(k): str(v or "") for k, v in layers.items()},
        "points": {"A": _point(points.get("A"), "A"), "B": _point(points.get("B"), "B")},
        "results": clean_results,
    }


def save_scenario(path: Path, scenario: Mapping[str, Any]) -> None:
    Path(path).write_text(json.dumps(scenario, indent=2, ensure_ascii=False), encoding="utf-8")


def load_scenario(path: Path) -> Dict[str, Any]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ScenarioError(f"Could not read the scenario file: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ScenarioError(f"The scenario file is not valid JSON: {exc}") from exc
    return parse_scenario(data)


def compare_runs(
    saved: Mapping[str, Mapping[str, Any]],
    current: Mapping[str, Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """One row per profile and metric: saved value, current value and change.

    Profiles present in only one run still get rows (the missing side is
    None), so a comparison never silently drops a route.
    """
    rows: List[Dict[str, Any]] = []
    keys = list(saved.keys()) + [key for key in current.keys() if key not in saved]
    for key in keys:
        before = saved.get(key) or {}
        after = current.get(key) or {}
        profile = str(after.get("profile") or before.get("profile") or key)
        for metric, label, unit in SUMMARY_METRICS:
            old = before.get(metric)
            new = after.get(metric)
            if old is None and new is None:
                continue
            delta = None if old is None or new is None else float(new) - float(old)
            percent = None
            if delta is not None and abs(float(old)) > 1e-9:
                percent = 100.0 * delta / abs(float(old))
            rows.append(
                {
                    "profile_key": key,
                    "profile": profile,
                    "metric": metric,
                    "label": label,
                    "unit": unit,
                    "saved": old,
                    "current": new,
                    "delta": delta,
                    "delta_pct": percent,
                }
            )
    return rows
