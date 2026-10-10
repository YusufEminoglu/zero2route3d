"""Read and restore the dock's inputs: QGIS settings and scenario files share this.

Widget values (profile scope, weights, playback) are user preferences and go
to QgsSettings. Layer choices are layer ids, which only mean something inside
one project, so they are stored in the project file instead.
"""
from __future__ import annotations

import contextlib
import json
from typing import Any, Dict

from qgis.core import QgsProject, QgsSettings

SETTINGS_KEY = "zero2route3d/dock/inputs"
PROJECT_SCOPE = "zero2route3d"
PROJECT_LAYERS_KEY = "dock_layers"

# Order matters: a scope combo resets its profile combo when it changes, so
# scopes are applied before profiles.
COMBOS = (
    "cmb_quick_mode_scope",
    "cmb_mode_scope",
    "cmb_quick_profile",
    "cmb_profile",
    "cmb_carto_theme",
    "cmb_quick_speed",
    "cmb_anim_speed",
)
SLIDERS = ("sld_slope", "sld_heat", "sld_green")
CHECKS = ("chk_quick_autopan", "chk_anim_autopan")
LAYER_COMBOS = (
    "cmb_route_road_layer",
    "cmb_route_building_layer",
    "cmb_dem_layer",
    "cmb_lst_layer",
    "cmb_green_layer",
)


def collect_inputs(dock: Any) -> Dict[str, Any]:
    """Current widget values as plain JSON-ready data."""
    combos: Dict[str, Any] = {}
    for name in COMBOS:
        combo = getattr(dock, name, None)
        if combo is None:
            continue
        data = combo.currentData()
        combos[name] = {"data": data if isinstance(data, (str, int, float)) else None, "text": combo.currentText()}
    sliders = {name: getattr(dock, name).value() for name in SLIDERS if getattr(dock, name, None) is not None}
    checks = {name: getattr(dock, name).isChecked() for name in CHECKS if getattr(dock, name, None) is not None}
    tabs = getattr(dock, "tab_widget", None)
    return {
        "combos": combos,
        "sliders": sliders,
        "checks": checks,
        "tab": tabs.currentIndex() if tabs is not None else 0,
    }


def apply_inputs(dock: Any, inputs: Dict[str, Any]) -> None:
    """Set widgets from :func:`collect_inputs` output; unknown values are skipped."""
    if not isinstance(inputs, dict):
        return
    combos = inputs.get("combos") or {}
    for name in COMBOS:
        combo = getattr(dock, name, None)
        saved = combos.get(name)
        if combo is None or not isinstance(saved, dict):
            continue
        index = -1
        if saved.get("data") is not None:
            index = combo.findData(saved["data"])
        if index < 0 and saved.get("text"):
            index = combo.findText(str(saved["text"]))
        if index >= 0:
            combo.setCurrentIndex(index)
    for name, value in (inputs.get("sliders") or {}).items():
        slider = getattr(dock, name, None) if name in SLIDERS else None
        if slider is not None:
            with contextlib.suppress(TypeError, ValueError):
                slider.setValue(int(value))
    for name, value in (inputs.get("checks") or {}).items():
        check = getattr(dock, name, None) if name in CHECKS else None
        if check is not None:
            check.setChecked(bool(value))
    tabs = getattr(dock, "tab_widget", None)
    with contextlib.suppress(TypeError, ValueError):
        tab = int(inputs.get("tab", -1))
        if tabs is not None and 0 <= tab < tabs.count():
            tabs.setCurrentIndex(tab)


def collect_layers(dock: Any) -> Dict[str, str]:
    """Layer id per layer combo ("" when nothing is selected)."""
    layers: Dict[str, str] = {}
    for name in LAYER_COMBOS:
        combo = getattr(dock, name, None)
        if combo is None:
            continue
        layer = combo.currentLayer()
        layers[name] = layer.id() if layer is not None else ""
    return layers


def apply_layers(dock: Any, layers: Dict[str, str]) -> None:
    """Select saved layers that still exist in the project."""
    if not isinstance(layers, dict):
        return
    project = QgsProject.instance()
    for name in LAYER_COMBOS:
        combo = getattr(dock, name, None)
        layer_id = layers.get(name)
        if combo is None or layer_id is None:
            continue
        if layer_id:
            layer = project.mapLayer(str(layer_id))
            if layer is not None:
                combo.setLayer(layer)
        elif combo.allowEmptyLayer():
            combo.setLayer(None)


def save_settings(dock: Any) -> None:
    """Remember the dock's inputs for the next session and project."""
    with contextlib.suppress(Exception):
        QgsSettings().setValue(SETTINGS_KEY, json.dumps(collect_inputs(dock)))
    with contextlib.suppress(Exception):
        QgsProject.instance().writeEntry(PROJECT_SCOPE, PROJECT_LAYERS_KEY, json.dumps(collect_layers(dock)))


def restore_settings(dock: Any) -> None:
    """Restore inputs saved by :func:`save_settings`; a bad entry is ignored."""
    with contextlib.suppress(Exception):
        raw = QgsSettings().value(SETTINGS_KEY, "")
        if raw:
            apply_inputs(dock, json.loads(str(raw)))
    restore_project_layers(dock)


def restore_project_layers(dock: Any) -> None:
    """Restore the layer choices stored in the current project."""
    with contextlib.suppress(Exception):
        raw, ok = QgsProject.instance().readEntry(PROJECT_SCOPE, PROJECT_LAYERS_KEY, "")
        if ok and raw:
            apply_layers(dock, json.loads(raw))
