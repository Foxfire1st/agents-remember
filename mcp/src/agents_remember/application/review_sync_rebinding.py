"""Rebinding one comparison generation across a managed sync's recovery states (ICR-R22@v1).

:mod:`agents_remember.worktrees.sync_transaction` owns the sync: it parks the worktree candidate,
carries each side onto the official line (fast-forward, an ordinary text merge, or the binary-stage
knowledge merge of :mod:`agents_remember.worktrees.knowledge_conflict`), restores the parked candidate,
and journals every partial outcome so a resume or a cancel can finish what it started. None of that
touches the leaf's review, and the two facts the review binds are exactly the two the sync moves: the
candidate source tree captured from the leaf's worktree, and the knowledge dataset at the repository's
declared publication location. This module owns the missing step -- measuring both against the
generation the review actually published, and recording what it found.

**It measures owners' values; it re-implements none of them.**

* the generation being judged is selected from the generation store's own order
  (:func:`~agents_remember.application.review_final_output_receipt.select_review_generation`), and its
  reviewed identities are read from its own sealed manifest;
* the resolved source side is :func:`capture_future_code_candidate` -- the shipped capture owner,
  re-derived after the sync the same way the review's own pre-publication recheck re-derives it, so a
  captured endpoint that moved is an observation rather than a guess;
* the resolved knowledge side is the ordinary read route's own answer at the declared publication
  location (:func:`~agents_remember.application.published_intent.resolve_published_intent`, reached
  through :func:`~agents_remember.application.knowledge_publication_route.declared_publication_location`),
  which is the same route a later task's planner selects its knowledge with (ICR-R20@v1).

**Nothing here can refuse a sync.** The transaction has already finished its Git work and written its
contract when this runs, and it is called with the result that fact produced. A capture that fails, a
leaf with no published generation, an unreadable manifest and a location holding nothing are all
*states* this reports rather than exceptions it raises, because a rebinding that could fail a completed
sync would be a mandatory review gate on the sync wearing a receipt's name -- which the packet does not
grant and :mod:`agents_remember.application.review_final_output_receipt` already refused to become for
closeout.

**Movement is recorded, never papered over.** The record is published as one durable artifact under the
task's own reports root, one file per (leaf, generation), and an exact re-measurement converges on that
one location. It supersedes nothing itself: the remedy it names is a successor generation frozen with
the judged generation as its ``parent``
(:func:`agents_remember.application.review_comparison_freeze.freeze_review_comparison`), so the
supersession is a lineage a reader resolves rather than a claim this record makes. Reclamation is
:func:`discard_review_sync_rebindings` -- a derived measurement, so a discarded one reads back as
``not-recorded`` naming the location it looked in, while the generation's own manifest and retained
bytes are untouched here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agents_remember.application.knowledge_publication_route import declared_publication_location
from agents_remember.application.published_intent import (
    PublishedIntentUnavailable,
    resolve_published_intent,
)
from agents_remember.application.review_comparison_generation import (
    COMPARISON_MANIFEST_NAME,
    ComparisonGenerationManifest,
    read_generation_refs,
    read_manifest,
)
from agents_remember.application.review_final_output_receipt import (
    ReviewGenerationSelection,
    select_review_generation,
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
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.review_sync_rebinding import (
    REVIEW_SYNC_REBINDING_VERSION,
    REVIEW_SYNC_SELECTION_RULE,
    ReviewSyncRebinding,
    ReviewSyncRebindingVerdict,
    SyncChannelMatch,
    SyncKnowledgeObservation,
    code_channel_match,
    knowledge_channel_match,
    review_sync_verdict,
)
from agents_remember.observer.events import now_iso
from agents_remember.worktrees.modules.future_code_candidate import (
    FutureCodeCandidateIdentity,
    capture_future_code_candidate,
)
from agents_remember.worktrees.task_resolver import slugify
from agents_remember.worktrees.worktree_contract import WorktreeContract

__all__ = [
    "REVIEW_SYNC_REBINDINGS_PREFIX",
    "REVIEW_SYNC_REBINDING_VERSION",
    "REVIEW_SYNC_SELECTION_RULE",
    "ReviewSyncRebinding",
    "ReviewSyncRebindingPublication",
    "ReviewSyncRebindingRead",
    "ReviewSyncRebindingVerdict",
    "SyncChannelMatch",
    "SyncKnowledgeObservation",
    "discard_review_sync_rebindings",
    "read_review_sync_rebinding",
    "rebinding_file_name",
    "rebinding_result_block",
    "record_review_sync_rebinding",
    "resolved_pair_completed",
]

# The file-name prefix every rebinding is published under, inside the shipped durable reports root:
# one file per leaf and judged generation, ``review-sync-rebinding-<leaf>-<generation-id>.json``. One
# file per generation rather than per sync, because the question this record answers is "does the
# review still describe what the leaf holds" and a later sync over the same generation replaces that
# answer instead of accumulating a second one beside it.
REVIEW_SYNC_REBINDINGS_PREFIX = "review-sync-rebinding"

# The one action that produces a comparison current with the resolved pair. Stated here, once,
# because a record whose remedy is re-spelled at every render site is a record whose remedy drifts:
# the successor is a normal freeze whose lineage names this generation as its predecessor, so the
# supersession travels as the successor's own recorded lineage rather than as prose.
_SUPERSESSION_ACTION = (
    "freeze a successor generation for this leaf with the judged generation as its parent "
    "(freeze_review_comparison), which records the supersession as the successor's own lineage; "
    "this rebinding substitutes neither the moved source head nor the changed dataset, and it "
    "relabels no earlier result as covering either"
)

# What a caller reads when this leaf published no comparison generation at all. It is a state rather
# than a silent omission, because "this leaf's review was never frozen" and "the review was frozen and
# agrees" are different facts about the store.
_NO_GENERATION_DETAIL = (
    "no comparison generation is published for this leaf, so there is no reviewed comparison to "
    "measure against the pair this sync resolved"
)


class _ResolvedSourceUnmeasurable(RuntimeError):
    """Internal control flow: the resolved source side could not be captured, so none is bound."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


