"""Comprehensive unit tests for 02Route 3D pure analytical and kinematic logic."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zero2route3d.core.ahp_engine import AHPEngine
from zero2route3d.core.html_bundler import StandaloneHtmlBundler
from zero2route3d.core.isochrone_engine import IsochroneEngine3D
from zero2route3d.core.kinematics import (
    aerodynamic_drag_power,
    cyclist_speed,
    haversine_distance_2d,
    haversine_distance_3d,
    minetti_energy_cost,
    rolling_resistance_force,
    scooter_speed,
    senior_fatigue_decay,
    solar_irradiance_aspect_factor,
    tobler_walking_speed,
    universal_thermal_comfort_utci,
    vehicle_free_flow_speed,
)
from zero2route3d.core.mobility_profiles import (
    PROFILES,
    MobilityProfile,
    get_profile,
    list_profile_keys,
)
from zero2route3d.core.multimodal import MultiModalRouter
from zero2route3d.core.network_source import NetworkSourceManager
from zero2route3d.core.profile_stats import (
    compute_route_statistics,
    densify_3d_linestring,
    generate_cue_sheet,
    smooth_elevation_series,
)
from zero2route3d.core.routing_engine import RouteResult3D, RoutingEngine3D, Waypoint
from zero2route3d.core.tsp_solver import solve_tsp_order


class TestRoute3DPureLogic(unittest.TestCase):
    """Test suite covering core physics, graph routing, AHP, TSP, and multimodal logic."""

    def test_haversine_2d_and_3d(self) -> None:
        p1 = (27.1428, 38.4237, 10.0)
        p2 = (27.1438, 38.4237, 25.0)
        d2 = haversine_distance_2d(p1, p2)
        d3 = haversine_distance_3d(p1, p2)
        self.assertGreater(d2, 70.0)
        self.assertGreater(d3, d2)
        self.assertAlmostEqual(d3, (d2**2 + 15.0**2)**0.5, places=1)

    def test_tobler_and_kinematics(self) -> None:
        flat_spd = tobler_walking_speed(0.0, base_speed_kmh=5.0)
        uphill_spd = tobler_walking_speed(0.15, base_speed_kmh=5.0)
        downhill_spd = tobler_walking_speed(-0.05, base_speed_kmh=5.0)
        self.assertGreater(flat_spd, uphill_spd)
        self.assertGreater(downhill_spd, uphill_spd)

    def test_minetti_energy(self) -> None:
        _j_flat, kcal_flat = minetti_energy_cost(0.0, mass_kg=70.0, distance_m=1000.0)
        _j_up, kcal_up = minetti_energy_cost(0.10, mass_kg=70.0, distance_m=1000.0)
        self.assertGreater(kcal_up, kcal_flat)
        self.assertGreater(kcal_flat, 10.0)

    def test_aerodynamic_and_rolling_resistance(self) -> None:
        drag = aerodynamic_drag_power(velocity_kmh=25.0)
        self.assertGreater(drag, 10.0)
        f_roll = rolling_resistance_force(mass_kg=85.0, slope_fraction=0.05, surface_type="asphalt")
        self.assertGreater(f_roll, 2.0)

    def test_senior_fatigue_and_utci(self) -> None:
        fatigue = senior_fatigue_decay(distance_m=4000.0, accumulated_climb_m=150.0)
        self.assertLess(fatigue, 0.9)
        self.assertGreater(fatigue, 0.4)

        stress_score = universal_thermal_comfort_utci(temp_c=34.0, mean_radiant_temp_c=45.0)
        self.assertGreater(stress_score, 0.5)

    def test_ahp_pairwise_matrix(self) -> None:
        engine = AHPEngine(["slope", "heat", "green"])
        engine.set_pairwise_comparison("slope", "heat", 3.0)
        engine.set_pairwise_comparison("slope", "green", 2.0)
        engine.set_pairwise_comparison("heat", "green", 0.5)
        res = engine.calculate()
        self.assertTrue(res.is_consistent)
        self.assertLess(res.consistency_ratio, 0.10)
        self.assertGreater(res.weights["slope"], res.weights["heat"])

    def test_tsp_solver(self) -> None:
        pts = [
            (27.0, 38.0, 0.0),
            (27.5, 38.0, 0.0),
            (27.2, 38.0, 0.0),
            (27.9, 38.0, 0.0),
        ]
        order = solve_tsp_order(pts, fix_start=True, fix_end=True)
        self.assertEqual(order[0], 0)
        self.assertEqual(order[-1], 3)
        self.assertEqual(order[1], 2)
        self.assertEqual(order[2], 1)

    def test_profiles_catalog(self) -> None:
        keys = list_profile_keys()
        self.assertIn("adult", keys)
        self.assertIn("wheelchair", keys)
        self.assertIn("stroller", keys)
        self.assertIn("truck", keys)
        self.assertIn("paramedic", keys)

        wheelchair = get_profile("wheelchair")
        self.assertFalse(wheelchair.stair_allowed)
        self.assertLessEqual(wheelchair.max_slope_pct, 6.0)

    def test_densification_and_smoothing(self) -> None:
        coords = [(27.0, 38.0, 10.0), (27.01, 38.0, 20.0)]
        densified = densify_3d_linestring(coords, sample_interval_m=10.0)
        self.assertGreater(len(densified), 20)

        elevs = [10.0, 12.0, 50.0, 14.0, 15.0]
        smoothed = smooth_elevation_series(elevs, window_size=3)
        self.assertLess(smoothed[2], 50.0)

    def test_cue_sheet_generation(self) -> None:
        coords = [
            (27.000, 38.000, 10.0),
            (27.005, 38.000, 12.0),
            (27.005, 38.005, 14.0),
            (27.010, 38.005, 15.0),
        ]
        cues = generate_cue_sheet(coords, get_profile("adult"))
        self.assertGreaterEqual(len(cues), 2)
        self.assertEqual(cues[0].direction, "depart")
        self.assertEqual(cues[-1].direction, "arrive")

    def test_routing_graph_and_od_matrix(self) -> None:
        net_mgr = NetworkSourceManager()
        bbox = (27.10, 38.40, 27.15, 38.45)
        segments = net_mgr.generate_synthetic_grid(bbox, grid_steps=6)

        engine = RoutingEngine3D()
        engine.build_graph(segments)

        w1 = Waypoint(lon=27.11, lat=38.41, name="Orig1")
        w2 = Waypoint(lon=27.14, lat=38.44, name="Dest1")
        res = engine.calculate_route([w1, w2], profile_key="adult")

        self.assertTrue(res.is_network_matched)
        self.assertGreater(len(res.coordinates_3d), 2)
        self.assertGreater(res.statistics.total_distance_m, 100.0)

        matrix = engine.calculate_od_matrix([w1], [w2], profile_key="adult")
        self.assertEqual(len(matrix), 1)
        self.assertGreater(matrix[0]["distance_m"], 100.0)

    def test_multimodal_router(self) -> None:
        net_mgr = NetworkSourceManager()
        bbox = (27.10, 38.40, 27.15, 38.45)
        segments = net_mgr.generate_synthetic_grid(bbox, grid_steps=6)
        engine = RoutingEngine3D()
        engine.build_graph(segments)

        router = MultiModalRouter(engine)
        w_orig = Waypoint(lon=27.11, lat=38.41, name="Home")
        w_dest = Waypoint(lon=27.14, lat=38.44, name="Office")
        hubs = [
            Waypoint(lon=27.12, lat=38.42, name="Station 1"),
            Waypoint(lon=27.13, lat=38.43, name="Station 2"),
        ]

        journey = router.calculate_multimodal_trip(w_orig, w_dest, hubs, access_mode="adult", main_mode="bicycle", egress_mode="adult")
        self.assertEqual(len(journey.legs), 3)
        self.assertGreater(journey.total_distance_km, 0.5)
        self.assertGreater(journey.transfer_count, 1)

    def test_isochrone_engine(self) -> None:
        net_mgr = NetworkSourceManager()
        bbox = (27.10, 38.40, 27.15, 38.45)
        segments = net_mgr.generate_synthetic_grid(bbox, grid_steps=6)
        engine = RoutingEngine3D()
        engine.build_graph(segments)

        iso_engine = IsochroneEngine3D(engine)
        origin = Waypoint(lon=27.12, lat=38.42, name="Center")
        iso_res = iso_engine.compute_isochrones(origin, profile_key="adult", time_intervals_min=(5.0, 10.0, 15.0))
        self.assertEqual(len(iso_res.bands), 3)
        self.assertGreater(iso_res.total_reachable_nodes, 0)

    def test_standalone_html_bundler(self) -> None:
        web_dir = Path(__file__).resolve().parent.parent / "web"
        bundler = StandaloneHtmlBundler(web_dir)

        profile = get_profile("adult")
        coords = [(27.14, 38.42, 10.0), (27.15, 38.43, 20.0)]
        stats = compute_route_statistics(coords, profile)
        res = RouteResult3D(
            coordinates_3d=coords,
            statistics=stats,
            profile=profile,
            waypoints=[Waypoint(27.14, 38.42), Waypoint(27.15, 38.43)],
        )

        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            bundler.export_standalone_html(res, tmp_path)
            self.assertTrue(tmp_path.exists())
            content = tmp_path.read_text(encoding="utf-8")
            self.assertIn("02Route 3D", content)
            self.assertIn("canvasContainer", content)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


if __name__ == "__main__":
    unittest.main()
