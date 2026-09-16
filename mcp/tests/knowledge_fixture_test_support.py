"""The shared branching knowledge fixture built through the real typed operations.

The fixture exists because the interesting identity behaviour is a *conflict*, not a
constructor call: two divergent successors of one revision carry the same friendly display
version, and both must remain separately addressable. It is built through the public
operation, never by inserting rows, so a later leaf that builds on it inherits a store it can
trust and a scenario it can extend rather than re-invent.

The same store also carries the *graph* half of the shared corpus, because the two halves are one
scenario: the realized invariant revision, its two recorded implementations, and the overlapping
families the selective-read and comparison leaves consume.

* the realized revision is ``base_revision_id`` -- the first revision of the first invariant;
* ``integration`` and ``synchronization`` are its two recorded realizations, and
  ``absent_source`` is a third one whose recorded path exists in no snapshot (it is retained as
  authored, because storage never resolves a source);
* ``family`` is ``{base_revision_id, second_revision_id}`` and ``overlapping_family`` is
  ``{second_revision_id, third_revision_id}``, so the sibling revision belongs to both families
  while the realized revision belongs to exactly one;
* ``family_successor_revision_id`` and ``family_sibling_revision_id`` are two concurrent
  successors of the family's first revision that both display ``v2``. Neither carries a
  membership, which is what makes "a newer revision does not silently re-point an existing
  membership" observable: the membership authored against the first revision still resolves the
  first revision's guarantee, and a newer composition has to be authored explicitly.

:func:`build_removed_relation_successor` produces the second, successor candidate dataset: a
database-level clone of this one with exactly one realization claim explicitly removed. The
baseline keeps that claim, so a comparison has a real before side instead of an always-current
pointer that would pretend the removed attribution never existed.

The module is test support, not production code: it decides nothing and is imported by tests
and by later leaves' fixtures only.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from agents_remember.memory.knowledge import families, memberships, realizations
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore, open_knowledge_store
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.family import FamilyRevisionDraft
from agents_remember.models.knowledge.graph import (
    FamilyMemberDraft,
    RealizationClaimDraft,
    RealizationRole,
)
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import (
    FamilyMemberRequest,
    FamilyRequest,
    FamilyRevisionRequest,
    InvariantRequest,
    NewAnchor,
    RealizationClaimRequest,
    RevisionDraft,
    RevisionRequest,
)
from agents_remember.models.knowledge.source import (
    FileLocator,
    GitBlobIdentity,
    LineRangeLocator,
    SourceAnchorDraft,
    SourceLocator,
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

# The families, their guarantees and the successor case. The sibling successors share one display
# version on purpose: a display version is a label, never a winner.
FAMILY_LABEL = "approved-state-family"
FAMILY_GUARANTEE = (
    "Approved state survives every admitted candidate write, and the deciding attribution is "
    "recorded beside it."
)
FAMILY_SUCCESSOR_GUARANTEE = (
    "Approved state survives every admitted candidate write, and the deciding attribution and "
    "batch boundary are recorded beside it."
)
FAMILY_SIBLING_GUARANTEE = (
    "Approved state survives every admitted candidate write, and every reopened candidate "
    "resolves the same identity it was authored against."
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
class RevisionClauses:
    """The optional clause variations a caller applies to one further fixture revision."""

    display_version: str = SHARED_SUCCESSOR_DISPLAY_VERSION
    conditions: tuple[str, ...] = BRANCH_A_CONDITIONS


@dataclass(frozen=True)
class FixtureRealization:
    """One recorded realization: its anchor, its claim and the path the anchor names."""

    anchor_id: str
    claim_id: str
    path: str


@dataclass(frozen=True)
class FixtureFamily:
    """One family: its identity, its first revision and the memberships authored against it."""

    family_id: str
    revision_id: str
    member_ids: tuple[str, str]


@dataclass(frozen=True)
class BranchingKnowledgeFixture:
    """One repository with the identity scenario and the graph scenario in one candidate."""

    database_path: Path
    repository_id: str
    invariant_id: str
    base_revision_id: str
    left_revision_id: str
    right_revision_id: str
    authorship: Authorship
    second_invariant_id: str
    second_revision_id: str
    third_invariant_id: str
    third_revision_id: str
    family: FixtureFamily
    family_successor_revision_id: str
    family_sibling_revision_id: str
    overlapping_family: FixtureFamily
    integration: FixtureRealization
    synchronization: FixtureRealization
    absent_source: FixtureRealization

    def reopen(self) -> OpenedKnowledgeStore:
        """Reopen the fixture store so a test reads what was really persisted."""

        return open_knowledge_store(self.database_path, self.repository_id)


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
    """Create the fixture store through the public operations and return its identities.

    I0 is authored with statement, applicability, conditions, exclusions and provenance.
    I-A and I-B are then authored as successors of I0, both displaying ``v2`` with different
    statements. The graph half follows through the same public operations: two further
    invariants, the overlapping families, the two realizations of I0 and the absent-source claim.
    The call leaves the store closed, so a caller reopens it to read.
    """

    directory.mkdir(parents=True, exist_ok=True)
    authorship = make_authorship()
    fixture = BranchingKnowledgeFixture(
        database_path=directory / database_name,
        repository_id=repository_id or str(uuid4()),
        invariant_id=invariant_id or str(uuid4()),
        base_revision_id=str(uuid4()),
        left_revision_id=str(uuid4()),
        right_revision_id=str(uuid4()),
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
        family_successor_revision_id=str(uuid4()),
        family_sibling_revision_id=str(uuid4()),
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
        _build_identity_half(store, fixture, authorship)
        _build_graph_half(store, fixture, authorship)
    finally:
        store.close()
    return fixture


def fixture_revision_draft(
    fixture: BranchingKnowledgeFixture,
    revision_id: str,
    *,
    statement: str,
    predecessors: tuple[str, ...],
    clauses: RevisionClauses | None = None,
) -> RevisionDraft:
    """Build one further revision draft of the fixture's invariant for extension scenarios."""

    resolved = clauses or RevisionClauses()
    return RevisionDraft(
        revision_id=revision_id,
        invariant_id=fixture.invariant_id,
        display_version=resolved.display_version,
        statement=statement,
        applicability=ESSENTIAL_APPLICABILITY,
        conditions=resolved.conditions,
        exclusions=ESSENTIAL_EXCLUSIONS,
        predecessors=predecessors,
        provenance=fixture.authorship,
    )


