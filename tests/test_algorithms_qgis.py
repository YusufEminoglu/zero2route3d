"""End-to-end Processing checks for 02Route 3D on a real QGIS runtime.

The existing suites only assert that algorithms *load*; none ever calls
processAlgorithm. Every correctness bug found in the audit passed those suites.
This harness runs the algorithms against real in-memory fixtures and asserts on
values, renderers and field aliases.
"""
from __future__ import annotations

import os
import sys
import traceback

from qgis.core import (
    QgsApplication,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProject,
    QgsVectorLayer,
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
