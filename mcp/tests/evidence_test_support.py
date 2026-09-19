"""The supporting-record cases' shared fixture: one admitted namespace with resolvable links.

Every case in this leaf's two test modules needs the same starting point, and it is a specific one
rather than a convenient one. An evidence claim resolves four links -- a subject, an evidence anchor
and every claimed-coverage endpoint -- and an observation records a candidate that some other
operation already established. A per-module copy of that topology would let the two modules disagree
about which identity is the facet revision and which is the invariant revision, which is exactly the
property the refusal cases assert; so it is built once, here, through the production application seam
rather than by inserting rows.

What the fixture provides, and why each part is there:

* **An admitted candidate namespace** built by :func:`build_admitted_candidate`, so provenance comes
  from the admission rather than from any authored payload -- the same reason the facet cases share
  theirs.
* **Two invariant revisions**, so a claim can have one subject and still leave a second identity that
  is deliberately *not* what another case names.
* **A source anchor and two realization claims**, so the evidence anchor and the claimed-coverage
  list are two distinct relations over distinct endpoints: an anchor is one kind of thing a claim can
  cover, and a realization claim is the other.
* **A facet record with one revision**, built through the facet commands, so the second subject kind
  is a real ``KnowledgeFacet`` revision rather than a claim about one.
* **A local artifact directory with a real file**, so the write-time digest check has bytes to read
  and the read-time resolution has four distinguishably different outcomes to report.

Nothing here is a second authority: the fixture builds stores through the same operations a caller
would, and the identities it returns are the ones those operations produced.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from agents_remember.application.knowledge import (
    admitted_knowledge_destination,
    initialize_knowledge_namespace,
    open_admitted_knowledge_store,
    write_authorship,
)
from agents_remember.application.knowledge_read import open_read_context
from agents_remember.memory.knowledge import anchors, facets, realizations
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.evidence import (
    EVIDENCE_CLAIM_KIND,
    EVIDENCE_CLAIM_SCHEMA,
    VERIFICATION_OBSERVATION_KIND,
    VERIFICATION_OBSERVATION_SCHEMA,
    AddEvidenceClaim,
    AddVerificationObservation,
    AnchorCoverage,
    EvidenceClaimPayload,
    InvariantRevisionSubject,
    KnowledgeFacetRevisionSubject,
    PublicationReference,
    RealizationClaimCoverage,
    ResultArtifactReference,
    RunEnvironment,
    VerificationObservationPayload,
)
from agents_remember.models.knowledge.evidence_read import VerificationObservationItem
from agents_remember.models.knowledge.facet import (
    AddFacet,
    DecisionPayload,
    FacetWriteRequest,
)
from agents_remember.models.knowledge.graph import RealizationClaimDraft, RealizationRole
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import (
    InvariantRequest,
    KnowledgeRefusal,
    NewAnchor,
    RevisionDraft,
    RevisionRequest,
)
from agents_remember.models.knowledge.source import (
    FileLocator,
    GitBlobIdentity,
    SourceAnchorDraft,
)

AUTHORIZATION = "260915-KS developer kickoff ruling"
FIXTURE_BLOB = "a" * 40
FIXTURE_PATH = "src/integration.py"
SECOND_PATH = "src/synchronization.py"

# A tree identity is a recorded Git object, and a knowledge snapshot identity is the shipped value.
CODE_TREE = "c" * 40
LOGICAL_DIGEST = "d" * 64
SCHEMA_VERSION = "ar-knowledge-sqlite/v7"
COMMAND_EXPECTED = "python -m pytest mcp/tests -q"
COMMAND_CONTROL = "python -m ruff check mcp/src"


@dataclass(frozen=True)
class EvidenceFixture:
    """One admitted namespace with every link an evidence claim or an observation can name."""

    destination: Any
    authorship: Authorship
    invariant_id: str
    invariant_revision_id: str
    second_invariant_revision_id: str
    anchor_id: str
    claim_id: str
    second_claim_id: str
    facet_record_id: str
    facet_revision_id: str
    artifact_root: Path
    artifact_path: str
    artifact_bytes: bytes
    artifact_sha256: str
    toolchain: tuple[tuple[str, str], ...] = field(
        default=(("ruff", "0.16.1"), ("pytest", "9.0.0"))
    )

    def snapshot(self) -> SnapshotIdentity:
        """The *recorded* candidate identity this fixture's observations name.

        It is deliberately not the dataset's own logical identity: a run records the candidate it
        tested, and the fixture's records name a fixed identity so a case can select them by it.
        """

        return SnapshotIdentity(
            repository_id=self.destination.repository.repository_id,
            schema_version=SCHEMA_VERSION,
            logical_digest=LOGICAL_DIGEST,
        )

    def snapshot_identity(self) -> SnapshotIdentity:
        """The identity the fixture's dataset actually holds, read from the file."""

        return dataset_identity(self.destination.database_path)


