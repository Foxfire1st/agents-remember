"""The onboarding refresh gate on history files (MIK-R30), for converted memory trees.

Today's gate (:mod:`.onboarding`) reads Update History entries and ``lastVerifiedCommit*`` metadata.
A converted tree has neither, so where K_B or K_C holds the layout marker the gate is this one:

* **Items.** Every source file changed between B and C whose onboarding is sidecar-stored and whose
  card (``onboarding/<path>.md`` or its sidecar ``.json``) exists in K_B or K_C raises one
  ``onboarding_trace`` item with subject ``onboarding:<path>``. The nearest governing route of every
  changed path (MIK-R21 rule 1: the nearest ancestor directory of ``onboarding/<path>.md`` holding an
  ``overview.md`` in K_C) raises one item with subject ``onboarding:<route>/overview``
  (``onboarding:overview`` for the root route, as MIK-R24's marker move writes it). Only the nearest
  route is gated, as today. An item's facts are the changed source files it covers.
* **Satisfied** by a *counted change* of the item's Markdown or sidecar between K_B and K_C (rule
  3), or by the leaf's history row with the item's subject and disposition ``no_impact``.
* **Counted change (rule 3).** Any change of the Markdown counts. A sidecar change counts unless it
  is limited to anchors' ``blob``, line numbers (a ``line_range`` locator's ``start``/``end``) and
  ``content``: those are the writer's and the fixer's mechanical updates, and a curator making only
  them has not reviewed anything either. Creating or deleting either file counts.
* **Findings.** Each open item is one repair finding naming the card or route and the required
  action. A row naming a subject the leaf raised no item for is reported as unnecessary
  (report-only, never an error). An unreadable history file or unresolvable sides are one finding
  each, so the gate never lapses into silence.

The trees reach this module as path-to-bytes mappings; the application layer resolves them (the
converted base of MIK-R24 rule 7 included). Nothing here reads Git, so the rule is a pure function
of its inputs.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Final

from pydantic import ValidationError

from agents_remember.kernel import coordination_context_resolver as resolver
from agents_remember.kernel.canonical_json import prefixed_sha256_digest
from agents_remember.models.knowledge_files.canonical import CanonicalFormatError, parse_json
from agents_remember.models.knowledge_files.documents import (
    LAYOUT_MARKER_PATH,
    ONBOARDING_ROOT,
    history_path,
    owner_history_attempt,
    parse_history_document,
)
from agents_remember.models.knowledge_files.history import (
    HistoryFile,
    OnboardingTraceRow,
    merged_leaf_history,
)

ITEM_KIND: Final = "onboarding_trace"
SUBJECT_PREFIX: Final = "onboarding:"
OVERVIEW: Final = "overview.md"
ROOT_ROUTE: Final = "."
CHECK_NAME: Final = "onboarding-trace"
MISSING_CODE: Final = "onboarding-trace-missing"
UNNECESSARY_CODE: Final = "onboarding-trace-unnecessary"
INCOMPLETE_CODE: Final = "onboarding-trace-incomplete"
HISTORY_UNREADABLE_CODE: Final = "onboarding-trace-history-unreadable"
BASE_SIDECAR_UNREADABLE_CODE: Final = "onboarding-trace-base-sidecar-unreadable"
_MECHANICAL_ANCHOR_FIELDS: Final = frozenset({"blob", "content"})
_LINE_FIELDS: Final = frozenset({"start", "end"})


def is_converted(files: Mapping[str, bytes]) -> bool:
    """A memory tree is converted exactly when it holds the layout marker (MIK-R21 rule 1)."""

    return LAYOUT_MARKER_PATH in files


def card_subject(source_path: str) -> str:
    return f"{SUBJECT_PREFIX}{source_path}"


def route_subject(route: str) -> str:
    return (
        f"{SUBJECT_PREFIX}overview" if route == ROOT_ROUTE else f"{SUBJECT_PREFIX}{route}/overview"
    )


def _route_directory(route: str) -> str:
    return ONBOARDING_ROOT if route == ROOT_ROUTE else f"{ONBOARDING_ROOT}/{route}"


# --------------------------------------------------------------------------------------------------
# Rule 3: what counts as an onboarding change
# --------------------------------------------------------------------------------------------------


def _is_anchor(value: Mapping[str, Any]) -> bool:
    return isinstance(value.get("locator"), Mapping) and "blob" in value and "content" in value


def _without_mechanical_fields(value: Any) -> Any:
    """``value`` with every anchor's ``blob``, ``content`` and line numbers removed."""

    if isinstance(value, Mapping):
        anchor = _is_anchor(value)
        kept: dict[str, Any] = {}
        for key, item in value.items():
            if anchor and key in _MECHANICAL_ANCHOR_FIELDS:
                continue
            if anchor and key == "locator" and isinstance(item, Mapping):
                kept[key] = {
                    name: part
                    for name, part in item.items()
                    if not (item.get("kind") == "line_range" and name in _LINE_FIELDS)
                }
                continue
            kept[key] = _without_mechanical_fields(item)
        return kept
    if isinstance(value, list):
        return [_without_mechanical_fields(item) for item in value]
    return value


