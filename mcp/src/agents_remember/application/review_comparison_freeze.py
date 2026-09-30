"""Freezing one live comparison into a durable generation, and reclaiming a partial one.

:mod:`agents_remember.application.review_comparison_generation` owns *what* a durable comparison
generation is -- the manifest, its layout and the reads -- and
:mod:`agents_remember.application.review_comparison_reopen` owns resolving one. This module owns the
act that *produces* a generation, and the reclamation of an attempt that did not.

**Freezing composes owners; it re-implements none of them.**

* the source endpoints come from the review's own resolution (R01) and the capture identity it
  already carries, so no second capture path exists;
* both knowledge halves are copied by the **storage snapshot owner** -- ``freeze_closed_snapshot``
  pins a read transaction and proves the copy is a closed database holding the pinned identity --
  so what is retained is a real dataset rather than a file that happened to be copied;
* the source inventory, the comparison identity and the record collections are the values the review
  composition already produced, carried verbatim;
* the code objects and both knowledge snapshots are retained by
  :mod:`agents_remember.application.review_comparison_retention`, which is the only thing in this
  package that writes a ref.

**Publication is one rename of a fully validated directory.** Everything is staged under a hidden
directory beside its destination, every referenced byte is read back and checked there, and only then
is the stage renamed onto the generation's own name. A failure at any point removes the stage and
releases a pin this call created, so a partial capture is unpublished and reclaimable rather than a
half-generation a reader might resolve.

A stage that survives a *hard* failure -- the process killed between the freeze and the rename, which
no in-process cleanup can cover -- is reclaimed by the next freeze, and only from the dead: each stage
is named for the process that owns it, and the sweep inside the generation root removes a stage whose
owner is gone while leaving a live one alone. That is the bounded reclamation path the packet requires
of temporary storage, and it is scoped to the one leaf directory the record names as its temporary
storage scope. A generation that is already published is never overwritten:
an exact retry converges on the published record, and a different binding under an occupied
generation id is refused.

**Reclamation of a *published* generation is somebody else's act.**
:mod:`agents_remember.application.review_comparison_reclamation` owns releasing a pin and discarding
a retained snapshot, and this module neither calls it nor reaches for a ref or a snapshot of a
generation that is already published. What it does own is the ephemeral state of an attempt that has
not published: a hidden stage is removed on every failure path, and a pin *this call* created is
released again, so a partial capture is reclaimable rather than durable.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from agents_remember.application.knowledge_baseline_generation import GENERATION_VERSION
from agents_remember.application.knowledge_before_half import ORIGIN_VERSION
from agents_remember.application.knowledge_review import compose_review
from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    refusal,
    resolve_review_candidate,
)
from agents_remember.application.review_comparison_generation import (
    COMPARISON_GENERATION_VERSION,
    COMPARISON_GENERATIONS_DIRECTORY,
    COMPARISON_MANIFEST_NAME,
    ComparisonArtifactReference,
    ComparisonGenerationManifest,
    ComparisonGenerationRef,
    ComparisonKnowledgeBinding,
    ComparisonPolicyStamp,
    ComparisonPublicationLineage,
    ComparisonRecordBinding,
    ComparisonScopeBinding,
    ComparisonSourceBinding,
    assemble_manifest,
    generation_directory,
    leaf_generation_root,
    read_manifest,
)
from agents_remember.application.review_comparison_retention import (
    KnowledgeRetentionRequest,
    retain_comparison_source,
    retain_knowledge_sides,
)
from agents_remember.application.review_curator_records import (
    RESERVED_CURATOR_OWNERS,
    require_curator_record_inputs,
)
from agents_remember.application.review_evidence_records import review_records_for_resolution
from agents_remember.application.review_record_rendering import (
    EMPTY_REVIEW_RECORDS,
    ReviewRecordInputs,
)
from agents_remember.errors import CodeObjectRetentionError, CuratorCoherenceError
from agents_remember.kernel.atomic_write import atomic_replace, atomic_write_bytes
from agents_remember.kernel.canonical_json import canonical_json_bytes, sha256_digest
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge.candidate_receipt import read_candidate_receipt
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.diff import DIFF_POLICY_VERSION
from agents_remember.models.knowledge.read import KnowledgeReadSeed
from agents_remember.models.knowledge.review import (
    KNOWLEDGE_REVIEW_SURFACE_VERSION,
    ComparisonIdentity,
    ReviewRefusal,
    ReviewSourceInventory,
    ReviewSurfaceRequest,
)
from agents_remember.models.knowledge.snapshot import CANDIDATE_RECEIPT_NAME
from agents_remember.worktrees.modules.code_object_retention import (
    RetainedCodeObject,
    release_retained_code_object,
)
from agents_remember.worktrees.task_resolver import slugify

__all__ = [
    "EMPTY_FREEZE_OPTIONS",
    "ComparisonEvidenceInput",
    "ComparisonFreezeOptions",
    "ComparisonGenerationFreeze",
    "ComparisonGenerationRequest",
    "freeze_comparison_generation",
    "freeze_resolved_review",
    "freeze_review_comparison",
]

# The one suffix a private stage carries, so the sweep recognises its own kind and nothing else.
_STAGE_SUFFIX = ".stage"

# The refusal codes every failure earns, from the shipped comparison vocabulary.
_ABSENT = "candidate_dataset_absent"
_UNRESOLVED = "candidate_unresolved"
_REFUSED = "comparison_refused"


@dataclass(frozen=True)
class ComparisonEvidenceInput:
    """One owner-produced artifact a caller asks the generation to cite.

    The caller names the owner and the artifact's task-relative path; this module reads the bytes and
    records their digest, which is what makes the citation checkable at reopen. A path that leaves the
    task root, or that does not resolve, is refused while freezing rather than recorded as a citation
    nobody can follow.
    """

    owner: str
    relative_path: str


@dataclass(frozen=True)
class ComparisonFreezeOptions:
    """What a caller may contribute to a freeze beyond the review's own composition.

    An omitted record contribution asks R14 for the actual owners' inputs at this resolved pair.
    Explicit inputs, generic evidence, typed historical absence and publication lineage retain
    their existing meanings. Only explicit recovery may retain its named parent's source capture.
    """

    records: ReviewRecordInputs | None = None
    evidence: tuple[ComparisonEvidenceInput, ...] = ()
    historical_absence: tuple[str, ...] = ()
    parent: ComparisonGenerationRef | None = None
    retain_parent_inputs: bool = False


# The empty contribution, as one value: a default built per call would rebuild the tuple it holds.
EMPTY_FREEZE_OPTIONS = ComparisonFreezeOptions()


@dataclass(frozen=True)
class ComparisonGenerationRequest:
    """Everything one freeze binds, as values other owners produced.

    ``comparison`` is the comparison owner's own identity and is absent for a task-context review,
    which compares no knowledge operand at all. ``historical_absence`` names the halves a caller has
    established carry no recorded generation -- R05's typed absence, declared by whoever can
    establish it and never inferred here -- and it is refused beside bytes that are present, because
    "nothing was recorded" and "here are the bytes" cannot both be true.
    """

    resolution: ReviewCandidateResolution
    inventory: ReviewSourceInventory
    comparison: ComparisonIdentity | None = None
    selector: KnowledgeReadSeed | None = None
    records: ReviewRecordInputs = EMPTY_REVIEW_RECORDS
    evidence: tuple[ComparisonEvidenceInput, ...] = ()
    historical_absence: tuple[str, ...] = ()
    parent: ComparisonGenerationRef | None = None
    retain_parent_inputs: bool = False


@dataclass(frozen=True)
class ComparisonGenerationFreeze:
    """The outcome of one freeze: the published record, or the refusal that stopped it.

    ``reused`` distinguishes the two ways a freeze succeeds. A first publication wrote the record; a
    retry of the same comparison found the generation it would have written and returned that record
    unchanged, because a retry that rewrote an immutable record would make "immutable" a claim about
    intention rather than about bytes.
    """

    state: Literal["published", "refused"]
    manifest: ComparisonGenerationManifest | None = None
    directory: Path | None = None
    manifest_path: Path | None = None
    reused: bool = False
    refusal: ReviewRefusal | None = None

    def published(self) -> bool:
        """Whether this freeze produced a durable generation."""

        return self.state == "published"


@dataclass
class _Staged:
    """The one staging directory a freeze owns, and the pin it created when it created one.

    ``created_pin`` is set only for a pin *this call* wrote, and ``repository`` is the repository that
    pin lives in. An existing pin belongs to a generation that is already published, so reclaiming it
    here would delete a record this call did not make.
    """

    directory: Path
    repository: Path | None = None
    created_pin: RetainedCodeObject | None = None


class _FreezeRefused(Exception):
    """Internal control flow: a freeze stopped, carrying the refusal that stopped it."""

    def __init__(self, refusal_value: ReviewRefusal) -> None:
        super().__init__(refusal_value.detail)
        self.refusal = refusal_value


def freeze_review_comparison(
    config: McpRuntimeConfig,
    request: ReviewSurfaceRequest,
    options: ComparisonFreezeOptions = EMPTY_FREEZE_OPTIONS,
) -> ComparisonGenerationFreeze:
    """Resolve, compose and freeze one review's comparison through the surface's own operations.

    This is the production entry: it resolves the candidate exactly as the surface does, composes the
    review exactly as the surface does -- including the pre-publication recheck that refuses a
    candidate whose captured endpoint moved -- and freezes only what that composition actually bound.
    A refused composition freezes nothing and returns its refusal unchanged, so a generation is never
    published for a comparison the surface declined to make.
    """

    resolved = resolve_review_candidate(
        config, request.repository_id, request.master, request.leaf_id
    )
    if isinstance(resolved, ReviewRefusal):
        return _refused(resolved)
    return freeze_resolved_review(resolved, request, options)


def freeze_resolved_review(
    resolved: ReviewCandidateResolution,
    request: ReviewSurfaceRequest,
    options: ComparisonFreezeOptions = EMPTY_FREEZE_OPTIONS,
) -> ComparisonGenerationFreeze:
    """Compose an explicitly resolved pair and publish through the single generation owner."""

    records = (
        options.records if options.records is not None else review_records_for_resolution(resolved)
    )
    unavailable = next(
        (
            channel
            for channel in records.channels
            if channel.records == "assessments"
            and channel.state == "unavailable"
            and channel.unreadable
        ),
        None,
    )
    if unavailable is not None:
        return _refused(
            refusal(
                _REFUSED,
                unavailable.detail,
                next_action=unavailable.next_action or "restore the expected curator artifacts",
                offending_input="assessments",
            )
        )
    composed = compose_review(resolved, request, records)
    if composed.state != "review" or composed.payload is None:
        return _refused(
            composed.refusal
            or refusal(
                _REFUSED,
                "the review composition returned no payload for this comparison",
                next_action=(
                    "reopen the review; a generation is never frozen for a comparison the surface "
                    "did not make"
                ),
            )
        )
    payload = composed.payload
    return freeze_comparison_generation(
        ComparisonGenerationRequest(
            resolution=resolved,
            inventory=payload.source.inventory,
            comparison=payload.comparison,
            selector=request.selector,
            records=records,
            evidence=options.evidence,
            historical_absence=options.historical_absence,
            parent=options.parent,
            retain_parent_inputs=options.retain_parent_inputs,
        )
    )


def freeze_comparison_generation(
    request: ComparisonGenerationRequest,
) -> ComparisonGenerationFreeze:
    """Retain, validate and publish one immutable comparison generation.

    The order is the contract's: derive and retain the source side, copy both knowledge halves
    through the storage owner, bind the scope, the records, the evidence and the policies, seal the
    record, validate everything referenced, and rename the finished directory into place. Nothing is
    published until every referenced byte has been read back inside the stage.
    """

    resolved = request.resolution
    contract = resolved.contract
    unfreezable = _unfreezable(resolved)
    if unfreezable is not None:
        return _refused(unfreezable)
    assert contract is not None  # ``_unfreezable`` refused a resolution without one
    input_issue = _record_input_refusal(request)
    if input_issue is not None:
        return _refused(input_issue)
    outcome = retain_comparison_source(
        resolved, retained_from=request.parent if request.retain_parent_inputs else None
    )
    if isinstance(outcome, ReviewRefusal):
        return _refused(outcome)
    staged = _stage(contract.task_root, contract.leaf_id)
    staged.repository = outcome.repository
    staged.created_pin = outcome.created_pin
    try:
        return _publish(request, contract.task_root, outcome.binding, staged)
    except _FreezeRefused as stopped:
        return _reclaim(staged, stopped.refusal)
    except (KnowledgeStorageError, OSError) as error:
        # A failure raised *outside* this operation's own control flow -- an unreadable record at the
        # destination, a filesystem that refuses the rename -- is reclaimed exactly like a refusal:
        # the stage is this call's own temporary output and the pin, if this call made one, is not
        # published anywhere. Without this, a hard failure would leave both behind.
        return _reclaim(staged, _storage_refusal(error))


def _record_input_refusal(request: ComparisonGenerationRequest) -> ReviewRefusal | None:
    """Validate immutable owner inputs before any retention writes."""

    if any(item.owner in RESERVED_CURATOR_OWNERS for item in request.evidence):
        return refusal(
            _REFUSED,
            "reserved curator owner pins cannot be supplied as generic evidence",
            offending_input="evidence",
            next_action="Restore the exact retained parent and owner artifacts, then retry the explicitly selected operation; current inputs are not substitutes.",
        )
    try:
        require_curator_record_inputs(
            request.resolution,
            request.records.assessments,
            request.records.artifacts,
            request.records.channels,
        )
    except (CuratorCoherenceError, ValueError, OSError) as error:
        return refusal(
            _REFUSED,
            f"curator inputs could not be retained: {error}",
            offending_input="assessments",
            next_action="Restore the exact retained parent and owner artifacts, then retry the explicitly selected operation; current inputs are not substitutes.",
        )
    if request.retain_parent_inputs and request.parent is None:
        return refusal(
            _REFUSED,
            "retained inputs require an exact parent generation",
            offending_input="parent",
            next_action="Restore the exact retained parent and owner artifacts, then retry the explicitly selected operation; current inputs are not substitutes.",
        )
    return None


def _unfreezable(resolved: ReviewCandidateResolution) -> ReviewRefusal | None:
    """Why a resolution cannot be frozen into a generation, or ``None`` when it can.

    A converted repository's comparison is four Git trees, recorded and pinned when it is resolved
    (MIK-R25): no dataset copy is created or retained for it, so there is nothing to freeze. A
    hand-assembled pair has no task artifact root to publish under.
    """

    if resolved.trees is not None or resolved.knowledge_unavailable:
        return _tree_comparison_refusal()
    if resolved.contract is None or resolved.baseline_code_root is None:
        return _no_task_root_refusal()
    return None


def _tree_comparison_refusal() -> ReviewRefusal:
    """The refusal a freeze of a tree comparison earns: it is recorded by trees, never copied."""

    return refusal(
        _REFUSED,
        "this comparison is four Git trees, recorded with its pinning refs when it was resolved "
        "(MIK-R25); no knowledge dataset is copied or retained for a review",
        next_action=(
            "reopen the comparison from its record under notes/reports/review-comparisons; its "
            "refs are deleted when the task is archived"
        ),
        offending_input="comparison",
    )


def _no_task_root_refusal() -> ReviewRefusal:
    """The refusal for a comparison with no enclosure contract to publish a generation under."""

    return refusal(
        _UNRESOLVED,
        "the comparison to freeze names no enclosure contract, so it has no task artifact root to "
        "publish a durable generation under",
        next_action=(
            "freeze a comparison resolved from canonical task context; a hand-assembled pair of named "
            "files has no task artifact plane to survive in"
        ),
        offending_input="comparison",
    )


def _publish(
    request: ComparisonGenerationRequest,
    task_root: Path,
    source: ComparisonSourceBinding,
    staged: _Staged,
) -> ComparisonGenerationFreeze:
    """Stage every byte, seal the record, and rename the finished directory into place."""

    knowledge = _retained_knowledge(request, staged)
    payload = _manifest_payload(request, task_root, source, knowledge)
    manifest = assemble_manifest(payload, recorded_at=_now())
    final = generation_directory(task_root, manifest.leaf_id, manifest.generation_id)
    if final.exists():
        return _reuse_or_refuse(manifest, final, staged)
    atomic_write_bytes(
        staged.directory / COMPARISON_MANIFEST_NAME,
        canonical_json_bytes(manifest.model_dump(mode="json")),
    )
    atomic_replace(staged.directory, final)
    return ComparisonGenerationFreeze(
        state="published",
        manifest=manifest,
        directory=final,
        manifest_path=final / COMPARISON_MANIFEST_NAME,
    )


def _reuse_or_refuse(
    manifest: ComparisonGenerationManifest, final: Path, staged: _Staged
) -> ComparisonGenerationFreeze:
    """Converge on an already published record, or refuse an occupied generation id.

    The id is derived from the bindings, so an occupied id holding the *same* binding digest is this
    same generation and the published record is returned exactly as it is. An occupied id holding a
    different binding cannot happen from this code and is refused rather than overwritten: the
    directory a reader resolved must never come to describe a different comparison.
    """

    published = read_manifest(final / COMPARISON_MANIFEST_NAME)
    _discard_stage(staged.directory)
    if published.binding_digest == manifest.binding_digest:
        return ComparisonGenerationFreeze(
            state="published",
            manifest=published,
            directory=final,
            manifest_path=final / COMPARISON_MANIFEST_NAME,
            reused=True,
        )
    raise _FreezeRefused(
        refusal(
            _REFUSED,
            f"the generation {manifest.generation_id} is already published with binding "
            f"{published.binding_digest}, while this freeze derived {manifest.binding_digest}",
            next_action=(
                "publish this comparison under its own generation id; an immutable generation is "
                "never overwritten to describe a different comparison"
            ),
            offending_input=manifest.generation_id,
        )
    )


def _retained_knowledge(
    request: ComparisonGenerationRequest, staged: _Staged
) -> tuple[ComparisonKnowledgeBinding, ...]:
    """Retain both halves through the retention owner, or stop the freeze with its refusal."""

    retained = retain_knowledge_sides(
        KnowledgeRetentionRequest(
            resolution=request.resolution,
            historical_absence=request.historical_absence,
        ),
        staged.directory,
    )
    if isinstance(retained, ReviewRefusal):
        raise _FreezeRefused(retained)
    return retained


def _storage_refusal(error: Exception) -> ReviewRefusal:
    """The refusal for a failure the freeze could not classify, named with what it was."""

    return refusal(
        _REFUSED,
        f"the comparison generation could not be published: {error}",
        next_action=(
            "repair the task artifact destination and freeze the comparison again; nothing was "
            "published, the stage was removed, and a pin this call created was released"
        ),
        offending_input="comparison-generations",
    )


def _reclaim(staged: _Staged, refusal_value: ReviewRefusal) -> ComparisonGenerationFreeze:
    """Remove this call's stage and release a pin this call created; then report the refusal.

    The order is deliberate: the ephemeral state is reclaimed first, because a cleanup failure must
    not replace the refusal that says why the freeze stopped -- and the refusal is the only thing the
    caller can act on.
    """

    _discard_stage(staged.directory)
    if staged.created_pin is not None:
        _release_quietly(staged.repository, staged.created_pin)
    return _refused(refusal_value)


# -- the record ---------------------------------------------------------------------------------


def _manifest_payload(
    request: ComparisonGenerationRequest,
    task_root: Path,
    source: ComparisonSourceBinding,
    knowledge: tuple[ComparisonKnowledgeBinding, ...],
) -> dict[str, Any]:
    """Assemble the unsealed, JSON-ready field set of one manifest from the owners' own values.

    Every nested value is dumped through its owner's own model, because the seal is a digest over the
    *stored* encoding: handing the models themselves to the sealer would digest whatever the encoder
    happened to do with them rather than the bytes the record publishes.
    """

    contract = request.resolution.contract
    assert contract is not None
    return {
        "manifest_version": COMPARISON_GENERATION_VERSION,
        "generation_index": 1 if request.parent is None else request.parent.generation_index + 1,
        "repository_id": request.resolution.repository_id,
        "master": contract.parent_task_name or contract.task_name,
        "leaf_id": contract.leaf_id,
        "task_root": str(task_root),
        "temporary_storage_scope": (
            f"{COMPARISON_GENERATIONS_DIRECTORY}/{slugify(contract.leaf_id)}"
        ),
        "contract_path": str(contract.contract_path),
        "source": _json(source),
        "knowledge": [_json(binding) for binding in knowledge],
        "scope": _json(_scope_binding(request)),
        "records": _json(_record_binding(request.records)),
        "evidence": [_json(reference) for reference in _evidence_references(request, task_root)],
        "policies": [_json(stamp) for stamp in _policy_stamps(request.comparison)],
        "lineage": _json(_lineage(request)),
    }


def _json(value: Any) -> Any:
    """One value in the stored encoding, through its owner's own model when it has one."""

    dump = getattr(value, "model_dump", None)
    return value if dump is None else dump(mode="json")


