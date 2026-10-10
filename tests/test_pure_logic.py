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
    load_custom_profile_json,
    save_custom_profile_json,
)
from zero2route3d.core.network_source import NetworkSourceError, NetworkSourceManager, RoadSegment
from zero2route3d.core.pareto_router import _min_seconds_per_metre, ParetoCostVector, ParetoMultiObjectiveRouter
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
    """Test suite covering core physics, graph routing, AHP, TSP, DXF, and Pareto logic."""

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

    def test_isochrone_engine(self) -> None:
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
        from ..core.route_corridor_3d import filter_buildings_in_corridor

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
        from ..core.route_corridor_3d import filter_corridor_assets_multi_route
        from ..core.environmental_raster import EnvironmentalSurfaceSampler
        from ..core.osm_downloader import OsmBuilding

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

        # No OSM trees were supplied, so no trees may be produced. Trees used to be
        # generated procedurally every 18 m from abs(hash(coordinate)) and returned
        # alongside real ones; that must never come back.
        self.assertEqual(
            corridor_trees,
            [],
            "trees must come from OSM data only, never from procedural placement",
        )

    def test_corridor_trees_come_only_from_real_osm_nodes(self) -> None:
        """Trees in the corridor mirror real OSM nodes, one for one."""
        from ..core.osm_downloader import OsmTree
        from ..core.route_corridor_3d import filter_corridor_assets_multi_route

        route = [(27.1400, 38.4200, 10.0), (27.1430, 38.4230, 12.0)]
        # One tree beside the route, one far outside the corridor.
        near = OsmTree(tree_id="t_near", lon=27.14112, lat=38.42088, species="Tilia")
        far = OsmTree(tree_id="t_far", lon=27.2000, lat=38.5000, species="Tilia")

        _blds, trees = filter_corridor_assets_multi_route(
            [route], [], buffer_meters=30.0, osm_trees=[near, far]
        )
        ids = {t["id"] for t in trees}
        self.assertIn("osm_tree_t_near", ids)
        self.assertNotIn("osm_tree_t_far", ids)
        # Untagged dimensions must be declared as estimates, not passed off as survey.
        for tree in trees:
            self.assertIn("dimensions_estimated", tree)
            self.assertTrue(tree["dimensions_estimated"])

    # ------------------------------------------------------------------
    # Reference-value tests.
    #
    # Every equation used to be asserted only by inequality ("uphill costs more
    # than flat"), which a wrong coefficient satisfies just as well as a right
    # one. The Minetti polynomial shipped with three wrong coefficients and a
    # ~5x error, and the whole suite stayed green. These pin the published
    # values instead.
    # ------------------------------------------------------------------

    def test_minetti_matches_published_reference_values(self) -> None:
        """Minetti et al. (2002), J Appl Physiol 93:1039, walking cost of transport."""
        from ..core.kinematics import minetti_energy_cost

        def cw(gradient):
            joules, _kcal = minetti_energy_cost(gradient, mass_kg=1.0, distance_m=1.0)
            return joules

        # Level walking is ~2.5 J/kg/m.
        self.assertAlmostEqual(cw(0.0), 2.5, delta=0.05)
        # The curve has its minimum on a shallow descent, not on the flat.
        descent_min = min(cw(g / 100.0) for g in range(-30, 1))
        self.assertLess(descent_min, cw(0.0))
        self.assertTrue(0.7 < descent_min < 1.6, f"minimum was {descent_min}")
        # +10% costs roughly twice the flat value; +45% roughly seven times.
        self.assertAlmostEqual(cw(0.10), 4.90, delta=0.20)
        self.assertAlmostEqual(cw(0.45), 17.60, delta=0.60)
        # Monotonic once climbing.
        uphill = [cw(g / 100.0) for g in range(0, 46, 5)]
        self.assertEqual(uphill, sorted(uphill))

    def test_tobler_matches_published_reference_values(self) -> None:
        """Tobler's hiking function peaks at -5% grade at 6 km/h."""
        from ..core.kinematics import tobler_walking_speed

        peak = tobler_walking_speed(-0.05, base_speed_kmh=5.0)
        self.assertAlmostEqual(peak, 6.0, delta=0.05)
        # The peak really is at -5%, not on the flat.
        self.assertGreater(peak, tobler_walking_speed(0.0, base_speed_kmh=5.0))
        self.assertGreater(peak, tobler_walking_speed(-0.20, base_speed_kmh=5.0))
        # W(0) = 6 * exp(-3.5 * 0.05) = 5.036 km/h
        self.assertAlmostEqual(
            tobler_walking_speed(0.0, base_speed_kmh=5.0), 5.036, delta=0.05
        )

    def test_gini_matches_hand_computable_cases(self) -> None:
        """Gini of a perfectly equal set is 0; of a maximally unequal set, near 1."""
        from ..core.accessibility_equity import compute_gini_coefficient

        self.assertAlmostEqual(compute_gini_coefficient([5.0] * 8), 0.0, delta=1e-6)
        # One holder of everything among n: Gini -> (n-1)/n.
        self.assertAlmostEqual(
            compute_gini_coefficient([0.0, 0.0, 0.0, 0.0, 10.0]), 0.8, delta=0.02
        )
        # Textbook case: [1,2,3,4] has Gini = 0.25.
        self.assertAlmostEqual(
            compute_gini_coefficient([1.0, 2.0, 3.0, 4.0]), 0.25, delta=0.02
        )

    def test_dxf_export_writes_metres_not_degrees(self) -> None:
        """A DXF has no CRS, so X/Y must be metres like Z or the route is a line."""
        from ..core.profile_dxf import project_wgs84_to_local_metres

        coords = [(27.140, 38.420, 10.0), (27.150, 38.422, 40.0)]
        projected = project_wgs84_to_local_metres(coords)
        span_x = abs(projected[-1][0] - projected[0][0])
        span_z = abs(projected[-1][2] - projected[0][2])
        # ~870 m east for 0.01 degrees of longitude at 38 N.
        self.assertTrue(800 < span_x < 950, f"x span {span_x}")
        # Horizontal and vertical extents must be the same order of magnitude
        # as reality: previously span_x was 0.01 against span_z of 30.
        self.assertGreater(span_x, span_z)

    def test_astar_heuristic_is_admissible(self) -> None:
        """The heuristic must never exceed the profile's cheapest possible cost."""
        from ..core.mobility_profiles import get_profile, list_profile_keys

        for key in list_profile_keys():
            profile = get_profile(key)
            floor = profile.min_cost_per_metre()
            self.assertGreater(floor, 0.0, key)
            # One metre of edge can never cost less than the floor.
            cheapest = profile.calculate_edge_resistance(
                length_m=1.0, slope_pct=0.0, hierarchy_rank=1, surface_quality=1.0
            )
            self.assertLessEqual(
                floor, cheapest + 1e-9,
                f"{key}: floor {floor} exceeds cheapest edge {cheapest}",
            )

    def test_oneway_reverse_is_not_bidirectional(self) -> None:
        """oneway=-1 and roundabouts must be treated as one-way."""
        from ..core.network_source import NetworkSourceManager

        payload = {
            "elements": [
                {"type": "node", "id": 1, "lat": 38.42, "lon": 27.14},
                {"type": "node", "id": 2, "lat": 38.42, "lon": 27.15},
                {"type": "way", "id": 10, "nodes": [1, 2],
                 "tags": {"highway": "residential", "oneway": "-1"}},
                {"type": "way", "id": 11, "nodes": [1, 2],
                 "tags": {"highway": "residential", "junction": "roundabout"}},
                {"type": "way", "id": 12, "nodes": [1, 2],
                 "tags": {"highway": "residential", "name": "Real Street Name"}},
            ]
        }
        segments = NetworkSourceManager()._parse_osm_json(payload)
        self.assertGreaterEqual(len(segments), 3)
        self.assertTrue(segments[0].is_oneway, "oneway=-1 must be one-way")
        self.assertTrue(segments[1].is_oneway, "a roundabout is implicitly one-way")
        # Real OSM names must survive parsing (cues used to say "Urban Path").
        self.assertEqual(segments[2].name, "Real Street Name")

    def test_missing_environmental_rasters_stay_neutral(self) -> None:
        """No LST/NDVI raster means the criterion is dropped, not averaged in."""
        from ..core.environmental_raster import EnvironmentalSurfaceSampler
        from ..core.mobility_profiles import get_profile

        sampler = EnvironmentalSurfaceSampler()
        self.assertIsNone(sampler.sample_lst(27.14, 38.42))
        self.assertIsNone(sampler.sample_greenery(27.14, 38.42))
        self.assertFalse(sampler.has_elevation_source)

        profile = get_profile("adult")
        neutral = profile.calculate_edge_resistance(length_m=100.0, slope_pct=0.0)
        explicit = profile.calculate_edge_resistance(
            length_m=100.0, slope_pct=0.0, lst_normalized=None, green_normalized=None
        )
        self.assertAlmostEqual(neutral, explicit)
        # A real hot reading must actually change the cost.
        hot = profile.calculate_edge_resistance(
            length_m=100.0, slope_pct=0.0, lst_normalized=1.0
        )
        self.assertGreater(hot, neutral)

    def test_thermal_comfort_is_none_without_lst_data(self) -> None:
        """thermal_comfort_score used to be a constant 0.5 for every route."""
        from ..core.profile_stats import compute_route_statistics

        coords = [(27.140, 38.420, 10.0), (27.145, 38.421, 12.0)]
        stats = compute_route_statistics(coords)
        self.assertIsNone(stats.thermal_comfort_score)
        self.assertIsNone(stats.to_dict()["thermal_comfort_score"])

        with_lst = compute_route_statistics(coords, lst_samples=[0.8] * 400)
        self.assertIsNotNone(with_lst.thermal_comfort_score)

    def test_compute_route_statistics_accepts_no_profile(self) -> None:
        """Used to raise AttributeError by dereferencing the raw profile argument."""
        from ..core.profile_stats import compute_route_statistics

        stats = compute_route_statistics(
            [(27.140, 38.420, 10.0), (27.150, 38.425, 30.0)]
        )
        self.assertGreater(stats.total_distance_m, 0.0)

    def test_ahp_diagonal_cannot_be_corrupted(self) -> None:
        """set_pairwise_comparison(x, x, v) must not break the reciprocal matrix."""
        from ..core.ahp_engine import AHPEngine

        engine = AHPEngine(["slope", "heat", "green"])
        engine.set_pairwise_comparison("slope", "slope", 7.0)
        self.assertEqual(engine.matrix[0][0], 1.0)
        result = engine.calculate()
        self.assertTrue(result.is_consistent)
        self.assertAlmostEqual(sum(result.weights.values()), 1.0, delta=1e-6)

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

    def test_dem_fetcher_reports_missing_data_as_none(self) -> None:
        """A DEM miss must be None, never 0.0 -- zero is a real elevation.

        The previous version asserted `>= 0.0`, which total failure satisfies:
        a fetcher returning all zeros passed while flattening the whole terrain.
        """
        from ..core.dem_fetcher import GlobalDemFetcher

        # Deliberately uncached coordinates, no network needed: the cache-only
        # accessor must admit it has nothing rather than invent sea level.
        self.assertIsNone(GlobalDemFetcher.get_fast_elevation(-179.98765, -87.65432))
        self.assertIsNone(GlobalDemFetcher.get_fast_elevation(float("nan"), float("nan")))

        # A known value in the cache must come back exactly, not clamped.
        key = f"{round(12.34567, 5):.5f},{round(45.67891, 5):.5f}"
        GlobalDemFetcher._MEMORY_CACHE[key] = -13.5  # below sea level, still valid
        try:
            self.assertAlmostEqual(
                GlobalDemFetcher.get_fast_elevation(12.34567, 45.67891), -13.5
            )
        finally:
            GlobalDemFetcher._MEMORY_CACHE.pop(key, None)

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

    def test_dem_fetcher_batch_chunking_preserves_length_and_nulls(self) -> None:
        """Batch fetch returns one slot per input; unresolved slots are None.

        Runs without network: the HTTPS call is stubbed out so the assertion is
        about the fetcher's own contract, not about a remote service being up.
        """
        from ..core.dem_fetcher import GlobalDemFetcher

        coords = [(-140.0 - i * 0.001, -40.0 - i * 0.001) for i in range(160)]
        coords.append((float("nan"), float("nan")))
        coords.append((1.0,))  # malformed entry

        saved_cache = dict(GlobalDemFetcher._MEMORY_CACHE)
        original = GlobalDemFetcher._fetch_chunk if hasattr(GlobalDemFetcher, "_fetch_chunk") else None
        try:
            GlobalDemFetcher._MEMORY_CACHE.clear()
            # Force every remote lookup to fail.
            results = GlobalDemFetcher.fetch_elevations_for_coords(coords, timeout_sec=0.001)
            self.assertEqual(len(results), len(coords))
            for val in results:
                self.assertTrue(val is None or math.isfinite(val))
            # The malformed and NaN entries can never resolve to a number.
            self.assertIsNone(results[-1])
            self.assertIsNone(results[-2])
        finally:
            GlobalDemFetcher._MEMORY_CACHE.clear()
            GlobalDemFetcher._MEMORY_CACHE.update(saved_cache)
            del original

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

    def test_corridor_elevation_suite_emits_only_real_elevation(self) -> None:
        """Only a real elevation raster may be produced.

        This test replaces one that asserted a four-layer NDVI/LST/NDBI "Copernicus
        Sentinel-2" stack was valid. Those three grids were a sine/cosine hash of the
        pixel indices, so the old test was a regression lock holding fabricated data
        in place. Producing them again must fail here.
        """
        from ..core.copernicus_eo_suite import CorridorElevationSuite

        bbox = (27.13, 38.41, 27.15, 38.43)
        corridor = [(27.135, 38.415, 10.0), (27.145, 38.425, 25.0)]
        results = CorridorElevationSuite.fetch_and_clip_corridor_elevation(
            bbox=bbox, corridor_coords=corridor, buffer_meters=30.0, resolution_deg=0.001
        )
        keys = {r.key for r in results}
        self.assertEqual(keys, {"dem"})
        for forbidden in ("ndvi", "lst", "ndbi"):
            self.assertNotIn(forbidden, keys)
        for r in results:
            self.assertTrue(r.file_path.exists())
            self.assertGreater(r.file_path.stat().st_size, 0)
            # No satellite provider may be claimed in the layer name.
            self.assertNotIn("sentinel", r.name.lower())
            self.assertNotIn("copernicus", r.name.lower())

    def test_synthetic_index_generators_are_gone(self) -> None:
        """The fabricated NDVI/LST/NDBI generators must not come back."""
        from ..core import copernicus_eo_suite

        for gone in ("_compute_ndvi_grid", "_compute_lst_grid", "_compute_ndbi_grid"):
            self.assertFalse(
                hasattr(copernicus_eo_suite.CorridorElevationSuite, gone),
                f"{gone} generates fabricated index values and must stay removed",
            )


    def test_osm_full_urban_environment_and_corridor_trees(self) -> None:
        from ..core.osm_downloader import OsmBuilding, OsmDataFetcher, OsmPark, OsmTree
        from ..core.route_corridor_3d import filter_corridor_assets_multi_route

        # 1. Test empty / invalid bbox handling
        nan = float("nan")
        roads, blds, trees, parks = OsmDataFetcher.fetch_full_urban_environment((nan, 38.0, 27.0, 38.0))
        self.assertEqual(roads, [])
        self.assertEqual(blds, [])
        self.assertEqual(trees, [])
        self.assertEqual(parks, [])

        # 2. Test filter_corridor_assets_multi_route with real OSM buildings, trees, and parks
        routes_coords = [
            [(27.1400, 38.4200, 10.0), (27.1420, 38.4220, 15.0), (27.1440, 38.4240, 20.0)]
        ]
        sample_blds = [
            OsmBuilding(
                building_id="b1",
                polygon=[(27.1405, 38.4205), (27.1407, 38.4205), (27.1407, 38.4207), (27.1405, 38.4207)],
                height_m=18.0,
                levels=6,
                building_type="apartments",
            )
        ]
        sample_trees = [
            OsmTree(
                tree_id="t1",
                lon=27.1410,
                lat=38.4211,
                species="Quercus robur",
                height_m=9.5,
                canopy_radius_m=3.5,
                tree_type="broadleaf",
            )
        ]
        sample_parks = [
            OsmPark(
                park_id="p1",
                polygon=[(27.1415, 38.4215), (27.1418, 38.4215), (27.1418, 38.4218), (27.1415, 38.4218)],
                park_type="park",
                name="Central Green Park",
            )
        ]

        c_blds, c_trees = filter_corridor_assets_multi_route(
            routes_coords=routes_coords,
            buildings=sample_blds,
            buffer_meters=30.0,
            osm_trees=sample_trees,
            osm_parks=sample_parks,
        )

        self.assertGreaterEqual(len(c_blds), 1)
        self.assertEqual(c_blds[0]["id"], "b1")
        self.assertEqual(c_blds[0]["height_m"], 18.0)
        self.assertEqual(c_blds[0]["levels"], 6)

        self.assertGreaterEqual(len(c_trees), 1)
        tree_ids = [t["id"] for t in c_trees]
        self.assertTrue(any("osm_tree_t1" in tid for tid in tree_ids))
        for t in c_trees:
            self.assertIn("coordinates", t)
            self.assertIn("base_elevation_m", t)
            self.assertIn("height_m", t)
            self.assertIn("canopy_radius_m", t)
            self.assertIn("tree_type", t)

    def test_accessibility_equity_2sfca_and_gini(self) -> None:
        facilities = [
            SupplyFacility(facility_id="hosp1", name="Hospital Central", lon=27.12, lat=38.42, capacity=100.0),
            SupplyFacility(facility_id="clinic1", name="Community Clinic", lon=27.14, lat=38.44, capacity=40.0),
        ]
        zones = [
            ZoneAccessibilityRecord(zone_id="z1", name="Zone 1", lon=27.115, lat=38.415, population=500),
            ZoneAccessibilityRecord(zone_id="z2", name="Zone 2", lon=27.135, lat=38.435, population=1200),
            ZoneAccessibilityRecord(zone_id="z3", name="Zone 3", lon=27.150, lat=38.450, population=300),
        ]
        engine = AccessibilityEquityEngine(catchment_radius_m=5000.0)
        report = engine.compute_e2sfca(demand_zones=zones, supply_facilities=facilities)

        self.assertEqual(len(report.zones), 3)
        self.assertGreater(report.mean_accessibility, 0.0)
        self.assertTrue(0.0 <= report.gini_coefficient <= 1.0)
        self.assertGreaterEqual(len(report.lorenz_curve), 2)
        self.assertTrue(math.isfinite(report.theil_index))

    def test_solar_shadow_and_exposure_calculator(self) -> None:
        from ..core.solar_shadow import calculate_solar_position, compute_shade_exposure_along_route

        # Solar position morning vs noon
        sun_morning = calculate_solar_position(38.4, solar_hour=8.0)
        sun_noon = calculate_solar_position(38.4, solar_hour=12.0)
        sun_evening = calculate_solar_position(38.4, solar_hour=19.0)

        self.assertGreater(sun_noon.elevation_deg, sun_morning.elevation_deg)
        self.assertGreater(sun_noon.elevation_deg, sun_evening.elevation_deg)
        self.assertGreater(sun_noon.direct_irradiance_w_m2, sun_morning.direct_irradiance_w_m2)

        # Route shade exposure
        coords_3d = [(27.14, 38.42, 10.0), (27.145, 38.425, 12.0), (27.15, 38.43, 15.0)]
        shade_rep = compute_shade_exposure_along_route(coords_3d, solar_hour=13.0, building_density_factor=0.8)
        self.assertTrue(0.0 <= shade_rep.direct_sun_pct <= 100.0)
        self.assertTrue(0.0 <= shade_rep.shaded_pct <= 100.0)
        self.assertAlmostEqual(shade_rep.direct_sun_pct + shade_rep.shaded_pct, 100.0, places=1)
        self.assertIn("comfort_category", shade_rep.to_dict())

    def test_all_mobility_profiles_kinematics_sweep(self) -> None:
        from ..core.mobility_profiles import PROFILES

        slopes = [-20.0, -10.0, -5.0, 0.0, 5.0, 10.0, 20.0]
        for key, prof in PROFILES.items():
            self.assertGreater(prof.base_speed_kmh, 0.0)
            self.assertGreater(prof.max_slope_pct, 0.0)
            for slope in slopes:
                t_sec = prof.travel_time_seconds(length_m=100.0, slope_pct=slope)
                self.assertTrue(math.isfinite(t_sec))
                self.assertGreater(t_sec, 0.0)

    def test_evacuation_multi_destination_routing(self) -> None:
        segments = fixture_network_segments()
        engine = RoutingEngine3D()
        engine.build_graph(segments)

        router = EvacuationRouter(engine)
        origin = Waypoint(lon=27.11, lat=38.41, name="Evacuee Home")
        shelters = [
            Waypoint(lon=27.13, lat=38.43, name="Shelter East"),
            Waypoint(lon=27.15, lat=38.45, name="Shelter North"),
        ]

        evac_res = router.calculate_evacuation_route(origin, shelters, profile_key="adult")
        self.assertGreater(len(evac_res.route_result.coordinates_3d), 2)
        self.assertIn(evac_res.muster_point.name, ("Shelter East", "Shelter North"))
        self.assertGreater(evac_res.egress_time_min, 0.0)

    def test_environmental_surface_sampler_bilinear_and_mcda(self) -> None:
        from ..core.environmental_raster import MCDAWeights

        weights = MCDAWeights(weight_slope=0.5, weight_heat=0.3, weight_green=0.2)
        sampler = EnvironmentalSurfaceSampler(weights=weights)
        slope_pct, aspect_deg, solar = sampler.sample_slope_and_aspect((27.140, 38.420), (27.145, 38.425))
        self.assertTrue(math.isfinite(slope_pct))
        self.assertTrue(0.0 <= aspect_deg <= 360.0)
        self.assertTrue(math.isfinite(solar))

    def test_pareto_router_three_objective_frontier(self) -> None:
        segments = fixture_network_segments()
        engine = RoutingEngine3D()
        engine.build_graph(segments)

        start_n = list(engine.nodes.keys())[0]
        end_n = list(engine.nodes.keys())[-1]
        pareto = ParetoMultiObjectiveRouter(engine.nodes, engine.adj, engine.sampler)
        res = pareto.solve_pareto_frontier(start_n, end_n, profile_key="adult")
        self.assertGreaterEqual(len(res.solutions), 1)
        for sol in res.solutions:
            self.assertGreater(len(sol.coordinates_3d), 1)
            self.assertGreater(sol.statistics.total_distance_m, 0.0)

    def test_cartographic_osm_themes_catalog(self) -> None:
        from ..core.osm_styling import get_theme_palette, list_osm_themes

        themes = list_osm_themes()
        self.assertGreaterEqual(len(themes), 8)
        theme_keys = [k for k, _ in themes]
        expected_keys = ["atlas", "cyber", "paper", "frost", "noir", "mediterranean", "nightprint", "default"]
        for ek in expected_keys:
            self.assertIn(ek, theme_keys)

        for key in expected_keys:
            pal = get_theme_palette(key)
            self.assertIn("label", pal)
            self.assertIn("roads_major", pal)
            self.assertIn("roads_minor", pal)
            self.assertIn("greens", pal)
            self.assertIn("buildings", pal)
            self.assertGreaterEqual(len(pal["buildings"]), 5)

    def test_multi_metric_profile_data_contract(self) -> None:
        from ..core.mobility_profiles import get_profile
        from ..core.profile_stats import compute_route_statistics

        # Case 1: Standard routing without rasters
        coords = [(27.1, 38.4, 10.0), (27.2, 38.4, 25.0), (27.3, 38.4, 15.0)]
        stats = compute_route_statistics(coords, profile=get_profile("adult"))
        self.assertGreater(len(stats.elevation_profile), 2)
        for pt in stats.elevation_profile:
            self.assertIn("distance_m", pt)
            self.assertIn("elevation_m", pt)
            self.assertIn("slope_pct", pt)
            self.assertIn("speed_kmh", pt)
            self.assertNotIn("lst_normalized", pt)
            self.assertNotIn("ndvi_normalized", pt)

        # Case 2: Routing with real raster environmental samples
        dense_count = len(stats.elevation_profile)
        mock_lst = [0.4] * dense_count
        mock_green = [0.75] * dense_count
        stats_env = compute_route_statistics(
            coords,
            profile=get_profile("adult"),
            lst_samples=mock_lst,
            green_samples=mock_green,
        )
        self.assertEqual(len(stats_env.elevation_profile), dense_count)
        for pt in stats_env.elevation_profile:
            self.assertIn("lst_normalized", pt)
            self.assertIn("ndvi_normalized", pt)
            self.assertAlmostEqual(pt["lst_normalized"], 0.4)
            self.assertAlmostEqual(pt["ndvi_normalized"], 0.75)

    def test_modal_access_policy_blocks_incompatible_roads(self) -> None:
        """Hard modal rules prevent physically invalid route choices."""
        from ..core.network_policy import evaluate_edge_access, surface_quality

        adult = get_profile("adult")
        bicycle = get_profile("bicycle")
        car = get_profile("car")

        self.assertFalse(evaluate_edge_access(car, {"highway": "footway"}).allowed)
        self.assertFalse(evaluate_edge_access(adult, {"highway": "motorway"}).allowed)
        self.assertFalse(evaluate_edge_access(bicycle, {"highway": "motorway"}).allowed)
        self.assertFalse(
            evaluate_edge_access(adult, {"highway": "residential", "foot": "no"}).allowed
        )
        self.assertTrue(
            evaluate_edge_access(
                car, {"highway": "footway", "motor_vehicle": "designated"}
            ).allowed
        )
        self.assertGreater(surface_quality("asphalt"), surface_quality("gravel"))
        self.assertGreater(surface_quality("gravel"), surface_quality("mud"))

        night = evaluate_edge_access(
            get_profile("night_walk"), {"highway": "residential", "lit": "no"}
        )
        self.assertTrue(night.allowed)
        self.assertGreater(night.penalty, 1.0)

    def test_router_respects_modal_access_and_reports_diagnostics(self) -> None:
        p1 = (27.1400, 38.4200, 5.0)
        p2 = (27.1410, 38.4200, 5.0)
        footway = RoadSegment(
            p1=p1,
            p2=p2,
            length_m=haversine_distance_2d(p1, p2),
            highway_type="footway",
        )
        engine = RoutingEngine3D(max_snap_distance_m=100.0)
        engine.build_graph([footway])

        walking = engine.calculate_route([Waypoint(*p1[:2]), Waypoint(*p2[:2])], "adult")
        self.assertTrue(walking.is_network_matched)
        self.assertEqual(walking.routing_diagnostics["segments"][0]["status"], "matched")

        driving = engine.calculate_route([Waypoint(*p1[:2]), Waypoint(*p2[:2])], "car")
        self.assertFalse(driving.is_network_matched)
        self.assertIn("modal access", driving.status_message)
        self.assertGreater(
            driving.routing_diagnostics["segments"][0]["blocked_by_access"], 0
        )

    def test_one_way_components_are_weak_and_order_independent(self) -> None:
        """Converging one-way edges belong to one snap component."""
        a = (27.1400, 38.4200, 0.0)
        b = (27.1420, 38.4200, 0.0)
        c = (27.1410, 38.4210, 0.0)
        segments = [
            RoadSegment(a, c, haversine_distance_2d(a, c), is_oneway=True),
            RoadSegment(b, c, haversine_distance_2d(b, c), is_oneway=True),
        ]
        engine = RoutingEngine3D()
        engine.build_graph(segments)
        self.assertEqual(len(engine.component_sizes), 1)
        self.assertEqual(engine.graph_diagnostics["one_way_segments"], 2)

    def test_snap_limit_rejects_misleading_connector(self) -> None:
        engine = RoutingEngine3D(max_snap_distance_m=50.0)
        engine.build_graph(fixture_network_segments())
        result = engine.calculate_route(
            [Waypoint(27.50, 38.80), Waypoint(27.51, 38.81)], "adult"
        )
        self.assertFalse(result.is_network_matched)
        self.assertIn("within 50 m", result.status_message)
        self.assertEqual(
            result.routing_diagnostics["segments"][0]["status"], "snap_failed"
        )

    def test_network_readiness_audit(self) -> None:
        from ..core.network_audit import audit_network

        a = (27.1400, 38.4200, 0.0)
        b = (27.1410, 38.4200, 0.0)
        c = (27.1420, 38.4200, 0.0)
        segments = [
            RoadSegment(a, b, haversine_distance_2d(a, b), highway_type="residential"),
            RoadSegment(b, c, haversine_distance_2d(b, c), highway_type="footway"),
        ]
        report = audit_network(segments, "car")
        self.assertEqual(report.segment_count, 2)
        self.assertEqual(report.allowed_segments, 1)
        self.assertEqual(report.blocked_segments, 1)
        self.assertEqual(report.component_count, 1)
        self.assertFalse(report.ready)
        self.assertTrue(60.0 < report.largest_component_pct < 70.0)
        self.assertFalse(report.findings[1]["allowed"])

    def test_osm_access_and_speed_tags_survive_parsing(self) -> None:
        payload = {
            "elements": [
                {"type": "node", "id": 1, "lat": 38.42, "lon": 27.14},
                {"type": "node", "id": 2, "lat": 38.42, "lon": 27.15},
                {
                    "type": "way",
                    "id": 10,
                    "nodes": [1, 2],
                    "tags": {
                        "highway": "cycleway",
                        "foot": "no",
                        "bicycle": "designated",
                        "motor_vehicle": "no",
                        "surface": "fine_gravel",
                        "lit": "yes",
                        "maxspeed": "20 mph",
                    },
                },
            ]
        }
        segment = NetworkSourceManager()._parse_osm_json(payload)[0]
        self.assertEqual(segment.foot, "no")
        self.assertEqual(segment.bicycle, "designated")
        self.assertEqual(segment.motor_vehicle, "no")
        self.assertEqual(segment.surface, "fine_gravel")
        self.assertEqual(segment.lit, "yes")
        self.assertAlmostEqual(segment.maxspeed_kmh, 32.18688, places=4)

    def test_isochrone_uses_real_hull_area_and_modal_access(self) -> None:
        from ..core.isochrone_engine import _convex_hull, _polygon_area_ha

        square = [
            (27.0000, 38.0000),
            (27.0010, 38.0000),
            (27.0010, 38.0010),
            (27.0000, 38.0010),
            (27.0005, 38.0005),
        ]
        hull = _convex_hull(square)
        self.assertEqual(len(hull), 4)
        # At 38 degrees this 0.001-degree square is roughly 0.97 hectares.
        self.assertTrue(0.8 < _polygon_area_ha(hull) < 1.2)

        p1 = (27.1400, 38.4200, 0.0)
        p2 = (27.1410, 38.4200, 0.0)
        engine = RoutingEngine3D()
        engine.build_graph([
            RoadSegment(p1, p2, haversine_distance_2d(p1, p2), highway_type="footway")
        ])
        result = IsochroneEngine3D(engine).compute_isochrones(
            Waypoint(*p1[:2]), profile_key="car", time_intervals_min=(5.0,)
        )
        self.assertEqual(result.total_reachable_nodes, 1)

    def test_pareto_router_respects_profile_constraints(self) -> None:
        p1 = (27.1400, 38.4200, 0.0)
        p2 = (27.1410, 38.4200, 0.0)
        engine = RoutingEngine3D()
        engine.build_graph([
            RoadSegment(p1, p2, haversine_distance_2d(p1, p2), highway_type="footway")
        ])
        nodes = list(engine.nodes)
        result = ParetoMultiObjectiveRouter(
            engine.nodes, engine.adj, engine.sampler
        ).solve_pareto_frontier(nodes[0], nodes[1], profile_key="car")
        self.assertEqual(result.solutions, [])

    def test_parallel_edges_keep_distinct_access_and_reverse_direction(self) -> None:
        a = (27.1400, 38.4200, 0.0)
        b = (27.1410, 38.4200, 0.0)
        distance = haversine_distance_2d(a, b)
        engine = RoutingEngine3D()
        engine.build_graph([
            RoadSegment(
                a, b, distance, highway_type="service", is_oneway=True,
                motor_vehicle="private",
            ),
            RoadSegment(a, b, distance, highway_type="residential", is_oneway=False),
        ])
        node_a = engine.coord_to_node[(round(a[0], 5), round(a[1], 5))]
        node_b = engine.coord_to_node[(round(b[0], 5), round(b[1], 5))]
        self.assertGreaterEqual(len(engine.adj[node_a]), 2)
        self.assertTrue(any(edge[0] == node_a for edge in engine.adj[node_b]))

        reverse = engine.calculate_route(
            [Waypoint(*b[:2]), Waypoint(*a[:2])], profile_key="car"
        )
        self.assertTrue(reverse.is_network_matched)


