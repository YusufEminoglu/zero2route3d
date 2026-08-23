"""Processing algorithm for Multi-Criteria Raster Cost Surface Generation."""
from __future__ import annotations

import math
from typing import Any, Dict, List

from qgis.core import (
    Qgis,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
    QgsRasterBlock,
    QgsRasterFileWriter,
)

from ..core.ahp_engine import AHPEngine


class MultiCriteriaCostSurfaceAlgorithm(QgsProcessingAlgorithm):
    """Generates a normalized multi-criteria friction (impedance) raster."""

    INPUT_DEM = "INPUT_DEM"
    INPUT_LST = "INPUT_LST"
    INPUT_GREEN = "INPUT_GREEN"
    WEIGHT_SLOPE = "WEIGHT_SLOPE"
    WEIGHT_HEAT = "WEIGHT_HEAT"
    WEIGHT_GREEN = "WEIGHT_GREEN"
    OUTPUT = "OUTPUT"

    def createInstance(self) -> QgsProcessingAlgorithm:
        return MultiCriteriaCostSurfaceAlgorithm()

    def name(self) -> str:
        return "mcda_cost_surface"

    def displayName(self) -> str:
        return "MCDA Multi-Criteria Friction Surface"

    def group(self) -> str:
        return "3D Mobility & Routing"

    def groupId(self) -> str:
        return "route3d"

    def tags(self) -> List[str]:
        return [
            "cost surface", "mcda", "ahp", "raster", "impedance", "resistance",
            "slope", "heat", "dem", "saaty", "friction",
        ]

    def shortHelpString(self) -> str:
        return (
            "Combines up to three real input rasters -- elevation, land surface "
            "temperature, and a vegetation index -- into a single normalized friction "
            "surface ranging from 0.0 (easiest to traverse) to 1.0 (hardest).\n\n"
            "Criterion weights are entered on the Saaty 1-9 scale and resolved through "
            "an AHP pairwise matrix. The consistency ratio is reported in the log and "
            "the run is rejected when CR exceeds 0.10.\n\n"
            "At least one input raster is required. Only the supplied criteria "
            "contribute: a missing layer is dropped from the weighting rather than "
            "replaced by an assumed value. All inputs must share the same CRS, extent "
            "and pixel grid."
        )

    def initAlgorithm(self, config: Dict[str, Any] = None) -> None:
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.INPUT_DEM,
                "Elevation (DEM) Layer",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.INPUT_LST,
                "Thermal (LST) Surface Layer",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.INPUT_GREEN,
                "Greenery (NDVI) Layer",
                optional=True,
            )
        )
        for key, label, default in (
            (self.WEIGHT_SLOPE, "Slope Weight (Saaty 1-9)", 5.0),
            (self.WEIGHT_HEAT, "Heat Avoidance Weight (Saaty 1-9)", 3.0),
            (self.WEIGHT_GREEN, "Green Preference Weight (Saaty 1-9)", 2.0),
        ):
            self.addParameter(
                QgsProcessingParameterNumber(
                    key,
                    label,
                    type=QgsProcessingParameterNumber.Double,
                    defaultValue=default,
                    minValue=1.0,
                    maxValue=9.0,
                )
            )
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.OUTPUT,
                "Multi-Criteria Friction Surface",
            )
        )

    def processAlgorithm(
        self,
        parameters: Dict[str, Any],
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> Dict[str, Any]:
        dem = self.parameterAsRasterLayer(parameters, self.INPUT_DEM, context)
        lst = self.parameterAsRasterLayer(parameters, self.INPUT_LST, context)
        green = self.parameterAsRasterLayer(parameters, self.INPUT_GREEN, context)

        criteria: List[Any] = []
        if dem is not None:
            criteria.append(
                ("slope", dem, self.parameterAsDouble(parameters, self.WEIGHT_SLOPE, context))
            )
        if lst is not None:
            criteria.append(
                ("heat", lst, self.parameterAsDouble(parameters, self.WEIGHT_HEAT, context))
            )
        if green is not None:
            criteria.append(
                ("green", green, self.parameterAsDouble(parameters, self.WEIGHT_GREEN, context))
            )

        if not criteria:
            raise QgsProcessingException(
                "Supply at least one input raster. A friction surface cannot be "
                "derived from weights alone."
            )

        reference = criteria[0][1]
        width = reference.width()
        height = reference.height()
        extent = reference.extent()
        if width <= 0 or height <= 0:
            raise QgsProcessingException("The reference raster reports an empty pixel grid.")

        for name, layer, _weight in criteria[1:]:
            if layer.width() != width or layer.height() != height:
                raise QgsProcessingException(
                    "The '{0}' raster is {1}x{2} but the reference raster is {3}x{4}. "
                    "Align the inputs (same CRS, extent and resolution) before "
                    "combining them.".format(name, layer.width(), layer.height(), width, height)
                )
            if layer.crs() != reference.crs():
                raise QgsProcessingException(
                    "The '{0}' raster is in {1} but the reference raster is in {2}. "
                    "Reproject first.".format(name, layer.crs().authid(), reference.crs().authid())
                )

        weights = self._ahp_weights([(name, w) for name, _layer, w in criteria], feedback)

        # Per-criterion min/max come from the real band statistics, so normalization
        # reflects the actual data instead of an assumed value range. This is what
        # made a Kelvin or scaled-integer raster clamp to 1.0 everywhere before.
        ranges: Dict[str, Any] = {}
        for name, layer, _w in criteria:
            stats = layer.dataProvider().bandStatistics(
                1, Qgis.RasterBandStatistic.Min | Qgis.RasterBandStatistic.Max
            )
            lo, hi = float(stats.minimumValue), float(stats.maximumValue)
            if not math.isfinite(lo) or not math.isfinite(hi) or hi <= lo:
                raise QgsProcessingException(
                    "The '{0}' raster has no usable value range "
                    "(min={1}, max={2}).".format(name, lo, hi)
                )
            ranges[name] = (lo, hi)

        out_path = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
        writer = QgsRasterFileWriter(out_path)
        provider = writer.createOneBandRaster(
            Qgis.DataType.Float32, width, height, extent, reference.crs()
        )
        if provider is None or not provider.isValid():
            raise QgsProcessingException(
                "Could not create the output raster at {0}.".format(out_path)
            )
        provider.setNoDataValue(1, -9999.0)

        blocks = {
            name: layer.dataProvider().block(1, extent, width, height)
            for name, layer, _w in criteria
        }

        out_block = QgsRasterBlock(Qgis.DataType.Float32, width, 1)
        for row in range(height):
            if feedback.isCanceled():
                break
            for col in range(width):
                total = 0.0
                covered = 0.0
                for name, _layer, _w in criteria:
                    block = blocks[name]
                    if block is None or block.isNoData(row, col):
                        continue
                    lo, hi = ranges[name]
                    value = (float(block.value(row, col)) - lo) / (hi - lo)
                    value = max(0.0, min(1.0, value))
                    # Vegetation lowers friction; slope and heat raise it.
                    if name == "green":
                        value = 1.0 - value
                    total += value * weights[name]
                    covered += weights[name]
                if covered <= 0.0:
                    # No criterion covered this pixel: NoData, not a neutral score.
                    out_block.setValue(0, col, -9999.0)
                else:
                    out_block.setValue(0, col, total / covered)
            provider.writeBlock(out_block, 1, 0, row)
            feedback.setProgress(int((row / max(1, height)) * 100.0))

        provider.setEditable(False)
        return {self.OUTPUT: out_path}

    def _ahp_weights(
        self,
        criteria: List[Any],
        feedback: QgsProcessingFeedback,
    ) -> Dict[str, float]:
        """Resolve Saaty importance scores into AHP weights, rejecting inconsistency."""
        names = [name for name, _score in criteria]
        if len(names) == 1:
            return {names[0]: 1.0}

        engine = AHPEngine(names)
        for index, (name_a, score_a) in enumerate(criteria):
            for name_b, score_b in criteria[index + 1:]:
                ratio = (score_a / score_b) if score_b else 1.0
                engine.set_pairwise_comparison(name_a, name_b, ratio)

        result = engine.calculate()
        feedback.pushInfo(
            "AHP consistency ratio: {0:.4f} (index {1:.4f})".format(
                result.consistency_ratio, result.consistency_index
            )
        )
        if not result.is_consistent:
            raise QgsProcessingException(
                "The supplied weights are not internally consistent "
                "(CR={0:.3f} > 0.10). Adjust them and re-run.".format(result.consistency_ratio)
            )
        for name, weight in result.weights.items():
            feedback.pushInfo("  weight[{0}] = {1:.4f}".format(name, weight))
        return dict(result.weights)
