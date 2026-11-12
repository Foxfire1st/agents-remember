"""A ``still_rejected`` row that re-points the fired links whose code a leaf removed (MIK-R194).

A leaf may delete the symbol a decision's ``reconsider_on`` link names. The link then has two
refusals that point at each other: left as it is, its base target cannot be mapped to C; re-authored
by hand, it is refused as re-authored. This module is the judged route between them (rules 4 to 7):

* the row carries ``repoint: [{link, path, locator}]``, one item per fired link, naming where the
  code that ``reconsider_when`` is about now lives at C;
* each item is admitted only for a fired link of the answered alternative, only where the fired base
  target really has no image at C, only where the leaf's current link is still the base target (or
  is already this same re-point, so a rerun writes the same bytes), and only where the new target
  resolves at C;
* the decision's revision goes up once in the leaf, and the stored row records ``before`` and
  ``after`` with their paths.

Nothing here derives a target, answers an item, or writes a rejection: a re-point exists only
because a curator wrote one in a hand-off, and a fired link without a row still reaches the curator's
worklist and blocks closeout (D430, MIK-R194 rule 8).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from pydantic import ValidationError

from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.application.knowledge_writer.authoring_rows import (
    HistoryRowAuthoring,
    _first_error,
)
from agents_remember.application.knowledge_writer.code_anchors import AnchorResolutionError
from agents_remember.application.knowledge_writer.handoff import RepointRequest, RowRequest
from agents_remember.application.knowledge_writer.memory_state import (
    canonical_equal,
    deep_copy,
)
from agents_remember.application.knowledge_writer.reconsideration import (
    ANCHOR,
    ANCHOR_STALE,
    REFRESH_NOT_CURRENT,
    Answer,
    CodeAtC,
    fired_target,
    link_at,
    reconsidered_alternative,
    refreshed_links,
    refreshed_target,
)
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.models.knowledge_files.history import ReconsiderationRow
from agents_remember.models.knowledge_files.reconsideration import link_target_key

__all__ = ["RepointContext", "RepointOutcome", "RepointRowAuthoring", "repointed_links"]

# The triggers a re-point answers: an anchor whose base target has no image at C, or an earlier
# refresh that no longer stands (MIK-R194 rule 4).
REPOINTABLE: Final = frozenset({ANCHOR, ANCHOR_STALE, REFRESH_NOT_CURRENT})
Resolver = Callable[[str, Mapping[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class RepointContext:
    """What a re-point is judged against: the answered item, C, and the snapshot that resolves at C.

    ``resolve`` resolves a path and a hand-off locator at C into an anchor document, and says why it
    cannot by raising :class:`AnchorResolutionError`.
    """

    answer: Answer
    code: CodeAtC
    resolve: Resolver


@dataclass(frozen=True)
class RepointOutcome:
    """The decision's links after the re-points, the refusals, and the row's stored items."""

    links: list[Any]
    problems: list[str]
    judged: bool
    items: list[dict[str, Any]]


def repointed_links(
    document: Mapping[str, Any],
    index: int,
    context: RepointContext,
    repoints: Sequence[RepointRequest],
) -> RepointOutcome:
    """The links with the named ones re-pointed and every other fired link refreshed as today.

    The problems read ``links.<i>: why``, as the refresh's own refusals do.
    """

    links = list(_list(document.get("links")))
    base_links = list(_list((context.answer.base_document or {}).get("links")))
    problems: list[str] = []
    items: list[dict[str, Any]] = []
    repointed: set[int] = set()
    for request in repoints:
        fired = _fired_at(context.answer.fired, request.link)
        link = link_at(links, request.link)
        refusal = _admission(link, fired, index, context.answer)
        if isinstance(refusal, str):
            problems.append(f"links.{request.link}: {refusal}")
            continue
        assert fired is not None and link is not None  # both are refused above
        known = _base_target(fired, link_at(base_links, request.link))
        item = _item(link, known, fired, request, context)
        if isinstance(item, str):
            problems.append(f"links.{request.link}: {item}")
            continue
        links[request.link] = {**link, "target": item["after"]}
        items.append(item)
        repointed.add(request.link)
    rest = Answer(
        fired=tuple(one for one in context.answer.fired if one.get("link") not in repointed),
        base_document=context.answer.base_document,
        item=context.answer.item,
        names_item=context.answer.names_item,
    )
    refresh = refreshed_links({**document, "links": links}, index, rest, context.code)
    return RepointOutcome(
        links=refresh.links,
        problems=[*problems, *refresh.problems],
        judged=bool(items) or refresh.judged,
        items=items,
    )


def _base_target(
    fired: Mapping[str, Any], base_link: Mapping[str, Any] | None
) -> Mapping[str, Any] | None:
    """The K_B target the item fired on: the base link's own, or the one an earlier refresh displaced."""

    known = fired_target(base_link, fired)
    if isinstance(known, Mapping) or not isinstance(fired.get("baseTarget"), Mapping):
        return known
    return fired["baseTarget"]


