"""Embedded WebGL Viewport and Python-to-JavaScript Bridge for 02Route 3D."""
from __future__ import annotations

import contextlib
import json
import os
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional

from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

# Safe QWebEngineView imports
HAS_WEBENGINE = False
QWebEngineView = None
QWebEngineSettings = None

try:
    from qgis.PyQt.QtWebEngineWidgets import QWebEngineView, QWebEngineSettings
    HAS_WEBENGINE = True
except Exception:
    with contextlib.suppress(Exception):
        from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineSettings
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
        if HAS_WEBENGINE and QWebEngineView is not None:
            try:
                self.web_view = QWebEngineView(self)
                settings = self.web_view.settings()
                if settings is not None:
                    attrs = [
                        "WebGLEnabled",
                        "Accelerated2dCanvasEnabled",
                        "LocalContentCanAccessRemoteUrls",
                        "LocalContentCanAccessFileUrls",
                        "JavascriptEnabled",
                    ]
                    for attr_name in attrs:
                        enum_target = getattr(QWebEngineSettings, "WebAttribute", QWebEngineSettings)
                        attr = getattr(enum_target, attr_name, None)
                        if attr is not None:
                            with contextlib.suppress(Exception):
                                settings.setAttribute(attr, True)

                self.layout.addWidget(self.web_view)
                self.reload_scene()
                return
            except Exception:
                self.web_view = None

        # Fallback container
        fb = QWidget(self)
        fb_layout = QVBoxLayout(fb)
        fb_layout.setContentsMargins(24, 24, 24, 24)
        fb_layout.setSpacing(16)

        lbl = QLabel(
            "<h3>🚀 3D WebGL Mobility Studio</h3>"
            "<p style='color:#94a3b8; font-size:12px;'>"
            "Direct hardware-accelerated WebGL 3D route rendering and kinematic animation."
            "</p>",
            fb,
        )
        lbl.setStyleSheet("color: #0f172a;")
        fb_layout.addWidget(lbl)

        btn_open = QPushButton("🌐 Open 3D Studio in Browser (Chrome/Edge)", fb)
        btn_open.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284c7, stop:1 #2563eb);
                color: #ffffff;
                font-weight: 700;
                font-size: 13px;
                padding: 12px 20px;
                border-radius: 8px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0369a1, stop:1 #1d4ed8);
            }
        """)
        btn_open.clicked.connect(self.open_in_external_browser)
        fb_layout.addWidget(btn_open)
        fb_layout.addStretch()

        self.layout.addWidget(fb)

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
