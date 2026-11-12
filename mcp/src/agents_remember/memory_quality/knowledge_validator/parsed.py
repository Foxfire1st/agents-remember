"""Read every file of a knowledge tree once, through the MIK-R21 and MIK-R07 models.

:func:`parse_tree` sorts each path into its location (MIK-R21 rule 1), checks its canonical
formatting, and validates it with the model its location and ``schema`` name. What parses is kept for
the tree-level rules; what does not becomes a :class:`ParseProblem`, which the shape-level rules of
the registry report. Each problem carries a *category* so that one rule owns it: a Pydantic
``extra_forbidden`` field on a record is a second owner of a relationship (rule 4), a bad locator or
``content`` field is an anchor problem (rule 6), a filename that does not begin with the record's ID
is an identity problem (rule 2), and everything else is shape (rule 1).

Census files (``knowledge/census/``) are MIK-R20's: they are not read here but by
:mod:`.rules_census`, which registers their schema and append-only rules.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from pydantic import ValidationError

from agents_remember.memory_quality.knowledge_validator.trees import KnowledgeTree
from agents_remember.models.knowledge_files.canonical import (
    FORMATTER_COMMAND,
    CanonicalFormatError,
    canonical_text,
    parse_json,
)
from agents_remember.models.knowledge_files.documents import (
    KNOWLEDGE_ROOT,
    LAYOUT_MARKER_PATH,
    ONBOARDING_ROOT,
    RECORD_DIRECTORIES,
    file_sidecar_path,
    parse_document,
    parse_history_document,
    route_sidecar_path,
    split_record_filename,
)
from agents_remember.models.knowledge_files.history import HistoryFile
from agents_remember.models.knowledge_files.ids import RecordKind
from agents_remember.models.knowledge_files.records import RELATIONS_BY_KIND, KnowledgeRecord
from agents_remember.models.knowledge_files.sidecars import (
    FileSidecar,
    LayoutMarker,
    RouteSidecar,
)

ProblemCategory = Literal[
    "shape", "canonical", "identity", "single_owner", "relation", "locator", "content"
]

HISTORY_DIRECTORY: Final = f"{KNOWLEDGE_ROOT}/history/"
CENSUS_DIRECTORY: Final = f"{KNOWLEDGE_ROOT}/census/"
_KIND_BY_DIRECTORY: Final[Mapping[str, RecordKind]] = {
    directory: kind for kind, directory in RECORD_DIRECTORIES.items()
}


@dataclass(frozen=True)
class ParseProblem:
    path: str
    field: str
    message: str
    category: ProblemCategory


@dataclass(frozen=True)
class RecordFile:
    path: str
    kind: RecordKind
    record: KnowledgeRecord


@dataclass(frozen=True)
class SidecarFile:
    path: str
    sidecar: FileSidecar | RouteSidecar

    @property
    def markdown_path(self) -> str:
        return f"{self.path.removesuffix('.json')}.md"


@dataclass(frozen=True)
class ParsedTree:
    records: tuple[RecordFile, ...] = ()
    sidecars: tuple[SidecarFile, ...] = ()
    histories: tuple[tuple[str, HistoryFile], ...] = ()
    markdown: Mapping[str, str] = field(default_factory=dict)
    problems: tuple[ParseProblem, ...] = ()
    # IDs named by record filenames whose content does not parse: the record exists, and its shape
    # violation is reported once, not again by every link to it.
    unparsed_record_ids: frozenset[str] = frozenset()


def _field(location: tuple[Any, ...]) -> str:
    return ".".join(str(part) for part in location)


def _category(error: Mapping[str, Any], *, is_record: bool) -> ProblemCategory:
    location = tuple(str(part) for part in error.get("loc", ()))
    if is_record and error.get("type") == "extra_forbidden" and len(location) == 1:
        return "single_owner"
    if "locator" in location:
        return "locator"
    if location and location[-1] == "content" and "anchor" in location:
        return "content"
    return "shape"


def _model_problems(path: str, error: ValidationError, *, is_record: bool) -> list[ParseProblem]:
    return [
        ParseProblem(
            path=path,
            field=_field(tuple(item.get("loc", ()))),
            message=str(item.get("msg", "invalid")),
            category=_category(item, is_record=is_record),
        )
        for item in error.errors()
    ]


class _Parser:
    def __init__(self) -> None:
        self.records: list[RecordFile] = []
        self.sidecars: list[SidecarFile] = []
        self.histories: list[tuple[str, HistoryFile]] = []
        self.markdown: dict[str, str] = {}
        self.problems: list[ParseProblem] = []
        self.unparsed_record_ids: set[str] = set()

    def problem(
        self, path: str, message: str, *, field: str = "", category: ProblemCategory = "shape"
    ) -> None:
        self.problems.append(ParseProblem(path, field, message, category))

    def text(self, path: str, data: bytes) -> str | None:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as error:
            self.problem(path, f"a knowledge file is UTF-8: {error}")
            return None

    def json_document(self, path: str, data: bytes) -> dict[str, Any] | None:
        text = self.text(path, data)
        if text is None:
            return None
        try:
            document = parse_json(text)
        except CanonicalFormatError as error:
            self.problem(path, str(error))
            return None
        if not isinstance(document, dict):
            self.problem(path, "a knowledge document is a JSON object")
            return None
        if canonical_text(document) != text:
            self.problem(
                path,
                f"not in the canonical formatting; run `{FORMATTER_COMMAND} {path}`",
                category="canonical",
            )
        return document

    def model(self, path: str, document: dict[str, Any], *, is_record: bool = False) -> Any:
        try:
            return parse_document(document)
        except ValidationError as error:
            self.problems.extend(_model_problems(path, error, is_record=is_record))
        except ValueError as error:
            self.problem(path, str(error), field="schema")
        return None

    def parse(self, path: str, data: bytes) -> None:
        if path == LAYOUT_MARKER_PATH:
            self.layout(path, data)
        elif path.startswith(HISTORY_DIRECTORY):
            self.history(path, data)
        elif path.startswith(CENSUS_DIRECTORY):
            return
        elif path.startswith(f"{KNOWLEDGE_ROOT}/"):
            self.record(path, data)
        elif path.endswith(".md"):
            text = self.text(path, data)
            if text is not None:
                self.markdown[path] = text
        elif path.endswith(".json"):
            self.sidecar(path, data)

    def layout(self, path: str, data: bytes) -> None:
        document = self.json_document(path, data)
        if document is not None and not isinstance(self.model(path, document), LayoutMarker):
            self.problem(
                path, "the layout marker is an ar-memory-layout/v2 document", field="schema"
            )

    def history(self, path: str, data: bytes) -> None:
        if "/" in path.removeprefix(HISTORY_DIRECTORY) or not path.endswith(".json"):
            self.problem(path, "a history file is knowledge/history/<owner-id>.json")
            return
        if self.json_document(path, data) is None:
            return
        try:
            self.histories.append((path, parse_history_document(path, data.decode("utf-8"))))
        except ValidationError as error:
            self.problems.extend(_model_problems(path, error, is_record=False))
        except ValueError as error:
            self.problem(path, str(error))

    def record(self, path: str, data: bytes) -> None:
        directory, _, filename = path.removeprefix(f"{KNOWLEDGE_ROOT}/").partition("/")
        kind = _KIND_BY_DIRECTORY.get(directory)
        if kind is None or not filename or "/" in filename:
            self.problem(path, "not a knowledge file location (MIK-R21 rule 1)")
            return
        try:
            file_id, _, extension = split_record_filename(filename)
        except ValueError as error:
            self.problem(path, str(error), category="identity")
            return
        if extension == "md":
            text = self.text(path, data)
            if text is not None:
                self.markdown[path] = text
            return
        self.record_json(path, data, kind=kind, file_id=file_id)

    def disallowed_relations(self, path: str, document: dict[str, Any], kind: RecordKind) -> bool:
        """Report each link whose relation is not admitted for ``kind`` (rule 3), by field.

        The record model refuses the same link without naming it; this names the field, and the
        caller drops the model's unnamed duplicate.
        """

        links = document.get("links")
        allowed = RELATIONS_BY_KIND[kind]
        found = False
        for index, link in enumerate(links if isinstance(links, list) else ()):
            relation = link.get("relation") if isinstance(link, dict) else None
            if isinstance(relation, str) and relation not in allowed:
                found = True
                self.problem(
                    path,
                    f"relation {relation!r} is not admitted for a {kind} record; "
                    f"admitted: {sorted(allowed)}",
                    field=f"links.{index}.relation",
                    category="relation",
                )
        return found

    def drop_unnamed_relation_error(self, path: str) -> None:
        self.problems = [
            problem
            for problem in self.problems
            if not (
                problem.path == path
                and problem.category == "shape"
                and not problem.field
                and "is not allowed on a" in problem.message
            )
        ]

    def record_json(self, path: str, data: bytes, *, kind: RecordKind, file_id: str) -> None:
        document = self.json_document(path, data)
        disallowed = document is not None and self.disallowed_relations(path, document, kind)
        record = None if document is None else self.model(path, document, is_record=True)
        if disallowed:
            self.drop_unnamed_relation_error(path)
        if record is None or getattr(record, "record_kind", None) != kind:
            if record is not None:
                directory = RECORD_DIRECTORIES[kind]
                self.problem(path, f"a {directory} file holds a {kind} record", field="schema")
            self.unparsed_record_ids.add(file_id)
            return
        if record.id != file_id:
            self.problem(
                path,
                f"the filename begins with {file_id}, not the record's ID {record.id}",
                field="id",
                category="identity",
            )
        self.records.append(RecordFile(path, kind, record))

    def sidecar(self, path: str, data: bytes) -> None:
        document = self.json_document(path, data)
        sidecar = None if document is None else self.model(path, document)
        if sidecar is None:
            return
        if not isinstance(sidecar, FileSidecar | RouteSidecar):
            self.problem(path, "an onboarding JSON file is a file or route sidecar", field="schema")
            return
        if isinstance(sidecar, FileSidecar):
            expected = file_sidecar_path(sidecar.path)
        else:
            expected = route_sidecar_path(sidecar.path)
        if expected != path:
            self.problem(path, f"this sidecar lives at {expected}", field="path")
            return
        self.sidecars.append(SidecarFile(path, sidecar))


def parse_tree(tree: KnowledgeTree) -> ParsedTree:
    """Parse every file of ``tree`` once; unparseable files become problems."""

    parser = _Parser()
    for path in sorted(tree.files):
        if path.startswith((f"{KNOWLEDGE_ROOT}/", f"{ONBOARDING_ROOT}/")):
            parser.parse(path, tree.files[path])
    return ParsedTree(
        records=tuple(parser.records),
        sidecars=tuple(parser.sidecars),
        histories=tuple(parser.histories),
        markdown=dict(parser.markdown),
        problems=tuple(parser.problems),
        unparsed_record_ids=frozenset(parser.unparsed_record_ids),
    )


def parse_sidecars_leniently(tree: KnowledgeTree) -> tuple[SidecarFile, ...]:
    """Return the sidecars of a base tree that parse, ignoring every other file and problem."""

    sidecars: list[SidecarFile] = []
    for path in sorted(tree.files):
        if not (path.startswith(f"{ONBOARDING_ROOT}/") and path.endswith(".json")):
            continue
        try:
            document = parse_json(tree.files[path].decode("utf-8"))
            sidecar = parse_document(document) if isinstance(document, dict) else None
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(sidecar, FileSidecar | RouteSidecar):
            sidecars.append(SidecarFile(path, sidecar))
    return tuple(sidecars)
