"""The curator hand-off document the file writer reads: entries, records and history rows.

The document is JSON, one of two spellings:

* a **list** -- the producer's hand-off list exactly as today (``curator-handoff-list.md``), read as
  ``{"entries": <the list>}``;
* an **object** with up to three sections, ``entries``, ``records`` and ``history``.

**Entries** keep the producer's thirteen fields unchanged. The curator's keys beside them say what the
entry becomes: ``scope`` and ``admission`` (and optionally ``status``) author an invariant record from
the verbatim ``statement``; ``invariant_id`` names a stored invariant the entry updates instead of
creating one; ``supersedes`` names what the new invariant supersedes; ``proofs`` lists the tests that
prove it, each with the curator's ``facet``. Every ``target`` becomes a realization entry. An entry
that authors no invariant (no target, no ``invariant_id``, no ``admission``) is a ruling and writes
nothing.

**Records** are curator-authored records of any kind: ``{key, kind, entry?, id?, slug?, fields}``.
``fields`` holds the kind's own fields (``status``, ``admission``, ``links`` …); the writer owns
``id``, ``schema``, ``origin`` and ``revision`` and refuses them there.

**History** rows are ``{subject, disposition, reason, items?, covers?, effect?, because?,
examined?}``. The writer mints the row ID, fills the invariant's revision and each examined member's
revision, and writes each covered entry's ``before`` and ``after`` anchor.

A string ``"handoff:<key>"`` names the record the same document authors under that key (an entry's
``id``, or a record's ``key``) wherever an ID is expected. Reading never resolves anything: it checks
the document's own shape and collects every problem, so one refusal names them all.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final

from agents_remember.application.curator_realization_authoring import (
    EntryRealization,
    realization_refusal,
)
from agents_remember.application.curator_scope import CuratorScope, read_curator_scope
from agents_remember.models.knowledge_files.ids import RECORD_PREFIXES, RecordKind

HANDLE_PREFIX: Final = "handoff:"
WRITER_OWNED_FIELDS: Final = frozenset({"id", "schema", "origin", "revision"})
SECTIONS: Final = ("entries", "records", "history")
_ROW_KEYS: Final = frozenset(
    {"subject", "disposition", "reason", "items", "covers", "effect", "because", "examined"}
)
# The architect's ruling (L21 review, finding 6): the template's ``incidental`` has no spelling in the
# file format and is written as ``support``.
ROLE_SPELLINGS: Final = {"incidental": "support"}
_TEST_ID = re.compile(r"(?P<path>[\w./@+-]+\.py)::(?P<name>[A-Za-z_][\w]*(?:::[A-Za-z_][\w]*)*)")
# MIK-R28 rule 2's "path plus symbol": the pytest selection ``<path> -k <name>`` where the ``-k``
# expression is one bare identifier (optionally quoted). An expression (``a and not b``) names no one
# test, so it is not read as one.
_SELECTED_TEST = re.compile(
    r"(?P<path>[\w./@+-]+\.py)\s+-k\s+(?P<quote>['\"]?)(?P<name>[A-Za-z_]\w*)(?P=quote)"
    r"(?=$|[\s,;:.)\]`'\"])(?!\s+(?:and|or|not)\b)"
)
# A test module named without a test in it: a ``test_*.py`` or ``*_test.py`` file (pytest's module
# pattern), not followed by ``::``. It names no resolvable test, so it is reported rather than
# silently dropped (MIK-R28 rule 2). Helper modules (``conftest.py``, ``*_test_support.py``, any
# other module under ``tests/``) hold no tests and are never reported.
_TEST_FILE = re.compile(
    r"(?<![\w./@+-])(?P<path>(?:[\w.@+-]+/)*(?:test_[\w.@+-]*|[\w.@+-]*_test)\.py)"
    r"(?![\w./@+-]|::)"
)


@dataclass(frozen=True)
class Problem:
    """One reason the operation is refused: where it was written, and what is wrong."""

    where: str
    message: str

    def render(self) -> str:
        return f"{self.where}: {self.message}"


@dataclass(frozen=True)
class LocatorRequest:
    """A locator as the hand-off writes it, already in the file form (``symbol`` carries ``name``)."""

    document: Mapping[str, Any]

    @property
    def kind(self) -> str:
        return str(self.document.get("kind", ""))

    def describe(self) -> str:
        if self.kind == "symbol":
            return f"symbol {self.document.get('name')}"
        if self.kind == "line_range":
            return f"lines {self.document.get('start')}-{self.document.get('end')}"
        return "the whole file"


@dataclass(frozen=True)
class TargetRequest:
    """One realization a target authors: a code place, its role and its rationale."""

    position: int
    path: str
    locator: LocatorRequest
    role: str
    rationale: str


@dataclass(frozen=True)
class TestReference:
    """A test named as ``path::name`` (``path::Class::method`` is the symbol ``Class.method``)."""

    path: str
    name: str

    @property
    def spelling(self) -> str:
        return f"{self.path}::{self.name.replace('.', '::')}"


@dataclass(frozen=True)
class TestFileMention:
    """A test file the evidence names without naming a test in it: no proof can be prepared."""

    path: str

    @property
    def spelling(self) -> str:
        return self.path


def tests_named_in(evidence: Sequence[str]) -> tuple[TestReference | TestFileMention, ...]:
    """Every test ``evidence`` names, in order of appearance, once each (MIK-R28 rule 2).

    A test is named as a test ID ``path::name`` (``path::Class::method`` is ``Class.method``) or as a
    path plus symbol, the pytest selection ``path -k name``. A test file named with neither is a
    :class:`TestFileMention`, so the report can say the evidence names no resolvable test. Reading
    resolves nothing: whether a named test exists at C is the writer's to establish.
    """

    found: list[tuple[int, int, TestReference | TestFileMention]] = []
    for position, text in enumerate(evidence):
        for pattern in (_TEST_ID, _SELECTED_TEST):
            for match in pattern.finditer(text):
                reference = TestReference(match["path"], match["name"].replace("::", "."))
                found.append((position, match.start(), reference))
    # A file some evidence string names a test in is named, wherever else it is mentioned bare.
    named = {item.path for _, _, item in found}
    for position, text in enumerate(evidence):
        for match in _TEST_FILE.finditer(text):
            if match["path"] not in named:
                found.append((position, match.start(), TestFileMention(match["path"])))
    ordered = [item for _, _, item in sorted(found, key=lambda one: (one[0], one[1]))]
    return tuple(dict.fromkeys(ordered))


@dataclass(frozen=True)
class ProofRequest:
    """A curator-confirmed proof: the test that proves the entry's invariant, and what it shows."""

    position: int
    test: TestReference
    facet: str


