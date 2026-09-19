"""Read one legacy Markdown onboarding artifact into the structure it declares, and nothing more.

This is the migration's parser, and its whole contract is the shape of the reading it does:

* **It reports structure, and it classifies nothing.** An artifact's semantic category, the truth of
  a claim it makes, whether a reference resolves and whether a card is any good are all somebody
  else's read. Nothing here inspects prose: there is no keyword test, no scoring, no heading-to-topic
  mapping, and no branch whose condition is a statement about meaning. What is read is a declared
  front-matter table, ATX headings, fenced regions and the corpus's own citation mark.
* **It never raises for a bad artifact.** Every artifact gets an outcome, and the vocabulary is the
  census's own: ``parsed``, ``unparsed``, ``unsupported``, ``unreadable``. A shape this parser has not
  been told about is reported ``unsupported`` with the content it could not read, never absorbed as a
  silent extension of a declared format.
* **It declares the formats it admits, with a reason each.** :data:`SUPPORTED_FORMATS` is that
  declaration, and :data:`UNSUPPORTED_FORMAT` is the form it deliberately refuses: the generated
  bootstrap artifact, whose leading table is not a card's front matter. A format that is not in the
  declared list can only be reported unsupported, which is why the refusal is an entry in the list.

The one correctness detail that matters most is where the metadata table **stops**. The legacy card
carries a two-column front-matter table and then a body full of tables of its own (a ``Finding |
Anchor | Source`` inventory, route tables, wave tables). Reading every ``|``-row that follows the
header as a declaration is a measured failure mode of this corpus, not a hypothetical one: it turns
a card's evidence inventory into front-matter rows the card never declared. So the reader continues
only while a row's key is a name of the declared front-matter vocabulary, and it stops at the first
key outside it or the first line that is not a table row at all.

Every stored text is bounded by the width the census payload declares for it (``PROSE_MAX_LENGTH``
for prose, ``LABEL_MAX_LENGTH`` for a name). The bound is storage, not interpretation: a stored value
is the artifact's own prefix, unmodified and unelided, because a truncation marker would be this
parser writing text into a field that means "what the artifact said".
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from agents_remember.memory.knowledge.schema_v9 import CENSUS_ARTIFACT_KINDS, CENSUS_PARSE_OUTCOMES
from agents_remember.models.knowledge.base import LABEL_MAX_LENGTH, PROSE_MAX_LENGTH

# The corpus's own citation mark: a prose citation is written ``cit:([<anchors>], <path>:<start>-<end>)``
# and the text between the mark and its matching ``)`` is the citation key this parser reports.
CITATION_MARK = "cit:("

# The four outcomes the census vocabulary declares, bound by name so that no path below spells one as
# a literal. The unpacking is also the guard: a vocabulary that stops carrying exactly these four
# states fails at import instead of letting a mismatched name reach a written row.
(
    OUTCOME_PARSED,
    OUTCOME_UNPARSED,
    OUTCOME_UNSUPPORTED,
    OUTCOME_UNREADABLE,
) = CENSUS_PARSE_OUTCOMES

# The declared formats, each a stable name a caller can record without re-deriving it from the artifact.
METADATA_TABLE_FORMAT = "markdown-metadata-table/v1"
FILE_LEVEL_CARD_FORMAT = "file-level-onboarding-card/v1"
ROUTE_OVERVIEW_FORMAT = "route-local-overview/v1"

# The one declared form this parser refuses. It is declared rather than omitted so that the refusal is
# a recorded decision with a reason: an artifact that is not a card is reported ``unsupported``, and a
# format merely missing from the list is indistinguishable from one nobody considered.
UNSUPPORTED_FORMAT = "generated-bootstrap-artifact/v1"

# The declared front-matter vocabulary. It is read as the table's own bound as well as its meaning:
# a row is front matter while its key is one of these names, and the table ends at the first key that
# is not. An artifact whose first key is already outside this set declares its leading table in a
# vocabulary this parser was never told about, so it is reported unsupported rather than half-read.
RECOGNIZED_FRONT_MATTER_KEYS: tuple[str, ...] = (
    "repository",
    "path",
    "sourceRoute",
    "doc_type",
    "lastUpdated",
    "lastVerifiedCommitHash",
    "lastVerifiedCommitDate",
    "governingOverview",
    "reviewedWorkingCandidate",
)
_RECOGNIZED_KEYS = frozenset(RECOGNIZED_FRONT_MATTER_KEYS)

# The declared fields whose values are references the artifact makes: the source a file card documents,
# the route a route overview describes, and the overview that governs either. They are read as
# references because the artifact declares them as such, not because of what they look like.
REFERENCE_FIELD_NAMES: tuple[str, ...] = ("path", "sourceRoute", "governingOverview")

# The declared doc_type field projected onto a format name. A declared doc_type this projection does
# not name is still an artifact using the metadata-table form -- the form is what the parser reads --
# so the projection's default is that form rather than a refusal: the vocabulary of doc_type values
# grows, the shape of a two-column declaration table does not.
FORMAT_BY_DOC_TYPE: Mapping[str, str] = {
    "file-level-onboarding": FILE_LEVEL_CARD_FORMAT,
    "route-local-overview": ROUTE_OVERVIEW_FORMAT,
    "repo-overview": ROUTE_OVERVIEW_FORMAT,
}

# The declared doc_type field projected onto the census's closed artifact-kind vocabulary. This is a
# lookup over ONE DECLARED FIELD -- a card that says ``doc_type: file-level-onboarding`` is reported
# under that declaration whether or not its body reads like a card -- and an artifact that declares no
# doc_type, or one this projection does not name, is ``other`` rather than guessed at from its prose.
ARTIFACT_KIND_BY_DOC_TYPE: Mapping[str, str] = {
    "file-level-onboarding": "file_level_onboarding",
    "route-local-overview": "route_local_overview",
    "repo-overview": "route_local_overview",
}
ARTIFACT_KIND_OTHER = "other"

# Import-time guards: the projections above may only name values the schema's closed vocabulary admits,
# and a reference may only be read from a field this module's own table vocabulary recognizes. An edit
# that broke either would otherwise fail at the first written row, or silently read nothing at all.
if not {*ARTIFACT_KIND_BY_DOC_TYPE.values(), ARTIFACT_KIND_OTHER} <= set(CENSUS_ARTIFACT_KINDS):
    raise RuntimeError("this module names an artifact kind the census vocabulary does not admit")
if not set(REFERENCE_FIELD_NAMES) <= set(RECOGNIZED_FRONT_MATTER_KEYS):
    raise RuntimeError("this module reads a reference from a field its table vocabulary excludes")

# The recognized table header, the separator row's cells, an ATX heading and a fence opener. Whitespace
# is not significant inside a Markdown table row, so the header pattern admits the padded spelling the
# corpus writes as well as a tight one; the heading pattern drops an ATX closing sequence.
_METADATA_HEADER = re.compile(r"^\|\s*Field\s*\|\s*Value\s*\|\s*$")
_SEPARATOR_CELL = re.compile(r"^:?-+:?$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(?:```|~~~)")

# What is reported when an artifact's bytes carry no content at all: the one value here that is not
# the artifact's own text, and a statement about the observation rather than about meaning, because
# the census refuses a non-parsed outcome with no evidence and an empty artifact has none to report.
NOTHING_OBSERVED = "(no content)"


@dataclass(frozen=True)
class SupportedFormat:
    """One legacy format this parser admits or refuses, and the observed structure that decides it.

    ``reason`` names the structure the corpus actually shows -- the header row, the declared field, the
    population that carries it -- because a format admitted on a guess cannot be re-checked later.
    """

    name: str
    doc_type: str
    reason: str


# The declared formats, each with the observed structure that decides it. Measured on the legacy
# corpus at the migration baseline: 2071 artifacts declare a two-column table whose first key is a
# front-matter name, of which 1984 declare ``file-level-onboarding`` and 85 declare a route-tier
# overview; 41 declare no readable front matter at all.
SUPPORTED_FORMATS: tuple[SupportedFormat, ...] = (
    SupportedFormat(
        name=METADATA_TABLE_FORMAT,
        doc_type="file-level-onboarding,route-local-overview,repo-entity-catalog,repo-overview",
        reason=(
            "the artifact's first table has the header row `| Field | Value |` followed by a "
            "two-column separator row, and its rows are two-cell rows whose keys are the declared "
            "front-matter names; the table is read as declarations and stops at its own end"
        ),
    ),
    SupportedFormat(
        name=FILE_LEVEL_CARD_FORMAT,
        doc_type="file-level-onboarding",
        reason=(
            "1984 artifacts declare `doc_type` `file-level-onboarding` and carry a `path` naming the "
            "single source file the card documents, next to a `governingOverview` naming the route "
            "overview that governs it"
        ),
    ),
    SupportedFormat(
        name=ROUTE_OVERVIEW_FORMAT,
        doc_type="route-local-overview,repo-overview",
        reason=(
            "85 artifacts declare `doc_type` `route-local-overview` or `repo-overview` and carry a "
            "`sourceRoute` naming the route they describe instead of a `path`, which is the declared "
            "field that separates a route-tier overview from a file card"
        ),
    ),
    SupportedFormat(
        name=UNSUPPORTED_FORMAT,
        doc_type="*",
        reason=(
            "NOT SUPPORTED: 38 artifacts carry the `| Field | Value |` header whose first row's key "
            "is outside the front-matter vocabulary (a bootstrap, wave or card-template table) and 3 "
            "carry no such table at all; both are generated artifacts whose leading table is not a "
            "card's declaration, so they are reported `unsupported` with their observed content and "
            "never read as a card"
        ),
    ),
)


@dataclass(frozen=True)
class FrontMatter:
    """The declarations the artifact's own metadata table carries, verbatim and in written order.

    ``fields`` holds the value the artifact wrote with the table's code-span quoting removed, because
    that quoting is not part of the declaration. The mapping is built for this one observation and
    never shared or mutated afterwards. ``keys_in_order`` is the written order, which a caller should
    not have to assume a mapping preserves.
    """

    fields: Mapping[str, str]
    keys_in_order: tuple[str, ...]


@dataclass(frozen=True)
class Section:
    """One heading the artifact writes, with the text that follows it before the next heading.

    ``line`` is the 1-based line of the heading itself. ``body`` is this section's own text: the lines
    after the heading up to the next heading of any level, so a document's lines belong to at most one
    section and a parent's text is never a duplicate of its children's.
    """

    heading: str
    level: int
    body: str
    line: int


@dataclass(frozen=True)
class ArtifactReference:
    """One reference the parser READ out of the artifact. Classification is NOT done here.

    ``reference_text`` is verbatim: a declared field's value, or the exact body of one ``cit:(...)``
    mark. ``field_or_section`` says only where the text was read from -- a declared field's name, or
    the heading of the section the mark appeared in -- and it is not a kind, a target or a verdict.
    Whether the reference resolves is a question for the module that resolves references.
    """

    reference_text: str
    field_or_section: str
    line: int


@dataclass(frozen=True)
class ParsedArtifact:
    """One artifact as this parser observed it: its declarations, its structure and its references.

    ``artifact_kind`` comes from the declared ``doc_type`` field and ``outcome`` from whether the
    declared front-matter form was readable at all. For any outcome other than ``parsed``, every read
    product is empty and ``unparsed_content`` carries the observed content instead: an artifact that
    was not parsed produced no title, no sections and no references, so reporting them alongside the
    content that was not read would be a claim the parser cannot support.
    """

    artifact_path: str
    artifact_kind: str
    outcome: str
    unparsed_content: str | None
    front_matter: FrontMatter | None
    title: str | None
    sections: tuple[Section, ...]
    citation_keys: tuple[str, ...]
    references: tuple[ArtifactReference, ...]


def declared_formats() -> tuple[SupportedFormat, ...]:
    """Return the formats this parser declares, in the order it resolves them.

    The declaration is the parser's own boundary: a form that is not here is reported unsupported
    rather than read on a best-effort basis, so a reader of an outcome can name the format the outcome
    was decided against.
    """

    return SUPPORTED_FORMATS


def classify_declared_format(front_matter: FrontMatter | None) -> str | None:
    """Return the declared format this front matter selects, or ``None`` if it declares no doc_type.

    This reads one declared field and looks it up. It never inspects the body: a route overview whose
    prose reads like a file card is still the format its own ``doc_type`` declares, and an artifact
    that declares no ``doc_type`` selects nothing here even though its table parsed.
    """

    if front_matter is None:
        return None
    doc_type = front_matter.fields.get("doc_type", "").strip()
    if not doc_type:
        return None
    return FORMAT_BY_DOC_TYPE.get(doc_type, METADATA_TABLE_FORMAT)


def parse_artifact(artifact_path: str, text: str) -> ParsedArtifact:
    """Return the observation of one legacy artifact, whatever its content turned out to be.

    A decode failure is reported rather than raised because the corpus's UTF-8 declaration is a claim
    about the format, not about today's bytes: text that is not the text of any UTF-8 sequence is
    ``unreadable``, with the bytes it could not decode shown escaped. Text with no declared
    front-matter form is ``unsupported``, which is the declared refusal rather than a raised error, so
    a sweep over a whole corpus reports every artifact it met.
    """

    if not _is_utf8_text(text):
        return _unread(artifact_path, OUTCOME_UNREADABLE, text)
    lines = text.split("\n")
    table = _read_metadata_table(lines)
    if table is None:
        return _unread(artifact_path, OUTCOME_UNSUPPORTED, text)
    fields, field_lines = table
    front_matter = FrontMatter(fields=fields, keys_in_order=tuple(fields))
    sections = _read_sections(lines)
    citations = _citation_occurrences(text)
    return ParsedArtifact(
        artifact_path=artifact_path,
        artifact_kind=_artifact_kind(front_matter),
        outcome=OUTCOME_PARSED,
        unparsed_content=None,
        front_matter=front_matter,
        title=_title(sections),
        sections=sections,
        citation_keys=tuple(dict.fromkeys(body for _line, body in citations)),
        references=_references(front_matter, field_lines, sections, citations),
    )


def _read_metadata_table(lines: list[str]) -> tuple[Mapping[str, str], Mapping[str, int]] | None:
    """Return the declared fields and the line each was read from, or ``None`` if there is no table.

    Reading continues only while a row is a two-cell row whose first key is a name of the declared
    front-matter vocabulary, so the body's own tables below it are never read as declarations.
    """

    header = _first_index(lines, _METADATA_HEADER)
    if header is None or not _is_separator_row(lines, header + 1):
        return None
    fields: dict[str, str] = {}
    field_lines: dict[str, int] = {}
    for index in range(header + 2, len(lines)):
        cells = _table_cells(lines[index])
        if len(cells) != 2 or cells[0] not in _RECOGNIZED_KEYS:
            break
        fields[cells[0]] = _unwrapped(cells[1])
        field_lines[cells[0]] = index + 1
    if not fields:
        return None
    return fields, field_lines


def _read_sections(lines: list[str]) -> tuple[Section, ...]:
    """Return every heading the artifact writes, each with the text that follows it."""

    positions = _heading_positions(lines)
    sections: list[Section] = []
    for order, (index, level, heading) in enumerate(positions):
        following = positions[order + 1][0] if order + 1 < len(positions) else len(lines)
        sections.append(
            Section(
                heading=_bounded(heading, LABEL_MAX_LENGTH),
                level=level,
                body=_bounded(_section_body(lines, index + 1, following), PROSE_MAX_LENGTH),
                line=index + 1,
            )
        )
    return tuple(sections)


def _title(sections: tuple[Section, ...]) -> str | None:
    """Return the text of the artifact's first level-one heading, or ``None`` if it writes none."""

    for section in sections:
        if section.level == 1:
            return section.heading
    return None


