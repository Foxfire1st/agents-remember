"""Named-ref-only closeout facts for an atomic master series."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import completion_blockers
from agents_remember.tasks.document_refs import (
    ResolvedTaskDocument,
    TaskDocumentRefError,
    TaskDocumentTopology,
)
from agents_remember.worktrees.integration.integration_branch_authority import (
    branch_worktree_owners,
)
from agents_remember.worktrees.modules.git import (
    branch_commit,
    is_ancestor,
    repository_identity,
    require_git,
    worktree_dirty,
)
from agents_remember.worktrees.queue.closeout_queue import CloseoutQueueError
from agents_remember.worktrees.queue.closeout_recovery import MemoryCloseoutOutcome
from agents_remember.worktrees.scheduling_mode import effective_execution_nature
from agents_remember.worktrees.series_leaf_contracts import exact_atomic_leaf_contracts
from agents_remember.worktrees.task_resolver import leaf_enclosure_path
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract


def require_closeout_publication_authority(contract: WorktreeContract) -> None:
    """Prove the atomic-completion facts a closeout publication owes, before anything is committed.

    ONE evaluation, read by BOTH surfaces: the dry run calls it from
    :func:`~agents_remember.worktrees.modules.closeout.closeout_preview_payload` and the apply from
    :func:`publish_closeout_under_authority`, so a preview cannot answer ``would-closeout`` for a
    series the apply will refuse. That gate used to exist only behind the apply, which is why a
    partial master's preview promised a closeout that then refused on every completion blocker --
    the misleading plan that started this repair.

    A leaf owes nothing here: :func:`publish_closeout_under_authority` returns straight to its
    publication for a leaf, and this keeps exactly that shape, so no leaf closeout preview changes.

    Closeout does not move a protected integration ref, so it must not acquire the landing-only
    integration authority lock.
    """

    if contract.kind == "leaf":
        return
    if contract.kind != "series":
        raise RuntimeError("atomic series closeout authority requires a series contract")
    topology = TaskDocumentTopology(contract.coordination_root)
    master_ref = topology.canonical_ref(contract.repo_name, contract.task_root / "task.json")
    _require_atomic_master_complete(topology, master_ref)
    _require_every_atomic_leaf_landed(contract)


def publish_closeout_under_authority[T](
    contract: WorktreeContract, publication: Callable[[], T]
) -> T:
    """Re-prove atomic completion before closeout publication.

    The same evaluation the dry run reads, so the preview and the apply cannot disagree about
    whether this contract may be closed out at all.
    """

    require_closeout_publication_authority(contract)
    return publication()


def publish_series_integration_under_authority[T](
    contract: WorktreeContract,
    publication: Callable[[], T],
) -> T:
    """Hold the atomic blocker and exact task/ref authority through its one landing."""

    if contract.kind != "series":
        raise RuntimeError("atomic series integration authority requires a series contract")
    topology = TaskDocumentTopology(contract.coordination_root)
    master_ref = topology.canonical_ref(contract.repo_name, contract.task_root / "task.json")
    _require_atomic_master_complete(topology, master_ref)
    current = load_contract(contract.contract_path)
    if current != contract:
        raise RuntimeError(_contract_changed_detail(contract, "re-run the integration preview"))
    _require_atomic_master_complete(topology, master_ref)
    _require_every_atomic_leaf_landed(current)
    return publication()


@dataclass(frozen=True)
class SeriesCheckpointRefs:
    """One live capture of the exact refs an unfinished master's checkpoint would land.

    The checkpoint owns its candidate: nothing here is read from the contract's closeout cells,
    because a master landed before completion may never have been closed out at all.
    """

    code_commit: str
    memory_content_commit: str = ""


def capture_series_checkpoint_refs(contract: WorktreeContract) -> SeriesCheckpointRefs:
    """Capture the exact live code and memory refs an unfinished master will land."""

    if contract.kind != "series":
        raise RuntimeError("atomic series checkpoint capture requires a series contract")
    code_commit = _series_branch_tip(contract.code_repo_path, contract.code_work_branch)
    if not code_commit:
        raise CloseoutQueueError(
            "atomic-series-checkpoint-no-code-ref",
            _unresolved_series_branch_detail(contract, "code", "worktree_checkpoint_landing"),
        )
    if contract.memory_mode != "external":
        return SeriesCheckpointRefs(code_commit=code_commit)
    memory = series_memory_closeout(contract, code_commit)
    return SeriesCheckpointRefs(
        code_commit=code_commit,
        memory_content_commit=memory.memory_commit,
    )


def _contract_changed_detail(contract: WorktreeContract, action: str) -> str:
    return (
        f"master {contract.task_id!r}: its contract {contract.contract_path} changed after the "
        f"landing was prepared, so nothing was landed; {action} against the current contract"
    )


def _memory_repository(series: WorktreeContract) -> Path:
    """The external memory repository the contract names; its absence is a contract-cell error."""

    if series.memory_repo_path is None:
        raise RuntimeError(
            f"master {series.task_id!r} uses external memory but its contract "
            f"{series.contract_path} has no memory repo_path; set memory.repo_path there and retry"
        )
    return series.memory_repo_path


def _series_branch_tip(repository: Path, branch: str) -> str:
    """The commit a series work branch names, or ``""`` when the branch does not resolve.

    Git reports an unresolved ref in its own words, which name neither the master nor the contract
    cell that holds the branch; every caller refuses in the product's words instead.
    """

    try:
        return branch_commit(repository, branch)
    except RuntimeError:
        return ""


def _unresolved_series_branch_detail(series: WorktreeContract, side: str, retry: str) -> str:
    """Name the master, the branch that does not resolve, the cell that names it, and the repair."""

    repository, branch = (
        (series.code_repo_path, series.code_work_branch)
        if side == "code"
        else (series.memory_repo_path, series.memory_work_branch)
    )
    return (
        f"master {series.task_id!r}: its series {side} branch {branch!r} does not resolve to a "
        f"commit in {repository}, so the master's {side} line cannot be read. The branch is named "
        f"by the {side}.work_branch cell of {series.contract_path}; restore that branch at the "
        f"master's last {side} commit, or correct the cell if it misnames the branch, then retry "
        f"{retry}"
    )


def require_series_checkpoint_authority(contract: WorktreeContract) -> None:
    """Refuse a checkpoint whose master is already a finished unit.

    One definition, two callers, and both are needed: the checkpoint's preflight evaluates it so
    the dry-run preview and the apply refuse identically and for the same named reason, and
    publication re-evaluates it so a master that completes *between* preflight and the ref move
    is refused there too. A checkpoint may never downgrade a finished integration to a weaker
    claim.
    """

    if contract.kind != "series":
        raise RuntimeError("atomic series checkpoint authority requires a series contract")
    topology = TaskDocumentTopology(contract.coordination_root)
    master_ref = topology.canonical_ref(contract.repo_name, contract.task_root / "task.json")
    if topology.resolve(master_ref).document.status == "Completed":
        raise CloseoutQueueError(
            "atomic-series-checkpoint-master-complete",
            f"master {master_ref.key} is already Completed; land it with worktree_integrate, whose "
            "route records a completed integration, rather than with the checkpoint route",
        )


def publish_series_checkpoint_under_authority[T](
    contract: WorktreeContract,
    publication: Callable[[], T],
    expected: SeriesCheckpointRefs,
) -> T:
    """Hold the exact task/ref authority through one non-final master exit.

    :func:`publish_series_integration_under_authority` proves the atomic master is a **finished
    unit**: its task document is ``Completed`` and every canonical leaf has its own landed
    enclosure. A master landed before completion has neither, so before this route existed a partial master had
    no way to land its accumulated line at all.

    This route keeps every authority that protects *other* owners' refs -- the series contract
    binding, the atomic landing authority, the source-lineage proof, the replay/ff source-state gate
    and the master-handover gate all still run in the caller -- and drops only the two assumptions
    that the master is finished. It retires nothing: no cleanup runs, and the recorded state is
    ``checkpointed`` rather than ``completed``.

    ``expected`` is **required, never defaulted**, and it is the refs the checkpoint captured at
    preflight. Revalidating that candidate is the entire reason this route exists, so a default
    would be a fail-open hole in exactly the repair it was written for: an omitted argument would
    silently admit whatever the live refs happened to be, i.e. a pair the preview never showed.
    Revalidation re-reads the live refs immediately before
    the irreversible ref move, so a candidate that moved in between refuses instead of landing.
    """

    require_series_checkpoint_authority(contract)
    current = load_contract(contract.contract_path)
    if current != contract:
        raise RuntimeError(_contract_changed_detail(contract, "re-run worktree_checkpoint_landing"))
    _require_checkpoint_candidate_unchanged(current, expected)
    return publication()


def _require_checkpoint_candidate_unchanged(
    contract: WorktreeContract, expected: SeriesCheckpointRefs
) -> None:
    """Re-prove the captured candidate against the live refs at the protected boundary."""

    live = capture_series_checkpoint_refs(contract)
    if live != expected:
        raise CloseoutQueueError(
            "atomic-series-checkpoint-candidate-moved",
            f"master {contract.task_id!r}: the atomic series candidate refs moved after this "
            "checkpoint captured them: captured "
            f"code={expected.code_commit} memory={expected.memory_content_commit}, live "
            f"code={live.code_commit} memory={live.memory_content_commit}. Re-run "
            "worktree_checkpoint_landing so the new refs are captured before they land",
        )


def _require_every_atomic_leaf_landed(series: WorktreeContract) -> None:
    _exact_atomic_landing_chain(series)


def _exact_atomic_landing_chain(series: WorktreeContract) -> list[WorktreeContract]:
    return _require_exact_atomic_landing_chain(series, exact_atomic_leaf_contracts(series))


def _require_exact_atomic_landing_chain(
    series: WorktreeContract,
    contracts: dict[str, WorktreeContract],
) -> list[WorktreeContract]:
    """Prove every canonical leaf landed on the series ref, in one order, over one spine.

    Base-to-tip equality cannot order this chain once a master reconciles with a sibling: a sync
    advances the recorded base pair to the official line the master absorbed, while a leaf landed
    before that reconciliation keeps the base it really started from, so the old walk from
    ``code_base_commit`` could not even find its first leaf. The order is read from the landings
    themselves instead -- the leaf tips are totally ordered by ancestry -- and every step between
    two consecutive landings is then proved to be either the exact previous landing or that landing
    merged with an official position this contract itself synced with. Every leaf is still proved
    landed, no leaf may start off the master's own line, and nothing but leaf landings and the
    reconciled source line may reach the ref.
    """

    ordered = _ordered_atomic_landing_chain(series, contracts)
    _require_chain_origin(series, ordered)
    _require_landing_spine_side(series, ordered, side="code")
    if series.memory_mode == "external":
        _require_landing_spine_side(series, ordered, side="memory")
    return ordered


def _ordered_atomic_landing_chain(
    series: WorktreeContract,
    contracts: dict[str, WorktreeContract],
) -> list[WorktreeContract]:
    """The canonical leaves, oldest landing first, each one proved against its enclosure.

    The order is the leaves' own landed ancestry, on both sides of the pair, so it survives a
    reconciliation that moved the recorded base and any leaf created afterwards.
    """

    remaining = dict(contracts)
    for leaf in remaining.values():
        _require_atomic_leaf_landed(series, leaf)
    ordered: list[WorktreeContract] = []
    while remaining:
        next_ids = [
            leaf_id
            for leaf_id, leaf in remaining.items()
            if all(
                _leaf_landing_precedes(series, leaf, other)
                for other_id, other in remaining.items()
                if other_id != leaf_id
            )
        ]
        if len(next_ids) != 1:
            raise CloseoutQueueError(
                "atomic-series-leaf-chain-invalid",
                _unordered_leaves_detail(series, remaining),
            )
        ordered.append(remaining.pop(next_ids[0]))
    return ordered


def _unordered_leaves_detail(
    series: WorktreeContract, remaining: dict[str, WorktreeContract]
) -> str:
    """Name the leaves whose landings cannot be ordered, and what clears it."""

    unordered = [
        f"{first_id!r} and {second_id!r}"
        for index, (first_id, first) in enumerate(remaining.items())
        for second_id, second in list(remaining.items())[index + 1 :]
        if not _leaf_landing_precedes(series, first, second)
        and not _leaf_landing_precedes(series, second, first)
    ]
    named = "; ".join(unordered) if unordered else f"leaves {sorted(remaining)!r}"
    return (
        f"master {series.task_id!r}: the landings of {named} are not ordered on the master's "
        "code-and-memory line (one pair recorded by two enclosures, or landings on different "
        "lines), so the leaves do not form one exact landing chain. Give each of those enclosures "
        "its own landing pair in the integrated_code_commit and integrated_memory_content_commit "
        "cells of its series-contract.md, then retry closeout"
    )


def _leaf_landing_precedes(
    series: WorktreeContract,
    earlier: WorktreeContract,
    later: WorktreeContract,
) -> bool:
    """Whether one leaf's landing is on the way to another's, on both sides of the pair.

    The order is over the *pair*, not over the code commit alone: a leaf whose code leg is
    ``not-applicable`` lands a memory commit and records the code position it stood on, so two
    leaves may share one code commit and remain two distinct landings -- each memory commit makes
    the pair unique. Only a pair that is equal on both sides is one landing recorded twice, and
    that stays unordered.
    """

    same_code = earlier.integrated_code_commit == later.integrated_code_commit
    same_memory = earlier.integrated_memory_content_commit == later.integrated_memory_content_commit
    if same_code and (series.memory_mode != "external" or same_memory):
        return False
    if not same_code and not is_ancestor(
        series.code_repo_path,
        earlier.integrated_code_commit,
        later.integrated_code_commit,
    ):
        return False
    if series.memory_mode != "external":
        return True
    memory_repo = _memory_repository(series)
    if same_memory:
        return True
    return is_ancestor(
        memory_repo,
        earlier.integrated_memory_content_commit,
        later.integrated_memory_content_commit,
    )


def _require_chain_origin(
    series: WorktreeContract,
    ordered: list[WorktreeContract],
) -> None:
    """Prove the chain starts where the master's own line starts, on both sides of the pair.

    With no reconciliation the oldest leaf still has to start at the recorded base exactly, which is
    the rule the chain walk used to enforce for every step. Once a sync has advanced that base, the
    oldest leaf's base and the position the first sync advanced from must lie on one line -- either
    direction, because a leaf may have landed before or after the sync -- and every other leaf has
    to start at or after the chain's own origin.
    """

    if not ordered:
        return
    root = ordered[0]
    if series.sync_log:
        origin_code, origin_memory = _series_pre_sync_base(series)
        _require_same_line(
            series.code_repo_path,
            root.code_base_commit,
            origin_code,
            _Leg(side="code", leaf_id=root.leaf_id),
        )
        if series.memory_mode == "external":
            _require_same_line(
                _memory_repository(series),
                root.memory_base_commit,
                origin_memory,
                _Leg(side="memory", leaf_id=root.leaf_id),
            )
    elif root.code_base_commit != series.code_base_commit or (
        series.memory_mode == "external" and root.memory_base_commit != series.memory_base_commit
    ):
        raise CloseoutQueueError(
            "atomic-series-leaf-chain-invalid",
            f"master {series.task_id!r}: the oldest landed leaf {root.leaf_id!r} starts at code "
            f"{root.code_base_commit} / memory {root.memory_base_commit}, not at the master's "
            f"recorded base code {series.code_base_commit} / memory {series.memory_base_commit}. "
            "Correct the code_base_commit and memory_base_commit cells of that leaf's "
            "series-contract.md or of the master's, or run worktree_sync on the master, then "
            "retry closeout",
        )
    for leaf in ordered:
        _require_leaf_starts_on_the_chain(series, root, leaf)


def _series_pre_sync_base(series: WorktreeContract) -> tuple[str, str]:
    """The pair the first recorded sync advanced from: the master's line before it reconciled."""

    first = series.sync_log[0]
    return (
        first.get("codeBaseFrom", "") or series.code_base_commit,
        first.get("memoryBaseFrom", "") or series.memory_base_commit,
    )


