"""Road network data acquisition, vector layer ingestion, and geocoding engine.

Supports fetching OpenStreetMap networks via Overpass API with local bounding
box caching, reverse geocoding via Photon API, and extracting topology from
active QGIS vector layers.
"""
from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import math
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .kinematics import haversine_distance_2d
from .input_validation import normalize_bbox


@dataclass
class RoadSegment:
    """Directed edge segment representing a traversable line between two vertices."""

    p1: Tuple[float, float, float]
    p2: Tuple[float, float, float]
    length_m: float
    highway_type: str = "residential"
    hierarchy_rank: int = 4  # 1: Motorway, 2: Primary, 3: Secondary/Tertiary, 4: Residential, 5: Path/Service
    lanes: int = 2
    is_steps: bool = False
    surface: str = "asphalt"
    is_oneway: bool = False
    # Real OSM "name" tag. Left empty when the way is unnamed -- an unnamed street
    # must stay unnamed rather than be given a placeholder like "Urban Path".
    name: str = ""
    access: str = ""
    foot: str = ""
    bicycle: str = ""
    motor_vehicle: str = ""
    lit: str = ""
    sidewalk: str = ""
    maxspeed_kmh: Optional[float] = None
    # Mode-specific exceptions to "oneway" (OSM oneway:bicycle / oneway:foot).
    # "no" opens the contra-flow direction to that mode; "yes" makes a
    # two-way street one-way for it.
    oneway_bicycle: str = ""
    oneway_foot: str = ""


class NetworkSourceError(RuntimeError):
    """Raised when no usable real network can be loaded for an operation."""


class NetworkSourceCancelled(NetworkSourceError):
    """Raised when the caller cancelled an OpenStreetMap download."""


# Overpass responses are read in pieces so a cancel takes effect mid-download.
_READ_CHUNK_BYTES = 64 * 1024


