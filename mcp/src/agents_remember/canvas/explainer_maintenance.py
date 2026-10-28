"""Update, inspect, retire and cancel an existing explainer seat."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from agents_remember.application.review_frame_store import (
    frame_scope,
    revision_manifest,
)
from agents_remember.canvas import explainer_service as lifecycle
from agents_remember.canvas.explainer_grades import Grades, GradeStore, grades_path
from agents_remember.canvas.explainer_paths import (
    EXPLAINER_FRAME,
    generation_path,
    read_workspace_id,
    receipt_id_segment,
    reference_path_of,
    repository_path,
    retire_receipts_root,
)
from agents_remember.canvas.explainer_staleness import (
    BlockFingerprint,
    grade_staleness,
    stale_grade_references,
)
from agents_remember.canvas.explainer_staleness_read import (
    GradeInputs,
    gone_grade_references,
    screen_target,
    staleness_read,
)
from agents_remember.canvas.explainer_state import (
    ExplainerRefused,
    ExplainerStateError,
    find_pending_receipt,
    generation,
    latest_build_failure,
    read_binding,
)
from agents_remember.canvas.explainer_workspace import (
    EnvironmentInputs,
    EnvironmentRun,
    EnvironmentUpdate,
    RetireOutcome,
    RetirePlace,
    RetireRequest,
    resume_retire,
    retire_environment,
    update_environment,
)
from agents_remember.canvas.paths import PathRefused, task_root
from agents_remember.kernel.git_command import run_git
from agents_remember.models.knowledge.review_frame import FrameBinding
from agents_remember.tasks.planned_comparison import retained_revision_numbers


def _latest_manifest(deps: lifecycle.ExplainerDeps, scope: lifecycle.LeafScope):
    """The highest retained revision's manifest of the leaf's explainer frame, or ``None``.

    The store's own retained list is the bound (descending), so revision 32 and beyond are read
    exactly like the first one; no display ceiling decides which revision the service sees.
    """

    root = _task_folder(deps, scope)
    try:
        frame = frame_scope(root, scope.leaf_id, EXPLAINER_FRAME)
    except ValueError:
        return None
    for number in retained_revision_numbers(frame.frame_dir):
        manifest = revision_manifest(frame.frame_dir, number)
        if manifest is not None:
            return manifest
    return None


def _tree_paths_at(repository: Path, tree: str) -> set[str] | None:
    if not repository.is_dir():
        return None
    result = run_git(repository, ["ls-tree", "-r", "--name-only", tree])
    if result.returncode != 0:
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _default_present_references(
    deps: lifecycle.ExplainerDeps,
    scope: lifecycle.LeafScope,
    grades: Grades,
    target_binding: FrameBinding | None,
) -> set[str]:
    all_refs = {item.reference for item in grades.grades}
    if target_binding is None or not all_refs:
        return all_refs
    if deps.present_references is not None:
        supplied = deps.present_references(scope, target_binding)
        if supplied is not None:
            return set(supplied)
    with suppress(PathRefused, KeyError):
        code_repo = repository_path(deps.config, scope.repository_id)
        base_paths = _tree_paths_at(code_repo, target_binding.code_base_tree)
        cand_paths = _tree_paths_at(code_repo, target_binding.code_candidate_tree)
        if base_paths is not None and cand_paths is not None:
            known_paths = base_paths | cand_paths
            present: set[str] = set()
            for ref in all_refs:
                ref_path = reference_path_of(ref)
                if ref_path is None or ref_path in known_paths:
                    present.add(ref)
            return present
    return all_refs


def _reconcile_grades_and_counts(
    deps: lifecycle.ExplainerDeps,
    scope: lifecycle.LeafScope,
    target_binding: FrameBinding | None,
    *,
    prune: bool = True,
) -> tuple[int | None, int | None, int, str | None]:
    """Prune absent grade references and compute ``(stale_parts, stale_grades, removed, error)``."""

    task_root_path = _task_folder(deps, scope)
    store = GradeStore(grades_path(task_root_path, scope.leaf_id), scope.leaf_id)
    try:
        stored = store.load()
    except (ExplainerRefused, ValueError, OSError) as error:
        return None, None, 0, str(error)
    fingerprints: Mapping[str, BlockFingerprint] = (
        deps.grade_fingerprints(scope, target_binding)
        if deps.grade_fingerprints is not None and target_binding is not None
        else {}
    )
    removed = 0
    if prune and stored.grades:
        initial_states = grade_staleness(stored, fingerprints)
        present = _default_present_references(deps, scope, stored, target_binding)
        gone = gone_grade_references(initial_states, present)
        if gone:
            stored, removed = store.prune(gone)
    states = grade_staleness(stored, fingerprints)
    stale_grades_count = len(stale_grade_references(states))
    manifest = _latest_manifest(deps, scope)
    if manifest is None or target_binding is None:
        return 0, stale_grades_count, removed, None
    try:
        read = staleness_read(
            task_root=task_root_path,
            leaf_id=scope.leaf_id,
            manifest=manifest,
            target=screen_target(
                task_root=task_root_path,
                environment=target_binding,
                supplemental=manifest.supplemental,
            ),
            grades=GradeInputs(grades=stored, fingerprints=fingerprints),
        )
    except (ExplainerRefused, ValueError) as error:
        return None, stale_grades_count, removed, str(error)
    return len(read.stale_parts), len(read.stale_grades), removed, None


def _count_blocks_changed(
    deps: lifecycle.ExplainerDeps,
    previous: FrameBinding,
    bound: lifecycle.BoundComparison,
) -> int | None:
    if deps.counter is None:
        return None
    if bound.record is not None:
        return deps.counter(previous, bound.record)
    with suppress(Exception):
        return deps.counter(previous, bound.binding)
    return None


def update_seat(deps: lifecycle.ExplainerDeps, scope: lifecycle.LeafScope) -> EnvironmentUpdate:
    """Rebind one environment to the leaf's current comparison, or refuse by name (rule 6)."""

    workspace = lifecycle._workspace(deps.config, scope)
    # A removed explainer has no folder at all, and taking the lock would make one (its ``state/``):
    # the absence is answered before the lock (MIK-R211 v5 rule 12).
    if not workspace.is_dir():
        raise ExplainerRefused(
            lifecycle.MISSING, f"leaf {scope.leaf_id} has no environment to update"
        )
    gen_file = generation_path(deps.config, scope.repository_id, scope.task, scope.leaf_id)
    with lifecycle._lock(deps, scope, wait=False):
        number = generation(gen_file)
        seat = lifecycle._seat(deps.config, scope, number)
        if deps.host.in_turn(seat):
            raise ExplainerRefused(
                lifecycle.IN_TURN,
                f"the explainer of leaf {scope.leaf_id} is in a turn; nothing is queued",
            )
        facts = deps.facts(scope)
        previous = read_binding(workspace)
        if previous is None and not (workspace / "trees").is_dir():
            raise ExplainerRefused(
                lifecycle.MISSING, f"leaf {scope.leaf_id} has no environment to update"
            )
        bound = deps.comparison(scope, facts)
        prepared = deps.prepare(scope, facts, bound)
        updated = update_environment(
            EnvironmentRun(
                ref=scope.environment(deps.config),
                inputs=EnvironmentInputs(
                    binding=bound.binding,
                    packet=prepared.packet,
                    capsule=deps.capsule(scope),
                    code_repository=repository_path(deps.config, scope.repository_id),
                    memory_repository=repository_path(
                        deps.config, scope.repository_id, memory=True
                    ),
                    planned=bound.planned,
                    record_digest=bound.record_digest,
                ),
                now=deps.stamp(),
                previous=previous,
            )
        )
        blocks = None if previous is None else _count_blocks_changed(deps, previous.binding, bound)
        stale_parts, stale_grades, removed_grades, unavailable = _reconcile_grades_and_counts(
            deps, scope, bound.binding, prune=True
        )
        return replace(
            updated,
            blocks_changed=blocks,
            stale_parts=stale_parts,
            stale_grades=stale_grades,
            removed_grades=removed_grades,
            counts_unavailable=unavailable,
        )


