"""Test-only rows for the knowledge read side: a small builder that inserts index-shaped rows.

**Why it exists.** The canonical knowledge database and every writer of its rows are retired
(MIK-R26). The read code that remains -- the scope read, the views, the comparison and the
reviewer's readers -- runs over the derived index of a memory tree, whose logical tables production
fills in exactly one place, ``memory/knowledge_index/projection.py``. The tests of that read code
need datasets a text tree cannot hold: several revisions of one invariant with lineage edges, a
membership present in one dataset and absent in the next, an anchor that names no file. This module
inserts those rows directly, the way the projection does: one ``INSERT`` per row into the schema
production declares, with each revision's seal computed by the production digest function the
readers verify on every decode.

**What it is not.** It is not a writer and holds none of the retired write rules. It takes no lock,
checks no label, lineage, endpoint or duplicate rule, and never refuses: what a test asks for is
inserted, and only the schema's own constraints can reject a row. A case that needs a write rule is
a case about the schema or about a reader, never about this module. No production code was moved
here; production keeps no caller of anything in this file.

**Its call shapes.** The fixtures of about forty test modules were written against
``store.create_invariant(request)``, ``families.create_family(store, request)`` and their
siblings. The builder answers the same call shapes so those fixtures keep their structure:

* :func:`open_knowledge_store` returns a :class:`RowStore`, the production
  ``OpenedKnowledgeStore`` (so every production reader accepts it) with the insert methods added;
* ``families``, ``memberships``, ``realizations`` and ``anchors`` are namespaces holding the
  builder's insert functions beside the production modules' read functions;
* the ``…Request`` types are plain containers for the arguments; the ``…Draft`` models they carry
  are production's own, except :class:`RevisionDraft`, which only fixtures construct and which
  therefore lives here;
* every insert returns a :class:`RowOutcome` whose ``state`` is ``"created"``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Literal
from uuid import uuid4

import apsw
from agents_remember.memory.knowledge import families as _production_families
from agents_remember.memory.knowledge import memberships as _production_memberships
from agents_remember.memory.knowledge.connection import (
    create_or_validate_schema,
)
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore
from agents_remember.models.knowledge.authorship import Authorship, KnowledgeState
from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    UUID_PATTERN,
    KnowledgeModel,
)
from agents_remember.models.knowledge.digest import (
    family_revision_payload_digest,
    revision_payload_digest,
)
from agents_remember.models.knowledge.family import FamilyRevision, FamilyRevisionDraft
from agents_remember.models.knowledge.graph import FamilyMemberDraft
from agents_remember.models.knowledge.invariant import InvariantRevision
from agents_remember.models.knowledge.repository import RepositoryIdentity
from anchor_fixture_models import RealizationClaimDraft, SourceAnchorDraft
from knowledge_row_codec_test_support import encode_authorship, encode_typed_column
from pydantic import Field, model_validator

__all__ = [
    "AnchorReference",
    "FamilyMemberRequest",
    "FamilyRequest",
    "FamilyRevisionRequest",
    "InvariantRequest",
    "NewAnchor",
    "RealizationClaimRequest",
    "RemoveFamilyMemberRequest",
    "RemoveRealizationClaimRequest",
    "RevisionDraft",
    "RevisionRequest",
    "RowOutcome",
    "RowStore",
    "SourceAnchorRequest",
    "anchors",
    "families",
    "memberships",
    "open_knowledge_store",
    "realizations",
    "write_authorship",
]

_UNSEALED = "0" * 64


# --- the argument containers ----------------------------------------------------------------------


@dataclass(frozen=True)
class InvariantRequest:
    repository_id: str
    invariant_id: str
    display_label: str
    provenance: Authorship


class RevisionDraft(KnowledgeModel):
    """One authored revision aggregate as a caller supplies it, before sealing.

    The digest is absent by design: the store recomputes and stores it, so a caller cannot
    present a payload whose seal belongs to different content.
    """

    revision_id: str = Field(pattern=UUID_PATTERN)
    invariant_id: str = Field(pattern=UUID_PATTERN)
    display_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    statement: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    applicability: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    conditions: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    predecessors: tuple[str, ...] = ()
    state_at_origin: KnowledgeState = "proposed"
    acceptance_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    provenance: Authorship

    @model_validator(mode="after")
    def _refuse_self_predecessor(self) -> RevisionDraft:
        if self.revision_id in self.predecessors:
            raise ValueError("a revision must not declare itself as its own predecessor")
        return self


@dataclass(frozen=True)
class RevisionRequest:
    repository_id: str
    revision: RevisionDraft


@dataclass(frozen=True)
class FamilyRequest:
    repository_id: str
    family_id: str
    display_label: str
    provenance: Authorship


@dataclass(frozen=True)
class FamilyRevisionRequest:
    repository_id: str
    revision: FamilyRevisionDraft


@dataclass(frozen=True)
class FamilyMemberRequest:
    repository_id: str
    member: FamilyMemberDraft


@dataclass(frozen=True)
class SourceAnchorRequest:
    repository_id: str
    anchor: SourceAnchorDraft
    provenance: Authorship


@dataclass(frozen=True)
class NewAnchor:
    """A realization whose anchor row is inserted with it."""

    anchor: SourceAnchorDraft
    kind: Literal["new"] = "new"


@dataclass(frozen=True)
class AnchorReference:
    """A realization citing an anchor row that is already there."""

    anchor_id: str
    kind: Literal["existing"] = "existing"


@dataclass(frozen=True)
class RealizationClaimRequest:
    repository_id: str
    claim: RealizationClaimDraft
    anchor: Any
    provenance: Authorship


@dataclass(frozen=True)
class RemoveFamilyMemberRequest:
    """Names the membership row to delete; the digest is accepted and not checked."""

    repository_id: str
    member_id: str
    expected_row_digest: str = ""


@dataclass(frozen=True)
class RemoveRealizationClaimRequest:
    """Names the claim row to delete; the digest is accepted and not checked."""

    repository_id: str
    claim_id: str
    expected_row_digest: str = ""


@dataclass(frozen=True)
class RowOutcome:
    """What an insert or a delete answers: the row's identities, and always ``created``/``removed``."""

    state: str = "created"
    repository_id: str = ""
    invariant_id: str = ""
    revision_id: str = ""
    family_id: str = ""
    member_id: str = ""
    anchor_id: str = ""
    claim_id: str = ""
    payload_digest: str | None = None
    stored: bool = True
    refusal: None = None


