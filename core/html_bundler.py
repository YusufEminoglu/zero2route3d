"""Standalone single-file 3D WebGL HTML report generator."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from .routing_engine import RouteResult3D


class StandaloneHtmlBundler:
    """Bundles 3D WebGL engine, Three.js, CSS, and 3D route into a single self-contained HTML file."""

    def __init__(self, web_dir: Path) -> None:
        self.web_dir = web_dir

    def export_standalone_html(self, result: RouteResult3D, output_path: Path) -> None:
        """Generate standalone HTML document with embedded data and JavaScript."""
        geojson_data = result.to_geojson_feature()
        json_str = json.dumps(geojson_data)

        # Read local CSS & JS
        css_content = ""
        css_file = self.web_dir / "css" / "studio3d.css"
        if css_file.exists():
            css_content = css_file.read_text(encoding="utf-8")

        three_js = ""
        three_file = self.web_dir / "js" / "three.module.js"
        if three_file.exists():
            three_js = three_file.read_text(encoding="utf-8")

        controls_js = ""
        controls_file = self.web_dir / "js" / "OrbitControls.js"
        if controls_file.exists():
            controls_js = controls_file.read_text(encoding="utf-8")

        app_js = ""
        app_file = self.web_dir / "js" / "app3d.js"
        if app_file.exists():
            app_js = app_file.read_text(encoding="utf-8")

        # Strip module imports from app_js for standalone script
        app_js_clean = app_js.replace("import * as THREE from './three.module.js';", "")
        app_js_clean = app_js_clean.replace("import { OrbitControls } from './OrbitControls.js';", "")

        html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>02Route 3D — Standalone 3D Route Report ({result.profile.name})</title>
  <style>
{css_content}
  </style>
</head>
<body>
  <div id="canvasContainer"></div>

  <div class="hud-panel top-hud">
    <div class="brand-section">
      <span class="brand-badge">OFFLINE 3D</span>
      <span class="brand-title">02Route 3D — {result.profile.name}</span>
    </div>
    <div class="stats-row">
      <div class="stat-item"><span class="stat-label">Distance</span><span class="stat-value">{result.statistics.total_distance_km} km</span></div>
      <div class="stat-item"><span class="stat-label">Duration</span><span class="stat-value">{result.statistics.total_duration_min} min</span></div>
      <div class="stat-item"><span class="stat-label">Climb</span><span class="stat-value">+{result.statistics.elevation_gain_m:.1f} m</span></div>
      <div class="stat-item"><span class="stat-label">Max Slope</span><span class="stat-value">{result.statistics.max_slope_pct:.1f}%</span></div>
    </div>
  </div>

  <div class="hud-panel bottom-player">
    <div class="player-controls">
      <div class="play-btn-group">
        <button id="btnPlay" class="btn-icon"><span id="playIcon">▶</span></button>
      </div>
      <div class="timeline-scrubber">
        <input type="range" id="scrubber" class="scrubber-slider" min="0" max="1000" value="0">
      </div>
    </div>
    <div class="chart-drawer" id="chartDrawer">
      <svg id="profileSvg" preserveAspectRatio="none"></svg>
      <div class="profile-needle" id="profileNeedle"></div>
    </div>
  </div>

  <!-- Embedded Three.js & Controls -->
  <script type="text/javascript">
{three_js}
  </script>
  <script type="text/javascript">
{controls_js}
  </script>
  <script type="text/javascript">
{app_js_clean}

  // Auto-load route data on startup
  window.addEventListener('DOMContentLoaded', () => {{
    const data = {json_str};
    if (window.setRouteData) {{
      window.setRouteData(data);
    }}
  }});
  </script>
</body>
</html>"""

        output_path.write_text(html_template, encoding="utf-8")