def build_evidence_fixture(tmp_path: Path, *, name: str = "evidence") -> EvidenceFixture:
    """Create one admitted namespace, store its links and write one artifact for it to reference."""

    directory = tmp_path / name
    directory.mkdir(parents=True, exist_ok=True)
    authorship = write_authorship(
        actor_ref="agent:evidence",
        authorization_ref=AUTHORIZATION,
        origin_refs=("requirement:KS-R12@v1",),
    )
    destination = admitted_knowledge_destination(
        directory / "candidate.db",
        RepositoryIdentity(repository_id=str(uuid4()), authority_home="agents-remember"),
        authorship,
    )
    initialize_knowledge_namespace(destination)
    store = _open(destination)
    try:
        invariant_id, revision_id, second_revision_id = _store_invariants(store, authorship)
        anchor_id, claim_id, second_claim_id = _store_realizations(
            store, authorship, revision_id, second_revision_id
        )
        facet_record_id, facet_revision_id = _store_facet(store, destination)
    finally:
        store.close()

    artifact_root = directory / "checkout"
    artifact_root.mkdir(parents=True, exist_ok=True)
    relative = "reports/knowledge-suite.log"
    artifact_file = artifact_root / relative
    artifact_file.parent.mkdir(parents=True, exist_ok=True)
    artifact_bytes = b"evidence-suite: 176 passed\n"
    artifact_file.write_bytes(artifact_bytes)

    return EvidenceFixture(
        destination=destination,
        authorship=authorship,
        invariant_id=invariant_id,
        invariant_revision_id=revision_id,
        second_invariant_revision_id=second_revision_id,
        anchor_id=anchor_id,
        claim_id=claim_id,
        second_claim_id=second_claim_id,
        facet_record_id=facet_record_id,
        facet_revision_id=facet_revision_id,
        artifact_root=artifact_root,
        artifact_path=relative,
        artifact_bytes=artifact_bytes,
        artifact_sha256=hashlib.sha256(artifact_bytes).hexdigest(),
    )


def _open(destination: Any) -> Any:
    return open_admitted_knowledge_store(destination)


def _store_invariants(store: Any, authorship: Authorship) -> tuple[str, str, str]:
    """Store one invariant with two revisions, returning the identity and both revisions."""

    invariant_id = str(uuid4())
    first, second = str(uuid4()), str(uuid4())
    store.create_invariant(
        InvariantRequest(
            repository_id=store.repository_id,
            invariant_id=invariant_id,
            display_label="evidence-subject",
            provenance=authorship,
        )
    )
    for revision_id, version, statement in (
        (first, "v1", "The retry budget is exercised by the committed fixture."),
        (second, "v2", "The retry budget is exercised by the committed fixture and its control."),
    ):
        store.create_revision(
            RevisionRequest(
                repository_id=store.repository_id,
                revision=RevisionDraft(
                    revision_id=revision_id,
                    invariant_id=invariant_id,
                    display_version=version,
                    statement=statement,
                    applicability="Every admitted candidate write in this namespace.",
                    conditions=("The candidate write is admitted for this namespace.",),
                    exclusions=(),
                    provenance=authorship,
                ),
            )
        )
    return (invariant_id, first, second)


def _store_realizations(
    store: Any,
    authorship: Authorship,
    revision_id: str,
    second_revision_id: str,
) -> tuple[str, str, str]:
    """Store one standalone anchor and two realization claims, returning their identities."""

    anchor_id = str(uuid4())
    anchors.insert_anchor_row(
        store,
        anchors.source_anchor_from_draft(
            SourceAnchorDraft(
                anchor_id=UUID(anchor_id),
                path=FIXTURE_PATH,
                source_identity=GitBlobIdentity(object_id=FIXTURE_BLOB),
                locator=FileLocator(),
            ),
            authorship,
        ),
    )
    claim_ids: list[str] = []
    pairs: tuple[tuple[str, RealizationRole, str], ...] = (
        (revision_id, "enforcement", "The integration entry point applies the admitted write."),
        (
            second_revision_id,
            "propagation-persistence",
            "The synchronization path propagates the approved state afterwards.",
        ),
    )
    for revision, role, rationale in pairs:
        claim_id = str(uuid4())
        realizations.insert_realization_claim(
            store,
            realizations.RealizationClaimRequest(
                repository_id=store.repository_id,
                claim=RealizationClaimDraft(
                    claim_id=claim_id,
                    invariant_revision_id=revision,
                    role=role,
                    rationale=rationale,
                ),
                anchor=NewAnchor(
                    anchor=SourceAnchorDraft(
                        anchor_id=UUID(str(uuid4())),
                        path=SECOND_PATH,
                        source_identity=GitBlobIdentity(object_id=FIXTURE_BLOB),
                        locator=FileLocator(),
                    )
                ),
                provenance=authorship,
            ),
        )
        claim_ids.append(claim_id)
    return (anchor_id, claim_ids[0], claim_ids[1])


