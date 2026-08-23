"""Processing algorithm for Spatial Accessibility & Transport Equity Scorecard (E2SFCA)."""
from __future__ import annotations

from typing import Any, Dict

from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPoint,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
    QgsWkbTypes,
)

from ..core.accessibility_equity import (
    AccessibilityEquityEngine,
    SupplyFacility,
    ZoneAccessibilityRecord,
)


class AccessibilityEquityAlgorithm(QgsProcessingAlgorithm):
    """Computes Enhanced 2-Step Floating Catchment Area (E2SFCA) accessibility & Gini equity indices."""

    INPUT_DEMAND = "INPUT_DEMAND"
    INPUT_FACILITIES = "INPUT_FACILITIES"
    CATCHMENT_RADIUS = "CATCHMENT_RADIUS"
    OUTPUT_ZONES = "OUTPUT_ZONES"

    def createInstance(self) -> QgsProcessingAlgorithm:
        return AccessibilityEquityAlgorithm()

    def name(self) -> str:
        return "accessibility_equity_scorecard"

    def displayName(self) -> str:
        return "Spatial Accessibility & Equity Scorecard (E2SFCA & Gini)"

    def group(self) -> str:
        return "3D Mobility & Routing"

    def groupId(self) -> str:
        return "route3d"

    def tags(self) -> list[str]:
        return ["accessibility", "equity", "e2sfca", "gini", "spatial justice", "palma", "catchment", "transit desert", "social vulnerability"]

    def shortHelpString(self) -> str:
        return "Computes population accessibility indices (E2SFCA), Gini coefficient, Palma ratio, and equity tiers (Transit Deserts vs Oases)."

    def initAlgorithm(self, config: Dict[str, Any] = None) -> None:
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_DEMAND,
                "Population Demand Zones (Points or Centroids)",
                [QgsProcessing.TypeVectorPoint],
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_FACILITIES,
                "Service / Transit Supply Facilities Layer",
                [QgsProcessing.TypeVectorPoint],
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.CATCHMENT_RADIUS,
                "Catchment Radius Buffer (meters)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=1500.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_ZONES,
                "Accessibility & Equity Analyzed Zones",
                type=QgsProcessing.TypeVectorPoint,
            )
        )

    def processAlgorithm(
        self,
        parameters: Dict[str, Any],
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> Dict[str, Any]:
        source_demand = self.parameterAsSource(parameters, self.INPUT_DEMAND, context)
        source_facs = self.parameterAsSource(parameters, self.INPUT_FACILITIES, context)
        radius = self.parameterAsDouble(parameters, self.CATCHMENT_RADIUS, context)

        fields = QgsFields()
        fields.append(QgsField("zone_id", 10))
        fields.append(QgsField("acc_score", 6))
        fields.append(QgsField("equity_tier", 10))
        fields.append(QgsField("pct_rank", 6))

        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT_ZONES,
            context,
            fields,
            QgsWkbTypes.Point,
            source_demand.sourceCrs(),
        )

        demand_records = []
        for f in source_demand.getFeatures():
            p = f.geometry().asPoint()
            pop = float(f["population"]) if "population" in f.fields().names() else 1000.0
            demand_records.append(
                ZoneAccessibilityRecord(
                    zone_id=str(f.id()),
                    name=f"Zone_{f.id()}",
                    lon=p.x(),
                    lat=p.y(),
                    population=max(1.0, pop),
                )
            )

        facility_records = []
        for f in source_facs.getFeatures():
            p = f.geometry().asPoint()
            cap = float(f["capacity"]) if "capacity" in f.fields().names() else 100.0
            facility_records.append(
                SupplyFacility(
                    facility_id=str(f.id()),
                    name=f"Fac_{f.id()}",
                    lon=p.x(),
                    lat=p.y(),
                    capacity=max(1.0, cap),
                )
            )

        engine = AccessibilityEquityEngine(catchment_radius_m=radius)
        res = engine.compute_e2sfca(demand_records, facility_records)

        for z in res.zones:
            geom = QgsGeometry.fromPointXY(QgsPoint(z.lon, z.lat))
            feat = QgsFeature(fields)
            feat.setGeometry(geom)
            feat.setAttributes([
                z.zone_id,
                z.accessibility_score,
                z.equity_tier,
                z.percentile_rank,
            ])
            sink.addFeature(feat, QgsFeatureSink.FastInsert)

        return {self.OUTPUT_ZONES: dest_id}
