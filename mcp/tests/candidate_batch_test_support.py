"""The candidate-change case harness: one admitted candidate, its batches and its probes.

Every case in the two candidate-batch test modules needs the same three things, and this module
owns them so neither module re-derives them:

* **One admitted candidate built through the real seam.** The harness opens a destination, binds its
  namespace through ``initialize_knowledge_namespace``, and resolves contexts through
  ``resolve_candidate_context``. A case therefore authors its batch against an identity the
  application actually read rather than against a digest it wrote down.
* **Batch construction from typed commands.** A case builds the command objects and the harness
  supplies the admitted context and the `ChangeBatch` shape, so no case has to remember the context
  payload.
* **The probes a refusal needs.** Row counts and the logical digest, read through a separate opened
  store, so "the stored dataset was left untouched" is measured rather than asserted.

It is test support, not production code: it decides nothing, and the only writes it performs are the
ones a case explicitly asks for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

import apsw
from agents_remember.application.knowledge import (
    admitted_knowledge_destination,
    change_knowledge_candidate,
    initialize_knowledge_namespace,
    open_admitted_knowledge_store,
    resolve_candidate_context,
    write_authorship,
)
from agents_remember.kernel.canonical_json import canonical_json_bytes
from agents_remember.memory.knowledge import (
    CANONICAL_TABLES,
    anchors,
    candidate_records,
    logical,
    memberships,
    realizations,
    records,
)
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.candidate import (
    AddFamily,
    AddFamilyMember,
    AddFamilyRevision,
    AddInvariant,
    AddInvariantRevision,
    AddRealizationClaim,
    CandidateResolution,
    ChangeBatch,
    ChangeCommand,
    KnowledgeContext,
    KnowledgeLane,
    MutationResult,
    RemoveFamilyMember,
    RemoveRealizationClaim,
    RemoveSourceAnchor,
)
from agents_remember.models.knowledge.context import AdmittedKnowledgeDestination
from agents_remember.models.knowledge.family import FamilyRevisionDraft
from agents_remember.models.knowledge.graph import FamilyMemberDraft, RealizationClaimDraft
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import (
    FamilyMemberRequest,
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

DEFAULT_AUTHORITY_HOME = "agents-remember"
CODE_TREE_ID = "a" * 40
MEMORY_TREE_ID = "b" * 40

# A hash-shaped identity for a candidate-side tree the cases never read: the harness records the
# exact tree inputs an admission would have resolved without pretending to own Git.
_RESOLUTION_NAME = "candidate:batch-case"


@dataclass(frozen=True)
class CandidateHarness:
    """One admitted draft candidate: its destination, its namespace and its resolution."""

    database_path: Path
    destination_repository: RepositoryIdentity
    authorship: Authorship
    resolution: CandidateResolution

    @property
    def repository_id(self) -> str:
        return self.destination_repository.repository_id

    def destination(self) -> AdmittedKnowledgeDestination:
        """Return the admitted destination this harness was built for."""

        return admitted_knowledge_destination(
            self.database_path, self.destination_repository, self.authorship
        )

    def digest_of(self, table: str, record_id: str) -> str:
        """Return one stored record's digest, or raise when it is not stored."""

        store = self.open()
        try:
            digest = candidate_records.stored_record_digest(store, table, record_id)
        finally:
            store.close()
        if digest is None:
            raise AssertionError(f"{table} {record_id} is not stored")
        return digest

    def open(self) -> OpenedKnowledgeStore:
        """Open the candidate for reads through the admitted destination.

        The caller owns the handle and closes it; the harness never closes a store a case is using.
        """

        return open_admitted_knowledge_store(self.destination())

    def context(self) -> KnowledgeContext:
        """Resolve the candidate's current context through the application seam."""

        return resolve_candidate_context(self.destination(), self.resolution)

    def batch(self, *commands: ChangeCommand) -> ChangeBatch:
        """Build one batch, against the context the candidate currently holds."""

        return ChangeBatch(expected=self.context(), commands=commands)  # type: ignore[arg-type]

    def apply(self, batch: ChangeBatch) -> MutationResult:
        """Apply one batch through the admitted destination."""

        return change_knowledge_candidate(self.destination(), batch)

    def seed(self, count: int = 1) -> tuple[tuple[str, str], ...]:
        """Author ``count`` invariant identities and first revisions through the operations.

        This is the preparation a "second batch" case needs: the dataset it starts from must have
        been written by the single-record operation, not by the batch under test, or the case would
        be measuring its own fixture.
        """

        seeds: list[tuple[str, str]] = []
        store = self.open()
        try:
            for index in range(count):
                invariant_id = str(uuid4())
                revision_id = str(uuid4())
                created = store.create_invariant(
                    InvariantRequest(
                        repository_id=self.repository_id,
                        invariant_id=invariant_id,
                        display_label=f"seeded obligation {index}",
                        provenance=self.authorship,
                    )
                )
                if created.state != "created":
                    raise AssertionError(f"seed invariant refused: {created.refusal!r}")
                revision = store.create_revision(
                    RevisionRequest(
                        repository_id=self.repository_id,
                        revision=RevisionDraft(
                            revision_id=revision_id,
                            invariant_id=invariant_id,
                            display_version="v1",
                            statement=f"Seeded obligation {index} the batch cases extend.",
                            applicability="Every admitted candidate write in this namespace.",
                            provenance=self.authorship,
                        ),
                    )
                )
                if revision.state != "created":
                    raise AssertionError(f"seed revision refused: {revision.refusal!r}")
                seeds.append((invariant_id, revision_id))
        finally:
            store.close()
        return tuple(seeds)

    def seed_membership(self, *, family_revision_id: str, invariant_revision_id: str) -> str:
        """Relate one pair through the single-record operation and return the membership identity.

        A case that wants the *database* to catch a duplicate relationship needs one row already
        there, written by the operation that owns that concept rather than by the batch under test.
        """

        member_id = str(uuid4())
        store = self.open()
        try:
            created = memberships.create_family_member(
                store,
                FamilyMemberRequest(
                    repository_id=self.repository_id,
                    member=FamilyMemberDraft(
                        member_id=member_id,
                        family_revision_id=family_revision_id,
                        invariant_revision_id=invariant_revision_id,
                        provenance=self.authorship,
                    ),
                ),
            )
            if created.state != "created":
                raise AssertionError(f"seed membership refused: {created.refusal!r}")
        finally:
            store.close()
        return member_id

    def table_counts(self) -> dict[str, int]:
        """Return every canonical table's row count for the candidate as it stands."""

        store = self.open()
        try:
            return table_counts(store)
        finally:
            store.close()

    def logical_digest(self) -> str:
        """Return the candidate's current logical dataset digest."""

        store = self.open()
        try:
            return logical_digest(store)
        finally:
            store.close()


