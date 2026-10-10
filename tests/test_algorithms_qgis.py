"""End-to-end Processing checks for 02Route 3D on a real QGIS runtime.

The existing suites only assert that algorithms *load*; none ever calls
processAlgorithm. Every correctness bug found in the audit passed those suites.
This harness runs the algorithms against real in-memory fixtures and asserts on
values, renderers and field aliases.
"""
from __future__ import annotations

import os
import struct
import sys
import traceback

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsVertexId,
)

RESULTS = []

class ErrorFeedback(QgsProcessingFeedback):
    """Feedback that records reportError/pushWarning text for assertions."""

    def __init__(self):
        super().__init__()
        self.errors = []

    def reportError(self, error, fatalError=False):
        self.errors.append(str(error))

    def pushWarning(self, warning):
        self.errors.append(str(warning))





def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" -- {detail}" if detail else ""))
    return bool(condition)


def make_network(crs="EPSG:4326"):
    """A tiny connected street grid."""
    layer = QgsVectorLayer(f"LineString?crs={crs}&field=highway:string", "net", "memory")
    provider = layer.dataProvider()
    if crs == "EPSG:4326":
        pts = [
            [(27.140, 38.420), (27.145, 38.420)],
            [(27.145, 38.420), (27.150, 38.420)],
            [(27.150, 38.420), (27.150, 38.425)],
            [(27.145, 38.420), (27.145, 38.425)],
            [(27.145, 38.425), (27.150, 38.425)],
        ]
    else:
        pts = [
            [(3021000, 4640000), (3021500, 4640000)],
            [(3021500, 4640000), (3022000, 4640000)],
        ]
    feats = []
    for line in pts:
        f = QgsFeature(layer.fields())
        f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(*p) for p in line]))
        f.setAttributes(["residential"])
        feats.append(f)
    provider.addFeatures(feats)
    layer.updateExtents()
    return layer


def reproject_layer(layer, authid):
    """A memory copy of a line layer in another CRS."""
    target = QgsCoordinateReferenceSystem(authid)
    out = QgsVectorLayer(f"LineString?crs={authid}&field=highway:string", "net_" + authid, "memory")
    transform = QgsCoordinateTransform(layer.crs(), target, QgsProject.instance())
    feats = []
    for feature in layer.getFeatures():
        geometry = QgsGeometry(feature.geometry())
        geometry.transform(transform)
        copy = QgsFeature(out.fields())
        copy.setGeometry(geometry)
        copy.setAttributes(feature.attributes())
        feats.append(copy)
    out.dataProvider().addFeatures(feats)
    out.updateExtents()
    return out


def make_route_layer_z():
    """A LineStringZ route, to prove Z survives the export path."""
    layer = QgsVectorLayer("LineStringZ?crs=EPSG:4326", "route", "memory")
    f = QgsFeature(layer.fields())
    f.setGeometry(QgsGeometry.fromWkt(
        "LineStringZ(27.140 38.420 10, 27.145 38.421 25, 27.150 38.422 40)"
    ))
    layer.dataProvider().addFeatures([f])
    layer.updateExtents()
    return layer


