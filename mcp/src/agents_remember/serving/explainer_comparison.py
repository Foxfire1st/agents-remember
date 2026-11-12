"""Comparison selection and exact pre-build witnesses for the explainer."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from agents_remember.canvas.explainer_comparison_inputs import ComparisonWitness
from agents_remember.canvas.explainer_paths import (
    repository_path,
)
from agents_remember.canvas.explainer_service import (
    BoundComparison,
    ExplainerRefused,
    LeafFacts,
    LeafScope,
)
from agents_remember.canvas.explainer_state import PlannedIdentity
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.recorded_reads import (
    changed_observations,
    file_identity,
)
from agents_remember.models.knowledge.review_frame import (
    COMPARISON_OWNER,
    PLANNED_OWNER,
    FrameBinding,
    comparison_reference,
    planned_reference,
)
from agents_remember.models.knowledge.review_trees import (
    ReviewKnowledgeSide,
    ReviewTreeComparisonRecord,
    ReviewTrees,
    ReviewTreesResult,
    TreeKnowledge,
)
from agents_remember.serving.review_trees import ReviewTreesQuery
from agents_remember.tasks.planned_comparison import (
    master_is_organizational,
    planned_comparison,
    resolve_master_contract,
)
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

if TYPE_CHECKING:
    from agents_remember.serving.explainer import ExplainerApplicationPorts


class PreparedComparisonResult(Protocol):
    @property
    def result(self) -> ReviewTreesResult: ...

    @property
    def native(self) -> tuple[object, ReviewTrees]: ...

    @property
    def witness(self) -> ComparisonWitness: ...


type ComparisonPreparationPort = Callable[
    [ReviewTreesQuery], PreparedComparisonResult | ReviewTreesResult
]


@dataclass(frozen=True)
class _SendRequest:
    message: str
    request_id: str
    comparison_number: int | None = None
    history: str | None = None
    planned: bool = False


class _ComparisonMoved(ExplainerRefused):
    def __init__(self, *, requested: int, current: int | None) -> None:
        self.requested = requested
        self.current = current
        current_label = f"#{current}" if current is not None else "new state"
        super().__init__(
            "comparison-moved",
            f"requested comparison #{requested}, but the leaf's current comparison is "
            f"{current_label}; refresh the form and Send again",
        )


_SEND_SELECTION: ContextVar[_SendRequest | None] = ContextVar("_SEND_SELECTION", default=None)


def _is_closed_leaf(
    config: McpRuntimeConfig, scope: LeafScope, application: ExplainerApplicationPorts
) -> bool:
    contract = application.recorded_leaf_contract(
        config, scope.repository_id, scope.task, scope.leaf_id
    )
    if contract is None:
        return False
    worktree = contract.code_worktree
    return worktree is None or not worktree.exists()


def _check_requested_number(
    port: ComparisonPreparationPort,
    scope: LeafScope,
    result: ReviewTreesResult,
    *,
    requested: int,
    recorded: bool,
) -> None:
    if result.state == "trees" and result.comparison is not None:
        current = result.latest if result.latest is not None else result.comparison.number
        if result.comparison.number != requested or current != requested:
            raise _ComparisonMoved(requested=requested, current=current)
        return
    prepared_current = port(
        ReviewTreesQuery(
            repository_id=scope.repository_id,
            master=scope.task,
            leaf_id=scope.leaf_id,
            number=None,
            recorded=recorded,
        )
    )
    current_res = (
        prepared_current
        if isinstance(prepared_current, ReviewTreesResult)
        else prepared_current.result
    )
    if current_res.state == "trees" and current_res.comparison is not None:
        current = (
            current_res.latest if current_res.latest is not None else current_res.comparison.number
        )
        if current != requested:
            raise _ComparisonMoved(requested=requested, current=current)


def _recorded(
    config: McpRuntimeConfig,
    port: ComparisonPreparationPort,
    scope: LeafScope,
    application: ExplainerApplicationPorts,
    *,
    selection: _SendRequest | None = None,
) -> tuple[ReviewTreeComparisonRecord, ReviewTrees, ComparisonWitness]:
    history = None if selection is None else selection.history
    requested = None if selection is None else selection.comparison_number
    recorded = (
        history == "recorded"
        if history is not None
        else _is_closed_leaf(config, scope, application)
    )
    prepared = port(
        ReviewTreesQuery(
            repository_id=scope.repository_id,
            master=scope.task,
            leaf_id=scope.leaf_id,
            number=requested,
            recorded=recorded,
        )
    )
    result = prepared if isinstance(prepared, ReviewTreesResult) else prepared.result
    if requested is not None:
        _check_requested_number(port, scope, result, requested=requested, recorded=recorded)
    if result.state != "trees" or result.comparison is None:
        detail = None if result.refusal is None else result.refusal.detail
        raise ExplainerRefused(
            "evidence-source-unavailable",
            f"the comparison owner answered {result.state!r}: {detail or 'no tree comparison'}",
        )
    if isinstance(prepared, ReviewTreesResult):
        raise ExplainerRefused(
            "evidence-source-unavailable", "comparison success has no native input witness"
        )
    return result.comparison, prepared.native[1], prepared.witness


def _planned(
    config: McpRuntimeConfig,
    contract: Path,
    scope: LeafScope,
    application: ExplainerApplicationPorts,
) -> tuple[ReviewTreeComparisonRecord, ReviewTrees]:
    """The planned comparison at the master line's two tip commits (MIK-R186 rule 4, MIK-R189 rule 4).

    The line comes from the canonical series contract reader and the master's canonical execution
    nature: an atomic master's line is its integration (work) branch, which previously landed leaves
    advanced, and an organizational master's is the sprint integration branch the contract records
    as its source branch. The line's own tip wins when it diverges from the fork base; the base
    engages only while the line names no commit at all, and nothing substitutes the repository's
    HEAD.
    """

    code_repository = repository_path(config, scope.repository_id)
    memory_repository = repository_path(config, scope.repository_id, memory=True)
    organizational = master_is_organizational(
        config.coordination_root, scope.repository_id, scope.task
    )
    try:
        parsed = load_contract(contract)
    except (ContractError, OSError) as error:
        raise ExplainerRefused(
            "planned-comparison-unavailable",
            f"the master line's series contract cannot be read: {error}",
        ) from error
    code_commit = _line_tip_commit(
        code_repository, *_master_line_branches(parsed, organizational, memory=False)
    )
    if code_commit is None:
        raise ExplainerRefused(
            "evidence-source-unavailable",
            f"the master line's code branches name no commit in {code_repository}",
        )
    memory_commit = _line_tip_commit(
        memory_repository, *_master_line_branches(parsed, organizational, memory=True)
    )
    if memory_commit is None:
        raise ExplainerRefused(
            "evidence-source-unavailable",
            f"the master line's memory branches name no commit in {memory_repository}",
        )
    record = planned_comparison(
        task_id=scope.task,
        leaf_id=scope.leaf_id,
        code_repository=str(code_repository),
        code_commit=code_commit,
        code_tree=_rev_parse(code_repository, f"{code_commit}^{{tree}}"),
        memory_repository=str(memory_repository),
        memory_commit=memory_commit,
        memory_tree=_rev_parse(memory_repository, f"{memory_commit}^{{tree}}"),
    )
    missing_memory = application.missing_tree(memory_repository, record.memory_candidate.tree)
    sides = tuple(
        ReviewKnowledgeSide(
            side=side,
            state="available" if missing_memory is None else "unavailable-history",
            tree=record.memory_candidate.tree,
            index_state="complete" if missing_memory is None else None,
            detail=missing_memory,
        )
        for side in ("before", "after")
    )
    trees = ReviewTrees(
        record,
        None,
        memory_repository,
        TreeKnowledge(sides[0], None),
        TreeKnowledge(sides[1], None),
        code_sides=application.code_sides(record),
    )
    return record, trees


def _rev_parse(repository: Path, ref: str) -> str:
    result = run_git(repository, ["rev-parse", ref])
    if result.returncode != 0:
        raise ExplainerRefused(
            "evidence-source-unavailable",
            f"the master line's {ref!r} cannot be read in {repository}: {result.stderr.strip()}",
        )
    return result.stdout.strip()


def _master_line_branches(
    parsed: WorktreeContract, organizational: bool, *, memory: bool
) -> tuple[str, str]:
    """One side's master-line branch and its fork base, from the canonical contract.

    An atomic master's line is the work branch (the integration line), and an organizational
    master's is the source branch (the sprint's integration branch), by the same separation the
    worktree lifecycle writes and the landed net comparison reads.
    """

    if memory:
        if organizational:
            return parsed.memory_source_branch, parsed.memory_work_branch
        return parsed.memory_work_branch, parsed.memory_source_branch
    if organizational:
        return parsed.code_source_branch, parsed.code_work_branch
    return parsed.code_work_branch, parsed.code_source_branch


def _line_tip_commit(repository: Path, line: str, base: str) -> str | None:
    """The named line's tip commit, or the fork base while the line names no commit at all.

    The first name that resolves wins: a line that exists is followed even when the base has
    advanced past it, and the base is read only for a master whose line holds nothing yet (an
    atomic master before its first landing). No branch name resolves, so the repository's own HEAD
    is never substituted for the contract's line.
    """

    for branch in (line, base):
        if not branch:
            continue
        try:
            result = run_git(repository, ["rev-parse", "--verify", "--quiet", branch])
        except (OSError, RuntimeError):
            continue
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return None


def _resolve_contract_for(
    config: McpRuntimeConfig,
    scope: LeafScope,
    contract_for: Callable[[LeafScope], Path | None] | None,
) -> Path | None:
    if contract_for is not None:
        return contract_for(scope)
    return resolve_master_contract(config.coordination_root, scope.repository_id, scope.task)


def _comparison(
    config: McpRuntimeConfig,
    port: ComparisonPreparationPort,
    contract_for: Callable[[LeafScope], Path | None] | None,
    application: ExplainerApplicationPorts,
) -> Callable[[LeafScope, LeafFacts], BoundComparison]:
    def read(scope: LeafScope, facts: LeafFacts) -> BoundComparison:
        selection = _SEND_SELECTION.get()
        if selection is not None and selection.history == "planned" and not facts.planned:
            raise ExplainerRefused(
                "not-a-planning-leaf",
                f"leaf {scope.leaf_id} is not planning; its page binds a recorded comparison",
            )
        if facts.planned:
            if selection is not None and selection.history in {"live", "recorded"}:
                raise ExplainerRefused(
                    "comparison-unavailable",
                    f"leaf {scope.leaf_id} is planning and has no {selection.history} comparison",
                )
            contract = _resolve_contract_for(config, scope, contract_for)
            if contract is None:
                raise ExplainerRefused(
                    "planned-comparison-unavailable",
                    "a planning leaf binds the master line's tips, and no series contract was "
                    "supplied to read them from",
                )
            inputs = _planned_inputs(config, scope, contract)
            record, trees = _planned(config, contract, scope, application)
            if changed_observations(inputs):
                raise ExplainerRefused(
                    "binding-moved", "planned comparison inputs moved during preparation"
                )
            binding = FrameBinding.from_record(
                record,
                comparison_owner=PLANNED_OWNER,
                comparison_reference=planned_reference(scope.task, scope.leaf_id),
            )
            planned = PlannedIdentity(
                code_tip=record.code_candidate.commit or record.code_candidate.tree,
                memory_tip=record.memory_candidate.commit or record.memory_candidate.tree,
                code_tree=record.code_candidate.tree,
                memory_tree=record.memory_candidate.tree,
            )
            return BoundComparison(
                binding=binding,
                planned=planned,
                record_digest=None,
                record=trees,
                witness=_planned_witness(
                    _PlannedReader(config, contract_for, application, inputs),
                    scope,
                    contract,
                    trees,
                ),
            )
        record, trees, witness = _recorded(config, port, scope, application, selection=selection)
        binding = FrameBinding.from_record(
            record,
            comparison_owner=COMPARISON_OWNER,
            comparison_reference=comparison_reference(record),
        )
        record_bytes = record.model_dump_json(by_alias=True).encode("utf-8")
        digest = "sha256:" + hashlib.sha256(record_bytes).hexdigest()
        return BoundComparison(binding=binding, record_digest=digest, record=trees, witness=witness)

    return read


@dataclass(frozen=True)
class _PlannedWitness:
    reads: dict[str, str]
    check: Callable[[], bool]

    def current(self) -> bool:
        if changed_observations(self.reads):
            return False
        try:
            current = self.check()
        except ExplainerRefused:
            raise
        except (OSError, ValueError, RuntimeError):
            return False
        return current and not changed_observations(self.reads)


@dataclass(frozen=True)
class _PlannedReader:
    config: McpRuntimeConfig
    contract_for: Callable[[LeafScope], Path | None] | None
    application: ExplainerApplicationPorts
    reads: dict[str, str]


def _planned_inputs(config: McpRuntimeConfig, scope: LeafScope, contract: Path) -> dict[str, str]:
    task = config.coordination_root / "tasks" / scope.repository_id / scope.task / "task.json"
    return {str(contract): file_identity(contract), str(task): file_identity(task)}


def _planned_witness(
    reader: _PlannedReader, scope: LeafScope, contract: Path, trees: ReviewTrees
) -> _PlannedWitness:
    config, contract_for, application = reader.config, reader.contract_for, reader.application
    parsed = load_contract(contract)
    organizational = master_is_organizational(
        config.coordination_root, scope.repository_id, scope.task
    )
    code_branches = _master_line_branches(parsed, organizational, memory=False)
    memory_branches = _master_line_branches(parsed, organizational, memory=True)
    code_repository = repository_path(config, scope.repository_id)
    memory_repository = repository_path(config, scope.repository_id, memory=True)
    record = trees.record
    code_available = all(side.state == "available" for side in trees.code_sides)
    memory_available = trees.before.wire.state == "available"

    def check() -> bool:
        if _resolve_contract_for(config, scope, contract_for) != contract:
            return False
        # Preserve the original named refusal when a source disappears. A readable
        # different tip is movement; a missing repository/ref is unavailable evidence.
        code_tip = _required_tip(code_repository, code_branches, "code")
        memory_tip = _required_tip(memory_repository, memory_branches, "memory")
        if code_tip != record.code_candidate.commit or memory_tip != record.memory_candidate.commit:
            return False
        missing_code = application.missing_tree(code_repository, record.code_candidate.tree)
        missing_memory = application.missing_tree(memory_repository, record.memory_candidate.tree)
        if code_available and missing_code is not None:
            raise ExplainerRefused("evidence-source-unavailable", missing_code)
        if memory_available and missing_memory is not None:
            raise ExplainerRefused("evidence-source-unavailable", missing_memory)
        return (missing_code is None) == code_available and (
            missing_memory is None
        ) == memory_available

    return _PlannedWitness(reader.reads, check)


def _required_tip(repository: Path, branches: tuple[str, str], side: str) -> str:
    tip = _line_tip_commit(repository, *branches)
    if tip is None:
        raise ExplainerRefused(
            "evidence-source-unavailable",
            f"the master line's {side} branches name no commit in {repository}",
        )
    return tip
