"""The read-scope fixture: the packet's ``P -> I1``, ``F1 -> {I1, J1}``, ``G1 -> {J1, K1}`` graph.

The shared branching fixture already carries one invariant with two realizations, but the selective
read is decided by a topology that fixture does not have: a *sibling* invariant that one family
shares with a *second* family the seed never reaches. That shape is the whole stopping rule, and it
is what this fixture exists to carry, so it is built here rather than bent into the other one.

Built as a text memory tree through the rows builder, never by inserting rows, so every identity
in it is one the projection produced:

* ``I1`` is the retry-budget invariant's single text revision, and it carries two realization
  claims at two distinct source locations (``integration`` and ``synchronization``). A text record
  holds exactly one revision (MIK-R74 rule 3), so the predecessor/successor revisions the old
  fixture authored beside it are gone: the cases that needed several revisions of one identity in
  one dataset tested a shape no tree can express, and they are deleted and named in the leaf's
  report;
* ``J1`` is the candidate-batch invariant's single revision. It carries **two claims at one
  location** (``batch``), which is what makes "two claim identities, one distinct source location"
  measurable, and it is the sibling the frontier advertises;
* ``K1`` is the source-resolution invariant's single revision, reachable only through ``G1``;
* ``F1`` is ``{I1, J1}`` and ``G1`` is ``{J1, K1}``, so ``J1`` is in both and ``K1`` is in the one
  the seed does not reach;
* ``H1`` is ``{I1, L1}``, a **second** family that directly contains ``I1``, which is what proves
  the path/invariant rule includes every directly containing family rather than only the first.

``absent_anchor`` is a recorded claim whose path exists in no tree, and ``mismatch_anchor`` is a
claim whose recorded blob identity is deliberately not the one the fixture repository holds at that
path. Both are authored like any other claim: storage never resolves a source, so whether a path
resolves is a fact about a snapshot and not a condition for recording it.
"""

from __future__ import annotations

import dataclasses
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore, open_read_only_store
from agents_remember.memory.knowledge_index.projection import text_uuid
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.base import KnowledgeState
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
    _entry_text_id,
    families,
    memberships,
    open_knowledge_store,
    realizations,
)

REPOSITORY_AUTHORITY_HOME = "agents-remember"

# The invariants, their labels and their statements. Stable prose so a reopened read is meaningful.
RETRY_LABEL = "shared-retry-budget"
RETRY_STATEMENT = "Retries share one budget across integration and synchronization."
BATCH_LABEL = "candidate-batch-atomicity"
BATCH_STATEMENT = "Apply every admitted candidate change as one transaction."
RESOLUTION_LABEL = "source-resolution-visibility"
RESOLUTION_STATEMENT = "Report an unresolved recorded source location as a resolution fact."
AUXILIARY_LABEL = "anchor-identity-preservation"
AUXILIARY_STATEMENT = "Preserve a recorded source identity when its location moves."

APPLICABILITY = "Every admitted candidate write in this repository namespace."
CONDITIONS = ("The candidate write is admitted for this namespace.",)
SUCCESSOR_CONDITIONS = (
    "The candidate write is admitted for this namespace.",
    "The shared attempt and timeout configuration is read once.",
)
EXCLUSIONS = ("Historical rows are not rewritten by a candidate write.",)
SUBJECT_LABEL = "v2"
BASE_LABEL = "v1"

FAMILY_LABEL = "retry-budget-family"
FAMILY_GUARANTEE = "The retry budget is shared by integration and synchronization."
OVERLAPPING_FAMILY_LABEL = "retry-and-resolution-family"
OVERLAPPING_FAMILY_GUARANTEE = (
    "The retry budget and the resolution-visibility obligation are recorded together."
)
DIRECT_FAMILY_LABEL = "retry-and-anchor-family"
DIRECT_FAMILY_GUARANTEE = "The retry budget and the anchor identity rule hold together."

# The source locations. ``INTEGRATION_PATH`` and ``SYNCHRONIZATION_PATH`` are two claims of I1 at
# two locations; ``BATCH_PATH`` carries the two claims that share one location.
INTEGRATION_PATH = "src/integration.py"
INTEGRATION_FILE_TEXT = (
    "# integration\nshared retry budget\n\n\nclass SharedBudget:\n"
    "    def retry_budget(self) -> int:\n        return 3\n"
)
SYNCHRONIZATION_PATH = "src/synchronization.py"
ABSENT_PATH = "src/retired_adapter.py"
BATCH_PATH = "src/batch.py"
RESOLUTION_PATH = "src/resolution.py"
AUXILIARY_PATH = "src/anchors.py"
MISMATCH_PATH = "src/timeout.py"
# A recorded path whose language the shipped extractor has NO grammar for. It is what makes the read
# rail's one remaining ``unsupported_locator`` reachable from the fixture: a symbol cannot be told
# from a mention in a language nothing parses, so the observation reports that instead of guessing.
UNPARSED_PATH = "src/schema.sql"

