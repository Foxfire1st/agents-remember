"""Named-ref-only closeout facts for an atomic master series."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.memory_ledger import LedgerRow, find_mapping, parse_ledger_text
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
from agents_remember.worktrees.ledger_projection import (
    LEDGER_RELATIVE_PATH,
    LedgerProjectionRefusal,
    contract_ledger_projection,
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
        raise RuntimeError("atomic series contract changed before protected landing")
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
    ledger_commit: str = ""


def capture_series_checkpoint_refs(contract: WorktreeContract) -> SeriesCheckpointRefs:
    """Read the live series refs and prove the existing ledger maps the code ref.

    The code ref is the live ``code_work_branch`` tip and the memory ref is the live
    ``memory_work_branch`` tip -- captured, never inherited from a stale contract cell. The
    code-to-memory mapping is proved by :func:`exact_series_memory_closeout`, the same reader the
    final series closeout uses, so the two routes cannot drift into two definitions of "the ledger
    maps this code".

    A capture that cannot prove the mapping raises, which is what makes "the refs are recorded only
    once their ledger mapping holds" a property of the value rather than a separate check a caller
    could skip.
    """

    if contract.kind != "series":
        raise RuntimeError("atomic series checkpoint capture requires a series contract")
    code_commit = branch_commit(contract.code_repo_path, contract.code_work_branch)
    if not code_commit:
        raise CloseoutQueueError(
            "atomic-series-checkpoint-no-code-ref",
            f"the atomic series code work branch {contract.code_work_branch!r} does not resolve, so "
            "there is no accumulated line to checkpoint",
        )
    if contract.memory_mode != "external":
        return SeriesCheckpointRefs(code_commit=code_commit)
    memory = exact_series_memory_closeout(contract, code_commit)
    return SeriesCheckpointRefs(
        code_commit=code_commit,
        memory_content_commit=memory.memory_commit,
        ledger_commit=memory.ledger_commit,
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
            "this atomic master is already Completed; land it with worktree_integrate, whose route "
            "records a completed integration, rather than with the checkpoint route",
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
    Revalidation re-reads the live refs and re-proves their ledger mapping here, immediately before
    the irreversible ref move, so a candidate that moved in between refuses instead of landing.
    """

    require_series_checkpoint_authority(contract)
    current = load_contract(contract.contract_path)
    if current != contract:
        raise RuntimeError("atomic series contract changed before protected landing")
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
            "the atomic series candidate refs moved after this checkpoint captured them: captured "
            f"code={expected.code_commit} ledger={expected.ledger_commit}, live "
            f"code={live.code_commit} ledger={live.ledger_commit}. Re-run "
            "worktree_checkpoint_landing so the new refs are captured and their ledger mapping "
            "proved before they land",
        )


def _require_every_atomic_leaf_landed(series: WorktreeContract) -> None:
    _exact_atomic_landing_chain(series)


def _exact_atomic_landing_chain(series: WorktreeContract) -> list[WorktreeContract]:
    expected, _sprint_ref = _atomic_leaf_documents(series)
    contracts: dict[str, WorktreeContract] = {}
    for path in sorted((series.task_root / "enclosures").glob("*/series-contract.md")):
        leaf = load_contract(path)
        if (
            leaf.kind != "leaf"
            or not leaf.leaf_id
            or leaf.leaf_id in contracts
            or leaf.contract_path.resolve() != path.resolve()
        ):
            raise CloseoutQueueError(
                "atomic-series-leaf-contract-set-invalid",
                f"atomic series has an invalid or duplicate leaf enclosure: {path}",
            )
        contracts[leaf.leaf_id] = leaf
    if set(contracts) != set(expected):
        raise CloseoutQueueError(
            "atomic-series-leaf-contract-set-incomplete",
            "atomic series closeout requires one exact enclosure for every canonical leaf: "
            f"expected={sorted(expected)!r}, found={sorted(contracts)!r}",
        )
    return _require_exact_atomic_landing_chain(series, contracts)


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
                "atomic series leaves do not form one exact code-and-memory landing chain",
            )
        ordered.append(remaining.pop(next_ids[0]))
    return ordered


