"""The mandatory invariant closeout gate (MIK-R09@v2): recompute, decide, validate.

No route that commits a leaf's memory may commit while a worklist item is open, while the worklist
run is ``incomplete``, or while the validator (MIK-R22) fails. :func:`judge` turns one recomputed
worklist and the exact candidate trees into the gate's findings:

* every item without a current satisfying row is **one** finding naming the item ID, kind, subject,
  facts and the required action -- decided by its kind's own predicate (:mod:`.predicates`);
* an ``incomplete`` run is one finding per input it could not read;
* every refusing validator violation is one finding. The validator's comparison base is the parent
  line's memory tip, so every record the leaf introduced -- in however many commits -- is judged new
  by the admission rule (MIK-R27, carried from L27).

The findings are repair findings: the curator's memory-quality run counts them toward
``curatorActionableCount``, and every commit route refuses while any exists. None is report-only,
and there is no waiver (rule 5).

**Always recomputed.** The gate never reads a persisted worklist or trusts its digest: a worklist
written by another build (L11 adds planning marks) would not match, so each evaluation recomputes
from the four sides and persists what it computed (carried from L11). A Git call that fails or times
out makes the run ``incomplete``, naming ``git``, never an unhandled error (carried from L03), and an
unreadable leaf task document does the same (carried from L11).

**Applicability.** A leaf whose K_B and K_C both lack the layout marker gets no gate at all
(``None``): it closes out exactly as before this master (MIK-R09 rule 6, MIK-R37). The refusal of
unconverted trees that names the crossing sync (MIK-R24 rule 9) is not wired here; it belongs to the
cutover's installed build (see the L09 report).
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

from agents_remember.application.knowledge_currentness.observe import CodeTree, open_code_tree
from agents_remember.application.knowledge_gate import memo
from agents_remember.application.knowledge_gate.predicates import (
    INVARIANT_KINDS,
    GateContext,
    GitReadFailed,
    item_open_reason,
)
from agents_remember.application.knowledge_worklist.base_cache import (
    converted_base_files,
    default_base_cache_directory,
)
from agents_remember.application.knowledge_worklist.compute import (
    Incomplete,
    git_failure,
    incomplete_worklist,
)
from agents_remember.application.knowledge_worklist.knowledge import (
    KnowledgeSide,
    KnowledgeSideUnreadable,
)
from agents_remember.application.knowledge_worklist.leaf import (
    CandidateTrees,
    leaf_gate_applies,
    leaf_worklist,
    persist_worklist,
    worklist_path,
)
from agents_remember.application.knowledge_worklist.registry import ITEM_KINDS
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.kernel.recorded_reads import recorded_reads
from agents_remember.memory.conversion.base import pinned_version
from agents_remember.memory.knowledge_index import MemoryTreeError, git_tree_snapshot
from agents_remember.memory_quality.knowledge_validator import (
    KnowledgeTree,
    validate_tree,
    validation_applies,
)
from agents_remember.memory_quality.knowledge_validator.trees import (
    code_tree_from_git,
    knowledge_tree_from_git,
)
from agents_remember.memory_quality.knowledge_worklist_section import item_facts
from agents_remember.models.knowledge_files.documents import history_path, owner_history_attempt
from agents_remember.models.knowledge_files.history import is_closed_history, writable_attempt
from agents_remember.worktrees.knowledge_gate import parent_memory_tip as contract_parent_memory_tip
from agents_remember.worktrees.knowledge_validation import LayoutProbeError
from agents_remember.worktrees.worktree_contract import WorktreeContract

__all__ = [
    "GATE_CHECK",
    "ITEM_OPEN",
    "RUN_INCOMPLETE",
    "VALIDATOR",
    "GateFinding",
    "GateResult",
    "GateTrees",
    "evaluate_leaf_gate",
    "judge",
    "recompute_for_gate",
    "validation_findings",
]

GATE_CHECK: Final = "knowledge-gate"
ITEM_OPEN: Final = "knowledge-item-open"
RUN_INCOMPLETE: Final = "knowledge-worklist-incomplete"
VALIDATOR: Final = "knowledge-validator"
_REFUSAL_LINES: Final = 40


@dataclass(frozen=True)
class GateFinding:
    """One repair finding of the gate: an open item, an unreadable input or a violation."""

    code: str
    path: str
    message: str
    item: str | None = None
    """The open item's ID, for an open-item finding."""

    def to_repair_finding(self) -> dict[str, str]:
        """The finding as the curator checklist's repairable row (check ``knowledge-gate``)."""

        finding = {
            "check": GATE_CHECK,
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }
        if self.item is not None:
            finding["itemId"] = self.item
        return finding


