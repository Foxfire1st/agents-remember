"""In-process ownership and reaping for detached lifecycle worker children."""

from __future__ import annotations

import subprocess
import threading
from typing import Any


class DetachedWorkerChildren:
    """Retain each spawned child until one dedicated waiter reaps it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._children: dict[int, subprocess.Popen[Any]] = {}

    def retain(self, process: subprocess.Popen[Any]) -> None:
        with self._lock:
            current = self._children.get(process.pid)
            if current is not None and current is not process:
                raise RuntimeError("a different retained worker already owns this numeric pid")
            if current is process:
                return
            self._children[process.pid] = process
        threading.Thread(
            target=self._wait_and_release,
            args=(process,),
            name=f"ar-worker-reaper-{process.pid}",
            daemon=True,
        ).start()

    def _wait_and_release(self, process: subprocess.Popen[Any]) -> None:
        try:
            process.wait()
        finally:
            with self._lock:
                if self._children.get(process.pid) is process:
                    del self._children[process.pid]


_CHILDREN = DetachedWorkerChildren()


def retain_detached_worker_child(process: subprocess.Popen[Any]) -> None:
    """Transfer one real Popen child to the lifecycle-owned reaper."""

    _CHILDREN.retain(process)
