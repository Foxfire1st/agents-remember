"""The final-output receipt: one generation beside what closeout and integration delivered (ICR-R21@v1).

A comparison generation (:mod:`agents_remember.application.review_comparison_generation`) records what a
review *read*. It says nothing about what the task then *delivered*, and the two are different facts:
closeout commits a tree and commits external memory, the ordinary publication route installs a dataset
at the repository's declared location, and any of those can differ from the inputs the generation bound
-- the candidate can move between the freeze and the commit, the curator can author records after the
review and publish those, and a publication that never ran leaves no dataset at all. A reader holding
only "a comparison was made" cannot tell whether the historical review opens the pair the task actually
landed, which is what the packet's conforming example requires and its non-conforming example denies.

:mod:`agents_remember.models.knowledge.review_final_output_receipt` owns the record's vocabulary --
:class:`FinalOutputReceipt` itself, its match states and the sentence it publishes -- and this module owns
the operation over it, re-exporting the record so there is one type and one import site:

* **The selection.** :func:`select_review_generation` answers which generation a leaf's final output is
  recorded against: the leaf's **highest recorded generation index**, read from the generation store's
  own ordered refs (a successor names its predecessor in its lineage and carries the higher index).
  Never a fallback to a live branch tip, a current HEAD or today's dataset. No generation, unreadable
  records, and a tie between two bindings on one index are three states, reported as three.

* **The receipt.** :func:`record_final_output_receipt` measures what one phase actually delivered --
  the delivered code commit and its tree, the delivered memory-content commit and its tree, and the
  published knowledge identity read back through the ordinary read route's own owner -- and publishes a
  :class:`FinalOutputReceipt` that states, per channel, whether the delivered output *is* the reviewed
  input. Comparable channels that all match are ``bound``; a channel that differs is ``moved``.
  ``bound`` is never reported for a mismatching code tree or published dataset, because that is how an
  old review comes to be relabelled as covering changed output.

* **The read-back.** :func:`read_final_output_receipt` reads one phase's receipt back beside the
  generations that came after the one it names. A superseded receipt is ``recorded`` with its successors
  named -- retained history that is labelled, never rewritten -- and a present-but-unreadable file is
  ``unreadable`` rather than absent, because "nothing is recorded here" and "the record cannot be read"
  are different facts.

**Recording is not a gate, and nothing here can refuse a transaction.** The transaction owners call
:func:`final_output_result_block` with the commits they already created. A closeout or an integration
that cannot record a receipt still completes, and its result carries the exact reason no receipt was
recorded instead of a receipt nobody could have produced: the packet adds no mandatory review gate to
closeout, and a recorder that could block the Git transaction it describes would be that gate wearing a
receipt's name.

**Movement is recorded, never papered over.** The packet requires a new generation or an explicit
supersession when a selected input moves; this module's half of that is the honest ``moved`` receipt plus
the remedy its statement names: publish a successor generation naming this one as its predecessor.
Freezing one is :func:`agents_remember.application.review_comparison_freeze.freeze_review_comparison`,
whose options already accept the predecessor ref, so the successor's own lineage makes the supersession a
value a reader resolves rather than a claim. **Reclamation:** one file per (leaf, generation, phase) under
the shipped durable reports root, so a phase measured twice converges on one location, and
:func:`discard_final_output_receipts` is the named owner that removes a leaf's receipts -- derived
measurements rather than retained inputs, so a removed one reads back as ``not-recorded`` naming the
location it looked in, while the generation's own manifests and retained bytes are untouched here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from agents_remember.application.knowledge_publication_route import declared_publication_location
from agents_remember.application.published_intent import (
    PublishedIntentUnavailable,
    resolve_published_intent,
)
from agents_remember.application.review_comparison_generation import (
    COMPARISON_MANIFEST_NAME,
    ComparisonGenerationManifest,
    ComparisonGenerationRef,
    generation_directories,
    read_generation_refs,
    read_manifest,
)
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
from agents_remember.models.knowledge.review_final_output_receipt import (
    FINAL_OUTPUT_RECEIPT_VERSION,
    FINAL_OUTPUT_SELECTION_RULE,
    FinalOutputPhase,
    FinalOutputReceipt,
    FinalOutputVerdict,
    MatchState,
    PublishedKnowledgeState,
    code_match_state,
    final_output_verdict,
    knowledge_match_state,
)
from agents_remember.observer.events import now_iso
from agents_remember.worktrees import route_review
from agents_remember.worktrees.modules.git import require_git
from agents_remember.worktrees.task_resolver import slugify
from agents_remember.worktrees.worktree_contract import WorktreeContract

__all__ = [
    "FINAL_OUTPUT_RECEIPTS_PREFIX",
    "FINAL_OUTPUT_RECEIPT_VERSION",
    "FINAL_OUTPUT_SELECTION_RULE",
    "FinalOutputPhase",
    "FinalOutputReceipt",
    "FinalOutputReceiptPublication",
    "FinalOutputReceiptRead",
    "FinalOutputVerdict",
    "MatchState",
    "PublishedKnowledgeState",
    "ReviewGenerationSelection",
    "attach_closeout_receipt",
    "attach_integration_receipt",
    "attach_prepared_selection",
    "discard_final_output_receipts",
    "final_output_result_block",
    "final_output_selection_block",
    "read_final_output_receipt",
    "read_final_output_receipts",
    "receipt_file_name",
    "record_final_output_receipt",
    "select_review_generation",
]

# The file-name prefix every receipt is published under, inside the shipped durable reports root:
# one file per leaf, generation and phase, `final-output-<leaf>-<generation-id>-<phase>.json`.
FINAL_OUTPUT_RECEIPTS_PREFIX = "final-output"


@dataclass(frozen=True)
class ReviewGenerationSelection:
    """Which generation a leaf's final output is recorded against, or why none could be selected.

    ``no-generation`` means the leaf published nothing -- a task that was never reviewed is not a task
    whose review was lost; ``unreadable`` means generation directories exist and none of their manifests
    could be read; and ``ambiguous`` is the tie the reopen owner also refuses, two generations claiming
    one index with different bindings, where binding either chooses a comparison the record does not.
    """

    state: Literal["selected", "no-generation", "unreadable", "ambiguous"]
    leaf_id: str
    ref: ComparisonGenerationRef | None
    detail: str


@dataclass(frozen=True)
class FinalOutputReceiptPublication:
    """One published receipt: the durable artifact, the record, and the read-back that proves it."""

    publication: DurableEvidencePublication
    receipt: FinalOutputReceipt
    read_back: EvidenceReadBack


@dataclass(frozen=True)
class FinalOutputReceiptRead:
    """What reading one phase's receipt back found, beside the generations that superseded it.

    ``superseded_by`` is measured from the store at read time rather than written into the receipt: a
    supersession is published by freezing a successor that names its predecessor in its lineage, and a
    receipt rewritten to point at it would be a record edited after the fact.
    """

    state: Literal["recorded", "not-recorded", "unreadable"]
    leaf_id: str
    phase: FinalOutputPhase
    destination: Path
    receipt: FinalOutputReceipt | None
    sha256: str | None
    superseded_by: tuple[ComparisonGenerationRef, ...]
    detail: str


# -- the selection ------------------------------------------------------------------------------


def select_review_generation(task_root: Path, leaf_id: str) -> ReviewGenerationSelection:
    """Return the generation one leaf's final output is recorded against.

    The rule is the generation store's own order: the highest recorded ``generation_index``, ties broken
    by the ``(index, id)`` ordering the store publishes. A supersession is a successor with a higher
    index naming its predecessor, so the durable record already answers this -- no live branch, no
    working tree and no clock participates.
    """

    refs = read_generation_refs(task_root, leaf_id)
    if refs:
        return _selected(refs, leaf_id)
    directories = generation_directories(task_root, leaf_id)
    if directories:
        return ReviewGenerationSelection(
            state="unreadable",
            leaf_id=leaf_id,
            ref=None,
            detail=(
                f"{len(directories)} generation director(y/ies) exist for {leaf_id} and none holds a "
                "readable manifest, so no generation could be selected and none is named"
            ),
        )
    return ReviewGenerationSelection(
        state="no-generation",
        leaf_id=leaf_id,
        ref=None,
        detail=(
            f"no comparison generation is published for {leaf_id}, so there is no reviewed generation "
            "to record a final output against"
        ),
    )


def _selected(refs: tuple[ComparisonGenerationRef, ...], leaf_id: str) -> ReviewGenerationSelection:
    """The highest-index ref, or the ambiguous state when two bindings claim that index."""

    latest = refs[-1]
    tied = [
        ref
        for ref in refs
        if ref.generation_index == latest.generation_index
        and ref.binding_digest != latest.binding_digest
    ]
    if tied:
        return ReviewGenerationSelection(
            state="ambiguous",
            leaf_id=leaf_id,
            ref=None,
            detail=(
                f"two generations of {leaf_id} claim index {latest.generation_index} with different "
                f"bindings ({', '.join(ref.generation_id for ref in tied)} and "
                f"{latest.generation_id}), so no one generation is selected"
            ),
        )
    return ReviewGenerationSelection(
        state="selected",
        leaf_id=leaf_id,
        ref=latest,
        detail=(
            f"generation {latest.generation_id} carries the highest recorded index "
            f"({latest.generation_index}) of the {len(refs)} readable generation(s) this leaf "
            "published, so it is the generation this leaf's final output is to be recorded against"
        ),
    )


# -- recording ----------------------------------------------------------------------------------


def receipt_file_name(leaf_id: str, generation_id: str, phase: FinalOutputPhase) -> str:
    """Return the one durable file name one phase's receipt for one generation is published under."""

    return f"{FINAL_OUTPUT_RECEIPTS_PREFIX}-{slugify(leaf_id)}-{generation_id}-{phase}.json"


