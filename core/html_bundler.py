"""Standalone single-file 3D WebGL HTML report generator."""
from __future__ import annotations

import json
import math
from html import escape
from pathlib import Path
from typing import Any, Dict, Optional, Union

from .js_bundle import JsBundleError, module_load_order, module_to_classic, module_to_namespace
from .routing_engine import RouteResult3D


class StandaloneHtmlBundler:
    """Bundles 3D WebGL engine, Three.js, CSS, and 3D route into a single self-contained HTML file."""

    def __init__(self, web_dir: Optional[Path] = None) -> None:
        self.web_dir = Path(web_dir) if web_dir else Path(__file__).resolve().parent.parent / "web"

    def bundle_to_file(self, data: Union[RouteResult3D, Dict[str, Any]], output_path: Path) -> None:
        """Generate standalone HTML document from RouteResult3D or GeoJSON dict."""
        self.export_standalone_html(data, output_path)

    @staticmethod
    def _finite_number(value: Any, default: float = 0.0) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        """Convert route payload values into strict JSON-safe primitives."""
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, dict):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        return value

    def export_standalone_html(self, result_or_geojson: Union[RouteResult3D, Dict[str, Any]], output_path: Path) -> None:
        """Generate standalone HTML document with embedded data and JavaScript."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(result_or_geojson, RouteResult3D):
            geojson_data = result_or_geojson.to_geojson_feature()
            profile_name = str(result_or_geojson.profile.name)
            dist_km = result_or_geojson.statistics.total_distance_km
            dur_min = result_or_geojson.statistics.total_duration_min
            climb_m = result_or_geojson.statistics.elevation_gain_m
            slope_pct = result_or_geojson.statistics.max_slope_pct
        else:
            geojson_data = result_or_geojson if isinstance(result_or_geojson, dict) else {}
            props = geojson_data.get("properties", {})
            if geojson_data.get("type") == "FeatureCollection":
                features = geojson_data.get("features", [])
                if features and isinstance(features[0], dict):
                    props = features[0].get("properties", {})
            profile_name = str(props.get("profile_name", "3D Route"))
            dist_km = props.get("distance_km", 0.0)
            dur_min = props.get("duration_min", 0.0)
            climb_m = props.get("elevation_gain_m", 0.0)
            slope_pct = props.get("max_slope_pct", 0.0)

        dist_val = self._finite_number(dist_km)
        dur_val = self._finite_number(dur_min)
        climb_val = self._finite_number(climb_m)
        slope_val = self._finite_number(slope_pct)

        json_str = json.dumps(self._json_safe(geojson_data), allow_nan=False)
        json_str = json_str.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        safe_profile_name = escape(profile_name, quote=True)

        # Read local CSS & JS
        css_content = ""
        css_file = self.web_dir / "css" / "studio3d.css"
        if css_file.exists():
            css_content = css_file.read_text(encoding="utf-8")

        # Three.js and OrbitControls are ES modules; rewrite them so they run in a
        # classic <script>. A missing file is fatal: an export that silently omits
        # the 3D engine produces a blank page reported as success.
        three_file = self.web_dir / "js" / "three.module.js"
        if not three_file.exists():
            raise JsBundleError(f"missing bundled asset: {three_file}")
        three_js = module_to_namespace(three_file.read_text(encoding="utf-8"), "THREE")

        controls_file = self.web_dir / "js" / "OrbitControls.js"
        if not controls_file.exists():
            raise JsBundleError(f"missing bundled asset: {controls_file}")
        controls_js = module_to_classic(
            controls_file.read_text(encoding="utf-8"),
            preamble=(
                "const { EventDispatcher, MOUSE, Quaternion, Spherical, TOUCH, "
                "Vector2, Vector3, Plane, Ray, MathUtils } = THREE;" + chr(10)
            ),
        )

        # First-party viewer modules: same treatment, in import order (each
        # after the modules it imports), found by following app3d.js.
        viewer_js = "\n".join(
            module_to_classic((self.web_dir / "js" / name).read_text(encoding="utf-8"))
            for name in module_load_order(self.web_dir / "js", "app3d.js")
        )

        html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>02Route 3D Studio — Standalone 3D Route Report ({safe_profile_name})</title>
  <style>
{css_content}
  </style>
</head>
<body>
  <div id="canvasContainer" tabindex="0" role="application"
       aria-label="3D route view. Drag to orbit, scroll to zoom. Space plays or pauses, arrow keys move along the route, 1 to 4 choose the camera."></div>

  <div class="hud-panel top-hud">
    <div class="brand-section">
      <span class="brand-badge">OFFLINE 3D</span>
      <span class="brand-title">02Route 3D Studio — {safe_profile_name}</span>
      <span class="route-pill" id="routeProfilePill">{safe_profile_name}</span>
      <div class="route-visibility-bar" id="routeVisibilityBar"></div>
    </div>

    <div class="stats-row" id="statsRow">
      <div class="stat-item">
        <span class="stat-label">Distance</span>
        <span class="stat-value" id="statDist">{dist_val:.2f} km</span>
      </div>
      <div class="stat-item">
        <span class="stat-label">Duration</span>
        <span class="stat-value" id="statDur">{dur_val:.1f} min</span>
      </div>
      <div class="stat-item">
        <span class="stat-label">Climb</span>
        <span class="stat-value" id="statClimb">+{climb_val:.1f} m</span>
      </div>
      <div class="stat-item">
        <span class="stat-label">Max Slope</span>
        <span class="stat-value" id="statSlope">{slope_val:.1f}%</span>
      </div>
    </div>

    <div class="camera-modes" role="group" aria-label="Camera mode">
      <button class="hud-btn active" data-cam="orbit" title="Free Orbit Camera (1)" aria-pressed="true">Orbit</button>
      <button class="hud-btn" data-cam="chase" title="Chase Camera (2)" aria-pressed="false">Chase</button>
      <button class="hud-btn" data-cam="pov" title="First-Person View (3)" aria-pressed="false">POV</button>
      <button class="hud-btn" data-cam="tour" title="Cinematic Drone Tour (4)" aria-pressed="false">Drone</button>
    </div>

    <div class="top-tools">
      <div class="sun-control">
        <label class="sun-label" for="sunTime">Sun</label>
        <input type="range" id="sunTime" min="6" max="20" step="0.25" value="14.5">
        <output class="sun-label" id="sunTimeLabel" for="sunTime">14:30</output>
      </div>
      <div class="sun-control">
        <label class="sun-label" for="zExag">Z-Exag</label>
        <input type="range" id="zExag" min="1" max="5" step="0.2" value="1.5">
        <output class="sun-label" id="zExagLabel" for="zExag">1.5x</output>
      </div>
      <button class="hud-btn" id="btnBasemap" title="Toggle Satellite / Street Basemap">🗺️ Map</button>
      <button class="hud-btn" id="btnRibbonMode" title="Cycle 3D Route Ribbon Coloring (Profile / Slope % / Speed)" aria-pressed="false">🎨 Ribbon: Profile</button>
      <button class="hud-btn" id="btnVoice" title="Toggle Voice Navigation Guidance" aria-pressed="false">🔊 Voice</button>
      <button class="hud-btn" id="btnSlicer" title="Toggle 3D Terrain Cross-Section Slicer" aria-pressed="false">🔪 Slicer</button>
      <div class="sun-control is-hidden" id="slicerControls">
        <label class="sun-label" for="slicerHeight">Cut Z</label>
        <input type="range" id="slicerHeight" min="0" max="100" step="1" value="65">
      </div>
      <button class="hud-btn" id="btnTheme" title="Toggle Light/Dark Studio Theme" aria-pressed="false">☀️ Light</button>
      <button class="hud-btn accent" id="btnRecord" title="Record WebM Video">⏺ Record</button>
      <button class="hud-btn" id="btnShot" title="Save High-Res PNG">📷 PNG</button>
    </div>
  </div>

  <!-- Live Turn-by-Turn Navigation Cue Banner -->
  <div id="liveCueBanner" class="live-cue-banner is-hidden" role="status" aria-live="polite">
    <span class="live-cue-icon" id="liveCueIcon" aria-hidden="true">🧭</span>
    <div class="live-cue-body">
      <div class="live-cue-instruction" id="liveCueInstruction">Follow route</div>
      <div class="live-cue-meta" id="liveCueMeta">0 m · 0.0%</div>
    </div>
  </div>

  <!-- Right-side 2D Radar Mini-Map & Compass -->
  <div class="radar-panel" id="radarPanel">
    <div class="radar-header">
      <span>2D RADAR TRACK</span>
      <span class="compass-rose" id="compassRose">🧭 N</span>
    </div>
    <canvas id="radarCanvas" width="180" height="180"></canvas>
  </div>

  <!-- Bottom Player & Elevation Profile -->
  <div class="hud-panel bottom-controls">
    <div class="elevation-chart-container">
      <div class="chart-header">
        <span class="chart-title">3D ELEVATION &amp; KINEMATIC PROFILE</span>
        <span class="chart-cursor-info" id="chartCursorInfo">Hover or scrub along route</span>
      </div>
      <div id="elevationChart" class="elevation-chart"></div>
    </div>

    <div class="multi-metric-panel" id="multiMetricPanel">
      <div class="multi-metric-header">
        <span class="chart-title">SYNCHRONIZED MULTI-METRIC RIBBONS</span>
        <span class="chart-cursor-info" id="metricLiveReadout">Slope: 0.0% · Speed: 5.0 km/h</span>
      </div>
      <div class="multi-metric-rows" id="multiMetricRows"></div>
    </div>

    <div class="player-scrubber-row">
      <button class="btn-play" id="btnPlay" title="Play/Pause (Space)" aria-label="Play" aria-pressed="false">
        <span id="playIcon" aria-hidden="true">▶</span>
      </button>

      <div class="scrubber-container">
        <input type="range" class="scrubber-slider" id="scrubber" min="0" max="1000" value="0"
               aria-label="Position along the route">
      </div>

      <div class="speed-pills" role="group" aria-label="Playback speed">
        <button class="speed-pill" data-speed="0.5" aria-pressed="false">0.5x</button>
        <button class="speed-pill active" data-speed="1" aria-pressed="true">1x</button>
        <button class="speed-pill" data-speed="2" aria-pressed="false">2x</button>
        <button class="speed-pill" data-speed="5" aria-pressed="false">5x</button>
        <button class="speed-pill" data-speed="10" aria-pressed="false">10x</button>
        <button class="speed-pill active" id="btnLoop" title="Loop Playback at Route End" aria-pressed="true">🔁</button>
      </div>
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
{viewer_js}

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

        temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        temp_path.write_text(html_template, encoding="utf-8")
        temp_path.replace(output_path)
