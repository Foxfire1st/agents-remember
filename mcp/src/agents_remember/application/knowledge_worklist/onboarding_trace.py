"""MIK-R30's ``onboarding_trace`` item kind: its registration, its sides and its worklist items.

**Registration (MIK-R08 rule 1, MIK-R30 rule 7).**

* kind: ``onboarding_trace``;
* subject key: ``onboarding:<path>`` for a file card, ``onboarding:<route>/overview`` for a route
  overview (``onboarding:overview`` for the root route);
* facts: the changed source files it covers (``sources``), plus the card's Markdown and sidecar
  paths and whether they carry a counted change (``countedChange``);
* satisfying-row rule: a counted onboarding change (``facts.countedChange``), or the leaf's history
  row with that subject and disposition ``no_impact``. The row half is the registry's common
  subject lookup; :func:`~agents_remember.worktrees.modules.onboarding_trace.onboarding_item_open`
  applies both halves.

The package ``__init__`` imports this module, so the kind is registered whenever the worklist is,
and the worklist's ``kinds`` list never depends on import order.

**Sides (MIK-R07 rule 0, MIK-R24 rule 7).** :func:`onboarding_trace_sides` reads K_C from the leaf's
memory candidate directory and K_B from its commit; an unconverted K_B of a converted K_C is
replaced by its conversion, read through the worklist's converted-base cache
(:func:`~.base_cache.converted_base_files`, which also holds the onboarding Markdown the gate
compares). It returns ``None`` where neither side is converted: the leaf keeps today's gate.

**One list (ruling Q2).** :func:`with_onboarding_items` adds the gate's items to the leaf's
``knowledge-worklist/v1`` document, with their facts and what satisfies them, and folds them into
the digest, so the closeout gate (MIK-R09) consumes one list.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from agents_remember.application.knowledge_worklist.base_cache import converted_base_files
from agents_remember.application.knowledge_worklist.compute import (
    Incomplete,
    incomplete_worklist,
    worklist_digest,
)
from agents_remember.application.knowledge_worklist.registry import (
    ItemKind,
    register_item_kind,
    subject_row,
)
from agents_remember.errors import AgentsRememberError
from agents_remember.kernel import coordination_context_resolver as resolver
from agents_remember.kernel.coordination_context.models import StorageSettings
from agents_remember.kernel.recorded_reads import observed_exists
from agents_remember.memory.conversion.base import pinned_version
from agents_remember.memory_quality.knowledge_validator.trees import (
    KnowledgeTree,
    knowledge_tree_from_directory,
    knowledge_tree_from_git,
)
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH
from agents_remember.worktrees.modules.context import contract_context
from agents_remember.worktrees.modules.onboarding_trace import (
    ITEM_KIND,
    OnboardingTraceResult,
    OnboardingTraceSides,
    onboarding_trace_result,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

__all__ = [
    "ONBOARDING_TRACE_KIND",
    "TraceSideRequest",
    "onboarding_trace_sides",
    "trace_context",
    "with_onboarding_items",
    "worklist_onboarding",
]

ONBOARDING_TRACE_KIND: Final = register_item_kind(
    ItemKind(
        name=ITEM_KIND,
        subject="onboarding card or route overview (onboarding:<path> | onboarding:<route>/overview)",
        subject_pattern=r"^onboarding:\S",
        facts=("sources", "markdown", "sidecar", "countedChange"),
        satisfying_row=(
            "a counted change of the card's or overview's Markdown or sidecar between K_B and K_C "
            "(facts.countedChange), or the leaf's onboarding_trace row with this subject and "
            "disposition no_impact (MIK-R30 rules 2 and 7)"
        ),
        row_lookup=subject_row(ITEM_KIND),
        owner="MIK-R30",
    )
)


@dataclass(frozen=True)
class TraceSideRequest:
    """The explicit inputs of :func:`onboarding_trace_sides`: who owns the history, and where."""

    owner: str
    memory_repository: Path
    memory_base: str
    """K_B's commit."""
    memory_candidate: Path | str
    """K_C: the memory working tree, or a Git tree of ``memory_repository`` (the gate's candidate)."""
    code_repository: Path
    code_base: str
    """B, the commit an unconverted K_B converts at when its own trailer names none."""
    cache_directory: Path | None = None
    """The converted-base cache (:mod:`.base_cache`); ``None`` converts on every run."""
    held_base_tree: str | None = None
    """The comparison's already converted K_B; no new conversion when supplied."""