def retire_seat(
    deps: lifecycle.ExplainerDeps,
    scope: lifecycle.LeafScope,
    *,
    receipt_id: str,
) -> RetireOutcome:
    """Remove one environment completely, waiting for the lock; a finished receipt stays in the task.

    MIK-R211 v5 rule 12: the chat is archived, the Git worktrees removed and pruned, and the leaf's
    folder deleted with everything in it, for a seat in any state -- live, closed, archived, or
    with no workspace left. The task's published revisions, grades and reports stay.
    """

    admitted_id = receipt_id_segment(receipt_id)
    workspace = lifecycle._workspace(deps.config, scope)
    task_root_path = _task_folder(deps, scope)
    receipts = retire_receipts_root(task_root_path, scope.leaf_id)
    with lifecycle._lock(deps, scope, wait=True):
        existing_binding = None
        if workspace.is_dir():
            with suppress(ExplainerStateError):
                existing_binding = read_binding(workspace)
        target_binding = None if existing_binding is None else existing_binding.binding
        if target_binding is None:
            with suppress(Exception):
                target_binding = deps.comparison(scope, deps.facts(scope)).binding
        _sp, _sg, removed_grades, _unavail = _reconcile_grades_and_counts(
            deps, scope, target_binding, prune=True
        )
        place = RetirePlace(
            workspace=workspace,
            receipts_root=receipts,
            now=deps.stamp(),
            archive_session=deps.host.archive,
            repositories=_repositories(deps, scope),
        )
        pending = find_pending_receipt(receipts, admitted_id)
        if pending is not None and pending.state != "finished":
            return replace(resume_retire(place, pending), removed_grades=removed_grades)
        number = generation(
            generation_path(deps.config, scope.repository_id, scope.task, scope.leaf_id)
        )
        outcome = retire_environment(
            RetireRequest(
                ref=scope.environment(deps.config),
                receipts_root=receipts,
                receipt_id=admitted_id,
                generation=number,
                now=deps.stamp(),
                archive_session=deps.host.archive,
            )
        )
        return replace(outcome, removed_grades=removed_grades)


