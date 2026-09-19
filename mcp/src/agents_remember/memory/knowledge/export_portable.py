"""The one deterministic logical encoder, and the portable artifact it produces.

There is exactly one canonical logical encoding in this package and
:mod:`agents_remember.memory.knowledge.logical` owns it. This module does not define a second one:
it puts that same body into a **portable envelope** and reads it back under a validation strict
enough that nothing which is not a complete export can be mistaken for one.

The envelope exists because a dataset identity is not a file. Two databases hold the same knowledge
when their canonical logical bodies are equal, whatever SQLite's page layout, journal state, file
mtime or path happens to be -- so the artifact that carries knowledge between databases carries
exactly the body the digest seals, plus the header that says which schema generation and which
format that body belongs to:

```json
{
  "format": "ar-knowledge-export/v1",
  "schema": "ar-knowledge-sqlite/v1",
  "userVersion": 1,
  "schemaFingerprint": "<the supported schema fingerprint>",
  "repositoryId": "<the namespace every row is bound to>",
  "logicalDigest": "<sha256 of the canonical logical body>",
  "tables": {"repository": [], "...": []}
}
```

Four properties are the whole contract, and each is a decision rather than a convention:

* **Every manifest key is present even when its array is empty.** "Examined and had nothing to
  carry" and "never mentioned" are different facts, and only the first is a complete export.
* **Rows are objects whose keys are the declared columns in declared order.** A portable artifact is
  read by something that is not this code, so a row is self-describing; but the order and the exact
  key set are still checked, so an artifact that renamed, reordered or dropped a column is refused
  rather than silently re-mapped.
* **Typed JSON columns cross as decoded JSON values, in the canonical key order.** The stored text of
  a JSON column may spell the same value several ways; the value is what is knowledge, so both the
  digest and the artifact carry the value. Every nested object inside such a value is rendered with
  its keys sorted -- the digest encoding's own order. Every other column crosses as its exact stored
  text, so Unicode, line endings and nulls are preserved rather than normalised.
* **The canonical rendering is the only form the reader accepts.** Parsing is not acceptance: the
  reader re-renders the document it parsed and refuses the artifact unless the text it was handed is
  that rendering exactly. Whitespace, a JSON escape that spells the same character, a reordered
  envelope field, a reordered table and a nested object key out of sorted order are each a different
  *document* of one dataset, and all of them are refused. The rendering keeps each header scalar's
  **declared JSON type**, because a header *declaration* is not a spelling: the one non-string
  header scalar, ``userVersion``, is the generation the artifact claims to be, and a document that
  spells this build's generation as ``1.0`` or ``true`` -- values a Python comparison calls equal to
  ``1`` -- is refused by the header check **by name**, as an unsupported generation
  (``_validate_header``), rather than as a rendering difference. Acceptance is therefore the gate
  *and* the header check, and together they make the accepted bytes a function of the dataset rather
  than of the producer, and the promise below true instead of approximate.
* **The digest excludes itself.** It seals the schema fingerprint and the complete logical records;
  it cannot also seal the field that carries it.

The two rules together are one guarantee, stated here in the form a consumer may rely on and nowhere
in a weaker or stronger one:

> **Every accepted artifact is the canonical rendering of the logical content it carries.** Two
> artifacts that both validate and declare the same ``logicalDigest`` are therefore the same bytes,
> and a dataset restored from an accepted artifact re-encodes to that artifact byte for byte.

The digest and the document cover different things, and a consumer comparing a hash should know
which is which. **Inside** ``logicalDigest``: the schema fingerprint and every canonical record --
every table's rows, in the digest encoding's sorted-key order. **Outside** it, and pinned by the
document form instead: the envelope's own field order, the declared manifest order of the tables,
each row's declared column order, the canonical spelling of every value (separators, no
insignificant whitespace, the literal Unicode and the sorted nested keys the kernel's canonical
encoding chooses), and **the JSON type of every header value**. The digest does not cover the
header's types; what covers them is the reader -- ``format``, ``schema`` and ``schemaFingerprint``
are each compared for equality against the one string this build implements, ``logicalDigest`` and
``repositoryId`` are each required to be a non-empty/well-formed string, ``tables`` is required to be
a JSON object, and ``userVersion`` is compared against the supported generation **type-strictly**, so
a header value that is not the encoder's type is refused rather than compared equal. Two artifacts
can therefore share a digest while differing on the last axes -- and the reader refuses every such
artifact, which is exactly why a byte or content-hash comparison across *accepted* artifacts is
sound.

What the artifact deliberately does not carry is as load-bearing as what it does: no SQLite page
order, no header counters, no mtimes, no filesystem paths, no Git commit, no ledger row and no
rendered view. A reader of this module therefore knows -- from the documented boundary -- that Git
ancestry and memory-commit attribution are not in it, and neither is any authority the receiving
context might be tempted to infer.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.kernel.canonical_json import CANONICAL_JSON_KWARGS
from agents_remember.memory.knowledge import logical
from agents_remember.memory.knowledge.export_refusals import (
    invalid_export_refusal,
    non_canonical_export_refusal,
    unsupported_schema_refusal,
)
from agents_remember.memory.knowledge.refusals import RefusalFacts
from agents_remember.memory.knowledge.schema_generations import (
    SchemaGeneration,
    declared_columns_for,
    declared_json_columns_for,
    declared_version_string,
    generation_for_key,
    generation_for_name,
    registered_key_listing,
)
from agents_remember.models.knowledge.portable import PortableValidation
from agents_remember.models.knowledge.result import KnowledgeRefusal

# The portable format this module writes and the only one it reads. A future shape is a new member,
# never an accepted variant of this one: an artifact whose format is unrecognised is refused rather
# than interpreted through the closest reader available.
EXPORT_FORMAT = "ar-knowledge-export/v1"

# The envelope's own keys. A document carrying a key outside this set is not an export of this
# format, whatever it holds; the check is a set comparison and not a tolerant read.
ENVELOPE_KEYS: tuple[str, ...] = (
    "format",
    "schema",
    "userVersion",
    "schemaFingerprint",
    "repositoryId",
    "logicalDigest",
    "tables",
)

# The documented boundary an export carries for its reader. They are returned on the result rather
# than embedded in the artifact: the artifact holds knowledge, and a note about what it is not is a
# statement of this contract, not a record of the dataset.
PORTABLE_NOTES: tuple[str, ...] = (
    "A complete logical export holds every canonical table, including the empty ones.",
    "It preserves stored IDs, provenance and state_at_origin as data; importing it grants no "
    "acceptance and promotes no record.",
    "It carries no Git ancestry, no memory-commit attribution and no ledger cache; a restored "
    "dataset is knowledge, not history.",
    "A filtered read response or a Markdown projection is not this format and does not validate.",
)

# The document rendering: canonical in every respect except key sorting, which is omitted on purpose
# because the declared column order is part of this format and the canonical sorted form would erase
# it. Stated as a delta from the kernel's canonical encoding so the two cannot drift apart silently:
# sorting is the *only* decision this format departs from, so the separators, the literal Unicode and
# the no-NaN policy are the kernel's, and the reader's re-rendering below uses this same mapping.
DOCUMENT_JSON_KWARGS: dict[str, Any] = {
    **CANONICAL_JSON_KWARGS,
    "sort_keys": False,
}

_DIGEST_PREFIX = "sha256:"
_MAX_RENDERED_VALUE = 120


@dataclass(frozen=True)
class ExportDocument:
    """One artifact that is a well-formed document of a supported generation.

    The distinction this type draws is the reason :func:`parse_export` and :func:`validate_export`
    are separate: duplicate JSON keys, unknown envelope fields and an unsupported declared
    generation are defects of the *document*, and they are detected here. Whether the tables inside
    it are a complete, typed, internally consistent dataset is :func:`validate_export`'s question,
    and it is asked only of a document that already passed this one.
    """

    repository_id: str
    schema_fingerprint: str
    logical_digest: str
    tables: dict[str, Any]
    # The generation the artifact **declares**, selected from the artifact and carried from here on.
    # Every table list, column order, key registry and JSON-column registry below reads it, so an
    # artifact is never read under the running build's generation.
    generation: SchemaGeneration


@dataclass(frozen=True)
class ValidatedExport:
    """One validation's report together with the rows it decoded, or the refusal that ended it.

    Both halves are returned by one call on purpose. A caller that validated through one reading of
    an artifact and then loaded a second reading would be importing rows nobody checked; here the
    rows that were checked are the rows that get loaded.
    """

    validation: PortableValidation | None = None
    tables: dict[str, list[dict[str, Any]]] | None = None
    refusal: KnowledgeRefusal | None = None

    def __post_init__(self) -> None:
        resolved = self.validation is not None and self.tables is not None
        if resolved == (self.refusal is not None):
            raise ValueError(
                "a validated export carries either a report and its rows, or the refusal that "
                "ended the validation -- never both and never neither"
            )


def export_envelope(
    tables: Mapping[str, list[dict[str, Any]]],
    repository_id: str,
    *,
    generation: SchemaGeneration,
    logical_digest: str | None = None,
    schema_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Build the portable envelope around one already-encoded logical table mapping.

    Each row is re-keyed into its table's **declared column order**, each typed JSON value's nested
    objects are put into the **canonical key order**, and the tables are laid out in the **declared
    manifest order**. All three are part of the format rather than cosmetic: a reader of this
    artifact checks them, because a reordered row is how a silently re-mapped column would arrive,
    and an artifact this package writes must pass this package's own reader. That property is what
    makes the round trip a proof rather than a coincidence, and canonicalising the values here is
    what makes it hold for a dataset whose stored JSON text was spelled in another order.

    ``logical_digest`` defaults to the digest of the body these tables form, computed through the
    one encoder in :mod:`logical`. The parameter exists for the export path, which has already
    computed it from the live dataset and must not compute a second, differently-scoped value.
    """

    ordered = {
        table: _ordered_rows(
            tables[table],
            generation.columns[table],
            generation.json_columns.get(table, frozenset()),
        )
        for table in generation.tables
    }
    return {
        "format": EXPORT_FORMAT,
        "schema": generation.schema_name,
        "userVersion": generation.user_version,
        "schemaFingerprint": schema_fingerprint or generation.fingerprint,
        "repositoryId": repository_id,
        "logicalDigest": logical_digest or logical.logical_digest_of_tables(generation, ordered),
        "tables": ordered,
    }


