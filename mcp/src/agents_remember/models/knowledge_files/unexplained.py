"""Unexplained changes: the item subjects, the ``no_invariant`` row subjects and the item predicate.

MIK-R10 registers two worklist kinds for a change the gate linkage (MIK-R08 definition 8) finds
intersecting no entry:

* ``unexplained_hunk`` -- one unlinked text hunk. Its subject is the path plus the ``sha256`` of the
  hunk's changed lines on each side, ``hunk:<path>@<base lines>..<candidate lines>``, each side
  ``sha256:<64 hex>`` or ``absent`` (a side with no changed line). The item ID is built from the
  subject and those two identities only, so an edit elsewhere in the file never reopens it
  (rule 6).
* ``unexplained_file`` -- one unlinked non-text change (binary, symlink, submodule, mode, empty
  file). Its subject is the path plus the C-side blob, ``file:<path>@<blob>`` (``@absent`` when C
  no longer holds the path), so a second, different change to the same path opens a new item.

**What satisfies an item** depends only on its path's coverage (rule 1), which its facts carry:

* **covered** (the path has a realization entry in K_B, or its governing onboarding route is
  ``migrated``): an ``attach`` or ``author`` puts an entry over the change, which links it, so the
  item is no longer raised (a delete-only hunk has no line at C, so no new entry can link it: it
  admits only ``no_invariant``). Otherwise the leaf's ``no_invariant`` row answers it, with subject
  ``hunk:<item id>`` or ``file:<path>@<blob>`` (:func:`row_subject`). An onboarding change never
  does.
* **uncovered**: the file's onboarding trace (MIK-R30) -- a counted change of its card, or the
  leaf's ``onboarding:<path>`` ``no_impact`` row. A ``no_invariant`` row does not.

:func:`unexplained_satisfied_by` is that rule over a stored item; the worklist computes its
``satisfiedBy`` with it and the closeout gate (MIK-R09) applies :func:`unexplained_item_open`, so
the two never disagree.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

from agents_remember.kernel.canonical_json import prefixed_sha256_digest

__all__ = [
    "ABSENT",
    "COUNTED_CHANGE",
    "COVERED",
    "FILE_ITEM_KIND",
    "HUNK_ITEM_KIND",
    "NO_INVARIANT",
    "ROW_SUBJECT_PATTERN",
    "UNCOVERED",
    "file_subject",
    "hunk_item_id",
    "hunk_subject",
    "parse_hunk_subject",
    "row_subject",
    "unexplained_item_open",
    "unexplained_satisfied_by",
]

HUNK_ITEM_KIND: Final = "unexplained_hunk"
FILE_ITEM_KIND: Final = "unexplained_file"
NO_INVARIANT: Final = "no_invariant"
ABSENT: Final = "absent"
COVERED: Final = "covered"
UNCOVERED: Final = "uncovered"
COUNTED_CHANGE: Final = "counted-change"
"""``satisfiedBy`` of an uncovered item whose card carries a counted onboarding change."""

_IDENTITY: Final = r"(?:sha256:[0-9a-f]{64}|absent)"
_BLOB: Final = r"(?:[0-9a-f]{40}|[0-9a-f]{64}|absent)"
_HUNK_SUBJECT: Final = re.compile(
    rf"^hunk:(?P<path>\S(?:.*\S)?)@(?P<base>{_IDENTITY})\.\.(?P<candidate>{_IDENTITY})$"
)
HUNK_SUBJECT_PATTERN: Final = _HUNK_SUBJECT.pattern
FILE_SUBJECT_PATTERN: Final = rf"^file:\S(?:.*\S)?@{_BLOB}$"
# A ``no_invariant`` row names a hunk item by its ID, and a file item by its subject.
ROW_SUBJECT_PATTERN: Final = rf"^(?:hunk:sha256:[0-9a-f]{{64}}|file:\S(?:.*\S)?@{_BLOB})$"


def hunk_subject(path: str, base: str, candidate: str) -> str:
    """``hunk:<path>@<base lines>..<candidate lines>``: the changed lines' identity on each side."""

    return f"hunk:{path}@{base}..{candidate}"


def parse_hunk_subject(subject: str) -> tuple[str, str, str] | None:
    """The ``(path, base identity, candidate identity)`` a hunk subject names, or ``None``."""

    matched = _HUNK_SUBJECT.match(subject)
    return None if matched is None else (matched["path"], matched["base"], matched["candidate"])


def hunk_item_id(subject: str) -> str:
    """The item ID of a hunk subject: the registry's formula over the kind, subject and both sides.

    The two identities are the subject's own, so the ID is a function of the subject alone and a
    ``no_invariant`` row that names the ID names exactly one subject.
    """

    parsed = parse_hunk_subject(subject)
    if parsed is None:
        raise ValueError(f"not an unexplained hunk subject: {subject!r}")
    return prefixed_sha256_digest([HUNK_ITEM_KIND, subject, [parsed[1], parsed[2]]])


def file_subject(path: str, blob: str | None) -> str:
    """``file:<path>@<C-side blob>``, ``@absent`` when C no longer holds the path."""

    return f"file:{path}@{blob or ABSENT}"


def row_subject(item: Mapping[str, Any]) -> str | None:
    """The subject of the ``no_invariant`` row that answers ``item``."""

    kind, subject = item.get("kind"), item.get("subject")
    if kind == HUNK_ITEM_KIND and isinstance(item.get("id"), str):
        return f"hunk:{item['id']}"
    if kind == FILE_ITEM_KIND and isinstance(subject, str):
        return subject
    return None


def unexplained_satisfied_by(
    item: Mapping[str, Any], rows_by_subject: Mapping[str, str]
) -> str | None:
    """What satisfies a stored ``unexplained_*`` item, or ``None`` while it is open.

    ``rows_by_subject`` maps each history-row subject of the leaf to its row ID. A covered item is
    answered only by its ``no_invariant`` row; an uncovered item only by the file's onboarding trace:
    a counted change (``facts.onboardingTrace.countedChange``) or the ``onboarding:<path>`` row. An
    unreadable card sidecar never satisfies a trace (MIK-R30), and an item whose coverage is not
    stated is open.
    """

    facts = item.get("facts")
    if not isinstance(facts, Mapping):
        return None
    coverage = facts.get("coverage")
    state = coverage.get("state") if isinstance(coverage, Mapping) else None
    if state == COVERED:
        subject = row_subject(item)
        return None if subject is None else rows_by_subject.get(subject)
    if state != UNCOVERED:
        return None
    trace = facts.get("onboardingTrace")
    if not isinstance(trace, Mapping) or trace.get("sidecarUnreadable"):
        return None
    if trace.get("countedChange") is True:
        return COUNTED_CHANGE
    subject = trace.get("subject")
    return rows_by_subject.get(subject) if isinstance(subject, str) else None


def unexplained_item_open(item: Mapping[str, Any], rows_by_subject: Mapping[str, str]) -> bool:
    """The MIK-R10 satisfying rule over a stored item, for the gate (MIK-R09)."""

    return unexplained_satisfied_by(item, rows_by_subject) is None
