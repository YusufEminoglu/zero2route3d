"""QGIS QML Layer Style Generator for 3D Route and Elevation Symbology."""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Union


def generate_route_qml_style(
    target_or_profile: Optional[Union[Path, str]] = None,
    layer_title: str = "3D Route",
    line_width_mm: float = 1.2,
    line_color_hex: str = "#0ea5e9",
) -> str:
    """Generate a standard QGIS 3.x/4.x compatible QML style XML string and optionally save to file."""
    # Profile-specific color mapping
    colors = {
        "adult": "14,165,233,255",
        "child": "245,158,11,255",
        "senior": "16,185,129,255",
        "wheelchair": "139,92,246,255",
        "commuter": "6,182,212,255",
        "road_bike": "59,130,246,255",
        "ebike": "168,85,247,255",
        "cargo_bike": "234,88,12,255",
        "scooter": "20,184,166,255",
        "car": "239,68,68,255",
        "paramedic": "225,29,72,255",
    }
    
    prof_key = "adult"
    target_file: Optional[Path] = None

    if isinstance(target_or_profile, Path):
        target_file = target_or_profile
    elif isinstance(target_or_profile, str):
        if target_or_profile.endswith(".qml"):
            target_file = Path(target_or_profile)
        else:
            prof_key = target_or_profile.lower()

    core_color = colors.get(prof_key, "14,165,233,255")
    glow_width = f"{line_width_mm * 2.0:.1f}"
    core_width = f"{line_width_mm:.1f}"

    qml_content = f"""<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34.0" styleCategories="AllStyleCategories">
  <renderer-v2 type="singleSymbol" enableorderby="0" forceraster="0" referencescale="-1">
    <symbols>
      <symbol type="line" name="0" alpha="1" clip_to_extent="1" force_rhr="0">
        <data_defined_properties>
          <Option type="Map">
            <Option type="QString" name="name" value=""/>
            <Option name="properties"/>
            <Option type="QString" name="type" value="collection"/>
          </Option>
        </data_defined_properties>
        <!-- Outer Glow Layer -->
        <layer enabled="1" class="SimpleLine" locked="0" pass="0">
          <Option type="Map">
            <Option type="QString" name="line_color" value="{core_color.replace('255', '90')}"/>
            <Option type="QString" name="line_style" value="solid"/>
            <Option type="QString" name="line_width" value="{glow_width}"/>
            <Option type="QString" name="line_width_unit" value="MM"/>
            <Option type="QString" name="capstyle" value="round"/>
            <Option type="QString" name="joinstyle" value="round"/>
          </Option>
        </layer>
        <!-- Inner Core Neon Line -->
        <layer enabled="1" class="SimpleLine" locked="0" pass="1">
          <Option type="Map">
            <Option type="QString" name="line_color" value="{core_color}"/>
            <Option type="QString" name="line_style" value="solid"/>
            <Option type="QString" name="line_width" value="{core_width}"/>
            <Option type="QString" name="line_width_unit" value="MM"/>
            <Option type="QString" name="capstyle" value="round"/>
            <Option type="QString" name="joinstyle" value="round"/>
          </Option>
        </layer>
      </symbol>
    </symbols>
  </renderer-v2>
  <blendMode>0</blendMode>
  <featureBlendMode>0</featureBlendMode>
</qgis>"""

    if target_file is not None:
        target_file.write_text(qml_content, encoding="utf-8")

    return qml_content
