"""The dock's Tools tab: all sixteen analysis tools, run from inside the dock.

Each tool is a QGIS algorithm class (``analysis/``) that is intentionally *not*
registered with the Processing Toolbox. This panel is its only front end:

* the form is built from the tool's own parameter model with QGIS's native
  parameter widgets (point picker on the canvas, layer and field combos, output
  destinations), so validation and behaviour match what the tool expects;
* the run goes through ``QgsProcessingAlgRunnerTask``: background thread,
  progress, cancel, and the algorithm's own post-processing (symbology, aliases);
* result layers are loaded into the project, file outputs get an Open button;
* the dock feeds its own state in: Point A/B, DEM, road network, buildings and
  the last route layer pre-fill the matching parameters.
"""
from __future__ import annotations

import contextlib
import html
import os
from typing import Any, Callable, Dict, List, Optional

from qgis.PyQt.QtCore import Qt, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from qgis.core import (
    QgsApplication,
    QgsMapLayer,
    QgsProcessingAlgRunnerTask,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingOutputLayerDefinition,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingUtils,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)
from qgis.gui import (
    QgsGui,
    QgsProcessingContextGenerator,
    QgsProcessingGui,
    QgsProcessingParameterWidgetContext,
    QgsProcessingParametersGenerator,
    QgsProcessingParametersWidget,
)

from ..analysis.catalog import CATEGORIES, create_tool

# Parameter names the dock can pre-fill, per kind of dock state.
START_POINT_PARAMS = ("START_POINT", "CENTER_POINT")
END_POINT_PARAMS = ("END_POINT",)
DEM_PARAMS = ("DEM_LAYER", "INPUT_DEM")
NETWORK_PARAMS = ("NETWORK_LAYER", "INPUT_NETWORK", "NETWORK")
BUILDING_PARAMS = ("BUILDINGS",)
ROUTE_PARAMS = ("INPUT_ROUTE",)
LST_PARAMS = ("INPUT_LST",)
GREEN_PARAMS = ("INPUT_GREEN",)
PROFILE_PARAMS = ("PROFILE", "PARAM_PROFILE", "WALK_PROFILE")


def _scoped(cls: Any, scope: str, name: str) -> Any:
    """``cls.Scope.Name`` on QGIS 3.30+ / Qt6, ``cls.Name`` on older builds."""
    holder = getattr(cls, scope, None)
    if holder is not None and hasattr(holder, name):
        return getattr(holder, name)
    return getattr(cls, name)


def _widget_type_standard() -> Any:
    return _scoped(QgsProcessingGui, "WidgetType", "Standard")


def _param_flag(name: str) -> Any:
    return _scoped(QgsProcessingParameterDefinition, "Flag", name)


class InvalidParameter(Exception):
    """A parameter widget holds a value the tool cannot accept."""

    def __init__(self, description: str, widget: Optional[QWidget] = None) -> None:
        super().__init__(description)
        self.description = description
        self.widget = widget


def make_context(feedback: Optional[QgsProcessingFeedback] = None) -> QgsProcessingContext:
    """A processing context bound to the current project."""
    project = QgsProject.instance()
    context = QgsProcessingContext()
    context.setProject(project)
    with contextlib.suppress(Exception):
        context.setTransformContext(project.transformContext())
    with contextlib.suppress(Exception):
        context.setEllipsoid(project.ellipsoid())
    if feedback is not None:
        context.setFeedback(feedback)
    return context


class _Feedback(QgsProcessingFeedback):
    """Feedback whose text reaches the GUI thread through a queued signal.

    Algorithms report from a worker thread; touching widgets there is unsafe, so
    every message goes out as a signal that Qt delivers on the main thread.
    """

    message = pyqtSignal(str, str)  # level, text

    def pushInfo(self, info: str) -> None:  # noqa: N802 - Qt override
        super().pushInfo(info)
        self.message.emit("info", str(info))

    def pushWarning(self, warning: str) -> None:  # noqa: N802
        super().pushWarning(warning)
        self.message.emit("warning", str(warning))

    def reportError(self, error: str, fatalError: bool = False) -> None:  # noqa: N802
        super().reportError(error, fatalError)
        self.message.emit("error", str(error))

    def setProgressText(self, text: str) -> None:  # noqa: N802
        super().setProgressText(text)
        if text:
            self.message.emit("info", str(text))