# The blob identity recorded for the mismatch claim is deliberately not the one the fixture
# repository holds at that path, so the observation is a real comparison and not a fixture fact.
MISMATCH_RECORDED_BLOB = "0" * 40

# The absent claim records a well-formed identity for a path the fixture tree does not carry, so
# "the path is absent" is an observation about the tree rather than a malformed stored anchor.
ABSENT_RECORDED_BLOB = "1" * 40


@dataclass(frozen=True)
class ReadFixtureRealization:
    """One recorded realization: its anchor, its claim and the path the anchor names."""

    anchor_id: str
    claim_id: str
    path: str


@dataclass(frozen=True)
class RevisionSeed:
    """One revision to author: its identity, its label, its text and its predecessor set."""

    invariant_id: str
    revision_id: str
    display_version: str
    statement: str
    conditions: tuple[str, ...]
    predecessors: tuple[str, ...]
    applicability: str = ""
    exclusions: tuple[str, ...] = ()
    state_at_origin: KnowledgeState = "proposed"


@dataclass(frozen=True)
class RealizationSeed:
    """One realization to author: its claim, its anchor, its role and its recorded blob."""

    realization: ReadFixtureRealization
    revision_id: str
    role: RealizationRole
    locator: SourceLocator
    recorded_blob: str
    rationale: str = ""


@dataclass(frozen=True)
class ExtraFamilySeed:
    """One whole family the tree holds beside the three it always holds.

    The identities are caller-minted; the member slots name the tree's own revision slots
    (``"subject"``, ``"batch"``, ``"resolution"``, ``"auxiliary"``), resolved when the tree is
    built. A family only one side's tree holds is what makes a one-sided context measurable.
    """

    family_id: str
    revision_id: str
    label: str
    guarantee: str
    member_slots: tuple[str, ...]


@dataclass(frozen=True)
class ExtraInvariantSeed:
    """One whole invariant the tree holds beside the four it always holds.

    The identities are caller-minted; the tree authors exactly one revision with these words
    and no membership. An invariant only one side's tree holds is what makes a measured zero
    family context (``no_family_recorded``) measurable. The text ID derives from the
    caller-minted invariant UUID rather than the authoring position, so the same caller
    identity projects the same record on both sides of a comparison while two different
    caller identities never share one.
    """

    invariant_id: str
    revision_id: str
    label: str
    statement: str
    applicability: str
    conditions: tuple[str, ...]
    exclusions: tuple[str, ...]
    state_at_origin: KnowledgeState = "proposed"


@dataclass(frozen=True)
class ReadFixtureExtraInvariant:
    """One extra invariant's projected identities: the invariant and its single revision."""

    invariant_id: str
    revision_id: str


# A seed revision that means the tree's own retry subject revision: an extra claim for a side
# whose subject revision identity the caller cannot know before the tree is built.
SUBJECT_REVISION = "subject-revision"
# The same for the tree's own batch revision: a re-recorded citation of the sibling invariant.
BATCH_REVISION = "batch-revision"


@dataclass(frozen=True)
class ReadFixtureFamily:
    """One family: its identity, its single revision and its membership rows."""

    family_id: str
    revision_id: str
    member_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReadScopeFixture:
    """One repository holding the packet's selection topology, plus a real Git tree beside it."""

    database_path: Path
    repository_id: str
    authorship: Authorship

    retry_invariant_id: str
    subject_revision_id: str

    batch_invariant_id: str
    batch_revision_id: str

    resolution_invariant_id: str
    resolution_revision_id: str

    auxiliary_invariant_id: str
    auxiliary_revision_id: str

    family: ReadFixtureFamily
    overlapping_family: ReadFixtureFamily
    direct_family: ReadFixtureFamily

    integration: ReadFixtureRealization
    synchronization: ReadFixtureRealization
    batch_primary: ReadFixtureRealization
    batch_secondary: ReadFixtureRealization
    resolution: ReadFixtureRealization
    auxiliary: ReadFixtureRealization
    absent_anchor: ReadFixtureRealization
    mismatch_anchor: ReadFixtureRealization

    git_root: Path
    git_tree_id: str
    git_blobs: Mapping[str, str]
    extra_families: tuple[ReadFixtureFamily, ...] = ()
    extra_invariants: tuple[ReadFixtureExtraInvariant, ...] = ()

    def reopen(self) -> OpenedKnowledgeStore:
        """Reopen the fixture store read-only, so a case reads what was really persisted."""

        return open_read_only_store(self.database_path, self.repository_id)

    @property
    def knowledge_digest(self) -> str:
        """The logical digest of the dataset this fixture holds, read from the real file.

        A case needs it to state what a continuation or a context binds without recomputing it by
        hand, so the value cases compare against is the one the store itself reports.
        """

        return dataset_identity(self.database_path).logical_digest

    def subject_claim_ids(self) -> tuple[str, ...]:
        """The claims of the subject revision plus its sibling, as a stable sorted set."""

        return tuple(
            sorted(
                (
                    self.integration.claim_id,
                    self.synchronization.claim_id,
                    self.batch_primary.claim_id,
                    self.batch_secondary.claim_id,
                )
            )
        )