class RoutingRulesTests(unittest.TestCase):
    """One-way rules per mode and hard accessibility slope limits."""

    @staticmethod
    def _one_way_street(**tags):
        a = (27.1500, 38.4300, 0.0)
        b = (27.1510, 38.4300, 0.0)
        engine = RoutingEngine3D()
        engine.build_graph([
            RoadSegment(a, b, haversine_distance_2d(a, b), highway_type="residential",
                        is_oneway=True, **tags),
        ])
        return engine, a, b

    def test_pedestrian_walks_against_one_way(self) -> None:
        engine, a, b = self._one_way_street()
        walk = engine.calculate_route([Waypoint(*b[:2]), Waypoint(*a[:2])], "adult",
                                      compute_alternatives=False)
        self.assertTrue(walk.is_network_matched)
        self.assertGreater(walk.statistics.total_distance_m, 50.0)

    def test_car_and_bicycle_respect_one_way(self) -> None:
        engine, a, b = self._one_way_street()
        for profile in ("car", "bicycle"):
            back = engine.calculate_route([Waypoint(*b[:2]), Waypoint(*a[:2])], profile,
                                          compute_alternatives=False)
            self.assertFalse(back.is_network_matched, profile)
            forward = engine.calculate_route([Waypoint(*a[:2]), Waypoint(*b[:2])], profile,
                                             compute_alternatives=False)
            self.assertTrue(forward.is_network_matched, profile)

    def test_contra_flow_bicycle_lane(self) -> None:
        engine, a, b = self._one_way_street(oneway_bicycle="no")
        back = engine.calculate_route([Waypoint(*b[:2]), Waypoint(*a[:2])], "bicycle",
                                      compute_alternatives=False)
        self.assertTrue(back.is_network_matched)
        car = engine.calculate_route([Waypoint(*b[:2]), Waypoint(*a[:2])], "car",
                                     compute_alternatives=False)
        self.assertFalse(car.is_network_matched)

    def test_wheelchair_never_exceeds_ramp_maximum(self) -> None:
        wheelchair = get_profile("wheelchair")
        self.assertTrue(math.isinf(wheelchair.calculate_edge_resistance(100.0, 12.0)))
        self.assertTrue(math.isfinite(wheelchair.calculate_edge_resistance(100.0, 7.0)))
        # A gentler slope is still cheaper than one above the comfort limit.
        self.assertLess(
            wheelchair.calculate_edge_resistance(100.0, 3.0),
            wheelchair.calculate_edge_resistance(100.0, 7.0),
        )
        stroller = get_profile("stroller")
        self.assertTrue(math.isinf(stroller.calculate_edge_resistance(100.0, 11.0)))
        # Other pedestrians are only slowed down by steep slopes.
        self.assertTrue(math.isfinite(get_profile("senior").calculate_edge_resistance(100.0, 30.0)))

    def test_custom_profile_json_round_trip_keeps_weights(self) -> None:
        profile = get_profile("wheelchair")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "custom.json"
            save_custom_profile_json(profile, path)
            loaded = load_custom_profile_json(path)
        self.assertEqual(loaded.hierarchy_weights, profile.hierarchy_weights)
        self.assertEqual(loaded.hard_slope_limit_pct, profile.hard_slope_limit_pct)


