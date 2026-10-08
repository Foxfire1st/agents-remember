"""Bounded receipt reconciliation driven by the existing terminal observer.

The cursor is disposable and holds only directory iterators. After restart, exact terminal
contract truth recovers obligations even when the closing call could not persist its debt.
"""

from __future__ import annotations

import os
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from agents_remember.cli.role_launch_archive import ArchiveAttempt, leaf_receipt_ref
from agents_remember.cli.role_launch_receipts import EXECUTIONS_DIRECTORY, _read_receipt
from agents_remember.kernel.authority import require_within_coordination
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.tasks import read_task_doc
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract

SCAN_ENTRIES_PER_PASS = 128
RECOVERY_BUDGET_SECONDS = 2.0
RECOVERY_INTERVAL_SECONDS = 5.0


def _scan(root: Path, depth: int = 0) -> Generator[Path | None]:
    """Yield for each inspected entry, so even an empty large tree costs a bounded pass.

    No symlink is followed. Report artifacts and message bindings are not receipt directories.
    The depth ceiling bounds open directory iterators; supported task paths fit well within it.
    """
    if depth >= 16:
        return
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                path = Path(entry.path)
                is_record = (
                    entry.is_file(follow_symlinks=False)
                    and path.suffix == ".json"
                    and (
                        root.name == EXECUTIONS_DIRECTORY
                        or (root.name == "history" and root.parent.name == EXECUTIONS_DIRECTORY)
                    )
                )
                yield path if is_record else None
                if entry.is_dir(follow_symlinks=False) and _descend(root, entry.name):
                    yield from _scan(path, depth + 1)
    except OSError:
        return


def _descend(root: Path, name: str) -> bool:
    if root.name == "notes":
        return name == "reports"
    if root.name == "reports":
        return name == EXECUTIONS_DIRECTORY
    if root.name == EXECUTIONS_DIRECTORY:
        return name == "history"
    if root.name == "history":
        return False
    return name not in {".git", "enclosures", "requirements", "onboarding"}


@dataclass
class LeafArchiveRecovery:
    config: McpRuntimeConfig
    _cursor: Generator[Path | None] | None = field(default=None, init=False)
    _next_at: float = field(default=0, init=False)

    def close(self) -> None:
        if self._cursor is not None:
            self._cursor.close()
            self._cursor = None

    def tick(self) -> None:
        now = time.monotonic()
        if now < self._next_at:
            return
        self._next_at = now + RECOVERY_INTERVAL_SECONDS
        if self._cursor is None:
            self._cursor = _scan(self.config.coordination_root / "tasks")
        deadline = now + RECOVERY_BUDGET_SECONDS
        for _ in range(SCAN_ENTRIES_PER_PASS):
            try:
                path = next(self._cursor)
            except StopIteration:
                self._cursor = None
                break
            if path is not None:
                self._record(path, deadline)
            if time.monotonic() >= deadline:
                break

    def _record(self, path: Path, deadline: float) -> None:
        try:
            path = require_within_coordination(self.config, str(path), "launch receipt")
            receipt = _read_receipt(path)
            if receipt is None:
                return
            ref = leaf_receipt_ref(receipt)
            contract = self._terminal_contract(receipt, ref)
            if contract is not None and ref is not None:
                ArchiveAttempt(self.config, contract, ref, deadline).record(path, receipt)
        except (OSError, ValueError, HTTPException):
            # An unreadable record proves no agent identity. The closing answer names it; the
            # observer leaves it alone and revisits it on the next bounded scan.
            return

    def _terminal_contract(
        self, receipt: dict[str, Any], ref: dict[str, str] | None
    ) -> WorktreeContract | None:
        if ref is None:
            return None
        workspace = receipt.get("workspace")
        address = workspace.get("contractPath") if isinstance(workspace, dict) else None
        if not isinstance(address, str):
            return None
        contract = load_contract(require_within_coordination(self.config, address, "leaf contract"))
        if contract.kind != "leaf" or contract.cleanup not in {"completed", "abandoned"}:
            return None
        if (
            contract.repo_name != ref["repository"]
            or contract.coordination_root != self.config.coordination_root
        ):
            return None
        task_path = require_within_coordination(
            self.config,
            str(self.config.coordination_root / "tasks" / ref["repository"] / ref["path"]),
            "leaf document",
        )
        document = read_task_doc(task_path)
        if (
            document.kind != "subTask"
            or document.id != contract.leaf_id
            or document.repo != ref["repository"]
            or task_path.parent != contract.task_root
        ):
            return None
        return contract
