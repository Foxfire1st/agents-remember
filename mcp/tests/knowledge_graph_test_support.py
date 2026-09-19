"""Shared builders and probes for the graph test modules.

Three focused test modules exercise the family, anchor, membership and realization behaviour, and
they all build their inputs the same way: through the public typed operations, with the fixture's
own provenance envelope. This module owns that construction plus the two probing helpers a refusal
case needs (whole-database row counts, and the raw write that can construct a state the operation
forbids).

It is test support, not production code: it decides nothing, and the seed objects exist so a call
site reads as one authored record rather than as a row of positional arguments.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import apsw
from agents_remember.memory.knowledge import CANONICAL_TABLES, realizations, records
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore, open_knowledge_store
from agents_remember.models.knowledge.family import FamilyRevision, FamilyRevisionDraft
from agents_remember.models.knowledge.graph import (
    FamilyMemberDraft,
    RealizationClaimDraft,
    RealizationRole,
)
from agents_remember.models.knowledge.result import (
    AnchorReference,
    FamilyMemberRequest,
    NewAnchor,
    RealizationClaimRequest,
    RemoveRealizationClaimRequest,
)
from agents_remember.models.knowledge.source import (
    FileLocator,
    GitBlobIdentity,
    SourceAnchorDraft,
    SourceLocator,
)
from knowledge_fixture_test_support import (
    FAMILY_DISPLAY_VERSION,
    BranchingKnowledgeFixture,
)

# Raw writes used only to construct a state the operations forbid. A lineage cycle has no
# reachable creation path through the store, so a cycle case writes the rows directly and the
# store's own rule is what the case then exercises.
FAMILY_REVISION_INSERT = (
    "INSERT INTO family_revision (repository_id, family_id, revision_id, display_version, "
    "joint_guarantee, state_at_origin, acceptance_ref, provenance, payload_digest) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

FAMILY_EDGE_INSERT = (
    "INSERT INTO family_predecessor "
    "(repository_id, family_id, child_revision_id, parent_revision_id) VALUES (?, ?, ?, ?)"
)

DEFAULT_CLAIM_RATIONALE = "An authored rationale for this recorded realization."


@dataclass(frozen=True)
class FamilySeed:
    """One family revision to build: its identity, guarantee, display version and predecessors."""

    revision_id: str
    guarantee: str
    display_version: str = FAMILY_DISPLAY_VERSION
    predecessors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClaimSeed:
    """One realization claim to build, with the anchor endpoint it cites."""

    claim_id: str
    invariant_revision_id: str
    anchor: AnchorReference | NewAnchor
    role: RealizationRole = "support"
    rationale: str = DEFAULT_CLAIM_RATIONALE


@dataclass(frozen=True)
class MemberSeed:
    """One membership to build: an exact family revision and an exact invariant revision."""

    member_id: str
    family_revision_id: str
    invariant_revision_id: str


@dataclass(frozen=True)
class AnchorSeed:
    """One anchor draft to build, with the locator shape the case wants stored."""

    anchor_id: str
    path: str
    locator: SourceLocator = field(default_factory=FileLocator)


def anchor_draft(seed: AnchorSeed) -> SourceAnchorDraft:
    """Return one anchor draft whose recorded blob identity is stable for its path.

    A fixture cannot invent a real Git object and must not resolve one either, so the identity is
    derived from the path: a case that re-reads the anchor can compare it byte for byte, and a case
    whose path exists in no snapshot is still a well-formed record.
    """

    return SourceAnchorDraft(
        anchor_id=UUID(seed.anchor_id),
        path=seed.path,
        source_identity=GitBlobIdentity(
            object_id=hashlib.sha256(seed.path.encode("utf-8")).hexdigest()[:40]
        ),
        locator=seed.locator,
    )


def family_draft(
    fixture: BranchingKnowledgeFixture, family_id: str, seed: FamilySeed
) -> FamilyRevisionDraft:
    """Return one authored family revision draft of the named family."""

    return FamilyRevisionDraft(
        family_id=family_id,
        revision_id=seed.revision_id,
        display_version=seed.display_version,
        joint_guarantee=seed.guarantee,
        predecessors=seed.predecessors,
        provenance=fixture.authorship,
    )


def sealed_family(
    fixture: BranchingKnowledgeFixture, family_id: str, seed: FamilySeed
) -> FamilyRevision:
    """Return one family revision sealed the way the store seals it, without storing it."""

    return records.sealed_family_revision_from_draft(
        fixture.repository_id, family_draft(fixture, family_id, seed)
    )


def claim_request(fixture: BranchingKnowledgeFixture, seed: ClaimSeed) -> RealizationClaimRequest:
    """Return one realization claim request carrying the fixture's provenance."""

    return RealizationClaimRequest(
        repository_id=fixture.repository_id,
        claim=RealizationClaimDraft(
            claim_id=seed.claim_id,
            invariant_revision_id=seed.invariant_revision_id,
            role=seed.role,
            rationale=seed.rationale,
        ),
        anchor=seed.anchor,
        provenance=fixture.authorship,
    )


