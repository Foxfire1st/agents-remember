"""The host-wide list of live AR seats (MIK-R188 rule 7), and the record of one seat by its agent.

A seat is an agent the host holds unarchived whose current AR launch record names it. The list
reads the host's unarchived agents once (``agents-live``, which pages the host's listing) and
joins them with this coordination root's current launch records: the record beside each task
folder and the per-request records of the taskless roles, never a history record. An agent the
host holds without such a record is no AR seat and is not listed. Nothing is started, bound or
changed.

Where the records are: beside a task document's folder at most three folders below its
repository's task root (``tasks/<repository>/[0_archive/]<master>/``), and under the
coordination root's own executions folder. The records of a folder are its ``*.json`` files and
the ``<role>/sessions/*.json`` files of the taskless roles. A deeper search would walk every file
of the task tree (555,838 files and 1.4 s on the installation this was measured on, 2026-10-19)
and finds copies of other coordination roots kept as evidence.

Bounds, measured on that installation: 337 current records of 39 MB in one task folder, the
largest 3.5 MB. One request reads a record of at most ``SEAT_RECORD_BYTES_LIMIT`` and fresh
records of at most ``SEAT_REQUEST_BYTES_LIMIT`` together. The kept part of a record (its chat
part, ``role_document_chats._chat_record``) is remembered by the file's identity and size and
time, so a record that did not change is not read again. A record that cannot be told (damaged,
over a bound) leaves the list incomplete, never shorter (INV-GD0F05, INV-4BT846): ``complete`` is
true only when the host said its listing ended and every record could be read.
"""

from __future__ import annotations

import stat
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from agents_remember.cli.paseo_status import read_live_agents
from agents_remember.cli.role_document_chat_tree import _read_record, _Reading
from agents_remember.cli.role_document_chats import _hosted_agent_id
from agents_remember.cli.role_launch_receipts import EXECUTIONS_DIRECTORY
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_identity import canonical_role
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology

SEAT_PAGE_SIZE = 50
SEAT_RECORD_BYTES_LIMIT = 8 * 1024 * 1024
SEAT_REQUEST_BYTES_LIMIT = 96 * 1024 * 1024
# How many kept parts the process remembers; beyond it the oldest is dropped and read again.
_KEPT_LIMIT = 8192
# The narrowest document a stored selection names comes first.
_REFERENCE_ORDER = ("taskDocumentRef", "masterDocumentRef", "sprintDocumentRef")


@dataclass(frozen=True, slots=True)
class SeatRecord:
    """One current launch record that names a hosted agent: where it is and what a row needs."""

    path: Path
    agent_id: str
    workspace_id: str | None
    role: str
    selection: dict[str, Any]
    created_at: str | None

    @property
    def reference(self) -> TaskDocumentRef | None:
        """The narrowest task document the seat serves; ``None`` for a taskless seat."""

        for name in _REFERENCE_ORDER:
            ref = self.selection.get(name)
            if isinstance(ref, dict):
                try:
                    return TaskDocumentRef.model_validate(ref)
                except ValueError:
                    return None
        return None


@dataclass(frozen=True, slots=True)
class SeatWalk:
    """The current records of one walk, and how many could not be told."""

    records: tuple[SeatRecord, ...]
    unread: int

    def record_of(self, agent_id: str) -> SeatRecord | None:
        """The newest current record that names the agent."""

        named = [record for record in self.records if record.agent_id == agent_id]
        return max(named, key=lambda record: record.created_at or "") if named else None