# The three states in which the transaction **carried the official line into the leaf**. They are
# listed rather than derived from a prefix because two of them begin with ``sync-pass-`` precisely so
# a caller can tell "the pass completed" from "the pass stopped". ``already-current`` is deliberately
# NOT here: that result carries nothing at all, because the recorded pair and the participating work
# branches already held the official line, so there is no carried pair to measure a review against --
# and its own sentence in ``_CARRIED_NOTHING`` says exactly that.
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
class _ResolvedCapture:
    """The resolved source side after a sync, or the named reason it could not be measured."""

    identity: FutureCodeCandidateIdentity | None
    state: Literal["captured", "unavailable"]
    detail: str


@dataclass(frozen=True)
class ReviewSyncRebindingPublication:
    """One published rebinding: the durable artifact, the record, and the read-back that proves it."""

    publication: DurableEvidencePublication
    rebinding: ReviewSyncRebinding
    read_back: EvidenceReadBack


@dataclass(frozen=True)
class ReviewSyncRebindingRead:
    """What reading one generation's rebinding back found.

    ``not-recorded`` is reported only for a location holding no file at all; a present-but-unreadable
    artifact is ``unreadable``, with the reader's own reason beside it, because "no sync has measured
    this generation" and "a measurement is here and cannot be read" are different facts and neither is
    the other.
    """

    state: Literal["recorded", "not-recorded", "unreadable"]
    leaf_id: str
    generation_id: str
    destination: Path
    rebinding: ReviewSyncRebinding | None
    sha256: str | None
    detail: str

    def covers_resolved_pair(self) -> bool:
        """Whether this read established that the review still describes the resolved pair."""

        return self.rebinding is not None and self.rebinding.covers_resolved_pair()


def rebinding_file_name(leaf_id: str, generation_id: str) -> str:
    """Return the one durable file name one generation's rebinding is published under."""

    return f"{REVIEW_SYNC_REBINDINGS_PREFIX}-{slugify(leaf_id)}-{generation_id}.json"