@dataclass(frozen=True)
class _Leg:
    """One side of the pair, and the oldest landed leaf whose base is being proved on it."""

    side: str
    leaf_id: str


def _require_same_line(repository: Path, left: str, right: str, leg: _Leg) -> None:
    """Refuse two positions that are not on one line, in either direction."""

    if (
        bool(left)
        and bool(right)
        and (is_ancestor(repository, left, right) or is_ancestor(repository, right, left))
    ):
        return
    raise CloseoutQueueError(
        "atomic-series-leaf-chain-invalid",
        f"atomic leaf {leg.leaf_id!r} {leg.side} base {left} is not on the master's own line at "
        f"{right}, the position its first sync advanced from. Correct that leaf's recorded base "
        "in its series-contract.md, or run worktree_sync on the master, then retry closeout",
    )


def _require_leaf_starts_on_the_chain(
    series: WorktreeContract,
    root: WorktreeContract,
    leaf: WorktreeContract,
) -> None:
    """Refuse a leaf whose recorded base is off the master's own line."""

    if not is_ancestor(series.code_repo_path, root.code_base_commit, leaf.code_base_commit):
        raise CloseoutQueueError(
            "atomic-series-leaf-chain-invalid",
            f"atomic leaf {leaf.leaf_id!r} does not start on the series code line: its recorded "
            f"base {leaf.code_base_commit} is not descended from the chain origin "
            f"{root.code_base_commit} (leaf {root.leaf_id!r}). Correct code_base_commit in that "
            "leaf's series-contract.md, or sync the leaf onto the master line, then retry closeout",
        )
    if series.memory_mode != "external":
        return
    if not is_ancestor(
        _memory_repository(series), root.memory_base_commit, leaf.memory_base_commit
    ):
        raise CloseoutQueueError(
            "atomic-series-leaf-chain-invalid",
            f"atomic leaf {leaf.leaf_id!r} does not start on the series memory line: its recorded "
            f"base {leaf.memory_base_commit} is not descended from the chain origin "
            f"{root.memory_base_commit} (leaf {root.leaf_id!r}). Correct memory_base_commit in "
            "that leaf's series-contract.md, or sync the leaf onto the master line, then retry "
            "closeout",
        )


