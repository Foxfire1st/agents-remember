"""The hand-over projection of one role launch: what its first message carries.

A role's first message is read again from cache on every turn of a seat's life, so it carries
only what the role needs before its first tool call. The complete hand-over object is written
once beside the hand-over text and named on the trailing ``AR handover artifacts:`` line.

The field list lives here alone. Tests derive from it and never repeat it: a field added to a
projection is added here, and every check that reads the list sees it.
"""

from __future__ import annotations

import json
from typing import Any

import tiktoken

from agents_remember.models.tokens import (
    VENDORED_ENCODING_NAME,
    vendored_vocabulary_cache,
)

# The hand-over object's fields the projection carries, in the order the message states them.
# A field stays out when the object has no such key or holds null there.
PROJECTION_FIELDS: tuple[str, ...] = (
    "schema",
    "requestId",
    "role",
    "operation",
    "agent",
    "selection",
    "taskReportPath",
    "documents",
    "taskDocumentFiles",
    "workspace",
    "arMcpContext",
    "repositoryContext",
    "host",
    "leafSeats",
)

# Optional field naming the complete hand-over JSON when callers explicitly opt in.
PROJECTION_FILE_FIELD = "handoverFile"

# The parts of a projected object field that the projection carries.
WORKSPACE_FIELDS: tuple[str, ...] = (
    "path",
    "codeRoot",
    "memoryRoot",
    "scratchRoot",
    "outsideScratchRoot",
    "contractPath",
)
AR_MCP_CONTEXT_FIELDS: tuple[str, ...] = (
    "readerArguments",
    "availableRepositoryIds",
    "repositorySelection",
)
HOST_FIELDS: tuple[str, ...] = (
    "parent",
    "developerQuestionRoute",
)
_PARENT_IDENTITY_FIELDS: tuple[str, ...] = ("agentId", "role", "task")
_SELECTION_REF_KEYS: tuple[str, ...] = (
    "sprintDocumentRef",
    "masterDocumentRef",
    "taskDocumentRef",
)
_RETURN_ADDRESS_REF_KEYS: tuple[tuple[str, str], ...] = (
    ("sprint_document_ref", "sprintDocumentRef"),
    ("master_document_ref", "masterDocumentRef"),
    ("task_document_ref", "taskDocumentRef"),
)

# The object's parts the first message leaves to the hand-over file. Named here so a check can
# say which of them a message must reach through the file rather than the projection.
FILE_ONLY_FIELDS: tuple[str, ...] = (
    "assignment",
    "canonicalTaskReportPath",
    "contextPacket",
    "instructionComposition",
    "launchChoices",
    "capsule",
    "taskDocumentDigest",
    "ownerHandover",
)
FILE_ONLY_NESTED: tuple[str, ...] = (
    "host.ownerRelation",
    "host.developerQuestions",
    "host.arMcpUsage",
    "host.roleTools",
    "host.messageBinding",
    "host.instructionSource",
    "arMcpContext.taskContext",
    "documents[0].canonicalTaskPath",
    "documents[0].taskDocReadArgs",
    "documents[0].documentIdentity",
    "workspace.taskReportRoot",
    "workspace.taskReportAccessRoot",
    "leafSeats.roleMessageArguments",
)


def project_handover(
    handover: dict[str, Any],
    *,
    handover_file: dict[str, Any] | None,
    bound_document: dict[str, Any] | None,
    omit_handover_file_field: bool = True,
) -> dict[str, Any]:
    """Build the slimmed wire projection; full fields remain untouched in ``.handover.json``."""

    raw_selection = handover.get("selection")
    selection = raw_selection if isinstance(raw_selection, dict) else {}
    projected_documents = _bound_document_entry(
        handover.get("documents"), bound_document, selection
    )
    rendered_read_path = (
        projected_documents[0].get("renderedReadPath") if projected_documents else None
    )
    projection: dict[str, Any] = {}
    for key in PROJECTION_FIELDS:
        value = _project_field(
            key,
            handover.get(key),
            selection=selection,
            projected_documents=projected_documents,
            rendered_read_path=rendered_read_path,
        )
        if value is not None:
            projection[key] = value
    if not omit_handover_file_field and handover_file is not None:
        projection[PROJECTION_FILE_FIELD] = {
            "path": handover_file["path"],
            "sha256": handover_file["sha256"],
        }
    return projection


