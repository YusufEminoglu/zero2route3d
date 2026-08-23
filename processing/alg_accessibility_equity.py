"""Processing algorithm for Spatial Accessibility & Transport Equity Scorecard (E2SFCA)."""
from __future__ import annotations

from typing import Any, Dict

from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
    QgsWkbTypes,
)

from .field_utils import DOUBLE, STRING, make_field
from .post_process import finalize_output
from .crs_utils import point_to_wgs84, wgs84, require_matching_crs
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
        require_matching_crs([
            ("demand zones", source_demand.sourceCrs() if source_demand else None),
            ("facilities", source_facs.sourceCrs() if source_facs else None),
        ])
        radius = self.parameterAsDouble(parameters, self.CATCHMENT_RADIUS, context)

        fields = QgsFields()
        fields.append(make_field("zone_id", STRING))
        fields.append(make_field("acc_score", DOUBLE))
        fields.append(make_field("equity_tier", STRING))
        fields.append(make_field("pct_rank", DOUBLE))

        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT_ZONES,
            context,
            fields,
            QgsWkbTypes.Point,
            # The engine emits WGS84 lon/lat, so the sink must declare WGS84.
            wgs84(),
        )

        demand_records = []
        for f in source_demand.getFeatures():
            p = point_to_wgs84(f.geometry().asPoint(), source_demand.sourceCrs(), context)
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
            p = point_to_wgs84(f.geometry().asPoint(), source_facs.sourceCrs(), context)
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
            geom = QgsGeometry.fromPointXY(QgsPointXY(z.lon, z.lat))
            feat = QgsFeature(fields)
            feat.setGeometry(geom)
            feat.setAttributes([
                z.zone_id,
                z.accessibility_score,
                z.equity_tier,
                z.percentile_rank,
            ])
            sink.addFeature(feat, QgsFeatureSink.FastInsert)

        # Remembered so postProcessAlgorithm can resolve and style the layer.
        self._dest_id = dest_id
        return {self.OUTPUT_ZONES: dest_id}

    def postProcessAlgorithm(
        self,
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> Dict[str, Any]:
        # The destination layer MUST be resolved through context.getMapLayer().
        # QgsProject.instance().mapLayer() returns None here, which turns every
        # styling and metadata call below into a silent no-op.
        finalize_output(
            context,
            getattr(self, "_dest_id", ""),
            title='Accessibility & Equity Scorecard',
            abstract='Two-step floating catchment accessibility scores per demand zone, with Gini and Palma inequality measures. Population and capacity come from the selected fields.',
            aliases={'zone_id': 'Zone ID', 'name': 'Zone name', 'population': 'Population', 'access': 'Accessibility score', 'tier': 'Equity tier'},
            point_color='#16a34a',
            feedback=feedback,
        )
        return {}