@dataclass(frozen=True)
class EntryRequest:
    """One producer entry and the curator keys that say what it becomes."""

    entry_id: str
    kind: str
    statement: str
    evidence: tuple[str, ...]
    targets: tuple[TargetRequest, ...] = ()
    proofs: tuple[ProofRequest, ...] = ()
    scope: CuratorScope | None = None
    admission: Any = None
    status: str | None = None
    invariant_id: str | None = None
    supersedes: tuple[str, ...] = ()
    # An entry that authors no invariant (no target, no ``invariant_id``, no ``admission``) is a
    # ruling: nothing is written for it.
    ruling: bool = False

    def cited_tests(self) -> tuple[TestReference | TestFileMention, ...]:
        """Every test the evidence text names, in order, once each (see :func:`tests_named_in`)."""

        return tests_named_in(self.evidence)


@dataclass(frozen=True)
class RecordRequest:
    """One curator-authored record of any kind, created or (with ``record_id``) updated."""

    key: str
    kind: RecordKind
    fields: Mapping[str, Any]
    entry: str | None = None
    record_id: str | None = None
    slug: str | None = None

    @property
    def handoff_entry(self) -> str:
        return self.entry or self.key


@dataclass(frozen=True)
class CoverRequest:
    """One covered entry of an invariant row: re-anchor, re-locate, remove, or this list's additions."""

    entry_id: str | None = None
    handoff: str | None = None
    locator: LocatorRequest | None = None
    remove: bool = False


@dataclass(frozen=True)
class RowRequest:
    """One judgment row, as the curator authors it; the writer fills its mechanical fields."""

    position: int
    subject: str
    disposition: str
    reason: str
    items: tuple[str, ...] = ()
    covers: tuple[CoverRequest, ...] = ()
    effect: str | None = None
    because: tuple[Any, ...] = ()
    examined: tuple[str, ...] = ()


@dataclass(frozen=True)
class HandoffDocument:
    entries: tuple[EntryRequest, ...] = ()
    records: tuple[RecordRequest, ...] = ()
    rows: tuple[RowRequest, ...] = ()
    rulings: tuple[EntryRequest, ...] = field(default=())