def _heading_positions(lines: list[str]) -> tuple[tuple[int, int, str], ...]:
    """Return the zero-based line, level and text of every heading written outside a code fence.

    A fenced block is quoted text: a line beginning with ``#`` inside one is a comment the artifact is
    showing, not a heading it writes. Reading it as one would name a section the artifact does not
    have, and would hand a citation an enclosing location that is really a line of source code.
    """

    positions: list[tuple[int, int, str]] = []
    fenced = False
    for index, line in enumerate(lines):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        match = None if fenced else _HEADING.match(line)
        if match is not None:
            positions.append((index, len(match.group(1)), match.group(2).strip()))
    return tuple(positions)


def _citation_occurrences(text: str) -> tuple[tuple[int, str], ...]:
    """Return ``(line, body)`` for every complete citation mark, in the order the artifact writes them.

    The body is everything between the mark and the ``)`` that closes it. A mark whose body does not
    close within the storable width is not delimited at all and is not reported, because a body this
    parser cannot bound is not a body it can hand on.
    """

    found: list[tuple[int, str]] = []
    cursor = 0
    line = 1
    while True:
        mark = text.find(CITATION_MARK, cursor)
        if mark < 0:
            return tuple(found)
        line += text.count("\n", cursor, mark)
        start = mark + len(CITATION_MARK)
        close = _body_end(text, start)
        if close is None:
            cursor = start
            continue
        found.append((line, _bounded(text[start:close], PROSE_MAX_LENGTH)))
        cursor = close + 1