_UNREADABLE: Final = object()


def _sidecar_meaning(data: bytes) -> Any:
    try:
        return _without_mechanical_fields(parse_json(data.decode("utf-8")))
    except (UnicodeDecodeError, CanonicalFormatError):
        return _UNREADABLE


def sidecar_unreadable(tree: Mapping[str, bytes], sidecar: str) -> bool:
    """Whether ``tree`` holds a sidecar at ``sidecar`` that cannot be parsed."""

    data = tree.get(sidecar)
    return data is not None and _sidecar_meaning(data) is _UNREADABLE


def counted_markdown_change(before: bytes | None, after: bytes | None) -> bool:
    """Any change of the Markdown counts, creation and deletion included."""

    return before != after


def counted_sidecar_change(before: bytes | None, after: bytes | None) -> bool:
    """A sidecar change counts unless only anchors' ``blob``, line numbers and ``content`` moved."""

    if before == after:
        return False
    if before is None or after is None:
        return True
    old, new = _sidecar_meaning(before), _sidecar_meaning(after)
    if new is _UNREADABLE:
        return False  # an unreadable sidecar never counts as a trace (review N5)
    return old is _UNREADABLE or old != new  # a readable repair of an unreadable one counts


def counted_change(
    base: Mapping[str, bytes], candidate: Mapping[str, bytes], markdown: str, sidecar: str
) -> bool:
    """Whether the document ``markdown`` or its ``sidecar`` has a counted change from K_B to K_C."""

    return counted_markdown_change(
        base.get(markdown), candidate.get(markdown)
    ) or counted_sidecar_change(base.get(sidecar), candidate.get(sidecar))


# --------------------------------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OnboardingTraceSides:
    """K_B and K_C as the gate reads them, the history owner, and the pairing it used.

    ``incomplete`` names the input that could not be read or established (the pairing, K_B, K_C);
    the gate then raises no item and reports one finding, so it never passes silently.
    """

    owner: str
    base: Mapping[str, bytes] = field(default_factory=dict)
    candidate: Mapping[str, bytes] = field(default_factory=dict)
    pairing: Mapping[str, Any] = field(default_factory=dict)
    incomplete: str | None = None


@dataclass(frozen=True)
class TraceItem:
    """One ``onboarding_trace`` item: a card or route overview some changed source files need."""

    subject: str
    markdown: str
    sidecar: str
    sources: tuple[str, ...]
    counted: bool
    row: str | None
    """The ID of the satisfying ``no_impact`` row, when the leaf's history file has one."""
    route: bool = False
    unreadable: bool = False
    """K_C's sidecar cannot be parsed: the item stays open until the leaf repairs it (N5, R2-2)."""

    @property
    def id(self) -> str:
        return prefixed_sha256_digest([ITEM_KIND, self.subject, list(self.sources)])

    @property
    def open(self) -> bool:
        return self.unreadable or (not self.counted and self.row is None)

    def to_document(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": ITEM_KIND,
            "subject": self.subject,
            "facts": {
                "sources": list(self.sources),
                "markdown": self.markdown,
                "sidecar": self.sidecar,
                "countedChange": self.counted and not self.unreadable,
                **({"sidecarUnreadable": f"{self.sidecar} in K_C"} if self.unreadable else {}),
            },
            "satisfiedBy": None if self.open else "counted-change" if self.counted else self.row,
        }

    def required_action(self, owner: str) -> str:
        what = "route overview" if self.route else "card"
        if self.unreadable:
            return (
                f"sidecar unreadable: {self.sidecar} cannot be parsed in K_C; repair "
                "it through the writer (knowledge-ingest) -- an unreadable sidecar never satisfies "
                "a trace"
            )
        return (
            f"update the {what} {self.markdown} (its Markdown, or a sidecar field other than an "
            f"anchor's blob, line numbers and content), or record in {history_path(owner)} a row "
            f'{{subject: "{self.subject}", disposition: "no_impact", reason}} through '
            "knowledge-ingest"
        )


