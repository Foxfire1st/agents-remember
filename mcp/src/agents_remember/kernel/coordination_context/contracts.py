"""Enclosure contract resolution through an injected worktree reader port.

Kernel owns the coordination-context resolver but sits below ``worktrees``;
the contract-file primitives are implemented by the worktree layer and bound
through ``ContractReaderPort`` at the call site.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.errors import ContractPlaceError
from agents_remember.kernel.coordination_context.models import (
    ContractReaderPort,
    EnclosureSelector,
)
from agents_remember.kernel.recorded_reads import observed_exists, observed_resolve


def resolve_contract(
    selector: EnclosureSelector,
    coordination_root: Path,
    code_repository_name: str,
    reader: ContractReaderPort,
) -> tuple[Any | None, Path | None]:
    candidate = observed_resolve(selector.contract_path) if selector.contract_path else None
    if candidate is None and selector.task_name:
        candidate = reader.find_task_contract(
            coordination_root,
            code_repository_name,
            selector.task_name,
            parent_task=selector.parent_task,
            leaf_id=selector.leaf_id,
        )
    if candidate is None and selector.worktree_name:
        candidate = reader.find_worktree_contract(
            coordination_root, code_repository_name, selector.worktree_name
        )
    if candidate is None:
        return None, None
    if not observed_exists(candidate):
        return None, candidate
    try:
        return reader.load_contract(candidate), candidate
    except ContractPlaceError:
        # MIK-R83 rule 3: a contract refused for naming another path is a
        # refusal about the caller's inputs, not an absent contract -- passing
        # it through is what keeps the copy from being silently ignored.
        raise
    except Exception:
        return None, candidate


__all__ = ["ContractReaderPort", "resolve_contract"]