def record_review_sync_rebinding(
    contract: WorktreeContract, payload: dict[str, Any]
) -> ReviewSyncRebindingPublication | None:
    """Measure one finished sync against the leaf's comparison generation and publish the record.

    Returns ``None`` in exactly two cases, and each is a fact rather than a silence a caller has to
    interpret: the payload is not a completed sync (so nothing was resolved to bind), or the leaf
    published no comparison generation (so there is no reviewed comparison to measure). Every other
    outcome -- including a capture the leaf's worktree refused and a declared location holding nothing
    -- is a published record carrying the state it measured.

    The caller passes the sync's own result payload because that is where the transaction states what
    it did; nothing here re-reads the journal, re-runs a merge or touches the parked candidate. The
    stash the transaction took, the candidate it restored and the generations the leaf already
    published are all left exactly as they were.
    """

    if not resolved_pair_completed(payload):
        return None
    if contract.kind != "leaf":
        return None
    selection = select_review_generation(contract.task_root, contract.leaf_id)
    if selection.state != "selected" or selection.ref is None:
        return None
    ref = selection.ref
    capture = _resolved_capture(contract)
    if capture.identity is None:
        # A capture the worktree refused is not a measured source side, so no candidate tree is
        # invented for it and no record is published: the resolved pair's source half does not
        # exist to be bound. The refusal is reported as a state by ``rebinding_result_block``.
        raise _ResolvedSourceUnmeasurable(capture.detail)
    manifest = read_manifest(ref.directory / COMPARISON_MANIFEST_NAME)
    rebinding = _assemble(contract, capture.identity, ref.manifest_digest, manifest)
    publication = publish_durable_evidence(
        contract.task_root,
        rebinding_file_name(contract.leaf_id, manifest.generation_id),
        canonical_json_bytes(rebinding.model_dump(mode="json")).decode("utf-8"),
    )
    return ReviewSyncRebindingPublication(
        publication=publication,
        rebinding=rebinding,
        read_back=read_back_evidence(publication),
    )


def rebinding_result_block(contract: WorktreeContract, payload: dict[str, Any]) -> dict[str, Any]:
    """Carry the measured rebinding on a completed sync result, or state why none was recorded.

    This is the entry point the sync tool calls, and it never refuses anything: a capture that could
    not be taken, an unreadable generation record and a filesystem that refuses the write are each
    reported as ``state`` here rather than raised, so a completed sync is returned unchanged and no
    review obligation becomes a gate on the Git transaction it describes.
    """

    try:
        published = record_review_sync_rebinding(contract, payload)
    except _ResolvedSourceUnmeasurable as unmeasurable:
        payload["review_rebinding"] = {
            "state": "source-unmeasured",
            "covers_resolved_pair": False,
            "detail": (
                "the pair this sync resolved was not measured against this leaf's comparison "
                "generation: the leaf's own capture owner could not derive the post-sync candidate, "
                f"so no candidate tree was observed to bind ({unmeasurable.detail})"
            ),
        }
        return payload
    except (KnowledgeStorageError, OSError, RuntimeError, ValueError) as error:
        payload["review_rebinding"] = {
            "state": "not-recorded",
            "detail": (
                "the resolved pair was not measured against this leaf's comparison generation, so "
                "no review rebinding is recorded: "
                f"{type(error).__name__}: {error}"
            ),
        }
        return payload
    if published is None:
        payload["review_rebinding"] = _nothing_to_bind_block(contract, payload)
        return payload
    rebinding = published.rebinding
    payload["review_rebinding"] = {
        "state": rebinding.state,
        "covers_resolved_pair": rebinding.covers_resolved_pair(),
        "statement": rebinding.statement(),
        "generation_id": rebinding.supersedes_generation_id,
        "generation_index": rebinding.supersedes_generation_index,
        "binding_digest": rebinding.supersedes_binding_digest,
        "manifest_digest": rebinding.supersedes_manifest_digest,
        "reviewed_candidate_code_tree_id": rebinding.reviewed_candidate_code_tree_id,
        "resolved_code_head": rebinding.resolved_code_head,
        "resolved_candidate_code_tree_id": rebinding.resolved_candidate_code_tree_id,
        "code_match": rebinding.code_match,
        "reviewed_knowledge_state": rebinding.reviewed_knowledge_state,
        "reviewed_knowledge_logical_digest": rebinding.reviewed_knowledge_logical_digest,
        "resolved_knowledge": rebinding.resolved_knowledge.model_dump(mode="json"),
        "knowledge_match": rebinding.knowledge_match,
        "successor_action": rebinding.successor_action,
        "evidence": published.publication.reference(),
        "evidence_sha256": published.publication.digest(),
        "read_back": published.read_back.state,
    }
    return payload


