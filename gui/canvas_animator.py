"""Real-Time 2D QGIS Canvas Animation Controller for 02Route 3D.

Animates moving avatar markers (pedestrians, micromobility, vehicles) along their
computed 2D/3D routes on the native QGIS map canvas at real-time or accelerated speeds.
"""
from __future__ import annotations

import contextlib
import math
from pathlib import Path
from typing import List, Optional, Sequence

from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
)
from qgis.gui import QgsMapCanvas

from ..core.kinematics import AnimatedAvatar, haversine_distance_3d
from ..core.mobility_profiles import get_profile_color
from ..core.routing_engine import RouteResult3D


class Route2DCanvasAnimator(QObject):
    """QGIS Map Canvas 2D route animation controller."""

    frame_updated = pyqtSignal(float, float, float)  # (current_time_s, max_duration_s, progress_fraction)
    playback_state_changed = pyqtSignal(bool)  # is_playing
    animation_finished = pyqtSignal()

    def __init__(self, canvas: Optional[QgsMapCanvas] = None, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.canvas = canvas
        self.avatars: List[AnimatedAvatar] = []
        self.current_time_s: float = 0.0
        self.max_duration_s: float = 0.0
        self.speed_multiplier: float = 10.0  # 10x default speed for visual comfort
        self.is_playing: bool = False
        self.auto_pan: bool = False
        self.loop: bool = True
        self._coord_transform: Optional[QgsCoordinateTransform] = None
        self._transform_crs_id: str = ""
        self.avatar_layer: Optional[QgsVectorLayer] = None
        self._avatar_feature_ids: List[int] = []

        self.timer = QTimer(self)
        self.timer.setInterval(40)  # 25 FPS
        self.timer.timeout.connect(self._on_tick)

    def set_canvas(self, canvas: QgsMapCanvas) -> None:
        """Assign or change the target map canvas."""
        if self.canvas != canvas:
            self.clear()
            self.canvas = canvas
            self._coord_transform = None
            self._transform_crs_id = ""

    def load_routes(self, routes: Sequence[RouteResult3D] | RouteResult3D) -> None:
        """Load one or more route results for multi-avatar animation."""
        self.stop()
        self.clear()

        route_list: List[RouteResult3D] = [routes] if isinstance(routes, RouteResult3D) else list(routes)
        if not route_list:
            return

        self.avatars = []
        max_dur = 0.0

        for r in route_list:
            if not r.coordinates_3d:
                continue

            coords = r.coordinates_3d
            cum_dist: List[float] = [0.0]
            cum_time: List[float] = [0.0]

            total_d = 0.0
            total_t = 0.0

            for i in range(1, len(coords)):
                seg_d = haversine_distance_3d(coords[i - 1], coords[i])
                total_d += seg_d
                cum_dist.append(total_d)

                # Use the same profile kinematics as route statistics/isochrones.
                dz = coords[i][2] - coords[i - 1][2]
                slope_pct = (dz / max(1.0, seg_d)) * 100.0 if seg_d > 0.1 else 0.0
                seg_t = r.profile.travel_time_seconds(seg_d, slope_pct=slope_pct)
                total_t += seg_t
                cum_time.append(total_t)

            max_dur = max(max_dur, total_t)
            color_hex = get_profile_color(r.profile.key)

            avatar = AnimatedAvatar(
                profile_key=r.profile.key,
                profile_name=r.profile.name,
                color_hex=color_hex,
                coordinates_3d=coords,
                cumulative_distances_m=cum_dist,
                timestamps_s=cum_time,
                total_duration_s=total_t,
                total_distance_m=total_d,
            )
            self.avatars.append(avatar)

        self.max_duration_s = max(1.0, max_dur)
        self.current_time_s = 0.0
        self._init_canvas_markers()
        self._update_avatar_positions()

    def _init_canvas_markers(self) -> None:
        """Create one QGIS SVG-rendered point layer for all animated avatars."""
        if not self.canvas:
            return
        with contextlib.suppress(Exception):
            import sip
            if sip.isdeleted(self.canvas):
                return

        self._remove_avatar_layer()
        try:
            from qgis.core import QgsCategorizedSymbolRenderer, QgsMarkerSymbol, QgsRendererCategory, QgsSvgMarkerSymbolLayer

            layer = QgsVectorLayer(
                "Point?crs=EPSG:4326&field=profile_key:string&field=profile_name:string&field=heading:double",
                "02Route 3D — Animated SVG Avatars",
                "memory",
            )
            if not layer.isValid():
                return

            layer.setCustomProperty("zero2route3d/animated_avatar_layer", True)
            provider = layer.dataProvider()
            categories = []
            avatar_dir = Path(__file__).resolve().parent.parent / "icons" / "avatars"
            category_keys = {avatar.profile_key for avatar in self.avatars}
            for key in sorted(category_keys):
                svg_path = avatar_dir / f"{key}.svg"
                if not svg_path.exists():
                    svg_path = avatar_dir / "adult.svg"
                svg_layer = QgsSvgMarkerSymbolLayer(str(svg_path))
                with contextlib.suppress(Exception):
                    svg_layer.setSize(10.0)
                with contextlib.suppress(Exception):
                    from qgis.core import QgsProperty, QgsSymbolLayer
                    svg_layer.setDataDefinedProperty(
                        QgsSymbolLayer.PropertyAngle,
                        QgsProperty.fromField("heading"),
                    )
                symbol = QgsMarkerSymbol()
                symbol.deleteSymbolLayer(0)
                symbol.appendSymbolLayer(svg_layer)
                label = next((avatar.profile_name for avatar in self.avatars if avatar.profile_key == key), key)
                categories.append(QgsRendererCategory(key, symbol, label))

            layer.setRenderer(QgsCategorizedSymbolRenderer("profile_key", categories))
            features = []
            for avatar in self.avatars:
                lon, lat, _ele = avatar.interpolate_position(0.0)
                feature = QgsFeature(layer.fields())
                feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))
                feature.setAttributes([avatar.profile_key, avatar.profile_name, 0.0])
                features.append(feature)
            provider.addFeatures(features)
            stored_features = list(layer.getFeatures())
            layer.updateExtents()
            QgsProject.instance().addMapLayer(layer)
            self.avatar_layer = layer
            self._avatar_feature_ids = [feature.id() for feature in stored_features]
            for avatar in self.avatars:
                avatar.marker = None
        except Exception:
            self.avatar_layer = None
            self._avatar_feature_ids = []

    def _remove_avatar_layer(self) -> None:
        """Remove the transient SVG avatar layer without touching user layers."""
        if self.avatar_layer is not None:
            with contextlib.suppress(Exception):
                QgsProject.instance().removeMapLayer(self.avatar_layer.id())
        self.avatar_layer = None
        self._avatar_feature_ids = []

    def play(self) -> None:
        """Start or resume animation."""
        if not self.avatars:
            return
        if self.current_time_s >= self.max_duration_s:
            self.current_time_s = 0.0
        self._init_canvas_markers()
        self.is_playing = True
        self.timer.start()
        self.playback_state_changed.emit(True)

    def pause(self) -> None:
        """Pause animation at current frame."""
        self.is_playing = False
        self.timer.stop()
        self.playback_state_changed.emit(False)

    def toggle_play(self) -> None:
        """Toggle between play and pause."""
        if self.is_playing:
            self.pause()
        else:
            self.play()

    def stop(self) -> None:
        """Stop animation and rewind to beginning."""
        self.pause()
        self.current_time_s = 0.0
        self._update_avatar_positions()
        self.frame_updated.emit(0.0, self.max_duration_s, 0.0)

    def seek_progress(self, fraction: float) -> None:
        """Seek to progress fraction (0.0 to 1.0)."""
        fraction = max(0.0, min(1.0, fraction))
        self.current_time_s = fraction * self.max_duration_s
        self._update_avatar_positions()
        self.frame_updated.emit(self.current_time_s, self.max_duration_s, fraction)

    def set_speed_multiplier(self, multiplier: float) -> None:
        """Set animation speed multiplier (e.g. 1x, 5x, 10x, 50x)."""
        self.speed_multiplier = max(0.1, min(200.0, float(multiplier)))

    def set_auto_pan(self, enabled: bool) -> None:
        """Enable or disable camera auto-panning to leading avatar."""
        self.auto_pan = bool(enabled)

    def clear(self) -> None:
        """Remove the transient SVG avatar layer and reset animation state."""
        self.pause()
        self._remove_avatar_layer()
        for avatar in self.avatars:
            avatar.marker = None
        self.avatars.clear()
        self.current_time_s = 0.0
        self.max_duration_s = 0.0

    def _on_tick(self) -> None:
        """Timer tick handler: advance simulation time and update canvas markers."""
        dt_real_sec = self.timer.interval() / 1000.0
        self.current_time_s += dt_real_sec * self.speed_multiplier

        if self.current_time_s >= self.max_duration_s:
            if self.loop:
                self.current_time_s = 0.0
            else:
                self.current_time_s = self.max_duration_s
                self.pause()
                self.animation_finished.emit()

        self._update_avatar_positions()
        progress = self.current_time_s / self.max_duration_s if self.max_duration_s > 0 else 0.0
        self.frame_updated.emit(self.current_time_s, self.max_duration_s, progress)

    def _update_avatar_positions(self) -> None:
        """Calculate coordinates for all avatars at current_time_s and update markers."""
        if not self.canvas or not self.avatars:
            return
        with contextlib.suppress(Exception):
            import sip
            if sip.isdeleted(self.canvas):
                return

        crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        crs_canvas = self.canvas.mapSettings().destinationCrs()
        crs_id = crs_canvas.authid() if crs_canvas.isValid() else ""
        if crs_id != self._transform_crs_id:
            self._coord_transform = None
            self._transform_crs_id = crs_id
        if crs_canvas.isValid() and crs_canvas != crs_wgs84 and self._coord_transform is None:
            with contextlib.suppress(Exception):
                self._coord_transform = QgsCoordinateTransform(crs_wgs84, crs_canvas, QgsProject.instance())
        transform = self._coord_transform

        leader_canvas_pt: Optional[QgsPointXY] = None

        for idx, avatar in enumerate(self.avatars):
            lon, lat, _ele = avatar.interpolate_position(self.current_time_s)
            pt_wgs84 = QgsPointXY(lon, lat)
            pt_canvas = pt_wgs84
            if transform is not None and transform.isValid():
                with contextlib.suppress(Exception):
                    pt_canvas = transform.transform(pt_wgs84)

            if self.avatar_layer is not None and idx < len(self._avatar_feature_ids):
                with contextlib.suppress(Exception):
                    feature_id = self._avatar_feature_ids[idx]
                    geometry = QgsGeometry.fromPointXY(QgsPointXY(lon, lat))
                    self.avatar_layer.dataProvider().changeGeometryValues({feature_id: geometry})
                    look_ahead = min(self.current_time_s + 0.2, avatar.total_duration_s)
                    next_lon, next_lat, _next_ele = avatar.interpolate_position(look_ahead)
                    heading = math.degrees(math.atan2(next_lon - lon, next_lat - lat))
                    self.avatar_layer.dataProvider().changeAttributeValues({feature_id: {2: heading}})

            if idx == 0:
                leader_canvas_pt = pt_canvas

        if self.avatar_layer is not None:
            with contextlib.suppress(Exception):
                self.avatar_layer.updateExtents()
                self.avatar_layer.triggerRepaint()

        if self.auto_pan and leader_canvas_pt is not None:
            with contextlib.suppress(Exception):
                extent = self.canvas.extent()
                if not extent.contains(leader_canvas_pt):
                    self.canvas.setCenter(leader_canvas_pt)