def _store_facet(store: Any, destination: Any) -> tuple[str, str]:
    """Store one decision facet through the facet commands, returning its record and revision."""

    record_id, revision_id = str(uuid4()), str(uuid4())
    result = facets.add_facet(
        store,
        FacetWriteRequest(
            repository_id=store.repository_id,
            command=AddFacet(
                record_id=record_id,
                revision_id=revision_id,
                facet_kind="decision",
                payload=DecisionPayload(
                    outcome="record the retry budget",
                    reason="the committed fixture exercises it",
                    decider="architect-seat",
                ).model_dump(mode="json"),
            ),
            provenance=destination.authorship,
        ),
    )
    assert result.state == "applied", result
    return (record_id, revision_id)


# ---------------------------------------------------------------------------
# The commands. Each builder returns one authored act, so a case states only what it varies.


@dataclass(frozen=True)
class ClaimOptions:
    """What one claim command varies. A dataclass rather than thirteen keyword arguments.

    The guidance is that arguments representing a concept become a value, and this is exactly that
    case: every field below is one of the claim's own authored parts, and a caller that names only
    what it varies reads as the act it is authoring.
    """

    claim_id: str | None = None
    revision_id: str | None = None
    subject: Any | None = None
    evidence_anchor_id: str | None = None
    coverage: tuple[Any, ...] | None = None
    explanation: str = "the retry budget is exercised by the committed fixture"
    limitations: str = ""
    assessment_refs: tuple[str, ...] = ()
    governing_route_id: str | None = None


def claim_command(
    fixture: EvidenceFixture, options: ClaimOptions | None = None
) -> AddEvidenceClaim:
    """Return one authored claim command, with every link pointing at the fixture's own rows."""

    chosen = options or ClaimOptions()
    return AddEvidenceClaim(
        claim_id=chosen.claim_id or str(uuid4()),
        revision_id=chosen.revision_id or str(uuid4()),
        subject=chosen.subject
        if chosen.subject is not None
        else InvariantRevisionSubject(revision_id=fixture.invariant_revision_id),
        evidence_anchor_id=chosen.evidence_anchor_id or fixture.anchor_id,
        coverage=chosen.coverage
        if chosen.coverage is not None
        else (RealizationClaimCoverage(claim_id=fixture.claim_id),),
        payload=EvidenceClaimPayload(
            explanation=chosen.explanation,
            limitations=chosen.limitations,
            assessment_refs=chosen.assessment_refs,
        ),
        governing_route_id=chosen.governing_route_id,
    )


@dataclass(frozen=True)
class ObservationOptions:
    """What one observation command varies, for the same reason :class:`ClaimOptions` exists."""

    observation_id: str | None = None
    revision_id: str | None = None
    command_name: str = "pytest"
    command_identity: str = COMMAND_EXPECTED
    knowledge_candidate: Any = "default"
    code_candidate_tree_id: str | None = None
    artifact: Any = "default"
    execution_result: str = "passed"
    environment: RunEnvironment | None = None
    publication: Any | None = None
    artifact_root: Any = "default"
    state_at_origin: str = "proposed"


def observation_command(
    fixture: EvidenceFixture, options: ObservationOptions | None = None
) -> AddVerificationObservation:
    """Return one authored observation command, defaulting to the fixture's own artifact."""

    chosen = options or ObservationOptions()
    snapshot = (
        fixture.snapshot()
        if chosen.knowledge_candidate == "default"
        else chosen.knowledge_candidate
    )
    reference = (
        artifact_reference(fixture, digest_checked=False)
        if chosen.artifact == "default"
        else chosen.artifact
    )
    root = str(fixture.artifact_root) if chosen.artifact_root == "default" else chosen.artifact_root
    return AddVerificationObservation(
        observation_id=chosen.observation_id or str(uuid4()),
        revision_id=chosen.revision_id or str(uuid4()),
        payload=VerificationObservationPayload(
            command_name=chosen.command_name,
            command_identity=chosen.command_identity,
            knowledge_candidate=snapshot,
            code_candidate_tree_id=chosen.code_candidate_tree_id,
            result_artifact=reference,
            execution_result=chosen.execution_result,  # type: ignore[arg-type]
            environment=chosen.environment or default_environment(),
            publication=chosen.publication,
            state_at_origin=chosen.state_at_origin,  # type: ignore[arg-type]
        ),
        artifact_root=root,
    )