def read_review_sync_rebinding(
    task_root: Path, leaf_id: str, generation_id: str
) -> ReviewSyncRebindingRead:
    """Read one generation's rebinding back, as a state rather than an exception.

    Consumed by :func:`~agents_remember.application.review_comparison_reopen.reopen_comparison_generation`,
    so a reader of a recorded comparison sees what the leaf's own syncs measured against it beside the
    inputs it bound instead of having to know this record's file name; and by
    :func:`read_review_sync_rebindings`, which is what a caller holding a leaf and no generation id
    uses.
    """

    destination = durable_reports_root(task_root) / rebinding_file_name(leaf_id, generation_id)
    try:
        raw = destination.read_bytes()
    except FileNotFoundError:
        return ReviewSyncRebindingRead(
            state="not-recorded",
            leaf_id=leaf_id,
            generation_id=generation_id,
            destination=destination,
            rebinding=None,
            sha256=None,
            detail=(
                f"nothing is recorded at {destination} for comparison generation "
                f"{generation_id} of {leaf_id}; a record this leaf's own reclamation discarded and "
                "one that was never written read the same here"
            ),
        )
    except OSError as error:
        return ReviewSyncRebindingRead(
            state="unreadable",
            leaf_id=leaf_id,
            generation_id=generation_id,
            destination=destination,
            rebinding=None,
            sha256=None,
            detail=f"the rebinding record at {destination} could not be read: {error}",
        )
    try:
        rebinding = ReviewSyncRebinding.model_validate(decoded_json(raw.decode("utf-8")))
    except (UnicodeDecodeError, ValueError) as error:
        return ReviewSyncRebindingRead(
            state="unreadable",
            leaf_id=leaf_id,
            generation_id=generation_id,
            destination=destination,
            rebinding=None,
            sha256=None,
            detail=(
                f"the rebinding record at {destination} is not a readable "
                f"ar-review-sync-rebinding/v1 record: {error}"
            ),
        )
    return ReviewSyncRebindingRead(
        state="recorded",
        leaf_id=leaf_id,
        generation_id=generation_id,
        destination=destination,
        rebinding=rebinding,
        sha256=_digest(raw),
        detail=rebinding.statement(),
    )


def rebinding_names_the_generation(
    read: ReviewSyncRebindingRead, manifest: ComparisonGenerationManifest
) -> ReviewSyncRebinding | None:
    """Return the record only when it describes exactly the generation it names, else ``None``.

    **Why this exists.** The record's own validator re-derives every verdict from the fields the
    record carries, so no record can claim a state its channels deny -- but a field *and* the verdict
    derived from it can be forged together: a record naming a reviewed candidate tree nobody captured
    is still internally consistent, because comparing two fabricated identities is still a comparison.
    This is the missing half, and it compares against the store rather than against the record: the
    reviewed baseline and candidate trees, the reviewed knowledge state and its digest, and the
    generation's own seal must all be the ones the leaf's published manifest holds.

    A caller holding the generation (the reopen owner, or a reader that resolved one) uses this.
    :func:`read_review_sync_rebinding` alone answers only what is *recorded* at a location, which is
    evidence of a measurement rather than proof that the measurement describes this generation.
    """

    if read.rebinding is None:
        return None
    record = read.rebinding
    if (
        record.supersedes_generation_id != manifest.generation_id
        or record.supersedes_generation_index != manifest.generation_index
        or record.supersedes_binding_digest != manifest.binding_digest
        or record.reviewed_baseline_code_tree_id != manifest.source.baseline_code_tree_id
        or record.reviewed_candidate_code_tree_id != manifest.source.candidate_code_tree_id
    ):
        return None
    after = manifest.knowledge_side("after")
    digest = None if after.identity is None else after.identity.logical_digest
    if record.reviewed_knowledge_state != after.state or (
        record.reviewed_knowledge_logical_digest != digest
    ):
        return None
    return record


def read_review_sync_rebindings(
    task_root: Path, leaf_id: str
) -> tuple[ReviewSyncRebindingRead, ...]:
    """Read every rebinding recorded for one leaf's generations, in the store's own generation order.

    A leaf that published three generations and synced twice has three readable answers here rather
    than one: a rebinding is a measurement of one generation, and the generations a leaf keeps are
    retained history, so collapsing them into "the latest" would discard the evidence that a
    superseded generation was measured before it was superseded.

    **This is the acceptance and curation route, not a shipped one.** No mounted tool calls it today:
    the per-generation reader is reached in production from the reopen owner
    (:func:`~agents_remember.application.review_comparison_reopen.reopen_comparison_generation`), and
    this leaf-wide form is what an acceptance reader (ICR-R25's recorded-comparison journey) or a
    curator draining a leaf's history holds. Recording that here is the honest alternative to
    implying a caller that does not exist.
    """

    refs = read_generation_refs(task_root, leaf_id)
    if refs:
        return tuple(
            read_review_sync_rebinding(task_root, leaf_id, ref.generation_id) for ref in refs
        )
    return ()


