"""The row-identity vocabulary one candidate batch reads and reports.

Two questions run through the whole batch operation -- "what is stored under this identity?" and
"which identities does this command address?" -- and they are answered here once. Keeping them out
of the precondition module means the checks read as rules over typed values rather than as a mix of
checks and SQL-shaped lookups.

Every digest returned here is the value the read operations already expose: ``row_digest`` for an
authored row and ``payload_digest`` for a sealed revision aggregate. A caller therefore carries an
expectation straight from a read instead of deriving a second identity scheme that could disagree
with the one it read.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from agents_remember.memory.knowledge import (
    anchors,
    facet_records,
    families,
    memberships,
    realizations,
    records,
)
from agents_remember.models.knowledge.candidate import (
    ChangeCommand,
    NewAnchor,
)

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore


# The tables a batch command writes directly. The repository row and the two predecessor-edge
# tables are written only as part of the aggregate that owns them, so an identity outside this set
# is a caller mistake rather than a record this operation can address.
WRITABLE_TABLES: tuple[str, ...] = (
    "invariant",
    "invariant_revision",
    "family",
    "family_revision",
    "source_anchor",
    "family_member",
    "realization_claim",
    "knowledge_record",
    "record_revision",
    "facet_attachment",
    "facet_decision_supersession",
    "explanation",
    "explanation_revision",
)

IdentityPairs = tuple[tuple[str, str], ...]
RecordReader = Callable[["OpenedKnowledgeStore", str], "str | None"]


def stored_record_digest(store: OpenedKnowledgeStore, table: str, record_id: str) -> str | None:
    """Return one stored record's digest, or ``None`` when the identity is not stored."""

    reader = _RECORD_READERS.get(table)
    if reader is None:
        raise ValueError(f"{table!r} is not one of the writable record tables {WRITABLE_TABLES}")
    return reader(store, record_id)


def _invariant_digest(store: OpenedKnowledgeStore, record_id: str) -> str | None:
    identity = store.get_invariant(record_id)
    return None if identity is None else identity.row_digest


def _family_digest(store: OpenedKnowledgeStore, record_id: str) -> str | None:
    identity = families.get_family(store, record_id)
    return None if identity is None else identity.row_digest


def _invariant_revision_digest(store: OpenedKnowledgeStore, record_id: str) -> str | None:
    stored = store.get_revision(record_id)
    return None if stored is None else stored.revision.payload_digest


def _family_revision_digest(store: OpenedKnowledgeStore, record_id: str) -> str | None:
    stored = families.get_family_revision(store, record_id)
    return None if stored is None else stored.revision.payload_digest


def _anchor_digest(store: OpenedKnowledgeStore, record_id: str) -> str | None:
    anchor = anchors.get_anchor(store, record_id)
    return None if anchor is None else records.anchor_row_digest(anchor, store.repository_id)


def _member_digest(store: OpenedKnowledgeStore, record_id: str) -> str | None:
    member = memberships.get_family_member(store, record_id)
    return None if member is None else member.row_digest


def _claim_digest(store: OpenedKnowledgeStore, record_id: str) -> str | None:
    claim = realizations.get_realization_claim(store, record_id)
    return None if claim is None else claim.row_digest


# The facet tables' readers all live in :mod:`…facets`, next to the write path that produces the
# rows: each returns the same value the read projection exposes, so an expectation carried from a
# read names the row the write path will compare against.
_RECORD_READERS: dict[str, RecordReader] = {
    "invariant": _invariant_digest,
    "invariant_revision": _invariant_revision_digest,
    "family": _family_digest,
    "family_revision": _family_revision_digest,
    "source_anchor": _anchor_digest,
    "family_member": _member_digest,
    "realization_claim": _claim_digest,
    "knowledge_record": facet_records.facet_record_digest,
    "record_revision": facet_records.record_revision_content_digest,
    "facet_attachment": facet_records.attachment_endpoint_digest,
    "facet_decision_supersession": facet_records.supersession_digest,
    "explanation": facet_records.explanation_record_digest,
    "explanation_revision": facet_records.explanation_revision_payload_digest,
}

