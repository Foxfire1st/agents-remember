"""Audited removal of a planning leaf proven to have never started."""

from __future__ import annotations

import hashlib
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.tasks import (
    DiscardedSubTask,
    DiscardSourceProof,
    TaskDocSourceSnapshot,
    TaskDocument,
    capture_task_doc_source,
    markdown_path_for,
    read_task_doc_with_source,
    write_task_docs_and_remove,
)
from agents_remember.worktrees.task_leaf_binding import (
    LeafTaskBinding,
    TaskLeafBindingError,
    resolve_leaf_task_binding,
)

from .task_doc_publication import (
    TaskDocPublication,
    preview_task_doc_projection_effects,
    publish_task_doc_set,
    task_doc_publication_transaction,
    validate_task_doc_transaction,
)
from .task_doc_response import graph_titles_for, task_doc_preview, task_doc_result
from .task_doc_route_review import TaskDocError, _validate
from .task_unstarted_evidence import TaskUnstartedEvidence, prove_task_unstarted


@dataclass(frozen=True)
class _DiscardCandidate:
    number: str
    audit: DiscardedSubTask
    evidence: TaskUnstartedEvidence
    updated: TaskDocument
    leaf_files: tuple[Path, Path]
    publication: TaskDocPublication
    deleted: list[str]


@dataclass(frozen=True)
class DiscardUnstartedRequest:
    config: McpRuntimeConfig
    repo_id: str
    task_name: str | None
    task_root: Path
    parent_path: Path
    subtask: dict[str, Any]
    dry_run: bool


@dataclass(frozen=True)
class _DiscardPublicationRequest:
    request: DiscardUnstartedRequest
    updated: TaskDocument
    binding: LeafTaskBinding
    evidence: TaskUnstartedEvidence
    leaf_files: tuple[Path, Path]
    deleted: list[str]


def discard_unstarted_subtask(request: DiscardUnstartedRequest) -> dict[str, Any]:
    """Preview or apply one parent-audited discard under the task/start CAS."""

    config = request.config
    repo_id = request.repo_id
    task_name = request.task_name
    task_root = request.task_root
    parent_path = request.parent_path
    subtask = request.subtask
    dry_run = request.dry_run
    number = str(subtask["number"]).strip()
    reason = str(subtask.get("reason") or "").strip()
    if not number:
        raise TaskDocError("discard-unstarted requires a nonblank subtask.number")
    if not reason:
        raise TaskDocError("discard-unstarted requires a nonblank audit reason")
    if subtask.get("keep_file"):
        raise TaskDocError("discard-unstarted always removes the child JSON and Markdown")
    parent, parent_snapshot = read_task_doc_with_source(parent_path)
    if parent.kind != "master":
        raise TaskDocError("discard-unstarted is valid only for a master-owned leaf")
    prior = next((item for item in parent.discardedSubTasks if item.number == number), None)
    if prior is not None:
        return _resume_discard_unstarted(
            request,
            parent,
            parent_snapshot,
            prior,
        )
    try:
        binding = resolve_leaf_task_binding(
            config.coordination_root,
            repo_id,
            task_root,
            number,
            task_name=task_name,
        )
    except TaskLeafBindingError as exc:
        raise TaskDocError(exc.detail) from exc
    evidence = prove_task_unstarted(config, binding)
    if not evidence.unstarted:
        return _discard_unstarted_refusal(task_root, parent, parent_path, evidence)
    audit = DiscardedSubTask(
        number=binding.row.number,
        name=binding.row.name,
        file=binding.row.file,
        scope=binding.row.scope,
        reason=reason,
        discardedAt=datetime.now(UTC).replace(microsecond=0).isoformat(),
        proof=evidence.persisted_proof(),
    )
    data = parent.model_dump(by_alias=True)
    data["subTasks"] = [
        row for row in data.get("subTasks", []) if row.get("number") != binding.row.number
    ]
    data.setdefault("discardedSubTasks", []).append(audit.model_dump(mode="json"))
    updated = _validate(data)
    leaf_files = [binding.leaf_json_path, binding.leaf_markdown_path]
    deleted: list[str] = []
    publication_context = TaskDocPublication(
        config,
        repo_id,
        task_root,
        parent,
        updated,
        [updated],
        (parent_snapshot, evidence.child_source),
        publisher=_discard_publication(
            _DiscardPublicationRequest(
                request=request,
                updated=updated,
                binding=binding,
                evidence=evidence,
                leaf_files=(leaf_files[0], leaf_files[1]),
                deleted=deleted,
            )
        ),
    )
    return _discard_candidate_result(
        task_root,
        _DiscardCandidate(
            number,
            audit,
            evidence,
            updated,
            (leaf_files[0], leaf_files[1]),
            publication_context,
            deleted,
        ),
        dry_run=dry_run,
    )