def discard_review_sync_rebindings(task_root: Path, leaf_id: str) -> tuple[Path, ...]:
    """Remove every rebinding this leaf recorded, and return the locations that were removed.

    The named reclamation owner. A rebinding is a derived measurement of identities the generation's
    own manifest still holds, so removing it costs no retained input: the generation, its snapshots and
    its pins are untouched here, and a discarded record reads back as ``not-recorded`` naming the
    location it looked in rather than as a measurement that agreed.

    **No mounted tool calls this yet**, for the same reason the leaf-wide reader has no caller: the
    records are reclamation-ready derived evidence, and the route that will drain them is the
    acceptance/curation one. Until it does, a discarded record and one that was never written read
    the same at the location -- which is what the reader's sentence says rather than claiming no sync
    ever measured the generation.
    """

    destination = durable_reports_root(task_root)
    removed: list[Path] = []
    prefix = f"{REVIEW_SYNC_REBINDINGS_PREFIX}-{slugify(leaf_id)}-"
    if not destination.is_dir():
        return ()
    for path in sorted(destination.glob(f"{prefix}*.json")):
        path.unlink()
        removed.append(path)
    return tuple(removed)


# -- assembling the record ----------------------------------------------------------------------


def _nothing_to_bind_block(contract: WorktreeContract, payload: dict[str, Any]) -> dict[str, Any]:
    """Why this sync recorded no rebinding, in this block's own vocabulary for the state it saw.

    Three families of answer, and each is a different fact: the transaction carried no pair (the
    state table), the pair is carried but this leaf has no comparison generation to measure against
    it (this block's own generation vocabulary), or the result is not a worktree_sync at all.

    **The generation states are this block's own words, not the selection owner's.** ``unreadable``
    from :func:`select_review_generation` means the *generation manifests* could not be read, while
    ``unreadable`` from :func:`read_review_sync_rebinding` means the *rebinding artifact* could not
    be read -- so publishing the selection's state here would give one key two meanings on two
    surfaces, and the selection's ``detail`` is R21's sentence about a final-output receipt. The
    selection's own state and sentence travel under ``selection_state`` and ``selection_detail``,
    labelled as the generation owner's answer rather than restated as this block's.
    """

    state = str(payload.get("state", ""))
    if payload.get("operation") != "worktree_sync":
        return {
            "state": "not-applicable",
            "detail": (
                f"this result reports operation {payload.get('operation')!r} rather than a "
                "worktree_sync, so it resolved no source/knowledge pair for a review to be measured "
                "against"
            ),
        }
    if not resolved_pair_completed(payload):
        carried_nothing = _CARRIED_NOTHING.get(state)
        if carried_nothing is not None:
            block_state, detail = carried_nothing
            return {"state": block_state, "detail": detail}
        if state in _CARRYING_SYNC_STATES:
            # A carrying state beside a failed result: the transaction reports the state a successful
            # pass would have, and the result itself says the pass did not succeed, so nothing was
            # resolved to measure. Stated as its own fact rather than as an unknown state.
            return {
                "state": "not-measured",
                "detail": (
                    f"the sync reported {state!r} with a failed result, so its pair is not measured "
                    "and no review rebinding is recorded for it"
                ),
            }
        return {
            "state": "not-measured",
            "detail": (
                f"the sync reported {state!r}, which is not one of the states in which this tool "
                "measures a resolved pair, so no review rebinding is recorded for it"
            ),
        }
    if contract.kind != "leaf":
        return {
            "state": "not-applicable",
            "detail": (
                "a series contract records existing commits rather than a leaf's captured candidate, "
                "so no comparison generation is bound to a resolved pair"
            ),
        }
    selection: ReviewGenerationSelection = select_review_generation(
        contract.task_root, contract.leaf_id
    )
    return _no_generation_block(selection)