def build_candidate_harness(
    directory: Path,
    *,
    lane: KnowledgeLane = "draft-candidate",
    database_name: str = "candidate-batch.db",
    repository_id: str | None = None,
) -> CandidateHarness:
    """Create an empty admitted candidate database and return its harness.

    The candidate starts empty on purpose: a case that needs authored records writes them through
    the batch operation itself, so the operation's own insertions are what the case measures.
    """

    directory.mkdir(parents=True, exist_ok=True)
    repository = RepositoryIdentity(
        repository_id=repository_id or str(uuid4()), authority_home=DEFAULT_AUTHORITY_HOME
    )
    authorship = write_authorship(
        actor_ref="agent:candidate-batch-case",
        authorization_ref="260915-KS developer kickoff ruling",
        origin_refs=("requirement:KS-R03@v1",),
    )
    harness = CandidateHarness(
        database_path=directory / database_name,
        destination_repository=repository,
        authorship=authorship,
        resolution=CandidateResolution(
            lane=lane,
            code_tree_id=CODE_TREE_ID,
            memory_tree_id=MEMORY_TREE_ID,
            snapshot_ref=_RESOLUTION_NAME,
            candidate_ref="draft:candidate-batch",
        ),
    )
    initialized = initialize_knowledge_namespace(harness.destination())
    if initialized.state != "created":
        raise AssertionError(
            f"the candidate harness could not initialize its destination: {initialized.refusal!r}"
        )
    return harness


def logical_digest(store: OpenedKnowledgeStore) -> str:
    """Return one open candidate's logical dataset digest."""

    repository = store.get_repository()
    if repository is None:
        raise AssertionError("the candidate under test is not bound to its namespace")
    return logical.logical_digest(store.connection, store.schema.schema_name)


def table_counts(store: OpenedKnowledgeStore) -> dict[str, int]:
    """Return every canonical table's row count for one open candidate."""

    return {
        table: int(next(iter(store.connection.execute(f"SELECT count(*) FROM {table}")))[0])
        for table in CANONICAL_TABLES
    }


@dataclass(frozen=True)
class RefusalEvidence:
    """One refusal with the two measurements that prove it wrote nothing."""

    result: MutationResult
    counts_before: dict[str, int]
    counts_after: dict[str, int]
    digest_before: str
    digest_after: str

    @property
    def refusal(self) -> KnowledgeRefusal:
        refusal = self.result.refusal
        if refusal is None:
            raise AssertionError("the observed result was not a refusal")
        return refusal

    def wrote_nothing(self) -> bool:
        """Whether every table's row count and the logical digest are unchanged."""

        return (
            self.counts_before == self.counts_after
            and self.digest_before == self.digest_after
            and self.result.before.logical_digest == self.result.after.logical_digest
        )


