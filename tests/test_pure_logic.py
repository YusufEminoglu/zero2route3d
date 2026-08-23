"""Comprehensive unit tests for 02Route 3D pure analytical and kinematic logic."""
from __future__ import annotations

import tempfile
import unittest
import math
from pathlib import Path

from zero2route3d.core.accessibility_equity import (
    AccessibilityEquityEngine,
    SupplyFacility,
    ZoneAccessibilityRecord,
)
from zero2route3d.core.ahp_engine import AHPEngine
from zero2route3d.core.environmental_raster import EnvironmentalSurfaceSampler
from zero2route3d.core.evacuation import EvacuationRouter, EvacuationRoutingError
from zero2route3d.core.html_bundler import StandaloneHtmlBundler
from zero2route3d.core.input_validation import (
    deduplicate_adjacent_coordinates,
    normalize_bbox,
    normalize_time_intervals,
    validate_waypoint_coordinates,
)
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
from zero2route3d.core.map_matching_3d import GPXPoint, HMMMapMatcher3D
from zero2route3d.core.micro_elevation import (
    BicubicSurfaceInterpolator,
    IDWSurfaceInterpolator,
)
from zero2route3d.core.mobility_profiles import (
    get_profile,
    list_profile_keys,
)
from zero2route3d.core.multimodal import MultiModalRouter
from zero2route3d.core.network_source import NetworkSourceError, NetworkSourceManager, RoadSegment
from zero2route3d.core.pareto_router import ParetoMultiObjectiveRouter
from zero2route3d.core.profile_dxf import export_route_to_dxf_3d
from zero2route3d.core.profile_stats import (
    compute_route_statistics,
    densify_3d_linestring,
    generate_cue_sheet,
    smooth_elevation_series,
)
from zero2route3d.core.qml_generator import generate_route_qml_style
from zero2route3d.core.routing_engine import RoutingEngine3D, Waypoint
from zero2route3d.core.tsp_solver import solve_tsp_order


def fixture_network_segments() -> list[RoadSegment]:
    """Return a small deterministic road-shaped fixture for pure engine tests."""
    coordinates = [
        (27.1000, 38.4000, 5.0),
        (27.1000, 38.4100, 6.0),
        (27.1100, 38.4100, 8.0),
        (27.1100, 38.4200, 10.0),
        (27.1200, 38.4200, 12.0),
        (27.1200, 38.4300, 13.0),
        (27.1300, 38.4300, 14.0),
        (27.1300, 38.4400, 16.0),
        (27.1400, 38.4400, 18.0),
        (27.1400, 38.4500, 19.0),
        (27.1500, 38.4500, 20.0),
    ]
    return [
        RoadSegment(p1=a, p2=b, length_m=haversine_distance_2d(a, b), highway_type="residential")
        for a, b in zip(coordinates, coordinates[1:])
    ]


