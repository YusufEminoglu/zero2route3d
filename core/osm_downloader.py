"""OpenStreetMap road network and building footprint fetcher for 02Route 3D."""
from __future__ import annotations

import contextlib
import http.client
import json
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

USER_AGENT = "02Route3D-QGIS-Plugin/0.1.0 (https://github.com/YusufEminoglu/zero2route3d)"
DEFAULT_TIMEOUT_S = 30


@dataclass
class OsmBuilding:
    """Real OSM building footprint with height and levels."""

    building_id: str
    polygon: List[Tuple[float, float]]  # [(lon, lat), ...]
    height_m: float = 12.0
    levels: int = 4
    building_type: str = "yes"


@dataclass
class OsmRoad:
    """Real OSM road centerline with highway type and name."""

    road_id: str
    geometry: List[Tuple[float, float]]  # [(lon, lat), ...]
    highway_type: str = "residential"
    name: str = ""
    oneway: bool = False
    maxspeed: Optional[float] = None


class OsmDataFetcher:
    """Fetches real roads and building footprints from OpenStreetMap Overpass API."""

    @staticmethod
    def fetch_roads_and_buildings(
        bbox: Tuple[float, float, float, float],
        timeout_s: int = DEFAULT_TIMEOUT_S,
    ) -> Tuple[List[OsmRoad], List[OsmBuilding]]:
        """Fetch roads and buildings in bbox: (min_lon, min_lat, max_lon, max_lat)."""
        if not bbox or len(bbox) < 4:
            return [], []

        min_lon, min_lat, max_lon, max_lat = (
            float(bbox[0]),
            float(bbox[1]),
            float(bbox[2]),
            float(bbox[3]),
        )
        if not (math.isfinite(min_lon) and math.isfinite(min_lat) and math.isfinite(max_lon) and math.isfinite(max_lat)):
            return [], []

        if min_lon >= max_lon or min_lat >= max_lat:
            return [], []

        to_sec = max(1, int(timeout_s)) if math.isfinite(timeout_s) and timeout_s > 0 else DEFAULT_TIMEOUT_S

        query = f"""
        [out:json][timeout:{to_sec}];
        (
          way["highway"~"primary|secondary|tertiary|residential|service|footway|cycleway|living_street|pedestrian|path|track|unclassified"]({min_lat},{min_lon},{max_lat},{max_lon});
          way["building"]({min_lat},{min_lon},{max_lat},{max_lon});
          relation["building"]({min_lat},{min_lon},{max_lat},{max_lon});
        );
        out body geom;
        """.strip()

        roads: List[OsmRoad] = []
        buildings: List[OsmBuilding] = []

        with contextlib.suppress(Exception):
            import urllib.parse
            body = urllib.parse.urlencode({"data": query}).encode("utf-8")
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT,
            }
            conn = http.client.HTTPSConnection("overpass-api.de", timeout=to_sec)
            try:
                conn.request("POST", "/api/interpreter", body=body, headers=headers)
                resp = conn.getresponse()
                if resp.status == 200:
                    raw_bytes = resp.read()
                    data = json.loads(raw_bytes.decode("utf-8"))

                    for elem in data.get("elements", []):
                        tags = elem.get("tags", {})
                        elem_id = str(elem.get("id", ""))

                        if "highway" in tags:
                            geom = elem.get("geometry", [])
                            coords = []
                            for pt in geom:
                                try:
                                    lon_val = float(pt["lon"])
                                    lat_val = float(pt["lat"])
                                    if math.isfinite(lon_val) and math.isfinite(lat_val):
                                        coords.append((lon_val, lat_val))
                                except (KeyError, ValueError, TypeError):
                                    continue

                            if len(coords) >= 2:
                                roads.append(
                                    OsmRoad(
                                        road_id=elem_id,
                                        geometry=coords,
                                        highway_type=str(tags.get("highway", "residential")),
                                        name=str(tags.get("name", "")),
                                        oneway=tags.get("oneway") in ("yes", "1", "true"),
                                    )
                                )
                        elif "building" in tags:
                            geom = elem.get("geometry", [])
                            coords = []
                            for pt in geom:
                                try:
                                    lon_val = float(pt["lon"])
                                    lat_val = float(pt["lat"])
                                    if math.isfinite(lon_val) and math.isfinite(lat_val):
                                        coords.append((lon_val, lat_val))
                                except (KeyError, ValueError, TypeError):
                                    continue

                            if len(coords) >= 3:
                                levels = 4
                                height_m = 12.0
                                if "building:levels" in tags:
                                    with contextlib.suppress(ValueError, TypeError):
                                        levels = max(1, int(float(tags["building:levels"])))
                                        height_m = levels * 3.2
                                elif "height" in tags:
                                    with contextlib.suppress(ValueError, TypeError):
                                        h_str = str(tags["height"]).replace("m", "").strip()
                                        height_m = max(3.0, float(h_str))
                                        levels = max(1, int(height_m / 3.2))

                                buildings.append(
                                    OsmBuilding(
                                        building_id=elem_id,
                                        polygon=coords,
                                        height_m=height_m,
                                        levels=levels,
                                        building_type=str(tags.get("building", "yes")),
                                    )
                                )
            finally:
                conn.close()

        return roads, buildings
