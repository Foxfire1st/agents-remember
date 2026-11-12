"""What a master's row must satisfy before reopening a leaf resets it to planning."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from agents_remember.tasks.document import TaskDocument
from agents_remember.tasks.retired_rows import refuse_retired_row


def require_reopenable_row(
    master: TaskDocument,
    master_path: Path,
    leaf_path: Path,
    row: dict[str, Any],
    error: Callable[[str], Exception],
) -> None:
    """Refuse a row that records a master retirement, or that points at another leaf document."""

    leaf_id = str(row.get("number"))
    refuse_retired_row(master, leaf_id, "reopening this task", error)
    file_name = str(row.get("file") or "")
    if not file_name:
        return
    row_path = (master_path.parent / Path(file_name).with_suffix(".json")).resolve(strict=False)
    if row_path != leaf_path.resolve(strict=False):
        raise error(f"parent row {leaf_id!r} points at {row_path}, not leaf {leaf_path}")