def record_final_output_receipt(
    contract: WorktreeContract,
    *,
    phase: FinalOutputPhase,
    code_commit: str,
    memory_content_commit: str,
) -> FinalOutputReceiptPublication:
    """Measure one phase's delivered output against the leaf's selected generation and publish it.

    The measurements are the owners' own: the delivered trees come from the repositories holding the
    commits the transaction already created, and the published knowledge identity is what
    ``resolve_published_intent`` answers where ``declared_publication_location`` resolves -- the read
    route a later task's planner uses. A phase measured twice converges on one file and the later
    measurement replaces the earlier one, because the record says what that phase delivered when it was
    read; ``replaced_existing`` reports the replacement rather than hiding it. Whatever the owners raise
    is raised here: a caller that must not be blocked by its own recorder calls
    :func:`final_output_result_block`, which reports such a failure as a state instead.
    """

    selection = select_review_generation(contract.task_root, contract.leaf_id)
    if selection.state != "selected" or selection.ref is None:
        raise RuntimeError(selection.detail)
    ref = selection.ref
    manifest = read_manifest(ref.directory / COMPARISON_MANIFEST_NAME)
    receipt = _assemble_receipt(
        _ReceiptInputs(
            contract=contract,
            phase=phase,
            ref=ref,
            manifest=manifest,
            published=_published_knowledge(contract),
        ),
        _delivered_output(contract, code_commit, memory_content_commit),
    )
    publication = publish_durable_evidence(
        contract.task_root,
        receipt_file_name(contract.leaf_id, manifest.generation_id, phase),
        canonical_json_bytes(receipt.model_dump(mode="json")).decode("utf-8"),
    )
    return FinalOutputReceiptPublication(
        publication=publication, receipt=receipt, read_back=read_back_evidence(publication)
    )


