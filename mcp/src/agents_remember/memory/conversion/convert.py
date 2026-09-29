"""Convert one memory tree, and its knowledge database, into the text format (MIK-R24 rules 1-6).

:func:`convert_memory` is a pure function of four inputs (rule 6): the memory tree's files, its
database, the code objects it resolves in (each card's anchor commit and every exported claim's
recorded blob, through :class:`CodeObjects`) and the conversion-format version. The paired code
commit is a fifth argument only for the rule 2 fallback cards, whose anchor commit is missing.

* A tree that already holds ``knowledge/layout.json`` is returned unchanged (a no-op).
* A version this build does not reproduce byte for byte is refused (:class:`ConversionVersionError`).
* The whole converted tree is validated (MIK-R22, standalone-conversion mode) before anything is
  returned for writing; a failing conversion writes nothing and names every failing file.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal

from agents_remember.memory.conversion.cards import SplitCard, render_card, split_card
from agents_remember.memory.conversion.citations import RowContext, TargetTally, row_reference
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory.conversion.legacy_db import ExportResult, export_database
from agents_remember.memory_quality.knowledge_validator.trees import KnowledgeTree
from agents_remember.memory_quality.knowledge_validator.validator import validate_tree
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.knowledge_files.documents import (
    LAYOUT_MARKER_PATH,
    ONBOARDING_ROOT,
    file_sidecar_path,
    parse_document,
    route_sidecar_path,
)
from agents_remember.models.knowledge_files.sidecars import (
    FILE_SIDECAR_SCHEMA,
    LAYOUT_MARKER_SCHEMA,
    ROOT_ROUTE_PATH,
    ROUTE_SIDECAR_SCHEMA,
)

CONVERSION_FORMAT_VERSION: Final = "1"
"""The one conversion-format version this build reproduces byte for byte (it pins the extractor).