def make_read_authorship(*, seed: str = "agent:read-fixture") -> Authorship:
    """Build one provenance envelope whose operation id is unique to this fixture build."""

    del seed
    return Authorship(
        actor_ref="agent:read-fixture",
        authorization_ref="260915-KS developer kickoff ruling",
        operation_id=uuid4(),
        recorded_at=datetime.now(UTC).isoformat(),
        origin_refs=("requirement:KS-R07@v1",),
    )


def build_read_scope_fixture(  # noqa: PLR0913 -- one builder serves both sides of a comparison
    directory: Path,
    *,
    repository_id: str | None = None,
    database_name: str = "knowledge-candidate.db",
    retry_text: tuple[str, str, tuple[str, ...]] | None = None,
    omit_claim_paths: tuple[str, ...] = (),
    extra_claims: tuple[RealizationSeed, ...] = (),
    direct_family_revision_number: int = 1,
    omit_members: tuple[tuple[str, str], ...] = (),
    direct_family_guarantee: str | None = None,
    extra_families: tuple[ExtraFamilySeed, ...] = (),
    extra_invariants: tuple[ExtraInvariantSeed, ...] = (),
    add_members: tuple[tuple[str, str], ...] = (),
) -> ReadScopeFixture:
    """Create the fixture store and its Git tree, then return every identity in it.

    The namespace argument is accepted and not used: every index is keyed by its tree alone, so
    the fixture reads under the index's own namespace. ``retry_text`` replaces the retry
    invariant's ``(display_version, statement, conditions)`` -- a comparison's candidate side is
    the same topology with a revised statement. ``omit_claim_paths`` leaves those claims out of
    the tree, and ``extra_claims`` authors further realizations, so one builder serves both sides
    of a comparison while the entry identities of the claims both sides keep stay identical.
    ``direct_family_revision_number`` writes the direct family's record at another revision
    number -- the membership rows keep their (family, invariant) identities, so a moved family
    revision pairs its members by row instead of dropping them. ``omit_members`` names
    ``(family field, revision field)`` pairs of the fixture whose membership row is left out.
    ``direct_family_guarantee`` replaces the direct family's stored guarantee text, so a moved
    family revision carries its own authored sentence. ``extra_families`` holds whole further
    families beside the three the tree always holds. ``extra_invariants`` holds whole further
    invariants beside the four the tree always holds, each with exactly one revision and no
    membership. ``add_members`` names ``(family field, revision field)`` pairs of the fixture
    whose membership row is added to the family's own revision -- a member joining a roster
    the tree otherwise holds unchanged.
    """

    _ = repository_id
    fixture = _seed_identities(directory, database_name)
    _write_git_tree(fixture)
    store = open_knowledge_store(fixture.database_path, fixture.repository_id)
    try:
        invariant_ids, revision_ids, extra_invariant_ids, extra_revision_ids = _build_invariants(
            store, fixture, retry_text=retry_text, extra_invariants=extra_invariants
        )
        family_ids, family_revision_ids, member_ids = _build_families(
            store,
            fixture,
            direct_family_revision_number=direct_family_revision_number,
            omit_members=omit_members,
            direct_family_guarantee=direct_family_guarantee,
            extra_families=extra_families,
            add_members=add_members,
        )
        claim_ids, anchor_ids = _build_realizations(
            store, fixture, omit_claim_paths=omit_claim_paths, extra_claims=extra_claims
        )
    finally:
        store.close()
    return dataclasses.replace(
        fixture,
        retry_invariant_id=invariant_ids[fixture.retry_invariant_id],
        subject_revision_id=revision_ids[fixture.subject_revision_id],
        batch_invariant_id=invariant_ids[fixture.batch_invariant_id],
        batch_revision_id=revision_ids[fixture.batch_revision_id],
        resolution_invariant_id=invariant_ids[fixture.resolution_invariant_id],
        resolution_revision_id=revision_ids[fixture.resolution_revision_id],
        auxiliary_invariant_id=invariant_ids[fixture.auxiliary_invariant_id],
        auxiliary_revision_id=revision_ids[fixture.auxiliary_revision_id],
        family=_project_family(fixture.family, family_ids, family_revision_ids, member_ids),
        overlapping_family=_project_family(
            fixture.overlapping_family, family_ids, family_revision_ids, member_ids
        ),
        direct_family=_project_family(
            fixture.direct_family, family_ids, family_revision_ids, member_ids
        ),
        extra_families=tuple(
            ReadFixtureFamily(
                family_id=family_ids[seed.family_id],
                revision_id=family_revision_ids[seed.revision_id],
                member_ids=(),
            )
            for seed in extra_families
        ),
        extra_invariants=tuple(
            ReadFixtureExtraInvariant(
                invariant_id=extra_invariant_ids[seed.invariant_id],
                revision_id=extra_revision_ids[seed.revision_id],
            )
            for seed in extra_invariants
        ),
        integration=_project_realization(fixture.integration, claim_ids, anchor_ids),
        synchronization=_project_realization(fixture.synchronization, claim_ids, anchor_ids),
        batch_primary=_project_realization(fixture.batch_primary, claim_ids, anchor_ids),
        batch_secondary=_project_realization(fixture.batch_secondary, claim_ids, anchor_ids),
        resolution=_project_realization(fixture.resolution, claim_ids, anchor_ids),
        auxiliary=_project_realization(fixture.auxiliary, claim_ids, anchor_ids),
        absent_anchor=_project_realization(fixture.absent_anchor, claim_ids, anchor_ids),
        mismatch_anchor=_project_realization(fixture.mismatch_anchor, claim_ids, anchor_ids),
    )