def _require_landing_spine_side(
    series: WorktreeContract,
    ordered: list[WorktreeContract],
    *,
    side: str,
) -> None:
    """Prove one series ref is exactly the leaf landings, joined by the reconciled source line.

    Each leaf's landing must be an ancestor of the ref, each step from one landing to the next must
    add nothing but an official position one of these contracts synced with, and the same holds for
    the step from the last landing to the ref. A ref that simply *is* the last landing -- every
    master that reconciled before its final leaf landed -- needs no step at all.
    """

    landings, bases, recorded_base, repository, branch = _spine_facts(series, ordered, side=side)
    positions = _landing_source_positions(series, ordered, side=side)
    tip = _series_branch_tip(repository, branch)
    if not tip:
        raise CloseoutQueueError(
            "atomic-series-ref-unresolved",
            _unresolved_series_branch_detail(series, side, "closeout"),
        )
    if not ordered:
        origin = (
            _series_pre_sync_base(series)[0 if side == "code" else 1]
            if series.sync_log
            else recorded_base
        )
        _require_admitted_step(
            repository, origin, tip, positions, _SpineStep(side, "the series ref")
        )
        return
    previous = landings[0]
    for index, leaf in enumerate(ordered):
        if not is_ancestor(repository, landings[index], tip):
            raise CloseoutQueueError(
                "atomic-series-leaf-not-landed",
                f"atomic leaf {leaf.leaf_id!r} has not landed on the exact series {side} ref; "
                f"land its enclosure on the master's {side} line with worktree_integrate, or "
                "correct its recorded integration in its series-contract.md, then retry closeout",
            )
        if index:
            _require_admitted_step(
                repository,
                previous,
                bases[index],
                positions,
                _SpineStep(side, f"atomic leaf {leaf.leaf_id!r}"),
            )
        previous = landings[index]
    if tip != landings[-1]:
        if not recorded_base or not is_ancestor(repository, recorded_base, tip):
            raise CloseoutQueueError(
                "atomic-series-leaf-chain-invalid",
                f"master {series.task_id!r}: the series {side} ref {branch} does not descend from "
                f"the recorded base {recorded_base}; restore the ref or correct the master's "
                f"recorded {side} base in its series-contract.md, then retry closeout",
            )
        _require_admitted_step(
            repository, landings[-1], tip, positions, _SpineStep(side, "the series ref")
        )


