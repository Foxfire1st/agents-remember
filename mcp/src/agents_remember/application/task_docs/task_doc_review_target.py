"""Target-resolution guard for ``task_doc`` review operations.

Prevents ``begin_review`` and ``record_review`` from silently falling back to master
``task.json`` when ``slug`` is omitted on a leaf contract, leaf-bound seat, or task
directory that already holds sibling leaf documents. Atomic-master ``record_route_review``
is intentionally exempt and keeps its own canonical series binding.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.application.agent_binding import AgentBinding, read_agent_binding
from agents_remember.worktrees.worktree_contract import WorktreeContract

from .task_doc_route_review import TaskDocError

_GUARDED_REVIEW_OPS = frozenset({"begin_review", "record_review"})


def _safe_agent_binding() -> AgentBinding | None:
    try:
        return read_agent_binding()
    except ValueError:
        return None


def _sibling_leaf_files(task_root: Path) -> list[str]:
    if not task_root.is_dir():
        return []
    return sorted(path.name for path in task_root.glob("*.json") if path.name != "task.json")


def enforce_review_target(
    operation: str,
    task_root: Path,
    slug: str | None,
    contract: WorktreeContract | None,
) -> None:
    """Refuse ambiguous or mismatched master review targets before reading ``task.json``."""
    if operation not in _GUARDED_REVIEW_OPS:
        return
    binding = _safe_agent_binding()
    leaf_bound_seat = (
        binding is not None
        and binding.task_ref is not None
        and Path(binding.task_ref.path).name != "task.json"
    )
    leaf_contract = contract is not None and contract.kind == "leaf"
    normalized_slug = slug.strip() if isinstance(slug, str) else ""

    if not normalized_slug:
        if leaf_contract:
            raise TaskDocError(
                f"task_doc {operation} requires an explicit leaf slug when a leaf contract is "
                "active; omitting slug would silently target master task.json.",
                code="review-target-slug-required",
            )
        if leaf_bound_seat:
            assert binding is not None and binding.task_ref is not None
            raise TaskDocError(
                f"task_doc {operation} requires an explicit leaf slug for leaf-bound seat "
                f"{binding.task_ref.path}; omitting slug would silently target master task.json.",
                code="review-target-slug-required",
            )
        siblings = _sibling_leaf_files(task_root)
        if siblings:
            sample = ", ".join(siblings[:3])
            raise TaskDocError(
                f"task_doc {operation} requires an explicit slug because leaf documents exist in "
                f"{task_root.name} ({sample}); pass the leaf slug, or explicit slug='task' for "
                "a genuine master-level review.",
                code="review-target-slug-required",
            )
        return

    if normalized_slug == "task":
        if leaf_contract:
            raise TaskDocError(
                f"task_doc {operation} cannot target master slug='task' while a leaf contract "
                "is active; pass the leaf's own slug.",
                code="review-target-mismatch",
            )
        if leaf_bound_seat:
            assert binding is not None and binding.task_ref is not None
            raise TaskDocError(
                f"task_doc {operation} cannot target master slug='task' from leaf-bound seat "
                f"{binding.task_ref.path}; pass the leaf's own slug.",
                code="review-target-mismatch",
            )


__all__ = ["enforce_review_target"]