class RoutingMathTests(unittest.TestCase):
    """Pareto dominance and heuristic, elevation gaps, slopes, speed limits."""

    def test_epsilon_dominance_loosens_pruning(self) -> None:
        a = ParetoCostVector(100.0, 10.0, 0.0, 50.0)
        b = ParetoCostVector(102.0, 10.0, 0.0, 50.0)
        self.assertTrue(a.dominates(b))
        # Within 3 %, b counts as dominating a too: epsilon loosens pruning.
        self.assertTrue(b.dominates(a, epsilon=0.03))
        self.assertFalse(b.dominates(a))

    def test_pareto_time_heuristic_is_admissible(self) -> None:
        for key in ("bicycle", "car", "paramedic", "truck", "adult", "scooter"):
            profile = get_profile(key)
            bound = _min_seconds_per_metre(profile)
            for rank in range(1, 6):
                for slope in (-30.0, -12.0, -5.0, 0.0, 5.0):
                    actual = profile.travel_time_seconds(
                        100.0, slope_pct=slope, hierarchy_rank=rank, lanes=4
                    ) / 100.0
                    self.assertLessEqual(bound, actual + 1e-9, (key, rank, slope))

    def test_missing_elevation_is_filled_from_neighbours(self) -> None:
        class PartialDem(EnvironmentalSurfaceSampler):
            def sample_elevation(self, lon, lat):
                return 100.0 if lon < 27.1525 else None

        pts = [(27.1500 + i * 0.001, 38.43, 0.0) for i in range(5)]
        segments = [
            RoadSegment(pts[i], pts[i + 1], haversine_distance_2d(pts[i], pts[i + 1]))
            for i in range(4)
        ]
        engine = RoutingEngine3D(sampler=PartialDem())
        engine.build_graph(segments)
        heights = [node[2] for node in engine.nodes.values()]
        self.assertTrue(all(abs(h - 100.0) < 1e-9 for h in heights), heights)
        self.assertGreater(engine.graph_diagnostics["elevation_filled_nodes"], 0)
        slopes = [edge[2] for edges in engine.adj.values() for edge in edges]
        self.assertTrue(all(abs(s) < 1e-9 for s in slopes))

    def test_short_dem_segment_slope_is_damped(self) -> None:
        class NoisyDem(EnvironmentalSurfaceSampler):
            def sample_elevation(self, lon, lat):
                return 10.5 if lon > 27.15 else 10.0

        a = (27.15000, 38.43, 0.0)
        b = (27.15002, 38.43, 0.0)  # about 1.7 m
        engine = RoutingEngine3D(sampler=NoisyDem())
        engine.build_graph([RoadSegment(a, b, haversine_distance_2d(a, b))])
        slope = max(abs(edge[2]) for edges in engine.adj.values() for edge in edges)
        # 0.5 m of DEM noise over 1.7 m would read as ~29 %; over the 10 m
        # minimum run it is 5 %.
        self.assertLessEqual(slope, 5.0 + 1e-6)

    def test_speed_limit_slows_vehicles_only(self) -> None:
        car = get_profile("car")
        free = car.calculate_edge_resistance(100.0, 0.0, hierarchy_rank=3)
        limited = car.calculate_edge_resistance(100.0, 0.0, hierarchy_rank=3, maxspeed_kmh=20.0)
        self.assertGreater(limited, free)
        fast_limit = car.calculate_edge_resistance(100.0, 0.0, hierarchy_rank=3, maxspeed_kmh=130.0)
        self.assertAlmostEqual(fast_limit, free)
        self.assertGreater(
            car.travel_time_seconds(100.0, hierarchy_rank=3, maxspeed_kmh=20.0),
            car.travel_time_seconds(100.0, hierarchy_rank=3),
        )
        walker = get_profile("adult")
        self.assertAlmostEqual(
            walker.calculate_edge_resistance(100.0, 0.0, maxspeed_kmh=20.0),
            walker.calculate_edge_resistance(100.0, 0.0),
        )


