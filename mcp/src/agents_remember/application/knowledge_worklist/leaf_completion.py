"""Complete and persist a leaf run: onboarding, uncovered changes, and named failures.

The leaf owns side selection and applicability. These completion entry points call that owner
at runtime, preserving its probes and the one recomputation route without an import cycle.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents_remember.application.knowledge_worklist.code import CodeReadError
from agents_remember.application.knowledge_worklist.compute import (
    Incomplete,
    git_failure,
    incomplete_worklist,
    worklist_digest,
)
from agents_remember.application.knowledge_worklist.onboarding_trace import (
    TraceSideRequest,
    onboarding_trace_sides,
    worklist_onboarding,
)
from agents_remember.application.knowledge_worklist.unexplained import (
    answering_trace_subjects,
    open_count,
    settle_uncovered,
)
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory_quality.knowledge_worklist_section import worklist_summary
from agents_remember.worktrees.knowledge_validation import LayoutProbeError
from agents_remember.worktrees.modules.onboarding_trace import OnboardingTraceSides
from agents_remember.worktrees.worktree_contract import WorktreeContract

if TYPE_CHECKING:
    from agents_remember.application.knowledge_worklist.leaf_inputs import ExplicitSides


def worklist_over(contract: WorktreeContract, sides: ExplicitSides) -> dict[str, Any] | None:
    """The full worklist over explicit sides: L08's run, MIK-R30's items, MIK-R10's settling.

    ``contract`` supplies what the onboarding gate reads beside the trees (the storage settings and
    the converted-base cache). ``None`` when both memory sides are unconverted.
    """

    # Runtime leaf ownership preserves the composition seam without an import cycle.
    from agents_remember.application.knowledge_worklist import leaf  # noqa: PLC0415

    document = leaf.worklist_for_sides(sides)
    if document is None:
        return None
    if document.get("state") != "complete":
        return _settled_unexplained(document)
    try:
        code_blobs = _candidate_code_blobs(sides, document)
    except (CodeReadError, subprocess.SubprocessError) as error:
        return incomplete_worklist(
            Incomplete("C", f"the code candidate's blobs cannot be read: {error}"),
            owner=document.get("owner"),
            pairing=document.get("pairing"),
        )
    candidate = sides.memory_candidate
    request = TraceSideRequest(
        owner=sides.owner or contract.leaf_id or contract.task_name,
        memory_repository=sides.memory_repository,
        memory_base=sides.memory_base,
        memory_candidate=candidate if isinstance(candidate, str) else Path(candidate),
        code_repository=sides.code_repository,
        code_base=sides.base,
        cache_directory=sides.cache_directory,
        held_base_tree=sides.held_base_tree,
    )
    # MIK-R30's items join the one list (ruling Q2), over the worklist's own B..C paths.
    document = worklist_onboarding(document, contract, request, code_blobs=code_blobs)
    return _settled_unexplained(document)


def _candidate_code_blobs(
    sides: ExplicitSides, document: Mapping[str, Any]
) -> dict[str, str | None]:
    """Each changed source file's blob at the code candidate C (MIK-R53 rule 3).

    Read from the worklist run's own candidate tree, so the blobs agree with the B..C paths
    the run judged. A tree Git cannot read makes the run incomplete, named: it never counts
    as unchanged.
    """

    pairing = document.get("pairing") or {}
    tree = (pairing.get("candidate") or {}).get("tree")
    if not isinstance(tree, str) or not tree:
        raise CodeReadError("the worklist names no code candidate tree")
    blobs = CodeObjects(sides.code_repository).tree(tree)
    return {
        str(change["path"]): blobs.get(str(change["path"]))
        for change in document.get("changes", [])
        if isinstance(change, Mapping) and isinstance(change.get("path"), str)
    }


def _settled_unexplained(document: dict[str, Any]) -> dict[str, Any]:
    """MIK-R10: an uncovered file's unexplained change is answered by its onboarding trace."""

    if document.get("state") != "complete":
        return document
    items = settle_uncovered(document.get("items") or [])
    settled = {**document, "items": items, "digest": worklist_digest("complete", items, [])}
    if isinstance(document.get("unexplained"), dict):
        settled["unexplained"] = {**document["unexplained"], "openCount": open_count(items)}
    trace = document.get("onboardingTrace")
    if isinstance(trace, dict):
        # A row that answers an uncovered item's onboarding trace is needed, not unnecessary.
        answering = answering_trace_subjects(items)
        settled["onboardingTrace"] = {
            **trace,
            "unnecessaryRows": [
                row for row in trace.get("unnecessaryRows") or () if row["subject"] not in answering
            ],
        }
    return settled


