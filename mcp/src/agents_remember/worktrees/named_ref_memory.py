"""Derive external-memory mappings from one exact local branch ref."""

from __future__ import annotations

from pathlib import Path

from agents_remember.kernel.memory_cache import derive_memory_ledger
from agents_remember.kernel.memory_ledger import MemoryLedger
from agents_remember.worktrees.modules.git import local_branch_ref


def load_named_ref_ledger(repository: Path, branch: str) -> MemoryLedger:
    """Read committed attribution from the exact local ref, independent of cached tables."""

    return derive_memory_ledger(repository, local_branch_ref(branch))