def onboarding_trace_sides(request: TraceSideRequest) -> OnboardingTraceSides | None:
    """The gate's sides over an explicit K_B commit; ``None`` when neither side is converted."""

    candidate = (
        knowledge_tree_from_directory(request.memory_candidate, label="K_C")
        if isinstance(request.memory_candidate, Path)
        else knowledge_tree_from_git(
            request.memory_repository, request.memory_candidate, label="K_C"
        )
    )
    base = knowledge_tree_from_git(request.memory_repository, request.memory_base, label="K_B")
    if not base.converted and not candidate.converted:
        return None
    # Mixed formats are never compared as they are: every legacy card would differ from its
    # converted counterpart and the gate would pass vacuously (review N1).
    if base.converted and not candidate.converted:
        return OnboardingTraceSides(
            owner=request.owner,
            incomplete="K_C is unconverted while K_B is converted; the crossing sync converts it",
        )
    try:
        version = pinned_version(candidate)
    except (KeyError, TypeError, ValueError):
        version = None
    if version is None:
        return OnboardingTraceSides(
            owner=request.owner,
            incomplete=(
                "K_C holds the layout marker without a pinned conversion-format version, so K_B "
                "cannot be compared as its conversion"
            ),
        )
    converted_base = not base.converted
    if converted_base:
        base = (
            knowledge_tree_from_git(
                request.memory_repository,
                request.held_base_tree,
                label=f"converted:{request.memory_base}",
            )
            if request.held_base_tree is not None
            else KnowledgeTree(
                label=f"converted:{request.memory_base}",
                files=converted_base_files(
                    request.memory_repository,
                    request.memory_base,
                    code=(request.code_repository, request.code_base),
                    version=version,
                    cache_directory=request.cache_directory,
                ),
            )
        )
    return OnboardingTraceSides(
        owner=request.owner,
        base=_onboarding_and_history(base),
        candidate=_onboarding_and_history(candidate),
        pairing={
            "base": request.code_base,
            "memoryBase": request.memory_base,
            "convertedBase": converted_base,
            "memoryCandidate": str(request.memory_candidate)
            if isinstance(request.memory_candidate, str)
            else request.memory_candidate.as_posix(),
        },
    )


def _onboarding_and_history(tree: KnowledgeTree) -> dict[str, bytes]:
    return {
        path: data
        for path, data in tree.files.items()
        if path.startswith(("onboarding/", "knowledge/history/")) or path == LAYOUT_MARKER_PATH
    }


def trace_context(contract: WorktreeContract) -> Any:
    """The storage settings the gate reads: the memory worktree's own, as closeout resolves them.

    The existing settings selectors/readers record their actual absence and consumed bytes. No
    separate before/after hash can conceal a changed-and-restored settings read.
    """

    settings = (
        None
        if contract.memory_worktree is None
        else contract.memory_worktree / "system/settings.md"
    )
    if settings is not None and observed_exists(settings) and settings.is_file():
        storage, _ = resolver.parse_coordination_settings(settings)
        return _TraceContext(storage=storage, code_repository_name=contract.repo_name)
    try:
        context = contract_context(contract)
    except AgentsRememberError:
        # No settings anywhere: the resolver's default storage, which stores every source's card
        # (the strictest reading: every changed file is gated).
        return _TraceContext(storage=StorageSettings(), code_repository_name=contract.repo_name)
    return context


@dataclass(frozen=True)
class _TraceContext:
    storage: Any
    code_repository_name: str


def with_onboarding_items(
    document: Mapping[str, Any], result: OnboardingTraceResult
) -> dict[str, Any]:
    """The worklist ``document`` with the gate's items in its one list (ruling Q2).

    Each item carries its facts and ``satisfiedBy`` (``counted-change``, the satisfying row's ID,
    or ``None`` while open). The digest covers them. Rows about files the leaf did not change are
    listed under ``onboardingTrace.unnecessaryRows`` (report-only). A history file the gate cannot
    read makes the worklist ``incomplete``, naming it, so no list is ever silently short.
    """

    if result.problems:
        code, path, message = result.problems[0]
        return incomplete_worklist(
            Incomplete("K_C", f"{path or code}: {message}"),
            owner=document.get("owner"),
            pairing=document.get("pairing"),
        )
    updated = dict(document)
    items = sorted(
        [*document.get("items", []), *(item.to_document() for item in result.items)],
        key=lambda item: (item["kind"], item["subject"]),
    )  # one list in the worklist's own order (review N2)
    updated["items"] = items
    updated["onboardingTrace"] = {
        "openCount": len(result.open_items),
        "unnecessaryRows": [
            {"subject": subject, "row": row} for subject, row in result.unnecessary
        ],
    }
    updated["digest"] = worklist_digest("complete", items, [])
    return updated


def worklist_onboarding(
    document: dict[str, Any], contract: WorktreeContract, request: TraceSideRequest
) -> dict[str, Any]:
    """Compute the gate over the worklist's own changed paths and merge its items (ruling Q2)."""

    if document.get("state") != "complete":
        return document
    try:
        sides = onboarding_trace_sides(request)
    except Exception as error:  # any side failure is named, never a short list (review N4)
        return incomplete_worklist(
            Incomplete(
                "K_B",
                f"the onboarding gate's sides cannot be read: {type(error).__name__}: {error}",
            ),
            owner=document.get("owner"),
            pairing=document.get("pairing"),
        )
    if sides is None:
        return document
    changed = sorted({change["path"] for change in document.get("changes", [])})
    return with_onboarding_items(
        document, onboarding_trace_result(trace_context(contract), changed, sides)
    )
