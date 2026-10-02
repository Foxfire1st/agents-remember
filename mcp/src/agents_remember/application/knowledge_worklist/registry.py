"""The worklist item-kind registry and the item identity (MIK-R08 rules 1-3).

Every kind of worklist item is registered here with four declarations: its **subject key** (what the
subject names, and the form it takes), its **facts** (the fields its ``facts`` object carries), its
**satisfying-row rule** (which history row answers it) and its **owner packet**. This packet
registers the three kinds it raises -- ``touched_invariant``, ``stale_invariant`` and
``reached_family``. The other registrants (MIK-R06 ``family_route_condition``, MIK-R10
``unexplained_hunk`` / ``unexplained_file``, MIK-R11 ``planned_untouched``, MIK-R14
``reconsideration_candidate``, MIK-R30 ``onboarding_trace``) are built later and call
:func:`register_item_kind` when they land.

The satisfying-row rule here is the *lookup* the gate starts from -- the leaf's history row of the
declared row kind about the item's subject (MIK-R07 rule 1). Whether that row is **current** is the
gate's rule (MIK-R09 rule 2), not this registry's; nothing here judges a row.

**Item identity (rule 3).** An item's ID is ``sha256:`` over the canonical JSON of its kind, its
subject and the per-side identities its facts carry: range content identities for entries, and each
member's ``{ id, revision }`` on each side for a family. Whole-file blobs, tree IDs and run IDs never
enter it, so rerunning over the same knowledge and the same changed ranges reproduces the ID, and a
mechanical blob change elsewhere in a file does not.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from agents_remember.kernel.canonical_json import prefixed_sha256_digest
from agents_remember.models.knowledge_files.history import (
    HistoryFile,
    HistoryRow,
    row_kind_for_subject,
)

__all__ = [
    "GUARANTEE_CHANGED",
    "ITEM_KINDS",
    "ItemKind",
    "RowLookup",
    "item_id",
    "kinds_document",
    "register_item_kind",
    "registered_kind",
    "satisfying_row",
    "subject_row",
]

RowLookup = Callable[[str, HistoryFile | None], HistoryRow | None]

GUARANTEE_CHANGED: Final = "guarantee-changed"
"""A ``reached_family`` item's ``reachedBy`` reason, beside ``record-changed``: K_C restates the
family's guarantee. The gate then holds the family's governing row to ``changed`` (MIK-R09 rule 2;
L37 ruling of 2026-10-02T01:04:49)."""


def subject_row(row_kind: str) -> RowLookup:
    """The common satisfying-row rule: the history row of ``row_kind`` whose subject is the item's."""

    def lookup(subject: str, history: HistoryFile | None) -> HistoryRow | None:
        if history is None:
            return None
        for row in history.rows:
            if row.subject == subject and row_kind_for_subject(row.subject).name == row_kind:
                return row
        return None

    return lookup


@dataclass(frozen=True)
class ItemKind:
    """One registered worklist item kind: subject key, facts, satisfying-row rule, owner packet."""

    name: str
    subject: str
    subject_pattern: str
    facts: tuple[str, ...]
    satisfying_row: str
    row_lookup: RowLookup = field(compare=False, repr=False)
    owner: str

    def accepts_subject(self, subject: str) -> bool:
        return re.match(self.subject_pattern, subject) is not None


ITEM_KINDS: Final[dict[str, ItemKind]] = {}


def register_item_kind(kind: ItemKind) -> ItemKind:
    """Register a kind; a second registration of the same name is refused, never overwritten."""

    if kind.name in ITEM_KINDS:
        raise ValueError(f"worklist item kind {kind.name!r} is already registered")
    ITEM_KINDS[kind.name] = kind
    return kind


def satisfying_row(kind: str, subject: str, history: HistoryFile | None) -> HistoryRow | None:
    """The history row the registered ``kind``'s rule finds for ``subject`` (currentness: R09)."""

    return ITEM_KINDS[kind].row_lookup(subject, history)


def item_id(kind: str, subject: str, identities: Any) -> str:
    """The stable item ID: ``sha256:`` over the kind, the subject and the per-side identities."""

    return prefixed_sha256_digest([kind, subject, identities])


_INVARIANT_ROW: Final = "the leaf's invariant row about the invariant (MIK-R07 rule 2)"
register_item_kind(
    ItemKind(
        name="touched_invariant",
        subject="invariant ID",
        subject_pattern=r"^INV-",
        facts=("entries", "added", "retired", "reanchored", "record", "context"),
        satisfying_row=_INVARIANT_ROW,
        row_lookup=subject_row("invariant"),
        owner="MIK-R08",
    )
)
register_item_kind(
    ItemKind(
        name="stale_invariant",
        subject="invariant ID",
        subject_pattern=r"^INV-",
        facts=("entries",),
        satisfying_row=_INVARIANT_ROW,
        row_lookup=subject_row("invariant"),
        owner="MIK-R08",
    )
)
register_item_kind(
    ItemKind(
        name="reached_family",
        subject="family ID",
        subject_pattern=r"^FAM-",
        facts=("members", "routes", "reachedBy"),
        satisfying_row="the leaf's family row about the family (MIK-R07 rule 5)",
        row_lookup=subject_row("family"),
        owner="MIK-R08",
    )
)


def registered_kind(name: str) -> ItemKind:
    try:
        return ITEM_KINDS[name]
    except KeyError:
        raise ValueError(f"no worklist item kind {name!r} is registered") from None


def kinds_document() -> list[Mapping[str, Any]]:
    """The registry as data, for the worklist file and the checklist."""

    return [
        {
            "name": kind.name,
            "subject": kind.subject,
            "facts": list(kind.facts),
            "satisfyingRow": kind.satisfying_row,
            "owner": kind.owner,
        }
        for kind in sorted(ITEM_KINDS.values(), key=lambda one: one.name)
    ]
