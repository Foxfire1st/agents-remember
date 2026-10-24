"""The hand-over projection of one role launch: what its first message carries.

A role's first message is read again from cache on every turn of a seat's life, so it carries
only what the role needs before its first tool call (MIK-R193 rule 1). The rest of the hand-over
object is written once beside the hand-over text and named in the projection's ``handoverFile``.

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
    "assignment",
    "selection",
    "taskReportPath",
    "canonicalTaskReportPath",
    "documents",
    "taskDocumentFiles",
    "workspace",
    "arMcpContext",
    "repositoryContext",
    "host",
    "leafSeats",
)

# The last field of the projection, and the only one the hand-over object does not have: the
# reference of the file that holds the whole object (rule 2).
PROJECTION_FILE_FIELD = "handoverFile"

# The parts of a projected object field that the projection carries.
WORKSPACE_FIELDS: tuple[str, ...] = (
    "path",
    "codeRoot",
    "memoryRoot",
    "scratchRoot",
    "outsideScratchRoot",
    "taskReportRoot",
    "taskReportAccessRoot",
    "contractPath",
)
AR_MCP_CONTEXT_FIELDS: tuple[str, ...] = (
    "readerArguments",
    "availableRepositoryIds",
    "repositorySelection",
)
HOST_FIELDS: tuple[str, ...] = (
    "parent",
    "ownerRelation",
    "developerQuestions",
)

# The object's parts the first message leaves to the hand-over file. Named here so a check can
# say which of them a message must reach through the file rather than the projection.
FILE_ONLY_FIELDS: tuple[str, ...] = (
    "contextPacket",
    "instructionComposition",
    "launchChoices",
    "capsule",
    "taskDocumentDigest",
    "ownerHandover",
)
FILE_ONLY_NESTED: tuple[str, ...] = (
    "host.arMcpUsage",
    "host.roleTools",
    "host.messageBinding",
    "host.instructionSource",
    "arMcpContext.taskContext",
)


def project_handover(
    handover: dict[str, Any],
    *,
    handover_file: dict[str, Any],
    bound_document: dict[str, Any] | None,
) -> dict[str, Any]:
    """The projection of one hand-over object: its permitted fields, under the same keys.

    ``handover_file`` is the reference of the file of rule 2; ``bound_document`` is the task
    document reference of the seat's effective task, or None for a seat that has none.
    """

    projection: dict[str, Any] = {}
    for key in PROJECTION_FIELDS:
        value = handover.get(key)
        if value is None:
            continue
        if key == "documents":
            value = _bound_document_entry(value, bound_document)
            if value is None:
                continue
        elif key == "workspace":
            value = _subset(value, WORKSPACE_FIELDS)
        elif key == "arMcpContext":
            value = _reader_context(value)
        elif key == "host":
            value = _subset(value, HOST_FIELDS)
        projection[key] = value
    projection[PROJECTION_FILE_FIELD] = {
        "path": handover_file["path"],
        "sha256": handover_file["sha256"],
    }
    return projection


def projection_text(projection: dict[str, Any]) -> str:
    """The projection as the message carries it: one compact JSON object."""

    return json.dumps(projection, ensure_ascii=False, separators=(",", ":"))


def first_read_paragraph(first_read: str) -> str:
    """The brief's own paragraph before the capsule: the first read, and where the rest lives.

    It carries the three sentences of ``ownerHandover`` that are said nowhere else — the first
    read, the ``contentDigest`` note and the acceptance line — and names the file that holds the
    hand-over fields the projection leaves out (MIK-R193 rule 1).
    """

    return (
        f"{first_read} Each contentDigest records the document snapshot used for this launch. "
        "Never claim AR review, curation, lifecycle, or Git acceptance from a finished turn. "
        "The fields of your hand-over that are not in the projection below are in the file "
        f"named by `{PROJECTION_FILE_FIELD}`. It is large: read the one field a step needs with "
        "a search or a one-line JSON read, not the whole file."
    )


def projection_tokens(text: str) -> int:
    """The o200k_base tokens of a projection exactly as it stands in the message."""

    with vendored_vocabulary_cache(VENDORED_ENCODING_NAME):
        encoding = tiktoken.get_encoding(VENDORED_ENCODING_NAME)
    return len(encoding.encode(text))


def _bound_document_entry(
    documents: Any, bound_document: dict[str, Any] | None
) -> list[dict[str, Any]] | None:
    """The one-item list holding the seat's bound task document, or None when it has none."""

    if bound_document is None or not isinstance(documents, list) or not documents:
        return None
    for entry in documents:
        if isinstance(entry, dict) and entry.get("taskDocumentRef") == bound_document:
            return [entry]
    # The effective task is always one of the resolved documents; a list that does not carry it
    # is still projected as its most specific entry rather than leaked whole.
    return [documents[-1]] if isinstance(documents[-1], dict) else None


def _subset(value: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    """The named parts of an object field; a value that is not an object stays as it is."""

    if not isinstance(value, dict):
        return value
    return {key: value[key] for key in keys if value.get(key) is not None}


def _reader_context(value: Any) -> dict[str, Any]:
    """The reader arguments; the repository choice stands in only where they are missing."""

    if not isinstance(value, dict):
        return value
    reader_arguments = value.get("readerArguments")
    if reader_arguments is not None:
        return {"readerArguments": reader_arguments}
    return _subset(value, ("availableRepositoryIds", "repositorySelection"))