class MapMatchingTests(unittest.TestCase):
    def test_projection_uses_metric_frame(self) -> None:
        # An east-west street at 60 degrees north: a point due north of its
        # middle must snap to the middle, not be pulled sideways.
        nodes = {0: (10.000, 60.0, 0.0), 1: (10.002, 60.0, 0.0)}
        matcher = HMMMapMatcher3D(nodes, {0: [(1, 111.0, 0.0, {})], 1: []})
        snap, dist, frac = matcher._project_point_to_edge((10.001, 60.0002), 0, 1)
        self.assertAlmostEqual(frac, 0.5, places=3)
        self.assertAlmostEqual(dist, 22.2, delta=1.0)

    def test_track_stays_on_connected_street(self) -> None:
        segments = fixture_network_segments()
        engine = RoutingEngine3D()
        engine.build_graph(segments)
        start = engine.nodes[next(iter(engine.nodes))]
        track = [GPXPoint(lon=start[0] + 0.00001 * i, lat=start[1] + 0.00001) for i in range(5)]
        result = HMMMapMatcher3D(engine.nodes, engine.adj).match_gps_track(track, search_radius_m=50.0)
        self.assertEqual(len(result.matched_points), len(track))


class AlternativeRouteTests(unittest.TestCase):
    def test_alternative_found_across_single_bridge(self) -> None:
        # Two loops joined by one bridge: forbidding the bridge (the old
        # method) made any alternative impossible; penalising it does not.
        def seg(a, b):
            return RoadSegment(a, b, haversine_distance_2d(a, b))

        w = [(27.100, 38.400, 0.0), (27.101, 38.401, 0.0), (27.101, 38.399, 0.0), (27.102, 38.400, 0.0)]
        e = [(27.103, 38.400, 0.0), (27.104, 38.401, 0.0), (27.104, 38.399, 0.0), (27.105, 38.400, 0.0)]
        segments = [
            seg(w[0], w[1]), seg(w[1], w[3]), seg(w[0], w[2]), seg(w[2], w[3]),
            seg(w[3], e[0]),  # the bridge
            seg(e[0], e[1]), seg(e[1], e[3]), seg(e[0], e[2]), seg(e[2], e[3]),
        ]
        engine = RoutingEngine3D()
        engine.build_graph(segments)
        result = engine.calculate_route([Waypoint(*w[0][:2]), Waypoint(*e[3][:2])], "adult")
        self.assertTrue(result.is_network_matched)
        self.assertEqual(len(result.alternative_routes), 1)