def _project_family(
    seed: ReadFixtureFamily,
    family_ids: dict[str, str],
    family_revision_ids: dict[str, str],
    member_ids: dict[str, str],
) -> ReadFixtureFamily:
    """Resolve one family's caller-side identities to the projected identities the tree holds.

    A membership the tree omits projects to an empty identity, like an omitted claim.
    """

    return ReadFixtureFamily(
        family_id=family_ids[seed.family_id],
        revision_id=family_revision_ids[seed.revision_id],
        member_ids=tuple(member_ids.get(member_id, "") for member_id in seed.member_ids),
    )


def _project_realization(
    seed: ReadFixtureRealization, claim_ids: dict[str, str], anchor_ids: dict[str, str]
) -> ReadFixtureRealization:
    """Resolve one realization's caller-side identities to the projected ones the tree holds.

    A claim the tree omits projects to empty identities: the field stays addressable, and no
    row answers for it.
    """

    return ReadFixtureRealization(
        anchor_id=anchor_ids.get(seed.anchor_id, ""),
        claim_id=claim_ids.get(seed.claim_id, ""),
        path=seed.path,
    )


def _seed_identities(directory: Path, database_name: str) -> ReadScopeFixture:
    """Allocate every identity the fixture needs before one statement runs."""

    directory.mkdir(parents=True, exist_ok=True)
    return ReadScopeFixture(
        database_path=directory / database_name,
        repository_id=INDEX_REPOSITORY_ID,
        authorship=make_read_authorship(),
        retry_invariant_id=str(uuid4()),
        subject_revision_id=str(uuid4()),
        batch_invariant_id=str(uuid4()),
        batch_revision_id=str(uuid4()),
        resolution_invariant_id=str(uuid4()),
        resolution_revision_id=str(uuid4()),
        auxiliary_invariant_id=str(uuid4()),
        auxiliary_revision_id=str(uuid4()),
        family=ReadFixtureFamily(
            family_id=str(uuid4()),
            revision_id=str(uuid4()),
            member_ids=(str(uuid4()), str(uuid4())),
        ),
        overlapping_family=ReadFixtureFamily(
            family_id=str(uuid4()),
            revision_id=str(uuid4()),
            member_ids=(str(uuid4()), str(uuid4())),
        ),
        direct_family=ReadFixtureFamily(
            family_id=str(uuid4()),
            revision_id=str(uuid4()),
            member_ids=(str(uuid4()), str(uuid4())),
        ),
        integration=ReadFixtureRealization(
            anchor_id=str(uuid4()), claim_id=str(uuid4()), path=INTEGRATION_PATH
        ),
        synchronization=ReadFixtureRealization(
            anchor_id=str(uuid4()), claim_id=str(uuid4()), path=SYNCHRONIZATION_PATH
        ),
        batch_primary=ReadFixtureRealization(
            anchor_id=str(uuid4()), claim_id=str(uuid4()), path=BATCH_PATH
        ),
        batch_secondary=ReadFixtureRealization(
            anchor_id=str(uuid4()), claim_id=str(uuid4()), path=BATCH_PATH
        ),
        resolution=ReadFixtureRealization(
            anchor_id=str(uuid4()), claim_id=str(uuid4()), path=RESOLUTION_PATH
        ),
        auxiliary=ReadFixtureRealization(
            anchor_id=str(uuid4()), claim_id=str(uuid4()), path=AUXILIARY_PATH
        ),
        absent_anchor=ReadFixtureRealization(
            anchor_id=str(uuid4()), claim_id=str(uuid4()), path=ABSENT_PATH
        ),
        mismatch_anchor=ReadFixtureRealization(
            anchor_id=str(uuid4()), claim_id=str(uuid4()), path=MISMATCH_PATH
        ),
        git_root=directory / "code",
        git_tree_id="",
        git_blobs={},
    )


