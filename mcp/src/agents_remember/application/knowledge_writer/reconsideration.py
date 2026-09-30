"""The writer's reconsideration rows (MIK-R14 rule 4): ``still_rejected`` and ``raise``.

A row with subject ``reconsider:<DEC-ID>#<alternative index>`` answers a
``reconsideration_candidate`` item. The subject must name a decision of the memory tree and one of
its ``rejected`` or ``deferred`` alternatives that a ``reconsider_on`` link addresses.

* ``still_rejected`` -- the rejection holds; the row's ``reason`` says why. Nothing else changes.
* ``raise`` -- the alternative goes to the developer:

  - the decision record's ``status`` becomes ``under_reconsideration`` in K_C (``status`` carries
    no meaning, so the revision stays);
  - a question is appended to the leaf's task-document ``openQuestions`` through the task owner
    (``task_doc``), preserving every existing question. It is a ``NORMATIVE_INTENT`` change, so it
    goes to the developer.

  The question is checked when the row is authored (a dry run of the task-document edit) and
  appended in a committing run before any knowledge file is written. When it cannot be written --
  no task owner, no resolvable leaf document, or a refused edit -- the ``raise`` is refused, nothing
  is written, and the item stays open (Failure And Recovery).

**Refresh on ``still_rejected`` (rulings Q2/Q3 and review R1 F1-F4).** A ``still_rejected``
answer refreshes the links whose trigger fired on the answered item (its ``facts.changed``), to the
state the curator judged (:func:`refreshed_links`):

* ``requirement_version`` -- the endpoint is re-pointed to the item's ``latestApproved`` version and
  ``latestPacket`` packet, never to a fresh manifest read;
* ``anchor`` and ``anchor_stale`` -- the target is re-anchored at C through the carry's own mapping
  (:func:`.carry.mapped_anchor`): a ``line_range`` is mapped through the zero-context diff from its
  recorded blob, a symbol is re-bound.

Each fired link in K_C is judged against the K_B target the item fired on (:func:`_link_state`):

* **unchanged** -- it is refreshed as above: a new judgment, so the decision is placed through the
  writer's record path and its revision goes up once in the leaf (ruling F2);
* **carried** -- a link refreshed earlier in the leaf that still stands for what was judged: a
  requirement at the item's version and packet, or an anchor whose content survives the mapping to
  the current C (the leaf changed the code elsewhere, review R4-1). Its refresh is written again,
  with no further revision bump -- for a plain rerun, the same bytes (review R3);
* **a new change** -- the judged target changed again after the refresh: a requirement whose
  approved version is newer than the one the link was refreshed to (review R4-3), or an anchor
  whose content changed again inside its range (review R5-1). A plain rerun is refused, naming the
  change and the new item; a row that answers the new item by naming its ID in ``items`` is a new
  judgment, and the link is refreshed to it;
* **a stale item** -- an anchor item computed before the anchored code last changed (its content at
  C is not the current one): refused, the worklist must be recomputed, so no change is absorbed
  unjudged;
* anything else was **re-authored** in the leaf and refuses the row, naming the link.

A link that cannot be mapped, or a fired link no longer at its index, refuses the row too. A
``raise`` leaves the links as they are: the reconsidered revision re-authors them.

The curator never reverses a decision: neither row changes an alternative, its status or the chosen
option. Decision authority stays with the developer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol

from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.application.knowledge_writer.carry import mapped_anchor
from agents_remember.memory.knowledge.requirement_endpoint import version_number
from agents_remember.models.knowledge_files.decisions import RECONSIDER_ON, RECONSIDERABLE
from agents_remember.models.knowledge_files.reconsideration import (
    REFRESHED_TRIGGERS,
    REQUIREMENT_VERSION,
    link_target_key,
    parse_reconsider_subject,
    question_key,
)

__all__ = [
    "UNDER_RECONSIDERATION",
    "Answer",
    "CodeAtC",
    "OpenQuestions",
    "Refresh",
    "append_questions",
    "raise_question",
    "reconsidered_alternative",
    "refreshed_links",
]

UNDER_RECONSIDERATION: Final = "under_reconsideration"


class OpenQuestions(Protocol):
    """The task owner's ``openQuestions`` of the leaf's task document.

    Both calls take the question's identifying ``key`` (:func:`question_key`) and its full text and
    return ``None`` on success or why not. A question whose key is already present is not appended
    again.
    """

    def check(self, key: str, question: str) -> str | None: ...

    def append(self, key: str, question: str) -> str | None: ...


def reconsidered_alternative(
    subject: str, record: tuple[str, str, dict[str, Any]] | None
) -> tuple[int, Mapping[str, Any]] | str:
    """The alternative a reconsideration row's subject names, or why it names none.

    ``record`` is the memory tree's ``(path, kind, document)`` for the subject's decision.
    """

    parsed = parse_reconsider_subject(subject)
    if parsed is None:
        return f"{subject!r} is not reconsider:<DEC-ID>#<alternative index>"
    decision, index = parsed
    if record is None or record[1] != "decision":
        return f"{decision} names no decision record (unknown subject)"
    document = record[2]
    alternative = _alternative(document, index)
    if isinstance(alternative, str):
        return f"{decision} {alternative}"
    if not any(_addresses(link, index) for link in _list(document.get("links"))):
        return f"no reconsider_on link of {decision} addresses alternative {index}"
    return index, alternative


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _alternative(document: Mapping[str, Any], index: int) -> Mapping[str, Any] | str:
    """The rejected or deferred alternative at ``index``, or why there is none."""

    alternatives = _list(document.get("alternatives"))
    if index >= len(alternatives) or not isinstance(alternatives[index], Mapping):
        return f"has no alternative {index}"
    alternative = alternatives[index]
    if alternative.get("status") not in RECONSIDERABLE:
        return (
            f"alternative {index} is {alternative.get('status')}; only a rejected or deferred "
            "alternative is reconsidered"
        )
    return alternative


def _addresses(link: Any, index: int) -> bool:
    """Whether ``link`` is a ``reconsider_on`` link of alternative ``index``."""

    return (
        isinstance(link, Mapping)
        and link.get("relation") == RECONSIDER_ON
        and link.get("alternative") == index
    )


def raise_question(
    subject: str,
    alternative: Mapping[str, Any],
    *,
    reason: str,
    leaf: str,
    row_id: str,
) -> tuple[str, str]:
    """The ``raise`` row's question for the developer: its key and its text."""

    key = question_key(subject, leaf)
    decision, index = parse_reconsider_subject(subject) or ("", 0)
    rejection = str(alternative.get("reason") or "").rstrip(".")
    condition = str(alternative.get("reconsider_when") or "").rstrip(".")
    text = (
        f"{key} Reconsider {alternative.get('option')!r}, the {alternative.get('status')} "
        f"alternative {index} of {decision}? The curator raised it: {reason} "
        f"(history row {row_id}; its rejection said: {rejection}; reconsider when: {condition}). "
        "The decision is under_reconsideration until the developer decides."
    )
    return key, text


