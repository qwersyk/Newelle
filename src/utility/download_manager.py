"""Process-wide tracking for downloads and managed installations.

The registry deliberately has no GTK dependency. Worker threads update mutable
tasks through :class:`DownloadTaskHandle`; UI code consumes immutable snapshots.
"""

from __future__ import annotations

import gettext
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum

_ = gettext.gettext


class DownloadKind(str, Enum):
    DEPENDENCY = "dependency"
    MODEL = "model"
    RUNTIME = "runtime"
    EXTENSION = "extension"
    SKILL = "skill"
    MCP = "mcp"


class DownloadStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_DOWNLOAD_STATUSES = frozenset(
    (DownloadStatus.QUEUED, DownloadStatus.RUNNING)
)


class DownloadCancelled(Exception):
    """Raised by cooperative workers after cancellation is requested."""


@dataclass(frozen=True)
class DownloadTaskSnapshot:
    task_id: str
    title: str
    kind: DownloadKind
    status: DownloadStatus
    phase: str
    fraction: float | None
    transferred_bytes: int | None
    total_bytes: int | None
    bytes_per_second: float | None
    created_at: float
    started_at: float | None
    finished_at: float | None
    error: str | None
    source_id: str | None
    cancellable: bool
    cancel_requested: bool


@dataclass
class _DownloadTask:
    task_id: str
    title: str
    kind: DownloadKind
    status: DownloadStatus
    phase: str
    source_id: str | None
    cancellable: bool
    fraction: float | None = None
    transferred_bytes: int | None = None
    total_bytes: int | None = None
    bytes_per_second: float | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    last_speed_bytes: int | None = None
    last_speed_at: float = field(default_factory=time.monotonic)

    def snapshot(self) -> DownloadTaskSnapshot:
        return DownloadTaskSnapshot(
            task_id=self.task_id,
            title=self.title,
            kind=self.kind,
            status=self.status,
            phase=self.phase,
            fraction=self.fraction,
            transferred_bytes=self.transferred_bytes,
            total_bytes=self.total_bytes,
            bytes_per_second=self.bytes_per_second,
            created_at=self.created_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            error=self.error,
            source_id=self.source_id,
            cancellable=self.cancellable,
            cancel_requested=self.cancel_event.is_set(),
        )


_current_task: ContextVar[object | None] = ContextVar(
    "newelle_download_task", default=None
)


class DownloadTaskHandle:
    """Thread-safe write interface for one registered task."""

    def __init__(self, manager: DownloadManager, task_id: str):
        self._manager = manager
        self.task_id = task_id

    @property
    def snapshot(self) -> DownloadTaskSnapshot:
        return self._manager.get(self.task_id)

    @property
    def is_active(self) -> bool:
        try:
            return self.snapshot.status in ACTIVE_DOWNLOAD_STATUSES
        except KeyError:
            return False

    @property
    def cancel_requested(self) -> bool:
        return self._manager._cancel_requested(self.task_id)

    def check_cancelled(self) -> None:
        if self.cancel_requested:
            raise DownloadCancelled()

    def update(
        self,
        *,
        title: str | None = None,
        phase: str | None = None,
        fraction: float | None = None,
        transferred_bytes: int | None = None,
        total_bytes: int | None = None,
        bytes_per_second: float | None = None,
        reset_progress: bool = False,
        cancellable: bool | None = None,
    ) -> None:
        self._manager.update(
            self.task_id,
            title=title,
            phase=phase,
            fraction=fraction,
            transferred_bytes=transferred_bytes,
            total_bytes=total_bytes,
            bytes_per_second=bytes_per_second,
            reset_progress=reset_progress,
            cancellable=cancellable,
        )

    def queue(self, phase: str | None = None) -> None:
        self._manager.set_status(self.task_id, DownloadStatus.QUEUED, phase=phase)

    def start(self, phase: str | None = None) -> None:
        self._manager.set_status(self.task_id, DownloadStatus.RUNNING, phase=phase)

    def complete(self, phase: str | None = None) -> None:
        self._manager.finish(self.task_id, DownloadStatus.COMPLETED, phase=phase)

    def fail(self, error: str, phase: str | None = None) -> None:
        self._manager.finish(
            self.task_id, DownloadStatus.FAILED, phase=phase, error=error
        )

    def cancelled(self, phase: str | None = None) -> None:
        self._manager.finish(self.task_id, DownloadStatus.CANCELLED, phase=phase)