@dataclass(frozen=True)
class _PublishedKnowledge:
    """What the ordinary read route found at the repository's declared publication location."""

    state: PublishedKnowledgeState
    identity: SnapshotIdentity | None
    path: str
    detail: str


def _published_knowledge(contract: WorktreeContract) -> _PublishedKnowledge:
    """Read the declared publication location through the route a later reader selects it with."""

    resolved = resolve_published_intent(declared_publication_location(contract).context)
    if isinstance(resolved, PublishedIntentUnavailable):
        return _PublishedKnowledge(
            state="not-recorded" if resolved.state == "not-recorded" else "unusable",
            identity=None,
            path=resolved.dataset_path.as_posix(),
            detail=resolved.detail,
        )
    identity = SnapshotIdentity(
        repository_id=resolved.repository_id,
        schema_version=resolved.schema_version,
        logical_digest=resolved.logical_digest,
    )
    return _PublishedKnowledge(
        state="published",
        identity=identity,
        path=resolved.database_path.as_posix(),
        detail=(
            "a read of the declared publication location holds exactly this dataset "
            f"({identity.logical_digest})"
        ),
    )


@dataclass(frozen=True)
class _ReceiptInputs:
    """What one receipt is assembled from: the selected generation, its phase, and its author's facts."""

    contract: WorktreeContract
    phase: FinalOutputPhase
    ref: ComparisonGenerationRef
    manifest: ComparisonGenerationManifest
    published: _PublishedKnowledge