def write_authorship(
    *,
    actor_ref: str,
    authorization_ref: str,
    origin_refs: Sequence[str] = (),
    recorded_at: str | None = None,
) -> Authorship:
    """One provenance envelope for the rows a fixture inserts."""

    return Authorship(
        actor_ref=actor_ref,
        authorization_ref=authorization_ref,
        operation_id=uuid4(),
        recorded_at=recorded_at or datetime.now(UTC).isoformat(),
        origin_refs=tuple(origin_refs),
    )


# --- the store ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RowStore(OpenedKnowledgeStore):
    """The production store value, with the builder's inserts beside its read methods."""

    def insert(self, statement: str, parameters: Sequence[Any]) -> None:
        self.connection.execute(statement, tuple(parameters))

    def create_repository(self, identity: RepositoryIdentity) -> Any:  # type: ignore[override]
        if self.get_repository() is None:
            self.insert(
                "INSERT INTO repository (repository_id, authority_home) VALUES (?, ?)",
                (identity.repository_id, identity.authority_home),
            )
        return RowOutcome(repository_id=identity.repository_id)

    def create_invariant(self, request: InvariantRequest) -> RowOutcome:
        self.insert(
            "INSERT INTO invariant (repository_id, invariant_id, display_label, label_provenance) "
            "VALUES (?, ?, ?, ?)",
            (
                request.repository_id,
                request.invariant_id,
                request.display_label,
                encode_authorship(request.provenance),
            ),
        )
        return RowOutcome(repository_id=request.repository_id, invariant_id=request.invariant_id)

    def create_revision(self, request: RevisionRequest) -> RowOutcome:
        draft = request.revision
        unsealed = InvariantRevision(
            repository_id=request.repository_id,
            invariant_id=draft.invariant_id,
            revision_id=draft.revision_id,
            display_version=draft.display_version,
            statement=draft.statement,
            applicability=draft.applicability,
            conditions=draft.conditions,
            exclusions=draft.exclusions,
            state_at_origin=draft.state_at_origin,
            acceptance_ref=draft.acceptance_ref,
            provenance=draft.provenance,
            predecessors=tuple(sorted(draft.predecessors)),
            payload_digest=_UNSEALED,
        )
        seal = revision_payload_digest(unsealed)
        self.insert(
            "INSERT INTO invariant_revision (repository_id, invariant_id, revision_id, "
            "display_version, statement, applicability, conditions, exclusions, state_at_origin, "
            "acceptance_ref, provenance, payload_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                request.repository_id,
                draft.invariant_id,
                draft.revision_id,
                draft.display_version,
                draft.statement,
                draft.applicability,
                encode_typed_column(list(draft.conditions)),
                encode_typed_column(list(draft.exclusions)),
                draft.state_at_origin,
                draft.acceptance_ref,
                encode_authorship(draft.provenance),
                seal,
            ),
        )
        for parent in unsealed.predecessors:
            self.insert(
                "INSERT INTO invariant_predecessor (repository_id, invariant_id, "
                "child_revision_id, parent_revision_id) VALUES (?, ?, ?, ?)",
                (request.repository_id, draft.invariant_id, draft.revision_id, parent),
            )
        return RowOutcome(
            repository_id=request.repository_id,
            invariant_id=draft.invariant_id,
            revision_id=draft.revision_id,
            payload_digest=seal,
        )


