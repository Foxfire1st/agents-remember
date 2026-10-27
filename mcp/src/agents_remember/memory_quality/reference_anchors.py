"""Reference anchors at the exact pair: their state, the census and the repair worklist (MIK-R198).

A code or test reference target carries an *anchor*: the source path, a locator inside it, the Git
blob the anchor was recorded against and the content identity of the located bytes. MIK-R198 rule 5
requires every such anchor to name the blob of its source path **in the exact paired code tree** and
to resolve there to its recorded content identity; rule 1's census and rule 2's worklist are how the
remaining repair is scheduled, and rule 2's fixer is the shipped mechanical one.

This module is one traversal over one knowledge tree and one code tree. It resolves an anchor
through the same product pieces the fixer uses (:func:`located`, :func:`git_blob_id`,
:func:`content_identity`, :func:`blob_lines`), so a state reported here is the state the guard and
the fixer see, never a parallel reading of the source.

The classes are the requirement's own:

* ``current`` -- the anchor names the tree's blob and its bytes are the recorded content;
* ``blob`` -- class (a): the located bytes are the recorded content, the blob moved. Repair is the
  recorded blob alone (a whole-file anchor qualifies only when its whole-file content matches);
* ``range`` -- class (b): the recorded content sits at exactly one other same-length line range of
  the same file. Repair is that locator and the blob;
* ``judgment`` -- class (c): the recorded content is gone, ambiguous, or the locator or path does
  not resolve. The fixer refuses it (rule 2) and a curator reads the source (rule 3).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from agents_remember.errors import SourceFileUnavailableError

# The validator package's ``__init__`` imports this module's rule module, so a module-scope import
# of its submodules would be a cycle: ``reference_anchors`` could not be imported before the
# validator. The three runtime helpers are therefore imported where they are used, and the tree
# types are typing-only.
if TYPE_CHECKING:
    from agents_remember.memory_quality.knowledge_validator.parsed import SidecarFile
    from agents_remember.memory_quality.knowledge_validator.trees import CodeTree, KnowledgeTree
from agents_remember.memory_quality.reference_state import git_blob_id, located, refreshed_anchor
from agents_remember.models.knowledge_files.anchor_content import (
    blob_lines,
    content_identity,
)
from agents_remember.models.knowledge_files.canonical import parse_json
from agents_remember.models.knowledge_files.shapes import AnchorTarget
from agents_remember.models.knowledge_files.sidecars import FileSidecar, RouteSidecar

CENSUS_SCHEMA: Final = "ar-reference-census/v1"
REFERENCE_ANCHOR_RULE: Final = "R198.1-reference-anchor"

CURRENT: Final = "current"
STALE_BLOB: Final = "stale:blob"
MOVED_RANGE: Final = "moved:range"
STALE_CONTENT: Final = "stale:content"
STALE_AMBIGUOUS: Final = "stale:ambiguous"
STALE_UNRESOLVED: Final = "stale:unresolved"
STALE_PATH_ABSENT: Final = "stale:path-absent"

REPAIR_NONE: Final = "none"
REPAIR_BLOB: Final = "blob"
REPAIR_RANGE: Final = "range"
REPAIR_JUDGMENT: Final = "judgment"
_REPAIRS: Final = {CURRENT: REPAIR_NONE, STALE_BLOB: REPAIR_BLOB, MOVED_RANGE: REPAIR_RANGE}

_PATH_ABSENT_REASON: Final = "the paired code tree holds no file at this path"
_UNRESOLVED_REASON: Final = "the recorded locator does not resolve in the paired code tree"
_NO_RANGE_REASON: Final = "no identical same-length line range in the same file"
_FILE_CAPTION: Final = "the whole-file content identity differs"


def _parse_tree(tree: Any) -> Any:
    """The validator's sidecar parser, imported here so this module stays importable first."""

    from agents_remember.memory_quality.knowledge_validator.parsed import (  # noqa: PLC0415
        parse_tree,
    )

    return parse_tree(tree)


def _parse_document(document: dict[str, Any]) -> Any:
    """The validator's sidecar parser for one document; imported lazily for the same reason."""

    from agents_remember.memory_quality.knowledge_validator.parsed import (  # noqa: PLC0415
        parse_document,
    )

    return parse_document(document)


