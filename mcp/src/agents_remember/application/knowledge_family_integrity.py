"""The one operation that composes the family-integrity pipeline, and the retention it proves.

``KS-R16@v1``'s Normative Requirement asks for one operation over the three record leaves, and this is
it: :func:`family_integrity_report` opens a dataset **read-only**, constructs the registered review
scope, reads the recorded detection run back, groups its facts, composes the five separated statuses,
measures each authored record's currentness and routes the groups into the existing curator worklist.
It decides nothing itself -- every one of those acts belongs to the module that owns it, and this file
is only the seam that puts them in the packet's order.

Two of the five status owners are **not** this leaf's to report. The structural validator and the
verification runner each report their own fact, so the request carries their two statuses verbatim and
the pipeline refuses a request that omits one rather than filling it with a plausible value: a pipeline
that invented another owner's status would be the collapse §4.1 forbids, wearing the other owners'
names. The detector's status is derived from the signals the run recorded, the curator's from the
records that are stored, and the authority's from the currentness comparison this leaf measures.

:func:`publish_review_evidence` is the second half of §6: a finding and the manifest needed to
interpret it are published through ``KS-R18@v1``'s durable route and **read back** from the exact
destination, because ``design/retrieval-review-design.md:370`` declined to certify cleanup retention and
a destination nobody read back is an assumption rather than evidence. The destination is
``<task_root>/notes/reports/`` -- outside the enclosure root and outside the worktree group by
construction -- and this module neither widens that set nor writes into an enclosure's own ``reports/``
directory, which cleanup removes.

Nothing here is a gate. The report carries the shipped actionability formula's own three terms and the
family-review row count beside them, identifies the validator that decides closeout readiness, and has
no field that could refuse a merge, block a closeout or add a fourth term.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from agents_remember.application.knowledge_composition import open_read_only_store
from agents_remember.memory.knowledge.detection import read_detection_run
from agents_remember.memory.knowledge.durable_evidence import (
    DurableEvidencePublication,
    EvidenceReadBack,
    publish_durable_evidence,
    read_back_evidence,
)
from agents_remember.memory.knowledge.refusals import RefusalFacts, refusal
from agents_remember.memory.knowledge.registered_scope import (
    RegisteredScopeResult,
    ScopeSnapshotSource,
    construct_registered_scope,
)
from agents_remember.memory_quality.family_review import (
    compose_currentness,
    compose_status_report,
    curator_currentness_status,
    curator_review_status,
    detector_status,
    family_review_summaries,
    group_detection_facts,
    route_family_review,
)
from agents_remember.memory_quality.knowledge_review import (
    AssessmentSummary,
    knowledge_review_section,
)
from agents_remember.models.knowledge.detection import DetectionRunResult
from agents_remember.models.knowledge.family_review import (
    FamilyIntegrityFactGroup,
    FamilyReviewRouting,
    FindingCurrentness,
    SeparatedStatusReport,
)
from agents_remember.models.knowledge.registered_scope import RegisteredScopeRequest
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.lifecycles.review_assessment import (
    ReviewAssessment,
    assessment_subject_id,
)

__all__ = [
    "FamilyIntegrityReport",
    "FamilyIntegrityRequest",
    "ReviewEvidenceRetention",
    "family_integrity_report",
    "publish_review_evidence",
    "worklist_row_disposable",
]

# The two owners whose status the pipeline does not derive. Named as a value so a request that omits
# one is refused by name instead of being filled with a plausible default.
CALLER_REPORTED_STATUS_OWNERS: tuple[str, ...] = ("structural-validator", "verification-runner")

# The one operation name the composition's own refusal carries. It is a member of the shipped
# operation vocabulary rather than a new refusal *code*: the codes this leaf reuses are all shipped.
COMPOSE_REPORT_OPERATION = "compose_family_integrity_report"


@dataclass(frozen=True)
class FamilyIntegrityRequest:
    """Everything one pipeline run reads, as identities and measurements rather than descriptions.

    ``current`` is the caller's measurement of the world, keyed by record identity, because currentness
    compares recorded identities against measured ones and this leaf neither reads a tree nor decides
    that two inputs are equivalent. The three counts are the shipped formula's own terms, carried in
    unchanged so the report can state a value it did not compute.
    """

    repository_id: str
    run_id: str
    scope_request: RegisteredScopeRequest
    sources: tuple[ScopeSnapshotSource, ...]
    owner_reported_statuses: Mapping[str, str]
    assessments: tuple[ReviewAssessment, ...] = ()
    current: Mapping[str, Mapping[tuple[str, str], tuple[str, str]]] = field(default_factory=dict)
    repair_count: int = 0
    missing_count: int = 0
    stale_count: int = 0
    unresolved_inputs: bool = False
    incomplete_scan: bool = False

    @property
    def missing_owner_statuses(self) -> tuple[str, ...]:
        """Return the owners whose status the request did not carry."""

        return tuple(
            owner
            for owner in CALLER_REPORTED_STATUS_OWNERS
            if owner not in self.owner_reported_statuses
        )


@dataclass(frozen=True)
class FamilyIntegrityReport:
    """The composed pipeline output: one record per act, and no merged verdict anywhere.

    A refused scope or an unreadable run short-circuits the whole report: no group is built and no
    routing row exists, because reporting a partial pipeline over a scope or a run that was not read
    would be the silent partial state §1.8 and the packet's failure behaviour both refuse.
    """

    state: Literal["composed", "refused"]
    scope: RegisteredScopeResult
    run: DetectionRunResult | None = None
    groups: tuple[FamilyIntegrityFactGroup, ...] = ()
    statuses: SeparatedStatusReport | None = None
    currentness: tuple[FindingCurrentness, ...] = ()
    routing: FamilyReviewRouting | None = None
    review_rows: tuple[AssessmentSummary, ...] = ()
    currentness_status: str | None = None
    refusal: KnowledgeRefusal | None = None

    def report_only_rows(self) -> int:
        """Return how many family-review rows the report-only section carries."""

        return 0 if self.routing is None else self.routing.review_row_count

    def section_lines(self) -> tuple[str, ...]:
        """Return the rendered report-only section, so a caller can assert a row is present in it."""

        return tuple(knowledge_review_section(self.review_rows).lines)


def family_integrity_report(
    database_path: Path, request: FamilyIntegrityRequest
) -> FamilyIntegrityReport:
    """Run the pipeline end to end over one read-only dataset.

    The order is the packet's own: scope construction first, because everything downstream is a claim
    about the scope the run examined; then the recorded run read back in its recorded order; then the
    facts grouped; then the five statuses composed from what the run, the records and the two
    caller-supplied owners actually say; then currentness measured per record; then the routing, which
    consumes the worklist that already exists rather than creating one.
    """

    missing = request.missing_owner_statuses
    if missing:
        return _refused(
            construct_registered_scope(request.scope_request, request.sources),
            _owner_status_refusal(missing),
        )
    scope = construct_registered_scope(request.scope_request, request.sources)
    if not scope.constructed():
        return _refused(scope, scope.refusal.refusal if scope.refusal is not None else None)
    store = open_read_only_store(database_path, request.repository_id)
    try:
        run = read_detection_run(store, request.run_id)
    finally:
        store.close()
    if run.state == "refused":
        return _refused(scope, run.refusal)
    return _compose(scope, run, request)


def _refused(
    scope: RegisteredScopeResult, refusal: KnowledgeRefusal | None
) -> FamilyIntegrityReport:
    """Short-circuit the pipeline, keeping whatever was resolved and the refusal that stopped it."""

    return FamilyIntegrityReport(state="refused", scope=scope, refusal=refusal)


def _compose(
    scope: RegisteredScopeResult,
    run: DetectionRunResult,
    request: FamilyIntegrityRequest,
) -> FamilyIntegrityReport:
    """Compose the report from the constructed scope and the recorded run."""

    groups = group_detection_facts(run.signals)
    currentness = compose_currentness(request.assessments, request.current)
    return FamilyIntegrityReport(
        state="composed",
        scope=scope,
        run=run,
        groups=groups,
        statuses=compose_status_report(_owner_statuses(run, request, currentness)),
        currentness=currentness,
        routing=route_family_review(
            groups,
            review_ids_by_subject=_review_ids_by_subject(request.assessments),
            repair_count=request.repair_count,
            missing_count=request.missing_count,
            stale_count=request.stale_count,
        ),
        review_rows=family_review_summaries(request.assessments, request.current),
        currentness_status=curator_currentness_status(currentness),
    )


def _owner_statuses(
    run: DetectionRunResult,
    request: FamilyIntegrityRequest,
    currentness: Sequence[FindingCurrentness],
) -> Mapping[str, str]:
    """Return all five owners' statuses: three derived from records, two reported by their owners."""

    reported = dict(request.owner_reported_statuses)
    reported["detector"] = detector_status(
        run.signals,
        unresolved_inputs=request.unresolved_inputs,
        incomplete_scan=request.incomplete_scan,
    )
    reported["curator-reviewer"] = curator_review_status(
        [record.disposition for record in request.assessments]
    )
    reported["authority-currentness"] = (
        "stale"
        if any(binding.binding_state == "stale" for binding in currentness)
        else "dependencies-match"
    )
    return reported


