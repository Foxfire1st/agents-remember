"""Cross-process serialization for the shared terminal catalog."""

from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def exclusive_terminal_catalog_lock(catalog_path: Path) -> Iterator[None]:
    """Hold the stable sibling lock across one catalog read/mutate/write lifecycle.

    The catalog itself is atomically replaced, so it cannot be the lock inode. Deployment is
    POSIX (the terminal host already depends on ``fcntl``); there is deliberately no process-local
    fallback that would pretend to protect dashboard/MCP writers across processes.
    """

    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = catalog_path.with_name(f".{catalog_path.name}.lock")
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
