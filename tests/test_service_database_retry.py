from __future__ import annotations

import threading
from contextlib import contextmanager

from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as SATimeoutError

import control_center.worker as worker_module
from control_center.listener import ListenerSupervisor
from control_center.worker import Worker


def database_unavailable() -> OperationalError:
    return OperationalError("SELECT 1", {}, OSError("dns unavailable"))


def pool_exhausted() -> SATimeoutError:
    return SATimeoutError(
        "QueuePool limit of size 1 overflow 0 reached, connection timed out, timeout 15.00",
        [],
        None,
    )


class RecordingStopEvent:
    def __init__(self):
        self.stopped = False
        self.waits: list[float] = []

    def is_set(self) -> bool:
        return self.stopped

    def set(self) -> None:
        self.stopped = True

    def wait(self, timeout: float) -> bool:
        self.waits.append(timeout)
        if len(self.waits) >= 2:
            self.stopped = True
        return self.stopped


class AvailableDatabase:
    def __init__(self):
        self.dispose_calls = 0

    @contextmanager
    def session_factory(self):
        yield object()

    def dispose(self) -> None:
        self.dispose_calls += 1


def test_worker_retries_transient_database_failure(monkeypatch):
    database = AvailableDatabase()
    stop_event = RecordingStopEvent()
    worker = Worker.__new__(Worker)
    worker.database = database
    worker.stop_event = stop_event
    worker.idle_delay = 0.5
    attempts = 0

    monkeypatch.setattr(worker_module, "recover_stale_jobs", lambda _session: 0)

    def run_once() -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise database_unavailable()
        return False

    monkeypatch.setattr(worker, "run_once", run_once)

    worker.run()

    assert attempts == 2
    assert database.dispose_calls == 1
    assert stop_event.waits == [5.0, 0.5]


def test_listener_retries_transient_database_failure(monkeypatch):
    database = AvailableDatabase()
    stop_event = RecordingStopEvent()
    supervisor = ListenerSupervisor.__new__(ListenerSupervisor)
    supervisor.database = database
    supervisor.stop_event = stop_event
    supervisor.handles = {}
    supervisor.lock = threading.Lock()
    attempts = 0

    def reconcile() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise database_unavailable()

    monkeypatch.setattr(supervisor, "reconcile", reconcile)

    supervisor.run()

    assert attempts == 2
    assert database.dispose_calls == 2
    assert stop_event.waits == [5.0, 10]


def test_listener_retries_pool_timeout_without_dispose(monkeypatch):
    database = AvailableDatabase()
    stop_event = RecordingStopEvent()
    supervisor = ListenerSupervisor.__new__(ListenerSupervisor)
    supervisor.database = database
    supervisor.stop_event = stop_event
    supervisor.handles = {}
    supervisor.lock = threading.Lock()
    attempts = 0

    def reconcile() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise pool_exhausted()

    monkeypatch.setattr(supervisor, "reconcile", reconcile)

    supervisor.run()

    assert attempts == 2
    assert database.dispose_calls == 1  # only final dispose in finally
    assert stop_event.waits == [5.0, 10]


def test_worker_retries_pool_timeout_without_dispose(monkeypatch):
    database = AvailableDatabase()
    stop_event = RecordingStopEvent()
    worker = Worker.__new__(Worker)
    worker.database = database
    worker.stop_event = stop_event
    worker.idle_delay = 0.5
    attempts = 0

    monkeypatch.setattr(worker_module, "recover_stale_jobs", lambda _session: 0)

    def run_once() -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise pool_exhausted()
        return False

    monkeypatch.setattr(worker, "run_once", run_once)

    worker.run()

    assert attempts == 2
    assert database.dispose_calls == 0
    assert stop_event.waits == [5.0, 0.5]