def _scope_binding(request: ComparisonGenerationRequest) -> ComparisonScopeBinding:
    """Bind the selection and R02's own inventory value, digesting the owner's payload verbatim."""

    selector = request.selector
    kind = None if selector is None else (str(getattr(selector, "kind", "") or "") or None)
    return ComparisonScopeBinding(
        selected="subject" if kind is not None else "task-context",
        selector_kind=kind,
        selector_id=_selector_id(selector),
        inventory_state=request.inventory.state,
        inventory_digest=sha256_digest(request.inventory.model_dump(mode="json")),
        changed_path_count=request.inventory.listed_total,
        inventory_partial=request.inventory.partial,
        detail=request.inventory.detail,
    )


def _selector_id(selector: KnowledgeReadSeed | None) -> str | None:
    """The identity an identity seed names, or ``None`` for a seed that names none.

    Read through the seed's own declared field for its kind, so a revision seed -- which selects one
    revision of an identity -- yields no identity this scope claims to have selected.
    """

    if selector is None:
        return None
    for field_name in ("invariant_id", "family_id"):
        value = getattr(selector, field_name, None)
        if isinstance(value, str):
            return value
    return None


def _record_binding(records: ReviewRecordInputs) -> ComparisonRecordBinding:
    """Bind the record collections the composition supplied, as counts and one digest.

    A currentness measurement is deliberately *not* digested: it is a measurement keyed by tuple
    identities, which has no canonical JSON spelling, and inventing one would make the digest depend
    on an encoding this record does not own. Whether a measurement was **performed** is recorded
    instead -- the fact a reader needs -- and what the measurement established remains the projection
    owner's to report. A bundle that carried no measurement, or one that failed, is therefore not
    recorded as measured (``ICR-R15@v1``): the presence of a value is not a measurement.
    """

    measured = records.currentness is not None and records.currentness.state == "measured"
    counts = (len(records.assessments), len(records.signals), len(records.observations))
    supplied = any(counts) or measured
    digest = sha256_digest(
        {
            "assessments": [record.model_dump(mode="json") for record in records.assessments],
            "signals": [record.model_dump(mode="json") for record in records.signals],
            "observations": [record.model_dump(mode="json") for record in records.observations],
        }
    )
    note = ", with a currentness measurement" if measured else ""
    return ComparisonRecordBinding(
        state="supplied" if supplied else "not-supplied",
        assessment_channel=next(
            (channel for channel in records.channels if channel.records == "assessments"), None
        ),
        assessments=counts[0],
        signals=counts[1],
        observations=counts[2],
        current_measured=measured,
        collection_digest=digest,
        detail=(
            f"the composition supplied {counts[0]} assessments, {counts[1]} signals and "
            f"{counts[2]} observations{note}; assessment availability is captured separately only "
            "when its owner supplied a channel"
        ),
    )


