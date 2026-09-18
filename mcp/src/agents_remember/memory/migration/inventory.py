"""The scope inventory: one row per in-scope source artifact, whether or not it has a card.

The inventory is taken over the **scope** -- the code repository's own tracked file set at a frozen
baseline -- and never over the onboarding tree being examined. That direction is the whole reason
this module exists: a corpus-derived list can only enumerate the sources that already have a card,
so the sources with **no onboarding** are exactly the ones it cannot see, and those are the files a
coverage question is asked about. A missing card is therefore a recorded row with
``inventory_state="absent"``, not an omission. The same rule runs the other way: a card whose
declared source is not in scope still gets a row, so a card left behind by a deleted file stays
visible instead of quietly disappearing from the count.

**A row's route is derived from the scope's own repository layout and is recorded.** A route is the
longest declared route directory that is a path prefix of the source path, matched on whole path
segments, so ``mcp/src/x.py`` belongs to route ``mcp`` while ``mcpfoo/x.py`` belongs to no route -- a
bare string prefix would silently file the second under the first. The route spelling is the shipped
``Route`` normalisation, a repository-relative path with no trailing separator, so a route path here
is ``"mcp"`` and never ``"mcp/"``. The route is a *recorded observation about the layout*, never
something read out of a card's content; the declared list is :data:`DEFAULT_ROUTE_DIRECTORIES`, and it
is the repository's own top-level route convention rather than a naming rule this module infers. This
axis is deliberately not the knowledge substrate's route axis: a route slice in the substrate keys on
a *stored* route and on explicit governing-route associations, while this row records where a file was
observed to live, and the two are not interchangeable.

**A bad artifact is a row, not an exception.** Unreadable files and non-UTF-8 content are recorded
against the artifact's own path with the matching outcome and with the observed content that could
not be parsed, because a silent skip and a clean read are indistinguishable in a count. The observed
content is capped (:data:`MAX_UNPARSED_LENGTH`): a durable record must not be able to absorb an
unbounded file.
"""

from __future__ import annotations

import posixpath
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from agents_remember.memory.knowledge.schema_v9 import (
    CENSUS_ARTIFACT_KINDS,
    CENSUS_INVENTORY_STATES,
    CENSUS_PARSE_OUTCOMES,
)
from agents_remember.memory.migration.baseline import FrozenBaseline
from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH

# The policy this module implements, recorded so a count can name the rule that produced it. A
# version is what makes "the rule changed" a comparison-invalidating fact rather than a quiet
# improvement.
INVENTORY_POLICY_VERSION = "scope-inventory/v1"

# The repository's declared route directories: the top-level directories that carry a route in the
# source tree. Declared once here rather than inline at each call site, because the list is a stated
# convention a reader has to be able to find -- and because a top-level directory this list does not
# name belongs to no route, which is a fact a caller should see rather than have guessed for it.
DEFAULT_ROUTE_DIRECTORIES: tuple[str, ...] = (
    "bootstrap",
    "dashboard",
    "dev-skills",
    "docs",
    "examples",
    "mcp",
    "scripts",
    "skills",
)

# The corpus directory whose route-shaped children are the routes this inventory can be asked about.
# A route path is itself the source-tree spelling (``mcp``), while the directory the corpus keeps that
# route's cards in is ``onboarding/mcp``; this is the segment the two spellings are told apart by.
CORPUS_DIRECTORY = "onboarding"

# The artifact-kind and inventory-state values this module's own reading of the closed vocabularies
# records: a source with no card is an artifact the inventory never met, and a card whose source is
# not in scope is a source file that is not there at the baseline. The schema's own tuples are
# asserted against these below, so a value the DDL would refuse cannot be produced here.
ABSENT_ARTIFACT_KIND: str = "other"
ABSENT_INVENTORY_STATE: str = "absent"
ABSENT_PARSE_OUTCOME: str = "unsupported"
PRESENT_INVENTORY_STATE: str = "present"
UNREADABLE_INVENTORY_STATE: str = "unreadable"
UNREADABLE_PARSE_OUTCOME: str = "unreadable"
MARKDOWN_CARD_SUFFIX: str = ".md"
ROUTE_OVERVIEW_STEM: str = "overview"

