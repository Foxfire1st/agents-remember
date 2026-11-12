"""The shared single-revision knowledge fixture built through the text-backed operations.

The fixture exists because the interesting read behaviour is a *shape*, not a constructor call:
several separately recorded revisions must remain separately addressable, with their exact
statements, and both must decode with no predecessors. It is built as a text memory tree through
the rows builder, never by inserting rows, so a later leaf that builds on it inherits a real
index it can trust and a scenario it can extend rather than re-invent. A text record holds
exactly one revision (MIK-R74 rule 3): the divergent-successor revisions the old fixture authored
beside one identity are gone, and the three revisions below are three separate single-revision
records that share nothing but their derived display.

The same store also carries the *graph* half of the shared corpus, because the two halves are one
scenario: the realized invariant revision, its two recorded implementations, and the overlapping
families the selective-read and comparison leaves consume.

* the realized revision is ``base_revision_id`` -- the single revision of the first invariant;
* ``integration`` and ``synchronization`` are its two recorded realizations, and
  ``absent_source`` is a third one whose recorded path exists in no snapshot (it is retained as
  authored, because storage never resolves a source);
* ``family`` is ``{base_revision_id, second_revision_id}`` and ``overlapping_family`` is
  ``{second_revision_id, third_revision_id}``, so the sibling revision belongs to both families
  while the realized revision belongs to exactly one.

The module is test support, not production code: it decides nothing and is imported by tests
and by later leaves' fixtures only.
"""

from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from agents_remember.memory.knowledge.store import OpenedKnowledgeStore, open_read_only_store
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.family import FamilyRevisionDraft
from agents_remember.models.knowledge.graph import FamilyMemberDraft, RealizationRole
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.source import FileLocator, LineRangeLocator, SourceLocator
from anchor_fixture_models import GitBlobIdentity, RealizationClaimDraft, SourceAnchorDraft
from knowledge_rows_test_support import (
    INDEX_REPOSITORY_ID,
    FamilyMemberRequest,
    FamilyRequest,
    FamilyRevisionRequest,
    InvariantRequest,
    NewAnchor,
    RealizationClaimRequest,
    RevisionDraft,
    RevisionRequest,
    RowStore,
    families,
    memberships,
    open_knowledge_store,
    realizations,
)

# Stable prose so a reopened comparison is meaningful rather than incidental.
REPOSITORY_AUTHORITY_HOME = "agents-remember"
INVARIANT_LABEL = "approved-state-preservation"
BASE_STATEMENT = "Preserve approved state across an admitted candidate write."
BRANCH_A_STATEMENT = "Preserve approved state and record the deciding attribution."
BRANCH_B_STATEMENT = "Preserve approved state and refuse a partially applied batch."
BASE_DISPLAY_VERSION = "v1"
SHARED_SUCCESSOR_DISPLAY_VERSION = "v2"
ESSENTIAL_APPLICABILITY = "Every admitted candidate write in this repository namespace."
BASE_CONDITIONS = ("The candidate write is admitted for this namespace.",)
BRANCH_A_CONDITIONS = (
    "The candidate write is admitted for this namespace.",
    "Attribution for the authored change is recorded.",
)
BRANCH_B_CONDITIONS = (
    "The candidate write is admitted for this namespace.",
    "The batch is applied as one transaction.",
)
ESSENTIAL_EXCLUSIONS = ("Historical rows are not rewritten by a candidate write.",)

# The two further invariants the overlapping families are built from. They are ordinary
# authored obligations: a family's joint guarantee is its own text, and these statements are
# never folded into it.
SECOND_INVARIANT_LABEL = "candidate-batch-atomicity"
SECOND_INVARIANT_STATEMENT = "Apply every admitted candidate change as one transaction."
THIRD_INVARIANT_LABEL = "source-resolution-visibility"
THIRD_INVARIANT_STATEMENT = "Report an unresolved recorded source location as a resolution fact."

# The families and their guarantees.
FAMILY_LABEL = "approved-state-family"
FAMILY_GUARANTEE = (
    "Approved state survives every admitted candidate write, and the deciding attribution is "
    "recorded beside it."
)
OVERLAPPING_FAMILY_LABEL = "candidate-write-integrity-family"
OVERLAPPING_FAMILY_GUARANTEE = (
    "A candidate write is atomic, and its recorded source location stays visible whether or not "
    "that location resolves in the selected snapshot."
)
FAMILY_DISPLAY_VERSION = "v1"