def projection_text(projection: dict[str, Any]) -> str:
    """The projection as the message carries it: one compact JSON object."""

    return json.dumps(projection, ensure_ascii=False, separators=(",", ":"))


def first_read_paragraph(first_read: str, *, rendered_read_path: str | None = None) -> str:
    """The brief's Launch Context paragraph: the first read, and where full fields live."""

    lead = _compact_first_read(first_read, rendered_read_path)
    return (
        f"{lead} Each contentDigest is launch snapshot; turn != acceptance. "
        "Full JSON: trailing `AR handover artifacts:` line (1 field/step)."
    )


def projection_tokens(text: str) -> int:
    """The o200k_base tokens of a projection exactly as it stands in the message."""

    with vendored_vocabulary_cache(VENDORED_ENCODING_NAME):
        encoding = tiktoken.get_encoding(VENDORED_ENCODING_NAME)
    return len(encoding.encode(text, disallowed_special=()))


def _compact_first_read(first_read: str, rendered_read_path: str | None) -> str:
    """Avoid repeating ``rendered_read_path`` when ``documents[0].renderedReadPath`` is below."""

    if not rendered_read_path:
        return first_read
    verbose = (
        "Read this rendered task document first with your harness's own file tools: "
        f"{rendered_read_path}"
    )
    if first_read.startswith(verbose):
        return "Read `documents[0].renderedReadPath` first" + first_read[len(verbose) :]
    return first_read


def _project_field(
    key: str,
    value: Any,
    *,
    selection: dict[str, Any],
    projected_documents: list[dict[str, Any]] | None,
    rendered_read_path: str | None,
) -> Any:
    """Project one top-level handover field, returning None when omitted."""

    if value is None:
        return None
    projectors: dict[str, Any] = {
        "selection": lambda: _non_null_mapping(value),
        "documents": lambda: projected_documents,
        "taskDocumentFiles": lambda: _project_task_document_files(value, rendered_read_path),
        "workspace": lambda: _subset(value, WORKSPACE_FIELDS),
        "arMcpContext": lambda: _reader_context(value),
        "host": lambda: _project_host(value, selection),
        "leafSeats": lambda: _project_leaf_seats(value),
    }
    projector = projectors.get(key)
    return projector() if projector is not None else value


def _non_null_mapping(value: Any) -> Any:
    """Drop keys whose value is None while keeping key order."""

    if not isinstance(value, dict):
        return value
    return {key: item for key, item in value.items() if item is not None}