# Why the cap exists: the observed remainder is stored in a durable record, and a record that could
# absorb an arbitrarily large file would let a single unreadable artifact decide how large the census
# is. It is the prose cap rather than a number invented here, because the remainder is prose.
MAX_UNPARSED_LENGTH: int = PROSE_MAX_LENGTH

# The read-state vocabulary is the closed parse-outcome vocabulary used as a *reader's* report: a
# reader says which outcome it reached and the row derives its own state from that. The literal
# mirrors the schema's tuple value for value, and the closes below fail loudly if either moves.
ReadState = Literal["parsed", "unparsed", "unsupported", "unreadable"]

# The successful read state: the one state whose row carries no unparsed remainder, and the one the
# default reader reports, so both are stated against a single name rather than a repeated literal.
READ_STATE_PARSED: ReadState = "parsed"

# The one read state that needs no observed remainder: the bytes decoded and there was nothing left
# unread. Every other state reports the content it could not parse -- including a successful read
# whose *parsing* found nothing it understood, which is a remainder rather than an absence of one.
_NOTHING_TO_REPORT: frozenset[str] = frozenset({"parsed"})

# What the observed remainder carries when there is no text to carry: the artifact was read and had no
# content, or it failed in a way that reported no text. Both are stated as the read-outcome fact rather
# than as an empty string, because a row whose outcome is not a parse must report what was not parsed.
EMPTY_ARTIFACT_CONTENT = "the artifact was read as empty content"
UNREADABLE_CONTENT_FALLBACK = "the artifact could not be read for a reason that reported no text"

# ``posixpath`` calls the current directory ``"."``; it is the spelling that means "no segments",
# which is what a card outside every declared route has.
POSIX_CURRENT_DIRECTORY = "."

# The closes the two vocabularies meet under. A vocabulary that gained a value in the schema would
# otherwise become a value this module records without ever being able to write it.
assert ABSENT_ARTIFACT_KIND in CENSUS_ARTIFACT_KINDS
assert ABSENT_INVENTORY_STATE in CENSUS_INVENTORY_STATES
assert PRESENT_INVENTORY_STATE in CENSUS_INVENTORY_STATES
assert UNREADABLE_INVENTORY_STATE in CENSUS_INVENTORY_STATES
assert ABSENT_PARSE_OUTCOME in CENSUS_PARSE_OUTCOMES
assert UNREADABLE_PARSE_OUTCOME in CENSUS_PARSE_OUTCOMES


@dataclass(frozen=True)
class ScopeEntry:
    """One in-scope source file: its repository-relative path and the route it belongs to."""

    source_path: str
    route_path: str


@dataclass(frozen=True)
class InventoryRow:
    """One inventory row: one in-scope artifact and the outcome of looking for its onboarding."""

    artifact_path: str
    artifact_kind: str
    declared_source_path: str | None
    source_path: str | None
    route_path: str
    inventory_state: str
    observed_doc_type: str | None
    observed_route_path: str | None
    parsing_outcome: str
    unparsed_content: str | None


@dataclass(frozen=True)
class ScopeInventory:
    """Every row of one baseline's scope inventory, grouped by the baseline it was read at.

    The baseline travels with the rows because two artifacts examined at two baselines are two
    observations: rows read at one frozen baseline are never merged into the count of another.
    """

    baseline_key: tuple[str, str]
    rows: tuple[InventoryRow, ...]

    @property
    def counts_by_state(self) -> Mapping[str, int]:
        """Return how many rows carry each inventory state, so absent rows are counted, not implied."""

        return dict(Counter(row.inventory_state for row in self.rows))

    @property
    def counts_by_route(self) -> Mapping[str, int]:
        """Return how many rows each recorded route holds, which is this inventory's slice axis."""

        return dict(Counter(row.route_path for row in self.rows))

    @property
    def rows_without_onboarding(self) -> tuple[InventoryRow, ...]:
        """Return the in-scope sources with no card at all -- the rows a corpus list cannot show."""

        return tuple(row for row in self.rows if row.inventory_state == ABSENT_INVENTORY_STATE)