@dataclass(frozen=True)
class _FamilyRevisionSeed:
    """One family revision of the fixture to draft: identity, guarantee, version and predecessors."""

    family_id: str
    revision_id: str
    guarantee: str
    predecessors: tuple[str, ...] = ()
    display_version: str = FAMILY_DISPLAY_VERSION


@dataclass(frozen=True)
class _FamilySeed:
    """The identity, first revision, label and guarantee of one family to create."""

    family_id: str
    revision_id: str
    label: str
    guarantee: str


def _build_identity_half(
    store: OpenedKnowledgeStore, fixture: BranchingKnowledgeFixture, authorship: Authorship
) -> None:
    repository_result = store.create_repository(_repository_identity(fixture))
    _require(fixture, "create_repository", repository_result.state, repository_result.refusal)
    invariant_result = store.create_invariant(
        InvariantRequest(
            repository_id=fixture.repository_id,
            invariant_id=fixture.invariant_id,
            display_label=INVARIANT_LABEL,
            provenance=authorship,
        )
    )
    _require(fixture, "create_invariant", invariant_result.state, invariant_result.refusal)
    for revision in _fixture_revisions(fixture, authorship):
        revision_result = store.create_revision(revision)
        _require(fixture, "create_revision", revision_result.state, revision_result.refusal)


def _build_graph_half(
    store: OpenedKnowledgeStore, fixture: BranchingKnowledgeFixture, authorship: Authorship
) -> None:
    _create_second_and_third_invariants(store, fixture, authorship)
    _create_families(store, fixture)
    _create_realizations(store, fixture, authorship)