def _admission(
    link: Mapping[str, Any] | None,
    fired: Mapping[str, Any] | None,
    index: int,
    answer: Answer,
) -> str | None:
    """Why this re-point item cannot be judged at all, or ``None`` (rule 4's admission test)."""

    if fired is None:
        return (
            "repoint names no link that fired on this item; name a link of the answered "
            "alternative that the item's facts.changed holds"
        )
    if fired.get("trigger") not in REPOINTABLE:
        return (
            f"repoint answers an anchor or a refresh that no longer stands, and this link fired as "
            f"{fired.get('trigger')}; leave it to the refresh"
        )
    if link is None or link.get("alternative") != index:
        return (
            f"the fired link is no longer a reconsider_on link of alternative {index}; "
            "recompute the worklist"
        )
    if fired.get("trigger") == REFRESH_NOT_CURRENT and not answer.names_item:
        return (
            "the item fired because an earlier refresh no longer stands: name that item in the "
            "row's 'items', so the re-point answers it"
        )
    return None


def _item(
    link: Mapping[str, Any],
    known: Mapping[str, Any] | None,
    fired: Mapping[str, Any],
    request: RepointRequest,
    context: RepointContext,
) -> dict[str, Any] | str:
    """One re-point as the row stores it, or why it is refused (rules 4 to 7)."""

    if known is None or not isinstance(known.get("path"), str):
        return (
            "the fired base target is not known with its path, so the row cannot record where the "
            "link stood; `raise` instead"
        )
    after = _resolved(request, context.resolve)
    if isinstance(after, str):
        return after
    one = _Item(
        current=link.get("target"),
        known=known,
        fired=fired,
        already=_already(link.get("target"), request, after),
    )
    if fired.get("trigger") == REFRESH_NOT_CURRENT:
        refusal = _refresh_refusal(one)
    else:
        refusal = _anchor_refusal(one, context)
    if refusal is not None:
        return refusal
    return {"link": request.link, "before": dict(known), "after": after}


def _anchor_refusal(one: _Item, context: RepointContext) -> str | None:
    """Why an anchor-triggered item cannot be re-pointed, or ``None`` when it can."""

    if not isinstance(refreshed_target(one.known, one.fired, context.code), str):
        return (
            "the fired target maps to C, so this link is refreshed as today and keeps its target; "
            "remove its repoint"
        )
    if _same(one.current, one.known) or one.already:
        return None
    return (
        "the link was re-authored in the leaf, so the row cannot re-point it; restore its base "
        "target in `records`, or `raise`"
    )


def _refresh_refusal(one: _Item) -> str | None:
    """Why a ``refresh_not_current`` item cannot be answered here, or ``None`` when it can.

    The item fired because the link no longer holds the target an earlier refresh wrote, so only THAT
    target may be replaced -- or the identical re-point a rerun would write. Any other current target
    belongs to another answer, and answering over it would discard a judgment someone else made.
    """

    if _same(one.current, one.known):
        return (
            "the link still stands at its base target, so no earlier refresh of it was written; "
            "recompute the worklist"
        )
    if _same_target(one.current, _earlier_refresh(one.fired)) or one.already:
        return None
    return (
        "the link's current target is neither the earlier refresh this item fired on nor this "
        "re-point, so another answer stands between them; recompute the worklist, or `raise`"
    )


@dataclass(frozen=True)
class _Item:
    """One re-point being judged: the leaf's current target, the fired base target, and its facts."""

    current: Any
    known: Mapping[str, Any]
    fired: Mapping[str, Any]
    already: bool