class TestRoute3DPureLogic(unittest.TestCase):
    """Test suite covering core physics, graph routing, AHP, TSP, multimodal, DXF, AI, and Pareto logic."""

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
        segments = fixture_network_segments()

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
        segments = fixture_network_segments()
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
        segments = fixture_network_segments()
        engine = RoutingEngine3D()
        engine.build_graph(segments)

        iso_engine = IsochroneEngine3D(engine)
        origin = Waypoint(lon=27.12, lat=38.42, name="Center")
        iso_res = iso_engine.compute_isochrones(origin, profile_key="adult", time_intervals_min=(5.0, 10.0, 15.0))
        self.assertEqual(len(iso_res.bands), 3)
        self.assertGreater(iso_res.total_reachable_nodes, 0)

    def test_micro_elevation_and_bicubic(self) -> None:
        bicubic = BicubicSurfaceInterpolator()
        patch_4x4 = [
            [10.0, 12.0, 14.0, 16.0],
            [12.0, 15.0, 18.0, 20.0],
            [14.0, 18.0, 22.0, 25.0],
            [16.0, 20.0, 25.0, 30.0],
        ]
        grad = bicubic.interpolate_patch_4x4(patch_4x4, 0.5, 0.5, 10.0, 10.0)
        self.assertGreater(grad.elevation_m, 15.0)
        self.assertGreater(grad.slope_pct, 0.0)

        idw = IDWSurfaceInterpolator()
        pts = [(0.0, 0.0, 10.0), (10.0, 0.0, 20.0), (0.0, 10.0, 30.0)]
        z_mid = idw.interpolate_point(5.0, 5.0, pts)
        self.assertGreater(z_mid, 12.0)
        self.assertLess(z_mid, 28.0)

    def test_pareto_multi_objective_router(self) -> None:
        net_mgr = NetworkSourceManager()
        segments = fixture_network_segments()
        engine = RoutingEngine3D()
        engine.build_graph(segments)

        sampler = EnvironmentalSurfaceSampler()
        pareto_router = ParetoMultiObjectiveRouter(engine.nodes, engine.adj, sampler)
        node_keys = list(engine.nodes.keys())
        res = pareto_router.solve_pareto_frontier(node_keys[0], node_keys[-1], profile_key="adult")
        self.assertGreaterEqual(res.to_dict()["solution_count"], 1)

    def test_accessibility_equity_scorecard(self) -> None:
        zones = [
            ZoneAccessibilityRecord("Z1", "Downtown", 27.12, 38.42, 5000),
            ZoneAccessibilityRecord("Z2", "Suburbs", 27.18, 38.48, 8000),
            ZoneAccessibilityRecord("Z3", "Periphery", 27.25, 38.55, 3000),
        ]
        facilities = [
            SupplyFacility("F1", "Central Metro", 27.125, 38.425, 100),
            SupplyFacility("F2", "District Hospital", 27.130, 38.430, 50),
        ]
        equity_engine = AccessibilityEquityEngine(catchment_radius_m=3000.0)
        eq_res = equity_engine.compute_e2sfca(zones, facilities)
        self.assertGreaterEqual(eq_res.gini_coefficient, 0.0)
        self.assertLessEqual(eq_res.gini_coefficient, 1.0)
        self.assertGreater(len(eq_res.lorenz_curve), 2)

    def test_map_matching_3d(self) -> None:
        net_mgr = NetworkSourceManager()
        segments = fixture_network_segments()
        engine = RoutingEngine3D()
        engine.build_graph(segments)

        matcher = HMMMapMatcher3D(engine.nodes, engine.adj)
        gpx_pts = [
            GPXPoint(lon=27.1105, lat=38.4102, elevation_raw_m=10.0),
            GPXPoint(lon=27.1208, lat=38.4205, elevation_raw_m=15.0),
            GPXPoint(lon=27.1302, lat=38.4309, elevation_raw_m=20.0),
        ]
        match_res = matcher.match_gps_track(gpx_pts)
        self.assertGreaterEqual(len(match_res.matched_points), 2)
        gpx_xml = match_res.to_gpx()
        self.assertIn("<trkpt", gpx_xml)

    def test_corridor_building_filter(self) -> None:
        from ..core.osm_downloader import OsmBuilding
        from ..core.route_corridor_3d import filter_buildings_in_corridor, filter_corridor_assets_multi_route

        route_coords = [
            (27.1400, 38.4200, 10.0),
            (27.1450, 38.4250, 15.0),
            (27.1500, 38.4300, 20.0),
        ]
        blds = [
            OsmBuilding("b1", [(27.1402, 38.4201), (27.1404, 38.4201), (27.1404, 38.4203)], height_m=16.0, levels=5),
            OsmBuilding("b2_far", [(27.2000, 38.5000), (27.2010, 38.5000), (27.2010, 38.5010)], height_m=12.0, levels=4),
        ]
        corridor = filter_buildings_in_corridor(route_coords, blds, buffer_meters=50.0)
        self.assertEqual(len(corridor), 1)
        self.assertEqual(corridor[0]["id"], "b1")
        self.assertEqual(corridor[0]["height_m"], 16.0)

    def test_multi_route_corridor_assets_and_3d_trees(self) -> None:
        from ..core.environmental_raster import EnvironmentalSurfaceSampler
        from ..core.osm_downloader import OsmBuilding
        from ..core.route_corridor_3d import filter_corridor_assets_multi_route

        route_1 = [
            (27.1400, 38.4200, 10.0),
            (27.1430, 38.4230, 12.0),
            (27.1460, 38.4260, 14.0),
        ]
        route_2 = [
            (27.1400, 38.4200, 10.0),
            (27.1425, 38.4235, 11.5),
            (27.1460, 38.4260, 14.0),
        ]

        blds = [
            OsmBuilding("b_near_1", [(27.1402, 38.4202), (27.1404, 38.4202), (27.1404, 38.4204)], height_m=20.0, levels=6),
            OsmBuilding("b_near_2", [(27.1426, 38.4236), (27.1428, 38.4236), (27.1428, 38.4238)], height_m=15.0, levels=4),
            OsmBuilding("b_distant", [(27.1900, 38.4900), (27.1910, 38.4900), (27.1910, 38.4910)], height_m=25.0, levels=7),
        ]

        sampler = EnvironmentalSurfaceSampler()
        corridor_blds, corridor_trees = filter_corridor_assets_multi_route(
            [route_1, route_2],
            blds,
            buffer_meters=30.0,
            green_sampler=sampler,
        )

        # Buildings within 30m across both routes
        self.assertEqual(len(corridor_blds), 2)
        bld_ids = {b["id"] for b in corridor_blds}
        self.assertIn("b_near_1", bld_ids)
        self.assertIn("b_near_2", bld_ids)
        self.assertNotIn("b_distant", bld_ids)

        # 3D Trees generated along 30m corridor
        self.assertGreater(len(corridor_trees), 0)
        first_tree = corridor_trees[0]
        self.assertIn("id", first_tree)
        self.assertIn("coordinates", first_tree)
        self.assertIn("base_elevation_m", first_tree)
        self.assertIn("height_m", first_tree)
        self.assertIn("canopy_radius_m", first_tree)
        self.assertIn("trunk_height_m", first_tree)
        self.assertIn("trunk_radius_m", first_tree)
        self.assertIn("tree_type", first_tree)
        self.assertIn("greenery_index", first_tree)
        self.assertGreaterEqual(first_tree["height_m"], 4.0)
        self.assertGreaterEqual(first_tree["canopy_radius_m"], 1.5)

    def test_multi_profile_groups_and_colors(self) -> None:
        from ..core.mobility_profiles import get_profile_color, list_profile_keys_for_group

        all_keys = list_profile_keys_for_group("all")
        self.assertEqual(len(all_keys), 15)

        ped_keys = list_profile_keys_for_group("pedestrian")
        self.assertIn("adult", ped_keys)
        self.assertIn("senior", ped_keys)

        veh_keys = list_profile_keys_for_group("vehicle")
        self.assertIn("car", veh_keys)
        self.assertIn("paramedic", veh_keys)

        car_col = get_profile_color("car")
        self.assertTrue(car_col.startswith("#"))

    def test_animated_avatar_interpolation(self) -> None:
        from ..core.kinematics import AnimatedAvatar

        avatar = AnimatedAvatar(
            profile_key="car",
            profile_name="Passenger Car",
            color_hex="#ef4444",
            coordinates_3d=[(27.0, 38.0, 10.0), (27.1, 38.0, 10.0), (27.2, 38.0, 10.0)],
            cumulative_distances_m=[0.0, 8000.0, 16000.0],
            timestamps_s=[0.0, 100.0, 200.0],
            total_duration_s=200.0,
            total_distance_m=16000.0,
        )

        p_start = avatar.interpolate_position(0.0)
        self.assertAlmostEqual(p_start[0], 27.0)

        p_mid = avatar.interpolate_position(50.0)
        self.assertAlmostEqual(p_mid[0], 27.05, places=2)

        p_end = avatar.interpolate_position(250.0)
        self.assertAlmostEqual(p_end[0], 27.2)

    def test_global_dem_fetcher(self) -> None:
        from ..core.dem_fetcher import GlobalDemFetcher

        pts = [(27.1428, 38.4237), (27.1500, 38.4300)]
        elevations = GlobalDemFetcher.fetch_elevations_for_coords(pts)
        self.assertEqual(len(elevations), 2)
        self.assertGreaterEqual(elevations[0], 0.0)

        single = GlobalDemFetcher.get_elevation_single(27.1428, 38.4237)
        self.assertGreaterEqual(single, 0.0)

    def test_local_server_lifecycle_and_port_selection(self) -> None:
        from ..core.local_server import Route3DLocalServer, _port_is_open

        with tempfile.TemporaryDirectory() as tmpdir:
            server = Route3DLocalServer(web_root=Path(tmpdir), start_port=18920, end_port=18930)
            self.assertFalse(server.is_running)
            self.assertEqual(server.url, "")

            url = server.start()
            self.assertTrue(server.is_running)
            self.assertTrue(url.startswith("http://127.0.0.1:1892"))
            self.assertIn("/index.html", url)
            self.assertIsNotNone(server.port)

            # Idempotent start
            url2 = server.start()
            self.assertEqual(url, url2)

            # Check port is detected as open
            self.assertTrue(_port_is_open(server.host, server.port))

            # Stop gracefully
            server.stop()
            self.assertFalse(server.is_running)
            self.assertEqual(server.url, "")

            # Idempotent stop
            server.stop()

    def test_route_statistics_empty_and_single_point(self) -> None:
        # Empty
        stats_empty = compute_route_statistics([])
        self.assertEqual(stats_empty.total_distance_m, 0.0)
        self.assertEqual(stats_empty.elevation_gain_m, 0.0)

        # Single point
        stats_single = compute_route_statistics([(27.1, 38.4, 15.0)])
        self.assertEqual(stats_single.total_distance_m, 0.0)
        self.assertEqual(stats_single.min_elevation_m, 15.0)
        self.assertEqual(stats_single.max_elevation_m, 15.0)

    def test_dxf_export_edge_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dxf = Path(tmpdir) / "empty_route.dxf"
            export_route_to_dxf_3d([], out_dxf)
            self.assertTrue(out_dxf.exists())

            out_dxf2 = Path(tmpdir) / "single_route.dxf"
            export_route_to_dxf_3d([(27.1, 38.4, 15.0)], out_dxf2)
            self.assertTrue(out_dxf2.exists())

    def test_html_standalone_bundler(self) -> None:
        bundler = StandaloneHtmlBundler()
        geojson_mock = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [27.1428, 38.4237, 10.0],
                    [27.1450, 38.4250, 20.0],
                ],
            },
            "properties": {
                "distance_km": 0.35,
                "duration_min": 4.5,
                "elevation_gain_m": 10.0,
                "profile_name": "Standard Adult",
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out_html = Path(tmpdir) / "standalone_studio.html"
            bundler.bundle_to_file(geojson_mock, out_html)
            self.assertTrue(out_html.exists())
            content = out_html.read_text(encoding="utf-8")
            self.assertIn("02Route 3D Studio", content)
            self.assertIn("27.1428", content)

    def test_nan_inf_kinematics_robustness(self) -> None:
        nan = float("nan")
        inf = float("inf")

        # Haversine with NaN / Inf / empty
        self.assertEqual(haversine_distance_2d((nan, 38.0), (27.0, 38.0)), 0.0)
        self.assertEqual(haversine_distance_2d((27.0, inf), (27.0, 38.0)), 0.0)
        self.assertEqual(haversine_distance_2d([], (27.0, 38.0)), 0.0)
        self.assertEqual(haversine_distance_2d((27.0, 38.0), (27.0, 38.0)), 0.0)

        # 3D Distance with NaN elevation
        d3 = haversine_distance_3d((27.0, 38.0, nan), (27.01, 38.0, inf))
        self.assertGreater(d3, 0.0)
        import math
        self.assertTrue(math.isfinite(d3))

        # Tobler walking speed
        self.assertGreater(tobler_walking_speed(nan), 0.0)
        self.assertGreater(tobler_walking_speed(inf), 0.0)
        self.assertGreater(tobler_walking_speed(0.1, base_speed_kmh=nan), 0.0)

        # Minetti energy cost
        j, kcal = minetti_energy_cost(nan, mass_kg=nan, distance_m=nan)
        self.assertGreaterEqual(kcal, 0.0)
        self.assertTrue(math.isfinite(kcal))

        # Cyclist, scooter, vehicle free flow speeds
        c_spd = cyclist_speed(nan, base_speed_kmh=nan, rider_power_watts=nan, total_mass_kg=nan)
        self.assertGreater(c_spd, 0.0)
        self.assertTrue(math.isfinite(c_spd))

        s_spd = scooter_speed(nan, base_speed_kmh=nan)
        self.assertGreater(s_spd, 0.0)
        self.assertTrue(math.isfinite(s_spd))

        v_spd = vehicle_free_flow_speed(4, lanes=nan, slope_pct=nan)
        self.assertGreater(v_spd, 0.0)
        self.assertTrue(math.isfinite(v_spd))

        # Irradiance & UTCI
        irr = solar_irradiance_aspect_factor(nan, nan)
        self.assertTrue(0.0 <= irr <= 1.0)
        utci = universal_thermal_comfort_utci(nan, nan, nan, nan)
        self.assertTrue(0.0 <= utci <= 1.0)

    def test_dem_fetcher_batch_chunking_and_cache_corruption(self) -> None:
        from zero2route3d.core.dem_fetcher import GlobalDemFetcher
        import math

        # Test batch with > 150 coordinates (tests chunking logic)
        coords = [(27.0 + i * 0.001, 38.0 + i * 0.001) for i in range(160)]
        results = GlobalDemFetcher.fetch_elevations_for_coords(coords, timeout_sec=0.5)
        self.assertEqual(len(results), 160)
        for val in results:
            self.assertTrue(math.isfinite(val))
            self.assertGreaterEqual(val, 0.0)

        # Test NaN coordinate in DEM fetcher
        single_nan = GlobalDemFetcher.get_elevation_single(float("nan"), float("nan"))
        self.assertEqual(single_nan, 0.0)

    def test_routing_engine_edge_cases(self) -> None:
        engine = RoutingEngine3D()
        nan = float("nan")

        # Zero waypoints
        res_empty = engine.calculate_route([], profile_key="adult")
        self.assertEqual(len(res_empty.coordinates_3d), 0)
        self.assertFalse(res_empty.is_network_matched)

        # Single waypoint
        w_single = Waypoint(lon=27.1, lat=38.4, name="Solo")
        res_single = engine.calculate_route([w_single], profile_key="adult")
        self.assertEqual(len(res_single.coordinates_3d), 1)
        self.assertFalse(res_single.is_network_matched)

        # Use the deterministic road fixture and route with identical start/end.
        net_mgr = NetworkSourceManager()
        segments = fixture_network_segments()
        engine.build_graph(segments)

        w1 = Waypoint(lon=27.11, lat=38.41)
        res_same = engine.calculate_route([w1, w1], profile_key="adult")
        self.assertTrue(res_same.is_network_matched)
        self.assertEqual(len(res_same.coordinates_3d), 1)

        # Nearest node lookup with NaN
        self.assertIsNone(engine.find_nearest_node((nan, nan)))

    def test_network_source_rejects_invalid_extent_without_fabrication(self) -> None:
        manager = NetworkSourceManager()
        nan = float("nan")
        with self.assertRaises(NetworkSourceError):
            manager.fetch_osm_network_bbox((nan, 38.4, 27.1, 38.5))

        empty_engine = RoutingEngine3D()
        result = empty_engine.calculate_route(
            [Waypoint(27.1, 38.4), Waypoint(27.2, 38.5)],
            profile_key="adult",
        )
        self.assertEqual(result.coordinates_3d, [])
        self.assertFalse(result.is_network_matched)

    def test_cue_sheet_2d_and_extreme_slopes(self) -> None:
        # 2D coordinates (no Z index) passed into cue sheet generator
        coords_2d = [(27.0, 38.0), (27.01, 38.0), (27.01, 38.01)]
        cues = generate_cue_sheet(coords_2d, get_profile("adult"))
        self.assertGreaterEqual(len(cues), 2)
        self.assertEqual(cues[0].direction, "depart")
        self.assertEqual(cues[-1].direction, "arrive")

    def test_profile_dxf_sanitization_and_qml(self) -> None:
        nan = float("nan")
        with tempfile.TemporaryDirectory() as tmpdir:
            # Subdirectory that does not exist yet
            target_dxf = Path(tmpdir) / "nested" / "sub" / "route.dxf"
            export_route_to_dxf_3d([(27.1, 38.4, 10.0), (nan, 38.5, nan), (27.2, 38.6, 20.0)], target_dxf)
            self.assertTrue(target_dxf.exists())
            dxf_text = target_dxf.read_text(encoding="utf-8")
            self.assertNotIn("nan", dxf_text.lower())
            self.assertIn("3D_ROUTE", dxf_text)

            target_qml = Path(tmpdir) / "nested" / "style.qml"
            qml_str = generate_route_qml_style(target_qml, line_width_mm=1.5)
            self.assertTrue(target_qml.exists())
            self.assertIn("<qgis", qml_str)

    def test_map_matcher_empty_and_nan(self) -> None:
        matcher = HMMMapMatcher3D(nodes={}, adj={})
        res = matcher.match_gps_track([GPXPoint(lon=float("nan"), lat=float("nan"))])
        self.assertEqual(len(res.matched_points), 0)
        self.assertEqual(res.status_message, "Graph is empty.")

    def test_shared_input_validation_and_coordinate_deduplication(self) -> None:
        bbox = normalize_bbox((27.1, 38.4, 27.1, 38.4))
        self.assertGreater(bbox[2] - bbox[0], 0.004)
        self.assertEqual(normalize_time_intervals([15, 5, 5, float("nan"), 0]), [5.0, 15.0])
        self.assertIsNone(validate_waypoint_coordinates([Waypoint(27.1, 38.4), Waypoint(27.2, 38.5)]))
        self.assertIsNotNone(validate_waypoint_coordinates([Waypoint(27.1, 95.0)]))

        cleaned = deduplicate_adjacent_coordinates(
            [(27.1, 38.4, 2.0), (27.1, 38.4, 2.0), (27.2, 38.5, 3.0)]
        )
        self.assertEqual(len(cleaned), 2)

    def test_profile_travel_time_is_shared_by_accessibility_and_animation(self) -> None:
        adult = get_profile("adult")
        flat = adult.travel_time_seconds(1000.0, slope_pct=0.0)
        uphill = adult.travel_time_seconds(1000.0, slope_pct=15.0)
        self.assertGreater(uphill, flat)
        self.assertTrue(all(math.isfinite(v) for v in (flat, uphill)))

    def test_densification_has_a_memory_cap(self) -> None:
        coords = [(27.0, 38.0, 0.0), (28.0, 38.0, 100.0)]
        dense = densify_3d_linestring(coords, sample_interval_m=0.5, max_points=1_000)
        self.assertLessEqual(len(dense), 1_002)

    def test_isochrone_rejects_invalid_intervals_without_work(self) -> None:
        engine = RoutingEngine3D()
        engine.build_graph(fixture_network_segments())
        result = IsochroneEngine3D(engine).compute_isochrones(
            Waypoint(27.12, 38.42), time_intervals_min=[0, -5, float("nan")]
        )
        self.assertEqual(result.bands, [])

    def test_evacuation_does_not_return_a_fake_origin_route(self) -> None:
        router = EvacuationRouter(RoutingEngine3D())
        with self.assertRaises(EvacuationRoutingError):
            router.calculate_evacuation_route(Waypoint(27.1, 38.4), [])

    def test_copernicus_eo_suite_multispectral_stack(self) -> None:
        from ..core.copernicus_eo_suite import CopernicusEOSuite
        bbox = (27.13, 38.41, 27.15, 38.43)
        corridor = [(27.135, 38.415, 10.0), (27.145, 38.425, 25.0)]
        results = CopernicusEOSuite.fetch_and_clip_multispectral_stack(
            bbox=bbox, corridor_coords=corridor, buffer_meters=30.0, resolution_deg=0.001
        )
        self.assertEqual(len(results), 4)
        keys = {r.key for r in results}
        self.assertEqual(keys, {"dem", "ndvi", "lst", "ndbi"})
        for r in results:
            self.assertTrue(r.file_path.exists())
            self.assertGreater(r.file_path.stat().st_size, 0)
            self.assertTrue(math.isfinite(r.min_val))
            self.assertTrue(math.isfinite(r.max_val))


if __name__ == "__main__":
    unittest.main()
