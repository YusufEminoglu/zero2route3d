"""Write a viewer site with a deterministic route payload, for browser tests.

Copies web/ to the target folder and writes data/current_route.json: three
profiles routed on the synthetic benchmark grid, a DEM grid from a smooth
analytic surface, and corridor buildings and trees beside the route. No
network access and nothing random, so screenshots are reproducible.

    python3 -m zero2route3d.tests.viewer.make_site <folder>
"""
from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path

from zero2route3d.core.routing_engine import RoutingEngine3D, Waypoint
from zero2route3d.core.terrain_grid import grid_bbox, sample_terrain_grid
from zero2route3d.tests.benchmark_routing import LAT0, LON0, STEP, grid_segments

WEB = Path(__file__).resolve().parents[2] / "web"


def _height(lon: float, lat: float) -> float:
    i = (lon - LON0) / STEP
    j = (lat - LAT0) / STEP
    return 40.0 + 25.0 * math.sin(i / 7.0) * math.cos(j / 9.0)


def build_payload() -> dict:
    engine = RoutingEngine3D()
    engine.build_graph(grid_segments(40))
    waypoints = [
        Waypoint(LON0 + 2.2 * STEP, LAT0 + 3.1 * STEP, "A"),
        Waypoint(LON0 + 31.4 * STEP, LAT0 + 27.6 * STEP, "B"),
    ]
    features = []
    for key in ("adult", "wheelchair", "bicycle"):
        result = engine.calculate_route(waypoints, profile_key=key)
        if not result.coordinates_3d:
            raise RuntimeError(f"{key}: {result.status_message}")
        features.append(result.to_geojson_feature())
    terrain = sample_terrain_grid(_height, grid_bbox([f["geometry"]["coordinates"] for f in features], 60.0))

    buildings, trees = [], []
    coords = features[0]["geometry"]["coordinates"]
    for k, idx in enumerate(range(2, len(coords) - 2, 3), start=1):
        lon, lat = coords[idx][0], coords[idx][1]
        off = 0.00018 if k % 2 else -0.00018
        west, south, size = lon + off, lat + off * 0.5, 0.00009
        buildings.append({
            "id": f"b{k}",
            "polygon": [[west, south], [west + size, south], [west + size, south + size * 0.8], [west, south + size * 0.8]],
            "height_m": 8 + (k % 6) * 4,
            "base_elevation_m": _height(west, south),
        })
        tree_lon, tree_lat = lon - off * 0.6, lat + off * 0.3
        trees.append({
            "id": f"t{k}",
            "coordinates": [tree_lon, tree_lat],
            "height_m": 7 + k % 5,
            "tree_type": "conifer" if k % 3 == 0 else "deciduous",
            "base_elevation_m": _height(tree_lon, tree_lat),
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "primary_profile_key": "adult",
            "route_count": len(features),
            "corridor_buildings": buildings,
            "corridor_trees": trees,
            "terrain": terrain,
        },
    }


def make_site(folder: Path) -> dict:
    if folder.exists():
        shutil.rmtree(folder)
    shutil.copytree(WEB, folder, ignore=shutil.ignore_patterns("current_route.json"))
    payload = build_payload()
    (folder / "data").mkdir(exist_ok=True)
    (folder / "data" / "current_route.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


if __name__ == "__main__":
    target = Path(sys.argv[1])
    data = make_site(target)
    print(f"site ready in {target}: {len(data['properties']['corridor_buildings'])} buildings")
