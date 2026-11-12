"""Test-only datasets for the knowledge read side: a text memory tree behind every file.

**Why it exists.** The canonical knowledge database and every writer of its rows are retired
(MIK-R26). The read code that remains -- the scope read, the views, the comparison and the
reviewer's readers -- runs over the derived index of a memory tree, whose logical tables
production fills in exactly one place, ``memory/knowledge_index/projection.py``. Rule 3 of
MIK-R74 therefore requires what this module does: every dataset file it hands out is built by
writing a text memory tree and calling :func:`build_index`, exactly as production builds one.
Neither this module nor a test module holds an ``INSERT INTO`` for an index table, enforced by
``test_knowledge_index_inserts.py``; the one exception rule 3 names is
``knowledge_conversion_test_support.py``, which builds the legacy database for the
conversion's reader (plus the retired-writer detector's own inert spelling vectors, which
never reach a connection).

**What it is not.** It is not a writer and holds none of the retired write rules. A text record
holds exactly one revision, so a dataset holds exactly one revision of each invariant and each
family: a second ``create_revision`` (or ``create_family_revision``) for an identity the dataset
already holds is refused loudly rather than merged silently, because the tree could not say which
revision it means. What a test asks for is otherwise written without refusal -- no lock, no label,
lineage, endpoint or duplicate check -- and only the text schemas' own constraints can reject a
row. No production code was moved here; production keeps no caller of anything in this file.

**Its call shapes.** The fixtures of the read-side test modules were written against
``store.create_invariant(request)``, ``families.create_family(store, request)`` and their
siblings. The builder answers the same call shapes so those fixtures keep their structure, with
two differences the text makes:

* every identity a call returns is the projection's own UUID for the text it wrote (a name-based
  ``uuid5`` over the text ID), never the UUID the caller passed in -- a caller-side UUID names
  nothing in a tree, so the fixture uses what the outcome returns;
* the repository of every dataset is the index's own namespace
  (:data:`INDEX_REPOSITORY_ID`), because an index is keyed by its tree alone (rule 2).

* :func:`open_knowledge_store` returns a :class:`RowStore`, the production
  ``OpenedKnowledgeStore`` (so every production reader accepts it) with the text-backed
  operations added;
* ``families``, ``memberships``, ``realizations`` and ``anchors`` are namespaces holding the
  builder's operations beside the production modules' read functions;
* the ``…Request`` types are plain containers for the arguments; the ``…Draft`` models they carry
  are production's own, except :class:`RevisionDraft`, which only fixtures construct and which
  therefore lives here;
* every operation returns a :class:`RowOutcome` whose ``state`` is ``"created"`` (or
  ``"removed"``), carrying the projected identities the tree produced.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Literal
from uuid import uuid4

import apsw
from agents_remember.kernel.canonical_json import canonical_json_bytes
from agents_remember.memory.knowledge import families as _production_families
from agents_remember.memory.knowledge import memberships as _production_memberships
from agents_remember.memory.knowledge.connection import (
    create_or_validate_schema,
)
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore
from agents_remember.memory.knowledge_index.build import build_index
from agents_remember.memory.knowledge_index.projection import (
    INDEX_REPOSITORY_ID,
    text_uuid,
)
from agents_remember.memory.knowledge_index.tree import MemoryTreeSnapshot
from agents_remember.models.knowledge.authorship import Authorship, KnowledgeState
from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    UUID_PATTERN,
    KnowledgeModel,
)
from agents_remember.models.knowledge.family import FamilyRevisionDraft
from agents_remember.models.knowledge.graph import FamilyMemberDraft
from agents_remember.models.knowledge_files.documents import file_sidecar_path, record_path
from anchor_fixture_models import RealizationClaimDraft, SourceAnchorDraft
from pydantic import Field, model_validator

__all__ = [
    "AnchorReference",
    "FamilyMemberRequest",
    "FamilyRequest",
    "FamilyRevisionRequest",
    "InvariantRequest",
    "NewAnchor",
    "ProofDraft",
    "ProofRequest",
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
    "proofs",
    "realizations",
    "write_authorship",
]

# The origin every fixture row is written under: the leaf that owns the rewrite, so the
# projected provenance names a real task and leaf rather than an unrecorded author.
_TEXT_ORIGIN = {"task": "260928-MIK", "leaf": "260928-MIK-L74"}
# A text record holds one revision; a second revision of one identity in one dataset refuses.
_SINGLE_REVISION = "a text record holds exactly one revision (MIK-R74 rule 3)"


# --- the argument containers ----------------------------------------------------------------------


@dataclass(frozen=True)
class InvariantRequest:
    repository_id: str
    invariant_id: str
    display_label: str
    provenance: Authorship


class RevisionDraft(KnowledgeModel):
    """One authored revision aggregate as a caller supplies it, before projection.

    The digest is absent by design: the projection recomputes and stores it, so a caller cannot
    present a payload whose seal belongs to different content. ``predecessors`` is accepted and
    ignored: text records hold no predecessor edge (MIK-R74 rule 1), and the seal the projection
    computes covers ().
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
    """A realization whose anchor row is written with it, as one sidecar entry."""

    anchor: SourceAnchorDraft
    kind: Literal["new"] = "new"