def _same_target(current: Any, earlier: Any) -> bool:
    """Whether two targets name the same place on the same bytes, in any of their spellings.

    A stored link is a document (``path``, ``locator``, ``blob``, ``content``) and an item's fired fact
    is the shared target key (``path@blob#content``); ``link_target_key`` is the one spelling both sides
    go through, so rule 4d's continuity holds a target exactly and not by its locator alone.
    """

    if current is None or earlier is None:
        return False
    return link_target_key(current) == link_target_key(earlier)


def _earlier_refresh(fired: Mapping[str, Any]) -> Any:
    """The target the earlier refresh wrote, as the item recorded it; ``None`` when it recorded none.

    The item's ``entry`` is that anchor as the leaf stores it (``anchor``), so continuity is exact: a
    ``refresh_not_current`` item fires because the link no longer holds this target at C, and answering
    it may only replace THIS target. Any other current target belongs to another answer, and
    overwriting it would discard someone else's judged change (MIK-R194 rule 4d).
    """

    entry = fired.get("entry")
    anchor = entry.get("anchor") if isinstance(entry, Mapping) else None
    if anchor is not None:
        return anchor
    identity = fired.get("identity")
    if isinstance(identity, Sequence) and not isinstance(identity, str) and identity:
        return identity[0]
    return None


def _resolved(request: RepointRequest, resolve: Resolver) -> dict[str, Any] | str:
    """The new target at C, or why it does not resolve there (rule 4c)."""

    try:
        return {**resolve(request.path, request.locator.document), "path": request.path}
    except AnchorResolutionError as error:
        return f"the repoint's target does not resolve at C: {error}"


def _fired_at(fired: Sequence[Mapping[str, Any]], link: int) -> Mapping[str, Any] | None:
    """The item's fired fact about ``link``, when it has one."""

    return next((one for one in fired if one.get("link") == link), None)


def _same(current: Any, known: Mapping[str, Any] | None) -> bool:
    """Whether the leaf's link is ``known``, byte for byte (``False`` when either is absent)."""

    return (
        isinstance(current, Mapping) and isinstance(known, Mapping) and dict(current) == dict(known)
    )


def _already(current: Any, request: RepointRequest, after: Mapping[str, Any]) -> bool:
    """Whether the leaf's link already names this re-point's target, so a rerun changes nothing."""

    if not isinstance(current, Mapping):
        return False
    locator = current.get("locator")
    return (
        current.get("path") == request.path
        and isinstance(locator, Mapping)
        and dict(locator) == dict(request.locator.document)
        and current.get("content") == after.get("content")
    )


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