@dataclass(frozen=True)
class AnchorObservation:
    """One reference anchor's state at one exact pair, and what closes it (MIK-R198 rules 1-2)."""

    sidecar: str
    field: str
    kind: str
    path: str
    locator: str
    recorded_blob: str
    actual_blob: str | None
    recorded_content: str
    actual_content: str | None
    """The content identity the anchor recorded, and what the locator resolves to now (rule 1)."""
    state: str
    repair: str
    reason: str

    @property
    def current(self) -> bool:
        return self.state == CURRENT

    def to_document(self) -> dict[str, Any]:
        return {
            "sidecar": self.sidecar,
            "field": self.field,
            "kind": self.kind,
            "path": self.path,
            "locator": self.locator,
            "recordedBlob": self.recorded_blob,
            "actualBlob": self.actual_blob,
            "recordedContent": self.recorded_content,
            "actualContent": self.actual_content,
            "state": self.state,
            "repair": self.repair,
            "reason": self.reason,
        }


def render_locator(locator: dict[str, Any]) -> str:
    """A locator as one comparable line: ``file``, ``symbol:<name>`` or ``line_range:<a>-<b>``."""

    kind = str(locator.get("kind"))
    if kind == "symbol":
        return f"symbol:{locator.get('name')}"
    if kind == "line_range":
        return f"line_range:{locator.get('start')}-{locator.get('end')}"
    return kind


def _range_matches(data: bytes, content: str, length: int) -> list[int]:
    """Every start whose ``length`` lines carry ``content`` (the fixer's own rule, counted)."""

    lines = blob_lines(data)
    return [
        start
        for start in range(1, len(lines) - length + 2)
        if content_identity(b"".join(lines[start - 1 : start - 1 + length])) == content
    ]


def observe(
    sidecar: str, field: str, target: AnchorTarget, path: str, source: CodeTree
) -> AnchorObservation:
    """Resolve one reference anchor against ``source`` and classify what closes it.

    The repair decision is the **shipped rule's**, not a second opinion: where a mechanical field
    moved, :func:`reference_state.refreshed_anchor` is the function the fixer runs, so the census can
    promise mechanical repair only where that function returns one. The states name what is left for
    judgment -- ambiguous, no identical range, changed content, an unresolvable locator, an absent path
    -- without ever relabelling uncertainty as a false claim.
    """

    anchor = target.anchor
    kind = target.kind
    locator = anchor.locator.model_dump()
    recorded, recorded_content = anchor.blob, anchor.content
    try:
        data = source.read(path)
    except SourceFileUnavailableError as error:
        return AnchorObservation(
            sidecar,
            field,
            kind,
            path,
            render_locator(locator),
            recorded,
            None,
            recorded_content,
            None,
            "not-indexed",
            REPAIR_JUDGMENT,
            str(error),
        )
    if data is None:
        return AnchorObservation(
            sidecar,
            field,
            kind,
            path,
            render_locator(locator),
            recorded,
            None,
            recorded_content,
            None,
            STALE_PATH_ABSENT,
            REPAIR_JUDGMENT,
            _PATH_ABSENT_REASON,
        )
    actual = git_blob_id(data)
    found = located(path, data, locator)
    actual_content = content_identity(found) if found is not None else None
    if actual_content == recorded_content:
        if actual == recorded:
            return AnchorObservation(
                sidecar,
                field,
                kind,
                path,
                render_locator(locator),
                recorded,
                actual,
                recorded_content,
                actual_content,
                CURRENT,
                REPAIR_NONE,
                "names the paired tree's blob and its recorded content",
            )
        return AnchorObservation(
            sidecar,
            field,
            kind,
            path,
            render_locator(locator),
            recorded,
            actual,
            recorded_content,
            actual_content,
            STALE_BLOB,
            REPAIR_BLOB,
            "the located bytes are the recorded content; re-record the blob",
        )
    refreshed = refreshed_anchor(
        path, {"blob": recorded, "content": recorded_content, "locator": locator}, data
    )
    if refreshed is not None:
        moved = refreshed["locator"]
        return AnchorObservation(
            sidecar,
            field,
            kind,
            path,
            render_locator(locator),
            recorded,
            actual,
            recorded_content,
            actual_content,
            MOVED_RANGE,
            REPAIR_RANGE,
            f"the shipped repair rule re-records it at {render_locator(moved)}",
        )
    state, reason = _judged(data, locator, recorded_content, found=found)
    return AnchorObservation(
        sidecar,
        field,
        kind,
        path,
        render_locator(locator),
        recorded,
        actual,
        recorded_content,
        actual_content,
        state,
        _REPAIRS.get(state, REPAIR_JUDGMENT),
        reason,
    )


def _judged(
    data: bytes, locator: dict[str, Any], content: str, *, found: bytes | None
) -> tuple[str, str]:
    """Name a stale anchor the shipped repair rule refuses (MIK-R198 rule 2)."""

    if locator.get("kind") == "file":
        return STALE_CONTENT, _FILE_CAPTION
    if locator.get("kind") != "line_range":
        return _unlocated(found, "the located bytes differ from the recorded content")
    return _judged_range(data, locator, content, found=found)


