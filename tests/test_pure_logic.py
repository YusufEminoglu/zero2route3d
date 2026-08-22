"""Unit tests for 02Route 3D pure Python algorithms and kinematics."""
import math
import unittest

from zero2route3d.core import (
    EnvironmentalSurfaceSampler,
    MCDAWeights,
    MobilityProfile,
    NetworkSourceManager,
    PROFILES,
    RoadSegment,
    RoutingEngine3D,
    Waypoint,
    compute_route_statistics,
    cyclist_speed,
    densify_3d_linestring,
    get_profile,
    haversine_distance_2d,
    haversine_distance_3d,
    minetti_energy_cost,
    scooter_speed,
    tobler_walking_speed,
    vehicle_free_flow_speed,
)


class TestKinematics(unittest.TestCase):
    """Test geodesic distance and biomechanical equations."""

    def test_haversine_distances(self):
        p1 = (27.1287, 38.4189, 10.0)
        p2 = (27.1400, 38.4250, 45.0)
        d2d = haversine_distance_2d(p1, p2)
        d3d = haversine_distance_3d(p1, p2)
        self.assertGreater(d2d, 1000.0)
        self.assertGreater(d3d, d2d)

    def test_tobler_hiking_speed(self):
        # Flat surface (0% slope)
        flat_spd = tobler_walking_speed(0.0, base_speed_kmh=5.0)
        self.assertAlmostEqual(flat_spd, 5.0, delta=0.5)

        # Gentle downhill (-5% slope) should be fastest
        downhill_spd = tobler_walking_speed(-0.05, base_speed_kmh=5.0)
        self.assertGreaterEqual(downhill_spd, flat_spd)

        # Steep uphill (+20% slope) should be significantly slower
        uphill_spd = tobler_walking_speed(0.20, base_speed_kmh=5.0)
        self.assertLess(uphill_spd, flat_spd * 0.7)

    def test_minetti_energy_cost(self):
        joules_flat, kcal_flat = minetti_energy_cost(0.0, mass_kg=70.0, distance_m=100.0)
        joules_up, kcal_up = minetti_energy_cost(0.15, mass_kg=70.0, distance_m=100.0)
        self.assertGreater(kcal_flat, 0.0)
        self.assertGreater(kcal_up, kcal_flat * 2.0)

    def test_active_and_vehicle_speeds(self):
        bike_flat = cyclist_speed(0.0, base_speed_kmh=18.0)
        bike_up = cyclist_speed(0.10, base_speed_kmh=18.0)
        self.assertLess(bike_up, bike_flat)

        scooter_flat = scooter_speed(0.0, base_speed_kmh=20.0)
        scooter_steep = scooter_speed(0.15, base_speed_kmh=20.0)
        self.assertEqual(scooter_steep, 4.0)  # motor cutoff

        car_h1 = vehicle_free_flow_speed(1, lanes=4)
        car_h4 = vehicle_free_flow_speed(4, lanes=2)
        self.assertGreater(car_h1, car_h4)


class TestMobilityProfiles(unittest.TestCase):
    """Test mobility profile catalog and resistance calculations."""

    def test_profile_catalog(self):
        self.assertIn("adult", PROFILES)
        self.assertIn("senior", PROFILES)
        self.assertIn("wheelchair", PROFILES)
        self.assertIn("stroller", PROFILES)
        self.assertIn("truck", PROFILES)

        p_wheel = get_profile("wheelchair")
        self.assertFalse(p_wheel.stair_allowed)
        self.assertEqual(p_wheel.max_slope_pct, 5.0)

    def test_edge_resistance_constraints(self):
        p_stroller = get_profile("stroller")
        # Stairs should be impassable for stroller
        stair_cost = p_stroller.calculate_edge_resistance(100.0, slope_pct=1.0, is_steps=True)
        self.assertEqual(stair_cost, float("inf"))

        # Flat smooth asphalt should have finite normal cost
        normal_cost = p_stroller.calculate_edge_resistance(100.0, slope_pct=1.0, is_steps=False)
        self.assertLess(normal_cost, 300.0)


class TestRoutingEngine(unittest.TestCase):
    """Test A* routing on synthetic and grid networks."""

    def setUp(self):
        self.net_mgr = NetworkSourceManager()
        self.bbox = (27.12, 38.41, 27.16, 38.44)
        self.grid_segs = self.net_mgr.generate_synthetic_grid(self.bbox, grid_steps=6)
        self.sampler = EnvironmentalSurfaceSampler()
        self.engine = RoutingEngine3D(sampler=self.sampler, weights=MCDAWeights())
        self.engine.build_graph(self.grid_segs)

    def test_routing_success(self):
        w1 = Waypoint(27.125, 38.415)
        w2 = Waypoint(27.155, 38.435)
        res = self.engine.calculate_route([w1, w2], profile_key="senior")

        self.assertTrue(res.is_network_matched)
        self.assertGreater(len(res.coordinates_3d), 3)
        self.assertGreater(res.statistics.total_distance_m, 1000.0)
        self.assertGreater(res.statistics.total_duration_min, 5.0)

        # Check GeoJSON export
        geojson = res.to_geojson_feature()
        self.assertEqual(geojson["type"], "Feature")
        self.assertEqual(geojson["geometry"]["type"], "LineString")
        self.assertEqual(len(geojson["geometry"]["coordinates"][0]), 3)

        # Check GPX export
        gpx = res.to_gpx()
        self.assertIn("<gpx", gpx)
        self.assertIn("<trkpt", gpx)

    def test_densification_and_stats(self):
        coords = [(27.12, 38.41, 10.0), (27.13, 38.42, 40.0)]
        dense = densify_3d_linestring(coords, sample_interval_m=10.0)
        self.assertGreater(len(dense), 10)

        stats = compute_route_statistics(dense, get_profile("adult"))
        self.assertGreater(stats.elevation_gain_m, 20.0)
        self.assertGreater(stats.total_calories_kcal, 0.0)


if __name__ == "__main__":
    unittest.main()
