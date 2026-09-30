"""MIK-R10's unexplained changes: the two registered kinds, the coverage lookup and their items.

**Registration (MIK-R08 rule 1, MIK-R10 rule 2).**

* ``unexplained_hunk`` -- subject ``hunk:<path>@<base lines>..<candidate lines>``, the path plus the
  ``sha256`` of the hunk's changed lines on each side (``absent`` for a side without one);
* ``unexplained_file`` -- subject ``file:<path>@<C-side blob>``;
* facts: the hunk (or file) and its path's coverage state; owner MIK-R10;
* satisfying row: in a covered file the leaf's ``no_invariant`` row (subject ``hunk:<item id>`` or
  the file subject); in an uncovered file the file's onboarding trace (MIK-R30).

The package ``__init__`` imports this module, so the kinds are registered whenever the worklist is.

**Raised.** Every hunk and every non-text change the gate linkage (MIK-R08 definition 8, computed in
:mod:`.compute`) leaves unlinked, in every changed file, covered or not. Hunks of one path with the
same changed lines on both sides are one subject, so one item lists them together. An attach or an
author puts an entry over the change and so links it: the item is then not raised, and the new entry
raises the invariant's own item (a ``touched_invariant`` answered by ``extended``, or nothing for a
new invariant). A delete-only hunk has no line at C, so no new entry links it and only
``no_invariant`` answers it (rule 4).

**Coverage (rule 1).** A path is *covered* when K_B holds a realization entry at it, or when its
governing onboarding route (MIK-R21 rule 1, over K_B's routes) has migration status ``migrated``: the
latest status entry for that route across every census of K_B (MIK-R20); a route without one is
``pending``. Coverage is read at the base, so nothing the leaf itself writes changes which record
answers its items.

**Satisfied** (:func:`~agents_remember.models.knowledge_files.unexplained.unexplained_satisfied_by`):
a covered item by its ``no_invariant`` row; an uncovered item by the file's onboarding trace. The
run first looks for the leaf's ``onboarding:<path>`` row; :func:`settle_uncovered` then takes the
onboarding gate's own item for the file where the leaf route computed one (a counted card change
satisfies it too). The gate (MIK-R09) applies the same predicate to the stored item, so the two
agree by construction. Nothing here suggests an invariant, matches similar code or writes a row
(Exclusions).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from agents_remember.application.knowledge_worklist.code import CodeReadError, CodeTrees
from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.application.knowledge_worklist.registry import (
    ItemKind,
    item_id,
    register_item_kind,
    subject_row,
)
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.memory_quality.knowledge_census.files import read_censuses
from agents_remember.models.knowledge_files.anchor_content import (
    RangeOutsideBlobError,
    content_identity,
    range_bytes,
)
from agents_remember.models.knowledge_files.census import (
    DEFAULT_ROUTE_STATUS,
    CensusRoute,
    governing_status,
)
from agents_remember.models.knowledge_files.documents import ONBOARDING_ROOT
from agents_remember.models.knowledge_files.history import HistoryFile, HistoryRow
from agents_remember.models.knowledge_files.sidecars import RealizationEntry
from agents_remember.models.knowledge_files.unexplained import (
    ABSENT,
    COVERED,
    FILE_ITEM_KIND,
    HUNK_ITEM_KIND,
    NO_INVARIANT,
    UNCOVERED,
    file_subject,
    hunk_item_id,
    hunk_subject,
    parse_hunk_subject,
    row_subject,
    unexplained_satisfied_by,
)
from agents_remember.worktrees.modules.onboarding_trace import (
    ITEM_KIND as ONBOARDING_ITEM_KIND,
)
from agents_remember.worktrees.modules.onboarding_trace import (
    card_subject,
    nearest_governing_route,
)

__all__ = [
    "UNEXPLAINED_FILE_KIND",
    "UNEXPLAINED_HUNK_KIND",
    "CoverageUnreadable",
    "RouteCoverage",
    "Unexplained",
    "UnexplainedSides",
    "answering_trace_subjects",
    "open_count",
    "route_coverage",
    "settle_uncovered",
    "unexplained_items",
]

MIGRATED: Final = "migrated"
_OVERVIEW: Final = "overview.md"
_ROOT_ROUTE: Final = "."
_SATISFYING_ROW: Final = (
    "covered file (a realization entry in K_B, or its governing route migrated): the leaf's "
    "no_invariant row with subject {row} and a reason -- or attach/author an entry over the change, "
    "which links it (a delete-only hunk admits only no_invariant); uncovered file: the file's "
    "onboarding trace, a counted change of its card or its onboarding:<path> no_impact row "
    "(MIK-R10 rules 3-5)"
)


def _hunk_row(subject: str, history: HistoryFile | None) -> HistoryRow | None:
    if history is None or parse_hunk_subject(subject) is None:
        return None
    return history.row_about(f"hunk:{hunk_item_id(subject)}")


UNEXPLAINED_HUNK_KIND: Final = register_item_kind(
    ItemKind(
        name=HUNK_ITEM_KIND,
        subject="changed lines of one hunk (hunk:<path>@<base lines sha256>..<candidate lines sha256>)",
        subject_pattern=r"^hunk:\S",
        facts=("path", "hunks", "changedLines", "deleteOnly", "coverage", "admits", "row"),
        satisfying_row=_SATISFYING_ROW.format(row="hunk:<item id>"),
        row_lookup=_hunk_row,
        owner="MIK-R10",
    )
)
UNEXPLAINED_FILE_KIND: Final = register_item_kind(
    ItemKind(
        name=FILE_ITEM_KIND,
        subject="non-text change of one path (file:<path>@<C-side blob>)",
        subject_pattern=r"^file:\S",
        facts=("path", "blob", "baseBlob", "status", "content", "modeChange", "coverage", "admits"),
        satisfying_row=_SATISFYING_ROW.format(row="file:<path>@<blob>"),
        row_lookup=subject_row("unexplained"),
        owner="MIK-R10",
    )
)


# --------------------------------------------------------------------------------------------------
# Coverage (rule 1)
# --------------------------------------------------------------------------------------------------


class CoverageUnreadable(ValueError):
    """K_B's censuses cannot be read, so no path's coverage can be decided (the run is incomplete)."""


@dataclass(frozen=True)
class RouteCoverage:
    """K_B's onboarding routes and every census route history, for the route half of coverage."""

    routes: frozenset[str] = frozenset()
    histories: tuple[CensusRoute, ...] = ()

    def route_status(self, path: str) -> tuple[str | None, str, str | None]:
        """``(governing route, its migration status, the census that recorded it)`` of ``path``."""

        route = nearest_governing_route(path, self.routes)
        if route is None:
            return None, DEFAULT_ROUTE_STATUS, None
        governing = governing_status(route, self.histories)
        return route, governing.status, governing.census