def open_knowledge_store(database_path: Path, repository_id: str) -> RowStore:
    """Open (creating when empty) one dataset file with the declared schema, for row inserts.

    The schema is created by the same production call the index builder makes, so the tables a
    fixture fills are exactly the tables an index has.
    """

    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = apsw.Connection(str(path))
    connection.execute("PRAGMA foreign_keys=ON")
    return RowStore(
        database_path=path,
        repository_id=repository_id,
        schema=create_or_validate_schema(connection),
        connection=connection,
    )


# --- the module-shaped inserts ---------------------------------------------------------------------


def _insert(store: OpenedKnowledgeStore, statement: str, parameters: Sequence[Any]) -> None:
    store.connection.execute(statement, tuple(parameters))


def create_family(store: OpenedKnowledgeStore, request: FamilyRequest) -> RowOutcome:
    _insert(
        store,
        "INSERT INTO family (repository_id, family_id, display_label, label_provenance) "
        "VALUES (?, ?, ?, ?)",
        (
            request.repository_id,
            request.family_id,
            request.display_label,
            encode_authorship(request.provenance),
        ),
    )
    return RowOutcome(repository_id=request.repository_id, family_id=request.family_id)


def create_family_revision(
    store: OpenedKnowledgeStore, request: FamilyRevisionRequest
) -> RowOutcome:
    draft = request.revision
    unsealed = FamilyRevision(
        repository_id=request.repository_id,
        family_id=draft.family_id,
        revision_id=draft.revision_id,
        display_version=draft.display_version,
        joint_guarantee=draft.joint_guarantee,
        state_at_origin=draft.state_at_origin,
        acceptance_ref=draft.acceptance_ref,
        provenance=draft.provenance,
        predecessors=tuple(sorted(draft.predecessors)),
        payload_digest=_UNSEALED,
    )
    seal = family_revision_payload_digest(unsealed)
    _insert(
        store,
        "INSERT INTO family_revision (repository_id, family_id, revision_id, display_version, "
        "joint_guarantee, state_at_origin, acceptance_ref, provenance, payload_digest) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            request.repository_id,
            draft.family_id,
            draft.revision_id,
            draft.display_version,
            draft.joint_guarantee,
            draft.state_at_origin,
            draft.acceptance_ref,
            encode_authorship(draft.provenance),
            seal,
        ),
    )
    for parent in unsealed.predecessors:
        _insert(
            store,
            "INSERT INTO family_predecessor (repository_id, family_id, child_revision_id, "
            "parent_revision_id) VALUES (?, ?, ?, ?)",
            (request.repository_id, draft.family_id, draft.revision_id, parent),
        )
    return RowOutcome(
        repository_id=request.repository_id,
        family_id=draft.family_id,
        revision_id=draft.revision_id,
        payload_digest=seal,
    )