def _owner_status_refusal(missing: Sequence[str]) -> KnowledgeRefusal:
    """Refuse a run whose request did not carry an owner's own status."""

    return refusal(
        "invalid_payload",
        COMPOSE_REPORT_OPERATION,
        "the pipeline composes five separately reported statuses and the request carried no status "
        f"for {' | '.join(missing)}. The pipeline does not fill in another owner's fact: a default "
        "here would be this leaf reporting a validator's or a runner's result it never measured",
        next_action=(
            "Supply the status each owner reported, verbatim, or run that owner before composing the "
            "pipeline report."
        ),
        facts=RefusalFacts(
            expected="a status reported by every owner this leaf does not own",
            observed=" | ".join(missing) or "<none>",
        ),
    )


def _review_ids_by_subject(
    assessments: Sequence[ReviewAssessment],
) -> Mapping[str, tuple[str, ...]]:
    """Return the stored record identities, keyed by the subject spelling the routing rows use."""

    grouped: dict[str, list[str]] = {}
    for record in assessments:
        grouped.setdefault(assessment_subject_id(record), []).append(record.assessmentId)
    return {subject: tuple(sorted(identities)) for subject, identities in grouped.items()}


@dataclass(frozen=True)
class ReviewEvidenceRetention:
    """One finding and its manifest, published and then read back from the exact destination.

    ``matched()`` is the only retention claim this record can make, and it is a claim about two
    read-backs rather than about a publication: the enclosure is cleaned between the two events, so the
    measurement is what proves survival. A read-back that did not match answers with the destination,
    the expected digest and the observed state instead of "published".
    """

    finding: DurableEvidencePublication
    manifest: DurableEvidencePublication
    finding_read_back: EvidenceReadBack
    manifest_read_back: EvidenceReadBack

    @property
    def destination(self) -> Path:
        """Return the resolved destination both artifacts were published to."""

        return self.finding.destination.parent

    def matched(self) -> bool:
        """Return whether both artifacts read back as the bytes that were published."""

        return self.finding_read_back.matched() and self.manifest_read_back.matched()

    def readable_together(self) -> bool:
        """Return whether the pair is readable together, which is what §6.3 asks this leaf to prove."""

        same_directory = self.finding.destination.parent == self.manifest.destination.parent
        return self.matched() and same_directory

    def blocked_reason(self) -> str:
        """Return the exact blocked-terminal-state reason, naming the destination and observed states."""

        if self.matched():
            return ""
        return (
            f"the resolved durable destination {self.destination} did not read both artifacts back: "
            f"finding -> {self.finding_read_back.state}; manifest -> {self.manifest_read_back.state}. "
            "Retention that rests on a path nobody read back is not evidence."
        )