class _KeptParts:
    """The kept parts of records by file identity, size and time; nothing else is held."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._parts: dict[Path, tuple[tuple[int, int, int, int], _Reading]] = {}

    def reading(self, path: Path, signature: tuple[int, int, int, int]) -> _Reading | None:
        with self._lock:
            kept = self._parts.get(path)
        return kept[1] if kept is not None and kept[0] == signature else None

    def keep(self, path: Path, signature: tuple[int, int, int, int], reading: _Reading) -> None:
        with self._lock:
            self._parts.pop(path, None)
            if len(self._parts) >= _KEPT_LIMIT:
                self._parts.pop(next(iter(self._parts)))
            self._parts[path] = (signature, _Reading(reading.record, reading.readable))


_KEPT = _KeptParts()


def walk_seats(config: McpRuntimeConfig) -> SeatWalk:
    """Every current launch record of this coordination root that names a hosted agent."""

    records: list[SeatRecord] = []
    unread = 0
    budget = SEAT_REQUEST_BYTES_LIMIT
    for path in _record_paths(config):
        try:
            status = path.lstat()
        except OSError:
            unread += 1
            continue
        if not stat.S_ISREG(status.st_mode):
            unread += 1
            continue
        signature = (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns)
        reading = _KEPT.reading(path, signature)
        if reading is None:
            reading = _read_record(path, min(SEAT_RECORD_BYTES_LIMIT, budget))
            budget = max(0, budget - reading.cost)
            if reading.readable:
                _KEPT.keep(path, signature, reading)
        if not reading.readable:
            unread += 1
            continue
        record = _seat_record(path, reading.record)
        if record is not None:
            records.append(record)
    return SeatWalk(tuple(records), unread)


def _record_paths(config: McpRuntimeConfig) -> Iterator[Path]:
    coordination = config.coordination_root
    roots = [coordination / "notes" / "reports" / EXECUTIONS_DIRECTORY]
    tasks = coordination / "tasks"
    for depth in ("*", "*/*", "*/*/*"):
        roots.extend(sorted(tasks.glob(f"{depth}/notes/reports/{EXECUTIONS_DIRECTORY}")))
    for root in roots:
        if not root.is_dir():
            continue
        yield from sorted(root.glob("*.json"))
        yield from sorted(root.glob("*/sessions/*.json"))


def _seat_record(path: Path, kept: dict[str, Any] | None) -> SeatRecord | None:
    if kept is None:
        return None
    agent_id = _hosted_agent_id(kept)
    if agent_id is None:
        return None
    created = kept.get("createdAt")
    return SeatRecord(
        path=path,
        agent_id=agent_id,
        workspace_id=kept["execution"].get("workspaceId"),
        role=canonical_role(str(kept["role"])),
        selection=kept["selection"],
        created_at=created if isinstance(created, str) else None,
    )


# ---------------------------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------------------------


def seat_list(config: McpRuntimeConfig, offset: int) -> dict[str, Any]:
    """One page of the live AR seats, sorted by task, role and start time."""

    live = read_live_agents(config)
    if not live.reachable:
        return {"state": "unavailable", "detail": live.unreachable_reason or "host unreachable"}
    walk = walk_seats(config)
    newest: dict[str, SeatRecord] = {}
    for record in walk.records:
        if record.agent_id not in live.agent_ids:
            continue
        held = newest.get(record.agent_id)
        if held is None or (record.created_at or "") > (held.created_at or ""):
            newest[record.agent_id] = record
    documents = _Documents(config)
    rows = sorted((_row(record, documents) for record in newest.values()), key=_row_order)
    page = rows[offset : offset + SEAT_PAGE_SIZE]
    following = offset + SEAT_PAGE_SIZE
    answer: dict[str, Any] = {
        "state": "seats",
        "seats": page,
        "nextOffset": following if following < len(rows) else None,
        "complete": live.complete and walk.unread == 0,
    }
    gaps = _gaps(live.complete, walk.unread)
    if gaps:
        answer["detail"] = gaps
    return answer


def _gaps(listing_ended: bool, unread: int) -> str:
    parts = []
    if not listing_ended:
        parts.append("the host's listing of its agents did not end, so some seats may be missing")
    if unread:
        parts.append(
            f"{unread} launch record(s) could not be read (damaged or over a bound), so their "
            "seats are unknown"
        )
    return "; ".join(parts)


class _Documents:
    """The id and title of each task document one list names, read once per request."""

    def __init__(self, config: McpRuntimeConfig) -> None:
        self._topology = TaskDocumentTopology(config.coordination_root)
        self._read: dict[TaskDocumentRef, tuple[str, str]] = {}

    def named(self, ref: TaskDocumentRef) -> tuple[str, str]:
        if ref not in self._read:
            try:
                document = self._topology.resolve(ref).document
                self._read[ref] = (document.id, document.title)
            except (TaskDocumentRefError, OSError, ValueError):
                # A document that cannot be read is named by its reference and no title.
                self._read[ref] = (ref.key, "")
        return self._read[ref]


def task_of(config: McpRuntimeConfig, record: SeatRecord) -> dict[str, str] | None:
    """The task a seat serves, as a row and a message's first line name it."""

    return _task(record, _Documents(config))


def _task(record: SeatRecord, documents: _Documents) -> dict[str, str] | None:
    ref = record.reference
    if ref is None:
        return None
    document_id, title = documents.named(ref)
    return {"repository": ref.repository, "path": ref.path, "id": document_id, "title": title}


def _row(record: SeatRecord, documents: _Documents) -> dict[str, Any]:
    row: dict[str, Any] = {
        "agentId": record.agent_id,
        "role": record.role,
        "task": _task(record, documents),
        "createdAt": record.created_at,
    }
    if record.workspace_id:
        row["workspaceId"] = record.workspace_id
    return row


def _row_order(row: dict[str, Any]) -> tuple[Any, ...]:
    task = row["task"]
    # Seats of a task first, by repository and task id; the taskless seats after them.
    place = (0, task["repository"], task["id"]) if task else (1, "", "")
    return (*place, row["role"], row["createdAt"] or "", row["agentId"])


def register_seat_list_route(app: FastAPI, config: McpRuntimeConfig) -> None:
    def endpoint(offset: int = Query(default=0, ge=0)) -> JSONResponse:
        return JSONResponse(seat_list(config, offset))

    app.add_api_route("/api/role-chats", endpoint, methods=["GET"])