def read_handoff(raw: Any) -> tuple[HandoffDocument, list[Problem]]:
    """Read the document's three sections, collecting every shape problem instead of stopping."""

    problems: list[Problem] = []
    if isinstance(raw, list):
        raw = {"entries": raw}
    if not isinstance(raw, Mapping):
        return HandoffDocument(), [Problem("document", "the hand-off is a JSON list or object")]
    unknown = sorted(set(raw) - set(SECTIONS))
    if unknown:
        problems.append(Problem("document", f"unknown section(s) {unknown}; known: {SECTIONS}"))
    sections = {name: _section(raw, name, problems) for name in SECTIONS}
    entries = [
        entry
        for position, one in enumerate(sections["entries"], start=1)
        if (entry := _entry(position, one, problems)) is not None
    ]
    records = [
        record
        for position, one in enumerate(sections["records"], start=1)
        if (record := _record(position, one, problems)) is not None
    ]
    rows = [
        row
        for position, one in enumerate(sections["history"], start=1)
        if (row := _row(position, one, problems)) is not None
    ]
    _require_distinct_handles(entries, records, problems)
    _require_attached_rulings(entries, records, problems)
    return (
        HandoffDocument(
            entries=tuple(entry for entry in entries if not entry.ruling),
            records=tuple(records),
            rows=tuple(rows),
            rulings=tuple(entry for entry in entries if entry.ruling),
        ),
        problems,
    )


def _require_attached_rulings(
    entries: Sequence[EntryRequest], records: Sequence[RecordRequest], problems: list[Problem]
) -> None:
    """An entry that authors no invariant is written only through a record that names it.

    Its evidence lives in that record's ``origin``. Without one, nothing of the entry could be
    stored, so the operation is refused rather than dropping its evidence or its proofs.
    """

    attached = {record.entry for record in records if record.entry is not None}
    for entry in entries:
        if not entry.ruling:
            continue
        where = f"entry {entry.entry_id}"
        if entry.entry_id not in attached:
            problems.append(
                Problem(
                    where,
                    "the entry names no record (no target, admission or invariant_id, and no "
                    "records[] item names it in 'entry'), so it cannot be written to the "
                    "knowledge files: attach it to a record, or keep it task-local and leave it "
                    "out of this list",
                )
            )
        if entry.proofs:
            problems.append(
                Problem(
                    where,
                    "'proofs' prove an invariant, and this entry authors none: add 'scope' and "
                    "'admission', or name the stored invariant in 'invariant_id'",
                )
            )


def _section(raw: Mapping[str, Any], name: str, problems: list[Problem]) -> Sequence[Any]:
    value = raw.get(name, [])
    if not isinstance(value, list):
        problems.append(Problem(name, "a section is a JSON list"))
        return []
    return value


def _require_distinct_handles(
    entries: Sequence[EntryRequest], records: Sequence[RecordRequest], problems: list[Problem]
) -> None:
    seen: set[str] = set()
    for handle in [entry.entry_id for entry in entries] + [record.key for record in records]:
        if handle in seen:
            problems.append(Problem(f"{HANDLE_PREFIX}{handle}", "two items share this handle"))
        seen.add(handle)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _evidence(value: Any, where: str, problems: list[Problem]) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, list) and all(isinstance(one, str) for one in value):
        return tuple(one for one in value if one.strip())
    problems.append(Problem(where, "'evidence' is text or a list of text"))
    return ()


def read_locator(value: Any) -> LocatorRequest | str:
    """Read a hand-off locator into the file form, or say why it is not one."""

    if not isinstance(value, Mapping):
        return "a locator is required: {kind: symbol, value} | {kind: line_range, start, end} | {kind: file}"
    kind = value.get("kind")
    if kind == "symbol":
        name = _text(value.get("value")) or _text(value.get("name"))
        return (
            LocatorRequest({"kind": "symbol", "name": name})
            if name
            else "a symbol locator names its symbol in 'value'"
        )
    if kind == "line_range":
        start, end = value.get("start"), value.get("end")
        if (
            isinstance(start, int)
            and isinstance(end, int)
            and not isinstance(start, bool)
            and 1 <= start <= end
        ):
            return LocatorRequest({"kind": "line_range", "start": start, "end": end})
        return "a line range is one-based and inclusive: 1 <= start <= end"
    if kind == "file":
        return LocatorRequest({"kind": "file"})
    return f"unknown locator kind {kind!r}"


