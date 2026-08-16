"""Repository-wide serialization for task-derived protected-ref authority."""

from __future__ import annotations

import fcntl
import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def integration_authority_lock_path(coordination_root: Path, repo_id: str) -> Path:
    """Return a bounded lock path without trusting a repository id as a path segment."""

    digest = hashlib.sha256(repo_id.encode("utf-8")).hexdigest()[:24]
    return coordination_root / "controlplane" / "integration-authority" / f"{digest}.lock"


@contextmanager
def integration_authority_lock(
    coordination_root: Path,
    repo_id: str,
) -> Iterator[None]:
    """Exclude task-topology publication from protected-ref decisions and mutations."""

    path = integration_authority_lock_path(coordination_root, repo_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