def _spine_facts(
    series: WorktreeContract,
    ordered: list[WorktreeContract],
    *,
    side: str,
) -> tuple[list[str], list[str], str, Path, str]:
    """The landings, the leaf bases, the recorded base, the repository, and the ref name."""

    if side == "code":
        return (
            [leaf.integrated_code_commit for leaf in ordered],
            [leaf.code_base_commit for leaf in ordered],
            series.code_base_commit,
            series.code_repo_path,
            series.code_work_branch,
        )
    return (
        [leaf.integrated_memory_content_commit for leaf in ordered],
        [leaf.memory_base_commit for leaf in ordered],
        series.memory_base_commit,
        _memory_repository(series),
        series.memory_work_branch,
    )


@dataclass(frozen=True)
class _SpineStep:
    """One step on a series spine: the side it is proved on, and what lands after it."""

    side: str
    step: str


def _require_admitted_step(
    repository: Path,
    earlier: str,
    later: str,
    positions: tuple[str, ...],
    step: _SpineStep,
) -> None:
    """Refuse a step that adds history beyond the earlier landing and the official positions.

    ``--no-merges`` is what makes a merge the reconciliation device rather than a loophole: a merge
    introduces no commit of its own, so only genuinely new non-merge history is refused.

    The step's commits are ENUMERATED and each one is tested for membership in the recorded
    positions. They are never subtracted from the revision walk: ``--not <a position>`` removes
    every commit that position reaches, so a single recorded position descending from the step's own
    endpoint removed the whole step and the check passed vacuously -- which is how a genuinely
    foreign commit could be admitted. A position that reaches past the step can no longer erase it,
    and the position still admits the step it actually records.

    "Admits the step it actually records" is why a position INSIDE the step admits its own ancestry
    too. The shape this rule names is one landing merged with an official position the contract
    itself synced with, and when that merge is the step's own endpoint the whole step IS that
    position's history: ``8dfc11b8`` on the 260915-KS master is exactly that merge -- its parents are
    the previous landing ``7db50f8f`` and the synced position ``8dd62345``, and every commit the step
    adds is reachable from ``8dd62345``. Admitting the position while refusing the line it introduced
    refused the very shape the rule exists to allow. The admission stays bounded to the positions
    this call was given: a position that merely descends FROM the step is not inside it, so it still
    cannot vacate a commit the step genuinely introduced.
    """

    if earlier == later:
        return
    if not is_ancestor(repository, earlier, later):
        raise CloseoutQueueError(
            "atomic-series-leaf-chain-invalid",
            f"atomic series {step.side} ref does not carry {step.step} in the leaf landing order "
            f"({earlier} is not an ancestor of {later}); repair that landing record in the "
            "enclosure's series-contract.md or the ref, then retry closeout",
        )
    revision_args = [
        "rev-list",
        "--no-merges",
        "--full-history",
        later,
        "--not",
        earlier,
    ]
    if step.side == "memory":
        revision_args.extend(["--", ".", ":(top,exclude)memory.md"])
    admitted = set(positions)
    official = _positions_inside_the_step(repository, earlier, later, admitted)
    foreign = [
        commit
        for commit in require_git(repository, revision_args).split()
        if commit not in admitted
        and not _reached_by_an_official_position(repository, commit, official)
    ]
    if foreign:
        raise CloseoutQueueError(
            "atomic-series-leaf-chain-invalid",
            f"atomic series {step.side} ref adds history beyond the exact leaf landing chain and the "
            f"reconciled source line at {step.step}: {', '.join(foreign[:5])}. Move those "
            "commits off the master line (or land them through a leaf), or record the official "
            "position they came from with worktree_sync, then retry closeout",
        )


