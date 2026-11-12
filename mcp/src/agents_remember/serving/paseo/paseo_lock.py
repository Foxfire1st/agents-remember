"""One bounded exclusion per host home, shared by install, start and explicit stop."""

from __future__ import annotations

import fcntl
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agents_remember.errors import PaseoRuntimeFailure


@contextmanager
def runtime_lock(home: Path, deadline: float) -> Iterator[bool]:
    path = home / "agents-remember" / "runtime.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        waited = False
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                waited = True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PaseoRuntimeFailure(
                        "host_operation_busy",
                        "daemon",
                        f"another operation still owns {home}; retry the same command after it finishes",
                    ) from None
                time.sleep(min(0.05, remaining))
        try:
            yield waited
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