def leaf_onboarding_trace_sides(
    contract: WorktreeContract, *, memory_tree: Path | None = None
) -> OnboardingTraceSides | None:
    """A leaf's MIK-R30 gate sides from its contract; ``None`` where today's gate still applies.

    Today's gate applies to a non-leaf contract, a leaf without its own memory worktree, and a leaf
    whose K_B and K_C are both unconverted; that route keeps its existing gate. K_B pairs exactly
    as the worklist's does. A side that cannot be established is an ``incomplete`` side, which the
    gate reports as a finding, never a pass.
    """

    # Runtime leaf ownership preserves the composition seam without an import cycle.
    from agents_remember.application.knowledge_worklist import leaf  # noqa: PLC0415

    candidate = memory_tree if memory_tree is not None else contract.memory_worktree
    if contract.kind != "leaf" or candidate is None:
        return None
    memory_repository = contract.memory_repo_path or contract.memory_worktree
    if memory_repository is None:
        return None
    owner = contract.leaf_id or contract.task_name
    try:
        if not leaf._trace_gate_converted(contract, memory_repository, Path(candidate)):
            return None  # cheap probe before any read: nothing is converted, today's gate applies
    except LayoutProbeError as error:  # never taken for unconverted memory
        return OnboardingTraceSides(owner=owner, incomplete=f"layout marker: {error}")
    try:
        memory_base = leaf.paired_memory_commit(
            memory_repository,
            contract.memory_source_branch,
            contract.code_repo_path,
            contract.code_base_commit,
        )
        return onboarding_trace_sides(
            leaf._trace_request(contract, memory_repository, memory_base, Path(candidate))
        )
    except Exception as error:  # an unestablished side is a finding, never a lapsed gate
        return OnboardingTraceSides(owner=owner, incomplete=f"{type(error).__name__}: {error}")


def recompute_leaf_worklist(
    contract: WorktreeContract,
) -> tuple[dict[str, Any], str | None] | None:
    """The one recompute entry point (MIK-R08 rule 8): compute, persist, and never raise.

    Every trigger calls this: the curator's memory-quality run, managed-sync completion, and the
    gate's closeout and landing evaluations (MIK-R09). It returns the document and where it was
    persisted (``None`` when the enclosure cannot be written), or ``None`` where no worklist
    applies. A failure the run did not anticipate is still a
    worklist -- the ``incomplete`` one naming the run itself, persisted -- never a silently missing
    list (rule 4) and never a failure of the route that triggered it.
    """

    # Runtime leaf ownership preserves the composition seam without an import cycle.
    from agents_remember.application.knowledge_worklist import leaf  # noqa: PLC0415

    if contract.kind != "leaf":
        return None
    try:
        document = leaf.leaf_worklist(contract, persist=False)
    except subprocess.SubprocessError as error:  # Git failed or timed out: named as git (MIK-R09)
        document = incomplete_worklist(
            git_failure(error), owner=contract.leaf_id or None, pairing=None
        )
    except Exception as error:  # the run's own failure is an incomplete worklist, named
        document = incomplete_worklist(
            Incomplete("worklist run", f"{type(error).__name__}: {error}"),
            owner=contract.leaf_id or None,
            pairing=None,
        )
    if document is None:
        return None
    path = leaf.worklist_path(contract)
    if path is not None:
        try:
            leaf.persist_worklist(path, document)
        except OSError:
            path = None  # an unwritable enclosure: the worklist is returned, not persisted
    return document, None if path is None else path.as_posix()


@dataclass(frozen=True)
class LeafWorklistRecompute:
    """The composition-bound :class:`KnowledgeWorklistPort` of the worktree layer."""

    def recompute(self, contract: WorktreeContract) -> dict[str, Any] | None:
        found = recompute_leaf_worklist(contract)
        if found is None:
            return None
        document, path = found
        return worklist_summary(document, path)