@dataclass(frozen=True)
class _ArtifactPath:
    """One declared artifact path: its confined path relative to the onboarding root, and what it is.

    ``is_file`` records whether the path exists as a regular file. It is an observed fact about one
    path, which is why it travels with the path instead of being probed again at each use.
    """

    artifact_path: str
    is_file: bool


@dataclass(frozen=True)
class _CardExamination:
    """What one artifact read as: the bounded observed content, the state, and its declared doc type."""

    observed_content: str
    read_state: ReadState
    declared_doc_type: str | None = None


@dataclass(frozen=True)
class _ReadingContext:
    """Everything one inventory passes down: the root read, the reader, the routes and the baseline.

    Bundled as one value because these are one concept -- the frozen reading this inventory was taken
    under -- and because a small argument list is what keeps the row builders inside the function
    budget the repository sets.
    """

    onboarding_root: Path
    read_artifact: Callable[[Path], tuple[str, str]]
    route_prefixes: tuple[tuple[str, ...], ...]
    baseline_key: tuple[str, str]


def _normalized_segments(path: str) -> tuple[str, ...] | None:
    """Return a path's normalised segments, or ``None`` when it is not a repository-relative path.

    A report label is a path, not an index into a filesystem: an absolute spelling and a traversal
    segment would each make the same text resolve to a file outside the tree the row is about.
    """

    if not path or path.startswith("/") or "\\" in path:
        return None
    segments = posixpath.normpath(path).split("/")
    if any(segment in {"", POSIX_CURRENT_DIRECTORY, ".."} for segment in segments):
        return None
    return tuple(segments)


def _route_segments(declared_routes: Iterable[str]) -> tuple[tuple[str, ...], ...]:
    """Return the declared routes as whole-segment prefixes, longest first, invalid entries dropped."""

    prefixes = []
    for declared in declared_routes:
        segments = _normalized_segments(declared)
        if segments is not None and segments != (POSIX_CURRENT_DIRECTORY,):
            prefixes.append(segments)
    return tuple(sorted(prefixes, key=len, reverse=True))


def _route_for_segments(
    route_prefixes: Sequence[tuple[str, ...]], segments: tuple[str, ...]
) -> str:
    """Return the longest declared route that is a whole-segment prefix of ``segments``.

    The comparison is over path segments rather than characters, so ``mcpfoo/x.py`` shares no segment
    with route ``mcp`` and belongs to no route. A source that matches no declared route returns the
    empty string: it is in scope and has no recorded route, which is a fact about the repository's
    route coverage rather than a reason to drop the row.
    """

    for prefix in route_prefixes:
        if segments[: len(prefix)] == prefix:
            return "/".join(prefix)
    return ""


def _route_for_path(path: str, route_prefixes: Sequence[tuple[str, ...]]) -> str | None:
    """Return the route a path belongs to, or ``None`` when the text is not a repository path at all."""

    segments = _normalized_segments(path)
    if segments is None:
        return None
    return _route_for_segments(route_prefixes, segments)


def _card_path_for(source_path: str, card_path_for_source: Callable[[str], str]) -> str:
    """Return the card path a caller's mapping names for one source, defaulting to the suffix rule."""

    declared = card_path_for_source(source_path)
    return declared if declared else source_path + MARKDOWN_CARD_SUFFIX


def _declared_artifact_path(declared: str, onboarding_root: Path) -> _ArtifactPath | None:
    """Return one declared artifact path confined to the onboarding root, or ``None`` if not a path."""

    segments = _normalized_segments(declared)
    if segments is None:
        return None
    artifact_path = "/".join(segments)
    return _ArtifactPath(
        artifact_path=artifact_path,
        is_file=(onboarding_root / artifact_path).is_file(),
    )