class _ParametersForm(QgsProcessingParametersWidget):
    """The parameter widgets of one tool, built from its own parameter model."""

    def __init__(
        self,
        algorithm: Any,
        widget_context: QgsProcessingParameterWidgetContext,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(algorithm, parent)
        self.wrappers: Dict[str, Any] = {}
        self.processing_context = make_context()
        self._widget_context = widget_context

        class _ContextGenerator(QgsProcessingContextGenerator):
            def __init__(self, context: QgsProcessingContext) -> None:
                super().__init__()
                self._context = context

            def processingContext(self) -> QgsProcessingContext:  # noqa: N802
                return self._context

        self._context_generator = _ContextGenerator(self.processing_context)
        self.initWidgets()

    def initWidgets(self) -> None:  # noqa: N802 - Qt override
        super().initWidgets()
        hidden = _param_flag("FlagHidden")
        standard = _widget_type_standard()
        registry = QgsGui.processingGuiRegistry()

        for param in self.algorithm().parameterDefinitions():
            if param.flags() & hidden or param.isDestination():
                continue
            wrapper = registry.createParameterWidgetWrapper(param, standard)
            if wrapper is None:
                continue
            wrapper.setWidgetContext(self._widget_context)
            wrapper.registerProcessingContextGenerator(self._context_generator)
            wrapper.registerProcessingParametersGenerator(self)
            self.wrappers[param.name()] = wrapper
            widget = wrapper.createWrappedWidget(self.processing_context)
            if widget is None:
                continue
            label = wrapper.createWrappedLabel()
            if label is not None:
                self.addParameterLabel(param, label)
            self.addParameterWidget(param, widget, wrapper.stretch())

        for output in self.algorithm().destinationParameterDefinitions():
            if output.flags() & hidden:
                continue
            wrapper = registry.createParameterWidgetWrapper(output, standard)
            if wrapper is None:
                continue
            wrapper.setWidgetContext(self._widget_context)
            wrapper.registerProcessingContextGenerator(self._context_generator)
            wrapper.registerProcessingParametersGenerator(self)
            self.wrappers[output.name()] = wrapper
            label = wrapper.createWrappedLabel()
            if label is not None:
                self.addOutputLabel(label)
            self.addOutputWidget(wrapper.createWrappedWidget(self.processing_context), wrapper.stretch())

        for wrapper in list(self.wrappers.values()):
            wrapper.postInitialize(list(self.wrappers.values()))

    def createProcessingParameters(self, flags: Any = None) -> Dict[str, Any]:  # noqa: N802
        skip_validation = False
        with contextlib.suppress(Exception):
            if flags is not None:
                skip_validation = bool(flags & _scoped(QgsProcessingParametersGenerator, "Flag", "SkipValidation"))
        parameters: Dict[str, Any] = {}
        hidden = _param_flag("FlagHidden")
        for param in self.algorithm().parameterDefinitions():
            if param.flags() & hidden:
                continue
            wrapper = self.wrappers.get(param.name())
            if wrapper is None:
                continue
            value = wrapper.parameterValue()
            if param.isDestination():
                if value:
                    parameters[param.name()] = value
                continue
            parameters[param.name()] = value
            if not skip_validation and not param.checkValueIsAcceptable(value):
                raise InvalidParameter(param.description(), wrapper.wrappedWidget())
        return self.algorithm().preprocessParameters(parameters)

    def set_value(self, name: str, value: Any) -> bool:
        """Set one parameter widget; False when the tool has no such parameter."""
        wrapper = self.wrappers.get(name)
        if wrapper is None:
            return False
        try:
            wrapper.setParameterValue(value, self.processing_context)
        except Exception:  # noqa: BLE001 - a default that does not fit is skipped
            return False
        return True

    def set_parameters(self, parameters: Dict[str, Any]) -> None:
        for name, value in parameters.items():
            self.set_value(name, value)


def _help_html(algorithm: Any) -> str:
    """The tool's help text as simple HTML for a QLabel."""
    text = html.escape(str(algorithm.shortHelpString() or "").strip())
    return text.replace("\n\n", "<br><br>").replace("\n", " ")


class ToolPanel(QWidget):
    """Pick a tool, fill its form, run it in the background, open its results."""

    tool_finished = pyqtSignal(str, bool)  # tool name, success

    def __init__(
        self,
        iface: Any = None,
        defaults: Optional[Callable[[], Dict[str, Any]]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.iface = iface
        self._defaults = defaults
        self._algorithm: Any = None
        self._form: Optional[_ParametersForm] = None
        self._task: Optional[QgsProcessingAlgRunnerTask] = None
        self._context: Optional[QgsProcessingContext] = None
        self._feedback: Optional[_Feedback] = None
        self._running_algorithm: Any = None
        self._closed = False
        self.last_results: Dict[str, Any] = {}
        self.last_ok: Optional[bool] = None
        self._open_buttons: List[QPushButton] = []
        self._build_ui()
        self._fill_tools()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        row = QHBoxLayout()
        self.cmb_category = QComboBox()
        self.cmb_category.setToolTip("Tool group")
        self.cmb_tool = QComboBox()
        self.cmb_tool.setToolTip("Analysis tool")
        row.addWidget(self.cmb_category, 1)
        row.addWidget(self.cmb_tool, 2)
        layout.addLayout(row)

        self.btn_about = QPushButton("ℹ️ About this tool")
        self.btn_about.setCheckable(True)
        self.btn_about.toggled.connect(self._toggle_about)
        layout.addWidget(self.btn_about)
        self.lbl_about = QLabel()
        self.lbl_about.setWordWrap(True)
        self.lbl_about.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_about.setVisible(False)
        layout.addWidget(self.lbl_about)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll, 1)

        row = QHBoxLayout()
        self.btn_run = QPushButton("▶ Run tool")
        self.btn_run.setObjectName("toolRunButton")
        self.btn_run.clicked.connect(self.run_current_tool)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_run)
        self.btn_fill = QPushButton("🔄 Use dock inputs")
        self.btn_fill.setToolTip("Fill points, DEM, road network, buildings and route layer from the dock.")
        self.btn_fill.clicked.connect(self.apply_dock_defaults)
        row.addWidget(self.btn_run, 2)
        row.addWidget(self.btn_cancel, 1)
        layout.addLayout(row)
        layout.addWidget(self.btn_fill)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        layout.addWidget(self.lbl_status)

        self.open_row = QHBoxLayout()
        layout.addLayout(self.open_row)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setMinimumHeight(90)
        self.log.setMaximumHeight(150)
        layout.addWidget(self.log)

        self.cmb_category.currentIndexChanged.connect(self._on_category_changed)
        self.cmb_tool.currentIndexChanged.connect(self._on_tool_changed)

    def _toggle_about(self, shown: bool) -> None:
        self.lbl_about.setVisible(shown)

    def _fill_tools(self) -> None:
        self._tool_names: Dict[str, List[Any]] = {}
        for key, title, classes in CATEGORIES:
            tools = [cls().create() for cls in classes]
            self._tool_names[key] = tools
            self.cmb_category.addItem(title, key)
        self._on_category_changed(0)

    def _on_category_changed(self, _index: int) -> None:
        key = self.cmb_category.currentData()
        self.cmb_tool.blockSignals(True)
        self.cmb_tool.clear()
        for tool in self._tool_names.get(key, []):
            self.cmb_tool.addItem(tool.displayName(), tool.name())
        self.cmb_tool.blockSignals(False)
        self._on_tool_changed(0)

    def _on_tool_changed(self, _index: int) -> None:
        name = self.cmb_tool.currentData()
        if name:
            self._show_tool(str(name))

    def _show_tool(self, name: str) -> None:
        self._algorithm = create_tool(name)
        self.lbl_about.setText(_help_html(self._algorithm))
        context = QgsProcessingParameterWidgetContext()
        context.setProject(QgsProject.instance())
        iface = self.iface
        if iface is not None:
            with contextlib.suppress(Exception):
                context.setMapCanvas(iface.mapCanvas())
            with contextlib.suppress(Exception):
                context.setMessageBar(iface.messageBar())
            with contextlib.suppress(Exception):
                context.setBrowserModel(iface.browserModel())
            with contextlib.suppress(Exception):
                context.setActiveLayer(iface.activeLayer())
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self._form = _ParametersForm(self._algorithm, context)
        self.scroll.setWidget(self._form)
        self._clear_open_buttons()
        self.lbl_status.setText("")
        self.progress.setValue(0)
        self.apply_dock_defaults()

    # ------------------------------------------------------------ selection
    def tool_names(self) -> List[str]:
        """Names of every tool across all groups."""
        return [t.name() for tools in self._tool_names.values() for t in tools]

    def current_tool_name(self) -> str:
        return str(self.cmb_tool.currentData() or "")

    def select_tool(self, name: str) -> bool:
        """Show the form for ``name``; False when no such tool exists."""
        for index in range(self.cmb_category.count()):
            key = self.cmb_category.itemData(index)
            if any(t.name() == name for t in self._tool_names.get(key, [])):
                self.cmb_category.setCurrentIndex(index)
                tool_index = self.cmb_tool.findData(name)
                if tool_index >= 0:
                    self.cmb_tool.setCurrentIndex(tool_index)
                return self.current_tool_name() == name
        return False

    @property
    def form(self) -> Optional[_ParametersForm]:
        return self._form

    @property
    def is_running(self) -> bool:
        return self._task is not None

    # ------------------------------------------------------ dock integration
    def apply_dock_defaults(self) -> None:
        """Pre-fill parameters from the dock's current state."""
        form = self._form
        if form is None or self._defaults is None:
            return
        try:
            state = self._defaults() or {}
        except Exception:  # noqa: BLE001 - never let a defaults hook break the form
            state = {}

        def point(value: Any) -> Optional[str]:
            if not value:
                return None
            return f"{float(value[0])},{float(value[1])} [EPSG:4326]"

        def layer_id(layer: Any, kind: str) -> Optional[str]:
            if layer is None or not isinstance(layer, QgsMapLayer) or not layer.isValid():
                return None
            if kind == "raster" and not isinstance(layer, QgsRasterLayer):
                return None
            if kind == "vector" and not isinstance(layer, QgsVectorLayer):
                return None
            return layer.id()

        for names, value in (
            (START_POINT_PARAMS, point(state.get("start"))),
            (END_POINT_PARAMS, point(state.get("end"))),
        ):
            if value is not None:
                for name in names:
                    form.set_value(name, value)

        for names, layer, kind in (
            (DEM_PARAMS, state.get("dem"), "raster"),
            (LST_PARAMS, state.get("lst"), "raster"),
            (GREEN_PARAMS, state.get("green"), "raster"),
            (NETWORK_PARAMS, state.get("network"), "vector"),
            (BUILDING_PARAMS, state.get("buildings"), "vector"),
            (ROUTE_PARAMS, state.get("route"), "vector"),
        ):
            lid = layer_id(layer, kind)
            if lid is not None:
                for name in names:
                    form.set_value(name, lid)

        profile_name = state.get("profile_name")
        if profile_name:
            for param in self._algorithm.parameterDefinitions():
                if param.name() in PROFILE_PARAMS and isinstance(param, QgsProcessingParameterEnum):
                    options = list(param.options())
                    if profile_name in options:
                        form.set_value(param.name(), options.index(profile_name))

    # ------------------------------------------------------------------ run
    def _status(self, text: str, error: bool = False) -> None:
        self.lbl_status.setStyleSheet("color: #b91c1c;" if error else "")
        self.lbl_status.setText(text)

    def _log(self, level: str, text: str) -> None:
        prefix = {"warning": "⚠ ", "error": "✖ "}.get(level, "")
        self.log.appendPlainText(f"{prefix}{text}")

    def _set_running(self, running: bool) -> None:
        self.btn_run.setEnabled(not running)
        self.btn_cancel.setEnabled(running)
        self.btn_fill.setEnabled(not running)
        self.cmb_category.setEnabled(not running)
        self.cmb_tool.setEnabled(not running)
        if self._form is not None:
            self._form.setEnabled(not running)

    def _clear_open_buttons(self) -> None:
        for button in self._open_buttons:
            self.open_row.removeWidget(button)
            button.deleteLater()
        self._open_buttons = []

    def run_current_tool(self) -> bool:
        """Validate the form and start the tool in the background.

        Returns True when a run was started.
        """
        if self._task is not None or self._form is None or self._algorithm is None:
            return False
        self._clear_open_buttons()
        algorithm = self._algorithm
        try:
            parameters = self._form.createProcessingParameters()
        except InvalidParameter as exc:
            self._status(f"Check “{exc.description}”: the value is missing or not valid.", error=True)
            if exc.widget is not None:
                with contextlib.suppress(Exception):
                    exc.widget.setFocus()
            return False

        feedback = _Feedback()
        context = make_context(feedback)
        ok, message = algorithm.checkParameterValues(parameters, context)
        if not ok:
            self._status(message or "The parameters are not valid.", error=True)
            return False

        for value in parameters.values():
            if isinstance(value, QgsProcessingOutputLayerDefinition):
                value.destinationProject = QgsProject.instance()

        feedback.message.connect(self._log)
        feedback.progressChanged.connect(self._on_progress)
        self._feedback = feedback
        self._context = context
        self._running_algorithm = algorithm
        self.last_results = {}
        self.last_ok = None
        self.progress.setValue(0)
        self.log.appendPlainText(f"— {algorithm.displayName()} —")
        self._status("Running…")
        self._set_running(True)

        task = QgsProcessingAlgRunnerTask(algorithm, parameters, context, feedback)
        self._task = task
        if task.isCanceled():  # prepare() refused the inputs
            self._on_complete(False, {})
            return False
        task.executed.connect(self._on_complete)
        QgsApplication.taskManager().addTask(task)
        return True

    def cancel_run(self) -> None:
        if self._feedback is not None:
            self._feedback.cancel()
        if self._task is not None:
            with contextlib.suppress(Exception):
                self._task.cancel()
        self._status("Cancelling…")

    def _on_progress(self, value: float) -> None:
        self.progress.setValue(int(max(0.0, min(100.0, value))))

    def _on_complete(self, ok: bool, results: Dict[str, Any]) -> None:
        algorithm = self._running_algorithm
        context = self._context
        feedback = self._feedback
        self._task = None
        self._set_running(False)
        if self._closed or algorithm is None:
            return
        name = algorithm.name()
        loaded = True
        if ok and context is not None:
            loaded = self._load_results(algorithm, context, feedback, results)
        self.last_ok = bool(ok and loaded)
        self.last_results = dict(results or {})
        if ok:
            self.progress.setValue(100)
            self._status("Finished. Result layers were added to the project." if loaded else "Finished with problems; see the log.", error=not loaded)
            self._add_open_buttons(algorithm, results or {})
        elif feedback is not None and feedback.isCanceled():
            self._status("Cancelled.")
        else:
            self._status("The tool failed; see the log for the reason.", error=True)
        self._running_algorithm = None
        if feedback is not None:
            with contextlib.suppress(Exception):
                feedback.message.disconnect(self._log)
        self.tool_finished.emit(name, self.last_ok)

    # -------------------------------------------------------------- results
    def _load_results(
        self,
        algorithm: Any,
        context: QgsProcessingContext,
        feedback: Optional[QgsProcessingFeedback],
        results: Dict[str, Any],
    ) -> bool:
        """Add the run's output layers to the project (QGIS's own loader first)."""
        try:
            from processing.gui.Postprocessing import handleAlgorithmResults

            return bool(handleAlgorithmResults(algorithm, context, feedback, results))
        except ImportError:
            pass
        ok = True
        project = QgsProject.instance()
        for dest_id, details in context.layersToLoadOnCompletion().items():
            layer = QgsProcessingUtils.mapLayerFromString(dest_id, context, typeHint=details.layerTypeHint)
            if layer is None:
                ok = False
                continue
            details.setOutputLayerName(layer)
            owned = context.temporaryLayerStore().takeMapLayer(layer)
            project.addMapLayer(owned if owned is not None else layer)
        return ok

    def _add_open_buttons(self, algorithm: Any, results: Dict[str, Any]) -> None:
        """One Open button per file the tool wrote (HTML report, DXF, ...)."""
        for output in algorithm.outputDefinitions():
            path = results.get(output.name())
            if not isinstance(path, str) or not path or not os.path.isfile(path):
                continue
            button = QPushButton(f"Open {output.description()}")
            button.setToolTip(path)
            button.clicked.connect(lambda _checked=False, p=path: QDesktopServices.openUrl(QUrl.fromLocalFile(p)))
            self.open_row.addWidget(button)
            self._open_buttons.append(button)
            self._log("info", f"{output.description()}: {path}")

    # ------------------------------------------------------------- teardown
    def shutdown(self) -> None:
        """Cancel a running tool; called when the dock is torn down."""
        self._closed = True
        if self._feedback is not None:
            with contextlib.suppress(Exception):
                self._feedback.cancel()
        if self._task is not None:
            with contextlib.suppress(Exception):
                self._task.cancel()
            self._task = None