def measure_refusal(harness: CandidateHarness, batch: ChangeBatch) -> RefusalEvidence:
    """Apply one batch expected to refuse, measuring the dataset before and after it.

    The before and after readings are taken through separately opened stores rather than from the
    receipt, so what is proven is a fact about the database and not about the operation's own report.
    """

    counts_before = harness.table_counts()
    digest_before = harness.logical_digest()
    result = harness.apply(batch)
    counts_after = harness.table_counts()
    digest_after = harness.logical_digest()
    return RefusalEvidence(
        result=result,
        counts_before=counts_before,
        counts_after=counts_after,
        digest_before=digest_before,
        digest_after=digest_after,
    )


@dataclass(frozen=True)
class RemovalSeed:
    """One removable record: the command that removes it, and what it is."""

    kind: str
    command: ChangeCommand
    table: str
    record_id: str
    digest: str


def invariant_digest(harness: CandidateHarness, invariant_id: str) -> str:
    """Return one stored invariant identity's row digest, as a read exposes it."""

    store = harness.open()
    try:
        identity = store.get_invariant(invariant_id)
        if identity is None:
            raise AssertionError(f"invariant {invariant_id} is not stored")
        return identity.row_digest
    finally:
        store.close()


def revision_draft(
    harness: CandidateHarness,
    *,
    invariant_id: str,
    revision_id: str,
    predecessors: tuple[str, ...] = (),
) -> RevisionDraft:
    """One invariant revision draft carrying the harness's own provenance envelope."""

    return RevisionDraft(
        revision_id=revision_id,
        invariant_id=invariant_id,
        display_version="v1",
        statement="An authored candidate statement about atomic writes.",
        applicability="Every admitted candidate write in this namespace.",
        predecessors=predecessors,
        provenance=harness.authorship,
    )


def family_revision_draft(
    harness: CandidateHarness,
    *,
    family_id: str,
    revision_id: str,
    predecessors: tuple[str, ...] = (),
) -> FamilyRevisionDraft:
    """One family revision draft carrying the harness's own provenance envelope."""

    return FamilyRevisionDraft(
        family_id=family_id,
        revision_id=revision_id,
        display_version="v1",
        joint_guarantee="The authored obligations hold together under one admitted batch.",
        predecessors=predecessors,
        provenance=harness.authorship,
    )


def claim_command(
    seeds: CommandSeeds, *, revision_id: str, path: str = "src/claim_case.py"
) -> AddRealizationClaim:
    """One realization claim command that records its own anchor."""

    return AddRealizationClaim(
        claim=RealizationClaimDraft(
            claim_id=seeds.claim_id,
            invariant_revision_id=revision_id,
            role="enforcement",
            rationale="The batch boundary refuses a partially applied change.",
        ),
        anchor=NewAnchor(
            anchor=SourceAnchorDraft(
                anchor_id=seeds.anchor_id,
                path=path,
                source_identity=GitBlobIdentity(object_id="d" * 40),
                locator=FileLocator(),
            )
        ),
    )


