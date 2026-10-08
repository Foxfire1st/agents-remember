"""Bind and read the existing curator generation for one comparison, without a second record store."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from agents_remember.application.review_candidate_resolution import ReviewCandidateResolution
from agents_remember.application.review_comparison_generation import (
    ComparisonArtifactReference,
    ComparisonGenerationManifest,
)
from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH
from agents_remember.models.knowledge.review_records import (
    ReviewRecordChannel,
    ReviewRecordChannelState,
)
from agents_remember.models.lifecycles.curator_coherence import ValidatedCuratorCoherenceGeneration
from agents_remember.models.lifecycles.review_assessment import ReviewAssessment
from agents_remember.worktrees.integration.closeout.curator_coherence import (
    CuratorCoherenceError,
    curator_coherence_paths,
    load_curator_coherence_authority,
    load_curator_coherence_generation,
)

CURATOR_RECORD_OWNER = "curator-coherence-record/v1"
CURATOR_ARTIFACT_OWNER = "curator-coherence-artifact/v1"
RESERVED_CURATOR_OWNERS = frozenset({CURATOR_RECORD_OWNER, CURATOR_ARTIFACT_OWNER})
_OWNER = "curator_coherence.load_curator_coherence_generation"


@dataclass(frozen=True)
class CuratorAssessmentRecords:
    assessments: tuple[ReviewAssessment, ...]
    channel: ReviewRecordChannel
    artifacts: tuple[ComparisonArtifactReference, ...] = ()


def review_curator_records(resolved: ReviewCandidateResolution) -> CuratorAssessmentRecords:
    """A historical review reads only its bound owner; a live read asks the current authority."""

    contract = resolved.contract
    assert contract is not None
    expected_artifacts: tuple[str, ...] = ()
    try:
        if resolved.closed_leaf is not None:
            closed = resolved.closed_leaf
            expected_artifacts = tuple(
                channel.relative_path
                for channel in closed.reopened.evidence
                if channel.owner in RESERVED_CURATOR_OWNERS and channel.state != "available"
            )
            return _historical_records(resolved)
        paths = curator_coherence_paths(contract)
        if not paths.canonical.is_file():
            return _result(
                "none_recorded",
                f"The live curator authority {paths.canonical.name} is absent; no current authority is recorded for this candidate.",
            )
        expected_artifacts = (paths.canonical.name,)
        validated = load_curator_coherence_authority(contract)
        return _generation_records(resolved, validated)
    except (CuratorCoherenceError, OSError, ValueError) as error:
        return _result(
            "unavailable",
            _unreadable_detail(expected_artifacts, error),
            unreadable=expected_artifacts,
        )


# A closed leaf can bind about a hundred owner artifacts (ICR-L47 binds 94). The channel's
# `unreadable` field lists every one. The detail keeps its landed wording whenever it fits the prose
# bound; only a detail that would not fit names the count and the first few artifacts and bounds the
# owner's own error, so a missing generation is reported as `unavailable` instead of failing the
# whole review on the detail's length (MIK-L25 Q8, fixed in MIK-L31).
_NAMED_ARTIFACTS = 3
_ERROR_CHARACTERS = 4_000


def _unreadable_detail(artifacts: tuple[str, ...], error: Exception) -> str:
    named = ", ".join(artifacts) or "authority not located"
    detail = f"The curator owner could not be read ({named}): {error}"
    if len(detail) <= PROSE_MAX_LENGTH:
        return detail
    return (
        f"The curator owner could not be read ({_artifacts_named(artifacts)}): "
        f"{_bounded(str(error))}"
    )


def _artifacts_named(artifacts: tuple[str, ...]) -> str:
    if not artifacts:
        return "authority not located"
    if len(artifacts) <= _NAMED_ARTIFACTS:
        return ", ".join(artifacts)
    shown = ", ".join(artifacts[:_NAMED_ARTIFACTS])
    return f"{len(artifacts)} bound artifacts, listed as unreadable; the first are {shown}"


def _bounded(text: str) -> str:
    if len(text) <= _ERROR_CHARACTERS:
        return text
    return f"{text[:_ERROR_CHARACTERS]}… ({len(text) - _ERROR_CHARACTERS} more characters)"


def records_from_curator_generation(
    resolved: ReviewCandidateResolution, record_digest: str
) -> CuratorAssessmentRecords:
    """Explicit recovery input; never selected automatically when a historical pin is missing."""

    contract = resolved.contract
    assert contract is not None
    return _generation_records(resolved, load_curator_coherence_generation(contract, record_digest))


def _historical_records(resolved: ReviewCandidateResolution) -> CuratorAssessmentRecords:
    closed = resolved.closed_leaf
    assert closed is not None
    manifest = closed.manifest
    pins = (
        ()
        if manifest is None
        else tuple(ref for ref in manifest.evidence if ref.owner == CURATOR_RECORD_OWNER)
    )
    if not pins:
        return _without_pin(manifest)
    if len(pins) != 1:
        raise ValueError(
            "This generation has competing curator owner references; no record was selected."
        )
    assert manifest is not None
    pin = pins[0]
    _require_historical_pin(resolved, pin)
    supplied = records_from_curator_generation(resolved, pin.sha256)
    references = tuple(ref for ref in manifest.evidence if ref.owner in RESERVED_CURATOR_OWNERS)
    if set(references) != set(supplied.artifacts):
        raise ValueError(
            "The retained curator artifact set differs from the immutable owner's declared evidence."
        )
    if len(supplied.assessments) != manifest.records.assessments:
        raise ValueError(
            "The pinned curator collection does not match the frozen assessment count."
        )
    return supplied


def _without_pin(manifest: ComparisonGenerationManifest | None) -> CuratorAssessmentRecords:
    if manifest is not None:
        if any(ref.owner in RESERVED_CURATOR_OWNERS for ref in manifest.evidence):
            return _result(
                "unavailable", "Reserved curator artifacts have no unique owner record pin."
            )
        if manifest.records.assessments:
            return _result(
                "unavailable",
                "This generation recorded assessments but retained no resolvable curator owner reference.",
            )
        if manifest.records.assessment_channel is not None:
            return CuratorAssessmentRecords((), manifest.records.assessment_channel)
    return _result(
        "not_selected",
        "No assessment collection was captured in this historical comparison; this does not mean no assessments were ever authored.",
    )


def _require_historical_pin(
    resolved: ReviewCandidateResolution, pin: ComparisonArtifactReference
) -> None:
    closed = resolved.closed_leaf
    assert closed is not None and closed.manifest is not None and resolved.contract is not None
    channel = closed.manifest.records.assessment_channel
    if channel is None or channel.owner != _OWNER:
        raise ValueError(
            "The curator reference lacks captured owner-channel provenance; generic evidence does not supply assessments."
        )
    expected = curator_coherence_paths(resolved.contract).generation_record(pin.sha256)
    if pin.relative_path != expected.relative_to(resolved.contract.task_root).as_posix():
        raise ValueError("The curator pin does not name its exact content-addressed owner record.")
    unavailable = [
        channel.relative_path
        for channel in closed.reopened.evidence
        if channel.owner in RESERVED_CURATOR_OWNERS and channel.state != "available"
    ]
    if unavailable:
        raise ValueError("Bound curator artifacts are unavailable: " + ", ".join(unavailable))


def _generation_records(
    resolved: ReviewCandidateResolution, generation: ValidatedCuratorCoherenceGeneration
) -> CuratorAssessmentRecords:
    contract = resolved.contract
    assert contract is not None
    canonical = curator_coherence_paths(contract).canonical.resolve()
    references: dict[str, ComparisonArtifactReference] = {}
    for fact in generation.evidence:
        path = Path(fact.path).resolve()
        if path == canonical:
            continue  # The mutable pointer is never an immutable input.
        relative = path.relative_to(contract.task_root.resolve()).as_posix()
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != fact.sha256:
            raise ValueError(f"curator artifact moved during capture: {relative}")
        references[relative] = ComparisonArtifactReference(
            owner=CURATOR_RECORD_OWNER
            if path == generation.record_path.resolve()
            else CURATOR_ARTIFACT_OWNER,
            relative_path=relative,
            sha256=fact.sha256,
            byte_count=len(payload),
        )
    assessments = tuple(generation.record.assessments)
    return CuratorAssessmentRecords(
        assessments=assessments,
        artifacts=tuple(references[key] for key in sorted(references)),
        channel=_channel(
            "recorded" if assessments else "none_recorded",
            f"Read {len(assessments)} authored assessment(s) from immutable curator generation {generation.record_digest}.",
            len(assessments),
        ),
    )


def _result(
    state: ReviewRecordChannelState, detail: str, *, unreadable: tuple[str, ...] = ()
) -> CuratorAssessmentRecords:
    return CuratorAssessmentRecords(
        (), _channel(state, detail, 0 if state == "none_recorded" else None, unreadable=unreadable)
    )


def _channel(
    state: ReviewRecordChannelState,
    detail: str,
    count: int | None,
    *,
    unreadable: tuple[str, ...] = (),
) -> ReviewRecordChannel:
    return ReviewRecordChannel(
        records="assessments",
        state=state,
        owner=_OWNER,
        record_count=count,
        unreadable=unreadable,
        detail=detail,
        next_action=None
        if state in {"recorded", "none_recorded"}
        else "Restore the exact bound owner artifacts, or explicitly record a successor through the comparison producer.",
    )