class RepointRowAuthoring(HistoryRowAuthoring):
    """The history-row authoring that admits a re-point on a ``still_rejected`` row.

    Only ``_reconsideration_row`` is overridden: with no ``repoint`` the inherited row is written
    exactly as today, and with one the inherited ``_common_row``, ``_answered_item`` and
    ``_place_record`` do the rest of the work. The row still answers the item, the decision's
    revision still goes up once in the leaf, and the curator's judgment is still the only source of
    a target (MIK-R194 rules 4, 5 and 8).
    """

    def _reconsideration_row(
        self, request: RowRequest, existing: Mapping[str, Any] | None, where: str
    ) -> dict[str, Any] | None:
        """A reconsideration row, with the fired links the curator re-pointed (MIK-R194 rule 4)."""

        if not request.repoint:
            row = super()._reconsideration_row(request, existing, where)
            if row is not None:
                self._note_repoint_route(row)
            return row
        if request.disposition != "still_rejected":
            self.problem(
                where,
                "a raise carries no 'repoint': a raise leaves the links as they are and asks the "
                "developer, so say where the code lives in the row's reason",
            )
            return None
        row = self._common_row(request, existing, where)
        if row is None:
            return None
        outcome = self._judge_repoints(request, row, where)
        if outcome is None:
            return None
        return self._stored_repoint_row(row, outcome, where)

    def _judge_repoints(
        self, request: RowRequest, row: Mapping[str, Any], where: str
    ) -> RepointOutcome | None:
        """Judge and place the re-points, or ``None`` after naming every refusal."""

        decision = request.subject.removeprefix("reconsider:").split("#", 1)[0]
        found = self.state.record(decision)
        named = reconsidered_alternative(request.subject, found)
        if isinstance(named, str):
            self.problem(where, named)
            return None
        if found is None:
            return None
        item = self._answered_item(row, where)
        if item is None:
            return None
        _path, _kind, document = found
        context = RepointContext(
            self._context_answer(item, row, document), self._code(), self._resolve_at_c
        )
        outcome = repointed_links(document, named[0], context, request.repoint)
        for problem in outcome.problems:
            self.problem(where, f"the still_rejected re-point is refused: {problem}")
        if outcome.problems:
            return None
        judged = self._place_repoint(document, outcome, row)
        self._note_repoints(row, outcome, judged)
        return outcome

    def _context_answer(
        self, item: Mapping[str, Any], row: Mapping[str, Any], document: Mapping[str, Any]
    ) -> Answer:
        """The item this row answers, as the re-point's admission test reads it."""

        return Answer(
            fired=tuple(self._fired(item)),
            base_document=self.state.base_record(str(document.get("id"))),
            item=str(item.get("id") or ""),
            names_item=bool(row.get("items")),
        )

    def _code(self) -> CodeAtC:
        """The code candidate C, as the worklist's own reader spells it."""

        return CodeAtC(
            CodeTrees(CodeObjects(self.code.root), self.code.tree, self.code.tree),
            self.code.blobs,
        )

    def _stored_repoint_row(
        self, row: Mapping[str, Any], outcome: RepointOutcome, where: str
    ) -> dict[str, Any] | None:
        """The row as the history file stores it, after the stored shape admits the re-points."""

        stored = {**row, "repoint": outcome.items}
        try:
            ReconsiderationRow.model_validate(stored)
        except ValidationError as error:
            self.problem(where, _first_error(error))
            return None
        return stored

    @staticmethod
    def _fired(item: Mapping[str, Any]) -> list[Any]:
        """The item's fired facts, ``facts.changed``, or nothing when it carries none."""

        facts = item.get("facts")
        return list(facts.get("changed") or ()) if isinstance(facts, Mapping) else []

    def _note_repoint_route(self, row: Mapping[str, Any]) -> None:
        """Name ``repoint`` when the answered item fired on a refresh that no longer stands."""

        item = self.reconsiderations.get(str(row["subject"]))
        if item is None or not any(
            one.get("trigger") == REFRESH_NOT_CURRENT for one in self._fired(item)
        ):
            return
        self.notes.append(
            f"{row['subject']}: the item fired because an earlier refresh no longer stands; a row "
            "naming the item in 'items' may carry 'repoint' to say where the code lives now"
        )

    def _place_repoint(
        self,
        document: Mapping[str, Any],
        outcome: RepointOutcome,
        row: Mapping[str, Any],
    ) -> bool:
        """Write the decision with the re-pointed links, bumping its revision once in the leaf.

        ``False`` means the produced links are the stored ones, so a rerun writes the same bytes and
        bumps nothing (rule 4's rerun).
        """

        if canonical_equal(outcome.links, document.get("links")):
            return False
        self._place_record(
            "decision",
            {**deep_copy(document), "links": outcome.links},
            slug=None,
            before=document,
            handoff_entry=str(row["subject"]),
        )
        return True

    def _note_repoints(self, row: Mapping[str, Any], outcome: RepointOutcome, judged: bool) -> None:
        """Report every re-point with its link, both targets and the row's reason (rule 5)."""

        for one in outcome.items:
            before, after = one["before"], one["after"]
            state = "re-pointed" if judged else "already re-pointed, unchanged"
            self.notes.append(
                f"{row['subject']}: link {one['link']} {state} from {before['path']} "
                f"({_describe(before)}) to {after['path']} ({_describe(after)}): {row['reason']}"
            )

    def _resolve_at_c(self, path: str, locator: Mapping[str, Any]) -> dict[str, Any]:
        """``path`` and ``locator`` resolved at C as an anchor document, with its path."""

        return {**self.code.resolve(path, locator).to_document(), "path": path}


def _describe(anchor: Mapping[str, Any]) -> str:
    """A short spelling of an anchor's locator, for the report."""

    locator = anchor.get("locator")
    if not isinstance(locator, Mapping):
        return "no locator"
    kind = locator.get("kind")
    if kind == "symbol":
        return f"symbol {locator.get('name')}"
    if kind == "line_range":
        return f"lines {locator.get('start')}-{locator.get('end')}"
    return "the whole file"