def _body_end(text: str, start: int) -> int | None:
    """Return the index of the ``)`` closing a body opened before ``start``, or ``None`` if none does.

    The closing ``)`` is the first one written OUTSIDE the body's anchor quoting, which is a
    double-quoted string (with the corpus's backslash escapes) or a backtick code span. Counting
    parenthesis depth instead is wrong for this corpus: an anchor quotes the source text it is about,
    and that text may carry an unbalanced parenthesis -- a quoted call signature, say -- so a depth
    count runs past the real close and swallows the citations that follow it. The walk is bounded by the
    storable width, with one character to spare at the cap, so a mark that never closes stays cheap.
    """

    quote = ""
    index = start
    limit = min(len(text), start + PROSE_MAX_LENGTH + 1)
    while index < limit:
        char = text[index]
        if quote:
            if char == "\\" and quote == '"':
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in ('"', "`"):
            quote = char
        elif char == ")":
            return index
        index += 1
    return None


def _references(
    front_matter: FrontMatter,
    field_lines: Mapping[str, int],
    sections: tuple[Section, ...],
    citations: tuple[tuple[int, str], ...],
) -> tuple[ArtifactReference, ...]:
    """Return the declared field references and the written citation references, in reading order.

    A declared field with a blank value is not a reference the artifact made and is left out; the
    citation half is reported whether or not its target exists, since resolving a reference is the
    job of the module that resolves references and not of the parser that read it.
    """

    declared = tuple(
        ArtifactReference(
            reference_text=front_matter.fields[name], field_or_section=name, line=field_lines[name]
        )
        for name in REFERENCE_FIELD_NAMES
        if front_matter.fields.get(name, "").strip()
    )
    headings = tuple((section.line, section.heading) for section in sections)
    written = tuple(
        ArtifactReference(
            reference_text=body, field_or_section=_enclosing_heading(headings, line), line=line
        )
        for line, body in citations
    )
    return declared + written