@dataclass(frozen=True)
class GateTrees:
    """The exact candidate the gate judges, and what its validator compares it with.

    ``code_tree`` (C) and ``memory_tree`` (K_C) are Git trees already in their repositories' object
    stores. ``validation_bases`` are the validator's comparison bases: the parent line's memory tip
    at a leaf route (so every record the leaf introduced is new), each parent at a merge. An
    unconverted base is converted at its own trailer's code commit, or at ``base_code_commit`` (B)
    when it names none.
    """

    code_repository: Path
    code_tree: str
    memory_repository: Path
    memory_tree: str
    validation_bases: tuple[str, ...]
    base_code_commit: str
    cache_directory: Path | None = None


@dataclass(frozen=True)
class GateResult:
    """The gate's verdict over one exact candidate: its worklist and every finding."""

    owner: str | None
    worklist: Mapping[str, Any]
    worklist_path: str | None
    findings: tuple[GateFinding, ...]
    reports: tuple[GateFinding, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def memoisable(self) -> bool:
        """A verdict over complete inputs: nothing unreadable, no failed run (the memo's rule)."""

        if self.worklist.get("state") != "complete":
            return False
        return not any(
            finding.code in (RUN_INCOMPLETE, f"{VALIDATOR}-unreadable") for finding in self.findings
        )

    def count(self, code: str) -> int:
        return sum(1 for finding in self.findings if finding.code == code)

    def brief(self) -> dict[str, Any]:
        """The compact wire summary a route carries (the tool response's ``knowledgeGate``)."""

        return {
            "state": "pass" if self.ok else "refused",
            "owner": self.owner,
            "worklistState": self.worklist.get("state"),
            "worklistDigest": self.worklist.get("digest"),
            "worklistPath": self.worklist_path,
            "findingCount": len(self.findings),
            "openItemCount": self.count(ITEM_OPEN),
            "incompleteCount": self.count(RUN_INCOMPLETE),
            "violationCount": sum(
                1 for finding in self.findings if finding.code.startswith(VALIDATOR)
            ),
            "reportOnlyCount": len(self.reports),
        }

    def refusal(self) -> str | None:
        """Every finding as one refusal text, or ``None`` when the gate passes."""

        if self.ok:
            return None
        lines = [
            f"the mandatory invariant gate (MIK-R09) refuses: {len(self.findings)} finding(s) "
            f"for {self.owner or 'this leaf'}; each needs an authored row, a readable input or a "
            "valid tree (no waiver exists)"
        ]
        shown = self.findings[:_REFUSAL_LINES]
        lines += [
            f"- {finding.path or '-'}: [{finding.code}] {finding.message}" for finding in shown
        ]
        if len(self.findings) > len(shown):
            lines.append(f"- ... and {len(self.findings) - len(shown)} more")
        return "\n".join(lines)


def recompute_for_gate(
    contract: WorktreeContract, candidate: CandidateTrees
) -> dict[str, Any] | None:
    """The leaf's worklist recomputed over the exact candidate; ``None`` where none applies.

    Never raises: a Git failure is the ``incomplete`` worklist naming ``git``, and any other failure
    the one naming the run (MIK-R08 rule 4). Nothing persisted earlier is read.
    """

    owner = contract.leaf_id or None
    try:
        return leaf_worklist(contract, persist=False, candidate=candidate)
    except subprocess.SubprocessError as error:
        return incomplete_worklist(git_failure(error), owner=owner, pairing=None)
    except Exception as error:  # the run's own failure blocks, named; it is never a pass
        return incomplete_worklist(
            Incomplete("worklist run", f"{type(error).__name__}: {error}"),
            owner=owner,
            pairing=None,
        )


def evaluate_leaf_gate(
    contract: WorktreeContract, candidate: CandidateTrees, *, parent_memory_tip: str | None = None
) -> GateResult | None:
    """The gate over a leaf's exact closeout candidate; ``None`` when neither side is converted.

    ``parent_memory_tip`` is the parent line's memory tip, the validator's comparison base; when it
    is not given it is read from the contract, and only once the gate is known to apply, so an
    unconverted leaf reads nothing more than before.
    """

    if not _applies(contract, candidate):
        return None  # the worklist's own cheap probe: nothing converted, nothing more is read
    unreadable: list[GateFinding] = []
    if parent_memory_tip is None:
        parent_memory_tip, unreadable = _resolved_tip(contract)
    key = None if unreadable else memo.memo_key(contract, candidate, parent_memory_tip)
    kept = None if key is None else memo.remembered(key)
    if kept is not None:
        _persist(contract, kept.worklist)  # the latest worklist beside the contract stays this one
        return kept
    # Every requirement file the evaluation reads for approval state is recorded with the verdict.
    with recorded_reads() as reads:
        result = _evaluate(contract, candidate, parent_memory_tip, unreadable)
    if key is not None and result is not None:
        memo.remember(key, result, reads)
    return result


def _applies(contract: WorktreeContract, candidate: CandidateTrees) -> bool:
    if contract.kind != "leaf" or contract.memory_worktree is None:
        return False
    try:
        return leaf_gate_applies(contract, candidate)
    except (subprocess.SubprocessError, LayoutProbeError):
        return True  # a probe Git cannot answer is never taken for unconverted memory


def _resolved_tip(contract: WorktreeContract) -> tuple[str | None, list[GateFinding]]:
    """The parent line's memory tip read from the contract, or the finding that it cannot be."""

    try:
        return contract_parent_memory_tip(contract), []
    except (RuntimeError, subprocess.SubprocessError) as error:
        return None, [_unreadable("parent memory line", error)]


def _persist(contract: WorktreeContract, document: Mapping[str, Any]) -> str | None:
    path = worklist_path(contract)
    if path is None:
        return None
    try:
        persist_worklist(path, dict(document))
    except OSError:
        return None  # the verdict never depends on where the list could be written
    return path.as_posix()


def _evaluate(
    contract: WorktreeContract,
    candidate: CandidateTrees,
    parent_memory_tip: str | None,
    unreadable: list[GateFinding],
) -> GateResult | None:
    memory_repository = contract.memory_repo_path or contract.memory_worktree
    document = recompute_for_gate(contract, candidate)
    if document is None or memory_repository is None:
        return None
    path = _persist(contract, document)
    trees = GateTrees(
        code_repository=contract.code_repo_path,
        code_tree=candidate.code,
        memory_repository=memory_repository,
        memory_tree=candidate.memory,
        validation_bases=() if parent_memory_tip is None else (parent_memory_tip,),
        base_code_commit=contract.code_base_commit,
        cache_directory=default_base_cache_directory(contract.coordination_root),
    )
    result = judge(document, path, trees, contract.leaf_id or None)
    return replace(result, findings=(*unreadable, *result.findings)) if unreadable else result


def judge(
    document: Mapping[str, Any], path: str | None, trees: GateTrees, owner: str | None
) -> GateResult:
    """The gate's findings over one recomputed worklist and its exact candidate trees."""

    findings: list[GateFinding] = []
    if document.get("state") != "complete":
        findings += [
            GateFinding(
                RUN_INCOMPLETE,
                str(one.get("input")),
                f"the worklist run is incomplete: {one.get('input')} cannot be read "
                f"({one.get('detail')}); the gate blocks until it can",
            )
            for one in document.get("incomplete") or ({"input": "worklist", "detail": "-"},)
        ]
    else:
        findings += _item_findings(document.get("items") or (), trees, owner)
    refusing, reports = validation_findings(trees)
    return GateResult(
        owner=owner,
        worklist=document,
        worklist_path=path,
        findings=tuple([*findings, *refusing]),
        reports=tuple(reports),
    )


def _candidate(trees: GateTrees, owner: str | None) -> GateContext | GateFinding:
    """K_C and C read once for every predicate, or the finding that says which cannot be read."""

    try:
        snapshot = git_tree_snapshot(trees.memory_repository, trees.memory_tree)
        candidate = KnowledgeSide.from_snapshot("K_C", snapshot)
    except (KnowledgeSideUnreadable, MemoryTreeError, OSError, ValueError) as error:
        return _unreadable("K_C", error)
    except subprocess.SubprocessError as error:
        return _unreadable("git", error)
    code = open_code_tree(CodeTree(trees.code_repository, trees.code_tree))
    if code.problem is not None:
        return GateFinding(RUN_INCOMPLETE, "C", f"the code tree C cannot be read: {code.problem}")
    return GateContext(
        owner=owner,
        history=candidate.history(owner) if owner else None,
        candidate=candidate,
        code=code,
    )


def _item_findings(
    items: Sequence[Mapping[str, Any]], trees: GateTrees, owner: str | None
) -> list[GateFinding]:
    context = _candidate(trees, owner)
    if isinstance(context, GateFinding):
        return [context]
    where = _writable_history(context, owner, trees) if owner else "knowledge/history"
    try:
        decided = _decided(items, context, where)
    except GitReadFailed as error:  # never decided on a read that did not happen (never memoised)
        return [_unreadable("git", error)]
    findings: list[GateFinding] = []
    if not owner:
        findings.append(
            GateFinding(
                ITEM_OPEN,
                where,
                "the leaf names no leaf id, so it has no history file whose rows could answer "
                "its items (MIK-R07 rule 1)",
            )
        )
    return findings + decided


def _writable_history(context: GateContext, owner: str, trees: GateTrees) -> str:
    """The history file a row answering this leaf's items goes to, as the writer chooses it.

    An attempt is frozen once it is closed in the comparison base (the parent line's memory tip,
    which holds a reopened leaf's closed attempts), exactly as the writer reads its base; an attempt
    only the candidate closes -- this closeout's own -- is still the one the rows go to. So the
    message names a reopened leaf's latest attempt file, and a leaf never reopened its one file.
    Without a comparison base, the candidate's own flags stand in.
    """

    closed = {
        attempt: history.closed
        if not trees.validation_bases
        else _closed_in_bases(trees, history_path(owner, attempt))
        for path, history in context.candidate.parsed.history_files.items()
        if (attempt := owner_history_attempt(path, owner)) is not None
    }
    return history_path(owner, writable_attempt(closed))


def _closed_in_bases(trees: GateTrees, path: str) -> bool:
    """Whether a comparison base holds ``path`` as a closed history file (unreadable: not closed;
    the answer only names a file in a message)."""

    for base in trees.validation_bases:
        try:
            shown = run_git(
                trees.memory_repository,
                ["cat-file", "blob", f"{base}:{path}"],
                GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if shown.returncode == 0 and is_closed_history(shown.stdout.encode("utf-8")):
            return True
    return False


def _decided(
    items: Sequence[Mapping[str, Any]], context: GateContext, where: str
) -> list[GateFinding]:
    """Each item through its kind's predicate: the invariant kinds first, for the family rule."""

    invariant_reasons = {
        index: reason
        for index, item in enumerate(items)
        if item.get("kind") in INVARIANT_KINDS
        and (reason := item_open_reason(item, context)) is not None
    }
    context = replace(
        context,
        open_invariants=frozenset(str(items[index].get("subject")) for index in invariant_reasons),
    )
    reasons = [
        invariant_reasons.get(index)
        if item.get("kind") in INVARIANT_KINDS
        else item_open_reason(item, context)
        for index, item in enumerate(items)
    ]
    return [
        GateFinding(ITEM_OPEN, where, _open_message(item, reason), str(item.get("id")))
        for item, reason in zip(items, reasons, strict=True)
        if reason is not None
    ]


def _open_message(item: Mapping[str, Any], reason: str) -> str:
    kind = str(item.get("kind"))
    registered = ITEM_KINDS.get(kind)
    required = registered.satisfying_row if registered is not None else "a registered kind"
    return (
        f"{kind} {item.get('subject')} (item {item.get('id')}) is open: {reason}. "
        f"Required: {required}. Facts: {item_facts(item)}"
    )


def _unreadable(name: str, error: BaseException) -> GateFinding:
    return GateFinding(
        RUN_INCOMPLETE,
        name,
        f"the gate cannot read {name} ({type(error).__name__}: {error}); it blocks until it can",
    )


def validation_findings(trees: GateTrees) -> tuple[list[GateFinding], list[GateFinding]]:
    """The validator (MIK-R22) over K_C against the parent line's tip: refusing and report-only.

    Every judged candidate publishes a leaf (closeout or direct landing), so the history-row rule
    reads every file not closed in the base (``leaf_publication``). A Git call that fails or times
    out makes the run incomplete (``git``), never a validity verdict.
    """

    try:
        candidate = knowledge_tree_from_git(
            trees.memory_repository, trees.memory_tree, label=f"K_C {trees.memory_tree}"
        )
        bases = [_base(trees, base, candidate) for base in trees.validation_bases]
        if not validation_applies(candidate, bases):
            return [], []
        code = code_tree_from_git(
            trees.code_repository, trees.code_tree, label=f"C {trees.code_tree}"
        )
        report = validate_tree(candidate, bases=bases, code=code, leaf_publication=True)
    except subprocess.SubprocessError as error:  # a failed or timed-out Git call: incomplete
        return [_unreadable("git", error)], []
    except (OSError, ValueError) as error:
        return [
            GateFinding(
                f"{VALIDATOR}-unreadable",
                "",
                "the knowledge validator (MIK-R22) cannot read the candidate's trees "
                f"({type(error).__name__}: {error}); a tree that cannot be read is never committed",
            )
        ], []
    refusing = [
        GateFinding(VALIDATOR, violation.path, str(violation))
        for violation in report.violations
        if not violation.report_only
    ]
    reports = [
        GateFinding(VALIDATOR, violation.path, str(violation))
        for violation in report.violations
        if violation.report_only
    ]
    return refusing, reports


def _base(trees: GateTrees, commit: str, candidate: KnowledgeTree) -> KnowledgeTree:
    """One comparison base, replaced by its conversion when it is unconverted (MIK-R24 rule 7)."""

    base = knowledge_tree_from_git(trees.memory_repository, commit, label=f"memory base {commit}")
    if base.converted or not candidate.converted:
        return base
    version = pinned_version(candidate)
    if version is None:
        raise ValueError("K_C holds the layout marker without a pinned conversion-format version")
    files = converted_base_files(
        trees.memory_repository,
        commit,
        code=(trees.code_repository, trees.base_code_commit),
        version=version,
        cache_directory=trees.cache_directory,
    )
    return KnowledgeTree(label=f"converted:{commit}", files=files)