class NetworkSourceManager:
    """Acquires, caches, geocodes, and parses topological road networks."""

    CACHE_DIR = Path(tempfile.gettempdir()) / "zero2route3d_cache"

    def __init__(self) -> None:
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _highway_to_hierarchy(highway: str) -> int:
        """Map OSM highway tag to functional hierarchy rank (1..5)."""
        h = str(highway).lower()
        if h in {"motorway", "motorway_link", "trunk", "trunk_link"}:
            return 1
        if h in {"primary", "primary_link"}:
            return 2
        if h in {"secondary", "secondary_link", "tertiary", "tertiary_link"}:
            return 3
        if h in {"residential", "living_street", "unclassified"}:
            return 4
        return 5

    def fetch_osm_network_bbox(
        self,
        bbox: Tuple[float, float, float, float],
        buffer_ratio: float = 0.15,
        is_canceled: Optional[Callable[[], bool]] = None,
    ) -> List[RoadSegment]:
        """Fetch OSM highway ways within bbox using Overpass API with disk caching.

        A missing/invalid response is an input error, not a reason to invent a
        network.  Callers can surface :class:`NetworkSourceError` directly to
        QGIS users and let them choose a valid layer or retry the download.
        """
        try:
            min_lon, min_lat, max_lon, max_lat = normalize_bbox(bbox)
        except ValueError as exc:
            raise NetworkSourceError(str(exc)) from exc

        d_lon = max(0.005, max_lon - min_lon)
        d_lat = max(0.005, max_lat - min_lat)

        buf_r = float(buffer_ratio) if math.isfinite(buffer_ratio) and buffer_ratio >= 0 else 0.15
        buf_r = min(1.0, buf_r)
        buf_lon = d_lon * buf_r
        buf_lat = d_lat * buf_r
        s = max(-90.0, min_lat - buf_lat)
        w = max(-180.0, min_lon - buf_lon)
        n = min(90.0, max_lat + buf_lat)
        e = min(180.0, max_lon + buf_lon)

        cache_key = hashlib.sha256(f"{s:.4f}_{w:.4f}_{n:.4f}_{e:.4f}".encode("utf-8")).hexdigest()
        cache_file = self.CACHE_DIR / f"osm_{cache_key}.json"

        data = None
        if cache_file.exists():
            with contextlib.suppress(Exception):
                with open(cache_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict) and "elements" in loaded:
                        data = loaded

        if not data:
            query = f"""[out:json][timeout:25];(way["highway"]({s:.5f},{w:.5f},{n:.5f},{e:.5f}););out body;>;out skel qt;"""
            data = self._query_overpass(query, is_canceled=is_canceled)
            if data and isinstance(data, dict) and "elements" in data:
                with contextlib.suppress(Exception):
                    temp_cache = cache_file.with_suffix(".tmp")
                    with open(temp_cache, "w", encoding="utf-8") as f:
                        json.dump(data, f)
                    temp_cache.replace(cache_file)

        if not data or not isinstance(data, dict) or "elements" not in data:
            reasons = "; ".join(getattr(self, "last_overpass_errors", []) or [])
            raise NetworkSourceError(
                "OpenStreetMap network download failed"
                + (f" ({reasons})" if reasons else "")
                + ". Check the internet connection or select a QGIS line network layer."
            )

        segments = self._parse_osm_json(data)
        if not segments:
            raise NetworkSourceError(
                "OpenStreetMap returned no usable road segments for this extent. "
                "Try a larger extent or select a QGIS line network layer."
            )
        return segments

    def require_segments(
        self,
        vector_layer: Any = None,
        bbox: Optional[Tuple[float, float, float, float]] = None,
        is_canceled: Optional[Callable[[], bool]] = None,
    ) -> List[RoadSegment]:
        """Load a real network from a QGIS line layer or OSM, never fabricated data.

        ``is_canceled`` (e.g. ``feedback.isCanceled``) stops an OSM download
        between reads with :class:`NetworkSourceCancelled`.
        """
        if vector_layer is not None:
            segments = self.extract_from_qgis_layer(vector_layer)
            if not segments:
                raise NetworkSourceError(
                    "The selected network layer contains no usable line segments. "
                    "Select a non-empty line layer with a valid CRS."
                )
            return segments
        if bbox is None:
            raise NetworkSourceError(
                "No network source was provided. Select a QGIS line layer or enable OSM download."
            )
        return self.fetch_osm_network_bbox(bbox, is_canceled=is_canceled)

    def _query_overpass(
        self, query: str, is_canceled: Optional[Callable[[], bool]] = None
    ) -> Dict[str, Any] | None:
        """Safe HTTPS POST to Overpass API without generic urlopen."""
        endpoints = [
            ("overpass-api.de", "/api/interpreter"),
            ("maps.mail.ru", "/osm/tools/overpass/api/interpreter"),
        ]
        body = urllib.parse.urlencode({"data": query}).encode("utf-8")
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "02Route3D-QGIS-Plugin",
        }

        self.last_overpass_errors = []
        for host, path in endpoints:
            if is_canceled is not None and is_canceled():
                raise NetworkSourceCancelled("The OpenStreetMap download was cancelled.")
            try:
                conn = http.client.HTTPSConnection(host, timeout=12)
                try:
                    conn.request("POST", path, body=body, headers=headers)
                    resp = conn.getresponse()
                    if resp.status == 200:
                        raw = self._read_body(resp, is_canceled).decode("utf-8")
                        parsed = json.loads(raw)
                        if isinstance(parsed, dict):
                            return parsed
                        self.last_overpass_errors.append(f"{host}: unexpected response")
                    else:
                        self.last_overpass_errors.append(f"{host}: HTTP {resp.status} {resp.reason}".strip())
                finally:
                    conn.close()
            except NetworkSourceCancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - reported to the user below
                self.last_overpass_errors.append(f"{host}: {type(exc).__name__}: {exc}")
        return None

    @staticmethod
    def _read_body(resp: Any, is_canceled: Optional[Callable[[], bool]] = None) -> bytes:
        """Read an HTTP response in chunks, stopping if the caller cancels."""
        parts: List[bytes] = []
        while True:
            if is_canceled is not None and is_canceled():
                raise NetworkSourceCancelled("The OpenStreetMap download was cancelled.")
            chunk = resp.read(_READ_CHUNK_BYTES)
            if not chunk:
                return b"".join(parts)
            parts.append(chunk)

    def _parse_osm_json(self, data: Dict[str, Any]) -> List[RoadSegment]:
        """Convert OSM JSON response elements into list of RoadSegments."""
        nodes: Dict[int, Tuple[float, float]] = {}
        segments: List[RoadSegment] = []

        for el in data.get("elements", []):
            if el.get("type") == "node":
                try:
                    nid = int(el["id"])
                    nlon = float(el["lon"])
                    nlat = float(el["lat"])
                    if math.isfinite(nlon) and math.isfinite(nlat):
                        nodes[nid] = (nlon, nlat)
                except (KeyError, ValueError, TypeError):
                    continue

        for el in data.get("elements", []):
            if el.get("type") == "way":
                tags = el.get("tags") or {}
                highway = str(tags.get("highway", "residential"))
                hierarchy = self._highway_to_hierarchy(highway)
                is_steps = highway == "steps"
                surface = str(tags.get("surface", "asphalt"))
                lanes = 1
                with contextlib.suppress(Exception):
                    lanes = max(1, int(tags.get("lanes", 1)))
                oneway_tag = str(tags.get("oneway", "")).strip().lower()
                # oneway=-1 means the way is one-way *against* its digitisation order.
                # Treating it as bidirectional routed vehicles the wrong way down it.
                reversed_oneway = oneway_tag in {"-1", "reverse"}
                oneway = (
                    oneway_tag in {"yes", "1", "true"}
                    or reversed_oneway
                    or str(tags.get("junction", "")).strip().lower() == "roundabout"
                )
                street_name = str(tags.get("name", "") or "").strip()
                access = str(tags.get("access", "") or "").strip()
                foot = str(tags.get("foot", "") or "").strip()
                bicycle = str(tags.get("bicycle", "") or "").strip()
                motor_vehicle = str(
                    tags.get("motor_vehicle", tags.get("vehicle", "")) or ""
                ).strip()
                lit = str(tags.get("lit", "") or "").strip()
                sidewalk = str(tags.get("sidewalk", "") or "").strip()
                maxspeed_kmh = self._parse_speed_kmh(tags.get("maxspeed"))
                oneway_bicycle = str(tags.get("oneway:bicycle", "") or "").strip()
                oneway_foot = str(tags.get("oneway:foot", "") or "").strip()

                way_nodes = el.get("nodes") or []
                for i in range(len(way_nodes) - 1):
                    n1 = way_nodes[i]
                    n2 = way_nodes[i + 1]
                    if n1 in nodes and n2 in nodes:
                        c1 = nodes[n1]
                        c2 = nodes[n2]
                        dist = haversine_distance_2d(c1, c2)
                        if dist >= 0.1 and math.isfinite(dist):
                            seg = RoadSegment(
                                p1=(c1[0], c1[1], 0.0),
                                p2=(c2[0], c2[1], 0.0),
                                length_m=dist,
                                highway_type=highway,
                                hierarchy_rank=hierarchy,
                                lanes=lanes,
                                is_steps=is_steps,
                                surface=surface,
                                is_oneway=oneway,
                                name=street_name,
                                access=access,
                                foot=foot,
                                bicycle=bicycle,
                                motor_vehicle=motor_vehicle,
                                lit=lit,
                                sidewalk=sidewalk,
                                maxspeed_kmh=maxspeed_kmh,
                                oneway_bicycle=oneway_bicycle,
                                oneway_foot=oneway_foot,
                            )
                            if reversed_oneway:
                                # Store it in its true travel direction.
                                seg.p1, seg.p2 = seg.p2, seg.p1
                            segments.append(seg)
        return segments

    @staticmethod
    def _parse_speed_kmh(value: Any) -> Optional[float]:
        """Parse common OSM maxspeed forms without inventing a default."""
        if value is None:
            return None
        text = str(value).strip().lower()
        if not text or text in {"none", "signals", "variable", "walk"}:
            return None
        try:
            number = float(text.split()[0])
        except (ValueError, TypeError, IndexError):
            return None
        if "mph" in text:
            number *= 1.609344
        return number if math.isfinite(number) and number > 0 else None

    def extract_from_qgis_layer(self, vector_layer: Any) -> List[RoadSegment]:
        """Extract road network edges from an active QGIS line vector layer."""
        if vector_layer is None:
            return []
        segments: List[RoadSegment] = []

        def point_coordinate(point: Any, name: str, default: float = 0.0) -> float:
            """Read QgsPoint or QgsPointXY coordinates across QGIS versions."""
            value = getattr(point, name, None)
            if callable(value):
                value = value()
            try:
                return float(value) if value is not None else default
            except (TypeError, ValueError):
                return default

        with contextlib.suppress(ImportError):
            from qgis.core import (
                QgsCoordinateReferenceSystem,
                QgsCoordinateTransform,
                QgsProject,
            )

            crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
            crs_layer = vector_layer.crs()
            needs_transform = crs_layer != crs_wgs84
            transform = (
                QgsCoordinateTransform(crs_layer, crs_wgs84, QgsProject.instance())
                if needs_transform
                else None
            )

            for feat in vector_layer.getFeatures():
                geom = feat.geometry()
                if geom.isNull() or geom.isEmpty():
                    continue

                if needs_transform and transform is not None:
                    geom.transform(transform)

                abstract = geom.constGet()
                if abstract is None:
                    continue
                if geom.isMultipart() and hasattr(abstract, "numGeometries"):
                    parts = [abstract.geometryN(i) for i in range(abstract.numGeometries())]
                else:
                    parts = [abstract]

                field_lookup = {str(name).lower(): name for name in feat.fields().names()}

                def attribute(names: Tuple[str, ...], default: Any = "") -> Any:
                    for candidate in names:
                        real_name = field_lookup.get(candidate.lower())
                        if real_name is not None:
                            value = feat[real_name]
                            if value is not None and str(value).strip() != "":
                                return value
                    return default

                highway = str(attribute(("highway", "type", "road_type", "class", "kind"), "residential"))

                hierarchy = self._highway_to_hierarchy(highway)
                is_steps = "step" in highway.lower() or "merdiven" in highway.lower()
                surface = str(attribute(("surface", "pavement", "surf_type"), ""))
                name = str(attribute(("name", "street", "road_name", "ref"), ""))
                access = str(attribute(("access",), ""))
                foot = str(attribute(("foot", "pedestrian"), ""))
                bicycle = str(attribute(("bicycle", "bike"), ""))
                motor_vehicle = str(attribute(("motor_vehicle", "motorcar", "vehicle"), ""))
                lit = str(attribute(("lit", "lighting"), ""))
                sidewalk = str(attribute(("sidewalk",), ""))
                oneway_text = str(attribute(("oneway", "one_way"), "")).strip().lower()
                is_oneway = oneway_text in {"yes", "1", "true", "-1", "reverse"}
                reverse_oneway = oneway_text in {"-1", "reverse"}
                maxspeed_kmh = self._parse_speed_kmh(attribute(("maxspeed", "speed_limit"), None))
                oneway_bicycle = str(attribute(("oneway:bicycle", "oneway_bicycle", "oneway_bike"), ""))
                oneway_foot = str(attribute(("oneway:foot", "oneway_foot"), ""))
                try:
                    lanes = max(1, int(float(attribute(("lanes", "lane_count"), 1))))
                except (ValueError, TypeError, OverflowError):
                    lanes = 1

                for part in parts:
                    line = list(part.vertices())
                    for i in range(len(line) - 1):
                        p1 = line[i]
                        p2 = line[i + 1]
                        c1 = (
                            point_coordinate(p1, "x"),
                            point_coordinate(p1, "y"),
                            point_coordinate(p1, "z"),
                        )
                        c2 = (
                            point_coordinate(p2, "x"),
                            point_coordinate(p2, "y"),
                            point_coordinate(p2, "z"),
                        )
                        if not (math.isfinite(c1[0]) and math.isfinite(c1[1]) and math.isfinite(c2[0]) and math.isfinite(c2[1])):
                            continue
                        dist = haversine_distance_2d(c1, c2)
                        if dist >= 0.1 and math.isfinite(dist):
                            if reverse_oneway:
                                c1, c2 = c2, c1
                            segments.append(
                                RoadSegment(
                                    p1=c1,
                                    p2=c2,
                                    length_m=dist,
                                    highway_type=highway,
                                    hierarchy_rank=hierarchy,
                                    lanes=lanes,
                                    is_steps=is_steps,
                                    surface=surface,
                                    is_oneway=is_oneway,
                                    name=name,
                                    access=access,
                                    foot=foot,
                                    bicycle=bicycle,
                                    motor_vehicle=motor_vehicle,
                                    lit=lit,
                                    sidewalk=sidewalk,
                                    maxspeed_kmh=maxspeed_kmh,
                                    oneway_bicycle=oneway_bicycle,
                                    oneway_foot=oneway_foot,
                                )
                            )
        return segments