def _targets(
    entry_id: str, raw: Mapping[str, Any], where: str, problems: list[Problem]
) -> tuple[TargetRequest, ...]:
    targets = raw.get("target") or []
    if not isinstance(targets, list):
        problems.append(Problem(where, "'target' is a list"))
        return ()
    realization = EntryRealization.read(raw)
    refused = realization_refusal(entry_id, realization, targets)
    if refused is not None:
        problems.append(Problem(where, f"[{refused[0]}] {refused[1]}"))
        return ()
    planned: list[TargetRequest] = []
    for position, target in enumerate(targets, start=1):
        at = f"{where}.target[{position}]"
        path = _text(target.get("path")) if isinstance(target, Mapping) else None
        if path is None or not isinstance(target, Mapping):
            problems.append(Problem(at, "a target names its repository-relative 'path'"))
            continue
        locator = read_locator(target.get("locator"))
        if isinstance(locator, str):
            problems.append(Problem(at, locator))
            continue
        authored = realization.for_target(target)
        planned.append(
            TargetRequest(
                position=position,
                path=path,
                locator=locator,
                role=ROLE_SPELLINGS.get(authored.role, authored.role),
                rationale=authored.rationale,
            )
        )
    return tuple(planned)


def read_test_reference(value: Any) -> TestReference | str:
    """Read ``path::name`` or ``{path, symbol}`` into a test reference."""

    if isinstance(value, Mapping):
        path, name = _text(value.get("path")), _text(value.get("symbol"))
        if path and name:
            return TestReference(path, name)
        return "a test is '<path>::<name>' or {path, symbol}"
    if isinstance(value, str):
        match = _TEST_ID.fullmatch(value.strip())
        if match is not None:
            return TestReference(match["path"], match["name"].replace("::", "."))
    return f"not a test reference: {value!r}; write '<path>::<name>' or {{path, symbol}}"


def _proofs(
    raw: Mapping[str, Any], where: str, problems: list[Problem]
) -> tuple[ProofRequest, ...]:
    proofs = raw.get("proofs") or []
    if not isinstance(proofs, list):
        problems.append(Problem(where, "'proofs' is a list of {test, facet}"))
        return ()
    planned: list[ProofRequest] = []
    for position, proof in enumerate(proofs, start=1):
        at = f"{where}.proofs[{position}]"
        if not isinstance(proof, Mapping):
            problems.append(Problem(at, "a proof is {test, facet}"))
            continue
        test = read_test_reference(proof.get("test"))
        facet = _text(proof.get("facet"))
        if isinstance(test, str):
            problems.append(Problem(at, test))
        elif facet is None:
            problems.append(
                Problem(at, "the curator authors the proof's 'facet': what this test demonstrates")
            )
        else:
            planned.append(ProofRequest(position=position, test=test, facet=facet))
    return tuple(planned)


def _identifiers(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, list):
        return tuple(str(one) for one in value if isinstance(one, str) and one.strip())
    return ()


def _entry(position: int, raw: Any, problems: list[Problem]) -> EntryRequest | None:
    where = f"entries[{position}]"
    if not isinstance(raw, Mapping):
        problems.append(Problem(where, "an entry is a JSON object"))
        return None
    entry_id = _text(raw.get("id"))
    if entry_id is None:
        problems.append(Problem(where, "every entry carries its own 'id'"))
        return None
    where = f"entries[{position}] ({entry_id})"
    invariant_id = _text(raw.get("invariant_id"))
    admission = raw.get("admission")
    targets = _targets(entry_id, raw, where, problems)
    entry = EntryRequest(
        ruling=not raw.get("target") and invariant_id is None and admission is None,
        entry_id=entry_id,
        kind=str(raw.get("kind", "")),
        statement=str(raw.get("statement", "")),
        evidence=_evidence(raw.get("evidence"), where, problems),
        targets=targets,
        proofs=_proofs(raw, where, problems),
        admission=admission,
        status=_text(raw.get("status")),
        invariant_id=invariant_id,
        supersedes=_identifiers(raw.get("supersedes")),
    )
    if entry.ruling:
        return entry
    scope = raw.get("scope")
    if scope is not None or invariant_id is None:
        read = read_curator_scope(scope)
        if isinstance(read, str):
            problems.append(Problem(where, f"[unfilled_curation_scope] {read}"))
        else:
            entry = replace(entry, scope=read)
    if invariant_id is None and admission is None:
        problems.append(
            Problem(
                where, "a new invariant carries the curator's 'admission' {criteria, justification}"
            )
        )
    if not entry.statement.strip():
        problems.append(
            Problem(where, "an invariant's 'statement' is the producer's verbatim text")
        )
    return entry