def _positions_inside_the_step(
    repository: Path,
    earlier: str,
    later: str,
    positions: set[str],
) -> tuple[str, ...]:
    """The recorded positions INSIDE this step: strictly after its start, at or before its end.

    A position past the step is deliberately excluded, and so is the start itself. Excluding the
    start costs nothing -- the revision walk already removes everything the start reaches -- while
    excluding a position past the step is what keeps the enumeration honest: such a position reaches
    the whole step and would erase a commit the step genuinely introduced.
    """

    return tuple(
        position
        for position in sorted(positions)
        if position != earlier
        and is_ancestor(repository, earlier, position)
        and is_ancestor(repository, position, later)
    )


def _reached_by_an_official_position(
    repository: Path,
    commit: str,
    official: tuple[str, ...],
) -> bool:
    """Whether a position inside this step reaches the commit, so the step is that position's line."""

    return any(is_ancestor(repository, commit, position) for position in official)


def _landing_source_positions(
    series: WorktreeContract,
    ordered: list[WorktreeContract],
    *,
    side: str,
) -> tuple[str, ...]:
    """Every official position the chain's own syncs reconciled with, and the recorded base.

    A sync is journaled on the contract it ran for, and that is not always the series contract: the
    master's own reconciliation writes its entry here, while a leaf whose base had to be advanced
    writes its entry on the leaf's contract -- which is the only place that position is recorded.
    Reading the series contract alone therefore hid a leaf-level sync from this check and refused a
    step to a base the leaf's own contract records as synced, so both are read here. The union is
    still bounded by the chain's own evidence: every contract named here is one of the ordered
    leaves this closeout already proved landed, so a position no contract ever synced with stays
    inadmissible.
    """

    key = "codeBaseTo" if side == "code" else "memoryBaseTo"
    base = series.code_base_commit if side == "code" else series.memory_base_commit
    positions = {base}
    for contract in (series, *ordered):
        positions.update(entry.get(key, "") for entry in contract.sync_log)
    return tuple(sorted(position for position in positions if position))