def _repositories(deps: lifecycle.ExplainerDeps, scope: lifecycle.LeafScope) -> tuple[Path, ...]:
    """The two repositories whose worktree registrations a removal prunes."""

    found: list[Path] = []
    for memory in (False, True):
        with suppress(PathRefused):
            found.append(repository_path(deps.config, scope.repository_id, memory=memory))
    return tuple(found)


def _task_folder(deps: lifecycle.ExplainerDeps, scope: lifecycle.LeafScope) -> Path:
    """The leaf's task folder, from where its other reports lie (never from a caller's path)."""

    return task_root(deps.config, scope.repository_id, scope.task)


def seat_marks(
    deps: lifecycle.ExplainerDeps, scope: lifecycle.LeafScope
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The retirement marker and build state :func:`seat_state` names, without asking the host.

    A removal leaves no marker (MIK-R211 v5 rule 12), so the first is always ``None``; it stays in
    the answer for the Review tab's label, which reads both.
    """

    number = generation(
        generation_path(deps.config, scope.repository_id, scope.task, scope.leaf_id)
    )
    return None, _build_state(deps, scope, number)


def _build_state(
    deps: lifecycle.ExplainerDeps,
    scope: lifecycle.LeafScope,
    number: int,
) -> dict[str, Any] | None:
    building = lifecycle._BUILD_STATES.get((scope.repository_id, scope.task, scope.leaf_id))
    workspace = lifecycle._workspace(deps.config, scope)
    # The ledger lies in the removed folder's ``state/``: a removed workspace has no failure to show.
    failed = (
        latest_build_failure(lifecycle._ledger_path(deps.config, scope), number)
        if workspace.is_dir()
        else None
    )
    if (
        building is None
        or building.get("beforeCreate") is True
        or (failed is not None and building.get("state") != "building")
    ):
        building = failed
    return building


def seat_state(
    deps: lifecycle.ExplainerDeps, scope: lifecycle.LeafScope
) -> lifecycle.ExplainerState:
    """The page's read: no lock, no turn, no build. It names what is there and nothing more."""

    workspace = lifecycle._workspace(deps.config, scope)
    number = generation(
        generation_path(deps.config, scope.repository_id, scope.task, scope.leaf_id)
    )
    seat = lifecycle._seat(deps.config, scope, number) if number else ""
    binding = read_binding(workspace) if workspace.is_dir() else None
    state = lifecycle.SEAT_MISSING if binding is None else deps.host.seat_state(seat)
    building = _build_state(deps, scope, number)
    return lifecycle.ExplainerState(
        leaf=scope,
        workspace=workspace,
        present=binding is not None,
        generation=number,
        agent_id=seat,
        binding=binding,
        seat=state,
        retired=None,
        receipt=None,
        workspace_id=read_workspace_id(workspace),
        building=None if building is None else dict(building),
    )


@dataclass(frozen=True)
class CancelOutcome:
    """One cancel's answer: the exact seat and request it named, and what the host says after.

    MIK-R105 v1 rule 9: cancellation names whether a turn stopped, publication was prevented, or
    a request remains uncertain; it keeps the last ready revision and changes nothing else.
    ``publication_prevented`` is the route's own before/after read of the frame store, filled in
    by the caller that owns it; this outcome carries the host's facts.
    """

    leaf: lifecycle.LeafScope
    agent_id: str
    request_id: str
    seat: str
    turn_stopped: bool
    request_uncertain: bool
    permission_closed: bool
    publication_prevented: bool = True

    def as_record(self) -> dict[str, Any]:
        return {
            "leaf": {
                "repository": self.leaf.repository_id,
                "task": self.leaf.task,
                "leaf": self.leaf.leaf_id,
            },
            "agentId": self.agent_id,
            "requestId": self.request_id,
            "seat": self.seat,
            "turnStopped": self.turn_stopped,
            "requestUncertain": self.request_uncertain,
            "publicationPrevented": self.publication_prevented,
            "permissionClosed": self.permission_closed,
        }


def cancel_seat(
    deps: lifecycle.ExplainerDeps, scope: lifecycle.LeafScope, *, request_id: str
) -> CancelOutcome:
    """Cancel the leaf's seat at the exact seat and request (MIK-R105 v1 rule 9).

    The seat is the environment's recorded agent and nothing else; the request identity is the
    leaf's own build ledger row. A request the ledger does not name is reported uncertain rather
    than claimed cancelled. The host's named cancel ends the seat's turn and never archives or
    replaces it, and a permission decision that was pending is closed. Nothing writes to the
    frame store here, so the last ready revision is kept; the route measures publication itself.
    """

    state = seat_state(deps, scope)
    ledger = lifecycle._ledger_path(deps.config, scope)
    entry = lifecycle._ledger_entry(ledger, scope.leaf_id, request_id)
    request_uncertain = entry is None or entry.get("agentId") != state.agent_id
    if state.seat in {lifecycle.SEAT_ARCHIVED, lifecycle.SEAT_MISSING} and not state.agent_id:
        return CancelOutcome(
            leaf=scope,
            agent_id=state.agent_id,
            request_id=request_id,
            seat=state.seat,
            turn_stopped=False,
            request_uncertain=True,
            permission_closed=True,
        )
    answer = deps.host.cancel(state.agent_id, request_id=request_id) or {}
    return CancelOutcome(
        leaf=scope,
        agent_id=state.agent_id,
        request_id=request_id,
        seat=state.seat,
        turn_stopped=bool(answer.get("turnStopped")) if isinstance(answer, Mapping) else False,
        request_uncertain=request_uncertain
        or (not isinstance(answer, Mapping) or bool(answer.get("requestUncertain"))),
        permission_closed=(isinstance(answer, Mapping) and bool(answer.get("permissionClosed"))),
    )
