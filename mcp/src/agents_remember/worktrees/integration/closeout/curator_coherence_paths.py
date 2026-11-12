"""The curator authority's existing task-artifact paths and explicit evidence namespaces."""

from pathlib import Path

from agents_remember.errors import CuratorCoherenceError
from agents_remember.models.lifecycles.curator_coherence import CuratorCoherencePaths
from agents_remember.worktrees.worktree_contract import WorktreeContract


def curator_coherence_paths(contract: WorktreeContract) -> CuratorCoherencePaths:
    require_leaf_external_memory(contract)
    reports = contract.task_root / "notes" / "reports"
    history = reports / "curator-coherence" / contract.leaf_id
    return CuratorCoherencePaths(
        canonical=reports / f"{contract.leaf_id}-curator-coherence.json",
        generations=history / "generations",
        snapshots=history / "attempts",
        attestations=history / "attestations",
    )


def require_leaf_external_memory(contract: WorktreeContract) -> None:
    if contract.kind != "leaf" or contract.memory_mode != "external":
        raise CuratorCoherenceError(
            "curator-coherence-not-applicable",
            "curator coherence requires one external-memory leaf enclosure",
            next_action="status",
        )
    if contract.memory_worktree is None:
        raise CuratorCoherenceError(
            "curator-coherence-memory-worktree-missing",
            "the external-memory leaf has no memory worktree",
            next_action="worktree_status",
        )


def resolve_curator_evidence_ref(contract: WorktreeContract, reference: str) -> Path:
    """Resolve one explicit evidence namespace without implicit path fallback."""

    namespace, separator, relative_text = reference.partition(":")
    roots = {
        "code": contract.code_worktree,
        "memory": contract.memory_worktree,
        "task": contract.task_root,
    }
    root = roots.get(namespace)
    relative = Path(relative_text)
    if (
        not separator
        or root is None
        or not relative_text
        or relative.is_absolute()
        or relative == Path(".")
    ):
        raise CuratorCoherenceError(
            "curator-coherence-evidence-invalid",
            "judgment evidence must use one explicit code:, memory:, or task: file reference",
            next_action="publish",
        )
    resolved_root = root.resolve()
    resolved = (resolved_root / relative).resolve(strict=False)
    if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
        raise CuratorCoherenceError(
            "curator-coherence-evidence-invalid",
            f"judgment evidence must identify one existing scoped file: {reference}",
            next_action="publish",
        )
    return resolved