# The recorded source locations. The absent path is authored like any other: whether a path
# exists in a snapshot is a resolution fact, not a condition for storing the anchor.
INTEGRATION_PATH = "src/integration.py"
SYNCHRONIZATION_PATH = "src/synchronization.py"
ABSENT_PATH = "src/retired_adapter.py"
INTEGRATION_ROLE: RealizationRole = "enforcement"
SYNCHRONIZATION_ROLE: RealizationRole = "propagation-persistence"
ABSENT_ROLE: RealizationRole = "support"
INTEGRATION_RATIONALE = "The integration entry point applies the admitted write as one batch."
SYNCHRONIZATION_RATIONALE = "The synchronization path propagates the approved state afterwards."
ABSENT_RATIONALE = "The retired adapter applied the same obligation before the source moved."


@dataclass(frozen=True)
class FixtureRealization:
    """One recorded realization: its anchor, its claim and the path the anchor names."""

    anchor_id: str
    claim_id: str
    path: str


@dataclass(frozen=True)
class FixtureFamily:
    """One family: its identity, its single revision and the memberships recorded against it."""

    family_id: str
    revision_id: str
    member_ids: tuple[str, str]


@dataclass(frozen=True)
class BranchingKnowledgeFixture:
    """One repository with three single-revision invariants and the graph scenario in one file."""

    database_path: Path
    repository_id: str
    invariant_id: str
    base_revision_id: str
    authorship: Authorship
    second_invariant_id: str
    second_revision_id: str
    third_invariant_id: str
    third_revision_id: str
    family: FixtureFamily
    overlapping_family: FixtureFamily
    integration: FixtureRealization
    synchronization: FixtureRealization
    absent_source: FixtureRealization

    def reopen(self) -> OpenedKnowledgeStore:
        """Reopen the fixture store read-only, so a test reads what was really persisted."""

        return open_read_only_store(self.database_path, self.repository_id)


def make_authorship(
    *, actor_ref: str = "agent:fixture", recorded_at: str | None = None
) -> Authorship:
    """Build one provenance envelope with a stable actor and a real recorded instant."""

    return Authorship(
        actor_ref=actor_ref,
        authorization_ref="260915-KS developer kickoff ruling",
        operation_id=uuid4(),
        recorded_at=recorded_at or datetime.now(UTC).isoformat(),
        origin_refs=("requirement:KS-R01@v1", "requirement:KS-R02@v1"),
    )


def build_branching_knowledge_fixture(
    directory: Path,
    *,
    repository_id: str | None = None,
    invariant_id: str | None = None,
    database_name: str = "knowledge-candidate.db",
) -> BranchingKnowledgeFixture:
    """Create the fixture store through the text-backed operations and return its identities.

    The namespace and invariant arguments are accepted and not used beyond the seed: every index
    is keyed by its tree alone, so the fixture reads under the index's own namespace, and every
    identity a case asserts against is the projected identity the tree produced.
    """

    _ = (repository_id, invariant_id)
    directory.mkdir(parents=True, exist_ok=True)
    authorship = make_authorship()
    fixture = BranchingKnowledgeFixture(
        database_path=directory / database_name,
        repository_id=INDEX_REPOSITORY_ID,
        invariant_id=str(uuid4()),
        base_revision_id=str(uuid4()),
        authorship=authorship,
        second_invariant_id=str(uuid4()),
        second_revision_id=str(uuid4()),
        third_invariant_id=str(uuid4()),
        third_revision_id=str(uuid4()),
        family=FixtureFamily(
            family_id=str(uuid4()),
            revision_id=str(uuid4()),
            member_ids=(str(uuid4()), str(uuid4())),
        ),
        overlapping_family=FixtureFamily(
            family_id=str(uuid4()),
            revision_id=str(uuid4()),
            member_ids=(str(uuid4()), str(uuid4())),
        ),
        integration=FixtureRealization(
            anchor_id=str(uuid4()), claim_id=str(uuid4()), path=INTEGRATION_PATH
        ),
        synchronization=FixtureRealization(
            anchor_id=str(uuid4()), claim_id=str(uuid4()), path=SYNCHRONIZATION_PATH
        ),
        absent_source=FixtureRealization(
            anchor_id=str(uuid4()), claim_id=str(uuid4()), path=ABSENT_PATH
        ),
    )
    store = open_knowledge_store(fixture.database_path, fixture.repository_id)
    try:
        invariant_ids, revision_ids = _build_identity_half(store, fixture, authorship)
        family_ids, family_revision_ids, member_ids = _build_graph_families(store, fixture)
        claim_ids, anchor_ids = _create_realizations(store, fixture, authorship)
    finally:
        store.close()
    return dataclasses.replace(
        fixture,
        invariant_id=invariant_ids[fixture.invariant_id],
        base_revision_id=revision_ids[fixture.base_revision_id],
        second_invariant_id=invariant_ids[fixture.second_invariant_id],
        second_revision_id=revision_ids[fixture.second_revision_id],
        third_invariant_id=invariant_ids[fixture.third_invariant_id],
        third_revision_id=revision_ids[fixture.third_revision_id],
        family=_project_family(fixture.family, family_ids, family_revision_ids, member_ids),
        overlapping_family=_project_family(
            fixture.overlapping_family, family_ids, family_revision_ids, member_ids
        ),
        integration=_project_realization(fixture.integration, claim_ids, anchor_ids),
        synchronization=_project_realization(fixture.synchronization, claim_ids, anchor_ids),
        absent_source=_project_realization(fixture.absent_source, claim_ids, anchor_ids),
    )