def _create_second_and_third_invariants(
    store: OpenedKnowledgeStore, fixture: BranchingKnowledgeFixture, authorship: Authorship
) -> None:
    for invariant_id, revision_id, label, statement in (
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
        created = store.create_invariant(
            InvariantRequest(
                repository_id=fixture.repository_id,
                invariant_id=invariant_id,
                display_label=label,
                provenance=authorship,
            )
        )
        _require(fixture, "create_invariant", created.state, created.refusal)
        revision = store.create_revision(
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
        _require(fixture, "create_revision", revision.state, revision.refusal)


def _create_families(store: OpenedKnowledgeStore, fixture: BranchingKnowledgeFixture) -> None:
    _create_family(
        store,
        fixture,
        _FamilySeed(
            family_id=fixture.family.family_id,
            revision_id=fixture.family.revision_id,
            label=FAMILY_LABEL,
            guarantee=FAMILY_GUARANTEE,
        ),
    )
    for revision_id, guarantee in (
        (fixture.family_successor_revision_id, FAMILY_SUCCESSOR_GUARANTEE),
        (fixture.family_sibling_revision_id, FAMILY_SIBLING_GUARANTEE),
    ):
        successor = families.create_family_revision(
            store,
            FamilyRevisionRequest(
                repository_id=fixture.repository_id,
                revision=_family_draft(
                    fixture,
                    _FamilyRevisionSeed(
                        family_id=fixture.family.family_id,
                        revision_id=revision_id,
                        guarantee=guarantee,
                        predecessors=(fixture.family.revision_id,),
                        display_version=SHARED_SUCCESSOR_DISPLAY_VERSION,
                    ),
                ),
            ),
        )
        _require(fixture, "create_family_revision", successor.state, successor.refusal)
    _create_family(
        store,
        fixture,
        _FamilySeed(
            family_id=fixture.overlapping_family.family_id,
            revision_id=fixture.overlapping_family.revision_id,
            label=OVERLAPPING_FAMILY_LABEL,
            guarantee=OVERLAPPING_FAMILY_GUARANTEE,
        ),
    )
    for member_id, invariant_revision_id in zip(
        fixture.family.member_ids,
        (fixture.base_revision_id, fixture.second_revision_id),
        strict=True,
    ):
        _create_membership(
            store, fixture, member_id, fixture.family.revision_id, invariant_revision_id
        )
    for member_id, invariant_revision_id in zip(
        fixture.overlapping_family.member_ids,
        (fixture.second_revision_id, fixture.third_revision_id),
        strict=True,
    ):
        _create_membership(
            store, fixture, member_id, fixture.overlapping_family.revision_id, invariant_revision_id
        )


def _create_family(
    store: OpenedKnowledgeStore, fixture: BranchingKnowledgeFixture, seed: _FamilySeed
) -> None:
    created = families.create_family(
        store,
        FamilyRequest(
            repository_id=fixture.repository_id,
            family_id=seed.family_id,
            display_label=seed.label,
            provenance=fixture.authorship,
        ),
    )
    _require(fixture, "create_family", created.state, created.refusal)
    revision = families.create_family_revision(
        store,
        FamilyRevisionRequest(
            repository_id=fixture.repository_id,
            revision=_family_draft(
                fixture,
                _FamilyRevisionSeed(
                    family_id=seed.family_id,
                    revision_id=seed.revision_id,
                    guarantee=seed.guarantee,
                ),
            ),
        ),
    )
    _require(fixture, "create_family_revision", revision.state, revision.refusal)


def _create_membership(
    store: OpenedKnowledgeStore,
    fixture: BranchingKnowledgeFixture,
    member_id: str,
    family_revision_id: str,
    invariant_revision_id: str,
) -> None:
    created = memberships.create_family_member(
        store,
        FamilyMemberRequest(
            repository_id=fixture.repository_id,
            member=FamilyMemberDraft(
                member_id=member_id,
                family_revision_id=family_revision_id,
                invariant_revision_id=invariant_revision_id,
                provenance=fixture.authorship,
            ),
        ),
    )
    _require(fixture, "create_family_member", created.state, created.refusal)


def _create_realizations(
    store: OpenedKnowledgeStore, fixture: BranchingKnowledgeFixture, authorship: Authorship
) -> None:
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


def _repository_identity(fixture: BranchingKnowledgeFixture) -> RepositoryIdentity:
    return RepositoryIdentity(
        repository_id=fixture.repository_id, authority_home=REPOSITORY_AUTHORITY_HOME
    )


def _fixture_revisions(
    fixture: BranchingKnowledgeFixture, authorship: Authorship
) -> tuple[RevisionRequest, ...]:
    base = RevisionDraft(
        revision_id=fixture.base_revision_id,
        invariant_id=fixture.invariant_id,
        display_version=BASE_DISPLAY_VERSION,
        statement=BASE_STATEMENT,
        applicability=ESSENTIAL_APPLICABILITY,
        conditions=BASE_CONDITIONS,
        exclusions=ESSENTIAL_EXCLUSIONS,
        provenance=authorship,
    )
    return (
        RevisionRequest(repository_id=fixture.repository_id, revision=base),
        RevisionRequest(
            repository_id=fixture.repository_id,
            revision=fixture_revision_draft(
                fixture,
                fixture.left_revision_id,
                statement=BRANCH_A_STATEMENT,
                predecessors=(fixture.base_revision_id,),
            ),
        ),
        RevisionRequest(
            repository_id=fixture.repository_id,
            revision=fixture_revision_draft(
                fixture,
                fixture.right_revision_id,
                statement=BRANCH_B_STATEMENT,
                predecessors=(fixture.base_revision_id,),
                clauses=RevisionClauses(conditions=BRANCH_B_CONDITIONS),
            ),
        ),
    )


def _blob_identity(path: str) -> str:
    """Return a stable 40-hex Git object identity for one recorded path.

    A fixture cannot invent a real object, and it must not resolve one either: the anchor records
    the identity the author attributed, and whether that object exists in a snapshot is a
    resolution fact the reader owns.
    """

    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:40]


def _family_draft(
    fixture: BranchingKnowledgeFixture, seed: _FamilyRevisionSeed
) -> FamilyRevisionDraft:
    """Build one family revision draft of the fixture for either family.

    The display version defaults to the first revision's label; the concurrent successors pass the
    shared successor label explicitly, because two revisions displaying the same version is the
    supported state this fixture exists to carry.
    """

    return FamilyRevisionDraft(
        family_id=seed.family_id,
        revision_id=seed.revision_id,
        display_version=seed.display_version,
        joint_guarantee=seed.guarantee,
        predecessors=seed.predecessors,
        provenance=fixture.authorship,
    )


def _require(
    fixture: BranchingKnowledgeFixture, step: str, state: str, refusal_value: object
) -> None:
    """Fail loudly when a fixture step does not create: a fixture is not a probe."""

    if state != "created":
        raise AssertionError(
            f"knowledge fixture step {step} for repository {fixture.repository_id} returned "
            f"{state!r} instead of 'created': {refusal_value!r}"
        )
