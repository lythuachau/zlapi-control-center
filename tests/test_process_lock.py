from __future__ import annotations

import json

import pytest

from control_center.process_lock import (
    ProcessAlreadyRunningError,
    ProcessSingleton,
)


def test_process_singleton_rejects_duplicate_and_allows_reacquire(tmp_path):
    path = tmp_path / "run" / "listener.lock"
    first = ProcessSingleton(path, role="listener")
    duplicate = ProcessSingleton(path, role="listener")

    first.acquire()
    try:
        with pytest.raises(ProcessAlreadyRunningError):
            duplicate.acquire()
    finally:
        first.release()

    metadata = json.loads(path.read_text(encoding="utf-8"))
    assert metadata["role"] == "listener"
    assert isinstance(metadata["pid"], int)

    duplicate.acquire()
    duplicate.release()


def test_worker_and_listener_use_independent_locks(tmp_path):
    worker = ProcessSingleton(tmp_path / "run" / "worker.lock", role="worker")
    listener = ProcessSingleton(
        tmp_path / "run" / "listener.lock",
        role="listener",
    )

    with worker, listener:
        pass

    assert json.loads(worker.path.read_text(encoding="utf-8"))["role"] == "worker"
    assert json.loads(listener.path.read_text(encoding="utf-8"))["role"] == "listener"