def _evidence_references(
    request: ComparisonGenerationRequest, task_root: Path
) -> tuple[ComparisonArtifactReference, ...]:
    """Read and digest every cited artifact, refusing one that does not resolve inside the task root."""

    references = [_evidence_reference(evidence, task_root) for evidence in request.evidence]
    inherited: tuple[ComparisonArtifactReference, ...] = ()
    if request.retain_parent_inputs:
        assert request.parent is not None
        parent = read_manifest(request.parent.directory / COMPARISON_MANIFEST_NAME)
        if parent.manifest_digest() != request.parent.manifest_digest:
            raise _FreezeRefused(
                refusal(
                    _REFUSED,
                    "the retained parent moved before evidence binding",
                    offending_input="parent",
                    next_action="Restore the exact retained parent and owner artifacts, then retry the explicitly selected operation; current inputs are not substitutes.",
                )
            )
        inherited = parent.evidence
    for expected in (*inherited, *request.records.artifacts):
        observed = _evidence_reference(
            ComparisonEvidenceInput(expected.owner, expected.relative_path), task_root
        )
        if observed != expected:
            raise _FreezeRefused(
                refusal(
                    _REFUSED,
                    f"curator artifact moved before comparison publication: {expected.relative_path}",
                    offending_input="assessments",
                    next_action="Restore the exact retained parent and owner artifacts, then retry the explicitly selected operation; current inputs are not substitutes.",
                )
            )
        references.append(expected)
    return tuple(dict.fromkeys(references))