def _route_candidate(observed: str) -> str | None:
    """Return the route path one observed onboarding path names, or ``None`` when it names none.

    A route path is the source-tree spelling -- the shipped ``Route`` normalisation, so ``mcp`` and
    never ``mcp/`` or ``onboarding/mcp``. An observed path may arrive the way the corpus addresses it
    (``onboarding/mcp``), the way a caller noticed it on disk (a bare ``mcp``), as a card path under
    its route (``mcp/x.py.md``), or as the absolute directory a caller listed; all of those name the
    same route, and a name outside the declared route directories names none of them whatever precedes
    it.
    """

    segments = _normalized_segments(observed) or tuple(
        segment for segment in posixpath.normpath(observed).split("/") if segment
    )
    if CORPUS_DIRECTORY in segments:
        segments = segments[segments.index(CORPUS_DIRECTORY) + 1 :]
    if segments and segments[0] in DEFAULT_ROUTE_DIRECTORIES:
        return segments[0]
    return None


def _route_directories(observed_paths: frozenset[str]) -> tuple[str, ...]:
    """Return the routes this scope is asked about: what was observed, or the declared list.

    An empty observation means no tree was listed, not that the repository has no routes: the declared
    convention is then the whole answer, so a caller that resolved its sources without listing the
    corpus still gets the routes its layout implies. When paths *were* observed, only the routes they
    name are asked about, so a route directory that does not exist is not reported as empty coverage.
    """

    candidates = map(_route_candidate, observed_paths)
    observed_routes = tuple(sorted({route for route in candidates if route is not None}))
    return observed_routes or DEFAULT_ROUTE_DIRECTORIES


# The declared ``doc_type`` values this corpus uses, mapped to the census's closed artifact-kind
# vocabulary. The mapping is over a value the artifact **declares**, which is the one authority for
# what a document is: an artifact is what its front matter says it is, not what its filename suggests.
ARTIFACT_KIND_BY_DOC_TYPE: Mapping[str, str] = {
    "file-level-onboarding": "file_level_onboarding",
    "route-local-overview": "route_local_overview",
    "repo-overview": "route_local_overview",
}


def artifact_kind_of(declared_doc_type: str | None, artifact_path: str) -> str:
    """Return the artifact kind one artifact declares, falling back to its position when it declares none.

    The **declared** ``doc_type`` is the authority, because a document's own metadata is what says
    what it is; deriving the kind from a filename is the inference this record group exists to avoid,
    and the two disagree exactly where a corpus is being reorganised. The path is consulted only when
    the artifact declares no doc_type at all -- an artifact that parsed but declared no form, or one
    that did not parse -- and then only to give the row a kind rather than to override a declaration.
    """

    if declared_doc_type is not None:
        declared = ARTIFACT_KIND_BY_DOC_TYPE.get(declared_doc_type)
        if declared is not None:
            return declared
        return ABSENT_ARTIFACT_KIND
    document = posixpath.basename(artifact_path)
    if document == ROUTE_OVERVIEW_STEM + MARKDOWN_CARD_SUFFIX:
        return "route_local_overview"
    if document.endswith(MARKDOWN_CARD_SUFFIX):
        return "file_level_onboarding"
    return ABSENT_ARTIFACT_KIND


def _artifact_kind(artifact_path: str) -> str:
    """Return the artifact kind a card path names, for an artifact that declared no doc_type."""

    return artifact_kind_of(None, artifact_path)


def _read_artifact(path: Path) -> tuple[str, str]:
    """Read one artifact as UTF-8 text and report the state the read ended in.

    This is the default reader, so it stays the smallest thing that satisfies the seam: it decodes and
    reports. It does not catch its own failures -- the caller that builds a row turns a raised read
    failure into a recorded state, so a raise here is a state a row will carry, not a crash.
    """

    return path.read_text(encoding="utf-8"), READ_STATE_PARSED


