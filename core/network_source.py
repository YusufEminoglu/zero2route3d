"""Road network data acquisition and vector layer ingestion engine.

Supports fetching OpenStreetMap networks via Overpass API with local bounding
box caching, extracting topology from active QGIS vector layers, and generating
topological test graphs.
"""
from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from .kinematics import haversine_distance_2d


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


class NetworkSourceManager:
    """Acquires, caches, and parses topological road networks."""

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
        return 5  # pedestrian, footway, path, steps, cycleway, track, service

    def fetch_osm_network_bbox(
        self,
        bbox: Tuple[float, float, float, float],  # (min_lon, min_lat, max_lon, max_lat)
        buffer_ratio: float = 0.15,
    ) -> List[RoadSegment]:
        """Fetch OSM highway ways within bbox (with buffer margin) using Overpass API.
        
        Uses SHA-256 hashed disk cache for instant reuse.
        """
        min_lon, min_lat, max_lon, max_lat = bbox
        d_lon = max_lon - min_lon
        d_lat = max_lat - min_lat

        # Ensure minimum extent of ~500m
        d_lon = max(0.005, d_lon)
        d_lat = max(0.005, d_lat)

        buf_lon = d_lon * buffer_ratio
        buf_lat = d_lat * buffer_ratio
        s = min_lat - buf_lat
        w = min_lon - buf_lon
        n = max_lat + buf_lat
        e = max_lon + buf_lon

        cache_key = hashlib.sha256(f"{s:.4f}_{w:.4f}_{n:.4f}_{e:.4f}".encode("utf-8")).hexdigest()
        cache_file = self.CACHE_DIR / f"osm_{cache_key}.json"

        data = None
        if cache_file.exists():
            with contextlib.suppress(Exception):
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

        if not data:
            query = f"""[out:json][timeout:25];(way["highway"]({s:.5f},{w:.5f},{n:.5f},{e:.5f}););out body;>;out skel qt;"""
            data = self._query_overpass(query)
            if data and "elements" in data:
                with contextlib.suppress(Exception):
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(data, f)

        if not data or "elements" not in data:
            # Fallback to synthetic grid for offline resilience
            return self.generate_synthetic_grid(bbox)

        return self._parse_osm_json(data)

    def _query_overpass(self, query: str) -> Dict[str, Any] | None:
        """Safe HTTPS POST to Overpass API without generic urlopen."""
        endpoints = [
            ("overpass-api.de", "/api/interpreter"),
            ("maps.mail.ru", "/osm/tools/overpass/api/interpreter"),
        ]
        body = urllib.parse.urlencode({"data": query}).encode("utf-8")
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "02Route3D-QGIS-Plugin/0.1.0",
        }

        for host, path in endpoints:
            with contextlib.suppress(Exception):
                conn = http.client.HTTPSConnection(host, timeout=12)
                conn.request("POST", path, body=body, headers=headers)
                resp = conn.getresponse()
                if resp.status == 200:
                    raw = resp.read().decode("utf-8")
                    conn.close()
                    return json.loads(raw)
                conn.close()
        return None

    def _parse_osm_json(self, data: Dict[str, Any]) -> List[RoadSegment]:
        """Convert OSM JSON response elements into list of RoadSegments."""
        nodes: Dict[int, Tuple[float, float]] = {}
        segments: List[RoadSegment] = []

        for el in data.get("elements", []):
            if el.get("type") == "node":
                nodes[int(el["id"])] = (float(el["lon"]), float(el["lat"]))

        for el in data.get("elements", []):
            if el.get("type") == "way":
                tags = el.get("tags", {})
                highway = tags.get("highway", "residential")
                hierarchy = self._highway_to_hierarchy(highway)
                is_steps = highway == "steps"
                surface = tags.get("surface", "asphalt")
                lanes = 1
                with contextlib.suppress(Exception):
                    lanes = max(1, int(tags.get("lanes", 1)))
                oneway = tags.get("oneway") in {"yes", "1", "true"}

                way_nodes = el.get("nodes", [])
                for i in range(len(way_nodes) - 1):
                    n1 = way_nodes[i]
                    n2 = way_nodes[i + 1]
                    if n1 in nodes and n2 in nodes:
                        c1 = nodes[n1]
                        c2 = nodes[n2]
                        dist = haversine_distance_2d(c1, c2)
                        if dist >= 0.1:
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
                            )
                            segments.append(seg)
        return segments

    def extract_from_qgis_layer(self, vector_layer: Any) -> List[RoadSegment]:
        """Extract road network edges from an active QGIS line vector layer."""
        segments: List[RoadSegment] = []
        with contextlib.suppress(Exception):
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

                if needs_transform:
                    geom.transform(transform)

                # Extract line coordinates
                if geom.isMultipart():
                    lines = geom.asMultiPolyline()
                else:
                    lines = [geom.asPolyline()]

                # Attributes inspection
                fields = feat.fields().names()
                highway = "residential"
                for h_field in ["highway", "type", "road_type", "class", "KIND"]:
                    if h_field in fields and feat[h_field] is not None:
                        highway = str(feat[h_field])
                        break

                hierarchy = self._highway_to_hierarchy(highway)
                is_steps = "step" in highway.lower() or "merdiven" in highway.lower()

                for line in lines:
                    for i in range(len(line) - 1):
                        p1 = line[i]
                        p2 = line[i + 1]
                        c1 = (p1.x(), p1.y(), p1.z() if p1.is3D() else 0.0)
                        c2 = (p2.x(), p2.y(), p2.z() if p2.is3D() else 0.0)
                        dist = haversine_distance_2d(c1, c2)
                        if dist >= 0.1:
                            segments.append(
                                RoadSegment(
                                    p1=c1,
                                    p2=c2,
                                    length_m=dist,
                                    highway_type=highway,
                                    hierarchy_rank=hierarchy,
                                    is_steps=is_steps,
                                )
                            )
        return segments

    def generate_synthetic_grid(
        self,
        bbox: Tuple[float, float, float, float],
        grid_steps: int = 8,
    ) -> List[RoadSegment]:
        """Generate a regular Manhattan-style topological grid for testing/resilience."""
        min_lon, min_lat, max_lon, max_lat = bbox
        d_lon = (max_lon - min_lon) / max(2, grid_steps)
        d_lat = (max_lat - min_lat) / max(2, grid_steps)

        segments: List[RoadSegment] = []
        for i in range(grid_steps + 1):
            lon = min_lon + i * d_lon
            for j in range(grid_steps):
                lat1 = min_lat + j * d_lat
                lat2 = min_lat + (j + 1) * d_lat
                c1 = (lon, lat1, 0.0)
                c2 = (lon, lat2, 0.0)
                segments.append(
                    RoadSegment(
                        p1=c1,
                        p2=c2,
                        length_m=haversine_distance_2d(c1, c2),
                        highway_type="residential",
                        hierarchy_rank=4,
                    )
                )

        for j in range(grid_steps + 1):
            lat = min_lat + j * d_lat
            for i in range(grid_steps):
                lon1 = min_lon + i * d_lon
                lon2 = min_lon + (i + 1) * d_lon
                c1 = (lon1, lat, 0.0)
                c2 = (lon2, lat, 0.0)
                segments.append(
                    RoadSegment(
                        p1=c1,
                        p2=c2,
                        length_m=haversine_distance_2d(c1, c2),
                        highway_type="residential",
                        hierarchy_rank=4,
                    )
                )
        return segments