def route_coverage(paths: Iterable[str], census: Mapping[str, bytes]) -> RouteCoverage:
    """The route half of coverage over one memory tree.

    ``paths`` names the tree's files (only ``onboarding/**/overview.md`` matter: an onboarding route
    is a directory holding one, MIK-R21 rule 1); ``census`` holds the bytes of its
    ``knowledge/census/`` files. A census that does not read raises :class:`CoverageUnreadable`:
    coverage decided over a half-read census could hand an item to the wrong record.
    """

    routes: set[str] = set()
    prefix = f"{ONBOARDING_ROOT}/"
    for path in paths:
        if path.startswith(prefix) and path.endswith(f"/{_OVERVIEW}"):
            route = path.removeprefix(prefix).removesuffix(_OVERVIEW).rstrip("/")
            routes.add(route or _ROOT_ROUTE)
    tree = read_censuses(census)
    if tree.problems:
        named = "; ".join(f"{one.path}: {one.message}" for one in tree.problems[:5])
        raise CoverageUnreadable(f"the censuses of K_B cannot be read: {named}")
    return RouteCoverage(routes=frozenset(routes), histories=tuple(tree.route_histories()))


def _coverage(path: str, base: KnowledgeSide, coverage: RouteCoverage | None) -> dict[str, Any]:
    entries = sum(
        1
        for entry_id in base.entries_by_path.get(path, ())
        if isinstance(base.entries[entry_id].entry, RealizationEntry)
    )
    route, status, census = (coverage or RouteCoverage()).route_status(path)
    fact: dict[str, Any] = {
        "state": COVERED if entries or status == MIGRATED else UNCOVERED,
        "realizationEntries": entries,
        "route": route,
        "routeStatus": status,
    }
    if census is not None:
        fact["census"] = census
    return fact


# --------------------------------------------------------------------------------------------------
# Items
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Unexplained:
    """The run's unexplained items and what the leaf's history says about them."""

    items: tuple[dict[str, Any], ...] = ()
    unnecessary: tuple[tuple[str, str], ...] = field(default=())
    """``(subject, row id)`` of each ``no_invariant`` row that answers no raised item."""

    def summary(self) -> dict[str, Any]:
        return _summary(self.items, self.unnecessary)


def _summary(items: Iterable[Mapping[str, Any]], unnecessary: Iterable[Any]) -> dict[str, Any]:
    listed = list(items)
    counts = {HUNK_ITEM_KIND: 0, FILE_ITEM_KIND: 0}
    for item in listed:
        counts[str(item["kind"])] += 1
    return {
        "itemsByKind": counts,
        "openCount": open_count(listed),
        "unnecessaryRows": [{"subject": subject, "row": row} for subject, row in unnecessary],
    }


