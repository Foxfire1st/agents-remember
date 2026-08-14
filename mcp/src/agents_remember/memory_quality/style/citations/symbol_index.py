"""Resolve every exact anchor location through one immutable source snapshot index.

The persistent index derives each file's definitions, occurrences, headings, quote streams,
and TypeScript call extents once. Independent checker/fixer processes query that generation;
``locate_uncached`` retains the direct resolver as the parity oracle. Memory documents are
excluded because they repeat the anchors they describe.

A name defined once and mentioned by callers resolves to its definition. For unparsed
languages, uniqueness falls back to occurrence in exactly one file. Definition locations
are retained even when detail output is capped. Matching is exact; there is no similarity
or rename inference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agents_remember.memory_quality.style.citations import extents, model, source_index
from agents_remember.memory_quality.style.citations.resolution import Trees

SKIPPED_DIRECTORIES = source_index.SKIPPED_DIRECTORIES
SKIPPED_SUFFIXES = source_index.SKIPPED_SUFFIXES
# How many MENTIONING files one anchor materialises. Definitions are never capped -- the
# tiebreaker reads them, and a cap that hid the one definition would refuse every move of a
# widely-called symbol. Beyond it the COUNT stays exact, so a vacuous anchor reports its own
# vacuity as a number rather than as forty thousand lines of evidence.
LOCATION_FILE_LIMIT = 20


@dataclass(frozen=True)
class Location:
    """One range in one file that satisfies an anchor."""

    path: str
    extent: extents.Extent

    @property
    def written(self) -> str:
        return f"{self.path}:{self.extent.start}-{self.extent.end}"


@dataclass(frozen=True)
class Sightings:
    """Where one anchor exists in the code tree, and how much of it fitted."""

    locations: tuple[Location, ...] = ()
    files: int = 0
    defining_files: int = 0
    truncated: bool = False

    @property
    def definitions(self) -> tuple[Location, ...]:
        return tuple(one for one in self.locations if one.extent.kind == extents.DEFINITION)

    @property
    def unique(self) -> Location | None:
        """The single location a pure MOVE repoints to, or ``None`` when there is a choice.

        A rename and a deletion both land here as ``None``, and that is the whole point:
        nothing links an old name to a new one, so an anchor that resolves nowhere resolves
        nowhere. It is never guessed at from a similar name.

        A MENTION in a PARSED file never resolves a move, even when it is the only one in
        the tree. Measured live: probing a deleted name repointed a claim at the DOCSTRING
        that discussed it, because prose in a parsed file mentions names as freely as prose
        anywhere. Where a definition could have been found and was not, its absence is the
        answer. The mention is still reported as evidence; it is just not followed. The
        rule is per LANGUAGE rather than per file, so it held for Python only until this
        leaf; TypeScript, TSX and JavaScript now carry it too, and only a language with no
        grammar can still be repaired off a lone mention.
        """
        if self.defining_files == 1:
            return self.definitions[0] if len(self.definitions) == 1 else None
        if self.defining_files or self.files != 1 or len(self.locations) != 1:
            return None
        one = self.locations[0]
        return None if extents.parsed(one.path) else one


@dataclass
class _Batch:
    """One anchor's accumulating result while the walk is in progress."""

    locations: list[Location] = field(default_factory=list)
    files: int = 0
    defining_files: int = 0
    mentioning: int = 0

    def record(self, path: str, found: tuple[extents.Extent, ...]) -> None:
        self.files += 1
        if found[0].kind == extents.DEFINITION:
            self.defining_files += 1
            self.locations.extend(Location(path=path, extent=one) for one in found)
            return
        self.mentioning += 1
        if self.mentioning <= LOCATION_FILE_LIMIT:
            self.locations.extend(Location(path=path, extent=one) for one in found)

    def sightings(self) -> Sightings:
        return Sightings(
            locations=tuple(self.locations),
            files=self.files,
            defining_files=self.defining_files,
            truncated=self.mentioning > LOCATION_FILE_LIMIT,
        )


def locate(
    anchors: tuple[model.Anchor, ...],
    trees: Trees,
    *,
    index: source_index.RepositoryIndex | None = None,
) -> dict[model.Anchor, Sightings]:
    """Every location from the shared snapshot index, preserving direct-resolver order."""
    unique = tuple(dict.fromkeys(anchors))
    if not unique:
        return {}
    if index is None:
        with source_index.open_repository_index(trees) as acquired:
            return _located(unique, acquired)
    return _located(unique, index)


def _located(
    anchors: tuple[model.Anchor, ...], index: source_index.RepositoryIndex
) -> dict[model.Anchor, Sightings]:
    batches = {anchor: _Batch() for anchor in anchors}
    for anchor, files in index.locations(anchors).items():
        batch = batches[anchor]
        for file in files:
            batch.record(file.path, file.extents)
    return {anchor: batch.sightings() for anchor, batch in batches.items()}


def locate_uncached(
    anchors: tuple[model.Anchor, ...], trees: Trees
) -> dict[model.Anchor, Sightings]:
    """Direct source resolver retained as the semantic parity oracle for the index."""
    batches = {anchor: _Batch() for anchor in dict.fromkeys(anchors)}
    if batches:
        for path, relative in walk(trees):
            _visit(path, relative, batches)
    return {anchor: batch.sightings() for anchor, batch in batches.items()}


def walk(trees: Trees) -> list[tuple[Path, str]]:
    """Every readable code file, with the path text a ``Source`` would spell it as.

    The memory tree is excluded even when it sits INSIDE the code tree, which is what
    internal memory does: a card holds its own anchor text, so indexing cards makes every
    anchor ambiguous with the claim that names it.
    """
    return source_index.code_files(trees)


def _visit(path: Path, relative: str, batches: dict[model.Anchor, _Batch]) -> None:
    """Record every anchor this one file holds, reading and deriving it at most once."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    view = extents.FileView(path=relative, lines=lines)
    for anchor, batch in batches.items():
        found = view.extents(anchor)
        if found:
            batch.record(relative, found)


def described(anchor: model.Anchor, sightings: Sightings) -> str:
    """The tree-wide half of a finding: every location, or the fact that there are none."""
    if not sightings.files:
        return (
            f"{anchor.written} exists NOWHERE in the code tree, so this is a rename or a "
            f"deletion rather than a move -- the claim itself is what needs re-reading, not "
            f"the pointer"
        )
    shown = ", ".join(one.written for one in sightings.locations)
    scope = f"{sightings.files} file(s) hold it"
    if sightings.truncated:
        return f"{scope}; every definition and the first {LOCATION_FILE_LIMIT} mentions are {shown}"
    return f"{scope}, at {shown}"