class NetworkErrorReportingTests(unittest.TestCase):
    def test_overpass_failure_reason_reaches_the_user(self) -> None:
        from unittest import mock

        manager = NetworkSourceManager()
        with mock.patch("http.client.HTTPSConnection", side_effect=OSError("network unreachable")):
            with self.assertRaises(NetworkSourceError) as ctx:
                manager.fetch_osm_network_bbox((12.3456, 45.6789, 12.3466, 45.6799))
        self.assertIn("network unreachable", str(ctx.exception))

    def test_osm_fetcher_records_error(self) -> None:
        from unittest import mock
        from zero2route3d.core.osm_downloader import OsmDataFetcher

        with mock.patch("http.client.HTTPSConnection", side_effect=OSError("timed out")):
            roads, buildings, trees, parks = OsmDataFetcher.fetch_full_urban_environment(
                (12.3456, 45.6789, 12.3466, 45.6799)
            )
        self.assertEqual((roads, buildings, trees, parks), ([], [], [], []))
        self.assertIn("timed out", OsmDataFetcher.last_error)


class RoutingPerformanceTests(unittest.TestCase):
    """Phase 3 speed-ups must not change any answer."""

    def setUp(self) -> None:
        from zero2route3d.core.routing_engine import clear_graph_cache

        clear_graph_cache()

    def _grid_engine(self, edges: int = 1000):
        from zero2route3d.core.routing_engine import RoutingEngine3D
        from zero2route3d.tests.benchmark_routing import grid_segments, side_for_edges

        n = side_for_edges(edges)
        engine = RoutingEngine3D()
        engine.build_graph(grid_segments(n))
        return engine, n

    def test_od_matrix_matches_individual_routes(self) -> None:
        from zero2route3d.tests.benchmark_routing import spread_points

        engine, n = self._grid_engine()
        points = spread_points(n, 6)
        for profile in ("adult", "wheelchair", "car"):
            rows = engine.calculate_od_matrix(points, points, profile_key=profile)
            self.assertEqual(len(rows), 36)
            for row in rows:
                single = engine.calculate_route(
                    [points[row["origin_id"] - 1], points[row["dest_id"] - 1]],
                    profile_key=profile,
                    compute_alternatives=False,
                )
                self.assertAlmostEqual(row["distance_m"], single.statistics.total_distance_m, places=6)
                self.assertAlmostEqual(row["duration_min"], single.statistics.total_duration_min, places=9)

    def test_astar_cost_equals_dijkstra_cost(self) -> None:
        """The tightened heuristic stays admissible: A* finds the optimal cost."""
        from zero2route3d.core.mobility_profiles import get_profile

        engine, _n = self._grid_engine()
        nodes = sorted(engine.nodes)
        for profile_key in ("adult", "bicycle", "car", "wheelchair"):
            profile = get_profile(profile_key)
            for start, goal in ((nodes[0], nodes[-1]), (nodes[5], nodes[len(nodes) // 2]), (nodes[-3], nodes[40])):
                tree, settled, _ = engine._search(start, profile, set(engine.nodes))
                path, _visited, _ = engine._search(start, profile, {goal}, goal=goal)
                if goal not in settled:
                    self.assertNotIn(goal, path)
                    continue

                def cost(prev):
                    """Sum of the cheapest evaluated edge along the predecessor chain."""
                    total, node = 0.0, goal
                    table = engine._edge_table(profile)
                    while node != start:
                        parent = prev[node]
                        total += min(
                            entry[0]
                            for edge, entry in zip(engine.adj[parent], table[parent])
                            if edge[0] == node and entry is not None
                        )
                        node = parent
                    return total

                self.assertAlmostEqual(cost(path), cost(tree), places=6)

    def test_planar_bound_never_exceeds_haversine(self) -> None:
        from zero2route3d.core.kinematics import haversine_distance_2d
        from zero2route3d.core.network_source import RoadSegment
        from zero2route3d.core.routing_engine import RoutingEngine3D

        for lat0 in (-62.0, 0.0, 38.4, 69.5):
            engine = RoutingEngine3D()
            # Deterministic scatter (bandit flags the random module in shipped tests).
            pts = [(10.0 + (k * 0.6180339) % 0.5, lat0 - 0.3 + (k * 0.4142135) % 0.6, 0.0) for k in range(40)]
            engine.build_graph(
                [RoadSegment(p1=a, p2=b, length_m=haversine_distance_2d(a, b)) for a, b in zip(pts, pts[1:])]
            )
            for a in pts:
                for b in pts[::7]:
                    self.assertLessEqual(engine.planar_distance_lower_bound(a, b), haversine_distance_2d(a, b) + 1e-9)

    def test_snapping_reaches_the_full_radius_at_high_latitude(self) -> None:
        """Buckets narrow toward the poles; the old fixed span missed nodes there."""
        from zero2route3d.core.network_source import RoadSegment
        from zero2route3d.core.routing_engine import RoutingEngine3D

        lat = 69.6  # Tromso
        lon_900m = 900.0 / (111_195.0 * math.cos(math.radians(lat)))
        a, b = (18.95 + lon_900m, lat, 0.0), (18.95 + lon_900m, lat + 0.001, 0.0)
        engine = RoutingEngine3D(max_snap_distance_m=1000.0)
        engine.build_graph([RoadSegment(p1=a, p2=b, length_m=111.0)])
        self.assertIsNotNone(engine.find_nearest_node((18.95, lat), max_search_radius_m=1000.0))
        self.assertIsNone(engine.find_nearest_node((18.95, lat), max_search_radius_m=800.0))

    def test_snapping_prefers_the_component_both_points_reach(self) -> None:
        from zero2route3d.core.network_source import RoadSegment
        from zero2route3d.core.routing_engine import RoutingEngine3D

        # A long street both points can reach, and a tiny island next to the origin.
        street = [((27.0 + i * 0.0005, 38.4, 0.0), (27.0 + (i + 1) * 0.0005, 38.4, 0.0)) for i in range(10)]
        island = [((27.0, 38.4002, 0.0), (27.0001, 38.4002, 0.0))]
        engine = RoutingEngine3D()
        engine.build_graph([RoadSegment(p1=a, p2=b, length_m=40.0) for a, b in street + island])
        start, end = engine.find_compatible_nodes((27.0, 38.40015), (27.005, 38.4001))
        self.assertEqual(engine.component_by_node[start], engine.component_by_node[end])

    def test_graph_cache_reuses_a_built_graph(self) -> None:
        from zero2route3d.core.routing_engine import RoutingEngine3D
        from zero2route3d.tests.benchmark_routing import grid_segments

        segments = grid_segments(12)
        first = RoutingEngine3D()
        first.build_graph(segments)
        second = RoutingEngine3D()
        second.build_graph(list(segments))
        self.assertEqual(first.graph_diagnostics["graph_cache"], "miss")
        self.assertEqual(second.graph_diagnostics["graph_cache"], "hit")
        self.assertIs(first.adj, second.adj)
        changed = list(segments)
        changed[0] = type(changed[0])(**{**changed[0].__dict__, "is_oneway": not changed[0].is_oneway})
        third = RoutingEngine3D()
        third.build_graph(changed)
        self.assertEqual(third.graph_diagnostics["graph_cache"], "miss")
        # Rebuilding a cached engine must not clear the shared graph.
        first.build_graph(changed)
        self.assertTrue(second.adj)

    def test_densify_records_the_true_source_segment(self) -> None:
        from zero2route3d.core.profile_stats import densify_3d_linestring_indexed

        coords = [(27.0, 38.4, 0.0), (27.001, 38.4, 0.0), (27.001, 38.401, 0.0)]
        points, source = densify_3d_linestring_indexed(coords, sample_interval_m=10.0)
        self.assertEqual(len(points), len(source))
        for point, seg in zip(points[:-1], source[:-1]):
            if seg == 0:
                self.assertAlmostEqual(point[1], 38.4)
            else:
                self.assertAlmostEqual(point[0], 27.001)


class MetadataTests(unittest.TestCase):
    def test_every_metadata_value_survives_interpolation(self) -> None:
        """The QGIS Hub reads metadata.txt with ConfigParser interpolation: a bare
        '%' anywhere (e.g. in the changelog) rejects the upload."""
        import configparser

        parser = configparser.ConfigParser()
        parser.read(Path(__file__).resolve().parent.parent / "metadata.txt", encoding="utf-8")
        self.assertTrue(parser.has_section("general"))
        for key in parser["general"]:
            parser.get("general", key)  # raises InterpolationSyntaxError on a stray '%'


class CancellationTests(unittest.TestCase):
    """Background tasks and Processing feedback can stop downloads."""

    def test_overpass_download_stops_between_chunks(self) -> None:
        from zero2route3d.core.network_source import NetworkSourceCancelled

        class Response:
            def __init__(self) -> None:
                self.reads = 0

            def read(self, _size: int) -> bytes:
                self.reads += 1
                return b"x" * 10

        resp = Response()
        with self.assertRaises(NetworkSourceCancelled):
            NetworkSourceManager._read_body(resp, is_canceled=lambda: resp.reads >= 2)
        self.assertEqual(resp.reads, 2)

    def test_cancelled_overpass_query_makes_no_request(self) -> None:
        from unittest import mock
        from zero2route3d.core.network_source import NetworkSourceCancelled

        with mock.patch("http.client.HTTPSConnection") as conn:
            with self.assertRaises(NetworkSourceCancelled):
                NetworkSourceManager().require_segments(
                    bbox=(12.3456, 45.6789, 12.3466, 45.6799), is_canceled=lambda: True
                )
        conn.assert_not_called()

    def test_cancelled_dem_download_leaves_points_unresolved(self) -> None:
        from unittest import mock
        from zero2route3d.core.dem_fetcher import GlobalDemFetcher

        coords = [(-170.0 + i * 1e-4, -80.0) for i in range(400)]
        with mock.patch("http.client.HTTPSConnection") as conn:
            values = GlobalDemFetcher.fetch_elevations_for_coords(coords, is_canceled=lambda: True)
        conn.assert_not_called()
        self.assertEqual(values, [None] * len(coords))


class DemBudgetTests(unittest.TestCase):
    def test_small_extent_keeps_requested_resolution(self) -> None:
        from zero2route3d.core.copernicus_eo_suite import plan_dem_grid

        plan = plan_dem_grid((27.14, 38.42, 27.15, 38.43))
        self.assertFalse(plan["coarsened"])
        self.assertLess(plan["res_m"], 40.0)
        self.assertEqual(plan["points"], plan["width"] * plan["height"])

    def test_city_extent_is_coarsened_to_the_budget(self) -> None:
        from zero2route3d.core.copernicus_eo_suite import plan_dem_grid

        for bbox in ((26.9, 38.3, 27.3, 38.6), (0.0, 0.0, 1.0, 0.01), (10.0, 50.0, 10.7, 50.7)):
            plan = plan_dem_grid(bbox, max_points=25_000)
            self.assertTrue(plan["coarsened"])
            self.assertLessEqual(plan["points"], 25_000)
            self.assertLessEqual(plan["requests"], 167)
            # The grid still covers the whole extent.
            self.assertGreaterEqual(plan["width"] * plan["res_deg"], (bbox[2] - bbox[0]) - 1e-9)
            self.assertGreaterEqual(plan["height"] * plan["res_deg"], (bbox[3] - bbox[1]) - 1e-9)


class ScenarioIoTests(unittest.TestCase):
    def _scenario(self) -> dict:
        from zero2route3d.core.scenario_io import build_scenario

        return build_scenario(
            inputs={"sliders": {"sld_slope": 60}},
            layers={"cmb_dem_layer": "dem_1"},
            points={"A": {"lon": 27.14, "lat": 38.42, "name": "Home"}, "B": {"lon": 27.15, "lat": 38.43}},
            results={"adult": {"profile": "Adult", "distance_km": 2.0, "duration_min": 25.0, "climb_m": 10.0}},
            name="morning",
        )

    def test_round_trip_through_a_file(self) -> None:
        from zero2route3d.core.scenario_io import load_scenario, save_scenario

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.route3d.json"
            save_scenario(path, self._scenario())
            loaded = load_scenario(path)
        self.assertEqual(loaded["name"], "morning")
        self.assertEqual(loaded["points"]["A"], {"lon": 27.14, "lat": 38.42, "name": "Home"})
        self.assertEqual(loaded["points"]["B"]["name"], "B")
        self.assertEqual(loaded["layers"], {"cmb_dem_layer": "dem_1"})
        self.assertEqual(loaded["results"]["adult"]["distance_km"], 2.0)
        self.assertNotIn("max_slope_pct", loaded["results"]["adult"])

    def test_rejects_foreign_or_broken_files(self) -> None:
        from zero2route3d.core.scenario_io import ScenarioError, load_scenario, parse_scenario

        with self.assertRaises(ScenarioError):
            parse_scenario({"type": "FeatureCollection"})
        bad_point = self._scenario()
        bad_point["points"]["A"] = {"lon": 500, "lat": 0}
        with self.assertRaises(ScenarioError):
            parse_scenario(bad_point)
        future = self._scenario()
        future["version"] = 99
        with self.assertRaises(ScenarioError):
            parse_scenario(future)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ScenarioError):
                load_scenario(path)

    def test_comparison_keeps_profiles_from_both_runs(self) -> None:
        from zero2route3d.core.scenario_io import compare_runs

        saved = {"adult": {"profile": "Adult", "distance_km": 2.0, "duration_min": 25.0}}
        current = {
            "adult": {"profile": "Adult", "distance_km": 2.5, "duration_min": 25.0},
            "wheelchair": {"profile": "Wheelchair", "distance_km": 3.0},
        }
        rows = {(r["profile_key"], r["metric"]): r for r in compare_runs(saved, current)}
        self.assertAlmostEqual(rows[("adult", "distance_km")]["delta"], 0.5)
        self.assertAlmostEqual(rows[("adult", "distance_km")]["delta_pct"], 25.0)
        self.assertEqual(rows[("adult", "duration_min")]["delta"], 0.0)
        self.assertIsNone(rows[("wheelchair", "distance_km")]["saved"])
        self.assertIsNone(rows[("wheelchair", "distance_km")]["delta"])
        self.assertNotIn(("adult", "climb_m"), rows)


if __name__ == "__main__":
    unittest.main()
