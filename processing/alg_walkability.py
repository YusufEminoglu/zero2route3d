"""Processing algorithm for 3D Walkability and Universal Barrier-Free Accessibility Audit."""
from __future__ import annotations

import contextlib
import math
from typing import Any, Dict

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterRasterLayer,
    QgsWkbTypes,
)

from ..core.environmental_raster import EnvironmentalSurfaceSampler


class WalkabilityAuditAlgorithm(QgsProcessingAlgorithm):
    """Audits a street line layer for 3D pedestrian accessibility, steep gradients, and ADA compliance."""

    INPUT_NETWORK = "INPUT_NETWORK"
    INPUT_DEM = "INPUT_DEM"
    OUTPUT = "OUTPUT"

    def createInstance(self) -> QgsProcessingAlgorithm:
        return WalkabilityAuditAlgorithm()

    def name(self) -> str:
        return "walkability_3d_audit"

    def displayName(self) -> str:
        return "3D Walkability & Barrier-Free Accessibility Audit"

    def group(self) -> str:
        return "3D Mobility & Routing"

    def groupId(self) -> str:
        return "route3d"

    def tags(self) -> list[str]:
        return ["walkability", "ada", "barrier-free", "wheelchair", "stroller", "pedestrian audit", "slope compliance", "cross-slope", "active transport"]

    def shortHelpString(self) -> str:
        return "Audits road and footpath networks for steep slopes, ADA 1:12 barrier-free compliance, and scores 3D pedestrian walkability (0-100)."

    def initAlgorithm(self, config: Dict[str, Any] = None) -> None:
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_NETWORK,
                "Pedestrian & Road Network (Line Layer)",
                [QgsProcessing.TypeVectorLine],
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.INPUT_DEM,
                "Elevation Model (DEM Raster)",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                "Audited 3D Walkability Network",
                type=QgsProcessing.TypeVectorLine,
            )
        )

    @staticmethod
    def _to_wgs84(transform: QgsCoordinateTransform, point: Any) -> Any:
        """Reproject one point to WGS84, or None when the transform rejects it."""
        with contextlib.suppress(Exception):
            return transform.transform(point)
        return None

    def processAlgorithm(
        self,
        parameters: Dict[str, Any],
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> Dict[str, Any]:
        source = self.parameterAsSource(parameters, self.INPUT_NETWORK, context)
        dem_layer = self.parameterAsRasterLayer(parameters, self.INPUT_DEM, context)

        fields = QgsFields()
        fields.append(QgsField("length_m", 6))
        fields.append(QgsField("slope_pct", 6))
        fields.append(QgsField("is_ada_ok", 1))  # 1 for Yes, 0 for No
        fields.append(QgsField("walk_score", 6))  # 0 to 100

        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            QgsWkbTypes.LineString,
            source.sourceCrs(),
        )

        sampler = EnvironmentalSurfaceSampler(dem_layer=dem_layer)
        if not sampler.has_elevation_source:
            # Without a DEM every segment reads as 0% slope, so the audit would
            # certify an entire hillside city as barrier-free. Refuse instead.
            raise QgsProcessingException(
                "A DEM raster is required: a barrier-free audit cannot be produced "
                "without elevation data, because every segment would score as flat "
                "and fully ADA-compliant."
            )

        # Lengths must be measured on the ellipsoid. geom.length() returns degrees
        # for a geographic layer, which made slope percentages ~10^5 and zeroed
        # every walk score.
        distance_area = QgsDistanceArea()
        distance_area.setSourceCrs(source.sourceCrs(), context.transformContext())
        distance_area.setEllipsoid(context.project().ellipsoid() if context.project() else "WGS84")

        # The sampler expects WGS84 lon/lat.
        to_wgs84 = QgsCoordinateTransform(
            source.sourceCrs(),
            QgsCoordinateReferenceSystem("EPSG:4326"),
            context.transformContext(),
        )

        total_feats = source.featureCount()
        count = 0
        skipped_no_dem = 0

        for feat in source.getFeatures():
            if feedback.isCanceled():
                break

            geom = feat.geometry()
            if geom.isNull() or geom.isEmpty():
                continue

            length_m = distance_area.measureLength(geom)
            if not math.isfinite(length_m) or length_m < 0.1:
                continue

            p_start = geom.asPolyline()[0] if not geom.isMultipart() else geom.asMultiPolyline()[0][0]
            p_end = geom.asPolyline()[-1] if not geom.isMultipart() else geom.asMultiPolyline()[0][-1]

            w_start = self._to_wgs84(to_wgs84, p_start)
            w_end = self._to_wgs84(to_wgs84, p_end)
            if w_start is None or w_end is None:
                skipped_no_dem += 1
                continue

            z1 = sampler.sample_elevation(w_start.x(), w_start.y())
            z2 = sampler.sample_elevation(w_end.x(), w_end.y())
            if z1 is None or z2 is None:
                # Outside the DEM: emit no score rather than a flattering one.
                skipped_no_dem += 1
                continue

            dz = abs(z2 - z1)
            slope_pct = (dz / max(0.1, length_m)) * 100.0

            is_ada = 1 if slope_pct <= 8.33 else 0
            walk_score = max(0.0, min(100.0, 100.0 - (slope_pct / 15.0) * 80.0))

            out_feat = QgsFeature(fields)
            out_feat.setGeometry(geom)
            out_feat.setAttributes([
                round(length_m, 1),
                round(slope_pct, 1),
                is_ada,
                round(walk_score, 1),
            ])
            sink.addFeature(out_feat, QgsFeatureSink.FastInsert)

            count += 1
            if total_feats > 0:
                feedback.setProgress(int((count / total_feats) * 100.0))

        if skipped_no_dem:
            feedback.pushWarning(
                f"{skipped_no_dem} segment(s) fell outside the DEM and were omitted "
                f"rather than scored against missing elevation."
            )
        if count == 0:
            raise QgsProcessingException(
                "No segment could be audited: every feature was empty, shorter than "
                "0.1 m, or outside the supplied DEM."
            )

        return {self.OUTPUT: dest_id}
