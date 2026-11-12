"""The master-to-architect record: which hosted architect seat owns one master (MIK-R189 rule 7).

The record is the one identity ``explainer_control``'s ``update`` and ``retire`` are accepted from
(MIK-R186 rule 6); the explainer reports with ``role_message`` like every agent (MIK-R211 v5). It
lives at ``<coordination>/notes/reports/master-architect/<repository>/<master folder>.json`` and
holds three facts: the architect agent id, the act that wrote it, and the time.

It has two writers, and the last write wins:

* every Manager start (``cli/role_launch_routes.py``), recording the architect that started the
  Manager, and
* ``explainer_control`` action ``claim``, which any hosted architect seat may call to take a
  master over.

Both write through :func:`write_record`, so the file is replaced whole and a reader sees the old
record or the new one, never half of either (the kernel's atomic write). Reading never creates
anything: a missing or unreadable record is simply a master no seat is named for.
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_remember.canvas.explainer_service import ExplainerHost
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.kernel.primitives.control_vocabulary import (
    BRIDGE_REFUSED,
    BRIDGE_TIMEOUT,
    EXECUTIONS_DIRECTORY,
    HELD_STORE_SCHEMA,
    PROVIDER_LIMIT_REASON,
    SEND_OUTCOME_UNKNOWN,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.witnessed_memo import WitnessedMemo
from agents_remember.observer.provider_limits import limited_until, read_provider_limits
from agents_remember.serving.paseo.provider_limits import provider_limits_path

__all__ = [
    "ACTS",
    "BRIDGE_REFUSED",
    "BRIDGE_TIMEOUT",
    "CLAIM",
    "EXECUTIONS_DIRECTORY",
    "HELD_STORE_SCHEMA",
    "MANAGER_START",
    "MAX_AGENT_ID",
    "MAX_MASTER_ARCHITECT_BYTES",
    "PROVIDER_LIMIT_REASON",
    "SEND_OUTCOME_UNKNOWN",
    "ExplainerControlHostPorts",
    "HeldMessage",
    "HeldMessageCommitted",
    "HeldStoreFull",
    "architect_receipt",
    "bind_explainer_control_host",
    "explainer_control_host",
    "hold_message",
    "leaf_execution_receipts",
    "master_architect_path",
    "provider_limit_hold",
    "read_execution_receipt",
    "read_record",
    "read_record_architect",
    "store_path_for",
    "waiting_total",
    "withdraw_message",
    "write_record",
]

#: The act of a Manager start's writer: the architect that started the Manager.
MANAGER_START = "manager-start"
#: The act of a hosted architect seat taking a master over through ``explainer_control``.
CLAIM = "claim"
#: Every act a record carries; a writer names one of these and nothing else.
ACTS = (MANAGER_START, CLAIM)

#: One agent id's bound, as the record's readers take it.
MAX_AGENT_ID = 128
#: One record is three short facts; anything larger is not one this module wrote.
MAX_MASTER_ARCHITECT_BYTES = 4096

RECORD_DIRECTORY = ("notes", "reports", "master-architect")
DEVELOPER_MESSAGES_DIRECTORY = ("notes", "reports", "developer-messages")
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_.-]+")


def master_architect_path(coordination_root: Path, repository_id: str, master: str) -> Path:
    """The record of one master: its repository folder, then the master folder's own name."""

    return (
        coordination_root.joinpath(*RECORD_DIRECTORY)
        / _segment(repository_id, "repository")
        / f"{_segment(master, 'master')}.json"
    )


