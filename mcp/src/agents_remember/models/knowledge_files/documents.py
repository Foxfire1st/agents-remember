"""Where each knowledge file lives, and which model reads it (MIK-R21 rules 1 and 2).

Paths are memory-repository-relative POSIX strings:

=============================================  ====================================================
``knowledge/<kind-dir>/<ID>-<slug>.json``      a record (+ optional ``.md`` with the same stem)
``knowledge/layout.json``                      the layout marker
``knowledge/history/<owner-id>.json``          a history file (schema owned by MIK-R07)
``knowledge/census/<census-id>/…``             census files (schemas owned by MIK-R20)
``onboarding/<source path>.json``              a file sidecar beside ``<source path>.md``
``onboarding/<route>/overview.json``           a route sidecar beside ``overview.md``
=============================================  ====================================================

A document names its format in ``schema``; :func:`parse_document` dispatches on it and refuses a
schema this module does not know. The history schema (``ar-history/v1``) is MIK-R07's
(:mod:`.history`); a history file is read with :func:`parse_history_document`, which also checks that
the file is named after its owner. The census schemas (``ar-census-*/v1``) are MIK-R20's
(:mod:`.census`); they are registered here so every census file dispatches like any other.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

from agents_remember.models.knowledge_files.canonical import parse_json
from agents_remember.models.knowledge_files.census import CENSUS_MODELS, CensusDocument
from agents_remember.models.knowledge_files.history import HISTORY_SCHEMA, HistoryFile
from agents_remember.models.knowledge_files.ids import CROCKFORD_ALPHABET, RecordKind
from agents_remember.models.knowledge_files.records import (
    RECORD_MODELS,
    KnowledgeRecord,
    schema_name,
)
from agents_remember.models.knowledge_files.shapes import FileModel, require_repository_path
from agents_remember.models.knowledge_files.sidecars import (
    FILE_SIDECAR_SCHEMA,
    LAYOUT_MARKER_SCHEMA,
    ROOT_ROUTE_PATH,
    ROUTE_SIDECAR_SCHEMA,
    FileSidecar,
    LayoutMarker,
    RouteSidecar,
)

KNOWLEDGE_ROOT: Final = "knowledge"
ONBOARDING_ROOT: Final = "onboarding"
LAYOUT_MARKER_PATH: Final = f"{KNOWLEDGE_ROOT}/layout.json"

RECORD_DIRECTORIES: Final[Mapping[RecordKind, str]] = {
    "invariant": "invariants",
    "family": "families",
    "decision": "decisions",
    "incident": "incidents",
    "assumption": "assumptions",
    "limitation": "limitations",
    "failure_mode": "failure-modes",
    "scenario": "scenarios",
    "diagnostic": "diagnostics",
    "term": "terms",
}

SCHEMA_MODELS: Final[Mapping[str, type[FileModel]]] = {
    **{schema_name(kind): model for kind, model in RECORD_MODELS.items()},
    FILE_SIDECAR_SCHEMA: FileSidecar,
    ROUTE_SIDECAR_SCHEMA: RouteSidecar,
    LAYOUT_MARKER_SCHEMA: LayoutMarker,
    HISTORY_SCHEMA: HistoryFile,
    **CENSUS_MODELS,
}

KnowledgeDocument = (
    KnowledgeRecord | FileSidecar | RouteSidecar | LayoutMarker | HistoryFile | CensusDocument
)

_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_RECORD_FILENAME = re.compile(
    rf"^(?P<id>[A-Z]{{3}}-[{CROCKFORD_ALPHABET}]{{6}}(?:[{CROCKFORD_ALPHABET}]{{2}})?)"
    r"-(?P<slug>[A-Za-z0-9][A-Za-z0-9._-]*)\.(?P<ext>json|md)$"
)


def parse_document(document: Mapping[str, Any]) -> FileModel:
    """Validate a parsed JSON document against the model its ``schema`` names."""

    schema = document.get("schema")
    if not isinstance(schema, str):
        raise ValueError("a knowledge document names its format in a string 'schema' field")
    model = SCHEMA_MODELS.get(schema)
    if model is None:
        raise ValueError(f"unknown knowledge schema {schema!r}; known: {sorted(SCHEMA_MODELS)}")
    return model.model_validate(dict(document))


def parse_document_text(text: str) -> FileModel:
    """Parse strict JSON text and validate it with :func:`parse_document`."""

    document = parse_json(text)
    if not isinstance(document, dict):
        raise ValueError("a knowledge document is a JSON object")
    return parse_document(document)


def record_path(kind: RecordKind, record_id: str, slug: str, *, extension: str = "json") -> str:
    """Return ``knowledge/<kind-dir>/<ID>-<slug>.<extension>``; the slug is display only."""

    if not _SLUG.match(slug):
        raise ValueError(f"a record slug is [A-Za-z0-9._-] and starts alphanumeric: {slug!r}")
    if extension not in {"json", "md"}:
        raise ValueError("a record file is .json or .md")
    return f"{KNOWLEDGE_ROOT}/{RECORD_DIRECTORIES[kind]}/{record_id}-{slug}.{extension}"


def split_record_filename(filename: str) -> tuple[str, str, str]:
    """Split ``<ID>-<slug>.<ext>`` into (ID, slug, extension), refusing any other name."""

    match = _RECORD_FILENAME.match(filename)
    if match is None:
        raise ValueError(f"not a record filename: {filename!r}")
    return match["id"], match["slug"], match["ext"]


def history_path(owner_id: str) -> str:
    """Return ``knowledge/history/<owner-id>.json`` (for example ``260928-MIK-L07``)."""

    if not _SLUG.match(owner_id):
        raise ValueError(f"not a history owner id: {owner_id!r}")
    return f"{KNOWLEDGE_ROOT}/history/{owner_id}.json"


def parse_history_document(path: str, text: str) -> HistoryFile:
    """Parse the history file at ``path``, refusing one not named ``history_path(<its owner>)``."""

    document = parse_document_text(text)
    if not isinstance(document, HistoryFile):
        raise ValueError(f"{path} is not an {HISTORY_SCHEMA} document")
    expected = history_path(document.owner_id)
    if path != expected:
        raise ValueError(
            f"the history file of {document.owner_id!r} lives at {expected}, not {path}"
        )
    return document


def census_directory(census_id: str) -> str:
    """Return ``knowledge/census/<census-id>``."""

    if not _SLUG.match(census_id):
        raise ValueError(f"not a census id: {census_id!r}")
    return f"{KNOWLEDGE_ROOT}/census/{census_id}"


def file_sidecar_path(source_path: str) -> str:
    """Return ``onboarding/<source path>.json``, the sidecar beside ``<source path>.md``."""

    return f"{ONBOARDING_ROOT}/{require_repository_path(source_path)}.json"


def route_sidecar_path(route: str) -> str:
    """Return ``onboarding/<route>/overview.json`` (``onboarding/overview.json`` for ``.``)."""

    if route == ROOT_ROUTE_PATH:
        return f"{ONBOARDING_ROOT}/overview.json"
    return f"{ONBOARDING_ROOT}/{require_repository_path(route)}/overview.json"