def _record_kind(value: Any) -> RecordKind | None:
    return next((kind for kind in RECORD_PREFIXES if kind == value), None)


def _record(position: int, raw: Any, problems: list[Problem]) -> RecordRequest | None:
    where = f"records[{position}]"
    if not isinstance(raw, Mapping):
        problems.append(Problem(where, "a record is a JSON object"))
        return None
    key = _text(raw.get("key"))
    kind = _record_kind(raw.get("kind"))
    fields = raw.get("fields")
    if key is None:
        problems.append(Problem(where, "a record carries its local 'key'"))
    if kind is None:
        problems.append(Problem(where, f"'kind' is one of {sorted(RECORD_PREFIXES)}"))
    if not isinstance(fields, Mapping):
        problems.append(Problem(where, "'fields' holds the record's own fields"))
        return None
    owned = sorted(WRITER_OWNED_FIELDS & set(fields))
    if owned:
        problems.append(Problem(where, f"the writer owns {owned}; remove them from 'fields'"))
    unknown = sorted(set(raw) - {"key", "kind", "entry", "id", "slug", "fields"})
    if unknown:
        problems.append(Problem(where, f"unknown record key(s) {unknown}"))
    record_id, slug = _text(raw.get("id")), _text(raw.get("slug"))
    if record_id is None and slug is None:
        problems.append(Problem(where, "a new record names its display 'slug'"))
    if key is None or kind is None or owned:
        return None
    return RecordRequest(
        key=key,
        kind=kind,
        fields=dict(fields),
        entry=_text(raw.get("entry")),
        record_id=record_id,
        slug=slug,
    )


def _cover(value: Any, where: str, problems: list[Problem]) -> CoverRequest | None:
    if isinstance(value, str) and value.strip():
        return CoverRequest(entry_id=value)
    if not isinstance(value, Mapping):
        problems.append(
            Problem(where, "a cover is an entry ID or {id, locator?, remove?} or {handoff}")
        )
        return None
    handoff = _text(value.get("handoff"))
    if handoff is not None:
        return CoverRequest(handoff=handoff)
    entry_id = _text(value.get("id"))
    if entry_id is None:
        problems.append(Problem(where, "a cover names the entry 'id' it covers"))
        return None
    locator = None
    if value.get("locator") is not None:
        read = read_locator(value.get("locator"))
        if isinstance(read, str):
            problems.append(Problem(where, read))
            return None
        locator = read
    return CoverRequest(entry_id=entry_id, locator=locator, remove=value.get("remove") is True)


def _row(position: int, raw: Any, problems: list[Problem]) -> RowRequest | None:
    where = f"history[{position}]"
    if not isinstance(raw, Mapping):
        problems.append(Problem(where, "a row is a JSON object"))
        return None
    owned = sorted({"id", "revision"} & set(raw))
    if owned:
        problems.append(Problem(where, f"the writer owns {owned}; remove them from the row"))
    unknown = sorted(set(raw) - _ROW_KEYS - {"id", "revision"})
    if unknown:
        problems.append(Problem(where, f"unknown row key(s) {unknown}; known: {sorted(_ROW_KEYS)}"))
    if raw.get("because") is not None and not isinstance(raw.get("because"), list):
        problems.append(Problem(where, "'because' is a list of decision IDs and requirements"))
    subject, disposition, reason = (
        _text(raw.get("subject")),
        _text(raw.get("disposition")),
        _text(raw.get("reason")),
    )
    if subject is None or disposition is None or reason is None:
        problems.append(Problem(where, "a row carries 'subject', 'disposition' and 'reason'"))
        return None
    covers = [
        cover
        for index, one in enumerate(raw.get("covers") or [], start=1)
        if (cover := _cover(one, f"{where}.covers[{index}]", problems)) is not None
    ]
    because = raw.get("because") or []
    return RowRequest(
        position=position,
        subject=subject,
        disposition=disposition,
        reason=reason,
        items=_identifiers(raw.get("items")),
        covers=tuple(covers),
        effect=_text(raw.get("effect")),
        because=tuple(because) if isinstance(because, list) else (),
        examined=_identifiers(raw.get("examined")),
    )
