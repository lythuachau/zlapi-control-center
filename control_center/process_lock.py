from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO


class ProcessAlreadyRunningError(RuntimeError):
    """Raised when another process owns the requested service lock."""


class ProcessSingleton:
    """Cross-platform, process-scoped lock backed by a shared data file."""

    def __init__(self, path: Path, *, role: str):
        self.path = path
        self.role = role
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = self.path.open("x+b")
        except FileExistsError:
            handle = self.path.open("r+b")

        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b" ")
                handle.flush()
            handle.seek(0)
            self._lock(handle)
        except OSError as exc:
            handle.close()
            raise ProcessAlreadyRunningError(
                f"Another {self.role} process is already running"
            ) from exc

        metadata = {
            "pid": os.getpid(),
            "role": self.role,
            "started_at": datetime.now(UTC).isoformat(),
        }
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(metadata, separators=(",", ":")).encode("utf-8"))
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            handle.seek(0)
            self._unlock(handle)
        finally:
            handle.close()

    @staticmethod
    def _lock(handle: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return

        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(handle: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def __enter__(self) -> ProcessSingleton:
        self.acquire()
        return self

    def __exit__(self, *_args) -> None:
        self.release()