def _require_atomic_leaf_landed(
    series: WorktreeContract,
    leaf: WorktreeContract,
) -> None:
    if not _atomic_leaf_code_matches(series, leaf):
        raise CloseoutQueueError(
            "atomic-series-leaf-not-landed",
            f"atomic leaf {leaf.leaf_id!r} has not landed on the exact series code ref; land its "
            "enclosure with worktree_integrate or correct its recorded integration in its "
            "series-contract.md, then retry closeout",
        )
    if series.memory_mode != "external":
        return
    if not _atomic_leaf_memory_matches(series, leaf):
        raise CloseoutQueueError(
            "atomic-series-leaf-memory-not-landed",
            f"atomic leaf {leaf.leaf_id!r} has not landed its exact external-memory pair; land its "
            "enclosure with worktree_integrate or correct its recorded memory integration in its "
            "series-contract.md, then retry closeout",
        )


def _atomic_leaf_code_matches(
    series: WorktreeContract,
    leaf: WorktreeContract,
) -> bool:
    code_commit = leaf.integrated_code_commit
    parent_path = leaf.parent_contract_path.resolve() if leaf.parent_contract_path else None
    found = (
        leaf.repo_name,
        leaf.coordination_root.resolve(),
        leaf.task_root.resolve(),
        leaf.contract_path.resolve(),
        parent_path,
        leaf.memory_mode,
        leaf.integration_status,
        leaf.code_source_branch,
        code_commit,
    )
    expected = (
        series.repo_name,
        series.coordination_root.resolve(),
        series.task_root.resolve(),
        leaf_enclosure_path(series.task_root, leaf.leaf_id).resolve(),
        series.contract_path.resolve(),
        series.memory_mode,
        "completed",
        series.code_work_branch,
        leaf.code_commit,
    )
    return (
        bool(code_commit)
        and found == expected
        and _same_repository(leaf.code_repo_path, series.code_repo_path)
        and is_ancestor(series.code_repo_path, leaf.code_base_commit, code_commit)
    )


