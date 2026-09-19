"""The read-scope fixture: the packet's ``P -> I1``, ``F1 -> {I1, J1}``, ``G1 -> {J1, K1}`` graph.

The shared branching fixture already carries one invariant with two realizations, but the selective
read is decided by a topology that fixture does not have: a *sibling* invariant that one family
shares with a *second* family the seed never reaches. That shape is the whole stopping rule, and it
is what this fixture exists to carry, so it is built here rather than bent into the other one.

Built through the public store operations, never by inserting rows, so every identity in it is one
the real write path produced:

* ``I1`` is the second revision of the retry-budget invariant, and it carries two realization claims
  at two distinct source locations (``integration`` and ``synchronization``);
* ``J1`` is the first revision of the candidate-batch invariant. It carries **two claims at one
  location** (``batch``), which is what makes "two claim identities, one distinct source location"
  measurable, and it is the sibling the frontier advertises;
* ``K1`` is the first revision of the source-resolution invariant, reachable only through ``G1``;
* ``F1`` is ``{I1, J1}`` and ``G1`` is ``{J1, K1}``, so ``J1`` is in both and ``K1`` is in the one
  the seed does not reach;
* ``H1`` is ``{I1, L1}``, a **second** family that directly contains ``I1``, which is what proves
  the path/invariant rule includes every directly containing family rather than only the first;
* ``I1`` also has two retained siblings, ``I0`` (its predecessor) and ``I2``, and both of those
  successors display the same ``v2`` label, which is what makes "a display version selects nothing"
  measurable rather than rhetorical.

``absent_anchor`` is a recorded claim whose path exists in no tree, and ``mismatch_anchor`` is a
claim whose recorded blob identity is deliberately not the one the fixture repository holds at that
path. Both are authored like any other claim: storage never resolves a source, so whether a path
resolves is a fact about a snapshot and not a condition for recording it.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from agents_remember.memory.knowledge import families, memberships, realizations
from agents_remember.memory.knowledge.logical import dataset_identity
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

REPOSITORY_AUTHORITY_HOME = "agents-remember"

# The invariants, their labels and their statements. Stable prose so a reopened read is meaningful.
RETRY_LABEL = "shared-retry-budget"
RETRY_STATEMENTS = (
    "Retries draw on the attempt and timeout configuration.",
    "Retries share one budget across integration and synchronization.",
    "Retries share one budget and record the exhausted attempt.",
)
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
SHARED_SUCCESSOR_LABEL = "v2"
BASE_LABEL = "v1"
# The predecessor's authored label deliberately sorts **after** the two successors' shared label.
# The ordering case measures "an authored label never orders a page" by comparing the emitted
# sequence against the sequence a label-first key would produce, and that comparison is only
# non-vacuous -- and only deterministic -- when the two orders *must* disagree for the draw. With a
# monotone labelling (`v1` before `v2`) they agree whenever the predecessor's random id happens to
# sort first, which is exactly the coin-flip the fix round removed. A non-monotone label makes the
# disagreement structural: the predecessor is emitted first in `(label, id)` order and cannot be
# first in stored-id order unless its id sorts first, which the pairing below makes impossible.
PREDECESSOR_LABEL = "v3"

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
SYNCHRONIZATION_PATH = "src/synchronization.py"
ABSENT_PATH = "src/retired_adapter.py"
BATCH_PATH = "src/batch.py"
RESOLUTION_PATH = "src/resolution.py"
AUXILIARY_PATH = "src/anchors.py"
MISMATCH_PATH = "src/timeout.py"

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


@dataclass(frozen=True)
class RealizationSeed:
    """One realization to author: its claim, its anchor, its role and its recorded blob."""

    realization: ReadFixtureRealization
    revision_id: str
    role: RealizationRole
    locator: SourceLocator
    recorded_blob: str


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
    base_revision_id: str
    subject_revision_id: str
    successor_revision_id: str

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

    def reopen(self) -> OpenedKnowledgeStore:
        """Reopen the fixture store so a case reads what was really persisted."""

        return open_knowledge_store(self.database_path, self.repository_id)

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


def _ordered_revision_id(sort_marker: str) -> str:
    """Return a unique revision id whose stored order is ``sort_marker``'s order.

    The marker leads the canonical spelling, so two ids from this helper compare exactly as their
    markers do, and the rest of the id is still a real random UUID tail. That is enough to author
    one identity's stored order without turning the fixture's other identities into constants.
    """

    return f"{sort_marker}0000000-0000-4000-8000-{uuid4().hex[:12]}"


def build_read_scope_fixture(
    directory: Path,
    *,
    repository_id: str | None = None,
    database_name: str = "knowledge-candidate.db",
) -> ReadScopeFixture:
    """Create the fixture store and its Git tree, then return every identity in it."""

    fixture = _seed_identities(directory, repository_id, database_name)
    _write_git_tree(fixture)
    store = open_knowledge_store(fixture.database_path, fixture.repository_id)
    try:
        _build_invariants(store, fixture)
        _build_families(store, fixture)
        _build_realizations(store, fixture)
    finally:
        store.close()
    return fixture


def _seed_identities(
    directory: Path, repository_id: str | None, database_name: str
) -> ReadScopeFixture:
    """Allocate every identity the fixture needs before one statement runs."""

    directory.mkdir(parents=True, exist_ok=True)
    return ReadScopeFixture(
        database_path=directory / database_name,
        repository_id=repository_id or str(uuid4()),
        authorship=make_read_authorship(),
        retry_invariant_id=str(uuid4()),
        # The retry identity's three revision ids are authored so that their stored-id order is
        # fixed by construction rather than by the draw: the predecessor's id sorts *after* both
        # successors', and the predecessor is the one carrying ``PREDECESSOR_LABEL``, the label that
        # sorts last. The two orders therefore disagree at the first position of the identity's
        # revisions, for every draw -- which is what makes the ordering case's "an authored label
        # never orders a page" comparison a measurement of the code instead of a coin-flip on
        # random ids. The rest of the fixture's identities keep random ids, so every other case's
        # order remains a real observation rather than a fixture constant.
        base_revision_id=_ordered_revision_id("9"),
        subject_revision_id=_ordered_revision_id("3"),
        successor_revision_id=_ordered_revision_id("6"),
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


def _build_invariants(store: OpenedKnowledgeStore, fixture: ReadScopeFixture) -> None:
    """Author the four invariant identities and every revision the fixture needs."""

    created = store.create_repository(
        RepositoryIdentity(
            repository_id=fixture.repository_id, authority_home=REPOSITORY_AUTHORITY_HOME
        )
    )
    _require("create_repository", created.state, created.refusal)
    for invariant_id, label, revisions in _invariant_seeds(fixture):
        _create_invariant(store, fixture, invariant_id, label)
        for seed in revisions:
            _create_revision(store, fixture, seed)


def _invariant_seeds(
    fixture: ReadScopeFixture,
) -> tuple[tuple[str, str, tuple[RevisionSeed, ...]], ...]:
    """Return the four invariants and every revision of each, in authoring order."""

    base = fixture.base_revision_id
    return (
        (
            fixture.retry_invariant_id,
            RETRY_LABEL,
            (
                _revision(
                    fixture,
                    base,
                    text=(PREDECESSOR_LABEL, RETRY_STATEMENTS[0], CONDITIONS),
                ),
                _revision(
                    fixture,
                    fixture.subject_revision_id,
                    text=(SHARED_SUCCESSOR_LABEL, RETRY_STATEMENTS[1], SUCCESSOR_CONDITIONS),
                    predecessors=(base,),
                ),
                _revision(
                    fixture,
                    fixture.successor_revision_id,
                    text=(SHARED_SUCCESSOR_LABEL, RETRY_STATEMENTS[2], SUCCESSOR_CONDITIONS),
                    predecessors=(base,),
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
    argument list that a reader has to count.
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
    store: OpenedKnowledgeStore, fixture: ReadScopeFixture, invariant_id: str, label: str
) -> None:
    created = store.create_invariant(
        InvariantRequest(
            repository_id=fixture.repository_id,
            invariant_id=invariant_id,
            display_label=label,
            provenance=fixture.authorship,
        )
    )
    _require("create_invariant", created.state, created.refusal)


def _create_revision(
    store: OpenedKnowledgeStore, fixture: ReadScopeFixture, seed: RevisionSeed
) -> None:
    created = store.create_revision(
        RevisionRequest(
            repository_id=fixture.repository_id,
            revision=RevisionDraft(
                revision_id=seed.revision_id,
                invariant_id=seed.invariant_id,
                display_version=seed.display_version,
                statement=seed.statement,
                applicability=APPLICABILITY,
                conditions=seed.conditions,
                exclusions=EXCLUSIONS,
                predecessors=seed.predecessors,
                provenance=fixture.authorship,
            ),
        )
    )
    _require("create_revision", created.state, created.refusal)


def _invariant_of(fixture: ReadScopeFixture, revision_id: str) -> str:
    """Return the invariant identity one fixture revision belongs to."""

    if revision_id in (
        fixture.base_revision_id,
        fixture.subject_revision_id,
        fixture.successor_revision_id,
    ):
        return fixture.retry_invariant_id
    if revision_id == fixture.batch_revision_id:
        return fixture.batch_invariant_id
    if revision_id == fixture.resolution_revision_id:
        return fixture.resolution_invariant_id
    if revision_id == fixture.auxiliary_revision_id:
        return fixture.auxiliary_invariant_id
    raise AssertionError(f"{revision_id} is not a revision of this fixture")


def _build_families(store: OpenedKnowledgeStore, fixture: ReadScopeFixture) -> None:
    """Author the three families and the exact memberships the stopping rule is read through."""

    for seed, members in (
        (fixture.family, (fixture.subject_revision_id, fixture.batch_revision_id)),
        (
            fixture.overlapping_family,
            (fixture.batch_revision_id, fixture.resolution_revision_id),
        ),
        (fixture.direct_family, (fixture.subject_revision_id, fixture.auxiliary_revision_id)),
    ):
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
        revision = families.create_family_revision(
            store,
            FamilyRevisionRequest(
                repository_id=fixture.repository_id,
                revision=FamilyRevisionDraft(
                    family_id=seed.family_id,
                    revision_id=seed.revision_id,
                    display_version=BASE_LABEL,
                    joint_guarantee=_family_guarantee(seed.family_id, fixture),
                    provenance=fixture.authorship,
                ),
            ),
        )
        _require("create_family_revision", revision.state, revision.refusal)
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
            _require("create_family_member", member.state, member.refusal)


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


def _build_realizations(store: OpenedKnowledgeStore, fixture: ReadScopeFixture) -> None:
    """Author every realization claim, each citing the recorded blob its repository holds."""

    for seed in _realization_seeds(fixture):
        _create_realization(store, fixture, seed)


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
        seed(fixture.auxiliary, fixture.auxiliary_revision_id, "incidental", FileLocator()),
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


def _create_realization(
    store: OpenedKnowledgeStore, fixture: ReadScopeFixture, seed: RealizationSeed
) -> None:
    realization = seed.realization
    created = realizations.create_realization_claim(
        store,
        RealizationClaimRequest(
            repository_id=fixture.repository_id,
            claim=RealizationClaimDraft(
                claim_id=realization.claim_id,
                invariant_revision_id=seed.revision_id,
                role=seed.role,
                rationale=f"The recorded claim for {realization.path}.",
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
        (INTEGRATION_PATH, "# integration\nshared retry budget\n"),
        (SYNCHRONIZATION_PATH, "# synchronization\nshared retry budget\npropagated\n"),
        (BATCH_PATH, "# batch\none transaction\n"),
        (RESOLUTION_PATH, "# resolution\n"),
        (AUXILIARY_PATH, "# anchors\n"),
        (MISMATCH_PATH, "# timeout\nchanged bytes\n"),
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
