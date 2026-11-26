"""Report-only dependent confirmation and deferral flags for memory-quality checks (L52-R6, L52-R7).

Emits non-blocking ``reportOnlyFindings`` entries:
* ``pre_base_stale_confirmation`` (Lever-1): a leaf confirmed an already-stale dependent without
  editing its document;
* ``deferral_lifted``: a leaf's history contains a ``deferral_lifted`` record row;
* ``deferral_orphaned``: an open deferral targets a terminal task;
* ``deferral_due_warning``: an open deferral's watched file changed before the leaf's base.

Nothing emitted here ever flips ``ok`` or ``findingCount``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
    run_git_bytes,
)
from agents_remember.kernel.memory_attribution import parse_code_commit_trailer
from agents_remember.memory_quality.knowledge_validator.parsed import (
    ParsedTree,
    RecordFile,
    parse_tree,
)
from agents_remember.memory_quality.knowledge_validator.trees import (
    CodePathSet,
    CodeTree,
    KnowledgeTree,
    KnowledgeTreeReadError,
    code_tree_from_git,
)
from agents_remember.memory_quality.reference_state import git_blob_id, located, refreshed_anchor
from agents_remember.models.knowledge_files.anchor_content import content_identity
from agents_remember.models.knowledge_files.history import HistoryFile, RecordRow
from agents_remember.models.knowledge_files.records import (
    DependentAnchorLink,
    DependentDeferral,
    DependentFileLink,
    DependentRecord,
    DependentRecordLink,
)
from agents_remember.tasks.task_facts import TaskFact, load_task_facts

__all__ = [
    "DEPENDENT_FLAGS_CHECK",
    "DependentFlagInputs",
    "DependentFlagScope",
    "collect_dependent_report_only_findings",
]

DEPENDENT_FLAGS_CHECK: Final = "dependent_flags"


@dataclass(frozen=True)
class DependentFlagScope:
    """Optional leaf/coordination context for dependent report-only quality flags."""

    owner: str | None = None
    code_base: str | None = None
    coordination_root: Path | None = None
    repo_name: str | None = None


@dataclass(frozen=True)
class DependentFlagInputs:
    """Trees and context evaluated by :func:`collect_dependent_report_only_findings`."""

    memory_root: Path
    candidate_tree: KnowledgeTree
    code_root: Path
    code: CodeTree
    base_tree: KnowledgeTree | None = None
    scope: DependentFlagScope | None = None


@dataclass(frozen=True)
class _WarningEnv:
    code: CodeTree
    owner: str | None
    changed_files: frozenset[str]
    task_facts: Mapping[str, TaskFact]


def _memory_head_code_commit(memory_root: Path) -> str | None:
    try:
        result = run_git(
            memory_root,
            ["log", "-1", "--format=%B", "--end-of-options", "HEAD"],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return parse_code_commit_trailer(result.stdout)


def _infer_owner(parsed: ParsedTree) -> str | None:
    open_leaves = sorted(
        {
            history.leaf
            for _path, history in parsed.histories
            if history.leaf is not None and not history.closed
        }
    )
    return open_leaves[0] if len(open_leaves) == 1 else None


def _changed_files_from_base(
    code_root: Path, code_base: str | None, base_code: CodePathSet | None
) -> frozenset[str]:
    if not code_base:
        return frozenset()
    changed: set[str] = set()
    try:
        diff = run_git_bytes(
            code_root,
            ["diff", "--name-only", "--no-renames", "-z", code_base],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
        if diff.returncode == 0:
            changed.update(
                path.decode("utf-8", errors="surrogateescape")
                for path in diff.stdout.split(b"\0")
                if path
            )
        untracked = run_git_bytes(
            code_root,
            ["ls-files", "--others", "--exclude-standard", "-z"],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
        if untracked.returncode == 0:
            changed.update(
                path.decode("utf-8", errors="surrogateescape")
                for path in untracked.stdout.split(b"\0")
                if path
            )
    except (OSError, subprocess.SubprocessError):
        if base_code is not None and base_code.blobs is not None:
            for path, blob in base_code.blobs.items():
                target = code_root / path
                if not target.is_file() or git_blob_id(target.read_bytes()) != blob:
                    changed.add(path)
    return frozenset(changed)


def _record_revisions(parsed: ParsedTree) -> dict[str, int]:
    revisions: dict[str, int] = {}
    for record_file in parsed.records:
        revision = getattr(record_file.record, "revision", None)
        if isinstance(revision, int):
            revisions[record_file.record.id] = revision
    return revisions


def _moved_record_link_at_base(
    position: int, link: DependentRecordLink, base_revisions: Mapping[str, int]
) -> dict[str, Any] | None:
    rev = base_revisions.get(link.id)
    if rev is None:
        return {
            "position": position,
            "kind": "record",
            "id": link.id,
            "state": "stale",
            "reason": f"the base tree holds no record {link.id}",
        }
    if rev != link.revision:
        return {
            "position": position,
            "kind": "record",
            "id": link.id,
            "state": "stale",
            "recordedRevision": link.revision,
            "revision": rev,
        }
    return None


def _moved_file_link_at_base(
    position: int, link: DependentFileLink, base_blobs: Mapping[str, str]
) -> dict[str, Any] | None:
    observed = base_blobs.get(link.path)
    if observed is None:
        return {
            "position": position,
            "kind": "file",
            "path": link.path,
            "state": "stale",
            "reason": "the file is gone",
        }
    if observed != link.blob:
        return {
            "position": position,
            "kind": "file",
            "path": link.path,
            "state": "stale",
            "recordedBlob": link.blob,
            "observedBlob": observed,
        }
    return None


def _moved_anchor_link_at_base(
    position: int, link: DependentAnchorLink, base_code: CodePathSet, base_blobs: Mapping[str, str]
) -> dict[str, Any] | None:
    anchor = link.anchor
    if anchor.path is None:
        return None
    observed_blob = base_blobs.get(anchor.path)
    if observed_blob is None:
        return {
            "position": position,
            "kind": "anchor",
            "path": anchor.path,
            "state": "stale",
            "reason": f"the path {anchor.path!r} is absent at the base code tree",
        }
    if observed_blob == anchor.blob:
        return None
    data = base_code.read(anchor.path)
    if data is None:
        return None
    anchor_doc = anchor.to_document()
    found = located(anchor.path, data, anchor_doc["locator"])
    if (found is not None and content_identity(found) == anchor.content) or (
        refreshed_anchor(anchor.path, anchor_doc, data) is not None
    ):
        return None
    return {
        "position": position,
        "kind": "anchor",
        "path": anchor.path,
        "state": "stale",
        "recordedContent": anchor.content,
        "observedContent": None if found is None else content_identity(found),
    }


def _moved_links_at_base(
    record: DependentRecord,
    base_revisions: Mapping[str, int],
    base_code: CodePathSet | None,
) -> list[dict[str, Any]]:
    """Observe which ``stands_on`` links of ``record`` had already moved at base ``B``."""

    moved: list[dict[str, Any]] = []
    base_blobs = None if base_code is None else base_code.blobs
    for position, link in enumerate(record.stands_on, start=1):
        entry: dict[str, Any] | None = None
        if isinstance(link, DependentRecordLink):
            entry = _moved_record_link_at_base(position, link, base_revisions)
        elif base_code is not None and base_blobs is not None:
            if isinstance(link, DependentFileLink):
                entry = _moved_file_link_at_base(position, link, base_blobs)
            else:
                entry = _moved_anchor_link_at_base(position, link, base_code, base_blobs)
        if entry is not None:
            moved.append(entry)
    return moved


def _pre_base_stale_confirmation_findings(
    candidate_dependents: Mapping[str, RecordFile],
    base_parsed: ParsedTree | None,
    base_code: CodePathSet | None,
    *,
    owner: str | None,
    changed_files: frozenset[str],
) -> list[dict[str, Any]]:
    if base_parsed is None:
        return []
    base_dependents = {
        rf.record.id: rf.record
        for rf in base_parsed.records
        if isinstance(rf.record, DependentRecord)
    }
    base_revisions = _record_revisions(base_parsed)
    findings: list[dict[str, Any]] = []
    for record_id in sorted(candidate_dependents):
        rf = candidate_dependents[record_id]
        record = rf.record
        assert isinstance(record, DependentRecord)
        base_record = base_dependents.get(record_id)
        if base_record is None:
            continue
        confirmed_by_leaf = (owner is not None and record.confirmed.task == owner) or (
            record.confirmed != base_record.confirmed
        )
        if not confirmed_by_leaf or record.document in changed_files:
            continue
        pre_base_moved = _moved_links_at_base(base_record, base_revisions, base_code)
        if not pre_base_moved:
            continue
        findings.append(
            {
                "check": DEPENDENT_FLAGS_CHECK,
                "kind": "pre_base_stale_confirmation",
                "path": rf.path,
                "recordId": record.id,
                "document": record.document,
                "confirmedBy": record.confirmed.task,
                "preBaseMovedLinks": pre_base_moved,
                "message": (
                    f"{record.id} ({record.document}) was confirmed by {record.confirmed.task} "
                    f"without editing {record.document}, while already stale at the leaf's base "
                    f"({len(pre_base_moved)} pre-base moved link(s)). A signed Memory Reviewer "
                    "verdict with base and candidate knowledge_read(view='dependent', "
                    "staleOnly=True) receipts is required."
                ),
            }
        )
    return findings


def _selected_leaf_histories(
    parsed: ParsedTree, base_tree: KnowledgeTree | None, owner: str | None
) -> list[tuple[str, HistoryFile]]:
    if owner is not None:
        owned = [(path, history) for path, history in parsed.histories if history.owner_id == owner]
        if not owned:
            return []
        return [max(owned, key=lambda item: item[1].attempt_number)]
    open_files = [(path, history) for path, history in parsed.histories if not history.closed]
    if open_files:
        return open_files
    if base_tree is None:
        return []
    return [(path, history) for path, history in parsed.histories if path not in base_tree.files]


def _lifted_deferral_findings(
    parsed: ParsedTree, base_tree: KnowledgeTree | None, owner: str | None
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for hist_path, history in _selected_leaf_histories(parsed, base_tree, owner):
        for row in history.rows:
            if not isinstance(row, RecordRow) or row.disposition != "deferral_lifted":
                continue
            deferral_ids = list(row.deferralIds or ())
            findings.append(
                {
                    "check": DEPENDENT_FLAGS_CHECK,
                    "kind": "deferral_lifted",
                    "path": hist_path,
                    "recordId": row.subject,
                    "rowId": row.id,
                    "deferralIds": deferral_ids,
                    "reason": row.reason,
                    "message": (
                        f"Record {row.subject} lifted deferral(s) "
                        f"{', '.join(deferral_ids) or '(none listed)'} in row {row.id}: "
                        f"{row.reason}. A signed Memory Reviewer verdict is required to accept "
                        "a lifted deferral."
                    ),
                }
            )
    return findings


def _candidate_file_blob(code: CodeTree, path: str) -> str | None:
    data = code.read(path)
    return None if data is None else git_blob_id(data)


def _warnings_for_deferral(
    path: str, record: DependentRecord, deferral: DependentDeferral, env: _WarningEnv
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if deferral.condition.task is not None:
        fact = env.task_facts.get(deferral.condition.task)
        if fact is not None and fact.is_terminal:
            findings.append(
                {
                    "check": DEPENDENT_FLAGS_CHECK,
                    "kind": "deferral_orphaned",
                    "path": path,
                    "recordId": record.id,
                    "document": record.document,
                    "deferralId": deferral.id,
                    "targetTask": deferral.condition.task,
                    "targetStatus": fact.status,
                    "owner": deferral.owner,
                    "message": (
                        f"{record.id} ({record.document}) deferral {deferral.id} is orphaned: "
                        f"target task {deferral.condition.task} is {fact.status} "
                        f"(owner={deferral.owner}). Re-defer to an active leaf or lift/resolve "
                        "through knowledge_write."
                    ),
                }
            )
    watched = deferral.condition.file
    if watched is not None and watched not in env.changed_files:
        observed_blob = _candidate_file_blob(env.code, watched)
        if observed_blob != deferral.fileBlob:
            findings.append(
                {
                    "check": DEPENDENT_FLAGS_CHECK,
                    "kind": "deferral_due_warning",
                    "path": path,
                    "recordId": record.id,
                    "document": record.document,
                    "deferralId": deferral.id,
                    "file": watched,
                    "recordedBlob": deferral.fileBlob,
                    "observedBlob": observed_blob,
                    "owner": deferral.owner,
                    "message": (
                        f"{record.id} ({record.document}) deferral {deferral.id} watched file "
                        f"{watched} changed before this leaf's base "
                        f"(recorded blob {deferral.fileBlob}, candidate blob "
                        f"{observed_blob or 'absent'}; owner={deferral.owner}). Non-blocking "
                        "warning: this leaf did not touch the watched file."
                    ),
                }
            )
    return findings


def _open_deferral_warnings(
    candidate_dependents: Mapping[str, RecordFile], env: _WarningEnv
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for record_id in sorted(candidate_dependents):
        rf = candidate_dependents[record_id]
        record = rf.record
        assert isinstance(record, DependentRecord)
        for deferral in record.deferrals or ():
            if env.owner is not None and deferral.createdByLeaf == env.owner:
                continue
            findings.extend(_warnings_for_deferral(rf.path, record, deferral, env))
    return findings


def collect_dependent_report_only_findings(inputs: DependentFlagInputs) -> list[dict[str, Any]]:
    """Collect Lever-1 confirmation flags, lifted-deferral flags, and deferral warnings."""

    parsed_candidate = parse_tree(inputs.candidate_tree)
    candidate_dependents = {
        rf.record.id: rf
        for rf in parsed_candidate.records
        if isinstance(rf.record, DependentRecord)
    }
    scope = inputs.scope
    owner = (scope.owner if scope is not None else None) or _infer_owner(parsed_candidate)
    code_base = (
        scope.code_base
        if scope is not None and scope.code_base
        else _memory_head_code_commit(inputs.memory_root)
    )
    base_code: CodePathSet | None = None
    if code_base:
        try:
            base_code = code_tree_from_git(
                inputs.code_root, code_base, label=f"code base {code_base}"
            )
        except (KnowledgeTreeReadError, ValueError, OSError, subprocess.SubprocessError):
            base_code = None
    changed_files = _changed_files_from_base(inputs.code_root, code_base, base_code)
    base_parsed = parse_tree(inputs.base_tree) if inputs.base_tree is not None else None

    findings: list[dict[str, Any]] = []
    findings.extend(
        _pre_base_stale_confirmation_findings(
            candidate_dependents,
            base_parsed,
            base_code,
            owner=owner,
            changed_files=changed_files,
        )
    )
    findings.extend(_lifted_deferral_findings(parsed_candidate, inputs.base_tree, owner))

    needs_task_facts = any(
        d.condition.task is not None and (owner is None or d.createdByLeaf != owner)
        for rf in candidate_dependents.values()
        if isinstance(rf.record, DependentRecord)
        for d in rf.record.deferrals or ()
    )
    task_facts = (
        load_task_facts(scope.coordination_root, scope.repo_name)
        if needs_task_facts and scope is not None
        else {}
    )
    warning_env = _WarningEnv(
        code=inputs.code,
        owner=owner,
        changed_files=changed_files,
        task_facts=task_facts,
    )
    findings.extend(_open_deferral_warnings(candidate_dependents, warning_env))
    return findings
