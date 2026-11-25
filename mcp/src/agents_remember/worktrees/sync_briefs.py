"""Refresh file briefs once the persisted memory side has completed its content sync."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agents_remember.kernel.memory_ledger import LEDGER_RELATIVE_PATH
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH
from agents_remember.worktrees.knowledge_validation import PairedCode
from agents_remember.worktrees.modules.git import require_git
from agents_remember.worktrees.services import refresh_written_memory_briefs
from agents_remember.worktrees.sync_transaction_state import SyncOperationRecord


def refresh_synced_memory_briefs(
    record: SyncOperationRecord, coordination_root: Path
) -> str | None:
    """Refresh a durably settled checkout when its committed memory content changed."""

    memory = record.memory
    if memory is None or memory.temporary or memory.preSyncHead == memory.resultHead:
        return None
    memory_root = Path(memory.worktree)
    try:
        if not (memory_root / LAYOUT_MARKER_PATH).is_file():
            return None
        changed = require_git(
            memory_root,
            [
                "diff",
                "--name-only",
                "--no-renames",
                memory.preSyncHead,
                memory.resultHead,
                "--",
                ".",
                f":!{LEDGER_RELATIVE_PATH}",
            ],
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        return f"File briefs could not identify the synced memory tree: {error}"
    if not changed:
        return None
    return refresh_written_memory_briefs(
        memory_root,
        PairedCode(Path(record.code.worktree), record.code.resultHead),
        coordination_root,
    )
