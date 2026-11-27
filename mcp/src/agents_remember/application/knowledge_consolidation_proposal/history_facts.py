"""What the history files of one tree say for a proposal: a master's writes and past passes.

Two facts live only in a history file's own fields, which the index does not hold: ``written``
(the revisions an owner wrote) and ``review`` (the memory review that passed its change, with the
agent that handed the change over). Each is read here from the files that can hold it, each file
parsed once through the history model:

* **a master's writes** -- the files of the master's own leaves, named ``<master>-…`` like their
  owners. A rule counts as written by the master when a closed file of such a leaf names one of its
  revisions in ``written``, or holds a row about it that wrote it or changed its card entries
  (:data:`CARD_OR_TEXT_DISPOSITIONS`), or when the rule's record names such a leaf as its origin. A
  rule that the leaves only read (``no_impact`` and every other disposition) is not counted;
* **past consolidation passes** -- a closed file whose review fact was handed over by a
  consolidator. Its pass reached a family when it wrote a revision of the family or holds a family
  row about it other than ``unresolved``. The pass's time is the time the reviewer recorded the
  passed review (the reviewer's stamp ``at``).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from agents_remember.models.knowledge_files.documents import (
    KNOWLEDGE_ROOT,
    history_document,
    parse_document_text,
)
from agents_remember.models.knowledge_files.history import FamilyRow, HistoryFile, InvariantRow
from agents_remember.models.knowledge_files.ids import RECORD_PREFIXES
from agents_remember.models.knowledge_files.record_state import CONSOLIDATOR_ROLE, UNRESOLVED

from .refusal import ProposalRefusal

HISTORY_DIRECTORY: Final = f"{KNOWLEDGE_ROOT}/history"
# The invariant rows that wrote the rule's text (``changed``, ``deleted``) or its card entries
# (``moved`` re-anchors, ``extended`` adds an entry); ``no_impact`` and the consolidator's own
# ``stands`` and ``unresolved`` only read the rule.
CARD_OR_TEXT_DISPOSITIONS: Final = frozenset({"changed", "deleted", "moved", "extended"})
# A master id is the prefix its leaves' owner ids share (``261112-BUG`` of ``261112-BUG-L7``).
MASTER_ID_PATTERN: Final = re.compile(r"^[0-9]{6}-[A-Za-z0-9]+$")
_RULE_PREFIX: Final = f"{RECORD_PREFIXES['invariant']}-"
_FAMILY_PREFIX: Final = f"{RECORD_PREFIXES['family']}-"
# A consolidator's review fact spells the role as this JSON string; a file whose bytes lack it
# cannot hold one, so only the files that contain it are parsed (the model then decides).
_CONSOLIDATOR_MARK: Final = f'"{CONSOLIDATOR_ROLE}"'.encode()


@dataclass
class MasterWrites:
    """The rules a master's closed leaf files wrote, with the files that were read."""

    rules: set[str] = field(default_factory=set)
    closed_files: int = 0
    open_files: int = 0


def master_writes(memory_root: Path, master_id: str, rule_origins: dict[str, str]) -> MasterWrites:
    """The rules the leaves of ``master_id`` wrote or whose card entries they changed.

    Raises :class:`ProposalRefusal` when the id is not a master id, when no leaf file of the master
    is in the tree, or when one of its files cannot be read.
    """

    if MASTER_ID_PATTERN.match(master_id) is None:
        raise ProposalRefusal(
            "bad_master",
            f"{master_id!r} is not a master id; name the prefix its leaves share, such as "
            "261112-BUG for the leaves 261112-BUG-L<n>",
        )
    prefix = f"{master_id}-"
    writes = MasterWrites()
    for history in _files(memory_root, f"{master_id}-*.json"):
        if history.leaf is None or not history.leaf.startswith(prefix):
            continue
        if not history.closed:
            writes.open_files += 1
            continue
        writes.closed_files += 1
        writes.rules |= _rules_written(history)
    if writes.closed_files + writes.open_files == 0:
        raise ProposalRefusal(
            "selector_absent",
            f"the tree holds no history file of a leaf of {master_id}; name a finished master by "
            "the prefix of its leaves' history files under knowledge/history",
        )
    writes.rules |= {rule for rule, leaf in rule_origins.items() if leaf.startswith(prefix)}
    return writes


def last_consolidations(memory_root: Path) -> dict[str, datetime]:
    """Each family's latest consolidation pass: the time its passed review was recorded."""

    latest: dict[str, datetime] = {}
    for history in _files(memory_root, "*.json", must_contain=_CONSOLIDATOR_MARK):
        review = history.review
        if not history.closed or review is None:
            continue
        if review.handed_over_by.role != CONSOLIDATOR_ROLE:
            continue
        at = _instant(review.reviewer.at)
        for family in _families_reached(history):
            if family not in latest or latest[family] < at:
                latest[family] = at
    return latest


def _rules_written(history: HistoryFile) -> set[str]:
    rules = {one.record for one in history.written or () if one.record.startswith(_RULE_PREFIX)}
    rules |= {
        row.subject
        for row in history.rows
        if isinstance(row, InvariantRow) and row.disposition in CARD_OR_TEXT_DISPOSITIONS
    }
    return rules


def _families_reached(history: HistoryFile) -> set[str]:
    families = {
        one.record for one in history.written or () if one.record.startswith(_FAMILY_PREFIX)
    }
    families |= {
        row.subject
        for row in history.rows
        if isinstance(row, FamilyRow) and row.disposition != UNRESOLVED
    }
    return families


def _instant(stamp_at: str) -> datetime:
    """A stamp's time; a time without an offset is read as UTC, an unreadable one as the oldest."""

    try:
        moment = datetime.fromisoformat(stamp_at)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _files(
    memory_root: Path, pattern: str, *, must_contain: bytes | None = None
) -> Iterator[HistoryFile]:
    directory = memory_root / HISTORY_DIRECTORY
    for path in sorted(directory.glob(pattern)):
        relative = f"{HISTORY_DIRECTORY}/{path.name}"
        try:
            data = path.read_bytes()
            if must_contain is not None and must_contain not in data:
                continue
            yield history_document(relative, parse_document_text(data.decode("utf-8")))
        except (OSError, ValueError) as error:
            raise ProposalRefusal(
                "unavailable",
                f"the history file {relative} cannot be read ({error}); repair it with "
                "knowledge_integrity_check and read again",
            ) from error