class DownloadManager:
    """Registry of active and recently finished managed transfers."""

    def __init__(self, max_finished: int = 100):
        self.max_finished = max(0, int(max_finished))
        self._tasks: dict[str, _DownloadTask] = {}
        self._lock = threading.RLock()

    def create_task(
        self,
        title: str,
        *,
        kind: DownloadKind = DownloadKind.DEPENDENCY,
        source_id: str | None = None,
        phase: str = "",
        cancellable: bool = False,
        queued: bool = False,
    ) -> DownloadTaskHandle:
        status = DownloadStatus.QUEUED if queued else DownloadStatus.RUNNING
        now = time.time()
        task = _DownloadTask(
            task_id=uuid.uuid4().hex,
            title=title,
            kind=DownloadKind(kind),
            status=status,
            phase=phase,
            source_id=source_id,
            cancellable=bool(cancellable),
            started_at=None if queued else now,
        )
        with self._lock:
            self._tasks[task.task_id] = task
        return DownloadTaskHandle(self, task.task_id)

    @contextmanager
    def operation(self, title: str, **kwargs):
        """Create, bind, and automatically finalize a task."""
        handle = self.create_task(title, **kwargs)
        with self.bind(handle):
            try:
                yield handle
            except DownloadCancelled:
                handle.cancelled(_("Cancelled"))
                raise
            except Exception as error:
                handle.fail(str(error), _("Failed"))
                raise
            else:
                if handle.is_active:
                    handle.complete(_("Completed"))

    @contextmanager
    def bind(self, handle: DownloadTaskHandle):
        token = _current_task.set(handle)
        try:
            yield handle
        finally:
            _current_task.reset(token)

    def current_task(self) -> DownloadTaskHandle | None:
        handle = _current_task.get()
        if not isinstance(handle, DownloadTaskHandle):
            return None
        return handle if handle.is_active else None

    def get(self, task_id: str) -> DownloadTaskSnapshot:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            return task.snapshot()

    def list(self, *, active: bool | None = None) -> list[DownloadTaskSnapshot]:
        with self._lock:
            snapshots = [task.snapshot() for task in self._tasks.values()]
        if active is True:
            snapshots = [s for s in snapshots if s.status in ACTIVE_DOWNLOAD_STATUSES]
            return sorted(snapshots, key=lambda s: s.created_at)
        if active is False:
            snapshots = [s for s in snapshots if s.status not in ACTIVE_DOWNLOAD_STATUSES]
            return sorted(
                snapshots, key=lambda s: s.finished_at or s.created_at, reverse=True
            )
        return sorted(snapshots, key=lambda s: s.created_at)

    def find_active(self, source_id: str | None) -> DownloadTaskSnapshot | None:
        if source_id is None:
            return None
        return next(
            (task for task in self.list(active=True) if task.source_id == source_id),
            None,
        )

    def has_active(self, source_id: str | None = None) -> bool:
        if source_id is None:
            return bool(self.list(active=True))
        return self.find_active(source_id) is not None

    def set_status(
        self,
        task_id: str,
        status: DownloadStatus,
        *,
        phase: str | None = None,
    ) -> None:
        if status not in ACTIVE_DOWNLOAD_STATUSES:
            raise ValueError("set_status only accepts active statuses")
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status not in ACTIVE_DOWNLOAD_STATUSES:
                return
            task.status = status
            if phase is not None:
                task.phase = phase
            if status == DownloadStatus.RUNNING and task.started_at is None:
                task.started_at = time.time()

    def update(
        self,
        task_id: str,
        *,
        title: str | None = None,
        phase: str | None = None,
        fraction: float | None = None,
        transferred_bytes: int | None = None,
        total_bytes: int | None = None,
        bytes_per_second: float | None = None,
        reset_progress: bool = False,
        cancellable: bool | None = None,
    ) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status not in ACTIVE_DOWNLOAD_STATUSES:
                return
            if title is not None:
                task.title = title
            if phase is not None:
                task.phase = phase
            if reset_progress:
                task.fraction = None
                task.transferred_bytes = None
                task.total_bytes = None
                task.bytes_per_second = None
                task.last_speed_bytes = None
                task.last_speed_at = time.monotonic()
            if fraction is not None:
                task.fraction = max(0.0, min(float(fraction), 1.0))
            if total_bytes is not None:
                task.total_bytes = max(0, int(total_bytes))
            if transferred_bytes is not None:
                transferred = max(0, int(transferred_bytes))
                now = time.monotonic()
                if task.last_speed_bytes is None or transferred < task.last_speed_bytes:
                    task.last_speed_bytes = transferred
                    task.last_speed_at = now
                elif bytes_per_second is None:
                    elapsed = now - task.last_speed_at
                    if elapsed >= 0.1:
                        measured = (transferred - task.last_speed_bytes) / elapsed
                        task.bytes_per_second = (
                            measured
                            if task.bytes_per_second is None
                            else task.bytes_per_second * 0.65 + measured * 0.35
                        )
                        task.last_speed_bytes = transferred
                        task.last_speed_at = now
                task.transferred_bytes = transferred
            if bytes_per_second is not None:
                task.bytes_per_second = max(0.0, float(bytes_per_second))
            if cancellable is not None:
                task.cancellable = bool(cancellable)

    def finish(
        self,
        task_id: str,
        status: DownloadStatus,
        *,
        phase: str | None = None,
        error: str | None = None,
    ) -> None:
        if status in ACTIVE_DOWNLOAD_STATUSES:
            raise ValueError("finish requires a terminal status")
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status not in ACTIVE_DOWNLOAD_STATUSES:
                return
            task.status = status
            task.finished_at = time.time()
            task.cancellable = False
            if phase is not None:
                task.phase = phase
            task.error = error
            if status == DownloadStatus.COMPLETED:
                task.fraction = 1.0
            self._prune_finished_locked()

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if (
                task is None
                or task.status not in ACTIVE_DOWNLOAD_STATUSES
                or not task.cancellable
            ):
                return False
            task.cancel_event.set()
            task.phase = _("Cancelling…")
            task.cancellable = False
            return True

    def _cancel_requested(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            return bool(task and task.cancel_event.is_set())

    def clear_finished(self) -> None:
        with self._lock:
            for task_id, task in list(self._tasks.items()):
                if task.status not in ACTIVE_DOWNLOAD_STATUSES:
                    self._tasks.pop(task_id, None)

    def _prune_finished_locked(self) -> None:
        finished = sorted(
            (
                task
                for task in self._tasks.values()
                if task.status not in ACTIVE_DOWNLOAD_STATUSES
            ),
            key=lambda task: task.finished_at or task.created_at,
            reverse=True,
        )
        for task in finished[self.max_finished :]:
            self._tasks.pop(task.task_id, None)


_default_manager: DownloadManager | None = None
_default_manager_lock = threading.Lock()


def get_download_manager() -> DownloadManager:
    """Return the process-wide download and installation registry."""
    global _default_manager
    if _default_manager is None:
        with _default_manager_lock:
            if _default_manager is None:
                _default_manager = DownloadManager()
    return _default_manager


def current_download_task() -> DownloadTaskHandle | None:
    return get_download_manager().current_task()
