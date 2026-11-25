"""The short answer of a brief, as ``read_ar_files`` carries it.

For one path the short answer names the brief and whether it is current, and carries each of the
file's own rules with its text, the open weaknesses with their sentence, the documents that stand
on the file, each family by title with its counts, and the retired records it left out. What it
leaves out stands in the brief, which the agent reads with its own file tool: there is no page and
no second call.

**The bound.** The tool fits whole answers in the order asked, reserving each later path's counts
and brief path. A single oversized brief keeps its rules' texts in the brief's order, moved rules
first, until the bound, and the rest by ID and title. When even the minimum does not fit, the
batch uses compact rows.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from agents_remember.application.file_briefs.model import FileBrief, RuleBlock
from agents_remember.application.file_briefs.ordering import RulePlaces, moved_first

IN_THE_FILE: Final = (
    "Read the current brief with your own file tool for each rule's conditions and exclusions, "
    "where else it is realized, the tests that prove it, the decisions that formed it, each "
    "family's guarantee and its other rules with their text."
)
LEFT_OUT: Final = "Read the current brief with your own file tool for the rule texts that did not fit this answer."
RULE_LEFT_OUT: Final = "text in the brief"
NOTHING_RECORDED: Final = (
    "No rule, weakness, decision or standing document is recorded at this file, so it has no brief."
)
# A list longer than this is cut by the product's answer shaping; every list here stays within it.
LIST_CAP: Final = 20


@dataclass(frozen=True)
class BriefLocation:
    """Where the brief lies as the caller opens it, and whether it holds the tree measured now."""

    path: str
    current: bool
    freshness: str | None = None


def whole(brief: FileBrief, location: BriefLocation) -> dict[str, Any]:
    """The short answer of one path with every rule's text."""

    rules = _ordered(brief.rules)
    answer = _head(brief, location)
    answer["rules"] = [_rule(one) for one in rules[:LIST_CAP]]
    if len(rules) > LIST_CAP:
        answer["rulesNotListed"] = len(rules) - LIST_CAP
    answer["weaknesses"] = [
        {"id": one.id, "at": one.symbol, "text": one.statement}
        for one in _weaknesses(brief)[:LIST_CAP]
    ]
    answer["standsOnIt"] = [one.document for one in brief.documents[:LIST_CAP]]
    answer["families"] = [
        {"title": one.title, "here": len(one.here), "elsewhere": len(one.others)}
        for one in brief.families[:LIST_CAP]
    ]
    answer["retiredLeftOut"] = brief.retired_left_out
    answer["inTheFile"] = IN_THE_FILE
    return answer


def counts_only(brief: FileBrief, location: BriefLocation) -> dict[str, Any]:
    """A path past the bound: its brief's path and its counts in one line."""

    counts = brief.head.counts
    return {
        **_location_fields(location),
        "counts": (
            f"{counts.rules} rules, {counts.weaknesses} weaknesses, {counts.documents} documents, "
            f"{counts.families} families, {counts.retired} retired"
        ),
    }


def without_rule_texts(answer: Mapping[str, Any], keep: int) -> dict[str, Any]:
    """``answer`` with the first ``keep`` rules' texts and the rest by ID and title only."""

    trimmed = dict(answer)
    trimmed["rules"] = [
        dict(one) if position < keep else _named_only(one)
        for position, one in enumerate(answer.get("rules") or ())
    ]
    trimmed["weaknesses"] = [] if keep == 0 else answer.get("weaknesses", [])
    if keep < len(answer.get("rules") or ()):
        trimmed["leftOut"] = LEFT_OUT
    return trimmed


@dataclass(frozen=True)
class Candidate:
    """One answered row's two forms, and the field of the row they go into."""

    row: dict[str, Any]
    whole: dict[str, Any]
    counts: dict[str, Any]


def counts_notice(count: int) -> str:
    paths = "1 path" if count == 1 else f"{count} paths"
    return (
        f"The briefs of {paths} did not fit this answer whole, so their rows carry "
        "the counts and the brief's path. Read each current brief with your own file tool."
    )


def fit(
    candidates: Sequence[Candidate],
    *,
    fits: Callable[[], bool],
    field: str = "brief",
) -> int | None:
    """Fit the briefs of a batch inside the bound, in the order asked.

    Reserve the counts and path of every brief before adding rule texts in request order.
    Return the number of rows with counts only, or ``None`` when the minimum exceeds the bound.
    """

    for candidate in candidates:
        _set(candidate, field, candidate.counts)
    if not fits():
        return None
    whole_rows = 0
    for position, candidate in enumerate(candidates):
        _set(candidate, field, candidate.whole)
        if fits():
            whole_rows += 1
            continue
        if position == 0 and _keep_what_fits(candidate, field, fits):
            whole_rows += 1
        else:
            _set(candidate, field, candidate.counts)
        break
    return len(candidates) - whole_rows


def _keep_what_fits(candidate: Candidate, field: str, fits: Callable[[], bool]) -> bool:
    """Keep as many rules' texts as fit in the first row; ``False`` when not even one does."""

    rules = candidate.whole.get("rules") or []
    for keep in range(len(rules), 0, -1):
        _set(candidate, field, without_rule_texts(candidate.whole, keep))
        if fits():
            return True
    _set(candidate, field, candidate.counts)
    return False


def _set(candidate: Candidate, field: str, value: dict[str, Any]) -> None:
    candidate.row[field] = value


def _head(brief: FileBrief, location: BriefLocation) -> dict[str, Any]:
    return {**_location_fields(location), "memoryTree": brief.head.memory_tree[:12]}


def _location_fields(location: BriefLocation) -> dict[str, Any]:
    fields: dict[str, Any] = {"path": location.path, "current": location.current}
    if location.freshness is not None:
        fields["freshness"] = location.freshness
    return fields


def _ordered(rules: Sequence[RuleBlock]) -> list[RuleBlock]:
    by_id = {one.head.id: one for one in rules}
    places = [RulePlaces(head=one.head, symbols=tuple(p.symbol for p in one.here)) for one in rules]
    return [by_id[one.head.id] for one in moved_first(places)]


def _rule(rule: RuleBlock) -> dict[str, Any]:
    head = rule.head
    row: dict[str, Any] = {
        "id": head.id,
        "title": head.title.text,
        "state": head.standing.state,
        "writer": head.standing.writer,
        "proof": head.proofs,
        "anchors": (
            f"{head.anchors_current} of {head.anchors_total} current"
            if head.anchors_measured
            else f"{head.anchors_total} not measured"
        ),
        "at": sorted({one.symbol for one in rule.here}),
        "rule": rule.statement,
    }
    if not head.title.given:
        row["titleRecorded"] = False
    if head.moved:
        row["moved"] = True
    return row


def _named_only(rule: Mapping[str, Any]) -> dict[str, Any]:
    named = {key: rule[key] for key in ("id", "title", "moved") if key in rule}
    named["rule"] = RULE_LEFT_OUT
    return named


def _weaknesses(brief: FileBrief) -> list[Any]:
    seen: dict[str, Any] = {}
    for rule in brief.rules:
        for one in rule.weaknesses:
            seen.setdefault(one.id, one)
    for one in brief.loose_weaknesses:
        seen.setdefault(one.id, one)
    return [seen[key] for key in sorted(seen)]


__all__ = [
    "IN_THE_FILE",
    "LEFT_OUT",
    "LIST_CAP",
    "NOTHING_RECORDED",
    "BriefLocation",
    "Candidate",
    "counts_notice",
    "counts_only",
    "fit",
    "whole",
    "without_rule_texts",
]