``test_conversion_format_version_1_reproduces_its_pinned_bytes`` pins version 1's complete output
over the fixture tree. Any change that alters it -- in the cards, the citations, the export, the
marker escaping or the shipped extractor -- ships as a new version, never as version 1 (rule 6).
"""
SUPPORTED_VERSIONS: Final = frozenset({CONVERSION_FORMAT_VERSION})
OVERVIEW: Final = "overview.md"
_COMMIT: Final = re.compile(r"\b[0-9a-f]{7,64}\b")


class ConversionVersionError(ValueError):
    """The requested conversion-format version is not one this build reproduces."""


class ConversionRefused(ValueError):
    """The conversion failed validation or could not read an input; nothing was written."""

    def __init__(self, message: str, failing: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.failing = failing


@dataclass(frozen=True)
class MemoryInput:
    """The memory tree to convert: its knowledge and onboarding files, and its database."""

    label: str
    files: Mapping[str, bytes]
    database: Path | None

    @property
    def converted(self) -> bool:
        return LAYOUT_MARKER_PATH in self.files


@dataclass
class ConversionOutcome:
    """A finished conversion: the complete converted tree, the files it changed, and the report."""

    state: Literal["converted", "already-converted"]
    files: dict[str, bytes]
    changed: dict[str, bytes]
    report: dict[str, Any] = field(default_factory=dict)


def require_version(version: str) -> str:
    """Refuse a conversion-format version this build does not reproduce (rule 6)."""

    if version not in SUPPORTED_VERSIONS:
        raise ConversionVersionError(
            f"conversion-format version {version!r} is not reproduced by this build, which "
            f"reproduces only {sorted(SUPPORTED_VERSIONS)}; refusing rather than converting "
            "differently"
        )
    return version


def _own_path(card: str) -> str | None:
    relative = card.removeprefix(f"{ONBOARDING_ROOT}/")
    if PurePosixPath(relative).name == OVERVIEW:
        return None
    return relative.removesuffix(".md")


def route_of(card: str) -> str:
    """The route directory of an ``overview.md`` (``.`` for the repository root route)."""

    parent = PurePosixPath(card.removeprefix(f"{ONBOARDING_ROOT}/")).parent.as_posix()
    return ROOT_ROUTE_PATH if parent in {"", "."} else parent


def nearest_overview(card: str, overviews: frozenset[str]) -> str | None:
    """The overview governing ``card``: the nearest ancestor route, excluding the card itself."""

    directory = PurePosixPath(card).parent
    while True:
        candidate = (directory / OVERVIEW).as_posix()
        if candidate != card and candidate in overviews:
            return candidate
        if directory.as_posix() in {ONBOARDING_ROOT, ".", ""}:
            return None
        directory = directory.parent


def _declared_overview(card: str, value: str, overviews: frozenset[str]) -> str:
    """Resolve a ``governingOverview`` value the way a reader would, relative to the card first."""

    value = value.strip().strip("`").strip()
    base = PurePosixPath(card).parent
    candidates = [
        _normal(base / value),
        _normal(PurePosixPath(ONBOARDING_ROOT) / value),
        _normal(PurePosixPath(value)),
    ]
    return next((one for one in candidates if one in overviews), candidates[0])


def _normal(path: PurePosixPath) -> str:
    parts: list[str] = []
    for part in path.parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part != ".":
            parts.append(part)
    return "/".join(parts)


@dataclass
class _Tally:
    cards: int = 0
    overviews: int = 0
    real_rows: int = 0
    placeholder_rows: int = 0
    references: int = 0
    history_bytes: int = 0
    fallback_cards: list[str] = field(default_factory=list)
    governing_mismatch: list[dict[str, str]] = field(default_factory=list)
    path_mismatch: list[dict[str, str]] = field(default_factory=list)
    residual_metadata: Counter[str] = field(default_factory=Counter)
    escaped_files: list[str] = field(default_factory=list)
    markdownless: list[str] = field(default_factory=list)
    abbreviated: list[dict[str, Any]] = field(default_factory=list)
    stray_rows: list[dict[str, Any]] = field(default_factory=list)
    entry_states: dict[str, int] = field(default_factory=dict)
    targets: TargetTally = field(default_factory=TargetTally)


_FULL_COMMIT: Final = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def _anchor_commit(
    card: str, split: SplitCard, objects: CodeObjects, paired_commit: str, tally: _Tally
) -> tuple[str, bool]:
    """The card's anchor commit (rule 2), and whether it fell back to the paired commit.

    A full ID is looked up exactly. An abbreviated one is resolved against every commit of the store
    with that prefix: exactly one resolves it, none is a missing commit (the listed fallback), and
    more than one refuses the conversion, naming the card -- never a silent fallback. Every
    abbreviated value is listed in the report.
    """

    declared = split.metadata.get("lastVerifiedCommitHash", "")
    match = _COMMIT.search(declared)
    if match is None:
        return paired_commit, True
    value = match.group(0)
    if _FULL_COMMIT.match(value):
        commit = objects.commit(value)
        return (commit, False) if commit is not None else (paired_commit, True)
    candidates = objects.commits_with_prefix(value)
    tally.abbreviated.append(
        {"card": card, "lastVerifiedCommitHash": value, "resolved": list(candidates)}
    )
    if len(candidates) > 1:
        raise ConversionRefused(
            f"{card}: lastVerifiedCommitHash {value!r} is ambiguous in the code object store "
            f"({', '.join(candidates)}); write the full commit ID",
            (card,),
        )
    return (candidates[0], False) if candidates else (paired_commit, True)


def _prefetch(cards: Mapping[str, tuple[SplitCard, str]], objects: CodeObjects) -> None:
    wanted: set[str] = set()
    for split, commit in cards.values():
        tree = objects.tree(commit)
        for row in split.rows:
            for citation in row.claim.citations:
                blob = tree.get(citation.path)
                if blob is not None:
                    wanted.add(blob)
            for segment in row.claim.malformed:
                blob = tree.get(segment.strip())
                if blob is not None:
                    wanted.add(blob)
    objects.prefetch(wanted)


def _document_bytes(document: Mapping[str, Any], path: str) -> bytes:
    try:
        parse_document(document)
    except ValueError as error:
        raise ConversionRefused(f"{path}: {error}", (path,)) from error
    return canonical_text(document).encode("utf-8")


def _convert_cards(
    memory: MemoryInput, objects: CodeObjects, paired_commit: str, tally: _Tally
) -> tuple[dict[str, bytes], dict[str, dict[str, Any]]]:
    cards = sorted(
        path
        for path in memory.files
        if path.startswith(f"{ONBOARDING_ROOT}/") and path.endswith(".md")
    )
    overviews = frozenset(path for path in cards if PurePosixPath(path).name == OVERVIEW)
    split_cards: dict[str, tuple[SplitCard, str]] = {}
    for card in cards:
        split = split_card(memory.files[card].decode("utf-8"))
        commit, fallback = _anchor_commit(card, split, objects, paired_commit, tally)
        if fallback and split.rows:
            tally.fallback_cards.append(card)
        split_cards[card] = (split, commit)
    _prefetch(split_cards, objects)
    markdown: dict[str, bytes] = {}
    sidecars: dict[str, dict[str, Any]] = {}
    for card, (split, commit) in split_cards.items():
        _audit_card(card, split, overviews, tally)
        own = _own_path(card)
        context = RowContext(
            card=card,
            own_path=own,
            tree=objects.tree(commit),
            objects=objects,
            route_sidecar=own is None,
        )
        references = {
            str(number): row_reference(row, context, tally.targets)
            for number, row in enumerate(split.rows, start=1)
        }
        tally.references += len(references)
        text, escaped = render_card(split, list(range(1, len(split.rows) + 1)))
        if escaped:
            tally.escaped_files.append(f"{card} ({escaped})")
        markdown[card] = text.encode("utf-8")
        if own is None:
            if references:
                route = route_of(card)
                sidecars[route_sidecar_path(route)] = {
                    "schema": ROUTE_SIDECAR_SCHEMA,
                    "path": route,
                    "references": references,
                }
        elif references:
            sidecars[file_sidecar_path(own)] = {
                "schema": FILE_SIDECAR_SCHEMA,
                "path": own,
                "references": references,
                "realizes": [],
            }
    return markdown, sidecars


def _audit_card(card: str, split: SplitCard, overviews: frozenset[str], tally: _Tally) -> None:
    own = _own_path(card)
    tally.cards += 1
    tally.overviews += own is None
    tally.real_rows += len(split.rows)
    tally.placeholder_rows += split.placeholders
    tally.history_bytes += split.update_history_bytes
    tally.residual_metadata.update(split.residual_metadata)
    if split.stray_rows:
        tally.stray_rows.append({"card": card, "lines": list(split.stray_rows)})
    declared = split.metadata.get("governingOverview")
    if declared:
        nearest = nearest_overview(card, overviews)
        resolved = _declared_overview(card, declared, overviews)
        if resolved != nearest:
            tally.governing_mismatch.append(
                {"card": card, "governingOverview": declared, "nearest": nearest or "none"}
            )
    recorded = split.metadata.get("path")
    if own is not None and recorded and recorded != own:
        tally.path_mismatch.append({"card": card, "path": recorded})


def _merge_entries(
    sidecars: dict[str, dict[str, Any]],
    export: ExportResult,
    cards: frozenset[str],
) -> list[str]:
    """Put each exported entry in its file's sidecar; a path without a card gets one without Markdown."""

    markdownless: list[str] = []
    for path, entries in sorted(export.entries.items()):
        location = file_sidecar_path(path)
        sidecar = sidecars.setdefault(
            location,
            {"schema": FILE_SIDECAR_SCHEMA, "path": path, "references": {}, "realizes": []},
        )
        sidecar["realizes"] = [*sidecar["realizes"], *entries]
        if f"{ONBOARDING_ROOT}/{path}.md" not in cards:
            markdownless.append(location)
    return markdownless