@dataclass(frozen=True)
class AnchorReference:
    """A realization citing an anchor the dataset already recorded, by the caller's anchor id."""

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
    """What an operation answers: the projected identities, and always ``created``/``removed``."""

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
    """One provenance envelope for the rows a fixture writes (carried, not projected)."""

    return Authorship(
        actor_ref=actor_ref,
        authorization_ref=authorization_ref,
        operation_id=uuid4(),
        recorded_at=recorded_at or datetime.now(UTC).isoformat(),
        origin_refs=tuple(origin_refs),
    )


# --- the text tree --------------------------------------------------------------------------------


def _mint(prefix: str, sequence: int) -> str:
    """Mint one text ID: the kind prefix and a six-digit Crockford body the ID patterns admit."""

    return f"{prefix}-{sequence:06d}"


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _entry_text_id(path: str, role: str) -> str:
    """Derive one entry's text ID from its path and role, so two datasets built from the same
    topology mint the same entry for the same association.

    A sequential counter would renumber every entry after an omitted one, so an unchanged claim
    would project a different claim UUID on each side of a comparison and read as a removal plus
    an unrelated addition. The derived ID keeps the row identity stable across the omission, and
    a new path or role still mints a new row.
    """

    digest = hashlib.sha256(f"{path}\0{role}".encode()).digest()
    value = int.from_bytes(digest[:4], "big")
    body = "".join(_CROCKFORD[(value >> (5 * index)) & 31] for index in range(6))
    return f"RLZ-{body}"


def _revision_number(display_version: str) -> int:
    """Read a text revision number off a display version (``v2``/``r2`` -> 2, anything else 1)."""

    match = re.search(r"(\d+)$", display_version)
    return int(match.group(1)) if match else 1


def _locator_document(locator: Any) -> dict[str, Any]:
    """Spell one caller-side source locator the way a sidecar anchor carries it."""

    kind = getattr(locator, "kind", None)
    if kind == "symbol":
        return {"kind": "symbol", "name": locator.qualified_name}
    if kind == "line_range":
        return {
            "kind": "line_range",
            "start": locator.start_line,
            "end": locator.end_line,
        }
    return {"kind": "file"}