def _bounded_observed_content(text: str) -> str:
    """Return observed content capped at :data:`MAX_UNPARSED_LENGTH`, or the note for no content.

    The cap is the point of the function: an artifact is read before anything has vetted its size, so
    the text that reaches a durable record is bounded here rather than trusted to be small.
    """

    if not text:
        return EMPTY_ARTIFACT_CONTENT
    return text[:MAX_UNPARSED_LENGTH]


def _failure_evidence(error: OSError | UnicodeDecodeError) -> str:
    """Return the text one read failure reports, or a stated fact when the failure reported none."""

    if isinstance(error, UnicodeDecodeError):
        return error.reason or UNREADABLE_CONTENT_FALLBACK
    return str(error) or UNREADABLE_CONTENT_FALLBACK


def _require_known_read_state(read_state: str) -> ReadState:
    """Return a reader's reported state as the declared vocabulary, refusing anything outside it.

    A reader is an injected seam, so the state it reports is an input rather than a fact of this
    module: an undeclared state would otherwise be recorded as a failure, which would report an
    artifact a reader actually read as one that could not be read at all.
    """

    if read_state not in CENSUS_PARSE_OUTCOMES:
        raise ValueError(
            "a reader reports one of the declared parse outcomes; an undeclared state has no outcome "
            "this inventory can record, and guessing one would misreport the read"
        )
    return cast(ReadState, read_state)


# The header row of the corpus's metadata table, and the one row inside it that declares what the
# document is. Only the table's leading block is scanned, so a body table whose first cell happens to
# spell ``doc_type`` cannot be mistaken for the declaration.
_METADATA_HEADER: tuple[str, str] = ("Field", "Value")
_DOC_TYPE_ROW = "doc_type"
_METADATA_CELLS = 2


def _declared_doc_type(text: str) -> str | None:
    """Return the ``doc_type`` one artifact's metadata table declares, or ``None`` when it declares none.

    This is a structural read of a declared field and nothing else: it looks for the corpus's own
    header row, then for the one row whose first cell is ``doc_type``, and it stops at the first row
    that leaves the table. It never scores, guesses or maps a value -- what the field *means* is the
    parser's and the mapping registry's business, and this function only reports what was written.
    """

    lines = text.splitlines()
    header = next(
        (index for index, line in enumerate(lines) if _cells(line) == _METADATA_HEADER),
        None,
    )
    if header is None:
        return None
    for line in lines[header + 2 :]:
        cells = _cells(line)
        if not cells or len(cells) != _METADATA_CELLS:
            return None
        if cells[0] == _DOC_TYPE_ROW:
            return cells[1].strip().strip("`") or None
    return None


def _cells(line: str) -> tuple[str, ...]:
    """Return one markdown table row's cells, or an empty tuple when the line is not a table row."""

    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return ()
    cells = tuple(cell.strip() for cell in stripped.strip("|").split("|"))
    if all(set(cell) <= set("-: ") for cell in cells if cell):
        return ()
    return cells


def _examine_artifact(
    path: Path, read_artifact: Callable[[Path], tuple[str, str]]
) -> _CardExamination:
    """Return what one artifact read as, turning a raised read failure into a recorded state.

    A reader that raises is a read that failed, and the failure's own text is the evidence the row
    carries: refusing to raise here is what makes an unreadable artifact a row rather than a stopped
    run. The text is bounded before it reaches a row, because the file that failed to decode is
    exactly the file whose size nothing here has vetted.
    """

    try:
        text, read_state = read_artifact(path)
    except (OSError, UnicodeDecodeError) as error:
        evidence = _bounded_observed_content(_failure_evidence(error))
        return _CardExamination(evidence, cast(ReadState, UNREADABLE_PARSE_OUTCOME))
    return _CardExamination(
        text[:MAX_UNPARSED_LENGTH],
        _require_known_read_state(read_state),
        _declared_doc_type(text),
    )