def _entry_states(export: ExportResult, objects: CodeObjects, commit: str) -> dict[str, int]:
    """Exported entries by MIK-R03 state at the conversion's code tree (``current``/``stale``)."""

    tree = objects.tree(commit)
    states: Counter[str] = Counter()
    for path, entries in export.entries.items():
        blob = tree.get(path)
        for entry in entries:
            anchor = entry["anchor"]
            if blob is None:
                states["stale:path-absent"] += 1
            elif (
                blob == anchor["blob"]
                or objects.content(blob, anchor["locator"], path) == anchor["content"]
            ):
                states["current"] += 1
            else:
                states["stale"] += 1
    return dict(sorted(states.items()))


def _changed_files(
    files: Mapping[str, bytes],
    markdown: Mapping[str, bytes],
    documents: Mapping[str, Mapping[str, Any]],
) -> dict[str, bytes]:
    """Every rewritten card and every new document, refusing to overwrite an existing file."""

    changed = {path: text for path, text in markdown.items() if files.get(path) != text}
    for path, document in sorted(documents.items()):
        if path in files:
            raise ConversionRefused(f"{path} already exists in the unconverted tree", (path,))
        changed[path] = _document_bytes(document, path)
    return changed


def _require_valid(label: str, files: Mapping[str, bytes], report: dict[str, Any]) -> None:
    """Validate the whole converted tree (standalone-conversion mode); refuse on any violation."""

    validation = validate_tree(KnowledgeTree(label=label, files=files), conversion=True)
    refusing = [violation for violation in validation.violations if not violation.report_only]
    report_only = Counter(v.rule for v in validation.violations if v.report_only)
    report["validation"] = {
        "refusing": len(refusing),
        "reportOnly": sum(report_only.values()),
        "reportOnlyByRule": dict(sorted(report_only.items())),
    }
    if refusing:
        raise ConversionRefused(
            "the converted tree fails validation; nothing was written:\n"
            + "\n".join(violation.render() for violation in refusing),
            tuple(sorted({violation.path for violation in refusing})),
        )


