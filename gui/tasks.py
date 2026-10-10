"""Background work for the dock: QgsTask wrappers and thread-safe layer snapshots.

Network downloads (Overpass, Open-Elevation), graph building and routing used
to run on the GUI thread and froze QGIS for seconds to minutes. They now run
in QGIS's task manager. Rules that keep this safe:

* The work function receives only plain data and RasterLayerSnapshot objects.
  Vector layers are read on the main thread before the task starts, and map
  layers are created or added to the project only in the completion callback,
  which QGIS runs on the main thread.
* A raster layer's data provider is not safe to use from another thread while
  the canvas renders it, so snapshots carry a clone of the provider.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from qgis.core import QgsApplication, QgsMessageLog, QgsTask


class TaskCancelled(Exception):
    """Raised inside a work function when the user cancelled the task."""


class TaskContext:
    """What a work function may touch: progress, cancellation and a status line."""

    def __init__(self, task: QgsTask) -> None:
        self._task = task

    def progress(self, percent: float) -> None:
        self._task.setProgress(max(0.0, min(100.0, float(percent))))

    def is_canceled(self) -> bool:
        return self._task.isCanceled()

    def check(self) -> None:
        """Stop the work function here if the user cancelled."""
        if self._task.isCanceled():
            raise TaskCancelled()


class FunctionTask(QgsTask):
    """Run ``work(context)`` in the background; deliver the outcome on the main thread.

    ``on_done(result)`` runs after success, ``on_error(message)`` after an
    exception, ``on_cancel()`` after cancellation. All three run on the main
    thread (QgsTask.finished), so they may update widgets and the project.
    """

    def __init__(
        self,
        description: str,
        work: Callable[[TaskContext], Any],
        on_done: Callable[[Any], None],
        on_error: Optional[Callable[[str], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(description, getattr(QgsTask, "Flag", QgsTask).CanCancel)
        self._work = work
        self._on_done = on_done
        self._on_error = on_error
        self._on_cancel = on_cancel
        self.result: Any = None
        self.error: Optional[str] = None

    def run(self) -> bool:  # worker thread
        try:
            self.result = self._work(TaskContext(self))
            return not self.isCanceled()
        except TaskCancelled:
            return False
        except Exception as exc:  # noqa: BLE001 - handed to the UI in finished()
            self.error = str(exc) or type(exc).__name__
            QgsMessageLog.logMessage(f"{self.description()} failed: {self.error}", "02Route 3D")
            return False

    def finished(self, ok: bool) -> None:  # main thread
        owner = getattr(self, "_owner", None)
        if owner is not None and getattr(owner, "_tasks_closed", False):
            return  # the dock was closed while this ran
        if ok:
            try:
                self._on_done(self.result)
            except Exception as exc:  # noqa: BLE001 - shown instead of lost in the log
                QgsMessageLog.logMessage(f"{self.description()} result handling failed: {exc}", "02Route 3D")
                if self._on_error is not None:
                    self._on_error(f"Showing the result failed: {exc}")
        elif self.error is not None:
            if self._on_error is not None:
                self._on_error(self.error)
        elif self._on_cancel is not None:
            self._on_cancel()


def start_task(owner: Any, task: FunctionTask) -> FunctionTask:
    """Hand a task to QGIS's task manager and keep a reference on ``owner``.

    QgsTask objects are deleted when Python drops the last reference, which
    would kill a running task; the owner keeps it until it finishes.
    """
    task._owner = owner
    owner._tasks_closed = False
    running = getattr(owner, "_running_tasks", None)
    if running is None:
        running = []
        setattr(owner, "_running_tasks", running)
    running.append(task)

    def forget(*_args: Any) -> None:
        if task in running:
            running.remove(task)

    task.taskCompleted.connect(forget)
    task.taskTerminated.connect(forget)
    QgsApplication.taskManager().addTask(task)
    return task


def cancel_tasks(owner: Any) -> None:
    """Cancel every task started for ``owner`` and drop their callbacks."""
    owner._tasks_closed = True
    for task in list(getattr(owner, "_running_tasks", []) or []):
        try:
            task.cancel()
        except RuntimeError:  # already deleted by the task manager
            pass


class RasterLayerSnapshot:
    """The parts of a raster layer the samplers use, safe to read off the main thread.

    Exposes crs(), dataProvider() (a private clone), extent(), id() and name(),
    which is everything EnvironmentalSurfaceSampler calls.
    """

    def __init__(self, layer: Any) -> None:
        self._id = layer.id()
        self._name = layer.name()
        self._crs = layer.crs()
        self._extent = layer.extent()
        self._provider = layer.dataProvider().clone()

    def crs(self) -> Any:
        return self._crs

    def dataProvider(self) -> Any:  # noqa: N802 - mirrors QgsRasterLayer
        return self._provider

    def extent(self) -> Any:
        return self._extent

    def id(self) -> str:
        return self._id

    def name(self) -> str:
        return self._name


def snapshot(layer: Any) -> Optional[RasterLayerSnapshot]:
    """Snapshot a valid raster layer, or None."""
    if layer is None:
        return None
    try:
        if not layer.isValid():
            return None
        return RasterLayerSnapshot(layer)
    except Exception:  # noqa: BLE001 - a layer that cannot be cloned is skipped
        return None