def _build_invariants(
    store: RowStore,
    fixture: ReadScopeFixture,
    *,
    retry_text: tuple[str, str, tuple[str, ...]] | None = None,
    extra_invariants: tuple[ExtraInvariantSeed, ...] = (),
) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
    """Author the four invariant identities and each one's single text revision."""

    created = store.create_repository(
        RepositoryIdentity(
            repository_id=fixture.repository_id, authority_home=REPOSITORY_AUTHORITY_HOME
        )
    )
    _require("create_repository", created.state, created.refusal)
    invariant_ids: dict[str, str] = {}
    revision_ids: dict[str, str] = {}
    for invariant_id, label, revisions in _invariant_seeds(fixture, retry_text=retry_text):
        outcome = _create_invariant(store, fixture, invariant_id, label)
        invariant_ids[invariant_id] = outcome.invariant_id
        for seed in revisions:
            revision = _create_revision(store, fixture, seed)
            revision_ids[seed.revision_id] = revision.revision_id
    extra_invariant_ids: dict[str, str] = {}
    extra_revision_ids: dict[str, str] = {}
    for extra in extra_invariants:
        outcome = _create_invariant(store, fixture, extra.invariant_id, extra.label, extra=True)
        extra_invariant_ids[extra.invariant_id] = outcome.invariant_id
        revision = _create_revision(
            store,
            fixture,
            RevisionSeed(
                invariant_id=extra.invariant_id,
                revision_id=extra.revision_id,
                display_version=BASE_LABEL,
                statement=extra.statement,
                conditions=extra.conditions,
                predecessors=(),
                applicability=extra.applicability,
                exclusions=extra.exclusions,
                state_at_origin=extra.state_at_origin,
            ),
        )
        extra_revision_ids[extra.revision_id] = revision.revision_id
    return invariant_ids, revision_ids, extra_invariant_ids, extra_revision_ids


def _invariant_seeds(
    fixture: ReadScopeFixture,
    *,
    retry_text: tuple[str, str, tuple[str, ...]] | None = None,
) -> tuple[tuple[str, str, tuple[RevisionSeed, ...]], ...]:
    """Return the four invariants and the single revision of each, in authoring order."""

    return (
        (
            fixture.retry_invariant_id,
            RETRY_LABEL,
            (
                _revision(
                    fixture,
                    fixture.subject_revision_id,
                    text=retry_text or (SUBJECT_LABEL, RETRY_STATEMENT, SUCCESSOR_CONDITIONS),
                ),
            ),
        ),
        (
            fixture.batch_invariant_id,
            BATCH_LABEL,
            (
                _revision(
                    fixture,
                    fixture.batch_revision_id,
                    text=(BASE_LABEL, BATCH_STATEMENT, CONDITIONS),
                ),
            ),
        ),
        (
            fixture.resolution_invariant_id,
            RESOLUTION_LABEL,
            (
                _revision(
                    fixture,
                    fixture.resolution_revision_id,
                    text=(BASE_LABEL, RESOLUTION_STATEMENT, CONDITIONS),
                ),
            ),
        ),
        (
            fixture.auxiliary_invariant_id,
            AUXILIARY_LABEL,
            (
                _revision(
                    fixture,
                    fixture.auxiliary_revision_id,
                    text=(BASE_LABEL, AUXILIARY_STATEMENT, CONDITIONS),
                ),
            ),
        ),
    )


def _revision(
    fixture: ReadScopeFixture,
    revision_id: str,
    *,
    text: tuple[str, str, tuple[str, ...]],
    predecessors: tuple[str, ...] = (),
) -> RevisionSeed:
    """Build one revision seed from its authored text and its place in the lineage.

    ``text`` is ``(display_version, statement, conditions)`` -- the three things an author decides
    about one revision -- so a caller names the revision and its words without a six-position
    argument list that a reader has to count. ``predecessors`` is carried and not stored: text
    records hold no predecessor edge (MIK-R74 rule 1).
    """

    display_version, statement, conditions = text
    return RevisionSeed(
        invariant_id=_invariant_of(fixture, revision_id),
        revision_id=revision_id,
        display_version=display_version,
        statement=statement,
        conditions=conditions,
        predecessors=predecessors,
    )


def _create_invariant(
    store: RowStore,
    fixture: ReadScopeFixture,
    invariant_id: str,
    label: str,
    *,
    extra: bool = False,
):
    request = InvariantRequest(
        repository_id=fixture.repository_id,
        invariant_id=invariant_id,
        display_label=label,
        provenance=fixture.authorship,
    )
    created = (
        store.create_extra_invariant(request)
        if extra
        else store.create_invariant(request)
    )
    _require("create_invariant", created.state, created.refusal)
    return created


def _create_revision(store: RowStore, fixture: ReadScopeFixture, seed: RevisionSeed):
    created = store.create_revision(
        RevisionRequest(
            repository_id=fixture.repository_id,
            revision=RevisionDraft(
                revision_id=seed.revision_id,
                invariant_id=seed.invariant_id,
                display_version=seed.display_version,
                statement=seed.statement,
                applicability=seed.applicability or APPLICABILITY,
                conditions=seed.conditions,
                exclusions=seed.exclusions or EXCLUSIONS,
                predecessors=seed.predecessors,
                state_at_origin=seed.state_at_origin,
                provenance=fixture.authorship,
            ),
        )
    )
    _require("create_revision", created.state, created.refusal)
    return created