@dataclass(frozen=True)
class OnboardingTraceResult:
    """The gate's outcome for one candidate: every item, the unnecessary rows and any problem."""

    owner: str
    items: tuple[TraceItem, ...]
    unnecessary: tuple[tuple[str, str], ...] = ()
    """``(subject, row id)`` of each row naming a subject the leaf raised no item for."""
    problems: tuple[tuple[str, str, str], ...] = ()
    """``(code, path, message)`` of each input the gate could not read."""
    pairing: Mapping[str, Any] = field(default_factory=dict)

    @property
    def open_items(self) -> tuple[TraceItem, ...]:
        return tuple(item for item in self.items if item.open)

    @property
    def ok(self) -> bool:
        return not self.open_items and not self.problems

    def repair_findings(self) -> list[dict[str, str]]:
        """One finding per missing trace (rule 6), plus one per unreadable input."""

        findings = [
            {
                "check": CHECK_NAME,
                "code": code,
                "path": path,
                "message": message,
            }
            for code, path, message in self.problems
        ]
        findings.extend(
            {
                "check": CHECK_NAME,
                "code": MISSING_CODE,
                "path": item.markdown,
                "message": (
                    f"{item.subject} has no trace for the changed source file(s) "
                    f"{', '.join(item.sources)}: {item.required_action(self.owner)}"
                ),
                "itemId": item.id,
            }
            for item in self.open_items
        )
        return findings

    def report_only_findings(self) -> list[dict[str, str]]:
        return [
            {
                "check": CHECK_NAME,
                "code": UNNECESSARY_CODE,
                "path": history_path(self.owner),
                "subject": subject,
                "message": (
                    f"row {row} ({subject}) names a card or route no changed source file of this "
                    "leaf needs a trace for; it is unnecessary, not an error"
                ),
            }
            for subject, row in self.unnecessary
        ]

    def refusal(self) -> str:
        """The closeout refusal naming every missing trace and its required action."""

        lines = [f"{path or code}: {message}" for code, path, message in self.problems]
        lines.extend(
            f"{item.markdown} ({item.subject}; sources {', '.join(item.sources)}): "
            f"{item.required_action(self.owner)}"
            for item in self.open_items
        )
        return (
            "external-memory closeout requires an onboarding trace for every changed source file "
            "and its governing route overview (MIK-R30): " + "; ".join(lines)
        )

    def brief(self, limit: int = 50) -> dict[str, Any]:
        """The compact summary a tool response carries: counts and the first open subjects."""

        return {
            "owner": self.owner,
            "itemCount": len(self.items),
            "openCount": len(self.open_items),
            "open": [item.subject for item in self.open_items][:limit],
            "unnecessaryRowCount": len(self.unnecessary),
            "problemCount": len(self.problems),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "items": [item.to_document() for item in self.items],
            "openCount": len(self.open_items),
            "unnecessaryRows": [
                {"subject": subject, "row": row} for subject, row in self.unnecessary
            ],
            "problems": [
                {"code": code, "path": path, "message": message}
                for code, path, message in self.problems
            ],
            "pairing": dict(self.pairing),
        }


def _history_rows(
    sides: OnboardingTraceSides,
) -> tuple[dict[str, str], tuple[str, str, str] | None]:
    """The leaf's ``no_impact`` onboarding rows by subject, or the problem reading its files.

    Every history file of the leaf counts: a leaf reopened after its closeout has its closed file and
    a later attempt, read as one history (L37 ruling).
    """

    paths = sorted(
        (attempt, path)
        for path in sides.candidate
        if (attempt := owner_history_attempt(path, sides.owner)) is not None
    )
    histories: list[HistoryFile] = []
    for _attempt, path in paths:
        try:
            histories.append(parse_history_document(path, sides.candidate[path].decode("utf-8")))
        except (UnicodeDecodeError, ValueError, ValidationError) as error:
            return {}, (
                HISTORY_UNREADABLE_CODE,
                path,
                f"the leaf's history file cannot be read, so no onboarding row can satisfy an "
                f"item: {str(error).splitlines()[0]}",
            )
    history = merged_leaf_history(histories)
    if history is None:
        return {}, None
    return {
        row.subject: row.id
        for row in history.rows
        if isinstance(row, OnboardingTraceRow) and row.disposition == "no_impact"
    }, None


def _routes(candidate: Mapping[str, bytes]) -> frozenset[str]:
    """Every onboarding route of K_C: a directory under ``onboarding/`` holding ``overview.md``."""

    routes: set[str] = set()
    for path in candidate:
        pure = PurePosixPath(path)
        if pure.name != OVERVIEW or not path.startswith(f"{ONBOARDING_ROOT}/"):
            continue
        parent = pure.parent.as_posix().removeprefix(ONBOARDING_ROOT).lstrip("/")
        routes.add(parent or ROOT_ROUTE)
    return frozenset(routes)