def _bound_document_entry(
    documents: Any,
    bound_document: dict[str, Any] | None,
    selection: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """The one-item list holding the projected bound task document, or None when none applies."""

    if bound_document is None or not isinstance(documents, list) or not documents:
        return None
    for entry in documents:
        if isinstance(entry, dict) and entry.get("taskDocumentRef") == bound_document:
            return [_project_document_entry(entry, selection, bound_document)]
    # The effective task is always one of the resolved documents; a list that does not carry it
    # is still projected as its most specific entry rather than leaked whole.
    last = documents[-1]
    return (
        [_project_document_entry(last, selection, bound_document)]
        if isinstance(last, dict)
        else None
    )


def _project_document_entry(
    entry: dict[str, Any],
    selection: dict[str, Any],
    bound_document: dict[str, Any] | None,
) -> dict[str, Any]:
    """Flatten the bound document entry to its wire keys, omitting redundant refs and nulls."""

    identity = entry.get("documentIdentity")
    identity_map = identity if isinstance(identity, dict) else {}
    read_args = entry.get("taskDocReadArgs")
    read_args_map = read_args if isinstance(read_args, dict) else {}
    raw: dict[str, Any] = {
        "id": identity_map.get("id"),
        "kind": identity_map.get("kind"),
        "repo_id": read_args_map.get("repo_id"),
        "task_name": read_args_map.get("task_name"),
        "slug": read_args_map.get("slug"),
        "renderedReadPath": entry.get("renderedReadPath"),
        "jsonPath": read_args_map.get("json_path"),
        "contentDigest": entry.get("contentDigest"),
    }
    projected = {key: item for key, item in raw.items() if item is not None}
    reference = entry.get("taskDocumentRef")
    selected_ref = selection.get("taskDocumentRef") or bound_document
    if reference is not None and reference != selected_ref:
        projected["taskDocumentRef"] = reference
    return projected


def _project_task_document_files(
    value: Any, rendered_read_path: str | None
) -> dict[str, Any] | None:
    """Keep only non-default task-document file metadata; omit the section when empty."""

    if not isinstance(value, dict):
        return value
    projected: dict[str, Any] = {}
    paths = value.get("paths")
    if isinstance(paths, list) and paths and paths != [rendered_read_path]:
        projected["paths"] = paths
    copies = value.get("copies")
    if isinstance(copies, list):
        extra = [copy for copy in copies if copy != rendered_read_path]
        if extra:
            projected["extraCopies"] = extra
    missing = value.get("missingCopies")
    if isinstance(missing, list) and missing:
        projected["missingCopies"] = missing
    state = value.get("state")
    if state is not None and state != "inside-working-folder":
        projected["state"] = state
    return projected or None


def _subset(value: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    """The named parts of an object field; a value that is not an object stays as it is."""

    if not isinstance(value, dict):
        return value
    return {key: value[key] for key in keys if value.get(key) is not None}


def _reader_context(value: Any) -> Any:
    """Collapse identical reader arguments to ``shared``; otherwise keep per-tool or repo fields."""

    if not isinstance(value, dict):
        return value
    reader_arguments = value.get("readerArguments")
    if isinstance(reader_arguments, dict):
        context_args = reader_arguments.get("context_packet")
        files_args = reader_arguments.get("read_ar_files")
        if (
            set(reader_arguments) == {"context_packet", "read_ar_files"}
            and isinstance(context_args, dict)
            and context_args == files_args
        ):
            return {"readerArguments": {"shared": context_args}}
        return {"readerArguments": reader_arguments}
    if reader_arguments is not None:
        return {"readerArguments": reader_arguments}
    return _subset(value, ("availableRepositoryIds", "repositorySelection"))


def _project_host(value: Any, selection: dict[str, Any]) -> Any:
    """Keep host routing fields and deduplicate parent references against selection."""

    if not isinstance(value, dict):
        return value
    projected: dict[str, Any] = {}
    parent = value.get("parent")
    if parent is not None:
        projected["parent"] = _project_parent(parent, selection)
    route = value.get("developerQuestionRoute")
    if route is not None:
        projected["developerQuestionRoute"] = route
    return projected


def _project_parent(parent: Any, selection: dict[str, Any]) -> Any:
    """Project host.parent, omitting null refs and refs identical to selection."""

    if not isinstance(parent, dict):
        return parent
    projected = _subset(parent, _PARENT_IDENTITY_FIELDS)
    for key in _SELECTION_REF_KEYS:
        reference = parent.get(key)
        if reference is not None and reference != selection.get(key):
            projected[key] = reference
    return_address = parent.get("returnAddress")
    if return_address is not None:
        projected["returnAddress"] = _project_return_address(return_address, selection)
    return projected


def _project_return_address(return_address: Any, selection: dict[str, Any]) -> Any:
    """Project host.parent.returnAddress, omitting refs identical to selection."""

    if not isinstance(return_address, dict):
        return return_address
    ref_keys = {addr_key for addr_key, _sel_key in _RETURN_ADDRESS_REF_KEYS}
    projected = {
        key: item
        for key, item in return_address.items()
        if key not in ref_keys and item is not None
    }
    for addr_key, sel_key in _RETURN_ADDRESS_REF_KEYS:
        reference = return_address.get(addr_key)
        if reference is not None and reference != selection.get(sel_key):
            projected[addr_key] = reference
    return projected


def _project_leaf_seats(value: Any) -> Any:
    """Keep only leaf seat role names inline; full roleMessageArguments stay in the JSON file."""

    if not isinstance(value, dict):
        return value
    projected = _subset(value, ("roles",))
    return projected or None
