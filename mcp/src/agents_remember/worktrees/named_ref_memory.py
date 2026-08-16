"""Read external-memory authority from one exact local branch ref."""

from __future__ import annotations

from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_ledger import LedgerError, MemoryLedger, parse_ledger_text
from agents_remember.worktrees.modules.git import local_branch_ref


def load_named_ref_ledger(repository: Path, branch: str) -> MemoryLedger:
    """Parse ``memory.md`` from an exact local ref, never the ambient checkout."""

    ref = local_branch_ref(branch)
    result = run_git(repository, ["show", f"{ref}:memory.md"])
    if result.returncode != 0:
        detail = result.stderr.strip() or "memory.md is absent from the named memory source"
        raise LedgerError(f"cannot read {ref}:memory.md: {detail}")
    return parse_ledger_text(result.stdout)