def removal_seeds(
    harness: CandidateHarness, seeds: CommandSeeds | None = None
) -> tuple[RemovalSeed, ...]:
    """Author one removable record of each kind and return the commands that remove them.

    The records are created through the batch operation, because a batch is the only write path this
    leaf exposes; what the cases then measure is the removal, not the creation. The seeds come back
    in an order that can be removed in sequence -- the citing claim before the anchor it cites --
    so a case may apply them one at a time or all at once.
    """

    authored = seeds or CommandSeeds()
    created = harness.apply(
        harness.batch(
            AddInvariant(invariant_id=authored.invariant_id, display_label="removable invariant"),
            AddInvariantRevision(
                revision=RevisionDraft(
                    revision_id=authored.revision_id,
                    invariant_id=authored.invariant_id,
                    display_version="v1",
                    statement="An obligation whose recorded relations are later removed.",
                    applicability="Every admitted candidate write in this namespace.",
                    provenance=harness.authorship,
                )
            ),
            AddFamily(family_id=authored.family_id, display_label="removable family"),
            AddFamilyRevision(
                revision=FamilyRevisionDraft(
                    family_id=authored.family_id,
                    revision_id=authored.family_revision_id,
                    display_version="v1",
                    joint_guarantee="The authored obligations hold together.",
                    provenance=harness.authorship,
                )
            ),
            AddFamilyMember(
                member=FamilyMemberDraft(
                    member_id=authored.member_id,
                    family_revision_id=authored.family_revision_id,
                    invariant_revision_id=authored.revision_id,
                    provenance=harness.authorship,
                )
            ),
            AddRealizationClaim(
                claim=RealizationClaimDraft(
                    claim_id=authored.claim_id,
                    invariant_revision_id=authored.revision_id,
                    role="support",
                    rationale="A recorded realization the removal cases delete.",
                ),
                anchor=NewAnchor(
                    anchor=SourceAnchorDraft(
                        anchor_id=authored.anchor_id,
                        path="src/removal_seed.py",
                        source_identity=GitBlobIdentity(object_id="f" * 40),
                        locator=FileLocator(),
                    )
                ),
            ),
        )
    )
    if created.state != "changed":
        raise AssertionError(f"the removal seeds were not authored: {created.refusal!r}")

    store = harness.open()
    try:
        member = memberships.get_family_member(store, authored.member_id)
        claim = realizations.get_realization_claim(store, authored.claim_id)
        anchor = anchors.get_anchor(store, str(authored.anchor_id))
        if member is None or claim is None or anchor is None:
            raise AssertionError("a seeded removable record was not stored")
        # The claim cites the anchor, so a batch that removes both has to remove the claim first:
        # the declared foreign keys hold one record's removal back while another still cites it.
        return (
            RemovalSeed(
                kind="remove_realization_claim",
                command=RemoveRealizationClaim(
                    claim_id=authored.claim_id, expected_row_digest=claim.row_digest
                ),
                table="realization_claim",
                record_id=authored.claim_id,
                digest=claim.row_digest,
            ),
            RemovalSeed(
                kind="remove_source_anchor",
                command=RemoveSourceAnchor(anchor_id=str(authored.anchor_id)),
                table="source_anchor",
                record_id=str(authored.anchor_id),
                digest=records.anchor_row_digest(anchor, harness.repository_id),
            ),
            RemovalSeed(
                kind="remove_family_member",
                command=RemoveFamilyMember(
                    member_id=authored.member_id, expected_row_digest=member.row_digest
                ),
                table="family_member",
                record_id=authored.member_id,
                digest=member.row_digest,
            ),
        )
    finally:
        store.close()


def record_is_gone(harness: CandidateHarness, table: str, record_id: str) -> bool:
    """Whether the named record is absent from the candidate."""

    store = harness.open()
    try:
        return candidate_records.stored_record_digest(store, table, record_id) is None
    finally:
        store.close()


def insert_raw_membership(
    harness: CandidateHarness,
    *,
    family_revision_id: str,
    invariant_revision_id: str,
    member_id: UUID,
) -> None:
    """Write one membership row directly, so the declared unique pair is already stored.

    This is how a case reaches the database's own refusal: the constraint that refuses a duplicate
    pair is the declared unique tuple, and the batch's preconditions deliberately do not duplicate
    that check, so a row inserted outside the operation is what makes the tuple collide.
    """

    connection = apsw.Connection(str(harness.database_path))
    try:
        connection.execute(
            "INSERT INTO family_member "
            "(repository_id, member_id, family_revision_id, invariant_revision_id, provenance) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                harness.repository_id,
                str(member_id),
                family_revision_id,
                invariant_revision_id,
                canonical_json_bytes(harness.authorship.model_dump(mode="json")).decode("utf-8"),
            ),
        )
    finally:
        connection.close()


@dataclass
class CommandSeeds:
    """Stable identities one case authors, so a later assertion can name what it wrote."""

    invariant_id: str = field(default_factory=lambda: str(uuid4()))
    revision_id: str = field(default_factory=lambda: str(uuid4()))
    successor_id: str = field(default_factory=lambda: str(uuid4()))
    family_id: str = field(default_factory=lambda: str(uuid4()))
    family_revision_id: str = field(default_factory=lambda: str(uuid4()))
    member_id: str = field(default_factory=lambda: str(uuid4()))
    anchor_id: UUID = field(default_factory=uuid4)
    claim_id: str = field(default_factory=lambda: str(uuid4()))


__all__ = [
    "CODE_TREE_ID",
    "MEMORY_TREE_ID",
    "CandidateHarness",
    "CommandSeeds",
    "RefusalEvidence",
    "RemovalSeed",
    "build_candidate_harness",
    "claim_command",
    "family_revision_draft",
    "insert_raw_membership",
    "invariant_digest",
    "logical_digest",
    "measure_refusal",
    "record_is_gone",
    "removal_seeds",
    "revision_draft",
    "table_counts",
]