def _evidence_reference(
    evidence: ComparisonEvidenceInput, task_root: Path
) -> ComparisonArtifactReference:
    """One citation, read and digested while freezing, or the refusal that stops the freeze."""

    path = _confined(task_root, evidence.relative_path)
    if path is None:
        raise _FreezeRefused(
            refusal(
                _REFUSED,
                f"the cited artifact {evidence.relative_path} is not inside the task artifact root "
                f"{task_root}",
                next_action="cite an artifact the owner published under the task root",
                offending_input=evidence.relative_path,
            )
        )
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise _FreezeRefused(
            refusal(
                _REFUSED,
                f"the cited artifact {path} could not be read: {error}",
                next_action="publish the artifact before citing it in a comparison generation",
                offending_input=evidence.relative_path,
            )
        ) from error
    return ComparisonArtifactReference(
        owner=evidence.owner,
        relative_path=evidence.relative_path,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_count=len(payload),
    )


def _confined(task_root: Path, relative_path: str) -> Path | None:
    """Resolve one task-relative reference, or ``None`` when it escapes the task root."""

    candidate = Path(relative_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    return Path(task_root) / candidate


def _policy_stamps(comparison: ComparisonIdentity | None) -> tuple[ComparisonPolicyStamp, ...]:
    """Every owner-declared policy version this generation is subject to, named by its owner."""

    return (
        ComparisonPolicyStamp(
            owner="comparison-policy",
            version=DIFF_POLICY_VERSION if comparison is None else comparison.policy_version,
        ),
        ComparisonPolicyStamp(owner="review-surface", version=KNOWLEDGE_REVIEW_SURFACE_VERSION),
        ComparisonPolicyStamp(owner="baseline-origin", version=ORIGIN_VERSION),
        ComparisonPolicyStamp(owner="baseline-generation", version=GENERATION_VERSION),
        ComparisonPolicyStamp(owner="comparison-generation", version=COMPARISON_GENERATION_VERSION),
    )


def _lineage(request: ComparisonGenerationRequest) -> ComparisonPublicationLineage:
    """Where this generation came from: its predecessor and the inputs it descends from."""

    comparison = request.comparison
    return ComparisonPublicationLineage(
        parent_generation_id=None if request.parent is None else request.parent.generation_id,
        parent_manifest_digest=None if request.parent is None else request.parent.manifest_digest,
        comparison_binding_digest=None if comparison is None else comparison.binding_digest,
        candidate_receipt_digest=_receipt_digest(request.resolution),
    )


def _receipt_digest(resolved: ReviewCandidateResolution) -> str | None:
    """The candidate dataset's own admission receipt digest, when one is beside it.

    A receipt that exists but cannot be read is reported as no digest rather than as an error: the
    receipt is the *candidate's* admission record, this feature neither owns it nor depends on it, and
    refusing a freeze over a receipt nothing in the comparison reads would make an unrelated file the
    gate on a durable generation.
    """

    path = resolved.candidate_database.parent / CANDIDATE_RECEIPT_NAME
    if not path.is_file():
        return None
    try:
        return read_candidate_receipt(path).receipt_digest
    except KnowledgeStorageError:
        return None


# -- staging and reclamation --------------------------------------------------------------------


def _stage(task_root: Path, leaf_id: str) -> _Staged:
    """Create the one hidden staging directory a freeze owns, beside its destination.

    Hidden and a sibling of the destination on purpose: the rename that publishes the generation is
    then a same-filesystem rename, and a discovery pass listing generations never sees a stage. The
    name carries this process *and* a fresh uuid, so two concurrent freezes cannot share one stage and
    a stage left behind by a killed process can be told from a live one.
    """

    parent = leaf_generation_root(task_root, leaf_id)
    parent.mkdir(parents=True, exist_ok=True)
    _sweep_stale_stages(parent)
    stage = parent / f".{os.getpid()}-{uuid4().hex}{_STAGE_SUFFIX}"
    stage.mkdir()
    return _Staged(directory=stage)


def _sweep_stale_stages(parent: Path) -> tuple[Path, ...]:
    """Remove the stages of dead freeze processes inside one leaf's generation root.

    Scoped to the directory the record itself names as its temporary storage scope, and bounded to
    entries this feature creates: a hidden directory whose name carries a pid and the stage suffix. A
    stage whose owner is still alive is left alone, so a concurrent freeze never has its work removed
    from under it -- the same "reclaim only from the dead" rule the citation cache follows. Removal
    failures are suppressed: a stage this call cannot remove is reported by the next call, while an
    error here would stop a freeze that has nothing to do with it.
    """

    removed: list[Path] = []
    for entry in sorted(parent.iterdir()):
        if not entry.name.startswith(".") or not entry.name.endswith(_STAGE_SUFFIX):
            continue
        if not entry.is_dir() or _issuer_alive(entry.name):
            continue
        shutil.rmtree(entry, ignore_errors=True)
        if not entry.exists():
            removed.append(entry)
    return tuple(removed)


def _issuer_alive(stage_name: str) -> bool:
    """Whether the process that named one stage is still running.

    A name this feature did not write -- no pid segment, an unparsable one -- is treated as *live*, so
    the sweep never removes something it cannot attribute to a dead freeze.
    """

    head = stage_name.removeprefix(".").split("-", 1)[0]
    if not head.isdigit():
        return True
    pid = int(head)
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        # Permission or an unsupported signal check: the process may exist, and "may exist" means
        # live for a sweep that must not delete another freeze's work.
        return True
    return True


def _discard_stage(stage: Path) -> None:
    """Remove one private stage, without replacing the caller's outcome with a cleanup failure."""

    shutil.rmtree(stage, ignore_errors=True)


def _release_quietly(repository: Path | None, retained: RetainedCodeObject) -> None:
    """Release one pin this call created, on the way out of a freeze that did not publish.

    A failure here is suppressed rather than raised: this runs while a refusal is already being
    reported, and replacing it with a cleanup error would lose the only outcome the caller can act on.
    """

    if repository is None:
        return
    with suppress(CodeObjectRetentionError, OSError):
        release_retained_code_object(
            repository,
            retained,
            reason="the freeze that created this pin did not publish its comparison generation",
            recorded_at=_now(),
        )


def _refused(refusal_value: ReviewRefusal) -> ComparisonGenerationFreeze:
    """One refused freeze, carrying the refusal that names what stopped it."""

    return ComparisonGenerationFreeze(state="refused", refusal=refusal_value)


def _now() -> str:
    """The one clock this module reads, spelled as the stored record expects it."""

    return datetime.now(UTC).isoformat(timespec="seconds")