def _invariant_of(fixture: ReadScopeFixture, revision_id: str) -> str:
    """Return the invariant identity one fixture revision belongs to."""

    if revision_id == fixture.subject_revision_id:
        return fixture.retry_invariant_id
    if revision_id == fixture.batch_revision_id:
        return fixture.batch_invariant_id
    if revision_id == fixture.resolution_revision_id:
        return fixture.resolution_invariant_id
    if revision_id == fixture.auxiliary_revision_id:
        return fixture.auxiliary_invariant_id
    raise AssertionError(f"{revision_id} is not a revision of this fixture")


def _build_families(  # noqa: PLR0913 -- one call carries a whole family's delta
    store: RowStore,
    fixture: ReadScopeFixture,
    *,
    direct_family_revision_number: int = 1,
    omit_members: tuple[tuple[str, str], ...] = (),
    direct_family_guarantee: str | None = None,
    extra_families: tuple[ExtraFamilySeed, ...] = (),
    add_members: tuple[tuple[str, str], ...] = (),
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Author the three families and the exact memberships the stopping rule is read through."""

    family_ids: dict[str, str] = {}
    family_revision_ids: dict[str, str] = {}
    member_ids: dict[str, str] = {}
    omitted = _omitted_member_rows(fixture, omit_members)
    family_revisions: dict[str, str] = {}
    for seed, members in (
        (fixture.family, (fixture.subject_revision_id, fixture.batch_revision_id)),
        (
            fixture.overlapping_family,
            (fixture.batch_revision_id, fixture.resolution_revision_id),
        ),
        (fixture.direct_family, (fixture.subject_revision_id, fixture.auxiliary_revision_id)),
    ):
        family_revisions[seed.family_id] = seed.revision_id
        created = families.create_family(
            store,
            FamilyRequest(
                repository_id=fixture.repository_id,
                family_id=seed.family_id,
                display_label=_family_label(seed.family_id, fixture),
                provenance=fixture.authorship,
            ),
        )
        _require("create_family", created.state, created.refusal)
        family_ids[seed.family_id] = created.family_id
        revision_number = (
            direct_family_revision_number if seed.family_id == fixture.direct_family.family_id else 1
        )
        guarantee = _family_guarantee(seed.family_id, fixture)
        if seed.family_id == fixture.direct_family.family_id and direct_family_guarantee is not None:
            guarantee = direct_family_guarantee
        revision = families.create_family_revision(
            store,
            FamilyRevisionRequest(
                repository_id=fixture.repository_id,
                revision=FamilyRevisionDraft(
                    family_id=seed.family_id,
                    revision_id=seed.revision_id,
                    display_version=f"v{revision_number}",
                    joint_guarantee=guarantee,
                    provenance=fixture.authorship,
                ),
            ),
        )
        _require("create_family_revision", revision.state, revision.refusal)
        family_revision_ids[seed.revision_id] = revision.revision_id
        for member_id, invariant_revision_id in zip(seed.member_ids, members, strict=True):
            if (seed.family_id, invariant_revision_id) in omitted:
                continue
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
            _require("create_family_member", member.state, member.refusal)
            member_ids[member_id] = member.member_id
    for extra in extra_families:
        created = families.create_family(
            store,
            FamilyRequest(
                repository_id=fixture.repository_id,
                family_id=extra.family_id,
                display_label=extra.label,
                provenance=fixture.authorship,
            ),
        )
        _require("create_family", created.state, created.refusal)
        family_ids[extra.family_id] = created.family_id
        revision = families.create_family_revision(
            store,
            FamilyRevisionRequest(
                repository_id=fixture.repository_id,
                revision=FamilyRevisionDraft(
                    family_id=extra.family_id,
                    revision_id=extra.revision_id,
                    display_version=BASE_LABEL,
                    joint_guarantee=extra.guarantee,
                    provenance=fixture.authorship,
                ),
            ),
        )
        _require("create_family_revision", revision.state, revision.refusal)
        family_revision_ids[extra.revision_id] = revision.revision_id
        slots = {
            "subject": fixture.subject_revision_id,
            "batch": fixture.batch_revision_id,
            "resolution": fixture.resolution_revision_id,
            "auxiliary": fixture.auxiliary_revision_id,
        }
        for slot in extra.member_slots:
            try:
                invariant_revision_id = slots[slot]
            except KeyError:
                raise AssertionError(f"extra family member slot names a revision slot: {slot!r}") from None
            member = memberships.create_family_member(
                store,
                FamilyMemberRequest(
                    repository_id=fixture.repository_id,
                    member=FamilyMemberDraft(
                        member_id=str(uuid4()),
                        family_revision_id=extra.revision_id,
                        invariant_revision_id=invariant_revision_id,
                        provenance=fixture.authorship,
                    ),
                ),
            )
            _require("create_family_member", member.state, member.refusal)
    for family_field, revision_field in add_members:
        family = getattr(fixture, family_field, None)
        revision_id = getattr(fixture, revision_field, None)
        if not isinstance(family, ReadFixtureFamily) or not isinstance(revision_id, str):
            raise AssertionError(
                f"add_members names fixture fields: {family_field!r}, {revision_field!r}"
            )
        member = memberships.create_family_member(
            store,
            FamilyMemberRequest(
                repository_id=fixture.repository_id,
                member=FamilyMemberDraft(
                    member_id=str(uuid4()),
                    family_revision_id=family_revisions[family.family_id],
                    invariant_revision_id=revision_id,
                    provenance=fixture.authorship,
                ),
            ),
        )
        _require("create_family_member", member.state, member.refusal)
    return family_ids, family_revision_ids, member_ids


def _omitted_member_rows(
    fixture: ReadScopeFixture, omit_members: tuple[tuple[str, str], ...]
) -> set[tuple[str, str]]:
    """Resolve ``(family field, revision field)`` pairs to caller-side (family, revision) UUIDs."""

    omitted: set[tuple[str, str]] = set()
    for family_field, revision_field in omit_members:
        family = getattr(fixture, family_field, None)
        revision_id = getattr(fixture, revision_field, None)
        if not isinstance(family, ReadFixtureFamily) or not isinstance(revision_id, str):
            raise AssertionError(
                f"omit_members names fixture fields: {family_field!r}, {revision_field!r}"
            )
        omitted.add((family.family_id, revision_id))
    return omitted


def _family_label(family_id: str, fixture: ReadScopeFixture) -> str:
    return {
        fixture.family.family_id: FAMILY_LABEL,
        fixture.overlapping_family.family_id: OVERLAPPING_FAMILY_LABEL,
        fixture.direct_family.family_id: DIRECT_FAMILY_LABEL,
    }[family_id]


def _family_guarantee(family_id: str, fixture: ReadScopeFixture) -> str:
    return {
        fixture.family.family_id: FAMILY_GUARANTEE,
        fixture.overlapping_family.family_id: OVERLAPPING_FAMILY_GUARANTEE,
        fixture.direct_family.family_id: DIRECT_FAMILY_GUARANTEE,
    }[family_id]


def _build_realizations(
    store: RowStore,
    fixture: ReadScopeFixture,
    *,
    omit_claim_paths: tuple[str, ...] = (),
    extra_claims: tuple[RealizationSeed, ...] = (),
) -> tuple[dict[str, str], dict[str, str]]:
    """Author every realization claim, each citing the recorded blob its repository holds."""

    claim_ids: dict[str, str] = {}
    anchor_ids: dict[str, str] = {}
    omitted = set(omit_claim_paths)
    for seed in _realization_seeds(fixture):
        if seed.realization.path in omitted:
            continue
        created = _create_realization(store, fixture, seed)
        claim_ids[seed.realization.claim_id] = created.claim_id
        anchor_ids[seed.realization.anchor_id] = created.anchor_id
    # The caller's extra claims are explicit additions: they are always authored, even at an
    # omitted path, which is how a comparison re-records a withdrawn address.
    for seed in extra_claims:
        created = _create_realization(store, fixture, seed)
        claim_ids[seed.realization.claim_id] = created.claim_id
        anchor_ids[seed.realization.anchor_id] = created.anchor_id
    return claim_ids, anchor_ids


def _realization_seeds(fixture: ReadScopeFixture) -> tuple[RealizationSeed, ...]:
    """Return every realization to author, with the blob identity each one records.

    Every claim but the mismatch one records the blob the fixture repository really holds at its
    path, so "the recorded identity is the observed identity" is a comparison against real bytes
    rather than two copies of one fixture constant.
    """

    def seed(
        realization: ReadFixtureRealization,
        revision_id: str,
        role: RealizationRole,
        locator: SourceLocator,
        recorded_blob: str | None = None,
    ) -> RealizationSeed:
        return RealizationSeed(
            realization=realization,
            revision_id=revision_id,
            role=role,
            locator=locator,
            recorded_blob=(
                _blob_of(fixture, realization.path) if recorded_blob is None else recorded_blob
            ),
        )

    return (
        seed(fixture.integration, fixture.subject_revision_id, "enforcement", FileLocator()),
        seed(
            fixture.synchronization,
            fixture.subject_revision_id,
            "propagation-persistence",
            LineRangeLocator(start_line=3, end_line=7),
        ),
        seed(fixture.batch_primary, fixture.batch_revision_id, "enforcement", FileLocator()),
        seed(fixture.batch_secondary, fixture.batch_revision_id, "support", FileLocator()),
        seed(fixture.resolution, fixture.resolution_revision_id, "presentation", FileLocator()),
        # The auxiliary claim's role is the vocabulary's unclassified value: the old fixture
        # spelled it "incidental", which no vocabulary -- text or logical -- admits.
        seed(fixture.auxiliary, fixture.auxiliary_revision_id, "unclassified", FileLocator()),
        seed(
            fixture.absent_anchor,
            fixture.subject_revision_id,
            "support",
            FileLocator(),
            # The recorded location exists in no tree, so there is no blob to record; the identity
            # is authored like any other because storage never resolves a source.
            recorded_blob=ABSENT_RECORDED_BLOB,
        ),
        seed(
            fixture.mismatch_anchor,
            fixture.subject_revision_id,
            "support",
            FileLocator(),
            recorded_blob=MISMATCH_RECORDED_BLOB,
        ),
    )


def projected_claim_id(path: str, role: str) -> str:
    """Return the claim UUID the projection mints for one path and role.

    The projection derives claim identities from the entry's text ID, never from a caller
    UUID, so both sides of a comparison mint the same claim for the same association. A case
    that reads roster sources by claim looks them up under this identity.
    """

    return text_uuid("claim", _entry_text_id(path, role))


def _create_realization(store: RowStore, fixture: ReadScopeFixture, seed: RealizationSeed):
    realization = seed.realization
    if seed.revision_id == SUBJECT_REVISION:
        revision_id = fixture.subject_revision_id
    elif seed.revision_id == BATCH_REVISION:
        revision_id = fixture.batch_revision_id
    else:
        revision_id = seed.revision_id
    created = realizations.create_realization_claim(
        store,
        RealizationClaimRequest(
            repository_id=fixture.repository_id,
            claim=RealizationClaimDraft(
                claim_id=realization.claim_id,
                invariant_revision_id=revision_id,
                role=seed.role,
                rationale=seed.rationale or f"The recorded claim for {realization.path}.",
            ),
            anchor=NewAnchor(
                anchor=SourceAnchorDraft(
                    anchor_id=UUID(realization.anchor_id),
                    path=realization.path,
                    source_identity=GitBlobIdentity(object_id=seed.recorded_blob),
                    locator=seed.locator,
                )
            ),
            provenance=fixture.authorship,
        ),
    )
    _require("create_realization_claim", created.state, created.refusal)
    return created


def _write_git_tree(fixture: ReadScopeFixture) -> None:
    """Create the real Git tree the anchors are resolved against, and record its identity.

    The tree holds text at every recorded path except the absent one and the mismatch one, so a
    resolver has one of each outcome to observe: the exact recorded blob, a different blob at a
    recorded path, and a path the tree does not carry at all.
    """

    fixture.git_root.mkdir(parents=True, exist_ok=True)
    _git(fixture.git_root, ["init", "-q", "--initial-branch=main"])
    _git(fixture.git_root, ["config", "user.email", "fixture@example.invalid"])
    _git(fixture.git_root, ["config", "user.name", "read fixture"])
    for path, text in (
        # ``INTEGRATION_PATH`` carries a real nested Python definition beside the prose the fixture
        # is named for: a symbol locator is observed through the shipped tree-sitter extractor, so
        # the tree has to hold a construct that extractor can actually bind -- ``SharedBudget`` at
        # line 5 and its ``retry_budget`` method at 6-7.
        (INTEGRATION_PATH, INTEGRATION_FILE_TEXT),
        (SYNCHRONIZATION_PATH, "# synchronization\nshared retry budget\npropagated\n"),
        (BATCH_PATH, "# batch\none transaction\n"),
        (RESOLUTION_PATH, "# resolution\n"),
        (AUXILIARY_PATH, "# anchors\n"),
        (MISMATCH_PATH, "# timeout\nchanged bytes\n"),
        (UNPARSED_PATH, "-- schema\nCREATE TABLE budget (attempts INTEGER);\n"),
    ):
        target = fixture.git_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git(fixture.git_root, ["add", "-A"])
    _git(fixture.git_root, ["commit", "-q", "-m", "fixture tree"])
    object.__setattr__(
        fixture,
        "git_tree_id",
        _git(fixture.git_root, ["rev-parse", "HEAD^{tree}"]),
    )
    object.__setattr__(
        fixture,
        "git_blobs",
        {
            path: _git(fixture.git_root, ["rev-parse", f"HEAD:{path}"])
            for path in (
                INTEGRATION_PATH,
                SYNCHRONIZATION_PATH,
                BATCH_PATH,
                RESOLUTION_PATH,
                AUXILIARY_PATH,
                MISMATCH_PATH,
                UNPARSED_PATH,
            )
        },
    )


def _blob_of(fixture: ReadScopeFixture, path: str) -> str:
    """Return the blob identity the fixture repository holds at one path.

    The mapping is filled by :func:`_write_git_tree`, which runs before any realization is
    authored, so a missing entry is a fixture defect rather than an absent object and is reported
    as one instead of being answered with a placeholder identity.
    """

    try:
        return fixture.git_blobs[path]
    except KeyError as error:
        raise AssertionError(f"the fixture tree holds no blob for {path}") from error


def _git(root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(root),
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )
    if result.returncode != 0:
        raise AssertionError(f"fixture git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _require(step: str, state: str, refusal_value: object) -> None:
    """Fail loudly when a fixture step does not create: a fixture is not a probe."""

    if state != "created":
        raise AssertionError(
            f"read fixture step {step} returned {state!r} instead of 'created': {refusal_value!r}"
        )