def convert_memory(
    memory: MemoryInput,
    objects: CodeObjects,
    *,
    paired_commit: str,
    version: str = CONVERSION_FORMAT_VERSION,
) -> ConversionOutcome:
    """Convert ``memory``; refuse an unsupported version or a result that fails validation."""

    require_version(version)
    files = {path: bytes(data) for path, data in memory.files.items()}
    if memory.converted:
        return ConversionOutcome(
            state="already-converted",
            files=files,
            changed={},
            report={"state": "already-converted", "memory": memory.label},
        )
    paired = objects.commit(paired_commit)
    if paired is None:
        raise ConversionRefused(
            f"the paired code commit {paired_commit!r} is not in the object store"
        )
    tally = _Tally()
    markdown, sidecars = _convert_cards(memory, objects, paired, tally)
    export = export_database(memory.database, objects) if memory.database else ExportResult()
    tally.markdownless = _merge_entries(sidecars, export, frozenset(markdown))
    tally.entry_states = _entry_states(export, objects, paired)
    documents = {
        **sidecars,
        **export.records,
        LAYOUT_MARKER_PATH: {"schema": LAYOUT_MARKER_SCHEMA, "conversion": version},
    }
    changed = _changed_files(files, markdown, documents)
    files.update(changed)
    report = _report(memory, tally, export, version)
    report["pairedCodeCommit"] = paired
    report["after"]["files"] = {
        "routeSidecars": sum(1 for path in sidecars if path.endswith("/overview.json")),
        "fileSidecars": sum(1 for path in sidecars if not path.endswith("/overview.json")),
        "records": len(export.records),
        "layoutMarker": 1,
    }
    _require_valid(memory.label, files, report)
    return ConversionOutcome(state="converted", files=files, changed=changed, report=report)


def _report(
    memory: MemoryInput, tally: _Tally, export: ExportResult, version: str
) -> dict[str, Any]:
    targets = tally.targets
    unresolved_reasons = Counter(item["reason"] for item in targets.unresolved)
    return {
        "state": "converted",
        "memory": memory.label,
        "conversion": version,
        "before": {
            "markdownFiles": tally.cards,
            "overviews": tally.overviews,
            "citationRows": tally.real_rows + tally.placeholder_rows,
            "realRows": tally.real_rows,
            "placeholderRows": tally.placeholder_rows,
            "updateHistoryBytes": tally.history_bytes,
        },
        "after": {
            "markdownFiles": tally.cards,
            "references": tally.references,
            "targetsByKind": dict(sorted(targets.by_kind.items())),
            "coveredRanges": targets.covered_ranges,
            "referencesKeepingAnchorText": targets.anchor_texts_kept,
            "unresolvedTargets": len(targets.unresolved),
            "unresolvedByReason": dict(sorted(unresolved_reasons.items())),
            "invariants": export.invariants,
            "families": export.families,
            "realizations": sum(len(entries) for entries in export.entries.values()),
            "revisionDepths": {str(k): v for k, v in sorted(export.revision_depths.items())},
            "realizationStatesAtPairedCommit": tally.entry_states,
            "markdownlessSidecars": sorted(tally.markdownless),
            "realizationsBoundByTopLevelDefinition": sorted(export.disambiguated),
        },
        "fallbackAnchoredCards": sorted(tally.fallback_cards),
        "abbreviatedAnchorCommits": sorted(tally.abbreviated, key=lambda item: item["card"]),
        "strayCitationRows": sorted(tally.stray_rows, key=lambda item: item["card"]),
        "governingOverviewNotNearest": sorted(
            tally.governing_mismatch, key=lambda item: item["card"]
        ),
        "pathMetadataMismatch": sorted(tally.path_mismatch, key=lambda item: item["card"]),
        "residualMetadataKeys": dict(sorted(tally.residual_metadata.items())),
        "escapedMarkerFiles": sorted(tally.escaped_files),
        "unresolved": sorted(
            targets.unresolved, key=lambda item: (item["card"], item["text"], item["reason"])
        ),
    }
