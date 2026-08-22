"""QGIS QML Layer Style Generator for 3D Route and Elevation Symbology."""
from __future__ import annotations

from pathlib import Path


def generate_route_qml_style(target_path: Path, layer_title: str = "3D Route") -> None:
    """Generate a standard QGIS 3.x/4.x compatible QML style file for 3D LineStringZ layers."""
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
            <Option type="QString" name="line_color" value="56,189,248,100"/>
            <Option type="QString" name="line_style" value="solid"/>
            <Option type="QString" name="line_width" value="2.2"/>
            <Option type="QString" name="line_width_unit" value="MM"/>
            <Option type="QString" name="capstyle" value="round"/>
            <Option type="QString" name="joinstyle" value="round"/>
          </Option>
        </layer>
        <!-- Inner Core Neon Line -->
        <layer enabled="1" class="SimpleLine" locked="0" pass="1">
          <Option type="Map">
            <Option type="QString" name="line_color" value="14,165,233,255"/>
            <Option type="QString" name="line_style" value="solid"/>
            <Option type="QString" name="line_width" value="1.0"/>
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

    target_path.write_text(qml_content, encoding="utf-8")