def write_record(
    path: Path,
    *,
    architect_agent_id: str,
    act: str,
    at: str | None = None,
) -> dict[str, str]:
    """Replace one master's record with the seat this act names, and answer what was written."""

    if act not in ACTS:
        raise ValueError(f"{act!r} is not a master-to-architect act; use one of {ACTS}")
    body = {
        "architectAgentId": _agent_id(architect_agent_id),
        "act": act,
        "at": at or datetime.now(UTC).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write_text(path, json.dumps(body, indent=2, sort_keys=True) + "\n")
    return body


def read_record(path: Path) -> dict[str, str] | None:
    """The record at one master's address, or ``None`` when it names no seat or cannot be read."""

    try:
        if path.stat().st_size > MAX_MASTER_ARCHITECT_BYTES:
            return None
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    architect = body.get("architectAgentId")
    if not isinstance(architect, str) or not 0 < len(architect) <= MAX_AGENT_ID:
        return None
    record: dict[str, str] = {"architectAgentId": architect}
    for name in ("act", "at"):
        value = body.get(name)
        if isinstance(value, str) and value:
            record[name] = value
    return record


def read_record_architect(path: Path) -> str | None:
    """The agent id one master's record names, or ``None`` when no record names one."""

    record = read_record(path)
    return None if record is None else record["architectAgentId"]


def _segment(value: str, what: str) -> str:
    """One path segment of the record's own address, never a path a caller chose."""

    cleaned = value.strip()
    if (
        not cleaned
        or cleaned in {".", ".."}
        or "/" in cleaned
        or "\\" in cleaned
        or len(cleaned) > 128
    ):
        raise ValueError(f"a master-to-architect record needs one {what} name, not {value!r}")
    return cleaned


def _agent_id(value: str) -> str:
    cleaned = value.strip() if isinstance(value, str) else ""
    if not 0 < len(cleaned) <= MAX_AGENT_ID:
        raise ValueError(
            f"a master-to-architect record names an architect agent id of 1 to {MAX_AGENT_ID} "
            f"characters, not {value!r}"
        )
    return cleaned


# -- report custody, limit hold, held-store file operations, and host ports ------------------

MAX_HELD_PER_RECIPIENT = 20


class HeldStoreFull(ValueError):
    """A further message is refused as busy: the recipient already holds the bound."""


class HeldMessageCommitted(ValueError):
    """The waiting message was already committed or is no longer held by this sender."""


@dataclass(frozen=True, slots=True)
class HeldMessage:
    """One message taken into the recipient's held-message store."""

    sender_line: str
    sender_agent_id: str
    text: str
    reason: str
    permission: str | None = None
    attempted_message_id: str | None = None
    limited_until: str | None = None


@dataclass(frozen=True)
class ExplainerControlHostPorts:
    """Host and bridge adapters injected from ``cli`` into ``application``."""

    host_factory: Callable[[McpRuntimeConfig], ExplainerHost]
    bridge_send: Callable[[McpRuntimeConfig, str, dict[str, object]], dict[str, object]]


_HOST_PORTS: list[ExplainerControlHostPorts] = []


def bind_explainer_control_host(ports: ExplainerControlHostPorts) -> None:
    """Bind the Paseo host and bridge-send adapters from the CLI layer."""

    _HOST_PORTS.clear()
    _HOST_PORTS.append(ports)


def explainer_control_host(config: McpRuntimeConfig) -> ExplainerHost | None:
    if not _HOST_PORTS:
        return None
    return _HOST_PORTS[0].host_factory(config)


def provider_limit_hold(
    config: McpRuntimeConfig, agent_id: str, now: datetime | None = None
) -> dict[str, Any] | None:
    """MIK-R190 rule 9's hold check over the product's one provider-limits file."""

    path = provider_limits_path(config)
    if path is None:
        return None
    until = limited_until(read_provider_limits(path), agent_id, now or datetime.now(UTC))
    if until is None:
        return None
    return {
        "heldReason": PROVIDER_LIMIT_REASON,
        "limitedUntil": until.isoformat(),
        "detail": (
            f"The recipient's provider usage limit lasts until {until.isoformat()}; "
            "the message is held and delivered after that moment, in the order it was taken."
        ),
    }


def read_execution_receipt(path: Path) -> dict[str, Any]:
    """Read one role-launch execution receipt JSON file without importing FastAPI/CLI."""

    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Execution receipt is unreadable: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Execution receipt is malformed: {path}")
    return data


def architect_receipt(
    coordination_root: Path, architect_agent_id: str
) -> tuple[Path, dict[str, Any]] | None:
    """The architect's own execution receipt, read from its one folder and matched by agent id.

    The receipt of a taskless seat sits in the coordination root's executions folder; the scan is
    that single directory, never a walk over ``tasks/**``. An unreadable file is skipped, not
    guessed, and a file without this line's schema is not a receipt.
    """

    for folder in (
        coordination_root / "notes" / "reports" / EXECUTIONS_DIRECTORY,
        coordination_root / "notes" / "reports" / "role-executions",
    ):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            if path.is_symlink() or path.parent != folder:
                continue
            try:
                receipt = read_execution_receipt(path)
            except (OSError, ValueError):
                continue
            if receipt.get("agentId") != architect_agent_id:
                continue
            return path, receipt
    return None


def leaf_execution_receipts(master_root: Path, leaf_id: str) -> tuple[dict[str, Any], ...]:
    """The execution receipts a master's own folder holds for one leaf, read bounded.

    One directory list, filtered by the receipt's own ``selection.taskDocumentRef`` (or by the
    legacy ``<role>-<leaf>`` file stem); no walk over the task tree.
    """

    folder = master_root / "notes" / "reports" / EXECUTIONS_DIRECTORY
    if not folder.is_dir():
        return ()
    found: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.json")):
        if path.is_symlink() or path.parent != folder or "message-bindings" in path.parts:
            continue
        try:
            receipt = _witnessed_receipt(path)
        except (OSError, ValueError):
            continue
        selection = receipt.get("selection")
        ref = selection.get("taskDocumentRef") if isinstance(selection, dict) else None
        ref_path = ref.get("path") if isinstance(ref, dict) else None
        named_leaf = (
            ref_path.rsplit("/", 1)[-1].removesuffix(".json") if isinstance(ref_path, str) else ""
        )
        if leaf_id in {named_leaf, path.stem}:
            found.append(copy.deepcopy(receipt))
    return tuple(found)


type _ReceiptWitness = tuple[int, int, int, int, int, int]

#: The last parse per receipt file, with the identity it was read under. A parse is kept only
#: when the identity was the same before and after its read; callers receive copies. A master
#: folder holds about 400 receipts and the coordination root about 800 in all, so the bound
#: sits well above that working set; an evicted receipt is simply parsed again.
_parsed_receipts: WitnessedMemo[Path, _ReceiptWitness, dict[str, Any]] = WitnessedMemo(8192)


def _receipt_witness(path: Path) -> _ReceiptWitness:
    """Device, inode, mode, size, mtime and ctime; ctime moves on a restored-mtime rewrite."""
    info = os.lstat(path)
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _witnessed_receipt(path: Path) -> dict[str, Any]:
    """One receipt, parsed again only when the file's full identity moved."""
    try:
        before = _receipt_witness(path)
    except FileNotFoundError:
        _parsed_receipts.discard(path)
        raise
    kept = _parsed_receipts.get(path, before)
    if kept is not None:
        return kept[0]
    receipt = read_execution_receipt(path)
    if _receipt_witness(path) == before:
        _parsed_receipts.put(path, before, receipt)
    return receipt


def store_path_for(workspace_path: str | Path, agent_id: str) -> Path:
    """The held-message store file of one recipient; only an AR-minted UUID names one."""

    try:
        key = str(uuid.UUID(agent_id))
    except ValueError as error:
        raise ValueError(f"{agent_id!r} is not an agent id: AR agent ids are UUIDs.") from error
    if key != agent_id:
        raise ValueError(f"{agent_id!r} is not an agent id: AR agent ids are UUIDs.")
    return Path(workspace_path) / ".ar-product" / "held-messages" / f"{agent_id}.json"


def _read_held_store(path: Path, agent_id: str) -> dict[str, Any]:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "schema": HELD_STORE_SCHEMA,
            "agentId": agent_id,
            "messages": [],
            "updatedAt": datetime.now(UTC).isoformat(),
        }
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"The held-message store {path} is unreadable: {error}") from error
    if (
        not isinstance(body, dict)
        or body.get("schema") != HELD_STORE_SCHEMA
        or body.get("agentId") != agent_id
        or not isinstance(body.get("messages"), list)
    ):
        raise ValueError(f"The held-message store {path} is malformed.")
    body["messages"] = [row for row in body["messages"] if isinstance(row, dict)]
    return body