def _atomic_leaf_memory_matches(
    series: WorktreeContract,
    leaf: WorktreeContract,
) -> bool:
    memory_commit = leaf.integrated_memory_content_commit
    if leaf.memory_repo_path is None or series.memory_repo_path is None:
        return False
    return (
        bool(memory_commit)
        and leaf.memory_mode == "external"
        and leaf.memory_source_branch == series.memory_work_branch
        and memory_commit == leaf.memory_content_commit
        and _same_repository(leaf.memory_repo_path, series.memory_repo_path)
        and is_ancestor(series.memory_repo_path, leaf.memory_base_commit, memory_commit)
    )


def _same_repository(left: Path, right: Path) -> bool:
    left_identity = repository_identity(left)
    right_identity = repository_identity(right)
    return left_identity is not None and left_identity == right_identity


def _require_atomic_master_complete(
    topology: TaskDocumentTopology,
    master_ref: TaskDocumentRef,
) -> ResolvedTaskDocument:
    master = topology.resolve(master_ref)
    sprint_ref = topology.parent(master_ref)
    sprint = topology.resolve(sprint_ref) if sprint_ref is not None else None
    try:
        # L13-R5a: the effective nature — a nature-less legacy master executes
        # atomically under the default and closes out without migration.
        nature = effective_execution_nature(
            master.document, sprint.document if sprint is not None else None
        )
    except TaskDocumentRefError as exc:
        raise CloseoutQueueError(
            "atomic-series-closeout-task-invalid", f"{exc.status}: {exc}"
        ) from exc
    if nature != "atomic":
        raise CloseoutQueueError(
            "atomic-series-closeout-task-invalid",
            f"master {master_ref.key} has execution nature {nature!r}, but series closeout "
            "requires the canonical atomic master task; set that master's executionNature to "
            "atomic with task_doc, then retry closeout",
        )
    blockers = completion_blockers(master.document)
    # ``!= "Completed"`` is deliberate here and must stay: closeout proves a *completion* fact.
    # An ``abandoned`` master is terminal but not complete, and its retirement route is
    # ``worktree_abandon``, never this one -- so it is named rather than silently accepted.
    if master.document.status != "Completed" or blockers:
        if master.document.status == "abandoned":
            raise CloseoutQueueError(
                "atomic-series-closeout-master-abandoned",
                f"atomic master {master_ref.key} is abandoned, not completed; an abandoned master is reclaimed "
                "with worktree_abandon and is never closed out",
            )
        raise CloseoutQueueError(
            "atomic-series-closeout-master-incomplete",
            f"atomic master {master_ref.key} status={master.document.status!r} requires exact completion facts: {blockers!r}; finish the named rows and set its status Completed with task_doc before closeout",
        )
    return master