def _enclosing_heading(headings: tuple[tuple[int, str], ...], line: int) -> str:
    """Return the heading of the section a line falls in, or ``""`` if no heading precedes it.

    The empty location is reported rather than invented: content written above an artifact's first
    heading is in no section, and naming the nearest heading anyway would place a citation inside a
    section the artifact does not put it in.
    """

    enclosing = ""
    for start, heading in headings:
        if start > line:
            break
        enclosing = heading
    return enclosing


def _artifact_kind(front_matter: FrontMatter) -> str:
    """Return the artifact kind the declared ``doc_type`` selects, or ``other`` for any other read.

    This is a lookup of one DECLARED FIELD in a fixed projection. It is not derived from whether the
    artifact's content parsed, and it is not derived from its prose: a card whose body is unreadable
    is still the kind its declaration names, and an artifact that declares nothing is ``other``.
    """

    return ARTIFACT_KIND_BY_DOC_TYPE.get(
        front_matter.fields.get("doc_type", ""), ARTIFACT_KIND_OTHER
    )


def _unread(artifact_path: str, outcome: str, text: str) -> ParsedArtifact:
    """Return the observation of an artifact this parser could not read, carrying what it observed.

    The census refuses a non-parsed outcome with no evidence, so the observed content travels with the
    outcome. An artifact whose text is empty has no content to carry and reports that it had none,
    which is the observation rather than text this parser wrote.
    """

    observed = _utf8_safe(_bounded(text, PROSE_MAX_LENGTH))
    return ParsedArtifact(
        artifact_path=artifact_path,
        artifact_kind=ARTIFACT_KIND_OTHER,
        outcome=outcome,
        unparsed_content=observed if observed.strip() else NOTHING_OBSERVED,
        front_matter=None,
        title=None,
        sections=(),
        citation_keys=(),
        references=(),
    )