def _unparsed_content(examination: _CardExamination) -> str | None:
    """Return the unparsed content a row must carry, or ``None`` when the artifact parsed.

    A row saying it could not read something and naming nothing is indistinguishable from a row that
    was never taken, so this is the one place that decides what a non-parsing outcome has to report.
    """

    if examination.read_state in _NOTHING_TO_REPORT:
        return None
    return _bounded_observed_content(examination.observed_content)


def _inventory_state(examination: _CardExamination) -> str:
    """Return the inventory state an examination implies: met and read, or met and unreadable.

    Only an unreadable artifact is not ``present``: an artifact read as text is there whether or not
    its content yielded anything a parser understood, so a successful read stays a present artifact
    and reports its parse outcome separately.
    """

    return (
        UNREADABLE_INVENTORY_STATE
        if examination.read_state == UNREADABLE_PARSE_OUTCOME
        else PRESENT_INVENTORY_STATE
    )


def _card_row(entry: ScopeEntry, card: _ArtifactPath, context: _ReadingContext) -> InventoryRow:
    """Return the row for one in-scope source and the card found at its declared card path."""

    artifact = context.onboarding_root / card.artifact_path
    examination = _examine_artifact(artifact, context.read_artifact)
    return InventoryRow(
        artifact_path=card.artifact_path,
        artifact_kind=artifact_kind_of(examination.declared_doc_type, card.artifact_path),
        declared_source_path=entry.source_path,
        source_path=entry.source_path,
        route_path=entry.route_path,
        inventory_state=_inventory_state(examination),
        observed_doc_type=examination.declared_doc_type,
        observed_route_path=_route_for_path(card.artifact_path, context.route_prefixes),
        parsing_outcome=examination.read_state,
        unparsed_content=_unparsed_content(examination),
    )


def _absent_row(entry: ScopeEntry) -> InventoryRow:
    """Return the row for one in-scope source that has no card, which is the row this scope adds."""

    return InventoryRow(
        artifact_path="",
        artifact_kind=ABSENT_ARTIFACT_KIND,
        declared_source_path=None,
        source_path=entry.source_path,
        route_path=entry.route_path,
        inventory_state=ABSENT_INVENTORY_STATE,
        observed_doc_type=None,
        observed_route_path=None,
        parsing_outcome=ABSENT_PARSE_OUTCOME,
        unparsed_content=EMPTY_ARTIFACT_CONTENT,
    )


def _unclaimed_row(card: _ArtifactPath, context: _ReadingContext) -> InventoryRow:
    """Return the row for a card whose declared source is not in scope: a card with no source left."""

    artifact = context.onboarding_root / card.artifact_path
    examination = _examine_artifact(artifact, context.read_artifact)
    route_path = _route_for_path(card.artifact_path, context.route_prefixes)
    return InventoryRow(
        artifact_path=card.artifact_path,
        artifact_kind=artifact_kind_of(examination.declared_doc_type, card.artifact_path),
        declared_source_path=_declared_source_of(card.artifact_path),
        source_path=None,
        route_path=route_path if route_path else POSIX_CURRENT_DIRECTORY,
        inventory_state=_inventory_state(examination),
        observed_doc_type=examination.declared_doc_type,
        observed_route_path=route_path,
        parsing_outcome=examination.read_state,
        unparsed_content=_unparsed_content(examination),
    )


def _declared_source_of(artifact_path: str) -> str:
    """Return the source path a card's own path declares, by the corpus's one documented suffix rule."""

    if artifact_path.endswith(MARKDOWN_CARD_SUFFIX):
        return artifact_path[: -len(MARKDOWN_CARD_SUFFIX)]
    return artifact_path


def _card_path_of(entry: ScopeEntry) -> str:
    """Return the card path one scope entry's source declares syntactically, card or no card on disk.

    This answers "which artifact would this source's card be", which is a question about the path and
    not about the filesystem, so it probes nothing: it is what tells an observed card whether some
    in-scope source already accounts for it.
    """

    segments = _normalized_segments(entry.source_path + MARKDOWN_CARD_SUFFIX)
    return "/".join(segments) if segments is not None else ""