def _write_held_store(path: Path, store: dict[str, Any]) -> None:
    store["updatedAt"] = datetime.now(UTC).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = (json.dumps(store, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def waiting_total(workspace_path: str | Path, agent_id: str) -> int:
    """How many messages still wait for one recipient; unreadable counts as none."""

    try:
        path = store_path_for(workspace_path, agent_id)
        if not path.is_file():
            return 0
    except (OSError, ValueError):
        return 0
    with exclusive_file_lock(path, "held-message store"):
        try:
            store = _read_held_store(path, agent_id)
        except ValueError:
            return 0
        return len(store.get("messages", []))


def hold_message(workspace_path: str | Path, agent_id: str, message: HeldMessage) -> dict[str, Any]:
    """Take one message into the recipient's held-message store under its file lock."""

    path = store_path_for(workspace_path, agent_id)
    with exclusive_file_lock(path, "held-message store"):
        store = _read_held_store(path, agent_id)
        messages = store["messages"]
        attempted = message.attempted_message_id
        if attempted and any(row.get("id") == attempted for row in messages):
            mine = sum(1 for row in messages if row.get("senderAgentId") == message.sender_agent_id)
            until = next(
                (
                    row.get("limitedUntil")
                    for row in messages
                    if row.get("id") == attempted and isinstance(row.get("limitedUntil"), str)
                ),
                None,
            )
            return {
                "heldMessageId": attempted,
                "waitingMine": mine,
                "waitingTotal": len(messages),
                "limitedUntil": until,
            }
        if len(messages) >= MAX_HELD_PER_RECIPIENT:
            raise HeldStoreFull(
                f"Agent {agent_id} already holds {len(messages)} messages, the bound for one "
                "recipient; none was dropped to make room."
            )
        message_id = attempted or str(uuid.uuid4())
        row: dict[str, Any] = {
            "id": message_id,
            "senderLine": message.sender_line,
            "senderAgentId": message.sender_agent_id,
            "text": message.text,
            "reason": message.reason,
            "permission": message.permission,
            "takenAt": datetime.now(UTC).isoformat(),
        }
        if message.limited_until is not None:
            row["limitedUntil"] = message.limited_until
        messages.append(row)
        _write_held_store(path, store)
        mine = sum(1 for item in messages if item.get("senderAgentId") == message.sender_agent_id)
        return {
            "heldMessageId": message_id,
            "waitingMine": mine,
            "waitingTotal": len(messages),
            "limitedUntil": message.limited_until,
        }


def withdraw_message(
    workspace_path: str | Path, agent_id: str, message_id: str, sender_agent_id: str
) -> dict[str, Any]:
    """End one waiting message in a recipient's held-message store under its file lock."""

    path = store_path_for(workspace_path, agent_id)
    if not path.is_file():
        raise ValueError(f"Held message {message_id} was not found for {agent_id}.")
    with exclusive_file_lock(path, "held-message store"):
        store = _read_held_store(path, agent_id)
        kept: list[dict[str, Any]] = []
        target: dict[str, Any] | None = None
        for row in store["messages"]:
            if row.get("id") == message_id and target is None:
                target = row
            else:
                kept.append(row)
        if target is None:
            raise ValueError(f"Held message {message_id} was not found for {agent_id}.")
        if target.get("senderAgentId") != sender_agent_id:
            raise ValueError(f"Held message {message_id} belongs to another sender.")
        if target.get("claimedAt"):
            raise HeldMessageCommitted(
                f"Held message {message_id} is already being delivered and cannot be withdrawn."
            )
        store["messages"] = kept
        _write_held_store(path, store)
        return target