def create_family_member(store: OpenedKnowledgeStore, request: FamilyMemberRequest) -> RowOutcome:
    member = request.member
    _insert(
        store,
        "INSERT INTO family_member (repository_id, member_id, family_revision_id, "
        "invariant_revision_id, provenance) VALUES (?, ?, ?, ?, ?)",
        (
            request.repository_id,
            member.member_id,
            member.family_revision_id,
            member.invariant_revision_id,
            encode_authorship(member.provenance),
        ),
    )
    return RowOutcome(repository_id=request.repository_id, member_id=member.member_id)


def remove_family_member(
    store: OpenedKnowledgeStore, request: RemoveFamilyMemberRequest
) -> RowOutcome:
    _insert(
        store,
        "DELETE FROM family_member WHERE repository_id = ? AND member_id = ?",
        (request.repository_id, request.member_id),
    )
    return RowOutcome(
        state="removed", repository_id=request.repository_id, member_id=request.member_id
    )


def create_source_anchor(store: OpenedKnowledgeStore, request: SourceAnchorRequest) -> RowOutcome:
    anchor = request.anchor
    _insert(
        store,
        "INSERT INTO source_anchor (repository_id, anchor_id, path, source_identity, locator, "
        "provenance) VALUES (?, ?, ?, ?, ?, ?)",
        (
            request.repository_id,
            str(anchor.anchor_id),
            anchor.path,
            encode_typed_column(anchor.source_identity),
            encode_typed_column(anchor.locator),
            encode_authorship(request.provenance),
        ),
    )
    return RowOutcome(repository_id=request.repository_id, anchor_id=str(anchor.anchor_id))


def create_realization_claim(
    store: OpenedKnowledgeStore, request: RealizationClaimRequest
) -> RowOutcome:
    """Insert one claim row, and its anchor row first when the request carries a new anchor."""

    endpoint: Any = request.anchor  # the builder's container, or production's own model
    if endpoint.kind == "new":
        anchor_id = create_source_anchor(
            store,
            SourceAnchorRequest(
                repository_id=request.repository_id,
                anchor=endpoint.anchor,
                provenance=request.provenance,
            ),
        ).anchor_id
    else:
        anchor_id = str(endpoint.anchor_id)
    claim = request.claim
    _insert(
        store,
        "INSERT INTO realization_claim (repository_id, claim_id, invariant_revision_id, "
        "anchor_id, role, rationale, provenance) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            request.repository_id,
            claim.claim_id,
            claim.invariant_revision_id,
            anchor_id,
            claim.role,
            claim.rationale,
            encode_authorship(request.provenance),
        ),
    )
    return RowOutcome(
        repository_id=request.repository_id, claim_id=claim.claim_id, anchor_id=anchor_id
    )


def remove_realization_claim(
    store: OpenedKnowledgeStore, request: RemoveRealizationClaimRequest
) -> RowOutcome:
    _insert(
        store,
        "DELETE FROM realization_claim WHERE repository_id = ? AND claim_id = ?",
        (request.repository_id, request.claim_id),
    )
    return RowOutcome(
        state="removed", repository_id=request.repository_id, claim_id=request.claim_id
    )


class _Namespace:
    """The builder's functions under a module-shaped name, beside a production module's reads."""

    def __init__(self, production: ModuleType | None, **inserts: Any) -> None:
        self._production = production
        self.__dict__.update(inserts)

    def __getattr__(self, name: str) -> Any:
        if self._production is None:
            raise AttributeError(name)
        return getattr(self._production, name)


families: Any = _Namespace(
    _production_families,
    create_family=create_family,
    create_family_revision=create_family_revision,
)
memberships: Any = _Namespace(
    _production_memberships,
    create_family_member=create_family_member,
    remove_family_member=remove_family_member,
)
realizations: Any = _Namespace(
    None,
    create_realization_claim=create_realization_claim,
    remove_realization_claim=remove_realization_claim,
)
anchors: Any = _Namespace(None, create_source_anchor=create_source_anchor)
