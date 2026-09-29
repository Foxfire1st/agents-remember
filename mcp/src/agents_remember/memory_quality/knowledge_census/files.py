"""Where census files live and how a memory tree's censuses are read (MIK-R20 rule 1).

:func:`read_censuses` reads every file under ``knowledge/census/`` of one memory tree (path to exact
bytes, as the validator's ``KnowledgeTree`` holds them) through the ``ar-census-*/v1`` models. A file
at an unknown location, in the wrong schema for its location, naming another census or another
route than its filename, or not canonically formatted, becomes a :class:`CensusProblem`; the
validator reports those problems (``R20.1-census-shape`` and ``R20.1-census-canonical``).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Final, Literal

from pydantic import ValidationError

from agents_remember.models.knowledge_files.canonical import (
    FORMATTER_COMMAND,
    CanonicalFormatError,
    canonical_text,
    parse_json,
)
from agents_remember.models.knowledge_files.census import (
    BASELINE_FILENAME,
    CENSUS_ID_PATTERN,
    CLAIMS_DIRECTORY,
    INVENTORY_FILENAME,
    ROUTES_DIRECTORY,
    CensusBaseline,
    CensusClaims,
    CensusInventory,
    CensusRoute,
    route_slug,
)
from agents_remember.models.knowledge_files.documents import (
    KNOWLEDGE_ROOT,
    census_directory,
    parse_document,
)
from agents_remember.models.knowledge_files.shapes import FileModel

CENSUS_ROOT: Final = f"{KNOWLEDGE_ROOT}/census/"

ProblemCategory = Literal["shape", "canonical"]


def baseline_path(census_id: str) -> str:
    return f"{census_directory(census_id)}/{BASELINE_FILENAME}"


def inventory_path(census_id: str) -> str:
    return f"{census_directory(census_id)}/{INVENTORY_FILENAME}"


def claims_path(census_id: str, route: str) -> str:
    return f"{census_directory(census_id)}/{CLAIMS_DIRECTORY}/{route_slug(route)}.json"


def route_status_path(census_id: str, route: str) -> str:
    return f"{census_directory(census_id)}/{ROUTES_DIRECTORY}/{route_slug(route)}.json"


def census_id_of(path: str) -> str | None:
    """Return the census ID of a path under ``knowledge/census/``, or ``None``."""

    if not path.startswith(CENSUS_ROOT):
        return None
    census_id, _, rest = path.removeprefix(CENSUS_ROOT).partition("/")
    return census_id if census_id and rest else None


@dataclass(frozen=True)
class CensusProblem:
    path: str
    field: str
    message: str
    category: ProblemCategory = "shape"


@dataclass
class ParsedCensus:
    """One census directory's parsed files; a file that did not parse is absent here."""

    census_id: str
    baseline: CensusBaseline | None = None
    inventory: CensusInventory | None = None
    claims: dict[str, CensusClaims] = field(default_factory=dict)
    routes: dict[str, CensusRoute] = field(default_factory=dict)
    paths: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class CensusTree:
    """Every census of one memory tree, and the problems found reading them."""

    censuses: Mapping[str, ParsedCensus]
    problems: tuple[CensusProblem, ...] = ()

    def route_histories(self) -> Iterator[CensusRoute]:
        for census in self.censuses.values():
            yield from census.routes.values()


_Expected = type[CensusBaseline] | type[CensusInventory] | type[CensusClaims] | type[CensusRoute]


def _expected_model(rest: str) -> _Expected | None:
    if rest == BASELINE_FILENAME:
        return CensusBaseline
    if rest == INVENTORY_FILENAME:
        return CensusInventory
    directory, _, filename = rest.partition("/")
    if not filename or "/" in filename or not filename.endswith(".json"):
        return None
    return {CLAIMS_DIRECTORY: CensusClaims, ROUTES_DIRECTORY: CensusRoute}.get(directory)


class _Reader:
    def __init__(self) -> None:
        self.censuses: dict[str, ParsedCensus] = {}
        self.problems: list[CensusProblem] = []

    def problem(
        self, path: str, message: str, *, field: str = "", category: ProblemCategory = "shape"
    ) -> None:
        self.problems.append(CensusProblem(path, field, message, category))

    def document(self, path: str, data: bytes) -> FileModel | None:
        try:
            text = data.decode("utf-8")
            document = parse_json(text)
        except (UnicodeDecodeError, CanonicalFormatError) as error:
            self.problem(path, f"a census file is strict UTF-8 JSON: {error}")
            return None
        if not isinstance(document, dict):
            self.problem(path, "a census file is a JSON object")
            return None
        if canonical_text(document) != text:
            self.problem(
                path,
                f"not in the canonical formatting; run `{FORMATTER_COMMAND} {path}`",
                category="canonical",
            )
        try:
            return parse_document(document)
        except ValidationError as error:
            for item in error.errors():
                location = ".".join(str(part) for part in item.get("loc", ()))
                self.problem(path, str(item.get("msg", "invalid")), field=location)
        except ValueError as error:
            self.problem(path, str(error), field="schema")
        return None

    def read(self, path: str, data: bytes) -> None:
        census_id = census_id_of(path)
        if census_id is None or re.match(CENSUS_ID_PATTERN, census_id) is None:
            self.problem(
                path,
                "a census file lives at knowledge/census/<census-id>/…, the census ID "
                "[A-Za-z0-9._-] and starting alphanumeric",
            )
            return
        census = self.censuses.setdefault(census_id, ParsedCensus(census_id))
        census.paths.add(path)
        rest = path.removeprefix(f"{CENSUS_ROOT}{census_id}/")
        expected = _expected_model(rest)
        if expected is None:
            self.problem(
                path,
                "not a census file location: baseline.json, inventory.json, "
                "claims/<route-slug>.json or routes/<route-slug>.json (MIK-R20 rule 1)",
            )
            return
        model = self.document(path, data)
        if model is None:
            return
        if not isinstance(model, expected):
            schema = expected.model_fields["schema_"].default
            self.problem(path, f"this location holds an {schema} document", field="schema")
            return
        self.place(census, path, rest, model)

    def place(
        self,
        census: ParsedCensus,
        path: str,
        rest: str,
        model: CensusBaseline | CensusInventory | CensusClaims | CensusRoute,
    ) -> None:
        if model.census != census.census_id:
            self.problem(path, f"this file belongs to census {census.census_id!r}", field="census")
            return
        if isinstance(model, CensusClaims | CensusRoute):
            expected_name = f"{route_slug(model.route)}.json"
            if rest.partition("/")[2] != expected_name:
                directory = rest.partition("/")[0]
                self.problem(
                    path,
                    f"route {model.route!r} is filed at {directory}/{expected_name}",
                    field="route",
                )
                return
        if isinstance(model, CensusBaseline):
            census.baseline = model
        elif isinstance(model, CensusInventory):
            census.inventory = model
        elif isinstance(model, CensusClaims):
            census.claims[path] = model
        else:
            census.routes[path] = model

    def require_pinned_files(self, files: Mapping[str, bytes]) -> None:
        for census_id in sorted(self.censuses):
            for path in (baseline_path(census_id), inventory_path(census_id)):
                if path not in files:
                    self.problem(path, "every census has its baseline.json and inventory.json")


def read_censuses(files: Mapping[str, bytes]) -> CensusTree:
    """Read every census file among ``files`` (memory-repository path to bytes)."""

    reader = _Reader()
    for path in sorted(files):
        if path.startswith(CENSUS_ROOT):
            reader.read(path, files[path])
    reader.require_pinned_files(files)
    return CensusTree(censuses=dict(reader.censuses), problems=tuple(reader.problems))
