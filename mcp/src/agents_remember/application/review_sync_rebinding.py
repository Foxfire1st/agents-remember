"""Measure the exact post-sync code and memory candidates against one retained tree comparison.

The sync transaction is already complete: missing comparison/capture/evidence is a result state,
never an admission gate. Historical v1 measurements are readable but prove no memory-tree coverage.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

from agents_remember.application.review_comparison_generation import (
    ComparisonGenerationManifest,
)
from agents_remember.application.review_final_output_receipt import (
    comparison_key,
    require_comparison_repositories,
    select_review_comparison,
    source_comparison_matches,
)
from agents_remember.errors import FutureCodeCandidateError
from agents_remember.kernel.canonical_json import canonical_json_bytes, decoded_json
from agents_remember.memory.knowledge.durable_evidence import (
    DurableEvidencePublication,
    EvidenceReadBack,
    durable_reports_root,
    publish_durable_evidence,
    read_back_evidence,
)
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.review_final_output_receipt import tree_comparison_digest
from agents_remember.models.knowledge.review_sync_rebinding import (
    REVIEW_SYNC_REBINDING_VERSION,
    LegacyReviewSyncRebinding,
    ReviewSyncRebinding,
    code_channel_match,
    tree_sync_verdict,
)
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord
from agents_remember.observer.events import now_iso
from agents_remember.worktrees.modules.future_code_candidate import capture_future_code_candidate
from agents_remember.worktrees.modules.git import head_commit, worktree_candidate_tree
from agents_remember.worktrees.task_resolver import slugify
from agents_remember.worktrees.worktree_contract import WorktreeContract

REVIEW_SYNC_REBINDINGS_PREFIX = "review-sync-rebinding"
_SUPERSESSION_ACTION = "Open and record a successor tree comparison for the resolved code and memory candidates; retain this source comparison as previous input."

_CARRYING_SYNC_STATES: frozenset[str] = frozenset(
    {
        "synced",
        "sync-pass-completed-memory-skipped",
        "sync-pass-completed-source-moved-again",
    }
)

# The states in which the transaction resolved no pair, each with the store fact it observed and the
# R22 reason no rebinding is recorded. A table rather than a ladder, and one entry per state, so the
# sentence a caller reads names the state it was actually given: "this result is not a completed
# sync" was false for several of these, and it is the master's ruled-blocking class.
_CARRIED_NOTHING: dict[str, tuple[str, str]] = {
    "would-sync": (
        "preview",
        "this is a preview: the sync read its sources and moved no ref, no branch and no journal, so "
        "it resolved no pair to measure the review against",
    ),
    "already-current": (
        "no-movement",
        "the recorded base pair and participating work branches already contained the official line, "
        "so this sync carried nothing and resolved no pair to measure the review against",
    ),
    "sync-resolution-required": (
        "not-resolved",
        "the sync stopped with unmerged paths the knowledge adapter would not settle, so no pair was "
        "resolved to measure the review against",
    ),
    "sync-cancelled": (
        "cancelled",
        "the explicit cancellation restored every participating branch, so no pair was resolved to "
        "measure the review against",
    ),
    "memory-sync-choice-required": (
        "choice-required",
        "the sync required a memory sync choice before any mutation, so no pair was resolved to "
        "measure the review against",
    ),
}


def resolved_pair_completed(payload: dict[str, Any]) -> bool:
    """Whether this result is a finished sync that **carried the official line** into the leaf.

    Three facts, and each is checked rather than inferred: the operation is the sync, the sync did not
    fail, and its state is one of the three in which the transaction carried the line
    (:data:`_CARRYING_SYNC_STATES`). Nothing is inferred from which keys the payload happens to hold
    -- a preview is excluded because ``would-sync`` is not a carrying state, and an up-to-date leaf is
    excluded because ``already-current`` reports that the pair *already* held the line rather than
    that anything was carried. Every other state is described by its own entry in
    :data:`_CARRIED_NOTHING`, so a caller is told which fact was observed instead of a blanket claim
    about completions.

    The success conjunct is carried even though every producer of those three states returns zero
    today: a carrying state reported beside ``ok: false`` is a result this tool must not measure, and
    stating the requirement here is what keeps that true of a producer that does not exist yet.

    Exported because two callers need the same answer: the recorder, and the block that says why
    nothing was bound.
    """

    return (
        payload.get("operation") == "worktree_sync"
        and bool(payload.get("ok"))
        and str(payload.get("state", "")) in _CARRYING_SYNC_STATES
    )


@dataclass(frozen=True)
class ReviewSyncRebindingPublication:
    publication: DurableEvidencePublication
    rebinding: ReviewSyncRebinding
    read_back: EvidenceReadBack


@dataclass(frozen=True)
class ReviewSyncRebindingRead:
    state: Literal["recorded", "legacy-limit", "not-recorded", "unreadable"]
    leaf_id: str
    generation_id: str
    destination: Path
    rebinding: ReviewSyncRebinding | LegacyReviewSyncRebinding | None
    sha256: str | None
    detail: str

    def covers_resolved_pair(self) -> bool:
        return (
            isinstance(self.rebinding, ReviewSyncRebinding)
            and self.rebinding.covers_resolved_pair()
        )


def rebinding_file_name(leaf_id: str, generation_id: str) -> str:
    return f"{REVIEW_SYNC_REBINDINGS_PREFIX}-{slugify(leaf_id)}-{generation_id}.json"


class _ResolvedSourceUnmeasurable(RuntimeError):
    pass


def record_review_sync_rebinding(
    contract: WorktreeContract, payload: dict[str, Any]
) -> ReviewSyncRebindingPublication | None:
    if not resolved_pair_completed(payload) or contract.kind != "leaf":
        return None
    selection = select_review_comparison(contract.task_root, contract.leaf_id)
    if selection.comparison is None:
        return None
    comparison = selection.comparison
    require_comparison_repositories(contract, comparison)
    try:
        resolved = capture_future_code_candidate(contract)
    except FutureCodeCandidateError as error:
        raise _ResolvedSourceUnmeasurable(f"{error.status}: {error}") from error
    memory_head, memory_tree, memory_detail = _resolved_memory(contract)
    code_match = code_channel_match(comparison.code_candidate.tree, resolved.codeCandidateTree)
    memory_match = (
        "unmeasured"
        if memory_tree is None
        else code_channel_match(comparison.memory_candidate.tree, memory_tree)
    )
    rebinding = ReviewSyncRebinding(
        recorded_at=now_iso(),
        repository_id=contract.repo_name,
        master=contract.parent_task_name or contract.task_name,
        leaf_id=contract.leaf_id,
        task_root=str(contract.task_root),
        contract_path=str(contract.contract_path),
        comparison=comparison,
        comparison_digest=tree_comparison_digest(comparison),
        resolved_code_head=resolved.observedCodeHead,
        resolved_candidate_code_tree_id=resolved.codeCandidateTree,
        resolved_memory_head=memory_head,
        resolved_candidate_memory_tree_id=memory_tree,
        memory_detail=memory_detail,
        code_match=code_match,
        memory_match=memory_match,
        state=tree_sync_verdict(code_match, memory_match),
        successor_action=_SUPERSESSION_ACTION,
    )
    publication = publish_durable_evidence(
        contract.task_root,
        rebinding_file_name(contract.leaf_id, comparison_key(comparison)),
        canonical_json_bytes(rebinding.model_dump(mode="json", by_alias=True)).decode("utf-8"),
    )
    return ReviewSyncRebindingPublication(publication, rebinding, read_back_evidence(publication))


def _resolved_memory(contract: WorktreeContract) -> tuple[str | None, str | None, str]:
    if contract.memory_worktree is None:
        return None, None, "the enclosure has no memory worktree to capture"
    try:
        before = head_commit(contract.memory_worktree)
        with TemporaryDirectory(prefix="ar-sync-memory-") as scratch:
            tree = worktree_candidate_tree(contract.memory_worktree, Path(scratch) / "index")
        if before != head_commit(contract.memory_worktree):
            return None, None, "memory HEAD moved during the isolated candidate capture"
        return (
            before,
            tree,
            "the complete memory candidate was captured through an isolated copy of its index",
        )
    except (OSError, RuntimeError) as error:
        return None, None, f"the memory candidate could not be captured: {error}"


def rebinding_result_block(contract: WorktreeContract, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        published = record_review_sync_rebinding(contract, payload)
    except _ResolvedSourceUnmeasurable as error:
        payload["review_rebinding"] = {
            "rebinding_version": REVIEW_SYNC_REBINDING_VERSION,
            "state": "source-unmeasured",
            "covers_resolved_pair": False,
            "detail": str(error),
        }
        return payload
    except (KnowledgeStorageError, OSError, RuntimeError, ValueError) as error:
        payload["review_rebinding"] = {
            "rebinding_version": REVIEW_SYNC_REBINDING_VERSION,
            "state": "not-recorded",
            "covers_resolved_pair": False,
            "detail": str(error),
        }
        return payload
    if published is None:
        payload["review_rebinding"] = _nothing_to_bind_block(contract, payload)
        payload["review_rebinding"]["rebinding_version"] = REVIEW_SYNC_REBINDING_VERSION
        return payload
    block = published.rebinding.model_dump(mode="json", by_alias=True)
    block.update(
        covers_resolved_pair=published.rebinding.covers_resolved_pair(),
        statement=published.rebinding.statement(),
        evidence=published.publication.reference(),
        evidence_sha256=published.publication.digest(),
        read_back=published.read_back.state,
    )
    payload["review_rebinding"] = block
    return payload


def _nothing_to_bind_block(contract: WorktreeContract, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("operation") != "worktree_sync" or contract.kind != "leaf":
        return {
            "state": "not-applicable",
            "covers_resolved_pair": False,
            "detail": "this result is not a managed sync of a leaf's pair",
        }
    if not resolved_pair_completed(payload):
        state = str(payload.get("state", ""))
        if payload.get("ok") or state not in _CARRYING_SYNC_STATES:
            reason = _CARRIED_NOTHING.get(state)
            if reason is not None:
                return {"state": reason[0], "detail": reason[1], "covers_resolved_pair": False}
        return {
            "state": "not-measured",
            "covers_resolved_pair": False,
            "detail": f"sync state {state!r} did not successfully carry a resolved pair",
        }
    selection = select_review_comparison(contract.task_root, contract.leaf_id)
    return {
        "state": selection.state,
        "selection_state": selection.state,
        "detail": selection.detail,
        "covers_resolved_pair": False,
    }


def read_review_sync_rebinding(
    task_root: Path, leaf_id: str, generation_id: str
) -> ReviewSyncRebindingRead:
    destination = durable_reports_root(task_root) / rebinding_file_name(leaf_id, generation_id)
    try:
        raw = destination.read_bytes()
    except FileNotFoundError:
        return ReviewSyncRebindingRead(
            "not-recorded",
            leaf_id,
            generation_id,
            destination,
            None,
            None,
            f"no rebinding is recorded at {destination}",
        )
    except OSError as error:
        return ReviewSyncRebindingRead(
            "unreadable", leaf_id, generation_id, destination, None, None, str(error)
        )
    try:
        data = decoded_json(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("a rebinding must be a JSON object")
        if data.get("rebinding_version") == "ar-review-sync-rebinding/v1":
            legacy = LegacyReviewSyncRebinding.model_validate(data)
            if legacy.leaf_id != leaf_id or legacy.supersedes_generation_id != generation_id:
                raise ValueError("the historical rebinding names another leaf or generation")
            return ReviewSyncRebindingRead(
                "legacy-limit",
                leaf_id,
                generation_id,
                destination,
                legacy,
                f"sha256:{hashlib.sha256(raw).hexdigest()}",
                "historical v1 rebinding retains source and dataset identities but proves no exact reviewed memory tree; pair coverage is unavailable",
            )
        rebinding = ReviewSyncRebinding.model_validate(data)
        if (
            rebinding.leaf_id != leaf_id
            or comparison_key(rebinding.comparison) != generation_id
            or not source_comparison_matches(task_root, rebinding.comparison)
        ):
            raise ValueError(
                "the rebinding does not describe the retained source comparison at this location"
            )
    except (UnicodeDecodeError, ValueError) as error:
        return ReviewSyncRebindingRead(
            "unreadable", leaf_id, generation_id, destination, None, None, str(error)
        )
    return ReviewSyncRebindingRead(
        "recorded",
        leaf_id,
        generation_id,
        destination,
        rebinding,
        f"sha256:{hashlib.sha256(raw).hexdigest()}",
        rebinding.statement(),
    )


def rebinding_names_the_generation(
    read: ReviewSyncRebindingRead,
    manifest: ComparisonGenerationManifest | ReviewTreeComparisonRecord,
) -> ReviewSyncRebinding | None:
    """Accept only an exact tree-bound source record; legacy dataset proof cannot cover memory."""

    if not isinstance(manifest, ReviewTreeComparisonRecord) or not isinstance(
        read.rebinding, ReviewSyncRebinding
    ):
        return None
    return read.rebinding if read.rebinding.comparison == manifest else None