def _is_utf8_text(text: str) -> bool:
    """Return whether this text is the text of a UTF-8 byte sequence."""

    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _utf8_safe(text: str) -> str:
    """Return the observed text with any byte no UTF-8 decoder produced shown escaped.

    Text escapes this call unchanged; only a surrogate left by a lossy decode is rewritten, and it is
    rewritten rather than dropped, so the stored evidence still says what was there. A value carrying
    a lone surrogate is not serializable evidence, and a record that cannot be written is a worse
    report than one that spells the byte out.
    """

    return text.encode("utf-8", errors="backslashreplace").decode("utf-8")


def _table_cells(line: str) -> list[str]:
    """Return the trimmed cells of one Markdown table row, or ``[]`` if the line is not one."""

    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    body = stripped[1:-1] if stripped.endswith("|") else stripped[1:]
    return [cell.strip() for cell in body.split("|")]


def _is_separator_row(lines: list[str], index: int) -> bool:
    """Return whether the line is the two-column separator row a declared table must carry."""

    if index >= len(lines):
        return False
    cells = _table_cells(lines[index])
    return len(cells) == 2 and all(_SEPARATOR_CELL.match(cell) for cell in cells)


def _first_index(lines: list[str], pattern: re.Pattern[str]) -> int | None:
    """Return the zero-based line index of the first line the pattern matches, or ``None``."""

    for index, line in enumerate(lines):
        if pattern.match(line):
            return index
    return None


def _section_body(lines: list[str], start: int, stop: int) -> str:
    """Return one section's own text: the lines after its heading, up to the next heading."""

    return "\n".join(lines[start:stop]).strip()


def _unwrapped(value: str) -> str:
    """Return a declared value with the table's code-span quoting removed and nothing else changed.

    Only a value that is ONE code span is unwrapped -- both ends backticked with no backtick between
    them. A value quoting two spans is left verbatim, because stripping its outer characters would
    edit the declaration the artifact made rather than remove the table's quoting from it.
    """

    stripped = value.strip()
    if stripped.startswith("`") and stripped.endswith("`") and stripped.count("`") == 2:
        return stripped[1:-1].strip()
    return stripped


def _bounded(text: str, limit: int) -> str:
    """Return the observed text cut to the width the stored payload declares.

    The cap is a storage bound and not an interpretation: the value stays the artifact's own prefix,
    unmarked and unelided, because a marker appended here would be this parser writing text into a
    field whose meaning is what the artifact itself said.
    """

    return text[:limit]