def _leaf_landing_precedes(
    series: WorktreeContract,
    earlier: WorktreeContract,
    later: WorktreeContract,
) -> bool:
    """Whether one leaf's landing is on the way to another's, on both sides of the pair."""

    if earlier.integrated_code_commit == later.integrated_code_commit:
        return False
    if not is_ancestor(
        series.code_repo_path,
        earlier.integrated_code_commit,
        later.integrated_code_commit,
    ):
        return False
    if series.memory_mode != "external":
        return True
    assert series.memory_repo_path is not None
    return is_ancestor(
        series.memory_repo_path,
        earlier.integrated_ledger_commit,
        later.integrated_ledger_commit,
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

    root = ordered[0]
    if series.sync_log:
        origin_code, origin_memory = _series_pre_sync_base(series)
        _require_same_line(series.code_repo_path, root.code_base_commit, origin_code, side="code")
        if series.memory_mode == "external":
            assert series.memory_repo_path is not None
            _require_same_line(
                series.memory_repo_path, root.memory_base_commit, origin_memory, side="memory"
            )
    elif root.code_base_commit != series.code_base_commit or (
        series.memory_mode == "external" and root.memory_base_commit != series.memory_base_commit
    ):
        raise CloseoutQueueError(
            "atomic-series-leaf-chain-invalid",
            "atomic series leaves do not form one exact code-and-memory landing chain: the oldest "
            "leaf does not start at the recorded base",
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


def _require_same_line(repository: Path, left: str, right: str, *, side: str) -> None:
    """Refuse two positions that are not on one line, in either direction."""

    if (
        bool(left)
        and bool(right)
        and (is_ancestor(repository, left, right) or is_ancestor(repository, right, left))
    ):
        return
    raise CloseoutQueueError(
        "atomic-series-leaf-chain-invalid",
        f"atomic series {side} position {left} is not on the master's own line at {right}",
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
            f"{root.code_base_commit}",
        )
    if series.memory_mode != "external":
        return
    assert series.memory_repo_path is not None
    if not is_ancestor(series.memory_repo_path, root.memory_base_commit, leaf.memory_base_commit):
        raise CloseoutQueueError(
            "atomic-series-leaf-chain-invalid",
            f"atomic leaf {leaf.leaf_id!r} does not start on the series memory line: its recorded "
            f"base {leaf.memory_base_commit} is not descended from the chain origin "
            f"{root.memory_base_commit}",
        )


def _require_landing_spine_side(
    series: WorktreeContract,
    ordered: list[WorktreeContract],
    *,
    side: str,
) -> None:
    """Prove one series ref is exactly the leaf landings, joined by the reconciled source line.

    Each leaf's landing must be an ancestor of the ref, each step from one landing to the next must
    add nothing but an official position this contract synced with, and the same holds for the step
    from the last landing to the ref. A ref that simply *is* the last landing -- every master that
    reconciled before its final leaf landed -- needs no step at all.
    """

    landings, bases, recorded_base, repository, branch = _spine_facts(series, ordered, side=side)
    positions = _landing_source_positions(series, side=side)
    tip = branch_commit(repository, branch)
    previous = landings[0]
    for index, leaf in enumerate(ordered):
        if not is_ancestor(repository, landings[index], tip):
            raise CloseoutQueueError(
                "atomic-series-leaf-not-landed",
                f"atomic leaf {leaf.leaf_id!r} has not landed on the exact series {side} ref",
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
                f"atomic series {side} ref does not descend from the recorded base {recorded_base}",
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
    assert series.memory_repo_path is not None
    return (
        [leaf.integrated_ledger_commit for leaf in ordered],
        [leaf.memory_base_commit for leaf in ordered],
        series.memory_base_commit,
        series.memory_repo_path,
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
    """

    if earlier == later:
        return
    if not is_ancestor(repository, earlier, later):
        raise CloseoutQueueError(
            "atomic-series-leaf-chain-invalid",
            f"atomic series {step.side} ref does not carry {step.step} in the leaf landing order",
        )
    foreign = require_git(
        repository,
        ["rev-list", "--no-merges", later, "--not", earlier, *positions],
    ).split()
    if step.side == "memory":
        # A reconciled master also records its own ledger bookkeeping on its memory line, and the
        # evidence for it is the ledger itself: integration proves the landed table is the exact
        # projection of its source plus the master's own true rows, with the whole source tail. What
        # is admitted here is only a commit that rewrites the ledger and nothing else.
        foreign = [commit for commit in foreign if not _is_ledger_recording(repository, commit)]
    if foreign:
        raise CloseoutQueueError(
            "atomic-series-leaf-chain-invalid",
            f"atomic series {step.side} ref adds history beyond the exact leaf landing chain and the "
            f"reconciled source line at {step.step}: {', '.join(foreign[:5])}",
        )


def _is_ledger_recording(repository: Path, commit: str) -> bool:
    """Whether one commit rewrites the memory ledger and nothing else."""

    changed = require_git(
        repository, ["diff-tree", "--no-commit-id", "--name-only", "-r", commit]
    ).split()
    return bool(changed) and set(changed) <= {LEDGER_RELATIVE_PATH}


def _landing_source_positions(series: WorktreeContract, *, side: str) -> tuple[str, ...]:
    """Every official position this contract's own syncs reconciled with, and its recorded base."""

    key = "codeBaseTo" if side == "code" else "memoryBaseTo"
    base = series.code_base_commit if side == "code" else series.memory_base_commit
    positions = {base}
    positions.update(entry.get(key, "") for entry in series.sync_log)
    return tuple(sorted(position for position in positions if position))


def _atomic_leaf_documents(
    series: WorktreeContract,
) -> tuple[dict[str, TaskDocumentRef], TaskDocumentRef | None]:
    topology = TaskDocumentTopology(series.coordination_root)
    master_ref = topology.canonical_ref(series.repo_name, series.task_root / "task.json")
    master = topology.resolve(master_ref)
    expected: dict[str, TaskDocumentRef] = {}
    paths: set[str] = set()
    for row in master.document.subTasks:
        if not row.file or row.number in expected:
            raise CloseoutQueueError(
                "atomic-series-leaf-task-set-invalid",
                "atomic series requires unique subtask rows with exact task-document files",
            )
        leaf_path = (master.path.parent / row.file).with_suffix(".json")
        leaf_ref = topology.canonical_ref(series.repo_name, leaf_path)
        if leaf_ref.path in paths:
            raise CloseoutQueueError(
                "atomic-series-leaf-task-set-invalid",
                "atomic series subtask rows resolve to a duplicate task document",
            )
        leaf = topology.resolve(leaf_ref)
        if (
            leaf.document.kind == "master"
            or leaf.document.id != row.number
            or topology.parent(leaf_ref) != master_ref
        ):
            raise CloseoutQueueError(
                "atomic-series-leaf-task-set-invalid",
                f"atomic series row {row.number!r} does not bind one exact owned leaf",
            )
        expected[row.number] = leaf_ref
        paths.add(leaf_ref.path)
    if not expected:
        raise CloseoutQueueError(
            "atomic-series-leaf-task-set-invalid",
            "atomic series closeout requires at least one exact owned leaf",
        )
    return expected, topology.parent(master_ref)


def _require_atomic_leaf_landed(
    series: WorktreeContract,
    leaf: WorktreeContract,
) -> None:
    if not _atomic_leaf_code_matches(series, leaf):
        raise CloseoutQueueError(
            "atomic-series-leaf-not-landed",
            f"atomic leaf {leaf.leaf_id!r} has not landed on the exact series code ref",
        )
    if series.memory_mode != "external":
        return
    if not _atomic_leaf_memory_matches(series, leaf):
        raise CloseoutQueueError(
            "atomic-series-leaf-memory-not-landed",
            f"atomic leaf {leaf.leaf_id!r} has not landed its exact external-memory pair",
        )
    assert series.memory_repo_path is not None
    code_commit = leaf.integrated_code_commit
    memory_commit = leaf.integrated_memory_content_commit
    ledger_commit = leaf.integrated_ledger_commit
    ledger = parse_ledger_text(
        require_git(series.memory_repo_path, ["show", f"{ledger_commit}:memory.md"])
    )
    mapping = find_mapping(ledger, code_commit)
    if mapping is None or mapping.memory_commit != memory_commit:
        raise CloseoutQueueError(
            "atomic-series-leaf-ledger-mapping-invalid",
            f"atomic leaf {leaf.leaf_id!r} has no exact code-to-memory mapping on the series ref",
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
    ledger_commit = leaf.integrated_ledger_commit
    if leaf.memory_repo_path is None or series.memory_repo_path is None:
        return False
    found = (
        leaf.memory_mode,
        leaf.memory_source_branch,
        memory_commit,
        ledger_commit,
    )
    expected = (
        "external",
        series.memory_work_branch,
        leaf.memory_content_commit,
        leaf.ledger_commit,
    )
    return (
        bool(memory_commit and ledger_commit)
        and found == expected
        and _same_repository(leaf.memory_repo_path, series.memory_repo_path)
        and is_ancestor(series.memory_repo_path, leaf.memory_base_commit, memory_commit)
        and is_ancestor(series.memory_repo_path, leaf.memory_base_commit, ledger_commit)
        and is_ancestor(series.memory_repo_path, memory_commit, ledger_commit)
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
            "series closeout requires the canonical atomic master task",
        )
    blockers = completion_blockers(master.document)
    # ``!= "Completed"`` is deliberate here and must stay: closeout proves a *completion* fact.
    # An ``abandoned`` master is terminal but not complete, and its retirement route is
    # ``worktree_abandon``, never this one -- so it is named rather than silently accepted.
    if master.document.status != "Completed" or blockers:
        if master.document.status == "abandoned":
            raise CloseoutQueueError(
                "atomic-series-closeout-master-abandoned",
                "this atomic master is abandoned, not completed; an abandoned master is reclaimed "
                "with worktree_abandon and is never closed out",
            )
        raise CloseoutQueueError(
            "atomic-series-closeout-master-incomplete",
            f"atomic master closeout requires exact completion facts: {blockers!r}",
        )
    return master


def refuse_series_workbench_commit(contract: WorktreeContract) -> None:
    """Refuse dirty checkouts that own the exact atomic integration refs."""

    if contract.kind == "leaf":
        return
    branches = [(contract.code_repo_path, contract.code_work_branch)]
    if contract.memory_mode == "external":
        if contract.memory_repo_path is None:
            raise RuntimeError("external-memory series closeout requires a memory repository")
        branches.append((contract.memory_repo_path, contract.memory_work_branch))
    for repository, branch in branches:
        for checkout in branch_worktree_owners(repository, branch):
            if worktree_dirty(checkout):
                raise RuntimeError(
                    "series/master closeout cannot create code, memory, or ledger commits on "
                    "its integration worktree; land all content through closed leaves first"
                )


def exact_series_memory_closeout(
    contract: WorktreeContract, code_commit: str
) -> MemoryCloseoutOutcome:
    """Read the exact atomic memory ref and prove its ledger maps the code ref."""

    ledger_commit, mapping = _series_ledger_mapping(contract, code_commit)
    if mapping is None:
        raise RuntimeError(
            "series/master closeout requires its existing ledger head to map the exact "
            "series code commit; integration branches are not closeout workbenches"
        )
    _require_series_memory_reachable(contract, mapping.memory_commit, ledger_commit)
    return MemoryCloseoutOutcome(
        memory_commit=mapping.memory_commit,
        ledger_commit=ledger_commit,
    )


def series_memory_closeout(contract: WorktreeContract, code_commit: str) -> MemoryCloseoutOutcome:
    """Read the exact atomic memory ref and prove the code/memory pair this closeout records.

    A master that reconciled with a sibling through ``worktree_sync`` cannot name its final code
    tip in the ledger, and no closeout could have: the sync creates the code merge commit *after*
    the retained memory conflict is resolved, so the row naming it cannot exist when the table is
    written. The pair is still provable, from the same two facts integration later recomputes:

    * the master's own line stays mapped -- the chain tip the reconciliation carries is still a row
      of the landed table, with memory content reachable from the ledger commit; and
    * the landed table is the exact projection of its source plus this branch's own true rows, so
      no source row was dropped, reordered, replaced or superseded and no untrue row survived.

    The recorded pair is then the reconciled tip against the memory ref the same sync landed. The
    ordinary shape -- the ledger already maps the exact code tip -- is unchanged and is still read
    exactly as before.
    """

    ledger_commit, mapping = _series_ledger_mapping(contract, code_commit)
    if mapping is not None:
        _require_series_memory_reachable(contract, mapping.memory_commit, ledger_commit)
        return MemoryCloseoutOutcome(
            memory_commit=mapping.memory_commit,
            ledger_commit=ledger_commit,
        )
    chain_tip = _exact_atomic_landing_chain(contract)[-1].integrated_code_commit
    if code_commit == chain_tip:
        raise RuntimeError(
            "series/master closeout requires its existing ledger head to map the exact "
            "series code commit; integration branches are not closeout workbenches"
        )
    chain_mapping = find_mapping(_series_ledger(contract, ledger_commit), chain_tip)
    if chain_mapping is None:
        raise RuntimeError(
            f"reconciled series closeout requires the ledger to still map the master's own chain "
            f"tip {chain_tip}; the reconciliation dropped the master's own landing"
        )
    _require_series_memory_reachable(contract, chain_mapping.memory_commit, ledger_commit)
    _require_series_ledger_projection(contract)
    return MemoryCloseoutOutcome(memory_commit=ledger_commit, ledger_commit=ledger_commit)


def _series_ledger_mapping(
    contract: WorktreeContract, code_commit: str
) -> tuple[str, LedgerRow | None]:
    """The exact memory ref and the row (if any) the landed ledger gives this code commit."""

    if contract.memory_repo_path is None:
        raise RuntimeError("external-memory series closeout requires a memory repository")
    ledger_commit = branch_commit(contract.memory_repo_path, contract.memory_work_branch)
    return ledger_commit, find_mapping(_series_ledger(contract, ledger_commit), code_commit)


def _series_ledger(contract: WorktreeContract, ledger_commit: str):
    assert contract.memory_repo_path is not None
    return parse_ledger_text(
        require_git(contract.memory_repo_path, ["show", f"{ledger_commit}:memory.md"])
    )


def _require_series_memory_reachable(
    contract: WorktreeContract, memory_commit: str, ledger_commit: str
) -> None:
    assert contract.memory_repo_path is not None
    if not is_ancestor(contract.memory_repo_path, memory_commit, ledger_commit):
        raise RuntimeError(
            "series/master closeout ledger maps memory content that is not reachable "
            "from the exact series memory head"
        )


def _require_series_ledger_projection(contract: WorktreeContract) -> None:
    """Require the landed table to be the projection of its source plus this line's own rows."""

    try:
        projection = contract_ledger_projection(contract)
    except LedgerProjectionRefusal as error:
        raise RuntimeError(
            "reconciled series closeout requires the exact atomic memory ref to be the projection "
            f"of its own source: {error}"
        ) from error
    if not projection.is_fixed_point:
        raise RuntimeError(
            "reconciled series closeout requires the landed memory table to be the projection of "
            "its source plus this master's own true rows; the landed table diverges from it, so a "
            "source row was dropped, reordered or replaced. Reconcile memory.md on the memory work "
            "branch from its source and retry."
        )