@dataclass(frozen=True)
class _DeliveredOutput:
    """The identities one phase delivered, read out of the repositories that hold them."""

    code_commit: str
    code_tree: str
    memory_content_commit: str | None
    memory_tree: str | None


def _delivered_output(
    contract: WorktreeContract, code_commit: str, memory_content_commit: str
) -> _DeliveredOutput:
    """Read the trees of the commits one phase created; an absent memory commit means no output."""

    memory_tree = (
        _commit_tree(contract.memory_repo_path, memory_content_commit)
        if contract.memory_repo_path is not None and memory_content_commit
        else None
    )
    return _DeliveredOutput(
        code_commit=code_commit,
        code_tree=_commit_tree(contract.code_repo_path, code_commit),
        memory_content_commit=memory_content_commit if memory_tree is not None else None,
        memory_tree=memory_tree,
    )


def _assemble_receipt(inputs: _ReceiptInputs, delivered: _DeliveredOutput) -> FinalOutputReceipt:
    """Build the receipt from the owners' values; it selects nothing and re-derives no identity."""

    contract, manifest, published = inputs.contract, inputs.manifest, inputs.published
    after = manifest.knowledge_side("after")
    reviewed_digest = None if after.identity is None else after.identity.logical_digest
    published_digest = None if published.identity is None else published.identity.logical_digest
    code_verdict = code_match_state(manifest.source.candidate_code_tree_id, delivered.code_tree)
    knowledge_verdict = knowledge_match_state(
        after.state, reviewed_digest, published.state, published_digest
    )
    return FinalOutputReceipt(
        phase=inputs.phase,
        recorded_at=now_iso(),
        repository_id=contract.repo_name,
        master=contract.task_name,
        leaf_id=contract.leaf_id,
        task_root=contract.task_root.as_posix(),
        contract_path=contract.contract_path.as_posix(),
        generation_id=manifest.generation_id,
        generation_index=manifest.generation_index,
        binding_digest=manifest.binding_digest,
        manifest_digest=inputs.ref.manifest_digest,
        reviewed_baseline_code_tree_id=manifest.source.baseline_code_tree_id,
        reviewed_candidate_code_tree_id=manifest.source.candidate_code_tree_id,
        delivered_code_commit=delivered.code_commit,
        delivered_code_tree_id=delivered.code_tree,
        code_match=code_verdict,
        memory_output_state="recorded" if delivered.memory_tree is not None else "not-recorded",
        delivered_memory_content_commit=delivered.memory_content_commit,
        delivered_memory_tree_id=delivered.memory_tree,
        reviewed_knowledge_state=after.state,
        reviewed_knowledge_logical_digest=reviewed_digest,
        published_knowledge_state=published.state,
        published_knowledge=published.identity,
        published_knowledge_path=published.path,
        published_knowledge_detail=published.detail,
        knowledge_match=knowledge_verdict,
        state=final_output_verdict(code_verdict, knowledge_verdict, after.state),
    )


def _commit_tree(repository: Path, commit: str) -> str:
    """The tree one commit names, read out of the repository that holds it."""

    return require_git(repository, ["rev-parse", f"{commit}^{{tree}}"])


# -- reading the record back --------------------------------------------------------------------


def read_final_output_receipt(
    task_root: Path, leaf_id: str, generation_id: str, phase: FinalOutputPhase
) -> FinalOutputReceiptRead:
    """Read one phase's receipt back, beside the generations that came after the one it names.

    Returned as a state rather than raised, following the reopen owner's convention: a caller asking a
    leaf what its final output was is entitled to "nothing is recorded there" or "the record cannot be
    read" rather than an exception. The states stay distinct -- a present-but-unreadable file is never
    reported as absent -- and a superseded receipt is ``recorded`` with its successors named.

    **The consuming route is the reopen owner.**
    :func:`~agents_remember.application.review_comparison_reopen.reopen_comparison_generation` calls
    :func:`read_final_output_receipts` and reports what each phase recorded as part of reopening one
    generation, which is how the recorded comparison identifies the output its task delivered; the
    closed-leaf review route (:mod:`agents_remember.application.review_committed_leaf`) reaches that
    reopen, so this reader is not a dead API. A caller that holds a ``FinalOutputReceipt`` already --
    the transaction result itself -- compares the record it was handed instead of re-reading it.
    """

    return read_final_output_receipts(task_root, leaf_id, generation_id, phases=(phase,))[0]