def publish_review_evidence(
    task_root: Path,
    *,
    finding_name: str,
    finding_content: str,
    manifest_name: str,
    manifest_content: str,
) -> ReviewEvidenceRetention:
    """Publish a finding and its manifest, then read both back from the resolved destination.

    The destination is ``<task_root>/notes/reports/`` and it is *resolved by the shipped publication
    function* rather than by this one, so the set of destinations a retention claim may rest on keeps
    its single definition. The read-back happens immediately; whoever holds the publication record
    repeats it after cleanup, because that is the moment the claim becomes a measurement.
    """

    finding = publish_durable_evidence(task_root, finding_name, finding_content)
    manifest = publish_durable_evidence(task_root, manifest_name, manifest_content)
    return ReviewEvidenceRetention(
        finding=finding,
        manifest=manifest,
        finding_read_back=read_back_evidence(finding),
        manifest_read_back=read_back_evidence(manifest),
    )


def worklist_row_disposable(*, durable_reference_count: int) -> bool:
    """Return whether a worklist row may be discarded without destroying a durable reference.

    §6.4 and ``Doc13:114`` permit discarding a *regenerable* worklist row and permit it only when doing
    so destroys no durable reference. A row that is the only pointer to a finding or a manifest is
    therefore not regenerable, and this predicate is the one place that decides it: a caller that
    counted no durable reference gets ``False`` and keeps the row.
    """

    return durable_reference_count > 0
