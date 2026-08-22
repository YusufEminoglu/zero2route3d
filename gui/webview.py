"""Embedded WebGL Viewport and Python-to-JavaScript Bridge for 02Route 3D."""
from __future__ import annotations

import contextlib
import json
import os
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional

from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtWidgets import QLabel, QVBoxLayout, QWidget

# Safe QWebEngineView imports
HAS_WEBENGINE = False
with contextlib.suppress(ImportError):
    from qgis.PyQt.QtWebEngineWidgets import QWebEngineSettings, QWebEngineView
    HAS_WEBENGINE = True


class Studio3DWebViewport(QWidget):
    """Embedded hardware-accelerated 3D WebGL viewport running inside the QGIS dock."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.web_view: Optional[Any] = None
        self.html_path = Path(__file__).resolve().parent.parent / "web" / "index.html"
        self._current_geojson: Optional[Dict[str, Any]] = None

        self._setup_view()

    def _setup_view(self) -> None:
        if HAS_WEBENGINE:
            self.web_view = QWebEngineView(self)
            settings = self.web_view.settings()
            settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)

            self.layout.addWidget(self.web_view)
            self.reload_scene()
        else:
            lbl = QLabel("3D WebEngine is unavailable in this environment.\nUse 'Open in Browser' to view 3D route in Chrome.", self)
            lbl.setStyleSheet("color: #94a3b8; padding: 20px; font-weight: 600;")
            self.layout.addWidget(lbl)

    def reload_scene(self) -> None:
        """Reload the local 3D HTML application."""
        if self.web_view is not None and self.html_path.exists():
            url = QUrl.fromLocalFile(str(self.html_path.resolve()))
            self.web_view.load(url)

    def send_route(self, geojson_data: Dict[str, Any]) -> None:
        """Transmit computed route GeoJSON to the 3D WebGL canvas via JavaScript."""
        self._current_geojson = geojson_data
        if self.web_view is not None:
            json_str = json.dumps(geojson_data)
            script = f"if (window.setRouteData) {{ window.setRouteData({json_str}); }}"
            self.web_view.page().runJavaScript(script)

    def open_in_external_browser(self) -> None:
        """Open the 3D web studio in the user's default external browser (e.g. Chrome/Edge)."""
        if not self.html_path.exists():
            return

        if self._current_geojson:
            # Inject route payload into a temporary standalone HTML for instant offline browser viewing
            temp_html = Path(os.path.expanduser("~")) / ".qgis_zero2route3d_view.html"
            raw_html = self.html_path.read_text(encoding="utf-8")
            injected_script = f"""
            <script>
            window.addEventListener('load', () => {{
              setTimeout(() => {{
                if (window.setRouteData) window.setRouteData({json.dumps(self._current_geojson)});
              }}, 300);
            }});
            </script>
            """
            merged = raw_html.replace("</body>", f"{injected_script}\n</body>")
            temp_html.write_text(merged, encoding="utf-8")
            webbrowser.open(temp_html.as_uri())
        else:
            webbrowser.open(self.html_path.as_uri())