def _unlocated(found: bytes | None, reason: str) -> tuple[str, str]:
    """A judgment state for a locator that does or does not resolve."""

    return (STALE_UNRESOLVED, _UNRESOLVED_REASON) if found is None else (STALE_CONTENT, reason)


def _judged_range(
    data: bytes, locator: dict[str, Any], content: str, *, found: bytes | None
) -> tuple[str, str]:
    """Name a stale line-range anchor the shipped repair rule refuses."""

    length = int(locator["end"]) - int(locator["start"]) + 1
    matches = _range_matches(data, content, length)
    if len(matches) > 1:
        named = ", ".join(str(start) for start in matches[:5])
        return STALE_AMBIGUOUS, f"{len(matches)} identical same-length ranges (starts {named})"
    if matches:
        return STALE_CONTENT, (
            "the recorded content sits at one identical range, but the shipped repair rule refreshes "
            "no anchor whose blob already matches: re-record it from the source read"
        )
    return _unlocated(found, _NO_RANGE_REASON)


def reference_targets(sidecar: SidecarFile) -> Iterator[tuple[str, AnchorTarget]]:
    """Every code or test reference target of one sidecar, with its ``references.N.targets.I`` field."""

    for number, reference in sidecar.sidecar.references.items():
        for index, target in enumerate(reference.targets):
            if isinstance(target, AnchorTarget):
                yield f"references.{number}.targets.{index}", target


def observations(tree: KnowledgeTree, source: CodeTree) -> tuple[AnchorObservation, ...]:
    """Every reference anchor of ``tree``, resolved against ``source`` (one traversal)."""

    return observations_of(_sidecars(tree), source)


def observations_of(
    sidecars: Iterable[SidecarFile], source: CodeTree
) -> tuple[AnchorObservation, ...]:
    """Every reference anchor of already-parsed ``sidecars``, resolved against ``source``.

    A validator route hands the sidecars it has already parsed, so a whole-tree run parses the
    knowledge tree once instead of once per rule.
    """

    sites: list[tuple[str, str, AnchorTarget, str]] = []
    for sidecar in sidecars:
        own = sidecar.sidecar.path
        for field, target in reference_targets(sidecar):
            sites.append((sidecar.path, field, target, target.anchor.path or own))
    prefetch = getattr(source, "prefetch", None)
    if prefetch is not None:
        prefetch({path for *_, path in sites})
    return tuple(
        observe(sidecar, field, target, path, source) for sidecar, field, target, path in sites
    )


def _sidecars(tree: KnowledgeTree) -> tuple[SidecarFile, ...]:
    return tuple(
        sidecar
        for sidecar in _parse_tree(tree).sidecars
        if isinstance(sidecar.sidecar, FileSidecar | RouteSidecar)
    )


