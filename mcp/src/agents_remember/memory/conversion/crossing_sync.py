"""A crossing sync's knowledge half: markers, conversion, structural merge, validation (MIK-R24 rule 8).

A managed sync is a *crossing sync* when at least one of its merge base, its own side and the
incoming side is unconverted and at least one is converted. :func:`cross` runs the steps on the three
memory trees and returns a :class:`CrossingPlan` -- the merged ``knowledge/`` and ``onboarding/``
files, the conflicts, and the report -- without touching any repository. The managed sync
transaction applies the plan to its merge (``worktrees/knowledge_crossing``); if any step fails,
:class:`CrossingError` names it and the line is left unchanged.

1. **Markers first.** When the own side is a leaf line, the Update History no-impact markers that
   leaf added (``No content impact:``/``No route impact:``, lines absent from the base) become
   ``onboarding_trace`` rows ``{subject, disposition: no_impact, reason: <marker text>}`` in the
   leaf's history file, before its tree is converted.
2. **Convert** every unconverted tree with the converted side's pinned version; fallback cards use
   the own side's paired code commit on every side. (The commit route's converted base,
   ``base.GitBaseConverter``, uses the base's own ``Code-Commit`` instead; the two differ only for
   rule 2 fallback cards -- see there.)
3. **Merge structurally** (:mod:`.crossing`).
4. **Conflicts** are returned for the curator: overlapping authored edits and reference-number
   collisions. A record conflict is resolved through the writer; its row belongs to the leaf's
   history file, or on a master line to ``<task-id>-crossing-<n>``, which the plan names.
5. **Validate.** Enforced where the merge is committed, not here: the managed sync's commit step
   runs the knowledge validator (``memory_commit_refusal``) over the staged merge against both
   parents, and ``GitBaseConverter`` replaces the unconverted parent by its conversion (rule 7). A
   refusal leaves the merge staged for the curator.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal

from agents_remember.kernel.onboarding_doc import NO_IMPACT_MARKER_PATTERN, new_history_lines
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory.conversion.convert import (
    OVERVIEW,
    MemoryInput,
    convert_memory,
    route_of,
)
from agents_remember.memory.conversion.crossing import Conflict, merge_trees
from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH
from agents_remember.models.knowledge_files.canonical import canonical_text, parse_json
from agents_remember.models.knowledge_files.documents import (
    LAYOUT_MARKER_PATH,
    ONBOARDING_ROOT,
    RECORD_DIRECTORIES,
    history_path,
)
from agents_remember.models.knowledge_files.ids import mint_id

Step = Literal["markers", "convert", "merge", "validate"]
_CROSSING_FILE: Final = re.compile(
    r"^knowledge/history/(?P<task>.+)-crossing-(?P<n>[1-9][0-9]*)\.json$"
)


class CrossingError(RuntimeError):
    """A crossing step could not complete; the line is left unchanged."""

    def __init__(self, step: Step, message: str) -> None:
        super().__init__(f"crossing sync step '{step}' failed: {message}")
        self.step = step


@dataclass(frozen=True)
class HistoryOwner:
    """Who performs the sync: a leaf (its ID) or a master line (its task ID)."""

    kind: Literal["leaf", "master"]
    id: str


@dataclass
class CrossingPlan:
    """The merged knowledge/onboarding files (``None`` = delete) and what the curator must resolve."""

    files: dict[str, bytes | None]
    conflicts: list[Conflict]
    report: dict[str, Any] = field(default_factory=dict)
    conflict_versions: dict[str, tuple[bytes | None, bytes | None, bytes | None]] = field(
        default_factory=dict
    )


def is_crossing(base: bool, own: bool, incoming: bool) -> bool:
    """Rule 8: at least one of the three trees is unconverted and at least one is converted."""

    states = {base, own, incoming}
    return states == {True, False}


def _version(tree: MemoryInput) -> str | None:
    data = tree.files.get(LAYOUT_MARKER_PATH)
    if data is None:
        return None
    return str(parse_json(data.decode("utf-8"))["conversion"])


def _card_subject(card: str) -> str:
    relative = card.removeprefix(f"{ONBOARDING_ROOT}/")
    if PurePosixPath(relative).name == OVERVIEW:
        route = route_of(card)
        return "onboarding:overview" if route == "." else f"onboarding:{route}/overview"
    return f"onboarding:{relative.removesuffix('.md')}"


def marker_rows(base: MemoryInput, own: MemoryInput) -> list[dict[str, Any]]:
    """The own leaf's no-impact markers, as ``onboarding_trace`` rows (step 1)."""

    rows: list[dict[str, Any]] = []
    for card in sorted(own.files):
        if not (card.startswith(f"{ONBOARDING_ROOT}/") and card.endswith(".md")):
            continue
        before = base.files.get(card)
        added = new_history_lines(
            before.decode("utf-8") if before is not None else None, own.files[card].decode("utf-8")
        )
        markers = [
            piece
            for line in added
            if NO_IMPACT_MARKER_PATTERN.search(line)
            for piece in marker_pieces(line)
        ]
        if markers:
            rows.append(
                {
                    "id": mint_id("history_row"),
                    "subject": _card_subject(card),
                    "disposition": "no_impact",
                    "reason": MARKER_ROW_REASON,
                    "markers": markers,
                    "items": [],
                }
            )
    return rows


