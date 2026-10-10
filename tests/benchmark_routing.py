"""Deterministic routing benchmark: grid networks of about 1k, 10k and 100k edges.

Pure Python (no QGIS). Run from the parent directory of the plugin:

    python -m zero2route3d.tests.benchmark_routing            # print timings
    python -m zero2route3d.tests.benchmark_routing --gate     # fail above budgets

The networks are synthetic but realistic in what costs time: a street grid
with hills (vertex heights), mixed road classes, one-way streets, steps and
unpaved paths, so access rules and slope costs are exercised.
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from typing import Callable, Dict, List, Tuple

from zero2route3d.core.network_source import RoadSegment
from zero2route3d.core.routing_engine import RoutingEngine3D, Waypoint

# Grid spacing ~55 m; origin in Izmir so longitudes are scaled realistically.
LON0, LAT0 = 27.10, 38.40
STEP = 0.0005

# Budgets (seconds) for --gate. CI runners are slower than a laptop, so these
# are generous; they exist to catch order-of-magnitude regressions.
BUDGETS = {
    "build_10k": 4.0,
    "route_10k": 0.5,
    "od_50x50_10k": 25.0,
    "build_100k": 40.0,
    "route_100k": 4.0,
}


def grid_segments(n: int) -> List[RoadSegment]:
    """An n x n street grid (2·n·(n-1) segments) with deterministic attributes."""

    def node(i: int, j: int) -> Tuple[float, float, float]:
        z = 40.0 + 25.0 * math.sin(i / 7.0) * math.cos(j / 9.0) + 0.3 * ((i * 7 + j * 13) % 5)
        return (LON0 + i * STEP, LAT0 + j * STEP, z)

    segments: List[RoadSegment] = []
    for i in range(n):
        for j in range(n):
            for di, dj in ((1, 0), (0, 1)):
                a, b = (i, j), (i + di, j + dj)
                if b[0] >= n or b[1] >= n:
                    continue
                code = (i * 31 + j * 17 + di) % 23
                if i % 10 == 0 or j % 10 == 0:
                    highway, rank = "primary", 2
                elif code == 0:
                    highway, rank = "steps", 5
                elif code in (1, 2):
                    highway, rank = "footway", 5
                else:
                    highway, rank = "residential", 4
                p1, p2 = node(*a), node(*b)
                length = math.hypot((p2[0] - p1[0]) * 87_000.0, (p2[1] - p1[1]) * 111_000.0)
                segments.append(
                    RoadSegment(
                        p1=p1,
                        p2=p2,
                        length_m=length,
                        highway_type=highway,
                        hierarchy_rank=rank,
                        is_steps=highway == "steps",
                        surface="gravel" if code == 3 else "asphalt",
                        is_oneway=highway == "residential" and code % 4 == 0,
                        maxspeed_kmh=50.0 if rank == 2 else None,
                    )
                )
    return segments


def side_for_edges(edges: int) -> int:
    return max(3, int(round((1 + math.sqrt(1 + 2 * edges)) / 2)))


def corner_waypoints(n: int) -> List[Waypoint]:
    return [
        Waypoint(lon=LON0 + 0.2 * STEP, lat=LAT0 + 0.3 * STEP, name="SW"),
        Waypoint(lon=LON0 + (n - 1.3) * STEP, lat=LAT0 + (n - 1.2) * STEP, name="NE"),
    ]


def spread_points(n: int, count: int) -> List[Waypoint]:
    points = []
    for k in range(count):
        i = (k * 37) % (n - 2) + 1
        j = (k * 53 + 11) % (n - 2) + 1
        points.append(Waypoint(lon=LON0 + i * STEP + 0.1 * STEP, lat=LAT0 + j * STEP, name=f"P{k}"))
    return points


def timed(fn: Callable[[], object]) -> Tuple[float, object]:
    start = time.perf_counter()
    result = fn()
    return time.perf_counter() - start, result


def run(sizes=(1_000, 10_000, 100_000)) -> Dict[str, float]:
    results: Dict[str, float] = {}
    for edges in sizes:
        # The roadmap target is a 50 x 50 matrix on a 10k-edge network.
        od_size = 50 if edges == 10_000 else 10
        n = side_for_edges(edges)
        label = f"{edges // 1000}k"
        segments = grid_segments(n)
        engine = RoutingEngine3D()
        results[f"build_{label}"], _ = timed(lambda: engine.build_graph(segments))
        results[f"route_{label}"], route = timed(
            lambda: engine.calculate_route(corner_waypoints(n), profile_key="adult", compute_alternatives=False)
        )
        results[f"route_{label}_wheelchair"], _ = timed(
            lambda: engine.calculate_route(corner_waypoints(n), profile_key="wheelchair", compute_alternatives=False)
        )
        if not route.coordinates_3d:
            raise RuntimeError(f"benchmark route on the {label} grid failed: {route.status_message}")
        results[f"route_{label}_km"] = route.statistics.total_distance_km
        if edges <= 10_000:
            pts = spread_points(n, od_size)
            results[f"od_{od_size}x{od_size}_{label}"], rows = timed(
                lambda: engine.calculate_od_matrix(pts, pts, profile_key="adult")
            )
            results[f"od_{label}_matched"] = sum(1 for r in rows if r["is_matched"])
        print(
            f"{label:>5}: {len(segments):>7} segments  build {results[f'build_{label}']:.2f}s  "
            f"route {results[f'route_{label}']:.3f}s  wheelchair {results[f'route_{label}_wheelchair']:.3f}s"
            + (f"  OD {od_size}x{od_size} {results[f'od_{od_size}x{od_size}_{label}']:.2f}s" if edges <= 10_000 else ""),
            flush=True,
        )
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gate", action="store_true", help="exit non-zero when a timing exceeds its budget")
    parser.add_argument("--quick", action="store_true", help="skip the 100k network")
    args = parser.parse_args(argv)
    sizes = (1_000, 10_000) if args.quick else (1_000, 10_000, 100_000)
    results = run(sizes)
    if not args.gate:
        return 0
    failed = [
        f"{key}: {results[key]:.2f}s > {budget:.2f}s"
        for key, budget in BUDGETS.items()
        if key in results and results[key] > budget
    ]
    for line in failed:
        print("OVER BUDGET", line)
    print("benchmark gate:", "FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