def append_questions(
    questions: OpenQuestions | None, raised: list[tuple[str, str]]
) -> tuple[list[str], list[str]]:
    """Append each ``raise`` question in a committing run: ``(appended, refusals)``.

    Called before any knowledge file is written; a refusal refuses the whole operation.
    """

    appended: list[str] = []
    refusals: list[str] = []
    for key, question in raised:
        refusal = (
            "no task owner to append the question to"
            if questions is None
            else questions.append(key, question)
        )
        if refusal is None:
            appended.append(key)
        else:
            refusals.append(f"{key}: {refusal}")
    return appended, refusals


@dataclass(frozen=True)
class CodeAtC:
    """The code candidate C a refresh maps anchors to: its trees and each path's blob."""

    trees: CodeTrees
    blobs: Mapping[str, str]


# How a fired K_C link stands against the K_B target the item fired on (reviews R3 to R5).
UNCHANGED: Final = "unchanged"
CARRIED: Final = "carried"
EARLIER: Final = "earlier"
CHANGED: Final = "changed"
STALE_ITEM: Final = "stale item"
RE_AUTHORED: Final = "re-authored"
NEW_CHANGES: Final = frozenset({EARLIER, CHANGED})


@dataclass(frozen=True)
class Answer:
    """What a ``still_rejected`` row answers.

    ``fired`` is the item's ``facts.changed``; ``base_document`` the decision as the memory base
    records it (``MemoryState.base_record``); ``item`` the item's ID, and ``names_item`` whether the
    row names it in ``items`` (an explicit answer to that item).
    """

    fired: tuple[Mapping[str, Any], ...]
    base_document: Mapping[str, Any] | None = None
    item: str | None = None
    names_item: bool = False


@dataclass(frozen=True)
class Refresh:
    """The decision's links after the refresh, the refusals, and whether a link was newly judged.

    ``judged`` is true when a link was refreshed to a new judgment (the decision is placed and its
    revision goes up once in the leaf); a link only carried to the current C is not.
    """

    links: list[Any]
    problems: list[str]
    judged: bool