def refuse_series_workbench_commit(contract: WorktreeContract) -> None:
    """Refuse dirty checkouts that own the exact atomic integration refs."""

    if contract.kind == "leaf":
        return
    branches: list[tuple[Path, str, tuple[str, ...]]] = [
        (contract.code_repo_path, contract.code_work_branch, ())
    ]
    if contract.memory_mode == "external":
        branches.append((_memory_repository(contract), contract.memory_work_branch, ("memory.md",)))
    for repository, branch, exclude_paths in branches:
        for checkout in branch_worktree_owners(repository, branch):
            if worktree_dirty(checkout, exclude_paths=exclude_paths):
                raise RuntimeError(
                    f"master {contract.task_id!r}: closeout cannot create code or memory commits "
                    f"on its integration worktree {checkout} (branch {branch}), which has "
                    "uncommitted changes; deliver them through a closed leaf and its Owner's "
                    "publication, or discard them, then retry closeout"
                )


def series_memory_closeout(contract: WorktreeContract, code_commit: str) -> MemoryCloseoutOutcome:
    """Capture the actual memory output ref beside the exact series code candidate."""

    memory_repo = _memory_repository(contract)
    if _series_branch_tip(contract.code_repo_path, contract.code_work_branch) != code_commit:
        raise RuntimeError(
            f"master {contract.task_id!r}: its code branch {contract.code_work_branch} moved "
            "before the memory candidate was captured; retry closeout so both refs are captured "
            "together"
        )
    memory_commit = _series_branch_tip(memory_repo, contract.memory_work_branch)
    if not memory_commit:
        raise RuntimeError(
            _unresolved_series_branch_detail(
                contract, "memory", "the closeout or checkpoint landing"
            )
        )
    if not is_ancestor(memory_repo, contract.memory_base_commit, memory_commit):
        raise RuntimeError(
            f"master {contract.task_id!r}: its memory branch {contract.memory_work_branch} does "
            f"not descend from the recorded memory base {contract.memory_base_commit}; restore the "
            "branch or correct memory base_commit in its series-contract.md, then retry closeout"
        )
    return MemoryCloseoutOutcome(memory_commit=memory_commit)