def _observed_cards(context: _ReadingContext) -> dict[str, _ArtifactPath]:
    """Return every markdown artifact observed under the routes this scope derived, by its path."""

    observed: dict[str, _ArtifactPath] = {}
    for prefix in context.route_prefixes:
        directory = context.onboarding_root.joinpath(*prefix)
        for found in sorted(directory.rglob("*" + MARKDOWN_CARD_SUFFIX)):
            if not found.is_file():
                continue
            relative = found.relative_to(context.onboarding_root).as_posix()
            observed[relative] = _ArtifactPath(relative, is_file=True)
    return observed


def _scope_rows(entries: Sequence[ScopeEntry], context: _ReadingContext) -> Iterator[InventoryRow]:
    """Yield one row per in-scope source, from its card when one exists and as absent when none does."""

    for entry in entries:
        card = _declared_artifact_path(
            entry.source_path + MARKDOWN_CARD_SUFFIX, context.onboarding_root
        )
        if card is None or not card.is_file:
            yield _absent_row(entry)
            continue
        yield _card_row(entry, card, context)


def _unclaimed_rows(
    entries: Sequence[ScopeEntry], context: _ReadingContext
) -> Iterator[InventoryRow]:
    """Yield one row per observed card that no in-scope source claims: a card pointing at a gone file.

    The claimed set is what keeps one artifact to one row: a card a scope entry already reported is
    that entry's row, and only the cards no entry accounts for are reported here.
    """

    claimed = {_card_path_of(entry) for entry in entries}
    for artifact_path, card in sorted(_observed_cards(context).items()):
        if artifact_path in claimed:
            continue
        yield _unclaimed_row(card, context)


def resolve_scope(
    source_paths: Iterable[str],
    onboarding_paths: Iterable[str],
    *,
    card_path_for_source: Callable[[str], str],
) -> tuple[ScopeEntry, ...]:
    """Return one scope entry per in-scope source path, with the route the layout gives it.

    The route comes from the repository's own route convention as the onboarding tree's route
    directories state it, matched on whole path segments against the source path; it is never read out
    of a card's content. A source whose path is not repository-relative, and a source outside every
    declared route, are both dropped: a scope entry has to name a route the corpus can be asked about.
    """

    route_paths = _route_directories(frozenset(onboarding_paths))
    route_prefixes = _route_segments(route_paths)
    entries = []
    for source_path in source_paths:
        declared = _card_path_for(source_path, card_path_for_source)
        if _normalized_segments(declared) is None:
            continue
        route_path = _route_for_path(source_path, route_prefixes)
        if not route_path:
            continue
        entries.append(ScopeEntry(source_path=source_path, route_path=route_path))
    return tuple(sorted(entries, key=lambda entry: entry.source_path))


def build_inventory(
    *,
    baseline: FrozenBaseline,
    scope: Sequence[ScopeEntry],
    onboarding_root: Path,
    read_artifact: Callable[[Path], tuple[str, str]] | None = None,
) -> ScopeInventory:
    """Return the scope inventory at the frozen baseline, with one row per in-scope artifact.

    Every in-scope source gets a row whether or not a card exists for it, and every card whose source
    is out of scope gets a row too, so neither direction of the mapping can hide an artifact. No bad
    artifact raises: an unreadable file or non-UTF-8 content becomes a row carrying its outcome and
    the observed content, bounded, that could not be parsed.
    """

    entries = tuple(scope)
    context = _ReadingContext(
        onboarding_root=onboarding_root,
        read_artifact=read_artifact if read_artifact is not None else _read_artifact,
        route_prefixes=_route_segments(entry.route_path for entry in entries),
        baseline_key=baseline.key(),
    )
    rows = (*_scope_rows(entries, context), *_unclaimed_rows(entries, context))
    return ScopeInventory(baseline_key=context.baseline_key, rows=rows)
