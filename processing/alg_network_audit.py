"""QGIS Processing algorithm for profile-aware routing network QA."""
from __future__ import annotations

from typing import Any, Dict

from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsCategorizedSymbolRenderer,
    QgsFields,
    QgsGeometry,
    QgsPoint,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingException,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterVectorLayer,
    QgsRendererCategory,
    QgsLineSymbol,
    QgsWkbTypes,
)

from ..core.mobility_profiles import get_profile, list_profile_keys
from ..core.network_audit import audit_network
from ..core.network_source import NetworkSourceManager
from .crs_utils import wgs84
from .field_utils import BOOL, DOUBLE, INT, STRING, make_field
from .post_process import finalize_output, resolve_output_layer


class RoutingNetworkAuditAlgorithm(QgsProcessingAlgorithm):
    """Annotate network links with topology and modal-access findings."""

    NETWORK = "NETWORK"
    PROFILE = "PROFILE"
    OUTPUT = "OUTPUT"

    def name(self) -> str:
        return "audit_routing_network"

    def displayName(self) -> str:
        return "Audit Routing Network Readiness"

    def group(self) -> str:
        return "3D Mobility & Routing"

    def groupId(self) -> str:
        return "route3d"

    def tags(self) -> list[str]:
        return ["network", "audit", "topology", "access", "oneway", "surface", "quality"]

    def shortHelpString(self) -> str:
        return (
            "Checks a line network before routing and writes one annotated feature "
            "per segment. The selected mobility profile is evaluated against OSM-style "
            "access, foot, bicycle, motor_vehicle, highway, surface, and one-way fields. "
            "The output also reports weak-component membership and overall connectivity."
        )

    def initAlgorithm(self, config: Dict[str, Any] = None) -> None:
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.NETWORK,
                "Road Network Layer",
                types=[QgsProcessing.TypeVectorLine],
            )
        )
        self.profile_keys = list_profile_keys()
        self.addParameter(
            QgsProcessingParameterEnum(
                self.PROFILE,
                "Mobility Profile",
                options=[get_profile(key).name for key in self.profile_keys],
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                "Audited Network Segments",
                type=QgsProcessing.TypeVectorLine,
            )
        )

    def processAlgorithm(
        self,
        parameters: Dict[str, Any],
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> Dict[str, Any]:
        layer = self.parameterAsVectorLayer(parameters, self.NETWORK, context)
        profile_index = self.parameterAsEnum(parameters, self.PROFILE, context)
        profile_keys = list_profile_keys()
        profile_key = profile_keys[max(0, min(profile_index, len(profile_keys) - 1))]

        segments = NetworkSourceManager().extract_from_qgis_layer(layer)
        if not segments:
            raise QgsProcessingException("The network contains no usable line segments.")
        report = audit_network(segments, profile_key)

        fields = QgsFields()
        for name, kind in (
            ("segment_id", INT),
            ("profile", STRING),
            ("allowed", BOOL),
            ("reason", STRING),
            ("component", INT),
            ("oneway", BOOL),
            ("surface_q", DOUBLE),
            ("components", INT),
            ("largest_pct", DOUBLE),
            ("network_ok", BOOL),
        ):
            fields.append(make_field(name, kind))

        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, QgsWkbTypes.LineStringZ, wgs84()
        )
        if sink is None:
            raise QgsProcessingException("Could not create the audited network output.")

        for segment, finding in zip(segments, report.findings):
            if feedback.isCanceled():
                break
            feature = QgsFeature(fields)
            feature.setGeometry(
                QgsGeometry.fromPolyline(
                    [QgsPoint(*segment.p1), QgsPoint(*segment.p2)]
                )
            )
            feature.setAttributes(
                [
                    finding["segment_id"],
                    profile_key,
                    finding["allowed"],
                    finding["reason"],
                    finding["component"],
                    segment.is_oneway,
                    finding["surface_quality"],
                    report.component_count,
                    report.largest_component_pct,
                    report.ready,
                ]
            )
            sink.addFeature(feature, QgsFeatureSink.FastInsert)

        feedback.pushInfo(
            f"{report.allowed_segments}/{report.segment_count} segments allowed; "
            f"{report.component_count} component(s); largest component "
            f"{report.largest_component_pct:.1f}% of nodes."
        )
        self._dest_id = dest_id
        return {self.OUTPUT: dest_id}

    def postProcessAlgorithm(
        self, context: QgsProcessingContext, feedback: QgsProcessingFeedback
    ) -> Dict[str, Any]:
        finalize_output(
            context,
            getattr(self, "_dest_id", ""),
            title="Routing Network Readiness Audit",
            abstract="Profile-aware network access and topology audit for 02Route 3D.",
            aliases={
                "allowed": "Traversable by selected profile",
                "reason": "Access decision",
                "component": "Weak component identifier",
                "surface_q": "Surface smoothness (0-1)",
                "largest_pct": "Nodes in largest component (%)",
                "network_ok": "Network readiness check",
            },
            line_color="#f59e0b",
            feedback=feedback,
        )
        layer = resolve_output_layer(context, getattr(self, "_dest_id", ""))
        if layer is not None:
            categories = []
            for value, label, color in (
                (True, "Traversable", "#16a34a"),
                (False, "Blocked", "#dc2626"),
            ):
                symbol = QgsLineSymbol.createSimple(
                    {"color": color, "width": "0.8"}
                )
                categories.append(QgsRendererCategory(value, symbol, label))
            layer.setRenderer(QgsCategorizedSymbolRenderer("allowed", categories))
            layer.triggerRepaint()
        return {}

    def createInstance(self) -> "RoutingNetworkAuditAlgorithm":
        return RoutingNetworkAuditAlgorithm()