# One record identity per command kind, for the eleven commands that address exactly one. The
# twelfth -- a realization claim that also records a new anchor -- addresses two and is handled in
# :func:`written_identities` itself.
_WRITTEN_IDENTITY: dict[str, Callable[[Any], tuple[str, str]]] = {
    "add_invariant": lambda command: ("invariant", command.invariant_id),
    "set_invariant_label": lambda command: ("invariant", command.invariant_id),
    "add_invariant_revision": lambda command: (
        "invariant_revision",
        command.revision.revision_id,
    ),
    "add_family": lambda command: ("family", command.family_id),
    "set_family_label": lambda command: ("family", command.family_id),
    "add_family_revision": lambda command: ("family_revision", command.revision.revision_id),
    "add_source_anchor": lambda command: ("source_anchor", str(command.anchor.anchor_id)),
    "remove_source_anchor": lambda command: ("source_anchor", command.anchor_id),
    "add_family_member": lambda command: ("family_member", command.member.member_id),
    "remove_family_member": lambda command: ("family_member", command.member_id),
    "remove_realization_claim": lambda command: (
        "realization_claim",
        command.claim_id,
    ),
    # The six authored-judgment commands. Each table's identity is a single column, which is what
    # lets an expectation, a duplicate check and a receipt all address the row the same way.
    "add_facet": lambda command: ("knowledge_record", command.record_id),
    "attach_facet": lambda command: ("facet_attachment", command.attachment_id),
    "remove_facet_attachment": lambda command: ("facet_attachment", command.attachment_id),
    "author_explanation": lambda command: ("explanation", command.explanation_id),
    "add_explanation_revision": lambda command: ("explanation_revision", command.revision_id),
    "designate_explanation": lambda command: ("explanation", command.explanation_id),
}

# The commands that create nothing: they address an existing row to edit or remove it, so two of
# them may name the same row as long as only one command creates it.
_ADDRESSES_EXISTING: tuple[str, ...] = (
    "set_invariant_label",
    "set_family_label",
    "remove_source_anchor",
    "remove_family_member",
    "remove_realization_claim",
    "remove_facet_attachment",
    "designate_explanation",
)


def written_identities(command: ChangeCommand) -> IdentityPairs:
    """Return every record identity one command addresses, as ``(table, record_id)`` pairs."""

    if command.kind == "add_realization_claim":
        identities = [("realization_claim", command.claim.claim_id)]
        if isinstance(command.anchor, NewAnchor):
            identities.append(("source_anchor", str(command.anchor.anchor.anchor_id)))
        return tuple(identities)
    if command.kind == "add_facet":
        identities = [
            ("knowledge_record", command.record_id),
            ("record_revision", command.revision_id),
        ]
        if command.supersedes_revision_id is not None:
            identities.append(("facet_decision_supersession", command.revision_id))
        return tuple(identities)
    if command.kind == "author_explanation":
        return (
            ("explanation", command.explanation_id),
            ("explanation_revision", command.revision_id),
        )
    return (_WRITTEN_IDENTITY[command.kind](command),)


def inserted_identities(command: ChangeCommand) -> IdentityPairs:
    """Return the identities one command *creates*, which is what a duplicate check addresses."""

    if command.kind in _ADDRESSES_EXISTING:
        return ()
    return written_identities(command)


def pending_identities(commands: Sequence[ChangeCommand]) -> set[tuple[str, str]]:
    """Return every identity the batch itself creates, so a later command may cite it."""

    pending: set[tuple[str, str]] = set()
    for command in commands:
        pending.update(inserted_identities(command))
    return pending


def present(
    store: OpenedKnowledgeStore, pending: set[tuple[str, str]], table: str, record_id: str
) -> bool:
    """Whether a record exists now, or will exist by the time this batch is applied."""

    if (table, record_id) in pending:
        return True
    return stored_record_digest(store, table, record_id) is not None