def read_final_output_receipts(
    task_root: Path,
    leaf_id: str,
    generation_id: str,
    *,
    phases: tuple[FinalOutputPhase, ...] = ("closeout", "integration"),
) -> tuple[FinalOutputReceiptRead, ...]:
    """Read every phase of one generation's final output, in phase order.

    The plural form is what the reopen owner consumes, and it exists so one reopen measures the
    supersession set once: the phases a generation recorded are a small, fixed list, and a reader that
    asked per phase would re-scan the generation store once per phase for the same answer.
    """

    refs = read_generation_refs(task_root, leaf_id)
    superseded_by = _superseding_in(refs, generation_id)
    reads: list[FinalOutputReceiptRead] = []
    for phase in phases:
        destination = durable_reports_root(task_root) / receipt_file_name(
            leaf_id, generation_id, phase
        )
        read = _read_destination(destination, phase, superseded_by)
        reads.append(
            FinalOutputReceiptRead(
                state=read.state,
                leaf_id=leaf_id,
                phase=phase,
                destination=destination,
                receipt=read.receipt,
                sha256=read.sha256,
                superseded_by=superseded_by,
                detail=read.detail,
            )
        )
    return tuple(reads)


@dataclass(frozen=True)
class _ReadDestination:
    """One read of one receipt destination: its state, the record if any, and the sentence."""

    state: Literal["recorded", "not-recorded", "unreadable"]
    receipt: FinalOutputReceipt | None
    sha256: str | None
    detail: str


def _read_destination(
    destination: Path, phase: FinalOutputPhase, superseded_by: tuple[ComparisonGenerationRef, ...]
) -> _ReadDestination:
    """Read one destination: absent, unreadable, or the record it holds beside its supersessions."""

    if not destination.is_file():
        return _ReadDestination(
            state="not-recorded",
            receipt=None,
            sha256=None,
            detail=(
                f"no {phase} final-output receipt is present at {destination}, so no final output for "
                "that phase is recorded at that location"
            ),
        )
    try:
        content = destination.read_bytes()
        receipt = FinalOutputReceipt.model_validate(decoded_json(content.decode("utf-8")))
    except (OSError, UnicodeDecodeError, ValueError, ValidationError) as error:
        return _ReadDestination(
            state="unreadable",
            receipt=None,
            sha256=None,
            detail=(
                f"the {phase} final-output receipt at {destination} could not be read as a receipt: "
                f"{error}"
            ),
        )
    successors = (
        f", and {len(superseded_by)} later generation(s) of this leaf supersede it"
        if superseded_by
        else ", and no later generation of this leaf supersedes it"
    )
    return _ReadDestination(
        state="recorded",
        receipt=receipt,
        sha256=hashlib.sha256(content).hexdigest(),
        detail=(
            f"the {phase} receipt at {destination} records generation {receipt.generation_id} as "
            f"{receipt.state}{successors}"
        ),
    )


def _superseding_in(
    refs: tuple[ComparisonGenerationRef, ...], generation_id: str
) -> tuple[ComparisonGenerationRef, ...]:
    """Every readable generation in ``refs`` that came after the one a receipt names."""

    selected = next((ref for ref in refs if ref.generation_id == generation_id), None)
    if selected is None:
        return ()
    return tuple(ref for ref in refs if ref.generation_index > selected.generation_index)


def discard_final_output_receipts(task_root: Path, leaf_id: str) -> tuple[Path, ...]:
    """Remove every final-output receipt published for one leaf, and return what was removed.

    The named reclamation owner for this module's durable output: one file per (leaf, generation, phase),
    rewritten in place when a phase is measured again, so a leaf accumulates at most two files per
    generation it publishes and this call removes all of them. Receipts are derived measurements rather
    than retained inputs, so removing one needs no unavailable-history record -- a later read reports
    ``not-recorded`` naming the location it looked in, which stays true about the store -- while the
    generation's own manifests and retained bytes are not touched here at all.

    **Where it is called from, stated plainly.** No shipped route deletes a receipt today, and that is
    the same shape the generation owner itself landed
    (:mod:`agents_remember.application.review_comparison_reclamation` is likewise the named owner of a
    release that no automatic caller performs): the record is *retained evidence* -- ICR-R21@v1 exists
    so a later reader can open the comparison the task actually landed -- and deleting it during the
    ordinary closeout/cleanup would destroy the very artifact the requirement asks to survive. The
    consumers are therefore an explicit retention/release pass and the acceptance corridor that
    measures reclamation (ICR-R25@v1); this function is the one implementation they call, and it is
    routed as a debt in this leaf's report rather than left as an undocumented dead API.
    """

    directory = durable_reports_root(task_root)
    if not directory.is_dir():
        return ()
    removed: list[Path] = []
    for path in sorted(directory.glob(f"{FINAL_OUTPUT_RECEIPTS_PREFIX}-{slugify(leaf_id)}-*.json")):
        if path.is_file():
            path.unlink()
            removed.append(path)
    return tuple(removed)


