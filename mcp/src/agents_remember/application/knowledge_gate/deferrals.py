"""Closeout gate checks for dependent deferrals (L52-R4, L52-R5).

Refuses closeout when:
* a leaf confirms a deferred dependent without editing its document;
* a deferral targets this leaf as its due task;
* a deferral's watched file changed within this leaf's own ``B..C`` diff.

Reports without blocking when:
* a deferral's target task is already terminal (orphaned);
* a deferral's watched file changed before ``B`` on another line (this leaf did not touch it).

A deferral is never due or orphaned in the leaf that created it.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from agents_remember.application.knowledge_currentness.observe import (
    CodeTree,
    OpenedCodeTree,
    open_code_tree,
)
from agents_remember.application.knowledge_gate.gate import (
    RUN_INCOMPLETE,
    GateFinding,
    GateTrees,
)
from agents_remember.application.knowledge_gate.predicates import GateContext
from agents_remember.application.knowledge_worklist.knowledge import (
    KnowledgeSide,
    KnowledgeSideUnreadable,
)
from agents_remember.application.review_source_inventory import tree_difference_observation
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.memory.knowledge.tree_observation import TreeSide
from agents_remember.memory.knowledge_index import MemoryTreeError, git_tree_snapshot
from agents_remember.models.knowledge_files.records import DependentDeferral, DependentRecord
from agents_remember.tasks.task_facts import TaskFact, load_task_facts

__all__ = [
    "CONFIRMED_DEFERRED_WITHOUT_EDIT",
    "DEFERRAL_DUE_FILE",
    "DEFERRAL_DUE_FILE_WARNING",
    "DEFERRAL_DUE_TASK",
    "DEFERRAL_ORPHANED",
    "deferral_gate_findings",
]

CONFIRMED_DEFERRED_WITHOUT_EDIT: Final = "confirmed-deferred-document-without-edit"
DEFERRAL_DUE_TASK: Final = "knowledge-deferral-due-task"
DEFERRAL_DUE_FILE: Final = "knowledge-deferral-due-file"
DEFERRAL_ORPHANED: Final = "knowledge-deferral-orphaned"
DEFERRAL_DUE_FILE_WARNING: Final = "knowledge-deferral-due-file-warning"


@dataclass(frozen=True)
class _DeferralCheckEnv:
    """Shared context for checking every open deferral of one closeout candidate."""

    owner: str | None
    changed_files: frozenset[str]
    candidate_blobs: Mapping[str, str]
    task_facts: Mapping[str, TaskFact]


def _resolve_tree_id(repository: Path, ref: str) -> str | None:
    try:
        result = run_git(
            repository,
            ["rev-parse", "--verify", "--quiet", f"{ref}^{{tree}}"],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    tree_id = result.stdout.strip()
    return tree_id if result.returncode == 0 and tree_id else None


def _changed_files(trees: GateTrees, candidate_files: Mapping[str, str]) -> frozenset[str] | None:
    """Every path added, modified, deleted or renamed between B and C, or ``None`` if unreadable."""

    if not trees.base_code_commit:
        return frozenset()
    root = str(trees.code_repository)
    base_tree_id = _resolve_tree_id(trees.code_repository, trees.base_code_commit)
    cand_tree_id = _resolve_tree_id(trees.code_repository, trees.code_tree)
    if base_tree_id is not None and cand_tree_id is not None:
        observed = tree_difference_observation(
            TreeSide(tree_id=base_tree_id, root=root),
            TreeSide(tree_id=cand_tree_id, root=root),
        )
        if observed.available and not observed.partial:
            return frozenset(entry.path for entry in observed.entries)
    base_opened = open_code_tree(CodeTree(trees.code_repository, trees.base_code_commit))
    if base_opened.problem is not None:
        return None
    base_files = base_opened.files
    return frozenset(
        path
        for path in base_files.keys() | candidate_files.keys()
        if base_files.get(path) != candidate_files.get(path)
    )


def _load_sides(
    trees: GateTrees, context: GateContext | None
) -> tuple[KnowledgeSide, OpenedCodeTree] | GateFinding:
    if context is not None:
        return context.candidate, context.code
    try:
        snapshot = git_tree_snapshot(trees.memory_repository, trees.memory_tree)
        candidate = KnowledgeSide.from_snapshot("K_C", snapshot)
    except (KnowledgeSideUnreadable, MemoryTreeError, OSError, ValueError) as error:
        return GateFinding(
            RUN_INCOMPLETE,
            "K_C",
            f"the gate cannot read K_C ({type(error).__name__}: {error}); it blocks until it can",
        )
    except subprocess.SubprocessError as error:
        return GateFinding(
            RUN_INCOMPLETE,
            "git",
            f"the gate cannot read git ({type(error).__name__}: {error}); it blocks until it can",
        )
    opened = open_code_tree(CodeTree(trees.code_repository, trees.code_tree))
    if opened.problem is not None:
        return GateFinding(RUN_INCOMPLETE, "C", f"the code tree C cannot be read: {opened.problem}")
    return candidate, opened


def _deferred_records(candidate: KnowledgeSide) -> list[tuple[str, DependentRecord]]:
    found: list[tuple[str, DependentRecord]] = []
    for record_id in sorted(candidate.parsed.records):
        indexed = candidate.parsed.records[record_id]
        if isinstance(indexed.record, DependentRecord) and indexed.record.deferrals:
            found.append((indexed.path, indexed.record))
    return found


def _invalid_confirmation_finding(path: str, record: DependentRecord, owner: str) -> GateFinding:
    details = "; ".join(
        f"{d.id} (owner={d.owner}, passage={d.passage!r}, reason={d.reason!r})"
        for d in record.deferrals or ()
    )
    return GateFinding(
        code=CONFIRMED_DEFERRED_WITHOUT_EDIT,
        path=path,
        message=(
            f"{record.id} ({record.document}) was confirmed by {owner} without editing "
            f"{record.document}, while open deferrals remain: {details}. "
            "Edit the document to resolve the deferred passages and confirm it, or re-defer or "
            "lift each open deferral explicitly through knowledge_write."
        ),
    )


def _check_one_deferral(
    path: str,
    record: DependentRecord,
    deferral: DependentDeferral,
    env: _DeferralCheckEnv,
) -> tuple[list[GateFinding], list[GateFinding]]:
    refusing: list[GateFinding] = []
    reports: list[GateFinding] = []
    if env.owner is not None and deferral.createdByLeaf == env.owner:
        return refusing, reports
    if deferral.condition.task is not None:
        fact = env.task_facts.get(deferral.condition.task)
        if fact is not None and fact.is_terminal:
            reports.append(
                GateFinding(
                    code=DEFERRAL_ORPHANED,
                    path=path,
                    message=(
                        f"{record.id} ({record.document}) deferral {deferral.id} is orphaned: "
                        f"target task {deferral.condition.task} is {fact.status} "
                        f"(owner={deferral.owner}, passage={deferral.passage!r}, "
                        f"reason={deferral.reason!r}). Re-defer to an active leaf or lift/resolve "
                        "through knowledge_write."
                    ),
                )
            )
    if env.owner is not None and deferral.condition.task == env.owner:
        refusing.append(
            GateFinding(
                code=DEFERRAL_DUE_TASK,
                path=path,
                message=(
                    f"{record.id} ({record.document}) deferral {deferral.id} is due in leaf "
                    f"{env.owner} (owner={deferral.owner}, passage={deferral.passage!r}, "
                    f"reason={deferral.reason!r}). Edit {record.document} and confirm the "
                    "dependent record, or re-defer or lift the deferral through knowledge_write."
                ),
            )
        )
        return refusing, reports
    watched = deferral.condition.file
    if watched is not None and env.candidate_blobs.get(watched) != deferral.fileBlob:
        observed = env.candidate_blobs.get(watched) or "absent"
        if watched in env.changed_files:
            refusing.append(
                GateFinding(
                    code=DEFERRAL_DUE_FILE,
                    path=path,
                    message=(
                        f"{record.id} ({record.document}) deferral {deferral.id} is due because "
                        f"this leaf changed watched file {watched} "
                        f"(recorded blob {deferral.fileBlob}, candidate blob {observed}; "
                        f"owner={deferral.owner}, passage={deferral.passage!r}, "
                        f"reason={deferral.reason!r}). Edit {record.document} and confirm the "
                        "dependent record, or re-defer or lift through knowledge_write."
                    ),
                )
            )
        else:
            reports.append(
                GateFinding(
                    code=DEFERRAL_DUE_FILE_WARNING,
                    path=path,
                    message=(
                        f"{record.id} ({record.document}) deferral {deferral.id} watched file "
                        f"{watched} changed before this leaf's base "
                        f"(recorded blob {deferral.fileBlob}, candidate blob {observed}; "
                        f"owner={deferral.owner}, passage={deferral.passage!r}, "
                        f"reason={deferral.reason!r}). This leaf did not change {watched}, so "
                        "closeout is not blocked."
                    ),
                )
            )
    return refusing, reports


def deferral_gate_findings(
    trees: GateTrees,
    owner: str | None,
    *,
    context: GateContext | None = None,
) -> tuple[list[GateFinding], list[GateFinding]]:
    """Return ``(refusing_findings, report_only_findings)`` for open dependent deferrals."""

    loaded = _load_sides(trees, context)
    if isinstance(loaded, GateFinding):
        return [loaded], []
    candidate, code = loaded
    deferred = _deferred_records(candidate)
    if not deferred:
        return [], []
    changed_files = _changed_files(trees, code.files)
    if changed_files is None:
        return [
            GateFinding(
                RUN_INCOMPLETE,
                "B/C",
                f"the gate cannot compute changed files between {trees.base_code_commit} and "
                f"{trees.code_tree} for deferral checks; it blocks until the trees can be read",
            )
        ], []
    needs_task_facts = any(
        d.condition.task is not None and (owner is None or d.createdByLeaf != owner)
        for _path, record in deferred
        for d in record.deferrals or ()
    )
    task_facts = (
        load_task_facts(trees.coordination_root, trees.repo_name) if needs_task_facts else {}
    )
    env = _DeferralCheckEnv(
        owner=owner,
        changed_files=changed_files,
        candidate_blobs=code.files,
        task_facts=task_facts,
    )
    refusing: list[GateFinding] = []
    reports: list[GateFinding] = []
    for path, record in deferred:
        if (
            owner is not None
            and record.confirmed.task == owner
            and record.document not in changed_files
        ):
            refusing.append(_invalid_confirmation_finding(path, record, owner))
        for deferral in record.deferrals or ():
            one_refusing, one_reports = _check_one_deferral(path, record, deferral, env)
            refusing.extend(one_refusing)
            reports.extend(one_reports)
    return refusing, reports