def _ordered_rows(
    rows: list[dict[str, Any]],
    columns: tuple[str, ...],
    json_columns: frozenset[str],
) -> list[dict[str, Any]]:
    """Return one table's rows keyed in declared column order, with typed values canonicalised.

    Two orders are at stake and only one of them is the source's. The *columns* are re-keyed into
    the declared order, because that order is part of this format and the reader checks it. A typed
    JSON column's *nested* objects, by contrast, are put into the canonical (sorted) key order,
    whatever order the stored text happened to spell them in -- the stored spelling of a JSON value
    is not knowledge, so two datasets holding the same value must render as the same bytes. Without
    this step an export of a dataset whose JSON text was written in another order would be a document
    this package's own reader refuses.
    """

    return [
        {
            column: _canonical_json_value(row[column]) if column in json_columns else row[column]
            for column in columns
        }
        for row in rows
    ]


def _canonical_json_value(value: Any) -> Any:
    """Return one typed JSON value with every nested object key in the canonical (sorted) order."""

    if isinstance(value, Mapping):
        return {key: _canonical_json_value(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical_json_value(item) for item in value]
    return value


def encode_export(envelope: Mapping[str, Any]) -> str:
    """Render one envelope as the exact portable text, deterministically.

    **This is deliberately not the digest encoding.** The canonical form the digests use sorts
    object keys, which would destroy the declared column order this format carries and this reader
    checks; the two encodings answer two different questions. The *digest* must be insensitive to
    how a value was spelled, so it sorts keys recursively. The *document* keeps the declared order at
    the two levels where order is declared -- the envelope's header and each row's columns -- so it
    renders with ``sort_keys=False``, and the nested objects inside a typed JSON value arrive here
    already canonicalised by :func:`export_envelope`.

    **Header scalars are rendered from the value this build validates, not from the JSON type the
    caller's mapping happens to hold.** ``userVersion`` is the envelope's only non-string scalar and
    the only field a Python comparison can lose: ``1.0`` and ``true`` both equal ``1``, so a mapping
    that declares this format's generation under either of them would otherwise be written out
    type-verbatim, and the published encoder could emit an artifact its own reader must refuse. A
    declaration that is *not* this generation (``2``, ``"1"``) is written exactly as it was given, so
    the reader refuses it by name as an unsupported generation rather than as a spelling difference.

    What this does keep from the canonical form is every other decision: compact separators, literal
    Unicode rather than escapes, and no NaN. It renders the envelope it is given, so the bytes of one
    dataset are stable for a given envelope; what makes them stable for the *dataset* is that the
    builder canonicalises typed values and the reader accepts only the canonical form.
    """

    return _render_document(_validated_header(envelope))


def _validated_header(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Return one envelope with its header scalars spelled the way this build validates them.

    The rule is deliberately narrow. It rewrites exactly the values a *loose* comparison would call
    the declared generation -- ``1.0``, ``true`` -- into the integer the header check validates, and
    it leaves every other declaration untouched, including a different generation, a differently
    typed string, and a number that is not the declared version at all. A boolean is always
    rewritten, because no JSON boolean is ever a version this store writes. The reader applies the same rule's strict half: the rendering it
    compares an artifact against keeps the declared type, and the header check then refuses a
    respelled generation by name. Between them, neither the reader accepts the form nor the encoder
    emits it.
    """

    # The frame is the generation the envelope's **schema name** declares, rather than the
    # ``(schema, userVersion)`` pair: the respelling this repairs is precisely the version's JSON
    # type, so the pair does not resolve and a pair-based frame would leave the respelling in place.
    # For a generation-1 envelope this is generation 1 and the behaviour is the shipped one.
    declared = generation_for_name(envelope.get("schema"))
    return {
        key: declared.user_version
        if key == "userVersion"
        and declared is not None
        and type(value) is not int
        and (isinstance(value, bool) or value == declared.user_version)
        else value
        for key, value in envelope.items()
    }


def document_generation(document: Mapping[str, Any]) -> SchemaGeneration | None:
    """Return the generation one document's header declares, or ``None`` when none is registered.

    A document whose generation is unregistered is refused one step later by the header check, so
    this returning ``None`` is not a silent fallback to another generation: it means the rendering
    below has no declared table order to impose and keeps the document's own key order, which is
    exactly what an artifact of an unknown shape is rendered as before it is refused.
    """

    return generation_for_key(document.get("schema"), document.get("userVersion"))


def _render_document(envelope: Mapping[str, Any]) -> str:
    """Render one envelope mapping as portable text, with every value exactly as it is held."""

    return json.dumps(dict(envelope), **DOCUMENT_JSON_KWARGS)


def _plain_envelope(document: Mapping[str, Any]) -> dict[str, Any]:
    """Return one decoded document as the plain envelope mapping this module renders.

    The document's *declared* key values are kept -- including their JSON types -- and only the
    containers the format declares an order for are rebuilt: the envelope itself in the declared key
    order, and the table mapping through :func:`_plain_tables`.
    """

    return {
        key: _plain_document_value(document[key]) if key != "tables" else _plain_tables(document)
        for key in ENVELOPE_KEYS
        if key in document
    }


def canonical_document(text: str) -> str | None:
    """Return the canonical rendering of one artifact's text, or None when it is not an artifact.

    Two callers, one rule. :func:`encode_export` renders an envelope this module built; this function
    renders the *document a reader was handed*, from the values it decoded, through that same
    encoder -- so it is the one form this format accepts, spelled as this build spells it. It is also
    what the canonical-form guarantee is checked against in the evidence: every accepted artifact
    satisfies ``text == canonical_document(text)``.

    Equality with this rendering is **necessary and not sufficient** for acceptance, and the
    difference is worth stating rather than implying: the rendering pins the document's *order and
    spelling* and renders the generation from the validated value, while ``format``, ``schema`` and
    ``schemaFingerprint`` are compared by the header check *by value* against the one generation this
    build implements. A document that declares another schema or another format renders equal to
    itself here and is still refused, correctly, as an unsupported generation rather than as a
    misspelling of this one.

    The rendered document is built rather than reused, so a document whose key order the encoding
    would not produce -- a reordered envelope, a reordered table mapping -- renders in the declared
    order and differs from the text. Values are copied into plain Python values first: the decoded
    objects remember their source order for the declared-column check, and a JSON mapping is rendered
    in the order it was built. ``None`` means the text is not a renderable artifact of this format at
    all: not a JSON object, or one carrying a number (``NaN``, an infinity) the canonical encoding
    has no spelling for.
    """

    parsed = _parsed_document(text)
    if parsed is None:
        return None
    try:
        return encode_export(_plain_envelope(parsed))
    except ValueError:
        return None


def _render_declared_document(document: Mapping[str, Any]) -> str:
    """Render one decoded artifact document as the text this reader compares an artifact against.

    Two differences from :func:`canonical_document`, both deliberate. The header is rendered **as the
    document declared it**, including each scalar's JSON type, and the raw renderer is used rather
    than the encoder's header rule: this rendering answers "is the text spelled the way the document
    it holds spells itself?", which is the question the gate asks, and its answer must not depend on
    rewriting the one field whose *declaration* the next check compares by type. Rendering
    ``userVersion`` from the validated integer here would make the gate refuse a respelled generation
    as a spelling difference and hide what is actually wrong with the artifact -- that it claims a
    generation under a JSON type this build does not implement -- which the header check refuses by
    name one step later.
    """

    return _render_document(_plain_envelope(document))


def _plain_tables(document: Mapping[str, Any]) -> dict[str, Any]:
    """Return one document's table mapping in the declared manifest order, as canonical rows.

    Every level of the format is rebuilt here rather than copied, because a rendering that copied the
    document's own order would compare equal to it and the gate would be vacuous on the axes this
    format actually declares:

    * the **manifest order** below is the declared table order, not the order the document spelled;
    * each row is re-keyed into its table's **declared column order** and each typed JSON value's
      nested objects are re-sorted, through :func:`_ordered_rows` -- the same function the encoder
      uses, so the rendering and the encoder cannot drift apart about what "canonical" means.

    A table the manifest does not declare keeps the position the document gave it, because that
    document is refused as carrying an undeclared table before this rendering is ever compared -- the
    rendering must not silently drop content the document holds, or an artifact with an extra table
    would be refused for the wrong reason. The same reasoning applies to a row whose keys are not the
    declared columns: it is refused by the row check one step later, and this rendering must not
    reorder it into looking canonical.
    """

    tables = document["tables"]
    if not isinstance(tables, Mapping):
        return _plain_document_value(tables)
    generation = document_generation(document)
    declared_tables = generation.tables if generation is not None else ()
    declared = [table for table in declared_tables if table in tables]
    undeclared = [table for table in tables if table not in declared_tables]
    return {table: _canonical_rows(table, tables[table]) for table in (*declared, *undeclared)}


def _canonical_rows(table: str, rows: Any) -> Any:
    """Return one table's rows with declared column order and canonical typed values.

    The column frame comes from the document's own generation when it declares a registered one, and
    otherwise from the registry as a whole. The fallback is not a fallback *between generations*: the
    gate this rendering serves runs before the header check, and it is deciding whether the text is
    the canonical spelling of the document it holds. Without the registry frame a document whose
    header is about to be refused would be rendered with its own nested spellings intact, so a
    respelling inside it would be invisible and the artifact would be refused for the wrong reason.
    """

    if not isinstance(rows, list):
        return _plain_document_value(rows)
    plain = _plain_document_value(rows)
    if not all(isinstance(row, Mapping) for row in plain):
        return plain
    columns = declared_columns_for(table)
    if columns is None:
        return plain
    if not all([key for key in row] == list(columns) for row in plain):
        return plain
    return _ordered_rows(plain, columns, declared_json_columns_for(table) or frozenset())


def _plain_document_value(value: Any) -> Any:
    """Return one decoded document value as an ordinary Python value."""

    return value.plain() if isinstance(value, _Document) else _plain_value(value)


def _parsed_document(text: str) -> Mapping[str, Any] | None:
    """Return one artifact text as a JSON object, or None when it is not one."""

    if not isinstance(text, str):
        return None
    try:
        document = json.loads(text, object_pairs_hook=_Document)
    except ValueError:
        return None
    return document if isinstance(document, _Document) else None


def parse_export(text: str) -> ExportDocument | KnowledgeRefusal:
    """Read one artifact's document shape, or refuse it as malformed, unsupported or non-canonical.

    Four failures are refused here and nowhere else, because all four are properties of the JSON
    text and its header rather than of the dataset it claims to describe. They are checked in this
    order, and the order is the contract:

    1. **Duplicate keys and unparsable text.** A document whose object repeats a key is ambiguous
       about the value it holds, and JSON's last-one-wins read would silently pick one. This is the
       same rule the store applies to a stored JSON column, applied to the artifact.
    2. **Unknown or missing envelope fields.** The key set is compared, not sampled: an artifact with
       an extra field is not this format, and one missing ``logicalDigest`` is not a complete export
       that merely lacks a convenience.
    3. **A text that is not the rendering of the document it holds.** This is the gate the guarantee
       rests on and it runs on the whole document rather than on one level of it: the reader
       re-renders what it parsed through :func:`_render_declared_document` and refuses the artifact
       unless the text it was handed is that rendering exactly. It is exhaustive by construction --
       every difference a document can carry (insignificant whitespace, a JSON escape that spells the
       same character, a reordered envelope field, a reordered table mapping, a nested object key out
       of sorted order) is a difference between the text and its rendering -- so nothing about the
       *spelling* of an artifact can reach the checks below unexamined. The one thing it does not
       decide is a header *declaration*'s JSON type, which is a claim rather than a spelling: the
       rendering keeps what the document declared, and check 4 refuses a generation this build does
       not implement by name. A document the canonical encoding has no spelling for at all (a number
       that decodes to an infinity, ``NaN``) is refused here too, as a typed refusal rather than a
       raised ``ValueError``.
    4. **A wrong or unsupported declared generation.** ``format``, ``schema``, ``userVersion`` and
       ``schemaFingerprint`` are each compared against the manifest this code implements -- a version
       string alone is not evidence that the tables match it, and ``userVersion`` is compared
       **type-strictly**, so a generation respelled as ``1.0`` or ``true`` is refused rather than
       compared equal.

    The **manifest** (every canonical table present, no undeclared one) is checked between the
    envelope's own key set and the gate, because an undeclared table has no place in this format's
    rendering and would otherwise be refused for the spelling difference it causes rather than for
    what it is.
    """

    if not isinstance(text, str) or not text.strip():
        return _malformed("the artifact is empty", "<empty>")
    try:
        document = json.loads(text, object_pairs_hook=_Document)
    except ValueError as error:
        return _malformed(f"the artifact is not valid JSON: {error}", "<unparsable>")
    shaped = _shape_refusal(document)
    if shaped is not None:
        return shaped
    incomplete = _manifest_refusal(document, document_generation(document))
    if incomplete is not None:
        return incomplete
    misrendered = _non_canonical_refusal(text, document)
    if misrendered is not None:
        return misrendered
    return _read_envelope(document)


def _shape_refusal(document: Any) -> KnowledgeRefusal | None:
    """Refuse a document that is not a JSON object, or whose envelope key set is not this format's.

    One helper for the three document-shape checks, so the reading list above stays readable as the
    four decisions it is: a value that is not an object at all, an artifact carrying a field this
    format does not declare, and one that omits a required field are three ways of saying "this is not
    a document of this format", and none of them is about the dataset inside.
    """

    if not isinstance(document, Mapping):
        return _malformed("the artifact is not a JSON object", "<not-an-object>")
    unknown = [key for key in document if key not in ENVELOPE_KEYS]
    if unknown:
        return _malformed(
            f"the artifact carries field(s) {', '.join(sorted(unknown))} that are not part of "
            f"{EXPORT_FORMAT}",
            _short(unknown[0]),
        )
    missing = [key for key in ENVELOPE_KEYS if key not in document]
    if missing:
        return _malformed(
            f"the artifact does not carry {', '.join(missing)}, so it is not a complete export",
            _short(missing[0]),
        )
    return None


def _manifest_refusal(
    document: Mapping[str, Any], generation: SchemaGeneration | None
) -> KnowledgeRefusal | None:
    """Refuse a document whose table set is not this schema generation's manifest.

    This runs *before* the canonical-form gate, which is a decision rather than an accident: a
    document carrying an undeclared table is a document of *another shape*, and the canonical
    rendering of this format has nowhere to put that table. Checking the manifest first means such
    an artifact is refused for carrying a table this generation does not declare -- naming it --
    rather than for the spelling difference its extra table would otherwise cause.
    """

    tables = document["tables"]
    if not isinstance(tables, Mapping):
        return _row_refusal("the artifact's tables are not a JSON object", table="tables")
    # A document whose declared generation is unregistered has no table manifest to compare against,
    # and is refused by the header check one step later; comparing it against a *different*
    # generation's manifest here would name the wrong failure.
    if generation is None:
        return None
    unknown = [name for name in tables if name not in generation.tables]
    if unknown:
        return _row_refusal(
            f"the artifact carries table(s) {', '.join(sorted(unknown))} which this schema "
            "generation does not declare",
            table=_short(unknown[0]),
        )
    missing = [name for name in generation.tables if name not in tables]
    if missing:
        return _row_refusal(
            f"the artifact does not carry {', '.join(missing)}; a complete export includes every "
            "canonical table, including the empty ones",
            table=_short(missing[0]),
        )
    return None


def _non_canonical_refusal(text: str, document: Mapping[str, Any]) -> KnowledgeRefusal | None:
    """Refuse one well-formed document whose own text is not the rendering of what it holds.

    The comparison is a whole-document one, so a caller never has to guess which level was wrong;
    the detail names the axes instead, because they are what a producer has to change. Nothing is
    repaired here: an artifact this format did not write in the one accepted form is not silently
    normalised into it, since normalising would accept a document nobody can reproduce from the
    bytes that were handed over.

    A document the canonical encoding has **no spelling for** is refused here as well, rather than
    raised: a JSON number that decodes to an infinity (``1e400``, ``-1e400``) and the non-JSON
    constants Python's decoder accepts by default (``NaN``, ``Infinity``, ``-Infinity``) all reach
    this point as floats the canonical encoding refuses to write, and a raw ``ValueError`` escaping
    here would leave a caller of :func:`parse_export`, ``validate_knowledge_artifact`` or
    ``import_knowledge_dataset`` with a failure that has no code to branch on. The typed refusal is
    the same one a malformed artifact gets, and it is produced before any staging work, so the
    destination is still untouched.
    """

    try:
        rendering = _render_declared_document(document)
    except ValueError as error:
        return _malformed(
            f"the artifact carries a value the canonical encoding cannot spell, so it is not a "
            f"document of {EXPORT_FORMAT}: {error}",
            "<unrenderable>",
        )
    if text == rendering:
        return None
    return non_canonical_export_refusal(
        "import_knowledge_dataset",
        _canonical_difference(text, rendering),
    )


def _canonical_difference(text: str, rendering: str) -> str:
    """Return the bounded statement of how one text differs from its canonical rendering."""

    shared = 0
    for observed, expected in zip(text, rendering, strict=False):
        if observed != expected:
            break
        shared += 1
    if len(text) != len(rendering):
        shape = f"{len(text)} characters where the canonical rendering has {len(rendering)}"
    else:
        shape = f"{len(text)} characters in both"
    return (
        f"the artifact's text is not the canonical rendering of the document it carries: it "
        f"diverges at character {shared} and holds {shape}. This format accepts exactly one "
        "spelling of a dataset -- compact separators, no insignificant whitespace, the declared "
        "envelope and manifest order, and sorted keys inside every typed JSON value -- so an "
        "artifact that differs in any of them is refused rather than silently re-rendered into "
        "that form. Re-encode the dataset with this package's encoder and import that artifact."
    )


def validate_export(
    document: ExportDocument, *, expected_repository_id: str | None = None
) -> ValidatedExport:
    """Validate one parsed export as a complete, typed, self-consistent logical dataset.

    Every check below answers a question a different failure would otherwise answer silently:

    * **Declared column order.** A row's keys must be exactly the declared columns in the declared
      order. A renamed, reordered, missing or extra column is a mismatch between the artifact and
      the schema, not a value to be coerced into place.
    * **Declared column types.** Text columns carry strings, JSON columns carry JSON values, and a
      nullable column may carry null. Anything else is a value the schema cannot hold.
    * **The canonical key order of a typed JSON value.** Every nested object inside one is rendered
      with its keys sorted. This is now **defence in depth and unreachable from this reader's public
      entry point**: :func:`parse_export` refuses any text that is not the exact canonical rendering
      of its document, so a value whose nested keys are out of order is refused one step earlier, by
      the whole-document gate, before any table is read. The check is kept because it states the rule
      where the value is typed and would be the guard a caller of this function that did not come
      through :func:`parse_export` needs; the evidence states the ordering rather than assuming it
      (annex §7.3), and no case claims it as the reason an artifact was refused.
    * **Primary-key uniqueness.** Two rows with one declared key are one identity claimed twice,
      and the engine would report it as a constraint error long after the artifact was accepted as
      structurally sound.
    * **The declared seal and the declared namespace.** The declared digest must be the digest of
      the records the artifact actually carries, and the namespace it declares must be the one
      repository row it holds -- a mismatch means the artifact describes a dataset other than the
      one inside it.

    The **complete manifest** and the **declared generation** are asked one step earlier, in
    :func:`parse_export`, because both are properties of the document's shape rather than of the
    dataset inside it -- and because the canonical-form gate has to run after them: the rendering of
    a document with an undeclared table has no place to put it, so a manifest check placed here would
    never be reached.
    """

    tables = document.tables
    decoded: dict[str, list[dict[str, Any]]] = {}
    for table in document.generation.tables:
        rows = tables[table]
        if not isinstance(rows, list):
            return _invalid("the artifact's rows for this table are not a JSON array", table=table)
        checked = _validate_table_rows(table, rows, document.generation)
        if isinstance(checked, KnowledgeRefusal):
            return ValidatedExport(refusal=checked)
        decoded[table] = checked
    return _validate_dataset(document, decoded, expected_repository_id=expected_repository_id)


def body_of(document: ExportDocument) -> dict[str, Any]:
    """Return the canonical logical body one artifact carries, through the one encoder."""

    return logical.logical_body_from_tables(document.generation, document.tables)


def logical_body_of_artifact(text: str) -> dict[str, Any]:
    """Return the canonical logical body of one artifact's text, or raise on a refused artifact.

    A recovery recipe, a comparison and an importer all need the same body, and they get it the same
    way: this is a convenience over :func:`parse_export` + :func:`body_of`, not a second reader.
    """

    parsed = parse_export(text)
    if isinstance(parsed, KnowledgeRefusal):
        raise ValueError(f"{parsed.code}: {parsed.detail}")
    return body_of(parsed)


def artifact_digest(text: str) -> str:
    """Return the sha256 of one artifact's exact UTF-8 bytes."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    """Return the sha256 of one file's exact bytes, as publication records it."""

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_envelope(document: _Document) -> ExportDocument | KnowledgeRefusal:
    """Check one well-formed document's declared generation and return its portable content."""

    header = _validate_header(document)
    if isinstance(header, KnowledgeRefusal):
        return header
    return _read_tables(document, header)


def _validate_header(document: _Document) -> SchemaGeneration | KnowledgeRefusal:
    """Refuse a header that does not declare this format and this schema generation.

    Four declarations are compared against the manifest this build implements. A version string
    alone is not evidence that the tables match it, which is why the fingerprint is checked too:
    two databases can both say ``userVersion: 1`` and disagree about a column type or a trigger.

    The generation comparison is **type-strict** on purpose. ``userVersion`` is the envelope's only
    non-string scalar, and Python's ``==`` calls ``1.0 == 1`` and ``True == 1`` true, so a plain
    inequality would let an artifact declare the supported generation under a JSON type this build
    never writes and be compared equal -- an accepted artifact that is not the encoder's bytes. The
    type is therefore part of the declaration: anything that is not an ``int`` is refused here, by
    name, as an unsupported generation. The other three declarations need no such guard, because
    each is compared for equality against a *string*, and no JSON value other than that exact string
    equals it.
    """

    declared_format = document["format"]
    if declared_format != EXPORT_FORMAT:
        return _row_refusal(
            f"the artifact declares format {declared_format!r}; this reader implements "
            f"{EXPORT_FORMAT!r}",
            record_id=_short(declared_format),
        )
    declared_schema = document["schema"]
    declared_version = document["userVersion"]
    # Dispatch selects the generation **from the artifact**, type-strictly on ``userVersion``. The
    # type strictness is preserved rather than re-derived: in Python ``1 == 1.0 == True`` and all
    # three hash alike, so a lookup that was not type-strict would resolve the spellings ``1.0`` and
    # ``true`` to generation 1 -- which the reader deliberately refuses.
    generation = generation_for_key(declared_schema, declared_version)
    if generation is None:
        if generation_for_name(declared_schema) is None:
            return unsupported_schema_refusal(
                "import_knowledge_dataset",
                f"the artifact declares schema {declared_schema!r}; this store implements one of "
                f"{registered_key_listing()}",
                expected=registered_key_listing(),
                observed=_short(declared_schema),
            )
        return unsupported_schema_refusal(
            "import_knowledge_dataset",
            f"the artifact declares user version {declared_version!r}; the generation "
            f"{declared_schema!r} is spelled as the JSON integer "
            f"{declared_version_string(declared_schema)}, not {declared_version!r}",
            expected=declared_version_string(declared_schema),
            observed=_short(declared_version),
        )
    fingerprint = document["schemaFingerprint"]
    if fingerprint != generation.fingerprint:
        return unsupported_schema_refusal(
            "import_knowledge_dataset",
            "the artifact was written for a different schema generation: its fingerprint is not "
            "the declared generation's",
            expected=generation.fingerprint,
            observed=_short(fingerprint),
        )
    return generation


def _read_tables(
    document: _Document, generation: SchemaGeneration
) -> ExportDocument | KnowledgeRefusal:
    """Read the digest, the declared namespace and the table mapping out of one checked header."""

    digest = document["logicalDigest"]
    if not isinstance(digest, str) or not _is_sha256(digest):
        return _row_refusal(
            "the artifact's logicalDigest is not a sha256 digest", record_id=_short(digest)
        )
    repository_id = document["repositoryId"]
    if not isinstance(repository_id, str) or not repository_id:
        return _row_refusal(
            "the artifact declares no repository namespace", record_id=_short(repository_id)
        )
    return ExportDocument(
        repository_id=repository_id,
        schema_fingerprint=str(document["schemaFingerprint"]),
        logical_digest=digest,
        tables=document["tables"],
        generation=generation,
    )


def _validate_table_rows(
    table: str, rows: list[Any], generation: SchemaGeneration
) -> list[dict[str, Any]] | KnowledgeRefusal:
    """Check one table's rows for declared column order, declared types and unique primary keys."""

    columns = generation.columns[table]
    keys = generation.primary_keys[table]
    json_columns = generation.json_columns.get(table, frozenset())
    seen: set[tuple[Any, ...]] = set()
    checked: list[dict[str, Any]] = []
    for position, row in enumerate(rows):
        if not isinstance(row, _Document):
            return _row_refusal(
                f"row {position} of this table is not a JSON object keyed by declared columns",
                table=table,
                record_id=str(position),
            )
        declared = [key for key, _ in row.ordered_items()]
        if declared != list(columns):
            return _row_refusal(
                f"row {position} declares fields {declared}, but this table's declared columns in "
                f"order are {list(columns)}",
                table=table,
                record_id=str(position),
            )
        typed = _typed_row(table, columns, json_columns, row.plain())
        if isinstance(typed, KnowledgeRefusal):
            return typed
        key = tuple(typed[column] for column in keys)
        if any(value is None for value in key):
            return _row_refusal(
                f"row {position} carries a null in its declared primary key",
                table=table,
                record_id=_render_key(key),
            )
        if key in seen:
            return _row_refusal(
                "two rows declare the same primary key, so one identity is claimed twice",
                table=table,
                record_id=_render_key(key),
            )
        seen.add(key)
        checked.append(typed)
    return checked


def _typed_row(
    table: str,
    columns: tuple[str, ...],
    json_columns: frozenset[str],
    values: Mapping[str, Any],
) -> dict[str, Any] | KnowledgeRefusal:
    """Return one row's values as the exact types its declared columns can hold."""

    typed: dict[str, Any] = {}
    for column in columns:
        value = values[column]
        if value is None:
            typed[column] = None
            continue
        if column in json_columns:
            if not isinstance(value, (str, list, dict)):
                return _row_refusal(
                    f"column {column} is a typed JSON column and cannot hold {value!r}",
                    table=table,
                    record_id=column,
                )
            misplaced = _out_of_canonical_order(value)
            if misplaced is not None:
                return _row_refusal(
                    f"column {column} carries a JSON object whose keys are not in the canonical "
                    f"(sorted) key order at {misplaced}; this format's canonical form is the only "
                    "form it accepts, so two artifacts carrying the same knowledge are the same "
                    "bytes",
                    table=table,
                    record_id=column,
                )
            typed[column] = value
            continue
        if not isinstance(value, str):
            return _row_refusal(
                f"column {column} is declared TEXT and cannot hold {value!r}",
                table=table,
                record_id=column,
            )
        typed[column] = value
    return typed


def _out_of_canonical_order(value: Any, path: str = "") -> str | None:
    """Return the path of the first nested object whose keys are not in canonical order, or None.

    The canonical order is the sorted key order the digest encoding uses, so a value's *spelling* in
    a document is either the canonical one or a different spelling of the same value. This format
    accepts only the canonical one, which is what makes the artifact bytes a function of the dataset
    rather than of the producer.

    **Unreachable from the public reader, and kept as defence in depth.** The whole-document gate in
    :func:`parse_export` refuses a text that is not the canonical rendering of its document, and a
    nested key out of sorted order is such a difference, so no artifact reaches :func:`_typed_row`
    with an out-of-order value. The function stays because it states the rule at the level where the
    value is interpreted and would be the guard any future caller of :func:`validate_export` needs;
    its recursion is exercised directly by
    ``test_the_canonical_order_check_covers_depth_two_and_lists_inside_a_value``, and the ordering is
    proved in the evidence annex (the ``V2``/``V3`` mutations now die on the document gate's
    assertion, at the depth those cases drive).
    """

    if isinstance(value, Mapping):
        keys = list(value)
        if keys != sorted(keys):
            return f"{path} {keys}".strip()
        for key in keys:
            found = _out_of_canonical_order(value[key], f"{path}.{key}".lstrip("."))
            if found is not None:
                return found
        return None
    if isinstance(value, list):
        for position, item in enumerate(value):
            found = _out_of_canonical_order(item, f"{path}[{position}]")
            if found is not None:
                return found
    return None


def _validate_dataset(
    document: ExportDocument,
    tables: Mapping[str, list[dict[str, Any]]],
    *,
    expected_repository_id: str | None,
) -> ValidatedExport:
    """Check the declared namespace, the repository binding and the declared seal."""

    rows = tables["repository"]
    if len(rows) != 1:
        return ValidatedExport(
            refusal=_row_refusal(
                "a knowledge dataset is bound to exactly one repository namespace; this artifact "
                f"holds {len(rows)} repository row(s)",
                table="repository",
            )
        )
    bound = str(rows[0]["repository_id"])
    if bound != document.repository_id:
        return ValidatedExport(
            refusal=_row_refusal(
                "the artifact declares a repository namespace its own repository row does not name",
                table="repository",
                record_id=document.repository_id,
                expected=document.repository_id,
                observed=bound,
            )
        )
    if expected_repository_id is not None and bound != expected_repository_id:
        return ValidatedExport(
            refusal=_row_refusal(
                "the artifact holds a dataset for a different repository namespace than the "
                "destination being imported into",
                table="repository",
                record_id=bound,
                expected=expected_repository_id,
                observed=bound,
            )
        )
    recomputed = logical.logical_digest_of_tables(document.generation, tables)
    if recomputed != document.logical_digest:
        return ValidatedExport(
            refusal=_row_refusal(
                "the artifact's logicalDigest does not seal the records it carries, so its content "
                "is not the dataset it claims to be",
                expected=_short(document.logical_digest),
                observed=recomputed,
            )
        )
    return ValidatedExport(
        validation=PortableValidation(
            state="validated",
            repository_id=bound,
            schema_fingerprint=document.schema_fingerprint,
            logical_digest=recomputed,
            declared_digest=document.logical_digest,
            row_counts={table: len(tables[table]) for table in document.generation.tables},
        ),
        tables={table: list(tables[table]) for table in document.generation.tables},
    )


class _Document(dict):
    """A JSON object that remembers its own key order, and refuses a repeated key.

    ``dict`` preserves insertion order, but a duplicate key silently overwrites the first one, so
    the pairs are kept as well. Preserving order is what makes "the declared column order" checkable
    at all: a row whose fields were reordered in the artifact is a different document, and refusing
    it is how a silently re-mapped row is made impossible.
    """

    def __init__(self, pairs: list[tuple[str, Any]]) -> None:
        super().__init__()
        self._pairs: list[tuple[str, Any]] = []
        for key, value in pairs:
            if key in self:
                raise ValueError(f"duplicate JSON key: {key}")
            self._pairs.append((key, value))
            super().__setitem__(key, value)

    def ordered_items(self) -> tuple[tuple[str, Any], ...]:
        """Return this object's keys and values in the order the document declared them."""

        return tuple(self._pairs)

    def plain(self) -> dict[str, Any]:
        """Return this object as an ordinary mapping, recursively.

        The decoded document has to leave this module as plain Python values: a subclass of ``dict``
        is not a value SQLite will bind, and a typed JSON column's stored text has to be produced by
        the same encoding a database write would use. Converting once, here, is what keeps "the
        value that was validated" and "the value that is stored" the same object rather than two
        readings of one artifact.
        """

        return {key: _plain_value(value) for key, value in self._pairs}


def _plain_value(value: Any) -> Any:
    """Return one decoded JSON value as an ordinary Python value."""

    if isinstance(value, _Document):
        return value.plain()
    if isinstance(value, list):
        return [_plain_value(item) for item in value]
    return value


def _is_sha256(value: str) -> bool:
    """Whether one string is a bare lowercase sha256 hex digest, optionally prefixed."""

    candidate = value.removeprefix(_DIGEST_PREFIX)
    return len(candidate) == 64 and all(character in "0123456789abcdef" for character in candidate)


def _short(value: Any) -> str:
    """Render one offending value for a refusal, bounded rather than echoed whole."""

    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= _MAX_RENDERED_VALUE else f"{text[: _MAX_RENDERED_VALUE - 3]}..."


def _render_key(key: tuple[Any, ...]) -> str:
    """Render one primary-key tuple as the readable identity a refusal carries."""

    return "/".join(str(value) for value in key)


def _malformed(detail: str, record_id: str) -> KnowledgeRefusal:
    """Refuse one document that is not a well-formed artifact of this format."""

    return invalid_export_refusal(
        "import_knowledge_dataset", detail, facts=RefusalFacts(record_id=record_id)
    )


def _row_refusal(
    detail: str,
    *,
    table: str | None = None,
    record_id: str | None = None,
    expected: str | None = None,
    observed: str | None = None,
) -> KnowledgeRefusal:
    """Refuse one row or one column of an artifact, at the row level rather than the document level.

    Row-level checks run inside the table loop and return the refusal itself; only the document-level
    checks wrap theirs in a :class:`ValidatedExport`. Keeping the two factories apart is what makes a
    row failure unable to be mistaken for a validated document -- the type system says so, and an
    earlier version of this module that used one factory for both stored a refusal where a row list
    belonged.
    """

    return invalid_export_refusal(
        "import_knowledge_dataset",
        detail,
        facts=RefusalFacts(table=table, record_id=record_id, expected=expected, observed=observed),
    )


def _invalid(
    detail: str,
    *,
    table: str | None = None,
    record_id: str | None = None,
    expected: str | None = None,
    observed: str | None = None,
) -> ValidatedExport:
    """Refuse one artifact whose content is not a complete logical dataset."""

    return ValidatedExport(
        refusal=_row_refusal(
            detail, table=table, record_id=record_id, expected=expected, observed=observed
        )
    )


__all__ = [
    "ENVELOPE_KEYS",
    "EXPORT_FORMAT",
    "PORTABLE_NOTES",
    "ExportDocument",
    "ValidatedExport",
    "artifact_digest",
    "body_of",
    "canonical_document",
    "encode_export",
    "export_envelope",
    "file_digest",
    "logical_body_of_artifact",
    "parse_export",
    "validate_export",
]