def member_request(fixture: BranchingKnowledgeFixture, seed: MemberSeed) -> FamilyMemberRequest:
    """Return one membership request carrying the fixture's provenance."""

    return FamilyMemberRequest(
        repository_id=fixture.repository_id,
        member=FamilyMemberDraft(
            member_id=seed.member_id,
            family_revision_id=seed.family_revision_id,
            invariant_revision_id=seed.invariant_revision_id,
            provenance=fixture.authorship,
        ),
    )


def table_counts(store: OpenedKnowledgeStore) -> dict[str, int]:
    """Return every canonical table's row count, so a refusal can prove it wrote nothing."""

    return {
        table: int(next(iter(store.connection.execute(f"SELECT count(*) FROM {table}")))[0])
        for table in CANONICAL_TABLES
    }


def insert_raw_family_revision(store: OpenedKnowledgeStore, revision: FamilyRevision) -> None:
    """Write one family revision row directly, bypassing the operation that would seal it."""

    store.connection.execute(FAMILY_REVISION_INSERT, records.family_revision_row(revision))


def insert_raw_family_edge(
    store: OpenedKnowledgeStore, family_id: str, child_id: str, parent_id: str
) -> None:
    """Write one family predecessor edge directly, bypassing the operation that would refuse it."""

    store.connection.execute(
        FAMILY_EDGE_INSERT, (store.repository_id, family_id, child_id, parent_id)
    )


@dataclass(frozen=True)
class RelationRemovalSuccessor:
    """One successor candidate: the same dataset with exactly one claim explicitly removed."""

    database_path: Path
    repository_id: str
    removed_claim_id: str
    removed_claim_row_digest: str
    unchanged_claim_ids: tuple[str, ...]

    def reopen(self) -> OpenedKnowledgeStore:
        """Reopen the successor dataset so a test reads the candidate rows."""

        return open_knowledge_store(self.database_path, self.repository_id)


def build_removed_relation_successor(
    fixture: BranchingKnowledgeFixture,
    directory: Path,
    *,
    database_name: str = "knowledge-successor.db",
) -> RelationRemovalSuccessor:
    """Clone the fixture dataset and explicitly remove one realization claim from the clone.

    The clone is a database-level backup of the closed baseline -- the same way a candidate
    clone is made -- so the two datasets are byte-independent and the baseline keeps the claim.
    The removal goes through the public operation with the row digest the successor read, so a
    row that changed under it would refuse instead of removing something else.
    """

    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / database_name
    _clone_closed_database(fixture.database_path, destination)
    store = open_knowledge_store(destination, fixture.repository_id)
    try:
        removed = realizations.get_realization_claim(store, fixture.synchronization.claim_id)
        if removed is None:
            raise AssertionError(
                f"successor clone lost the claim to remove: {fixture.synchronization.claim_id}"
            )
        result = realizations.remove_realization_claim(
            store,
            RemoveRealizationClaimRequest(
                repository_id=fixture.repository_id,
                claim_id=fixture.synchronization.claim_id,
                expected_row_digest=removed.row_digest,
            ),
        )
        if result.state != "removed":
            raise AssertionError(
                f"successor claim removal returned {result.state!r} instead of 'removed': "
                f"{result.refusal!r}"
            )
        unchanged = realizations.list_claims_for_invariant_revision(store, fixture.base_revision_id)
    finally:
        store.close()
    return RelationRemovalSuccessor(
        database_path=destination,
        repository_id=fixture.repository_id,
        removed_claim_id=fixture.synchronization.claim_id,
        removed_claim_row_digest=removed.row_digest,
        unchanged_claim_ids=tuple(item.claim_id for item in unchanged.claims),
    )


_BACKUP_STEP_PAGES = 64


def _clone_closed_database(source: Path, destination: Path) -> None:
    """Clone one closed candidate database with SQLite's own backup.

    The source must be closed and is not modified; the destination is a separate candidate
    dataset, not a publication. The step loop runs the backup to completion and the backup object
    is closed before either connection is, so no live handle survives the call.
    """

    origin = apsw.Connection(str(source))
    target = apsw.Connection(str(destination))
    try:
        backup = target.backup("main", origin, "main")
        while not backup.done:
            backup.step(_BACKUP_STEP_PAGES)
        backup.close()
    finally:
        target.close()
        origin.close()