def refreshed_links(
    document: Mapping[str, Any], index: int, answer: Answer, code: CodeAtC
) -> Refresh:
    """The decision's links with the fired links of alternative ``index`` refreshed.

    Only the links whose trigger fired are refreshed (review ruling F1). Each is judged against the
    K_B target the item fired on (:func:`_link_state`); the problems that refuse the refresh are
    named ``links.<i>: why``.
    """

    links = list(_list(document.get("links")))
    base_links = list(_list((answer.base_document or {}).get("links")))
    problems: list[str] = []
    judged = False
    for one in answer.fired:
        if one.get("trigger") not in REFRESHED_TRIGGERS:
            continue
        position = one.get("link")
        link = _at(links, position)
        if not isinstance(position, int) or link is None or link.get("alternative") != index:
            problems.append(
                f"links.{position}: the fired link is no longer a reconsider_on link of "
                f"alternative {index}; recompute the worklist"
            )
            continue
        known = _fired_target(_at(base_links, position), one)
        outcome = _outcome(link.get("target"), known, one, answer, code)
        if isinstance(outcome, str):
            problems.append(f"links.{position}: {outcome}")
        else:
            links[position] = {**link, "target": outcome[0]}
            judged = judged or outcome[1]
    return Refresh(links, problems, judged)


def _outcome(
    current: Any,
    known: Mapping[str, Any] | None,
    fired: Mapping[str, Any],
    answer: Answer,
    code: CodeAtC,
) -> tuple[dict[str, Any], bool] | str:
    """One fired link: ``(target, newly judged)`` to write, or a refusal."""

    state, detail = _link_state(current, known, fired, code)
    if state == CARRIED:
        return detail, False  # an earlier refresh that still stands: no further bump
    if state == RE_AUTHORED:
        return (
            f"the link was re-authored since the worklist was computed (it no longer names "
            f"{fired.get('target')}); recompute the worklist"
        )
    if state == STALE_ITEM:
        return (
            f"the code this link anchors changed after the worklist was computed (the item saw "
            f"{detail[0]}, the code now holds {detail[1]}); recompute the worklist and answer "
            "its item"
        )
    if state in NEW_CHANGES and not answer.names_item:
        return _new_change(state, detail, fired, answer)
    target = _refreshed_target(current, fired, code)
    return target if isinstance(target, str) else (target, True)


def _new_change(state: str, detail: Any, fired: Mapping[str, Any], answer: Answer) -> str:
    """The refusal of a plain rerun when the judged target changed again (R4-3, R5-1)."""

    if state == EARLIER:
        latest = fired.get("latestApproved")
        change = (
            f"this link was refreshed to {detail} earlier in the leaf, and {latest} has been "
            f"approved since, so the item now asks about {latest}: judge {latest}"
        )
    else:
        change = (
            f"the code this link anchors changed again after it was refreshed earlier in the "
            f"leaf (its content was {detail[0]}, it is now {detail[1]}), so the item now asks "
            "about that change: judge it"
        )
    return f"{change} and answer the new item by naming {answer.item} in the row's items"


