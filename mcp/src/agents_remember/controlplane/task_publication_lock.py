"""Short repository-scoped CAS mutex for authoritative task and door publication."""

from __future__ import annotations

import fcntl
import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def task_publication_lock_path(coordination_root: Path, repo_id: str) -> Path:
    digest = hashlib.sha256(repo_id.encode("utf-8")).hexdigest()[:24]
    return coordination_root / "controlplane" / "task-publication" / f"{digest}.lock"


@contextmanager
def task_publication_lock(
    coordination_root: Path,
    repo_id: str,
    *,
    create: bool = True,
) -> Iterator[None]:
    """Serialize exact source CAS and multi-document bytes, never semantic state."""

    path = task_publication_lock_path(coordination_root, repo_id)
    if not path.exists() and not create:
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
