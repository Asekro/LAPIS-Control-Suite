"""Reserve instruments on the UI thread and execute blocking work off-thread."""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import logging
from threading import Event
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal, Slot

log = logging.getLogger(__name__)


@dataclass
class Job:
    name: str
    resources: tuple[str, ...]
    cancel: Event
    success: Callable[[Any], None]
    failure: Callable[[Exception], None]


class Jobs(QObject):
    delivered = Signal(int, object, object)
    changed = Signal()
    finished = Signal()
    progress = Signal(str, object)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix='lapis')
        self.active: dict[int, Job] = {}
        self.waiting: dict[int, tuple[set[int], Callable[[Exception | None], None]]] = {}
        self.sequence = 0
        self.delivered.connect(self.finish, Qt.ConnectionType.QueuedConnection)

    def busy(self, *resources: str, include_reads: bool = True) -> bool:
        reserved = set(resources)
        return any(not reserved.isdisjoint(job.resources) for job in self.active.values()
                   if include_reads or job.name not in ('read', 'vacuum-read', 'vacuum-pumps'))

    def start(self, name: str, resources: tuple[str, ...], work: Callable[[Event], Any],
              success: Callable[[Any], None], failure: Callable[[Exception], None], *,
              after_reads: tuple[str, ...] = ()) -> bool:
        """Reserve resources now and wait for ongoing status reads on the named devices."""
        requested = set(resources)
        read_resources = set(after_reads)
        blockers = set()
        for read_token, job in self.active.items():
            if job.name == 'read' and not read_resources.isdisjoint(job.resources):
                blockers.add(read_token)
            elif not requested.isdisjoint(job.resources):
                return False
        self.sequence += 1
        token = self.sequence
        cancel = Event()
        self.active[token] = Job(name, resources, cancel, success, failure)

        def run() -> None:
            try:
                value = work(cancel)
            except Exception as exc:
                if isinstance(exc, InterruptedError) and exc.__context__ is None:
                    log.info("Job %d %s on %s was cancelled", token, name, resources)
                else:
                    log.exception("Job %d %s on %s failed", token, name, resources)
                self.delivered.emit(token, None, exc)
            else:
                self.delivered.emit(token, value, None)

        def dispatch(read_error: Exception | None) -> None:
            if cancel.is_set():
                self.finish(token, None, InterruptedError('Queued operation cancelled.'))
            elif read_error is not None and name not in ('disable', 'laser-off'):
                self.finish(token, None, read_error)
            else:
                self.pool.submit(run)

        if blockers:
            self.waiting[token] = (blockers, dispatch)
        else:
            self.pool.submit(run)
        self.changed.emit()
        return True

    @Slot(int, object, object)
    def finish(self, token: int, result: object, error: Exception | None) -> None:
        job = self.active.pop(token)
        try:
            if error is None:
                job.success(result)
            else:
                job.failure(error)
        finally:
            for queued, (reads, dispatch) in list(self.waiting.items()):
                if token in reads:
                    reads.remove(token)
                    if not reads or error is not None:
                        del self.waiting[queued]
                        dispatch(error)
            self.finished.emit()
            self.changed.emit()

    def cancel(self, name: str) -> None:
        for job in self.active.values():
            if job.name == name:
                job.cancel.set()

    def running(self, name: str) -> bool:
        return any(job.name == name for job in self.active.values())

    def waiting_action(self, resource: str) -> int | None:
        """Return the queued action reserving this resource, if any."""
        return next((token for token in self.waiting
                     if resource in self.active[token].resources), None)

    def discard_waiting_action(self, resource: str) -> None:
        token = self.waiting_action(resource)
        if token is not None:
            del self.waiting[token]
            self.finish(token, None, InterruptedError('Queued operation cancelled.'))


def error_text(error: BaseException) -> str:
    messages = []
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        message = str(error) or type(error).__name__
        if message not in messages:
            messages.append(message)
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    return ' · '.join(messages)