# -- what the transaction owners put in their results -------------------------------------------


def final_output_selection_block(
    contract: WorktreeContract, prepared_candidate_tree: str | None
) -> dict[str, Any]:
    """The read-only projection a prepared-work owner reports: which generation the closeout binds.

    Nothing is written and nothing is refused here. ``prepared_is_reviewed_candidate`` is the one
    comparison the block makes, and it is ``None`` when either side is unknown rather than ``True``: a
    prepared candidate that was never measured, and a leaf with no selected generation, are not a
    prepared candidate that agrees with the review.
    """

    if contract.kind != "leaf":
        return {
            "state": "not-applicable",
            "detail": (
                "a series contract records existing commits rather than a leaf's comparison "
                "generation, so no prepared candidate is bound to one"
            ),
        }
    selection = select_review_generation(contract.task_root, contract.leaf_id)
    block: dict[str, Any] = {
        "state": selection.state,
        "leaf_id": selection.leaf_id,
        "selection_rule": FINAL_OUTPUT_SELECTION_RULE,
        "generation_id": None,
        "generation_index": None,
        "binding_digest": None,
        "manifest_digest": None,
        "reviewed_candidate_code_tree_id": None,
        "prepared_candidate_code_tree_id": prepared_candidate_tree,
        "prepared_is_reviewed_candidate": None,
        "detail": selection.detail,
    }
    if selection.state != "selected" or selection.ref is None:
        return block
    ref = selection.ref
    block.update(
        {
            "generation_id": ref.generation_id,
            "generation_index": ref.generation_index,
            "binding_digest": ref.binding_digest,
            "manifest_digest": ref.manifest_digest,
        }
    )
    manifest = _selected_manifest(selection, block)
    if manifest is None:
        return block
    reviewed = manifest.source.candidate_code_tree_id
    block["reviewed_candidate_code_tree_id"] = reviewed
    if prepared_candidate_tree is None:
        return block
    matches = prepared_candidate_tree == reviewed
    block["prepared_is_reviewed_candidate"] = matches
    block["detail"] = (
        f"{selection.detail}; the prepared candidate tree {prepared_candidate_tree} "
        + (
            "is the reviewed candidate tree"
            if matches
            else f"is not the reviewed candidate tree {reviewed}, so this closeout would deliver "
            "code the selected generation did not review"
        )
    )
    return block


def _selected_manifest(
    selection: ReviewGenerationSelection, block: dict[str, Any]
) -> ComparisonGenerationManifest | None:
    """Read the selected generation's manifest, reporting an unreadable one in the block itself."""

    assert selection.ref is not None
    try:
        return read_manifest(selection.ref.directory / COMPARISON_MANIFEST_NAME)
    except (OSError, ValueError) as error:
        block["state"] = "unreadable"
        block["detail"] = f"{selection.detail}; its manifest could not be read back: {error}"
        return None


def attach_prepared_selection(
    payload: dict[str, Any], contract: WorktreeContract
) -> dict[str, Any]:
    """Carry the selected comparison generation on the prepared-closeout result (ICR-R21@v1).

    A read-only projection of the durable generation store: which generation this closeout would
    bind, and whether the prepared candidate is the tree that generation reviewed. It refuses
    nothing, so a leaf that never froze a comparison previews exactly as it did.
    """

    if not payload.get("ok"):
        return payload
    prepared = route_review.code_candidate_tree(contract) if contract.kind == "leaf" else None
    payload["final_comparison_selection"] = final_output_selection_block(contract, prepared)
    return payload