def _no_generation_block(selection: ReviewGenerationSelection) -> dict[str, Any]:
    """Why a carried pair has no comparison generation to measure against, in R22's own words.

    One entry per selection state, so this block never borrows the reader's ``unreadable`` for the
    generation store's own unreadability and never restates R21's final-output sentence as this
    record's answer. The selection owner's own state and sentence are carried beside it, named for
    the owner that produced them.
    """

    reasons: dict[str, tuple[str, str]] = {
        "no-generation": (
            "no-generation",
            "no comparison generation is published for this leaf, so there is no reviewed comparison "
            "to measure against the pair this sync carried",
        ),
        "ambiguous": (
            "generation-selection-ambiguous",
            "two comparison generations of this leaf claim one recorded index with different "
            "bindings, so no single reviewed comparison exists to measure against the pair this sync "
            "carried",
        ),
        "unreadable": (
            "generation-unreadable",
            "this leaf's comparison-generation directories exist and none holds a readable manifest, "
            "so the reviewed comparison could not be resolved to measure against the pair this sync "
            "carried",
        ),
    }
    state, detail = reasons.get(
        selection.state,
        (
            "no-generation",
            f"the generation store reports {selection.state!r} for this leaf, so no reviewed "
            "comparison could be resolved to measure against the pair this sync carried",
        ),
    )
    return {
        "state": state,
        "detail": detail,
        "selection_state": selection.state,
        "selection_detail": selection.detail,
    }


def _assemble(
    contract: WorktreeContract,
    resolved: FutureCodeCandidateIdentity,
    manifest_digest: str,
    manifest: ComparisonGenerationManifest,
) -> ReviewSyncRebinding:
    """Build the record from the owners' values; it selects nothing and re-derives no reviewed value."""

    knowledge = _resolved_knowledge(contract)
    after = manifest.knowledge_side("after")
    reviewed_digest = None if after.identity is None else after.identity.logical_digest
    code_match = code_channel_match(
        manifest.source.candidate_code_tree_id, resolved.codeCandidateTree
    )
    knowledge_match = knowledge_channel_match(after.state, reviewed_digest, knowledge)
    return ReviewSyncRebinding(
        recorded_at=now_iso(),
        repository_id=contract.repo_name,
        master=contract.parent_task_name or contract.task_name,
        leaf_id=contract.leaf_id,
        task_root=contract.task_root.as_posix(),
        contract_path=contract.contract_path.as_posix(),
        supersedes_generation_id=manifest.generation_id,
        supersedes_generation_index=manifest.generation_index,
        supersedes_binding_digest=manifest.binding_digest,
        supersedes_manifest_digest=manifest_digest,
        reviewed_baseline_code_tree_id=manifest.source.baseline_code_tree_id,
        reviewed_candidate_code_tree_id=manifest.source.candidate_code_tree_id,
        resolved_code_head=resolved.observedCodeHead,
        resolved_candidate_code_tree_id=resolved.codeCandidateTree,
        code_match=code_match,
        reviewed_knowledge_state=after.state,
        reviewed_knowledge_logical_digest=reviewed_digest,
        resolved_knowledge=knowledge,
        knowledge_match=knowledge_match,
        state=review_sync_verdict(code_match, knowledge_match, after.state),
        successor_action=_SUPERSESSION_ACTION,
    )


def _resolved_capture(contract: WorktreeContract) -> _ResolvedCapture:
    """Capture the leaf's candidate after the sync, or name why the owner refused.

    A failure here is the shipped capture owner's own refusal converted into a state: a worktree the
    merge left unable to produce a candidate is precisely the case this record must not report as an
    agreement, and it is a normal outcome of a sync over a dirty worktree rather than a defect.
    """

    try:
        return _ResolvedCapture(
            identity=capture_future_code_candidate(contract),
            state="captured",
            detail="the leaf's candidate was captured after the sync",
        )
    except FutureCodeCandidateError as error:
        return _ResolvedCapture(
            identity=None,
            state="unavailable",
            detail=f"{error.status}: {error}",
        )


def _resolved_knowledge(contract: WorktreeContract) -> SyncKnowledgeObservation:
    """Read the declared publication location through the route a later reader selects it with."""

    location = declared_publication_location(contract)
    resolved = resolve_published_intent(location.context)
    if isinstance(resolved, PublishedIntentUnavailable):
        return SyncKnowledgeObservation(
            state="not-recorded" if resolved.state == "not-recorded" else "unusable",
            path=resolved.dataset_path.as_posix(),
            detail=resolved.detail,
        )
    identity = SnapshotIdentity(
        repository_id=resolved.repository_id,
        schema_version=resolved.schema_version,
        logical_digest=resolved.logical_digest,
    )
    return SyncKnowledgeObservation(
        state="published",
        dataset=identity,
        path=resolved.database_path.as_posix(),
        detail=(
            "a read of the declared publication location holds exactly this dataset "
            f"({identity.logical_digest})"
        ),
    )


def _digest(raw: bytes) -> str:
    """The canonical ``sha256:<hex>`` reference of one published artifact's bytes."""

    return f"sha256:{hashlib.sha256(raw).hexdigest()}"