def facet_subject(fixture: EvidenceFixture) -> KnowledgeFacetRevisionSubject:
    """Return the fixture's decision-facet revision as a claim subject."""

    return KnowledgeFacetRevisionSubject(revision_id=fixture.facet_revision_id)


def anchor_coverage(fixture: EvidenceFixture) -> AnchorCoverage:
    """Return the fixture's standalone anchor as one claimed-coverage endpoint."""

    return AnchorCoverage(anchor_id=fixture.anchor_id)


def expect_refusal(value: Any) -> KnowledgeRefusal:
    """Narrow one payload-seam outcome to its refusal, refusing a value that is not one.

    The seam returns ``BaseModel | KnowledgeRefusal`` because that is what a caller has to branch on.
    A case that has already proved the refusal half needs one place to say so, and naming it here
    keeps the narrowing out of every assertion that follows.
    """

    assert isinstance(value, KnowledgeRefusal), value
    return value


def expect_artifact(item: VerificationObservationItem) -> ResultArtifactReference:
    """Narrow one served observation to its recorded artifact reference."""

    artifact = item.observation.payload.result_artifact
    assert artifact is not None
    return artifact


def expect_publication(item: VerificationObservationItem) -> PublicationReference:
    """Narrow one served observation to its recorded publication reference."""

    publication = item.observation.payload.publication
    assert publication is not None
    return publication


def read_context(fixture: EvidenceFixture, *, task_ref: str | None = None) -> Any:
    """Return the read context that names the fixture's candidate exactly.

    Built through the shipped constructor rather than assembled, so the context names the identity the
    dataset actually holds: a read verified against it is verified against the file, which is what
    makes "this read is against the declared snapshot" a real comparison rather than a formality.

    No source resolution is requested, which is the shipped ``None`` state for both of its halves --
    the fields are a pair, and supplying one without the other is refused as an incomplete request
    rather than answered as ``not_requested``. An evidence read resolves *recorded* references, not
    source anchors against a Git tree, so requesting one would be a different question.
    """

    return open_read_context(
        fixture.destination.database_path,
        fixture.destination.repository.repository_id,
        task_ref=task_ref,
    )


def artifact_reference(
    fixture: EvidenceFixture, *, digest_checked: bool = False
) -> ResultArtifactReference:
    return ResultArtifactReference(
        path=fixture.artifact_path,
        sha256=fixture.artifact_sha256,
        size_bytes=len(fixture.artifact_bytes),
        digest_checked_against_bytes=digest_checked,
    )


def default_environment() -> RunEnvironment:
    return RunEnvironment(
        host="fixture-host",
        interpreter="3.13.15",
        toolchain=(("pytest", "9.0.0"),),
    )


def claim_payload() -> dict[str, Any]:
    """The frozen payload of one claim, as a mapping, for a raw payload-seam case."""

    return EvidenceClaimPayload(
        explanation="the fixture covers the retry budget",
        limitations="only the committed fixture is exercised",
    ).model_dump(mode="json")


def observation_payload(fixture: EvidenceFixture) -> dict[str, Any]:
    """The frozen payload of one observation, as a mapping."""

    return VerificationObservationPayload(
        command_name="pytest",
        command_identity=COMMAND_EXPECTED,
        knowledge_candidate=fixture.snapshot(),
        execution_result="not_run",
        environment=default_environment(),
    ).model_dump(mode="json")


def schema_names() -> tuple[str, str]:
    """Return the two record schemas this leaf registers, for the seam's pair assertions."""

    return (EVIDENCE_CLAIM_SCHEMA, VERIFICATION_OBSERVATION_SCHEMA)


def record_kinds() -> tuple[str, str]:
    """Return the two record kinds this leaf registers."""

    return (EVIDENCE_CLAIM_KIND, VERIFICATION_OBSERVATION_KIND)


def recorded_at() -> str:
    """Return one fixed instant, so a publication reference is a function of the fixture."""

    return datetime(2026, 9, 18, 3, 0, tzinfo=UTC).isoformat()


def digest_of(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