MARKER_ROW_REASON: Final = (
    "Update History no-impact attestation(s) moved here by the crossing sync (MIK-R24 rule 8 "
    "step 1); the marker lines are in 'markers'."
)


def marker_pieces(line: str) -> list[str]:
    """One marker line as history text values: itself, or consecutive pieces no longer than the
    text limit (split at the limit, so the same line always splits the same way)."""

    return [
        line[start : start + PROSE_MAX_LENGTH] for start in range(0, len(line), PROSE_MAX_LENGTH)
    ]


def with_markers(
    own: MemoryInput, owner: HistoryOwner, rows: list[dict[str, Any]]
) -> tuple[dict[str, bytes], list[dict[str, Any]]]:
    """The own tree with the marker rows in the leaf's history file, and the rows not moved.

    A marker whose subject already has a row in the history file is not moved (a file holds one
    row per subject, MIK-R07) and the existing, authored row is left as it is; the marker is
    returned so the crossing report names it -- never dropped silently.
    """

    path = history_path(owner.id)
    files = dict(own.files)
    existing = files.get(path)
    document: dict[str, Any] = (
        parse_json(existing.decode("utf-8"))
        if existing is not None
        else {"schema": "ar-history/v1", "leaf": owner.id, "closed": False, "rows": []}
    )
    known = {row["subject"] for row in document["rows"]}
    not_moved = [row for row in rows if row["subject"] in known]
    document["rows"] = [*document["rows"], *(row for row in rows if row["subject"] not in known)]
    files[path] = canonical_text(document).encode("utf-8")
    return files, [{"subject": row["subject"], "markers": row["markers"]} for row in not_moved]


def next_crossing_owner(task_id: str, *trees: Mapping[str, bytes]) -> str:
    """``<task-id>-crossing-<n>``: one more than any crossing file of this task on any side."""

    numbers = [
        int(match["n"])
        for tree in trees
        for path in tree
        if (match := _CROSSING_FILE.match(path)) and match["task"] == task_id
    ]
    return f"{task_id}-crossing-{max(numbers, default=0) + 1}"


Sides = tuple[MemoryInput, MemoryInput, MemoryInput]
_SIDE_NAMES: Final = ("base", "own", "incoming")


def _pinned_version(sides: Sides) -> str:
    if not is_crossing(*(tree.converted for tree in sides)):
        raise CrossingError("convert", "not a crossing sync: the three trees are all alike")
    versions = {version for version in map(_version, sides) if version is not None}
    if len(versions) != 1:
        raise CrossingError(
            "convert", f"converted sides disagree on the version: {sorted(versions)}"
        )
    return versions.pop()


def _convert_sides(
    sides: Sides, objects: CodeObjects, own_paired_commit: str, version: str
) -> dict[str, dict[str, bytes]]:
    converted: dict[str, dict[str, bytes]] = {}
    for name, tree in zip(_SIDE_NAMES, sides, strict=True):
        try:
            outcome = convert_memory(
                tree, objects, paired_commit=own_paired_commit, version=version
            )
        except ValueError as error:
            raise CrossingError("convert", f"{name} side: {error}") from error
        converted[name] = outcome.files
    return converted