class _TextTree:
    """One dataset as text files: invariant/family records and file sidecars with entries."""

    def __init__(self) -> None:
        self.invariants: dict[str, dict[str, Any]] = {}
        self.families: dict[str, dict[str, Any]] = {}
        self.sidecars: dict[str, dict[str, Any]] = {}
        self.invariant_sequence = 0
        self.family_sequence = 0
        self.entry_sequence = 0
        # Caller UUID -> text ID, so a fixture's names resolve to the tree's names.
        self.invariant_ids: dict[str, str] = {}
        self.revision_invariants: dict[str, str] = {}
        self.family_ids: dict[str, str] = {}
        self.family_revision_families: dict[str, str] = {}
        # Projected UUID -> text lookup, so a reopened store answers the projected
        # identities fixtures hand out, not only the caller UUIDs they were built with.
        self.revision_invariants_by_projected: dict[str, str] = {}
        self.family_revision_families_by_projected: dict[str, str] = {}
        self.claim_entries: dict[str, str] = {}
        self.anchor_ids: dict[str, str] = {}
        self.anchor_facts: dict[str, dict[str, Any]] = {}
        self.member_rows: dict[str, tuple[str, str]] = {}
        # Projected UUID -> text lookup, so a remove can name the row by the identity the tree
        # returned rather than only by the caller-side UUID it was built with.
        self.claim_entries_by_projected: dict[str, str] = {}
        self.member_rows_by_projected: dict[str, tuple[str, str]] = {}
        self.anchor_facts_by_projected: dict[str, dict[str, Any]] = {}
        # Text IDs whose single revision is already written: a text record holds one.
        self.invariant_revised: set[str] = set()
        self.family_revised: set[str] = set()

    def invariant_text_id(self, caller_id: str) -> str:
        """Return the text ID for one caller-side invariant, minting its record shell first."""

        text_id = self.invariant_ids.get(caller_id)
        if text_id is None:
            self.invariant_sequence += 1
            text_id = _mint("INV", self.invariant_sequence)
            self.invariant_ids[caller_id] = text_id
            self.invariants[text_id] = {
                "schema": "ar-invariant/v1",
                "id": text_id,
                "origin": dict(_TEXT_ORIGIN),
                "revision": 1,
                "status": "proposed",
                "statement": text_id,
                "applicability": "Every admitted candidate write.",
                "conditions": [],
                "exclusions": [],
                "supersedes": [],
                "admission": "legacy-unassessed",
            }
        return text_id

    def extra_invariant_text_id(self, caller_id: str) -> str:
        """Return the text ID for one caller-side extra invariant, derived from its UUID.

        Positional minting would give two different caller identities the same text ID
        whenever they sit at the same authoring position on the two sides of a comparison,
        which would project them to one record. Deriving from the caller UUID keeps the same
        caller identity on one record on both sides and two caller identities apart.
        """

        text_id = self.invariant_ids.get(caller_id)
        if text_id is None:
            # Six-plus-two Crockford body from the caller UUID's own hex: hex digits are all
            # valid Crockford characters once uppercased, so the derived ID satisfies the
            # record-filename rule while staying distinct per caller identity.
            text_id = f"INV-{caller_id.replace('-', '')[:8].upper()}"
            if text_id in self.invariants:
                raise ValueError(f"extra invariant text ID {text_id!r} is already recorded")
            self.invariant_ids[caller_id] = text_id
            self.invariants[text_id] = {
                "schema": "ar-invariant/v1",
                "id": text_id,
                "origin": dict(_TEXT_ORIGIN),
                "revision": 1,
                "status": "proposed",
                "statement": text_id,
                "applicability": "Every admitted candidate write.",
                "conditions": [],
                "exclusions": [],
                "supersedes": [],
                "admission": "legacy-unassessed",
            }
        return text_id

    def family_text_id(self, caller_id: str) -> str:
        """Return the text ID for one caller-side family, minting its record shell first."""

        text_id = self.family_ids.get(caller_id)
        if text_id is None:
            self.family_sequence += 1
            text_id = _mint("FAM", self.family_sequence)
            self.family_ids[caller_id] = text_id
            self.families[text_id] = {
                "schema": "ar-family/v1",
                "id": text_id,
                "origin": dict(_TEXT_ORIGIN),
                "revision": 1,
                "status": "proposed",
                "title": text_id,
                "guarantee": "Every member the family admits.",
                "members": [],
                "routes": [],
                "admission": "legacy-unassessed",
            }
        return text_id

    def files(self) -> dict[str, bytes]:
        """Render the tree as a snapshot's files: one JSON document per record and sidecar."""

        rendered: dict[str, bytes] = {}
        for text_id, record in self.invariants.items():
            rendered[record_path("invariant", text_id, text_id.lower())] = canonical_json_bytes(
                record
            )
        for text_id, record in self.families.items():
            rendered[record_path("family", text_id, text_id.lower())] = canonical_json_bytes(record)
        for path, sidecar in self.sidecars.items():
            rendered[path] = canonical_json_bytes(sidecar)
        return rendered

    def to_authoring(self) -> dict[str, Any]:
        """Dump the whole authoring basis as JSON-native data: records, maps and counters.

        Everything a builder operation consults is carried: without the maps and the
        sequences a reopened store cannot extend the dataset it opens, only reread it.
        Tuples become lists and sets become sorted lists; :meth:`from_authoring` restores
        both, so a dump/load round-trip rebuilds byte-identical files.
        """

        return {
            "format": "ar-text-tree-authoring/v1",
            "invariants": self.invariants,
            "families": self.families,
            "sidecars": self.sidecars,
            "invariant_sequence": self.invariant_sequence,
            "family_sequence": self.family_sequence,
            "entry_sequence": self.entry_sequence,
            "invariant_ids": self.invariant_ids,
            "revision_invariants": self.revision_invariants,
            "revision_invariants_by_projected": self.revision_invariants_by_projected,
            "family_ids": self.family_ids,
            "family_revision_families": self.family_revision_families,
            "family_revision_families_by_projected": (self.family_revision_families_by_projected),
            "claim_entries": self.claim_entries,
            "anchor_ids": self.anchor_ids,
            "anchor_facts": self.anchor_facts,
            "member_rows": {key: list(pair) for key, pair in self.member_rows.items()},
            "claim_entries_by_projected": self.claim_entries_by_projected,
            "member_rows_by_projected": {
                key: list(pair) for key, pair in self.member_rows_by_projected.items()
            },
            "anchor_facts_by_projected": self.anchor_facts_by_projected,
            "invariant_revised": sorted(self.invariant_revised),
            "family_revised": sorted(self.family_revised),
        }

    @classmethod
    def from_authoring(cls, data: dict[str, Any]) -> _TextTree:
        """Rebuild the authoring basis a :meth:`to_authoring` dump holds, failing closed.

        A present-but-invalid dump raises instead of opening onto an empty tree: silently
        losing the basis is exactly the reopen-extension failure this exists to prevent.
        """

        if not isinstance(data, dict) or data.get("format") != "ar-text-tree-authoring/v1":
            raise ValueError("not an ar-text-tree-authoring/v1 authoring dump")
        try:
            tree = cls()
            tree.invariants = dict(data["invariants"])
            tree.families = dict(data["families"])
            tree.sidecars = dict(data["sidecars"])
            tree.invariant_sequence = int(data["invariant_sequence"])
            tree.family_sequence = int(data["family_sequence"])
            tree.entry_sequence = int(data["entry_sequence"])
            tree.invariant_ids = dict(data["invariant_ids"])
            tree.revision_invariants = dict(data["revision_invariants"])
            tree.revision_invariants_by_projected = dict(data["revision_invariants_by_projected"])
            tree.family_ids = dict(data["family_ids"])
            tree.family_revision_families = dict(data["family_revision_families"])
            tree.family_revision_families_by_projected = dict(
                data["family_revision_families_by_projected"]
            )
            tree.claim_entries = dict(data["claim_entries"])
            tree.anchor_ids = dict(data["anchor_ids"])
            tree.anchor_facts = dict(data["anchor_facts"])
            tree.member_rows = {
                str(key): (pair[0], pair[1]) for key, pair in dict(data["member_rows"]).items()
            }
            tree.claim_entries_by_projected = dict(data["claim_entries_by_projected"])
            tree.member_rows_by_projected = {
                str(key): (pair[0], pair[1])
                for key, pair in dict(data["member_rows_by_projected"]).items()
            }
            tree.anchor_facts_by_projected = dict(data["anchor_facts_by_projected"])
            tree.invariant_revised = set(data["invariant_revised"])
            tree.family_revised = set(data["family_revised"])
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise ValueError(
                f"the authoring dump beside the dataset is not intact: {exc!r}"
            ) from exc
        return tree