def _discard_candidate_result(
    task_root: Path,
    candidate: _DiscardCandidate,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    if dry_run:
        validate_task_doc_transaction(task_doc_publication_transaction(candidate.publication))
        result = task_doc_preview("remove_subtask", candidate.updated, task_root)
        result.update(
            {
                "removedSubtask": candidate.number,
                "discardState": "would-discard",
                "discardAudit": candidate.audit.model_dump(mode="json"),
                "discardEvidence": candidate.evidence.public_payload(),
                "wouldDeleteFiles": [
                    path.as_posix() for path in candidate.leaf_files if path.exists()
                ],
                "projectionEffects": [
                    effect.model_dump(mode="json")
                    for effect in preview_task_doc_projection_effects(candidate.publication)
                ],
            }
        )
        return result
    published = publish_task_doc_set(candidate.publication)
    json_path, markdown_path = published.written[0]
    result = task_doc_result("remove_subtask", candidate.updated, json_path, markdown_path)
    result.update(
        {
            "removedSubtask": candidate.number,
            "discardState": "discarded",
            "discardAudit": candidate.audit.model_dump(mode="json"),
            "discardEvidence": candidate.evidence.public_payload(),
            "deletedFiles": candidate.deleted,
            "projectionEffects": [
                effect.model_dump(mode="json") for effect in published.projection_effects
            ],
        }
    )
    return result


def _discard_publication(
    publication_request: _DiscardPublicationRequest,
) -> Callable[[], list[tuple[Path, Path]]]:
    """Build the task-lock-held publication that races exactly against start reservation."""

    def publication() -> list[tuple[Path, Path]]:
        request = publication_request.request
        try:
            current_binding = resolve_leaf_task_binding(
                request.config.coordination_root,
                request.repo_id,
                request.task_root,
                publication_request.binding.row.number,
                task_name=request.task_name,
            )
        except TaskLeafBindingError as exc:
            raise TaskDocError(exc.detail) from exc
        current = prove_task_unstarted(request.config, current_binding)
        if not current.unstarted or current.fingerprint != publication_request.evidence.fingerprint:
            raise TaskDocError(
                "discard-unstarted evidence changed before publication; inspect the exact "
                "started/ambiguous lifecycle evidence and retry its routed transition"
            )
        written, removed = write_task_docs_and_remove(
            request.task_root,
            [publication_request.updated],
            list(publication_request.leaf_files),
            graph_titles=graph_titles_for(request.task_root, publication_request.updated),
        )
        publication_request.deleted.extend(path.as_posix() for path in removed)
        return written

    return publication


def _resume_discard_unstarted(
    request: DiscardUnstartedRequest,
    parent: TaskDocument,
    parent_snapshot: TaskDocSourceSnapshot,
    audit: DiscardedSubTask,
) -> dict[str, Any]:
    """Converge a lost response or interrupted child removal from the parent audit."""

    config = request.config
    task_root = request.task_root
    reason = str(request.subtask.get("reason") or "").strip()
    if audit.reason != reason:
        raise TaskDocError(
            "discard-unstarted already published with a different audit reason; "
            "the durable parent record is authoritative"
        )
    leaf_files = _discard_audit_leaf_files(task_root, audit)
    replay_removals = _discard_replay_removals(leaf_files, audit)
    deleted: list[str] = []

    def publication() -> list[tuple[Path, Path]]:
        current_removals = _discard_replay_removals(leaf_files, audit)
        written, removed = write_task_docs_and_remove(
            task_root,
            [parent],
            current_removals,
            graph_titles=graph_titles_for(task_root, parent),
        )
        deleted.extend(path.as_posix() for path in removed)
        return written

    publication_context = TaskDocPublication(
        config,
        request.repo_id,
        task_root,
        parent,
        parent,
        [parent],
        (parent_snapshot, capture_task_doc_source(leaf_files[0])),
        publisher=publication,
    )
    persisted_evidence = {
        "state": "unstarted",
        "fingerprint": audit.proof.fingerprint,
        "taskDocumentRef": audit.proof.taskDocumentRef.model_dump(mode="json"),
        "facts": [{"kind": "parent-audit", "state": "published"}],
    }
    if request.dry_run:
        validate_task_doc_transaction(task_doc_publication_transaction(publication_context))
        result = task_doc_preview("remove_subtask", parent, task_root)
        result.update(
            {
                "removedSubtask": audit.number,
                "discardState": "already-discarded",
                "alreadyDiscarded": True,
                "discardAudit": audit.model_dump(mode="json"),
                "discardEvidence": persisted_evidence,
                "wouldDeleteFiles": [path.as_posix() for path in replay_removals],
                "projectionEffects": [
                    effect.model_dump(mode="json")
                    for effect in preview_task_doc_projection_effects(publication_context)
                ],
            }
        )
        return result
    published = publish_task_doc_set(publication_context)
    json_path, markdown_path = published.written[0]
    result = task_doc_result("remove_subtask", parent, json_path, markdown_path)
    result.update(
        {
            "removedSubtask": audit.number,
            "discardState": "already-discarded",
            "alreadyDiscarded": True,
            "discardAudit": audit.model_dump(mode="json"),
            "discardEvidence": persisted_evidence,
            "deletedFiles": deleted,
            "projectionEffects": [
                effect.model_dump(mode="json") for effect in published.projection_effects
            ],
        }
    )
    return result


def _discard_unstarted_refusal(
    task_root: Path,
    parent: TaskDocument,
    parent_path: Path,
    evidence: TaskUnstartedEvidence,
) -> dict[str, Any]:
    result = task_doc_result(
        "remove_subtask",
        parent,
        parent_path,
        markdown_path_for(task_root, parent),
    )
    result.update(
        {
            "ok": False,
            "discardState": (
                "refused-ambiguous" if evidence.state == "ambiguous" else "refused-started"
            ),
            "detail": (
                "discard-unstarted refused because canonical evidence does not prove that "
                "the leaf is unstarted"
            ),
            "discardEvidence": evidence.public_payload(),
            "nextAction": evidence.next_action or "developer-decision",
            "nextTool": evidence.next_tool,
            "nextArgs": evidence.next_args,
        }
    )
    return result


def _discard_audit_leaf_files(task_root: Path, audit: DiscardedSubTask) -> list[Path]:
    root = task_root.resolve(strict=False)
    markdown = root / audit.file
    if markdown.parent != root or markdown.suffix != ".md":
        raise TaskDocError("discard audit child source path is invalid")
    return [markdown.with_suffix(".json"), markdown]


def _discard_replay_removals(
    leaf_files: list[Path],
    audit: DiscardedSubTask,
) -> list[Path]:
    """Return only still-present child bytes proven identical to discard admission."""

    proofs = (audit.proof.childJson, audit.proof.childMarkdown)
    return [
        path
        for path, proof in zip(leaf_files, proofs, strict=True)
        if _discard_source_still_removable(path, proof)
    ]


def _discard_source_still_removable(path: Path, proof: DiscardSourceProof) -> bool:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise TaskDocError(
            "discard-unstarted recovery cannot inspect the accepted child source: "
            f"{path}: {type(exc).__name__}"
        ) from exc
    if not stat.S_ISREG(mode):
        raise TaskDocError(
            f"discard-unstarted recovery refuses a present non-regular child source: {path}"
        )
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise TaskDocError(
            "discard-unstarted recovery cannot read the accepted child source: "
            f"{path}: {type(exc).__name__}"
        ) from exc
    observed_sha256 = hashlib.sha256(payload).hexdigest()
    if proof.state != "present" or proof.sha256 != observed_sha256 or proof.size != len(payload):
        raise TaskDocError(
            "discard-unstarted recovery found child bytes that differ from the exact "
            f"accepted discard source; preserve and inspect {path}"
        )
    return True