def _fired_target(
    base_link: Mapping[str, Any] | None, fired: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    """The whole K_B target the item fired on, or ``None`` when only its key is known.

    The base record's link at the fired position when it still names the fired target (the memory
    worktree's ``HEAD``, which is K_B until the leaf commits memory); otherwise, for an anchor, the
    K_B anchor the facts carry (``facts.changed[].entry.anchor``). A requirement whose base link was
    already committed refreshed is known by its key alone.
    """

    target = base_link.get("target") if base_link is not None else None
    if isinstance(target, Mapping) and link_target_key(target) == fired.get("target"):
        return target
    entry = fired.get("entry")
    anchor = entry.get("anchor") if isinstance(entry, Mapping) else None
    if isinstance(anchor, Mapping) and link_target_key(anchor) == fired.get("target"):
        return anchor
    return None


def _link_state(
    current: Any, known: Mapping[str, Any] | None, fired: Mapping[str, Any], code: CodeAtC
) -> tuple[str, Any]:
    """Where the K_C link stands against the fired K_B target, with what that state carries.

    The refresh of the K_B target is the one :func:`_refreshed_target` writes: a requirement at the
    item's ``latestApproved`` and ``latestPacket``, an anchor mapped to C. Only exact equality
    counts; a link that merely shares the version, the packet or the path was re-authored.
    """

    if not isinstance(current, Mapping):
        return RE_AUTHORED, None
    if fired.get("trigger") == REQUIREMENT_VERSION:
        return _requirement_state(current, known, fired)
    return _anchor_state(current, known, fired, code)


def _requirement_state(
    current: Mapping[str, Any], known: Mapping[str, Any] | None, fired: Mapping[str, Any]
) -> tuple[str, Any]:
    """A requirement: unchanged, carried, refreshed to an earlier version, or re-authored.

    ``same`` holds when the link names the fired endpoint apart from its version and packet: by the
    whole K_B target when it is known, by the fired key otherwise. An earlier refresh must be to a
    version between the fired one and the newly approved one (review R5-2).
    """

    version, latest = current.get("version"), fired.get("latestApproved")
    packet = current.get("packet") == fired.get("latestPacket")
    if known is None:
        if link_target_key(current) == fired.get("target"):
            return UNCHANGED, None
        same = link_target_key({**current, "version": fired.get("version")}) == fired.get("target")
    else:
        if dict(current) == dict(known):
            return UNCHANGED, None
        endpoint = {**current, "version": known.get("version"), "packet": known.get("packet")}
        same = endpoint == dict(known)
    if same and version == latest and packet:
        return CARRIED, dict(current)
    if same and _newer(version, fired.get("version")) and _newer(latest, version):
        return EARLIER, version
    return RE_AUTHORED, None


def _newer(version: Any, than: Any) -> bool:
    mine = version_number(version) if isinstance(version, str) else None
    theirs = version_number(than) if isinstance(than, str) else None
    return mine is not None and theirs is not None and mine > theirs


def _anchor_state(
    current: Mapping[str, Any],
    known: Mapping[str, Any] | None,
    fired: Mapping[str, Any],
    code: CodeAtC,
) -> tuple[str, Any]:
    """An anchor: unchanged, carried, changed again, a stale item, or re-authored.

    The refresh of the K_B target is its mapping to the current C. The item is current only when
    the content it saw at C (``facts.changed[].entry.candidate.content``) is that refresh's content;
    otherwise the code changed after the worklist was computed and nothing may absorb that change.
    """

    if known is None:
        return RE_AUTHORED, None
    refresh = _refreshed_target(known, fired, code)
    seen = _seen_content(fired)
    if not isinstance(refresh, str) and seen is not None and seen != refresh["content"]:
        return STALE_ITEM, (seen, refresh["content"])
    if dict(current) == dict(known):
        return UNCHANGED, None
    if isinstance(refresh, str):
        return RE_AUTHORED, None
    return _refreshed_anchor_state(current, refresh, fired, code)


def _refreshed_anchor_state(
    current: Mapping[str, Any], refresh: dict[str, Any], fired: Mapping[str, Any], code: CodeAtC
) -> tuple[str, Any]:
    """A link refreshed earlier in the leaf: carried when its content survives the mapping to C."""

    carried = _refreshed_target(current, fired, code)
    if carried != refresh:
        return RE_AUTHORED, None
    if refresh["content"] == current.get("content"):
        return CARRIED, refresh
    return CHANGED, (current.get("content"), refresh["content"])


def _seen_content(fired: Mapping[str, Any]) -> Any:
    """The anchored content the item saw at C, when its facts record it."""

    entry = fired.get("entry")
    seen = entry.get("candidate") if isinstance(entry, Mapping) else None
    return seen.get("content") if isinstance(seen, Mapping) else None


def _at(links: list[Any], position: Any) -> Mapping[str, Any] | None:
    """The link at ``position`` when it is a ``reconsider_on`` link of any alternative."""

    if not isinstance(position, int) or not 0 <= position < len(links):
        return None
    link = links[position]
    return link if isinstance(link, Mapping) and link.get("relation") == RECONSIDER_ON else None


def _refreshed_target(target: Any, fired: Mapping[str, Any], code: CodeAtC) -> dict[str, Any] | str:
    """An anchor mapped to C, or a requirement re-pointed to the item's approved version."""

    if not isinstance(target, Mapping):
        return "the fired link has no anchor or requirement target"
    if fired.get("trigger") == REQUIREMENT_VERSION:
        version, packet = fired.get("latestApproved"), fired.get("latestPacket")
        if not isinstance(version, str) or not isinstance(packet, str):
            return (
                "the item names no approved version and packet to re-point the link to "
                "(the manifest entry names no file)"
            )
        return {**target, "packet": packet, "version": version}
    path = target.get("path")
    mapped = (
        mapped_anchor(code.trees, path, dict(target), code.blobs.get(path))
        if isinstance(path, str)
        else None
    )
    if mapped is None:
        return (
            f"the linked anchor {path} cannot be mapped to C (its range has no image, or it no "
            "longer resolves); re-author the link target in 'records' before answering "
            "still_rejected"
        )
    return mapped