def _identity(data: bytes | None, start: int, count: int) -> str:
    """The changed lines' identity on one side; ``absent`` only for a side with no changed line.

    A hunk names lines of its own blobs, so a range outside the blob is an unreadable input: the run
    becomes ``incomplete`` naming it, never an item with a made-up ``absent`` side.
    """

    if data is None or count == 0:
        return ABSENT
    try:
        return content_identity(range_bytes(data, start, start + count - 1))
    except RangeOutsideBlobError as error:
        raise CodeReadError(f"a hunk's changed lines are not in its blob: {error}") from error


def _admits(coverage: Mapping[str, Any], at_candidate: bool) -> list[str]:
    if coverage["state"] == UNCOVERED:
        return ["onboarding_trace"]
    return ["attach", "author", NO_INVARIANT] if at_candidate else [NO_INVARIANT]


def _trace(path: str) -> dict[str, Any]:
    """The onboarding-trace fact before the gate's own item is known: only a row can answer."""

    return {"subject": card_subject(path), "item": None, "countedChange": False}


@dataclass
class _Collector:
    code: CodeTrees
    base: KnowledgeSide
    coverage: RouteCoverage | None
    hunks: dict[str, dict[str, Any]] = field(default_factory=dict)
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    _blobs: dict[str, bytes] = field(default_factory=dict)

    def blob(self, blob: str | None) -> bytes | None:
        if blob is None:
            return None
        if blob not in self._blobs:
            try:
                self._blobs[blob] = self.code.objects.blob(blob)
            except Exception as error:  # a blob the store does not hold: named, never skipped
                raise CodeReadError(f"the code blob {blob} cannot be read: {error}") from error
        return self._blobs[blob]

    def change(self, change: Mapping[str, Any]) -> None:
        path = str(change["path"])
        base_blob, candidate_blob = self.code.base().get(path), self.code.candidate().get(path)
        file_level = change.get("fileLevel")
        if isinstance(file_level, Mapping) and not file_level.get("linked"):
            self._file(change, path, base_blob, candidate_blob)
        unlinked = [one for one in change.get("hunks") or () if not one.get("linked")]
        if not unlinked:
            return
        before, after = self.blob(base_blob), self.blob(candidate_blob)
        for hunk in unlinked:
            (old_start, old_count), (new_start, new_count) = hunk["base"], hunk["candidate"]
            sides = (
                _identity(before, old_start, old_count),
                _identity(after, new_start, new_count),
            )
            subject = hunk_subject(path, *sides)
            item = self.hunks.get(subject)
            if item is None:
                coverage = _coverage(path, self.base, self.coverage)
                item = self.hunks[subject] = {
                    "kind": HUNK_ITEM_KIND,
                    "subject": subject,
                    "identities": list(sides),
                    "facts": {
                        "path": path,
                        "hunks": [],
                        "changedLines": {"base": sides[0], "candidate": sides[1]},
                        "deleteOnly": new_count == 0,
                        "coverage": coverage,
                        "admits": _admits(coverage, new_count > 0),
                    },
                }
            item["facts"]["hunks"].append({"base": hunk["base"], "candidate": hunk["candidate"]})

    def _object(self, tree: str, path: str, blob: str | None) -> str | None:
        """The tree entry's object at ``path``: a regular file's blob, else a symlink's blob or a
        submodule's commit, which the code trees' file map does not list."""

        if blob is not None:
            return blob
        result = run_git(
            self.code.repository,
            ["rev-parse", "--verify", "--quiet", f"{tree}:{path}"],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
        found = result.stdout.strip()
        return found if result.returncode == 0 and found else None

    def _file(
        self,
        change: Mapping[str, Any],
        path: str,
        base_blob: str | None,
        candidate_blob: str | None,
    ) -> None:
        base_blob = self._object(self.code.base_tree, path, base_blob)
        candidate_blob = self._object(self.code.candidate_tree, path, candidate_blob)
        subject = file_subject(path, candidate_blob)
        coverage = _coverage(path, self.base, self.coverage)
        self.files[subject] = {
            "kind": FILE_ITEM_KIND,
            "subject": subject,
            "identities": [candidate_blob or ABSENT],
            "facts": {
                "path": path,
                "blob": candidate_blob or ABSENT,
                "baseBlob": base_blob or ABSENT,
                "status": change.get("status"),
                "content": change.get("content"),
                "modeChange": change.get("modeChange"),
                "coverage": coverage,
                "admits": _admits(coverage, candidate_blob is not None),
            },
        }


def _rows_by_subject(history: HistoryFile | None) -> dict[str, str]:
    return {} if history is None else {row.subject: row.id for row in history.rows}


def _finished(raw: dict[str, Any], rows: Mapping[str, str]) -> dict[str, Any]:
    identities = raw.pop("identities")
    item = {**raw, "id": item_id(raw["kind"], raw["subject"], identities)}
    facts = item["facts"]
    facts["row"] = row_subject(item)
    if facts["coverage"]["state"] == UNCOVERED:
        facts["onboardingTrace"] = _trace(facts["path"])
    item["satisfiedBy"] = unexplained_satisfied_by(item, rows)
    return item


@dataclass(frozen=True)
class UnexplainedSides:
    """What the items are raised over: C and B, K_B and K_C, K_B's route coverage, the owner."""

    code: CodeTrees
    base: KnowledgeSide
    candidate: KnowledgeSide
    coverage: RouteCoverage | None
    owner: str | None


def unexplained_items(changes: Sequence[Mapping[str, Any]], sides: UnexplainedSides) -> Unexplained:
    """Raise one item per unlinked change of ``changes`` (the run's ``changes[]`` linkage)."""

    collector = _Collector(code=sides.code, base=sides.base, coverage=sides.coverage)
    for change in changes:
        collector.change(change)
    history = None if sides.owner is None else sides.candidate.history(sides.owner)
    rows = _rows_by_subject(history)
    items = tuple(
        _finished(raw, rows)
        for raw in sorted(
            [*collector.hunks.values(), *collector.files.values()],
            key=lambda one: (one["kind"], one["subject"]),
        )
    )
    # Only a covered item takes a no_invariant row (rule 3); a row about an uncovered item
    # satisfies nothing (rule 5), so it is reported as unnecessary like any other stray row.
    answering = {
        item["facts"]["row"] for item in items if item["facts"]["coverage"]["state"] == COVERED
    }
    unnecessary = tuple(
        (row.subject, row.id)
        for row in (history.rows if history is not None else ())
        if row.disposition == NO_INVARIANT and row.subject not in answering
    )
    return Unexplained(items=items, unnecessary=unnecessary)


def settle_uncovered(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """``items`` with each uncovered unexplained item bound to the onboarding gate's item for its file.

    The leaf route adds MIK-R30's items after the run (they need the onboarding Markdown); an
    uncovered item whose file has such an item takes its counted change, its unreadable sidecar and
    its answer, so a card updated in the leaf satisfies it. Without one, the item keeps what the run
    found (the ``onboarding:<path>`` row). The caller recomputes the digest over the settled list.
    """

    traces = {item["subject"]: item for item in items if item.get("kind") == ONBOARDING_ITEM_KIND}
    return [_settled(item, traces) for item in items]


def answering_trace_subjects(items: Iterable[Mapping[str, Any]]) -> frozenset[str]:
    """The ``onboarding:<path>`` subjects that answer an uncovered unexplained item (rule 5).

    MIK-R30 raises no card item for such a path when it gates no card there, so its own report
    would call the leaf's row about it unnecessary; this set is what that report leaves out.
    """

    return frozenset(
        str(item["facts"]["onboardingTrace"]["subject"])
        for item in items
        if item.get("kind") in (HUNK_ITEM_KIND, FILE_ITEM_KIND)
        and isinstance((item.get("facts") or {}).get("onboardingTrace"), Mapping)
    )


def open_count(items: Iterable[Mapping[str, Any]]) -> int:
    """How many unexplained items are open."""

    return sum(
        1
        for item in items
        if item.get("kind") in (HUNK_ITEM_KIND, FILE_ITEM_KIND) and item.get("satisfiedBy") is None
    )


def _settled(item: Mapping[str, Any], traces: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    facts = item.get("facts") or {}
    if item.get("kind") not in (HUNK_ITEM_KIND, FILE_ITEM_KIND) or not isinstance(
        facts.get("onboardingTrace"), Mapping
    ):
        return dict(item)
    subject = facts["onboardingTrace"].get("subject")
    trace = traces.get(subject)
    if trace is None:
        return dict(item)
    trace_facts = trace.get("facts") or {}
    bound: dict[str, Any] = {
        "subject": subject,
        "item": trace.get("id"),
        "countedChange": trace_facts.get("countedChange") is True,
    }
    if trace_facts.get("sidecarUnreadable"):
        bound["sidecarUnreadable"] = trace_facts["sidecarUnreadable"]
    settled = {**item, "facts": {**facts, "onboardingTrace": bound}}
    answered = trace.get("satisfiedBy")
    settled["satisfiedBy"] = answered if isinstance(answered, str) else None
    return settled
