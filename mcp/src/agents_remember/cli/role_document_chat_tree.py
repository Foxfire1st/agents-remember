"""Name the chats of the documents the dashboard's tree lists, from one read of the host.

The read of one document (``role_document_chats``) asks the host once per launch record, on
every change of the selection: until it answers the chat pane does not know which chat belongs
to the document. This route answers for the tree's documents before a click. Each selection's
launch records are read from their computed paths and the host is asked a single time which
agents it still holds.

One request reads each record once, however many selections name its document, keeps of it
only the part an answer is made of, and reads no more bytes than its two limits allow.

What that one read cannot tell is answered as unknown (``agents: null``), never as "no chat": a
selection that names no document, a record that cannot be read, is damaged or belongs
elsewhere, a record over a byte limit, and an agent beyond a listing that did not end. The read
of the one document stays the authority that corrects this answer.
"""

from __future__ import annotations

import json
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agents_remember.cli.investigator_receipts import matches_selection
from agents_remember.cli.paseo_status import LiveAgents, read_live_agents
from agents_remember.cli.role_document_chats import (
    _chat_record,
    _document_agent,
    _hosted_agent_id,
    _primary_roles,
)
from agents_remember.cli.role_launch_receipts import RECEIPT_SCHEMA, _receipt_path
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import RoleResultRequest, RoleSelection
from agents_remember.tasks.document_refs import TaskDocumentRefError

# One request names at most this many documents. A document has at most three launch records;
# each distinct record is read once, within the two byte limits below, and the host is read
# once for the whole request.
TREE_SELECTION_LIMIT = 256
# The largest record this route reads: 1 MiB. A record holds its launch capsule, and the largest
# on the installation this was measured on is 362 KB. A larger record is not read here and its
# document is answered as unknown; a launch and the read of the one document read a record of
# any size.
RECORD_BYTES_LIMIT = 1024 * 1024
# The bytes of all records one request reads: 16 MiB, where that installation's 180 records are
# 5.1 MB together. Without it a request could read its 768 records at the record limit, 768 MiB
# for one answer. A record that no longer fits is not read and its document is unknown; records
# are read in the order the request names their documents, so the first named are answered.
REQUEST_BYTES_LIMIT = 16 * 1024 * 1024


class DocumentChatTreeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selections: list[RoleSelection] = Field(max_length=TREE_SELECTION_LIMIT)


def register_document_chat_tree_route(app: FastAPI, config: McpRuntimeConfig) -> None:
    def endpoint(request: DocumentChatTreeRequest) -> JSONResponse:
        return JSONResponse(document_chat_tree(config, request.selections))

    app.add_api_route("/api/role-launch/document-chats/tree", endpoint, methods=["POST"])


def document_chat_tree(config: McpRuntimeConfig, selections: list[RoleSelection]) -> dict[str, Any]:
    """One answer per selection, in the order asked; nothing per selection when the host is away."""

    reads = _RequestReads()
    records = [_selection_records(config, selection, reads) for selection in selections]
    live = read_live_agents(config)
    if not live.reachable:
        return {"chats": [], "unavailable": True, "detail": live.unreachable_reason}
    return {"chats": [{"agents": _live_agents(found, live)} for found in records]}


@dataclass(frozen=True, slots=True)
class _Reading:
    """What one read of a record's path gave.

    ``record`` is the kept part of the record (``_chat_record``). It is ``None`` where there is
    no record, and where there is one that cannot be told: ``readable`` is then false. ``cost``
    is the number of bytes the read took from the file.
    """

    record: dict[str, Any] | None = None
    readable: bool = True
    cost: int = 0


class _RequestReads:
    """The record reads of one request: each path once, all within the request's bytes.

    A path that was read answers every later selection that names it, so a document named many
    times costs one read and one kept record. Nothing is held beyond the request.
    """

    def __init__(self) -> None:
        self._readings: dict[Path, _Reading] = {}
        self._bytes_left = REQUEST_BYTES_LIMIT

    def record_at(self, path: Path) -> _Reading:
        reading = self._readings.get(path)
        if reading is None:
            reading = _read_record(path, min(RECORD_BYTES_LIMIT, self._bytes_left))
            self._bytes_left = max(0, self._bytes_left - reading.cost)
            self._readings[path] = reading
        return reading


def _read_record(path: Path, limit: int) -> _Reading:
    """Read the record at ``path`` when it has at most ``limit`` bytes, and keep its chat part.

    A path without a file is a role that was never started there. Anything else that is not a
    readable record of this line within the limit cannot be told.
    """

    try:
        status = path.lstat()
    except FileNotFoundError:
        return _Reading()
    except OSError:
        return _Reading(readable=False)
    if not stat.S_ISREG(status.st_mode) or status.st_size > limit:
        return _Reading(readable=False)
    data = _bounded_bytes(path, limit)
    record = _kept_part(data) if data is not None and len(data) <= limit else None
    return _Reading(record, readable=record is not None, cost=len(data or b""))


def _bounded_bytes(path: Path, limit: int) -> bytes | None:
    """The first ``limit + 1`` bytes of a file at most; ``None`` when it cannot be read.

    The size was measured before the read. The one byte beyond the limit shows a file that
    grew since, and the read ends there whatever its size has become.
    """

    try:
        with path.open("rb") as stream:
            return stream.read(limit + 1)
    except OSError:
        return None


def _kept_part(data: bytes) -> dict[str, Any] | None:
    """The chat part of a record's bytes; ``None`` when they are no whole record of this line."""

    try:
        receipt = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        # Bytes that are not UTF-8 or not JSON, a number too long to convert (all ValueError),
        # and a nesting beyond the parser's depth.
        return None
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA:
        return None
    record = _chat_record(receipt)
    report = receipt.get("report")
    if record is not None and isinstance(report, dict) and isinstance(report.get("path"), str):
        record["report"] = {"path": report["path"]}
    return record


def _selection_records(
    config: McpRuntimeConfig, selection: RoleSelection, reads: _RequestReads
) -> list[dict[str, Any]] | None:
    """The kept launch records of a document's primary roles; ``None`` where they cannot be told."""

    if not (
        selection.task_document_ref
        or selection.master_document_ref
        or selection.sprint_document_ref
    ):
        # The Projects chats are no document of the tree, and their record store is not bounded.
        return None
    records = []
    try:
        for role in _primary_roles(selection):
            scope = RoleResultRequest.model_validate({**selection.model_dump(), "role": role})
            reading = reads.record_at(_receipt_path(config, scope))
            if not reading.readable:
                return None
            if reading.record is None:
                continue
            if not matches_selection(reading.record, scope):
                return None
            records.append(reading.record)
    except (TaskDocumentRefError, OSError):
        return None
    return records


def _live_agents(
    records: list[dict[str, Any]] | None, live: LiveAgents
) -> list[dict[str, Any]] | None:
    if records is None:
        return None
    agents = []
    for record in records:
        agent_id = _hosted_agent_id(record)
        if agent_id is None:
            continue
        if agent_id in live.agent_ids:
            agents.append(_document_agent(record))
        elif not live.complete:
            return None
    return agents