def nearest_governing_route(source_path: str, routes: frozenset[str]) -> str | None:
    """The route whose directory is the nearest ancestor of ``onboarding/<source_path>.md``."""

    directory = PurePosixPath(source_path).parent
    while True:
        route = directory.as_posix()
        route = ROOT_ROUTE if route in {"", "."} else route
        if route in routes:
            return route
        if route == ROOT_ROUTE:
            return None
        directory = directory.parent


def _card_sources(context: Any, changed_paths: Iterable[str]) -> list[str]:
    sources = []
    for source_path in changed_paths:
        storage = resolver.resolve_storage_for_source(
            source_path, context.storage, context.code_repository_name
        )
        if storage != "disabled" and resolver.is_sidecar_storage(storage):
            sources.append(source_path)
    return sources


def onboarding_trace_result(
    context: Any, changed_paths: Sequence[str], sides: OnboardingTraceSides
) -> OnboardingTraceResult:
    """Evaluate MIK-R30 for the changed source files ``changed_paths`` over ``sides``."""

    if sides.incomplete is not None:
        return OnboardingTraceResult(
            owner=sides.owner,
            items=(),
            problems=(
                (
                    INCOMPLETE_CODE,
                    "",
                    f"the onboarding trace gate cannot establish its sides: {sides.incomplete}",
                ),
            ),
            pairing=sides.pairing,
        )
    rows, problem = _history_rows(sides)
    base, candidate = sides.base, sides.candidate
    items: list[TraceItem] = []
    for source in sorted(set(_card_sources(context, changed_paths))):
        markdown = f"{ONBOARDING_ROOT}/{source}.md"
        sidecar = f"{ONBOARDING_ROOT}/{source}.json"
        if not any(path in tree for tree in (base, candidate) for path in (markdown, sidecar)):
            continue  # no card on either side: today's missing-onboarding check owns it
        subject = card_subject(source)
        items.append(
            TraceItem(
                subject=subject,
                markdown=markdown,
                sidecar=sidecar,
                sources=(source,),
                counted=counted_change(base, candidate, markdown, sidecar),
                row=rows.get(subject),
                unreadable=sidecar_unreadable(candidate, sidecar),
            )
        )
    routes = _routes(candidate)
    governed: dict[str, set[str]] = {}
    for source in changed_paths:
        route = nearest_governing_route(source, routes)
        if route is not None:
            governed.setdefault(route, set()).add(source)
    for route, sources in sorted(governed.items()):
        directory = _route_directory(route)
        markdown, sidecar = f"{directory}/{OVERVIEW}", f"{directory}/overview.json"
        subject = route_subject(route)
        items.append(
            TraceItem(
                subject=subject,
                markdown=markdown,
                sidecar=sidecar,
                sources=tuple(sorted(sources)),
                counted=counted_change(base, candidate, markdown, sidecar),
                row=rows.get(subject),
                route=True,
                unreadable=sidecar_unreadable(candidate, sidecar),
            )
        )
    raised = {item.subject for item in items}
    # A defect in the fixed base commit cannot be repaired in the leaf: an input problem, named,
    # never a permanently open item (R2-2).
    problems = [] if problem is None else [problem]
    problems.extend(
        (
            BASE_SIDECAR_UNREADABLE_CODE,
            item.sidecar,
            "K_B's sidecar cannot be parsed, so the onboarding trace gate cannot compare it; the "
            "base commit's knowledge must be repaired on its own line",
        )
        for item in items
        if sidecar_unreadable(base, item.sidecar)
    )
    return OnboardingTraceResult(
        owner=sides.owner,
        items=tuple(items),
        unnecessary=tuple(sorted((s, r) for s, r in rows.items() if s not in raised)),
        problems=tuple(problems),
        pairing=sides.pairing,
    )


def onboarding_item_open(item: Mapping[str, Any], rows_by_subject: Mapping[str, str]) -> bool:
    """The kind's satisfying rule over an item document, for the generic gate (MIK-R09 rule 7)."""

    facts = item.get("facts")
    if not isinstance(facts, Mapping) or "sidecarUnreadable" in facts:
        return True  # an unreadable leaf-side sidecar never satisfies a trace (N5, R2-1)
    counted = facts.get("countedChange") is True
    return not counted and item.get("subject") not in rows_by_subject