def _project_family(
    seed: FixtureFamily,
    family_ids: dict[str, str],
    family_revision_ids: dict[str, str],
    member_ids: dict[str, str],
) -> FixtureFamily:
    """Resolve one family's caller-side identities to the projected identities the tree holds."""

    return FixtureFamily(
        family_id=family_ids[seed.family_id],
        revision_id=family_revision_ids[seed.revision_id],
        member_ids=(member_ids[seed.member_ids[0]], member_ids[seed.member_ids[1]]),
    )


def _project_realization(
    seed: FixtureRealization, claim_ids: dict[str, str], anchor_ids: dict[str, str]
) -> FixtureRealization:
    """Resolve one realization's caller-side identities to the projected ones the tree holds."""

    return FixtureRealization(
        anchor_id=anchor_ids[seed.anchor_id],
        claim_id=claim_ids[seed.claim_id],
        path=seed.path,
    )


def _build_identity_half(
    store: RowStore, fixture: BranchingKnowledgeFixture, authorship: Authorship
) -> tuple[dict[str, str], dict[str, str]]:
    repository_result = store.create_repository(_repository_identity(fixture))
    _require(fixture, "create_repository", repository_result.state, repository_result.refusal)
    invariant_ids: dict[str, str] = {}
    revision_ids: dict[str, str] = {}
    for invariant_id, revision_id, label, statement in (
        (fixture.invariant_id, fixture.base_revision_id, INVARIANT_LABEL, BASE_STATEMENT),
        (
            fixture.second_invariant_id,
            fixture.second_revision_id,
            SECOND_INVARIANT_LABEL,
            SECOND_INVARIANT_STATEMENT,
        ),
        (
            fixture.third_invariant_id,
            fixture.third_revision_id,
            THIRD_INVARIANT_LABEL,
            THIRD_INVARIANT_STATEMENT,
        ),
    ):
        invariant_result = store.create_invariant(
            InvariantRequest(
                repository_id=fixture.repository_id,
                invariant_id=invariant_id,
                display_label=label,
                provenance=authorship,
            )
        )
        _require(fixture, "create_invariant", invariant_result.state, invariant_result.refusal)
        invariant_ids[invariant_id] = invariant_result.invariant_id
        revision_result = store.create_revision(
            RevisionRequest(
                repository_id=fixture.repository_id,
                revision=RevisionDraft(
                    revision_id=revision_id,
                    invariant_id=invariant_id,
                    display_version=BASE_DISPLAY_VERSION,
                    statement=statement,
                    applicability=ESSENTIAL_APPLICABILITY,
                    conditions=BASE_CONDITIONS,
                    exclusions=ESSENTIAL_EXCLUSIONS,
                    provenance=authorship,
                ),
            )
        )
        _require(fixture, "create_revision", revision_result.state, revision_result.refusal)
        revision_ids[revision_id] = revision_result.revision_id
    return invariant_ids, revision_ids