def cross(
    sides: Sides,
    objects: CodeObjects,
    *,
    own_paired_commit: str,
    repository: Path,
    owner: HistoryOwner | None,
) -> CrossingPlan:
    """Run rule 8 steps 1-4 over (base, own, incoming); raise :class:`CrossingError` on failure."""

    base, own, _ = sides
    version = _pinned_version(sides)
    rows: list[dict[str, Any]] = []
    if owner is not None and owner.kind == "leaf" and not own.converted:
        try:
            rows = marker_rows(base, own)
        except (UnicodeDecodeError, ValueError) as error:
            raise CrossingError("markers", str(error)) from error
    converted = _convert_sides(sides, objects, own_paired_commit, version)
    not_moved: list[dict[str, Any]] = []
    if rows and owner is not None:
        converted["own"], not_moved = with_markers(
            MemoryInput(label=own.label, files=converted["own"], database=None), owner, rows
        )
    try:
        merged = merge_trees(*(converted[name] for name in _SIDE_NAMES), repository=repository)
    except (RuntimeError, ValueError) as error:
        raise CrossingError("merge", str(error)) from error
    plan = CrossingPlan(files=merged.files, conflicts=merged.conflicts)
    for path in merged.conflicted_paths:
        base_bytes, own_bytes, incoming_bytes = (converted[name].get(path) for name in _SIDE_NAMES)
        plan.conflict_versions[path] = (base_bytes, own_bytes, incoming_bytes)
    record_owner = None
    if owner is not None:
        record_owner = (
            owner.id if owner.kind == "leaf" else next_crossing_owner(owner.id, *converted.values())
        )
        if owner.kind == "master" and _record_conflicts(merged.conflicted_paths):
            # Rule 8 step 4: a master line has no leaf, so the resolved record's row goes into
            # <task-id>-crossing-<n>.json. The file is opened here for the curator's row and
            # closed by the sync in the merge commit itself.
            plan.files[history_path(record_owner)] = canonical_text(
                {"schema": "ar-history/v1", "crossing": record_owner, "closed": False, "rows": []}
            ).encode("utf-8")
    plan.report = {
        "version": version,
        "converted": [
            name for name, tree in zip(_SIDE_NAMES, sides, strict=True) if not tree.converted
        ],
        "markerRows": len(rows) - len(not_moved),
        "markersNotMoved": not_moved,
        "taken": dict(sorted(merged.taken.items())),
        "cards": _card_counts(converted, merged.files, merged.conflicted_paths),
        "conflicts": [one.to_document() for one in merged.conflicts],
        "recordConflictHistoryOwner": record_owner,
    }
    return plan


def _record_conflicts(paths: tuple[str, ...]) -> list[str]:
    """The conflicted paths that are knowledge records (not sidecars, history or the marker)."""

    directories = tuple(f"knowledge/{name}/" for name in RECORD_DIRECTORIES.values())
    return [path for path in paths if path.startswith(directories)]


def _card_counts(
    converted: Mapping[str, Mapping[str, bytes]],
    merged: Mapping[str, bytes | None],
    conflicted: tuple[str, ...],
) -> dict[str, int]:
    """Cards (Markdown) taken from each side, merged cleanly, or left conflicted."""

    counts = {"unchanged": 0, "fromOwn": 0, "fromIncoming": 0, "mergedClean": 0, "conflicted": 0}
    base, own, incoming = converted["base"], converted["own"], converted["incoming"]
    for path in merged:
        if not path.endswith(".md"):
            continue
        before, mine, theirs = base.get(path), own.get(path), incoming.get(path)
        if path in conflicted:
            counts["conflicted"] += 1
        elif mine == theirs:
            counts["unchanged"] += 1
        elif before == mine:
            counts["fromIncoming"] += 1
        elif before == theirs:
            counts["fromOwn"] += 1
        else:
            counts["mergedClean"] += 1
    return counts