_AUTHORING_SUFFIX = ".authoring.json"


def _authoring_path(database_path: Path) -> Path:
    """Name the file beside a dataset file that carries its text authoring basis."""

    return database_path.with_name(database_path.name + _AUTHORING_SUFFIX)


def _write_authoring_basis(database_path: Path, tree: _TextTree) -> None:
    """Persist the tree every rebuild just built from, beside the dataset file."""

    _authoring_path(database_path).write_text(
        json.dumps(tree.to_authoring(), indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )


def _read_authoring_basis(database_path: Path) -> _TextTree | None:
    """Reload a persisted authoring basis, or None when the dataset has none yet.

    A present-but-unreadable dump fails closed (see :meth:`_TextTree.from_authoring`):
    opening onto an empty tree would silently drop the basis this exists to keep.
    """

    sidecar = _authoring_path(database_path)
    if not sidecar.exists():
        return None
    return _TextTree.from_authoring(json.loads(sidecar.read_text(encoding="utf-8")))


def _snapshot(tree: _TextTree) -> MemoryTreeSnapshot:
    """Wrap one text tree as the in-memory snapshot :func:`build_index` reads."""

    return MemoryTreeSnapshot(
        key="0" * 40,
        source="directory",
        location="fixture",
        files=tree.files(),
    )


# --- the store ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RowStore(OpenedKnowledgeStore):
    """The production store value over a text-built index, with the builder's operations beside it.

    Every operation mutates the text tree and rebuilds the dataset file with
    :func:`build_index`, so the connection always reads a real index production built.
    """

    tree: _TextTree = field(default_factory=_TextTree)

    def _rebuild(self) -> None:
        """Rebuild the dataset file from the text tree and reopen the read connection."""

        self.connection.close()
        path = Path(self.database_path)
        if path.exists():
            path.unlink()
        report = build_index(_snapshot(self.tree), path)
        if report.state != "complete":
            raise ValueError(f"the fixture tree did not build cleanly: {report.problems!r}")
        connection = apsw.Connection(str(path))
        connection.execute("PRAGMA foreign_keys=ON")
        object.__setattr__(self, "connection", connection)
        _write_authoring_basis(path, self.tree)

    def create_repository(self, *_args: Any) -> Any:  # type: ignore[override]
        """Accept the repository row: the projection writes the index's own namespace on build."""

        return RowOutcome(repository_id=INDEX_REPOSITORY_ID)

    def create_invariant(self, request: InvariantRequest) -> RowOutcome:
        """Register one invariant's text shell; its revision's content arrives with the revision.

        The caller's display label names no text field: the projection reads the record's own
        ID as its display label, so the label is carried and not stored.
        """

        text_id = self.tree.invariant_text_id(request.invariant_id)
        self._rebuild()
        return RowOutcome(
            repository_id=INDEX_REPOSITORY_ID,
            invariant_id=text_uuid("identity", text_id),
        )

    def create_extra_invariant(self, request: InvariantRequest) -> RowOutcome:
        """Register one extra invariant's text shell under its caller-derived text ID.

        Positional minting would collide across the two sides of a comparison, so extras
        derive their text ID from the caller UUID instead (see
        :meth:`_TextTree.extra_invariant_text_id`).
        """

        text_id = self.tree.extra_invariant_text_id(request.invariant_id)
        self._rebuild()
        return RowOutcome(
            repository_id=INDEX_REPOSITORY_ID,
            invariant_id=text_uuid("identity", text_id),
        )

    def create_revision(self, request: RevisionRequest) -> RowOutcome:
        """Write one invariant's single revision as its record's content and rebuild."""

        draft = request.revision
        text_id = self.tree.invariant_text_id(draft.invariant_id)
        if draft.revision_id in self.tree.revision_invariants:
            raise ValueError(
                f"revision {draft.revision_id!r} is already recorded; {_SINGLE_REVISION}"
            )
        record = self.tree.invariants[text_id]
        if text_id in self.tree.invariant_revised:
            raise ValueError(f"invariant {text_id} already holds a revision; {_SINGLE_REVISION}")
        self.tree.invariant_revised.add(text_id)
        record["revision"] = _revision_number(draft.display_version)
        record["status"] = "accepted" if draft.state_at_origin == "accepted" else "proposed"
        record["statement"] = draft.statement
        record["applicability"] = draft.applicability
        record["conditions"] = list(draft.conditions)
        record["exclusions"] = list(draft.exclusions)
        self.tree.revision_invariants[draft.revision_id] = text_id
        revision = text_uuid("revision", f"{text_id}@{record['revision']}")
        self.tree.revision_invariants_by_projected[revision] = text_id
        self._rebuild()
        return RowOutcome(
            repository_id=INDEX_REPOSITORY_ID,
            invariant_id=text_uuid("identity", text_id),
            revision_id=revision,
        )

    def close(self) -> None:
        """Close the read connection; a second close is a no-op for fixture teardown order."""

        with contextlib.suppress(apsw.Error):
            self.connection.close()


def open_knowledge_store(database_path: Path, repository_id: str) -> RowStore:
    """Open one text-backed dataset file, creating it from an empty text tree when absent.

    An existing file is opened as it stands and never wiped: cases that curate a built tree
    (one-sided identities, added families) write into the rows the builder left. The store
    reuses the file's own text authoring basis, persisted beside the dataset by every
    rebuild: reopening keeps the builder's maps and counters, so extending the opened
    dataset stages onto the original text tree instead of an empty one. A dataset with no
    persisted basis (built outside the builder, or predating it) still opens onto an empty
    tree for row-level reads. The passed namespace is accepted and not used: every index
    is keyed by its tree alone, so the store reads under :data:`INDEX_REPOSITORY_ID`.
    """

    _ = repository_id
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        build_index(_snapshot(_TextTree()), path)
    connection = apsw.Connection(str(path))
    connection.execute("PRAGMA foreign_keys=ON")
    basis = _read_authoring_basis(path)
    return RowStore(
        database_path=path,
        repository_id=INDEX_REPOSITORY_ID,
        schema=create_or_validate_schema(connection),
        connection=connection,
        tree=basis if basis is not None else _TextTree(),
    )


# --- the module-shaped operations -------------------------------------------------------------------


def _store_tree(store: OpenedKnowledgeStore) -> _TextTree:
    if not isinstance(store, RowStore):
        raise TypeError("this builder writes text trees, so it needs its own RowStore")
    return store.tree


def _rebuild(store: OpenedKnowledgeStore) -> None:
    if not isinstance(store, RowStore):
        raise TypeError("this builder writes text trees, so it needs its own RowStore")
    store._rebuild()


def create_family(store: OpenedKnowledgeStore, request: FamilyRequest) -> RowOutcome:
    """Register one family's text shell; its revision's content arrives with the revision."""

    tree = _store_tree(store)
    text_id = tree.family_text_id(request.family_id)
    tree.families[text_id]["title"] = request.display_label
    _rebuild(store)
    return RowOutcome(
        repository_id=INDEX_REPOSITORY_ID,
        family_id=text_uuid("identity", text_id),
    )


def create_family_revision(
    store: OpenedKnowledgeStore, request: FamilyRevisionRequest
) -> RowOutcome:
    """Write one family's single revision as its record's content and rebuild."""

    tree = _store_tree(store)
    draft = request.revision
    text_id = tree.family_text_id(draft.family_id)
    record = tree.families[text_id]
    if text_id in tree.family_revised:
        raise ValueError(f"family {text_id} already holds a revision; {_SINGLE_REVISION}")
    tree.family_revised.add(text_id)
    record["revision"] = _revision_number(draft.display_version)
    record["status"] = "accepted" if draft.state_at_origin == "accepted" else "proposed"
    record["guarantee"] = draft.joint_guarantee
    tree.family_revision_families[draft.revision_id] = text_id
    revision = text_uuid("revision", f"{text_id}@{record['revision']}")
    tree.family_revision_families_by_projected[revision] = text_id
    _rebuild(store)
    return RowOutcome(
        repository_id=INDEX_REPOSITORY_ID,
        family_id=text_uuid("identity", text_id),
        revision_id=revision,
    )


def _revision_text_id(tree: _TextTree, revision_id: Any) -> str | None:
    """Resolve one revision to its text ID by caller UUID or projected UUID."""

    text_id = tree.revision_invariants.get(revision_id)
    if text_id is None:
        text_id = tree.revision_invariants_by_projected.get(str(revision_id))
    return text_id


def create_family_member(store: OpenedKnowledgeStore, request: FamilyMemberRequest) -> RowOutcome:
    """Record one member invariant in its family revision's text and rebuild."""

    tree = _store_tree(store)
    member = request.member
    family_text = tree.family_revision_families.get(member.family_revision_id)
    if family_text is None:
        family_text = tree.family_revision_families_by_projected.get(str(member.family_revision_id))
    if family_text is None:
        raise ValueError(
            f"family revision {member.family_revision_id!r} is not recorded in this dataset"
        )
    invariant_text = _revision_text_id(tree, member.invariant_revision_id)
    if invariant_text is None:
        raise ValueError(
            f"member revision {member.invariant_revision_id!r} is not recorded in this dataset"
        )
    record = tree.families[family_text]
    if invariant_text in record["members"]:
        raise ValueError(
            f"invariant {invariant_text} is already a member of family {family_text}; "
            "a text family lists each member once"
        )
    record["members"].append(invariant_text)
    tree.member_rows[str(member.member_id)] = (family_text, invariant_text)
    member_uuid = text_uuid("member", f"{family_text}/{invariant_text}")
    tree.member_rows_by_projected[member_uuid] = (family_text, invariant_text)
    _rebuild(store)
    return RowOutcome(
        repository_id=INDEX_REPOSITORY_ID,
        member_id=member_uuid,
    )


def remove_family_member(
    store: OpenedKnowledgeStore, request: RemoveFamilyMemberRequest
) -> RowOutcome:
    """Withdraw one membership from its family revision's text and rebuild."""

    tree = _store_tree(store)
    identifier = str(request.member_id)
    row = tree.member_rows.pop(identifier, None)
    if row is None:
        row = tree.member_rows_by_projected.pop(identifier, None)
    if row is not None:
        family_text, invariant_text = row
        members = tree.families[family_text]["members"]
        if invariant_text in members:
            members.remove(invariant_text)
    _rebuild(store)
    return RowOutcome(
        state="removed", repository_id=INDEX_REPOSITORY_ID, member_id=request.member_id
    )


def _anchor_facts(anchor: SourceAnchorDraft) -> dict[str, Any]:
    """Read one caller-side anchor draft as the blob, locator and path one entry carries."""

    identity = anchor.source_identity
    blob = getattr(identity, "object_id", None) or getattr(identity, "content", None)
    if blob is None:
        raise ValueError(f"an anchor names its source object: {identity!r}")
    content = "sha256:" + hashlib.sha256(str(blob).encode("utf-8")).hexdigest()
    return {
        "blob": str(blob),
        "locator": _locator_document(anchor.locator),
        "path": anchor.path,
        "content": content,
    }


def create_source_anchor(store: OpenedKnowledgeStore, request: SourceAnchorRequest) -> RowOutcome:
    """Record one anchor's facts for a later ``AnchorReference``; anchors live in entries.

    A text tree carries anchors only inside realization entries, so a standalone anchor writes
    no file yet: its blob, locator and path are remembered, and the entry that cites it projects
    the anchor row. The outcome carries the caller's anchor id; the projected anchor UUID is
    returned with the entry that cites it.
    """

    tree = _store_tree(store)
    facts = _anchor_facts(request.anchor)
    tree.anchor_facts[str(request.anchor.anchor_id)] = facts
    return RowOutcome(repository_id=INDEX_REPOSITORY_ID, anchor_id=str(request.anchor.anchor_id))


def create_realization_claim(
    store: OpenedKnowledgeStore, request: RealizationClaimRequest
) -> RowOutcome:
    """Write one realization entry into its source file's sidecar and rebuild."""

    tree = _store_tree(store)
    endpoint: Any = request.anchor  # the builder's container, or production's own model
    if endpoint.kind == "new":
        facts = _anchor_facts(endpoint.anchor)
        tree.anchor_facts[str(endpoint.anchor.anchor_id)] = facts
    else:
        facts = tree.anchor_facts.get(str(endpoint.anchor_id))
        if facts is None:
            facts = tree.anchor_facts_by_projected.get(str(endpoint.anchor_id))
        if facts is None:
            raise ValueError(f"anchor {endpoint.anchor_id!r} is not recorded in this dataset")
    claim = request.claim
    invariant_text = _revision_text_id(tree, claim.invariant_revision_id)
    if invariant_text is None:
        raise ValueError(
            f"cited revision {claim.invariant_revision_id!r} is not recorded in this dataset"
        )
    entry_id = _entry_text_id(facts["path"], claim.role)
    for sidecar in tree.sidecars.values():
        if any(entry["id"] == entry_id for entry in sidecar["realizes"]):
            raise ValueError(
                f"an entry for {facts['path']} with role {claim.role} is already recorded; "
                "a text tree holds one entry per path and role"
            )
    tree.claim_entries[str(claim.claim_id)] = entry_id
    sidecar_path = file_sidecar_path(facts["path"])
    sidecar = tree.sidecars.get(sidecar_path)
    if sidecar is None:
        sidecar = {
            "schema": "ar-onboarding-file/v1",
            "path": facts["path"],
            "references": {},
            "realizes": [],
        }
        tree.sidecars[sidecar_path] = sidecar
    sidecar["realizes"].append(
        {
            "id": entry_id,
            "invariant": invariant_text,
            "anchor": {
                "locator": facts["locator"],
                "blob": facts["blob"],
                "content": facts["content"],
            },
            "role": claim.role,
            "rationale": claim.rationale,
        }
    )
    anchor_uuid = text_uuid("anchor", entry_id)
    for caller_anchor, known in list(tree.anchor_facts.items()):
        if known is facts:
            tree.anchor_ids[caller_anchor] = anchor_uuid
    claim_uuid = text_uuid("claim", entry_id)
    tree.claim_entries_by_projected[claim_uuid] = entry_id
    tree.anchor_facts_by_projected[anchor_uuid] = facts
    _rebuild(store)
    return RowOutcome(
        repository_id=INDEX_REPOSITORY_ID,
        claim_id=claim_uuid,
        anchor_id=anchor_uuid,
    )


def _proof_text_id(path: str, facet: str) -> str:
    """Derive one proof entry's text ID from its path and facet, mirroring entries.

    Realization entries derive from path and role; proofs carry no role, so the facet
    takes its place. The derived ID keeps the row identity stable across omission,
    and a new path or facet still mints a new row.
    """

    digest = hashlib.sha256(f"{path}\0{facet}".encode()).digest()
    value = int.from_bytes(digest[:4], "big")
    body = "".join(_CROCKFORD[(value >> (5 * index)) & 31] for index in range(6))
    return f"PRF-{body}"


@dataclass(frozen=True)
class ProofDraft:
    """One anchored test's demonstration of a facet of an invariant revision."""

    proof_id: str
    invariant_revision_id: str
    facet: str


@dataclass(frozen=True)
class ProofRequest:
    """Author one proof entry into its source file's sidecar and rebuild."""

    repository_id: str
    proof: ProofDraft
    anchor: Any


def create_proof(store: OpenedKnowledgeStore, request: ProofRequest) -> RowOutcome:
    """Write one proof entry into its source file's sidecar and rebuild."""

    tree = _store_tree(store)
    facts = _anchor_facts(request.anchor)
    invariant_text = _revision_text_id(tree, request.proof.invariant_revision_id)
    if invariant_text is None:
        raise ValueError(
            f"cited revision {request.proof.invariant_revision_id!r} is not recorded in this dataset"
        )
    entry_id = _proof_text_id(facts["path"], request.proof.facet)
    for sidecar in tree.sidecars.values():
        known = [entry["id"] for entry in sidecar["realizes"]]
        known += [entry["id"] for entry in sidecar.get("proves", ())]
        if entry_id in known:
            raise ValueError(
                f"an entry for {facts['path']} is already recorded; "
                "a text tree holds one entry per path and facet"
            )
    sidecar_path = file_sidecar_path(facts["path"])
    sidecar = tree.sidecars.get(sidecar_path)
    if sidecar is None:
        sidecar = {
            "schema": "ar-onboarding-file/v1",
            "path": facts["path"],
            "references": {},
            "realizes": [],
        }
        tree.sidecars[sidecar_path] = sidecar
    proves = sidecar.get("proves")
    if proves is None:
        proves = sidecar["proves"] = []
    proves.append(
        {
            "id": entry_id,
            "invariant": invariant_text,
            "anchor": {
                "locator": facts["locator"],
                "blob": facts["blob"],
                "content": facts["content"],
            },
            "facet": request.proof.facet,
        }
    )
    tree.claim_entries[str(request.proof.proof_id)] = entry_id
    _rebuild(store)
    return RowOutcome(
        repository_id=INDEX_REPOSITORY_ID,
        claim_id=text_uuid("claim", entry_id),
    )


def remove_realization_claim(
    store: OpenedKnowledgeStore, request: RemoveRealizationClaimRequest
) -> RowOutcome:
    """Withdraw one realization entry from its sidecar and rebuild."""

    tree = _store_tree(store)
    identifier = str(request.claim_id)
    entry_id = tree.claim_entries.pop(identifier, None)
    if entry_id is None:
        entry_id = tree.claim_entries_by_projected.pop(identifier, None)
    if entry_id is not None:
        for path in list(tree.sidecars):
            realizes = tree.sidecars[path]["realizes"]
            kept = [entry for entry in realizes if entry["id"] != entry_id]
            if len(kept) != len(realizes):
                if kept:
                    tree.sidecars[path]["realizes"] = kept
                else:
                    del tree.sidecars[path]
                break
    _rebuild(store)
    return RowOutcome(state="removed", repository_id=INDEX_REPOSITORY_ID, claim_id=request.claim_id)


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
proofs: Any = _Namespace(None, create_proof=create_proof)
anchors: Any = _Namespace(None, create_source_anchor=create_source_anchor)