def _build_graph_families(
    store: RowStore, fixture: BranchingKnowledgeFixture
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    family_ids: dict[str, str] = {}
    family_revision_ids: dict[str, str] = {}
    member_ids: dict[str, str] = {}
    for seed, label, guarantee, members in (
        (
            fixture.family,
            FAMILY_LABEL,
            FAMILY_GUARANTEE,
            (fixture.base_revision_id, fixture.second_revision_id),
        ),
        (
            fixture.overlapping_family,
            OVERLAPPING_FAMILY_LABEL,
            OVERLAPPING_FAMILY_GUARANTEE,
            (fixture.second_revision_id, fixture.third_revision_id),
        ),
    ):
        created = families.create_family(
            store,
            FamilyRequest(
                repository_id=fixture.repository_id,
                family_id=seed.family_id,
                display_label=label,
                provenance=fixture.authorship,
            ),
        )
        _require(fixture, "create_family", created.state, created.refusal)
        family_ids[seed.family_id] = created.family_id
        revision = families.create_family_revision(
            store,
            FamilyRevisionRequest(
                repository_id=fixture.repository_id,
                revision=FamilyRevisionDraft(
                    family_id=seed.family_id,
                    revision_id=seed.revision_id,
                    display_version=FAMILY_DISPLAY_VERSION,
                    joint_guarantee=guarantee,
                    provenance=fixture.authorship,
                ),
            ),
        )
        _require(fixture, "create_family_revision", revision.state, revision.refusal)
        family_revision_ids[seed.revision_id] = revision.revision_id
        for member_id, invariant_revision_id in zip(seed.member_ids, members, strict=True):
            member = memberships.create_family_member(
                store,
                FamilyMemberRequest(
                    repository_id=fixture.repository_id,
                    member=FamilyMemberDraft(
                        member_id=member_id,
                        family_revision_id=seed.revision_id,
                        invariant_revision_id=invariant_revision_id,
                        provenance=fixture.authorship,
                    ),
                ),
            )
            _require(fixture, "create_family_member", member.state, member.refusal)
            member_ids[member_id] = member.member_id
    return family_ids, family_revision_ids, member_ids


def _create_realizations(
    store: RowStore, fixture: BranchingKnowledgeFixture, authorship: Authorship
) -> tuple[dict[str, str], dict[str, str]]:
    recordings: tuple[tuple[FixtureRealization, SourceLocator, RealizationRole, str], ...] = (
        (
            fixture.integration,
            FileLocator(),
            INTEGRATION_ROLE,
            INTEGRATION_RATIONALE,
        ),
        (
            fixture.synchronization,
            LineRangeLocator(start_line=5, end_line=9),
            SYNCHRONIZATION_ROLE,
            SYNCHRONIZATION_RATIONALE,
        ),
        (
            fixture.absent_source,
            FileLocator(),
            ABSENT_ROLE,
            ABSENT_RATIONALE,
        ),
    )
    claim_ids: dict[str, str] = {}
    anchor_ids: dict[str, str] = {}
    for realization, locator, role, rationale in recordings:
        created = realizations.create_realization_claim(
            store,
            RealizationClaimRequest(
                repository_id=fixture.repository_id,
                claim=RealizationClaimDraft(
                    claim_id=realization.claim_id,
                    invariant_revision_id=fixture.base_revision_id,
                    role=role,
                    rationale=rationale,
                ),
                anchor=NewAnchor(
                    anchor=SourceAnchorDraft(
                        anchor_id=UUID(realization.anchor_id),
                        path=realization.path,
                        source_identity=GitBlobIdentity(object_id=_blob_identity(realization.path)),
                        locator=locator,
                    )
                ),
                provenance=authorship,
            ),
        )
        _require(fixture, "create_realization_claim", created.state, created.refusal)
        claim_ids[realization.claim_id] = created.claim_id
        anchor_ids[realization.anchor_id] = created.anchor_id
    return claim_ids, anchor_ids


def _repository_identity(fixture: BranchingKnowledgeFixture) -> RepositoryIdentity:
    return RepositoryIdentity(
        repository_id=fixture.repository_id, authority_home=REPOSITORY_AUTHORITY_HOME
    )


def _blob_identity(path: str) -> str:
    """Return a stable 40-hex Git object identity for one recorded path.

    A fixture cannot invent a real object, and it must not resolve one either: the anchor records
    the identity the author attributed, and whether that object exists in a snapshot is a
    resolution fact the reader owns.
    """

    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:40]


def _require(
    fixture: BranchingKnowledgeFixture, step: str, state: str, refusal_value: object
) -> None:
    """Fail loudly when a fixture step does not create: a fixture is not a probe."""

    if state != "created":
        raise AssertionError(
            f"knowledge fixture step {step} for repository {fixture.repository_id} returned "
            f"{state!r} instead of 'created': {refusal_value!r}"
        )