def unreadable_sidecars(tree: KnowledgeTree) -> tuple[str, ...]:
    """Every onboarding sidecar the product parser cannot read as one (rule 1 names them)."""

    unreadable: list[str] = []
    for path in sorted(tree.files):
        if not (path.startswith("onboarding/") and path.endswith(".json")):
            continue
        if path.endswith(".index.json"):
            continue
        try:
            document = parse_json(tree.files[path].decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            unreadable.append(path)
            continue
        if not isinstance(document, dict):
            unreadable.append(path)
            continue
        try:
            parsed = _parse_document(document)
        except ValueError:
            unreadable.append(path)
            continue
        if not isinstance(parsed, FileSidecar | RouteSidecar):
            unreadable.append(path)
    return tuple(unreadable)


def rejected_sidecars(tree: KnowledgeTree) -> tuple[str, ...]:
    """Every readable onboarding sidecar the tree parser refused as a sidecar of this tree.

    A sidecar can parse as JSON, hold a well-formed document, and still be no sidecar of this tree:
    it lives at a path its own declaration does not name (``onboarding/pkg/wrong.py.json`` declaring
    ``pkg/module.py``). The tree parser records that as a problem and does not list the file among
    its sidecars, so its targets would otherwise leave the census denominator silently while the
    census still answered that it was complete. Rule 1 asks for the opposite: an input the parser
    rejected is named as an incomplete input rather than dropped.
    """

    parsed = _parse_tree(tree)
    # Physical paths: a file is an accepted sidecar because it *lives* at an accepted sidecar's path.
    # Comparing the parser's problems against the sidecars' *declared* paths let an accepted sidecar
    # whose declaration aliases the rejected file's physical path suppress that rejection, and a
    # readable-but-mislocated input disappeared from the accounting again (L191-A3-02).
    named = {sidecar.path for sidecar in parsed.sidecars}
    rejected = {
        problem.path
        for problem in parsed.problems
        if problem.path.startswith("onboarding/")
        and problem.path.endswith(".json")
        and not problem.path.endswith(".index.json")
        and problem.path not in named
    }
    return tuple(sorted(rejected))


def _source_route(path: str) -> str:
    """The source route a target's path belongs to: its first segment (``mcp``, ``docs``, …)."""

    parts = [part for part in path.split("/") if part]
    return parts[0] if parts else "(none)"


def census(tree: KnowledgeTree, source: CodeTree, *, memory: str | None = None) -> dict[str, Any]:
    """The rule-1 census of one tree at one paired code tree: states, classes and worklist."""

    observed = observations(tree, source)
    unreadable = unreadable_sidecars(tree)
    rejected = rejected_sidecars(tree)
    invalid = tuple(sorted({*unreadable, *rejected}))
    states = Counter(item.state for item in observed)
    repairs = Counter(item.repair for item in observed)
    per_sidecar = Counter(item.sidecar for item in observed)
    per_route: dict[str, Counter[str]] = {}
    for item in observed:
        per_route.setdefault(_source_route(item.path), Counter())[item.state] += 1
    documents = _documents(observed, tree)
    current = states.get(CURRENT, 0)
    return {
        "ok": not invalid and current == len(observed),
        "schema": CENSUS_SCHEMA,
        "pair": {"code": source.label, "memory": memory or tree.label},
        "denominator": len(observed),
        "currentCount": current,
        "debtCount": len(observed) - current,
        "states": dict(sorted(states.items())),
        "repairs": dict(sorted(repairs.items())),
        "targets": [item.to_document() for item in observed],
        "sidecars": dict(sorted(per_sidecar.items())),
        "sidecarCount": len(per_sidecar),
        # Rule 1's source-route accounting: every route's targets, by state, reconciling to the
        # denominator above.
        "routes": {
            route: {
                "targets": sum(states_by_state.values()),
                "states": dict(sorted(states_by_state.items())),
            }
            for route, states_by_state in sorted(per_route.items())
        },
        "routeCount": len(per_route),
        "documents": documents["repairable"],
        "judgmentDocuments": documents["judgment"],
        "unreadableSidecars": list(unreadable),
        # A readable sidecar the tree parser refused is an incomplete input, not a silent absence:
        # it is named here and it makes the census answer that it is not complete.
        "rejectedSidecars": list(rejected),
        "invalidInputCount": len(invalid),
    }


def _documents(
    observed: tuple[AnchorObservation, ...], tree: KnowledgeTree
) -> dict[str, list[str]]:
    """The card names (onboarding-relative) that hold repairable and judgment targets (rule 2).

    A sidecar without a card cannot be named by the citation work order, which takes cards; it is
    listed separately so the curator's route to it is chosen rather than assumed.
    """

    repairable: set[str] = set()
    judgment: set[str] = set()
    for item in observed:
        if item.current:
            continue
        bucket = repairable if item.repair in {REPAIR_BLOB, REPAIR_RANGE} else judgment
        bucket.add(item.sidecar)
    return {
        "repairable": _card_names(repairable, tree),
        "judgment": _card_names(judgment, tree),
    }


def _card_names(sidecars: set[str], tree: KnowledgeTree) -> list[str]:
    """Split ``sidecars`` into their onboarding-relative card names and their cardless remainder."""

    cards: list[str] = []
    cardless: list[str] = []
    for sidecar in sorted(sidecars):
        card = f"{sidecar.removesuffix('.json')}.md"
        if card in tree.files:
            cards.append(card.removeprefix("onboarding/"))
        else:
            cardless.append(sidecar)
    return [*cards, *cardless]


def census_for_memory(memory_root: Path, code_root: Path) -> dict[str, Any]:
    """The census of one converted memory directory against one code directory (the CLI's run)."""

    # cycle-free import: see the TYPE_CHECKING note at the module's imports
    from agents_remember.memory_quality.knowledge_validator.trees import (  # noqa: PLC0415
        CodeDirectory,
        knowledge_tree_from_directory,
    )

    tree = knowledge_tree_from_directory(memory_root)
    source = CodeDirectory(label=code_root.as_posix(), root=code_root)
    return census(tree, source, memory=memory_root.as_posix())


__all__ = [
    "CENSUS_SCHEMA",
    "CURRENT",
    "MOVED_RANGE",
    "REFERENCE_ANCHOR_RULE",
    "REPAIR_BLOB",
    "REPAIR_JUDGMENT",
    "REPAIR_RANGE",
    "STALE_AMBIGUOUS",
    "STALE_BLOB",
    "STALE_CONTENT",
    "STALE_PATH_ABSENT",
    "STALE_UNRESOLVED",
    "AnchorObservation",
    "census",
    "census_for_memory",
    "observations",
    "observe",
    "reference_targets",
    "render_locator",
    "unreadable_sidecars",
]