def attach_closeout_receipt(payload: dict[str, Any], contract: WorktreeContract) -> dict[str, Any]:
    """Record the closeout's final-output receipt on its own result (ICR-R21@v1).

    Recorded after the Git transaction finished and its contract was written, so it never
    participates in the transaction's admission: a receipt that cannot be produced is reported as
    ``not-recorded`` and the completed closeout is returned unchanged.
    """

    code_commit = payload.get("code_commit")
    if not payload.get("ok") or not code_commit:
        return payload
    payload["final_output_receipt"] = final_output_result_block(
        contract,
        phase="closeout",
        code_commit=str(code_commit),
        memory_content_commit=str(payload.get("memory_content_commit") or ""),
    )
    return payload


def attach_integration_receipt(
    payload: dict[str, Any], contract: WorktreeContract
) -> dict[str, Any]:
    """Attach the integration phase's receipt to a landing result (ICR-R21@v1).

    The landed refs are read again here, after the move, and the receipt records them. An integration
    that did not land -- a blocked or refused one -- carries nothing to record, and nothing is
    recorded: a receipt is a claim about an output that exists.
    """

    code_commit = payload.get("integrated_code_commit")
    if not payload.get("ok") or not code_commit:
        return payload
    payload["final_output_receipt"] = final_output_result_block(
        contract,
        phase="integration",
        code_commit=str(code_commit),
        memory_content_commit=str(payload.get("integrated_memory_content_commit") or ""),
    )
    return payload


def final_output_result_block(
    contract: WorktreeContract,
    *,
    phase: FinalOutputPhase,
    code_commit: str,
    memory_content_commit: str,
) -> dict[str, Any]:
    """Record one phase's receipt for a transaction result, reporting every failure as a state.

    The transaction owners' entry point, and it never raises: three states, each saying only what
    happened -- ``recorded`` with the receipt's identities and the read-back that proves the bytes,
    ``not-recorded`` with the concrete reason (no selected generation, an unreadable store, a
    destination that refused the write), and ``not-applicable`` for a series contract, which owns no
    leaf comparison generation at all.
    """

    if contract.kind != "leaf":
        return {
            "state": "not-applicable",
            "phase": phase,
            "detail": (
                "a series contract records existing commits rather than a leaf's comparison "
                "generation, so no final-output receipt is recorded for it"
            ),
        }
    try:
        recorded = record_final_output_receipt(
            contract,
            phase=phase,
            code_commit=code_commit,
            memory_content_commit=memory_content_commit,
        )
    except (KnowledgeStorageError, ValidationError, OSError, RuntimeError, ValueError) as error:
        return {
            "state": "not-recorded",
            "phase": phase,
            "leaf_id": contract.leaf_id,
            "detail": (
                f"the {phase} final-output receipt was not recorded, so this result claims no binding "
                f"between the leaf's comparison generation and the delivered output: {error}"
            ),
        }
    return _recording_block(phase, recorded)


def _recording_block(
    phase: FinalOutputPhase, recorded: FinalOutputReceiptPublication
) -> dict[str, Any]:
    """Project one published receipt into the transaction result's own payload block."""

    receipt = recorded.receipt
    return {
        "state": "recorded",
        "phase": phase,
        "leaf_id": receipt.leaf_id,
        "selection_rule": receipt.selection_rule,
        "generation_id": receipt.generation_id,
        "generation_index": receipt.generation_index,
        "binding_digest": receipt.binding_digest,
        "manifest_digest": receipt.manifest_digest,
        "receipt_state": receipt.state,
        "code_match": receipt.code_match,
        "knowledge_match": receipt.knowledge_match,
        "delivered_code_commit": receipt.delivered_code_commit,
        "delivered_code_tree_id": receipt.delivered_code_tree_id,
        "delivered_memory_content_commit": receipt.delivered_memory_content_commit,
        "delivered_memory_tree_id": receipt.delivered_memory_tree_id,
        "published_knowledge_state": receipt.published_knowledge_state,
        "published_knowledge_digest": (
            None
            if receipt.published_knowledge is None
            else receipt.published_knowledge.logical_digest
        ),
        "statement": receipt.statement(),
        "destination": recorded.publication.destination.as_posix(),
        "sha256": recorded.publication.sha256,
        "replaced_existing_receipt": recorded.publication.replaced_existing,
        "read_back": recorded.read_back.state,
    }