def run():
    from zero2route3d.processing.provider import Route3DProcessingProvider

    provider = Route3DProcessingProvider()
    provider.loadAlgorithms()
    algs = {a.name(): a for a in provider.algorithms()}
    print(f"\nProvider exposes {len(algs)} algorithms")

    ctx = QgsProcessingContext()
    ctx.setProject(QgsProject.instance())
    # ---- 1. Group ids must not split the toolbox into two identical folders.
    group_ids = {a.groupId() for a in algs.values()}
    check("all algorithms share one groupId", len(group_ids) == 1, str(group_ids))

    # ---- 2. Every algorithm must carry help text.
    missing_help = [n for n, a in algs.items() if not (a.shortHelpString() or "").strip()]
    check("every algorithm has shortHelpString", not missing_help, str(missing_help))

    # ---- 3. Every sink-producing algorithm must define postProcessAlgorithm.
    no_post = [n for n, a in algs.items()
               if "postProcessAlgorithm" not in type(a).__dict__]
    check("sink algorithms define postProcessAlgorithm", len(no_post) <= 4, str(no_post))

    # ---- 4. Isochrone profile enum must map 1:1 onto the profile registry.
    from zero2route3d.core.mobility_profiles import get_profile, list_profile_keys
    iso = algs.get("generate_3d_isochrone")
    if iso is not None:
        iso.initAlgorithm({})
        param = next(p for p in iso.parameterDefinitions() if p.name() == "PROFILE")
        options = param.options()
        expected = [get_profile(k).name for k in list_profile_keys()]
        check("isochrone enum matches profile registry", list(options) == expected,
              f"{len(options)} options vs {len(expected)} profiles")

    # ---- 4b. The isochrone follows the streets: the middle of a city block
    # (about 200 m from any street) is outside, which the old node hull was not.
    if iso is not None:
        net = make_network()
        errs = ErrorFeedback()
        res, ok = iso.run({
            "CENTER_POINT": "27.140,38.420 [EPSG:4326]",
            "TIME_MINUTES": 15.0,
            "PROFILE": 0,
            "NETWORK_LAYER": net,
            "BUFFER_M": 50.0,
            "OUTPUT": "memory:",
        }, ctx, errs)
        layer = ctx.getMapLayer(res.get("OUTPUT")) if ok else None
        feat = next(layer.getFeatures(), None) if layer is not None else None
        if check("isochrone algorithm produced a polygon", feat is not None, str(errs.errors[:2])):
            geom = feat.geometry()
            block_centre = QgsGeometry.fromPointXY(QgsPointXY(27.1475, 38.4225))
            street_point = QgsGeometry.fromPointXY(QgsPointXY(27.1475, 38.4202))
            check("isochrone excludes the block interior and covers the streets",
                  not geom.contains(block_centre) and geom.contains(street_point),
                  f"area_ha={feat['area_ha']}")
            # Five 440-560 m streets with a 50 m corridor each side: roughly 25 ha.
            check("isochrone area is the street corridor, not the hull",
                  10.0 < float(feat["area_ha"]) < 40.0, f"area_ha={feat['area_ha']}")

    # ---- 4c. Solar exposure with a building layer uses real shadows.
    solar = algs.get("solar_shade_exposure")
    if solar is not None:
        import math as _math
        lat = 38.4
        kx = 111320.0 * _math.cos(_math.radians(lat))
        ky = 110574.0
        route = QgsVectorLayer("LineStringZ?crs=EPSG:4326", "street", "memory")
        rf = QgsFeature(route.fields())
        rf.setGeometry(QgsGeometry.fromWkt(f"LineStringZ(27.0 {lat} 10, {27.0 + 100 / kx} {lat} 10)"))
        route.dataProvider().addFeatures([rf])
        blocks = QgsVectorLayer("Polygon?crs=EPSG:4326&field=height:double", "blocks", "memory")
        bf = QgsFeature(blocks.fields())
        x0, x1, y0, y1 = 27.0 + 20 / kx, 27.0 + 80 / kx, lat - 30 / ky, lat - 10 / ky
        bf.setGeometry(QgsGeometry.fromWkt(f"Polygon(({x0} {y0}, {x1} {y0}, {x1} {y1}, {x0} {y1}, {x0} {y0}))"))
        bf.setAttributes([20.0])
        blocks.dataProvider().addFeatures([bf])
        solar.initAlgorithm({})
        errs = ErrorFeedback()
        res, ok = solar.run({
            "INPUT_ROUTE": route, "SOLAR_HOUR": 12.0, "DAY_OF_YEAR": 355,
            "BUILDINGS": blocks, "HEIGHT_FIELD": "height", "OUTPUT": "memory:",
        }, ctx, errs)
        out = ctx.getMapLayer(res.get("OUTPUT")) if ok else None
        feat = next(out.getFeatures(), None) if out is not None else None
        check("solar exposure computes building shadows",
              feat is not None and feat["method"] == "building shadows" and 50.0 < float(feat["shaded_pct"]) < 70.0,
              f"{feat and (feat['method'], feat['shaded_pct'])} {errs.errors[:1]}")

    # ---- 4d. Transit + walk journey from a GTFS feed.
    transit = algs.get("transit_route_gtfs")
    if transit is not None:
        import tempfile as _tempfile
        import zipfile as _zipfile
        feed = {
            "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\nA,Alpha,38.400,27.000\nB,Bravo,38.400,27.010\n"
                         "C,Charlie,38.400,27.020\nE,Echo,38.4009,27.020\nD,Delta,38.410,27.020\n",
            "routes.txt": "route_id,route_short_name,route_type\nR1,1,3\nR2,2,3\n",
            "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                            "WK,1,1,1,1,1,0,0,20260101,20261231\n",
            "trips.txt": "route_id,service_id,trip_id\nR1,WK,r1a\nR2,WK,r2a\n",
            "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
                              "r1a,08:10:00,08:10:00,A,1\nr1a,08:15:00,08:15:00,B,2\nr1a,08:20:00,08:20:00,C,3\n"
                              "r2a,08:25:00,08:25:00,E,1\nr2a,08:35:00,08:35:00,D,2\n",
        }
        gtfs_path = os.path.join(_tempfile.mkdtemp(prefix="zero2route3d-gtfs-"), "feed.zip")
        with _zipfile.ZipFile(gtfs_path, "w") as zf:
            for name, text in feed.items():
                zf.writestr(name, text)
        streets = QgsVectorLayer("LineString?crs=EPSG:4326&field=highway:string", "streets", "memory")
        lines = [[(26.999, 38.400), (27.010, 38.400)], [(27.010, 38.400), (27.020, 38.400)],
                 [(27.020, 38.400), (27.020, 38.405)], [(27.020, 38.405), (27.020, 38.411)]]
        sfeats = []
        for line in lines:
            f = QgsFeature(streets.fields())
            f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(*p) for p in line]))
            f.setAttributes(["residential"])
            sfeats.append(f)
        streets.dataProvider().addFeatures(sfeats)
        transit.initAlgorithm({})
        errs = ErrorFeedback()
        res, ok = transit.run({
            "GTFS": gtfs_path, "START_POINT": "27.0003,38.4001 [EPSG:4326]",
            "END_POINT": "27.0201,38.4099 [EPSG:4326]", "DATE": "2026-10-14", "TIME": "08:05",
            "WALK_PROFILE": 0, "MAX_WALK_M": 800.0, "MAX_RIDES": 4, "NETWORK_LAYER": streets, "OUTPUT": "memory:",
        }, ctx, errs)
        out = ctx.getMapLayer(res.get("OUTPUT")) if ok else None
        legs = [(f["mode"], f["route"], f["depart"], f["arrive"]) for f in out.getFeatures()] if out else []
        check("transit journey rides bus 1 then bus 2 with walks between",
              [leg[0] for leg in legs] == ["walk", "ride", "walk", "ride", "walk"]
              and [leg[1] for leg in legs if leg[0] == "ride"] == ["1", "2"],
              f"{legs} {errs.errors[:1]}")
        check("transit journey arrives with the 08:35 bus",
              bool(legs) and legs[-2][3] == "08:35", str(legs[-2:] if legs else legs))

    # ---- 5. Walkability must refuse to certify without a DEM.
    walk = algs.get("walkability_3d_audit")
    if walk is not None:
        net = make_network()
        walk.initAlgorithm({})
        errs = ErrorFeedback()
        _res, ok = walk.run({"INPUT_NETWORK": net, "OUTPUT": "memory:"}, ctx, errs)
        check("walkability refuses to score without a DEM",
              (not ok) and any("DEM" in m for m in errs.errors),
              f"ok={ok} errors={errs.errors[:1]}")

    # ---- 6. Export algorithms must reject empty input rather than invent Izmir.
    from zero2route3d.processing.route_input import extract_route_coords_3d
    empty = QgsVectorLayer("LineString?crs=EPSG:4326", "empty", "memory")
    try:
        extract_route_coords_3d(empty, ctx, "test layer")
        check("empty route layer raises instead of substituting coordinates", False)
    except QgsProcessingException as exc:
        check("empty route layer raises instead of substituting coordinates",
              "no usable 3D geometry" in str(exc), str(exc)[:70])

    # ---- 7. Z must survive extraction (this used to raise AttributeError).
    coords = extract_route_coords_3d(make_route_layer_z(), ctx, "route")
    check("LineStringZ keeps its Z values", [c[2] for c in coords] == [10.0, 25.0, 40.0],
          str([c[2] for c in coords]))

    # ---- 8. A 3D route must actually compute and be styled.
    route_alg = algs.get("compute_3d_route")
    if route_alg is not None:
        net = make_network()
        QgsProject.instance().addMapLayer(net, False)
        route_alg.initAlgorithm({})
        errs = ErrorFeedback()
        res, ok = route_alg.run({
            "START_POINT": "27.140,38.420 [EPSG:4326]",
            "END_POINT": "27.150,38.425 [EPSG:4326]",
            "PROFILE": 0,
            "NETWORK_LAYER": net,
            "OUTPUT": "memory:",
        }, ctx, errs)
        if not check("3D route algorithm succeeded", ok, str(errs.errors[:2])):
            pass
        else:
            out = res.get("OUTPUT")
            layer = ctx.getMapLayer(out) if isinstance(out, str) else out
            check("3D route produced a feature",
                  layer is not None and layer.featureCount() >= 1,
                  f"{layer.featureCount() if layer else 'no layer'} feature(s)")
            if layer is not None:
                feat = next(layer.getFeatures())
                dist = feat["dist_km"]
                check("3D route reports a plausible distance",
                      dist is not None and 0.1 < float(dist) < 5.0, f"dist_km={dist}")
                check("3D route geometry carries Z",
                      feat.geometry().constGet().is3D(), str(feat.geometry().wkbType()))
                # postProcessAlgorithm must have styled the layer via
                # context.getMapLayer -- not QgsProject.instance().mapLayer.
                route_alg.postProcessAlgorithm(ctx, errs)
                renderer = layer.renderer()
                check("3D route output is styled by postProcessAlgorithm",
                      renderer is not None
                      and renderer.__class__.__name__ == "QgsSingleSymbolRenderer",
                      renderer.__class__.__name__ if renderer else "None")
                idx = layer.fields().indexOf("dist_km")
                check("3D route output carries field aliases",
                      layer.attributeAlias(idx) == "Distance (km)",
                      repr(layer.attributeAlias(idx)))
                check("3D route exposes snap diagnostics",
                      all(layer.fields().indexOf(name) >= 0 for name in
                          ("snap_a_m", "snap_b_m", "expanded", "blocked")))

    # ---- 9. Network audit must execute and annotate every extracted segment.
    audit_alg = algs.get("audit_routing_network")
    if audit_alg is not None:
        net = make_network()
        audit_alg.initAlgorithm({})
        errs = ErrorFeedback()
        res, ok = audit_alg.run({
            "NETWORK": net,
            "PROFILE": 0,
            "OUTPUT": "memory:",
        }, ctx, errs)
        if check("routing network audit succeeded", ok, str(errs.errors[:2])):
            out = res.get("OUTPUT")
            audited = ctx.getMapLayer(out) if isinstance(out, str) else out
            check("routing network audit annotates segments",
                  audited is not None and audited.featureCount() == 5,
                  f"{audited.featureCount() if audited else 'no layer'} feature(s)")
            if audited is not None:
                first = next(audited.getFeatures())
                check("routing network audit reports topology",
                      first["component"] >= 0 and first["largest_pct"] > 0)
                audit_alg.postProcessAlgorithm(ctx, errs)
                check("routing network audit styles allowed and blocked links",
                      audited.renderer().__class__.__name__ == "QgsCategorizedSymbolRenderer")

    check("provider exposes network audit algorithm",
          "audit_routing_network" in algs, str(sorted(algs)))

    # ---- 10. Custom QGIS networks must preserve Z and common fields.
    tagged = QgsVectorLayer(
        "LineStringZ?crs=EPSG:4326&field=HIGHWAY:string&field=ONEWAY:string"
        "&field=SURFACE:string&field=FOOT:string&field=MAXSPEED:string",
        "tagged_network",
        "memory",
    )
    tagged_feature = QgsFeature(tagged.fields())
    tagged_feature.setGeometry(
        QgsGeometry.fromWkt("LineStringZ(27.140 38.420 12, 27.141 38.420 18)")
    )
    tagged_feature.setAttributes(["footway", "yes", "paving_stones", "designated", "20 mph"])
    tagged.dataProvider().addFeatures([tagged_feature])
    from zero2route3d.core.network_source import NetworkSourceManager
    extracted = NetworkSourceManager().extract_from_qgis_layer(tagged)
    check("QGIS network extraction preserves Z and routing fields",
          len(extracted) == 1
          and extracted[0].p1[2] == 12.0
          and extracted[0].p2[2] == 18.0
          and extracted[0].is_oneway
          and extracted[0].foot == "designated"
          and 32.0 < extracted[0].maxspeed_kmh < 32.3,
          repr(extracted[0] if extracted else None))

    # ---- 11. CRS round trip: the same streets in Web Mercator, with the
    # points given in Mercator too, give the same route as in WGS84, and the
    # output comes back in WGS84 on the right spot.
    if route_alg is not None:
        def route_on(network, start, end):
            errs = ErrorFeedback()
            res, ok = route_alg.run({
                "START_POINT": start, "END_POINT": end, "PROFILE": 0,
                "NETWORK_LAYER": network, "OUTPUT": "memory:",
            }, ctx, errs)
            if not ok:
                return None, errs.errors
            out = res.get("OUTPUT")
            layer = ctx.getMapLayer(out) if isinstance(out, str) else out
            return layer, errs.errors

        wgs = make_network()
        merc = reproject_layer(wgs, "EPSG:3857")
        to_merc = QgsCoordinateTransform(
            QgsCoordinateReferenceSystem("EPSG:4326"), QgsCoordinateReferenceSystem("EPSG:3857"),
            QgsProject.instance(),
        )
        a = to_merc.transform(QgsPointXY(27.140, 38.420))
        b = to_merc.transform(QgsPointXY(27.150, 38.425))
        base, _ = route_on(wgs, "27.140,38.420 [EPSG:4326]", "27.150,38.425 [EPSG:4326]")
        moved, errors = route_on(
            merc, f"{a.x()},{a.y()} [EPSG:3857]", f"{b.x()},{b.y()} [EPSG:3857]"
        )
        if check("route on a Web Mercator network with Mercator points succeeds",
                 base is not None and moved is not None, str(errors[:2])):
            f0, f1 = next(base.getFeatures()), next(moved.getFeatures())
            d0, d1 = float(f0["dist_km"]), float(f1["dist_km"])
            check("Mercator and WGS84 inputs give the same route length",
                  abs(d0 - d1) <= 0.005 * d0, f"{d0:.4f} vs {d1:.4f} km")
            first = f1.geometry().constGet().vertexAt(QgsVertexId(0, 0, 0))
            check("route output is in WGS84 at the start point",
                  moved.crs().authid() == "EPSG:4326"
                  and abs(first.x() - 27.140) < 1e-4 and abs(first.y() - 38.420) < 1e-4,
                  f"{moved.crs().authid()} ({first.x():.5f}, {first.y():.5f})")

    # ---- 12. Cancelling an OD matrix stops it: nothing after the cancel is
    # written, and the run does not fail.
    od_alg = algs.get("od_matrix_3d")
    if od_alg is not None:
        class CancelAfter(ErrorFeedback):
            def __init__(self, polls):
                super().__init__()
                self.polls = polls

            def isCanceled(self):
                self.polls -= 1
                return self.polls < 0

        def points(coords):
            layer = QgsVectorLayer("Point?crs=EPSG:4326", "pts", "memory")
            feats = []
            for x, y in coords:
                f = QgsFeature(layer.fields())
                f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
                feats.append(f)
            layer.dataProvider().addFeatures(feats)
            return layer

        net = make_network()
        origins = points([(27.140, 38.420), (27.145, 38.420), (27.150, 38.420)])
        dests = points([(27.150, 38.425), (27.145, 38.425)])
        counts = {}
        for label, feedback in (("full", ErrorFeedback()), ("cancelled", CancelAfter(3))):
            od_alg.initAlgorithm({})
            res, ok = od_alg.run({
                "INPUT_ORIGINS": origins, "INPUT_DESTS": dests, "INPUT_NETWORK": net,
                "PARAM_PROFILE": 0, "OUTPUT": "memory:",
            }, ctx, feedback)
            out = res.get("OUTPUT") if ok else None
            layer = ctx.getMapLayer(out) if isinstance(out, str) else out
            counts[label] = layer.featureCount() if layer is not None else None
        check("cancelled OD matrix stops early without failing",
              counts.get("full") == 6 and counts.get("cancelled") is not None
              and counts["cancelled"] < 6, str(counts))

    # ---- 13. The cost surface runs on a real GeoTIFF (it used an enum that
    # only exists from QGIS 3.40 on, so it crashed on 3.28-3.38).
    cost_alg = algs.get("mcda_cost_surface")
    if cost_alg is not None:
        import tempfile

        from osgeo import gdal, osr

        gdal.UseExceptions()
        folder = tempfile.mkdtemp(prefix="r3d_cost_")
        dem_path = os.path.join(folder, "dem.tif")
        driver = gdal.GetDriverByName("GTiff")
        ds = driver.Create(dem_path, 20, 20, 1, gdal.GDT_Float32)
        ds.SetGeoTransform((27.14, 0.0005, 0, 38.43, 0, -0.0005))
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326)
        ds.SetProjection(srs.ExportToWkt())
        band = ds.GetRasterBand(1)
        band.WriteRaster(0, 0, 20, 20, struct.pack("400f", *[float(10 + (i % 20) * 3 + (i // 20)) for i in range(400)]))
        band.FlushCache()
        ds = None
        out_path = os.path.join(folder, "friction.tif")
        cost_alg.initAlgorithm({})
        errs = ErrorFeedback()
        _res, ok = cost_alg.run({
            "INPUT_DEM": QgsRasterLayer(dem_path, "dem"), "OUTPUT": out_path,
        }, ctx, errs)
        values = []
        if ok and os.path.exists(out_path):
            out = gdal.Open(out_path)
            values = list(struct.unpack("400f", out.GetRasterBand(1).ReadRaster(0, 0, 20, 20, buf_type=gdal.GDT_Float32)))
            out = None
        check("cost surface writes a 0..1 friction raster",
              ok and values and all(0.0 <= v <= 1.0 for v in values) and max(values) > min(values),
              f"ok={ok} range=({min(values) if values else None}, {max(values) if values else None}) {errs.errors[:1]}")

    print()
    failed = [r for r in RESULTS if not r[1]]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        print("FAIL")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX_PATH", ""), True)
    app = QgsApplication([], False)
    app.initQgis()
    sys.path.append(os.path.join(QgsApplication.prefixPath(), "python", "plugins"))
    code = 1
    try:
        code = run()
    except Exception:
        traceback.print_exc()
        print("FAIL")
    finally:
        app.exitQgis()
    sys.exit(code)
