"""The product's held-message store: what it is on disk, and how a reader visits it.

The store is one JSON file per recipient inside that recipient's own working folder, named by the
product's own receipt, plus one bounded queue naming the receipts that hold something now. This
module owns those bytes -- file names, schemas, bounds, the atomic writer, the rows a message
leaves in its recipient's launch record, and the bounded discovery that finds the stores that
exist -- so the delivery machinery (:mod:`cli.role_held_messages`) and the native attention queue,
which only *reads* them (MIK-R200 rule 3), take one definition instead of two that can drift.

The queue is a visit order and never a census: it is bounded by age and by count, and a torn body
reads as no entries. The authority is the store file, so :class:`HeldAuthorityReader` reads the
stores themselves and says whether its census over them was complete. Nothing here sends, holds,
withdraws or drains anything.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents_remember.controlplane.held_store_discovery import (
    ENUMERATION_BUDGET,
    HeldFolderDiscovery,
    ReceiptRootWalk,
    ReceiptWalk,
    file_stamp,
)
from agents_remember.kernel.file_lock import exclusive_file_lock

if TYPE_CHECKING:
    from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

logger = logging.getLogger(__name__)

#: The store schema every file of one recipient carries.
HELD_STORE_SCHEMA = "ar-held-messages/v1"
#: Rule 4a: at most this many held messages wait for one recipient.
MAX_HELD_PER_RECIPIENT = 20
#: The product's own queue of receipts that hold messages (MIK-R171 rule 1).
HELD_QUEUE_SCHEMA = "ar-held-message-queue/v1"
HELD_QUEUE_FILE = "held-message-queue.json"
#: The queue is bounded twice: by age and by count. Losing an entry costs one later visit.
HELD_QUEUE_MAX_ENTRIES = 512
HELD_QUEUE_MAX_AGE_SECONDS = 24 * 60 * 60


def product_held_dir(workspace_path: str | Path) -> Path:
    """The product-owned part of a working folder that keeps held messages."""

    return Path(workspace_path) / ".ar-product" / "held-messages"


def store_path_for(workspace_path: str | Path, agent_id: str) -> Path:
    """The store file of one recipient; only an AR-minted id names one."""

    try:
        key = str(uuid.UUID(agent_id))
    except ValueError as error:
        raise ValueError(f"{agent_id!r} is not an agent id: AR agent ids are UUIDs.") from error
    if key != agent_id:
        raise ValueError(f"{agent_id!r} is not an agent id: AR agent ids are UUIDs.")
    return product_held_dir(workspace_path) / f"{agent_id}.json"


def read_queue_file(path: Path) -> dict[str, str]:
    """One held-message queue as receipt path to the time it was named; unreadable is empty."""

    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(body, dict) or body.get("schema") != HELD_QUEUE_SCHEMA:
        return {}
    entries = body.get("entries")
    if not isinstance(entries, dict):
        return {}
    return {
        key: value
        for key, value in entries.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def queue_is_readable(path: Path) -> bool:
    """Whether the queue exists and parses as one; an absent queue is a complete emptiness."""

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return True
    except OSError:
        return False
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(body, dict) and body.get("schema") == HELD_QUEUE_SCHEMA


def live_queue(entries: dict[str, str], now: datetime | None = None) -> dict[str, str]:
    """The entries still worth a visit: recent enough, oldest first, at most the bound."""

    moment = now or datetime.now(UTC)
    recent: list[tuple[str, str]] = []
    for path, stamped in entries.items():
        try:
            age = (moment - datetime.fromisoformat(stamped)).total_seconds()
        except ValueError:
            continue
        if age <= HELD_QUEUE_MAX_AGE_SECONDS:
            recent.append((stamped, path))
    recent.sort()
    return {path: stamped for stamped, path in recent[-HELD_QUEUE_MAX_ENTRIES:]}


def read_waiting_rows(
    workspace_path: str | Path, agent_id: str
) -> tuple[list[dict[str, Any]], bool]:
    """Every message still waiting for one recipient, and whether the read was complete.

    A missing store is a complete emptiness. A store that cannot be read, cannot be parsed, or
    carries a row the product could not have written is *incomplete*: the reader keeps its last
    known occurrence rather than presenting an unverified end (rule 3). Texts never leave here.
    """

    body, readable = _store_body(workspace_path, agent_id)
    if not readable:
        return [], False
    if body is None:
        return [], True
    return _waiting_rows(body.get("messages"))


def _store_body(workspace_path: str | Path, agent_id: str) -> tuple[dict[str, Any] | None, bool]:
    """One recipient's parsed store and whether the read was complete.

    ``(None, True)`` is the absent store, ``(body, True)`` holds messages, ``(None, False)`` is a
    read that could not say.
    """

    try:
        text = store_path_for(workspace_path, agent_id).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, True
    except (OSError, ValueError):
        return None, False
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        return None, False
    if not isinstance(body, dict) or body.get("schema") != HELD_STORE_SCHEMA:
        return None, False
    if body.get("agentId") != agent_id or not isinstance(body.get("messages"), list):
        return None, False
    return body, True


def _waiting_rows(messages: object) -> tuple[list[dict[str, Any]], bool]:
    """Each waiting row reduced to its identity and safe reason; whether every row was one.

    The text never leaves here. A row the store could not have written -- not an object, or
    without its id -- makes the read *incomplete*, so no caller reads a partly understood store
    as one that holds nothing.
    """

    rows: list[dict[str, Any]] = []
    whole = True
    for row in messages if isinstance(messages, list) else ():
        if not isinstance(row, dict):
            whole = False
            continue
        message_id = row.get("id")
        if not isinstance(message_id, str) or not message_id:
            whole = False
            continue
        rows.append(
            {
                "id": message_id,
                "senderAgentId": _text(row.get("senderAgentId")),
                "takenAt": _text(row.get("takenAt")),
                "reason": _text(row.get("reason")) or "held",
                "limitedUntil": _text(row.get("limitedUntil")),
            }
        )
    return rows, whole


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


# The queue's reader and its liveness rule, under the names the code below uses for them.
_read_queue = read_queue_file
_live_queue = live_queue


# --- the store's own primitives, its contents, and receipt discovery ---------------------
#
# :mod:`cli.role_held_messages` owns *when* a message is taken, claimed, sent and ended; what
# follows is what the store *is* on disk, so its writer and every reader share one definition.

# How many terminal lines a receipt keeps; the store itself keeps no ended text.
MAX_HELD_RECORD_LINES = 30
# A claim older than this belongs to a drain that never delivered, so it is retried.
STALE_CLAIM_SECONDS = 120.0
# Receipt subtrees a discovery never walks: archived records and bindings.
"""How deep under ``tasks`` a task folder can sit; its receipts are below it."""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _blank_store(agent_id: str) -> dict[str, Any]:
    return {
        "schema": HELD_STORE_SCHEMA,
        "agentId": agent_id,
        "messages": [],
        "updatedAt": _now_iso(),
    }


def _read_store_file(path: Path, agent_id: str) -> dict[str, Any]:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _blank_store(agent_id)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"The held-message store {path} is unreadable: {error}") from error
    if not isinstance(body, dict) or body.get("schema") != HELD_STORE_SCHEMA:
        raise ValueError(f"The held-message store {path} is malformed.")
    if body.get("agentId") != agent_id:
        raise ValueError(f"The held-message store {path} names another recipient.")
    messages = body.get("messages")
    if not isinstance(messages, list):
        raise ValueError(f"The held-message store {path} has no message list.")
    body["messages"] = [row for row in messages if isinstance(row, dict)]
    return body


def _write_store_file(path: Path, store: dict[str, Any]) -> None:
    store["updatedAt"] = _now_iso()
    _write_json_atomic(path, store)


def held_queue_path(config: McpRuntimeConfig) -> Path:
    """The one file that names the receipts holding messages (MIK-R171 rule 1)."""

    return config.coordination_root / "temp" / HELD_QUEUE_FILE


def _write_json_atomic(path: Path, body: dict[str, Any]) -> None:
    """Replace one small product file whole; a reader sees the old body or the new one."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = (json.dumps(body, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
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


def _update_queue(config: McpRuntimeConfig, change: Callable[[dict[str, str]], bool]) -> None:
    """Apply one change to the queue under its lock; a failure costs only a later visit."""

    path = held_queue_path(config)
    try:
        with exclusive_file_lock(path, "held-message queue"):
            entries = _live_queue(_read_queue(path))
            if change(entries) is False:
                return
            _write_json_atomic(path, {"schema": HELD_QUEUE_SCHEMA, "entries": entries})
    except (OSError, ValueError) as error:
        # The store already holds the message; the queue only orders the sweep's visits.
        logger.warning("The held-message queue %s could not be written: %s", path, error)


def note_held_receipt(config: McpRuntimeConfig, receipt_path: Path) -> None:
    """Name one receipt that holds messages, so a sweep visits it before the others."""

    def note(entries: dict[str, str]) -> bool:
        entries[receipt_path.as_posix()] = _now_iso()
        return True

    _update_queue(config, note)


def forget_held_receipt(config: McpRuntimeConfig, receipt_path: Path) -> None:
    """Forget one receipt that holds nothing now; an absent entry changes nothing."""

    def forget(entries: dict[str, str]) -> bool:
        return entries.pop(receipt_path.as_posix(), None) is not None

    _update_queue(config, forget)


def held_queue_receipts(config: McpRuntimeConfig) -> list[Path]:
    """The receipts that hold messages, the oldest visit first (MIK-R171 rule 1).

    Reading is lock-free: the queue is replaced whole, so a reader sees one complete body.
    """

    return [Path(path) for path in _live_queue(_read_queue(held_queue_path(config)))]


def waiting_total(workspace_path: str | Path, agent_id: str) -> int:
    """How many messages still wait for one recipient; a missing or unreadable store is none."""

    try:
        path = store_path_for(workspace_path, agent_id)
    except ValueError:
        return 0
    try:
        if not path.is_file():
            return 0
    except OSError:
        return 0
    with exclusive_file_lock(path, "held-message store"):
        try:
            store = _read_store_file(path, agent_id)
        except ValueError:
            return 0
        return len(store.get("messages", []))


def waiting_counts(store: dict[str, Any], sender_agent_id: str | None = None) -> tuple[int, int]:
    """How many messages of one sender, and in all, still wait."""

    messages = store.get("messages", [])
    total = len(messages)
    mine = (
        sum(1 for row in messages if row.get("senderAgentId") == sender_agent_id)
        if sender_agent_id
        else 0
    )
    return mine, total


def combined_text(messages: list[dict[str, Any]]) -> str:
    """One delivery for all that is held (D114): each under its sender's line, in order."""

    return "\n\n".join(f"{row['senderLine']}\n{row['text']}" for row in messages)


def combined_message_id(messages: list[dict[str, Any]]) -> str:
    """The stable id of one batch: a retry after a crash redelivers under the same id."""

    digest = hashlib.sha256(
        json.dumps(
            [row["id"] for row in messages], ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()[:32]
    return f"held-batch:{digest}"


class HeldStoreFull(ValueError):
    """A further message is refused as busy: the recipient already holds the bound."""


@dataclass(frozen=True, slots=True)
class HeldMessage:
    """One message taken into the store: who sent what text, and why it waits.

    ``attempted_message_id`` is the host id of a send the host did not answer (a refusal as
    host-unreachable, a timeout or an unknown outcome; MIK-R171 rules 2 and 3). The waiting
    message keeps that id, so its later delivery goes out under an id the host may already
    record, and a host that records a message id once delivers it at most once.

    ``limited_until`` is rule 9's reset time for a message a provider limit holds, as an ISO 8601
    time with offset, stored on the row itself: the row is what a restarted process reads back.
    """

    sender_line: str
    sender_agent_id: str
    text: str
    reason: str
    permission: str | None = None
    attempted_message_id: str | None = None
    limited_until: str | None = None


def hold_message(workspace_path: str | Path, agent_id: str, message: HeldMessage) -> dict[str, Any]:
    """Take one message the host cannot take now; refuse a message beyond the bound."""

    path = store_path_for(workspace_path, agent_id)
    with exclusive_file_lock(path, "held-message store"):
        store = _read_store_file(path, agent_id)
        messages = store["messages"]
        attempted = message.attempted_message_id
        if attempted and any(row.get("id") == attempted for row in messages):
            # The same attempt is taken twice (a repeat after a stop): one waiting copy.
            mine, total = waiting_counts(store, message.sender_agent_id)
            return {
                "heldMessageId": attempted,
                "waitingMine": mine,
                "waitingTotal": total,
                "limitedUntil": limited_until_in(store, attempted),
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
            "takenAt": _now_iso(),
            "reason": message.reason,
        }
        if message.permission:
            row["permission"] = message.permission
        if message.attempted_message_id:
            row["attemptedMessageId"] = message.attempted_message_id
        if message.limited_until:
            # Rule 9's reset time rides on the waiting row: why it waits, and until when.
            row["limitedUntil"] = message.limited_until
        messages.append(row)
        _write_store_file(path, store)
        mine, total = waiting_counts(store, message.sender_agent_id)
    return {
        "heldMessageId": message_id,
        "waitingMine": mine,
        "waitingTotal": total,
        "limitedUntil": row.get("limitedUntil"),
    }


def limited_until_in(store: dict[str, Any], message_id: str) -> str | None:
    """The reset time one already-waiting row carries, if it carries one."""

    for row in store.get("messages", []):
        if row.get("id") == message_id:
            value = row.get("limitedUntil")
            return value if isinstance(value, str) and value else None
    return None


class HeldMessageCommitted(Exception):
    """A waiting message already committed to delivery cannot be withdrawn."""

    def __init__(self, message_id: str) -> None:
        super().__init__(message_id)
        self.message_id = message_id


def withdraw_message(
    workspace_path: str | Path,
    agent_id: str,
    message_id: str,
    sender_agent_id: str,
    stale_seconds: float = STALE_CLAIM_SECONDS,
) -> dict[str, Any] | None:
    """End one waiting message of its sender as withdrawn; ``None`` when not waiting.

    Withdrawal and a delivery claim have one observable winner: a message under a live claim is
    committed to delivery and raises :class:`HeldMessageCommitted`. A stale claim belongs to a
    dead drain and is cleared with the withdrawal.
    """

    path = store_path_for(workspace_path, agent_id)
    with exclusive_file_lock(path, "held-message store"):
        store = _read_store_file(path, agent_id)
        if live_claim(store, stale_seconds) is not None:
            raise HeldMessageCommitted(message_id)
        ended = None
        for index, row in enumerate(store["messages"]):
            if row.get("id") == message_id and row.get("senderAgentId") == sender_agent_id:
                ended = store["messages"].pop(index)
                break
        cleared = (
            store.pop("deliveryClaim", None) is not None
            or store.pop("deliveryClaimAt", None) is not None
            or store.pop("deliveryClaimIds", None) is not None
        )
        if ended is None and not cleared:
            return None
        _write_store_file(path, store)
        return ended


def live_claim(store: dict[str, Any], stale_seconds: float) -> str | None:
    """The store's delivery claim when one is live; ``None`` when absent or stale."""

    claim = store.get("deliveryClaim")
    claimed_at = store.get("deliveryClaimAt")
    if not isinstance(claim, str) or not isinstance(claimed_at, str):
        return None
    try:
        age = (datetime.now(UTC) - datetime.fromisoformat(claimed_at)).total_seconds()
    except ValueError:
        return None
    return claim if age < stale_seconds else None


def end_waiting_as(workspace_path: str | Path, agent_id: str) -> list[dict[str, Any]]:
    """End every waiting message (archived or gone); the texts are deleted."""

    path = store_path_for(workspace_path, agent_id)
    with exclusive_file_lock(path, "held-message store"):
        store = _read_store_file(path, agent_id)
        ended = list(store["messages"])
        store["messages"] = []
        store.pop("deliveryClaim", None)
        store.pop("deliveryClaimAt", None)
        store.pop("deliveryClaimIds", None)
        _write_store_file(path, store)
    return ended


def record_lines(
    receipt: dict[str, Any], ended: list[dict[str, Any]], how: str
) -> list[dict[str, Any]]:
    """One line per ended message for the launch record: sender, time, how it ended.

    Lines are deduplicated by message id, so a staged end flushed twice never appears twice.
    """

    lines = receipt.get("heldMessageLines")
    if not isinstance(lines, list):
        lines = []
        receipt["heldMessageLines"] = lines
    have = {line.get("messageId") for line in lines if isinstance(line, dict)}
    now = _now_iso()
    for row in ended:
        if not isinstance(row, dict) or row.get("id") in have:
            continue
        have.add(row.get("id"))
        lines.append(
            {
                "messageId": row.get("id"),
                "sender": row.get("senderLine"),
                "senderAgentId": row.get("senderAgentId"),
                "time": now,
                "how": how,
            }
        )
    del lines[: max(0, len(lines) - MAX_HELD_RECORD_LINES)]
    return [line for line in lines if isinstance(line, dict)]


def _stage_pending_lines(
    workspace_path: str | Path, agent_id: str, ended: list[dict[str, Any]], how: str
) -> bool:
    """Persist ended rows for a later line write; ``True`` when they are staged.

    A receipt write that fails after the texts left the store still leaves its ends recoverable
    here.
    """

    try:
        path = store_path_for(workspace_path, agent_id)
    except ValueError:
        return False
    with exclusive_file_lock(path, "held-message store"):
        try:
            store = _read_store_file(path, agent_id)
        except ValueError:
            return False
        staged = store.get("pendingLines")
        if not isinstance(staged, list):
            staged = []
            store["pendingLines"] = staged
        have = {entry.get("messageId") for entry in staged if isinstance(entry, dict)}
        for row in ended:
            if not isinstance(row, dict) or row.get("id") in have:
                continue
            have.add(row.get("id"))
            staged.append(
                {
                    "messageId": row.get("id"),
                    "sender": row.get("senderLine"),
                    "senderAgentId": row.get("senderAgentId"),
                    "time": _now_iso(),
                    "how": how,
                }
            )
        try:
            _write_store_file(path, store)
        except OSError:
            return False
    return True


def sender_terminal_lines(
    receipt: dict[str, Any], sender_agent_id: str, limit: int = 10
) -> list[dict[str, Any]]:
    """What a sender reads about its ended messages at its next call (rule 4a)."""

    lines = receipt.get("heldMessageLines")
    if not isinstance(lines, list):
        return []
    mine = [
        line
        for line in lines
        if isinstance(line, dict) and line.get("senderAgentId") == sender_agent_id
    ]
    return mine[-limit:]


def _store_endpoints(receipt: dict[str, Any]) -> tuple[str, str] | None:
    """The (folder, agent) owning one receipt's held-message store, if named."""

    workspace = receipt.get("workspace")
    execution = receipt.get("execution")
    folder = workspace.get("path") if isinstance(workspace, dict) else None
    agent_id = execution.get("agentId") if isinstance(execution, dict) else None
    if not isinstance(folder, str) or not folder or not isinstance(agent_id, str):
        return None
    try:
        store_path_for(folder, agent_id)
    except ValueError:
        return None
    return folder, agent_id


def _clear_pending_lines(workspace_path: str | Path, agent_id: str, ids: list[str]) -> None:
    """Forget staged ends the receipt now records; best-effort, never raises."""

    try:
        path = store_path_for(workspace_path, agent_id)
    except ValueError:
        return
    with suppress(OSError, ValueError), exclusive_file_lock(path, "held-message store"):
        try:
            store = _read_store_file(path, agent_id)
        except ValueError:
            return
        staged = store.get("pendingLines")
        if not isinstance(staged, list):
            return
        store["pendingLines"] = [
            entry
            for entry in staged
            if not isinstance(entry, dict) or entry.get("messageId") not in ids
        ]
        _write_store_file(path, store)


def _read_store_counts(
    workspace_path: str | Path, agent_id: str, sender_agent_id: str
) -> tuple[int, int]:
    """The (mine, total) waiting now; an unreadable store counts as none."""

    try:
        path = store_path_for(workspace_path, agent_id)
    except ValueError:
        return 0, 0
    with exclusive_file_lock(path, "held-message store"):
        try:
            store = _read_store_file(path, agent_id)
        except ValueError:
            return 0, 0
        rows = [row for row in store.get("messages", []) if isinstance(row, dict)]
    mine = sum(1 for row in rows if row.get("senderAgentId") == sender_agent_id)
    return mine, len(rows)


def recount_waiting(receipt: dict[str, Any]) -> None:
    workspace = receipt.get("workspace")
    execution = receipt.get("execution")
    folder = workspace.get("path") if isinstance(workspace, dict) else None
    agent_id = execution.get("agentId") if isinstance(execution, dict) else None
    if not isinstance(folder, str) or not isinstance(agent_id, str):
        receipt["heldWaiting"] = 0
        return
    if not Path(folder).is_dir():
        # A removed working folder holds nothing; counting never recreates it.
        receipt["heldWaiting"] = 0
        return
    try:
        path = store_path_for(folder, agent_id)
    except ValueError:
        receipt["heldWaiting"] = 0
        return
    with exclusive_file_lock(path, "held-message store"):
        try:
            store = _read_store_file(path, agent_id)
        except ValueError as error:
            # The count is left as it stands rather than published as zero: a store that
            # cannot be read is not a store that holds nothing (MIK-R171 rule 5).
            logger.warning(
                "The held-message count of agent %s could not be refreshed: %s.", agent_id, error
            )
            return
        receipt["heldWaiting"] = len(store.get("messages", []))


# The name the line writers use: both recount from the store, never from a retained count.
recount_waiting_for = recount_waiting


# --- the authoritative held rows: the stores themselves, in bounded retained visits ----------
#
# The queue above is bounded by age and by count, and a torn body reads as no entries: taken as a
# census it would let a still-held message vanish. The store file is the authority, so
# :class:`HeldAuthorityReader` names the receipts that could own one, reads the stores in bounded
# visits, and says whether its census over them was complete.

#: Receipts one observation may visit; an unchanged one costs a stat and no parse.
MAX_HELD_AUTHORITY_RECEIPTS = 512
#: Stores one observation reads, least recently read first, so one beyond the cap is revisited
#: within a bounded number of observations instead of being dropped.
MAX_HELD_AUTHORITY_STORES = 64
#: Store records and receipt stamps retained; beyond this the oldest are shed and the observation
#: reports itself incomplete rather than quietly smaller.
MAX_HELD_AUTHORITY_RETAINED = 1024
#: Rows one view returns; the oldest are shed first and make the view incomplete.
MAX_HELD_AUTHORITY_ROWS = 256


@dataclass(frozen=True, slots=True)
class HeldAuthorityRow:
    """One message a store still holds, and the recipient that store belongs to."""

    recipient_agent_id: str
    folder: str
    message_id: str
    sender_agent_id: str
    taken_at: str
    reason: str
    limited_until: str | None = None


@dataclass(frozen=True, slots=True)
class HeldAuthorityView:
    """One bounded read of the held stores: the rows it read, and whether it read them all.

    ``rows`` is every row of every store whose own record is current at the stamp its file
    carries now, plus the rows last read from a store that could not be read whole. One store's
    emptiness clears that store's rows and no other's, so no pass resurrects a row. ``complete``
    claims the census: every known store recorded at its current stamp, discovery at the end of
    the receipts, a readable queue, nothing shed.
    """

    rows: tuple[HeldAuthorityRow, ...]
    complete: bool
    stores_read: int
    stores_known: int


@dataclass(frozen=True, slots=True)
class _HeldStoreTarget:
    """Where one recipient's store file lives: the two names a store is read by."""

    folder: str
    agent_id: str


@dataclass(frozen=True, slots=True)
class _StoreRecord:
    """One store's own last read: the stamp it was read at, its rows, and whether it was whole.

    The stamp makes a record current: a store whose file still carries it needs no second read,
    and one that changed is read again. A read that was not whole never counts as current.
    """

    stamp: tuple[int, int, int] | None
    rows: tuple[HeldAuthorityRow, ...]
    whole: bool


def _receipt_body(path: Path) -> dict[str, Any] | None:
    """One receipt's parsed body; ``None`` when the file cannot be read as one."""

    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def _rows_of(
    target: _HeldStoreTarget, waiting: list[dict[str, Any]]
) -> tuple[HeldAuthorityRow, ...]:
    """One store's waiting rows, reduced to what a reader may publish."""

    return tuple(
        HeldAuthorityRow(
            recipient_agent_id=target.agent_id,
            folder=target.folder,
            message_id=str(row["id"]),
            sender_agent_id=str(row.get("senderAgentId") or ""),
            taken_at=str(row.get("takenAt") or ""),
            reason=str(row.get("reason") or "held"),
            limited_until=row.get("limitedUntil"),
        )
        for row in waiting
    )


class HeldAuthorityReader:
    """The held stores themselves, read in bounded visits and retained between them.

    One instance per reader, kept beside the other last-known facts of whatever reads it. One
    call names candidate stores from the queue *as written*, from a resumable walk of the
    execution receipts, and from the bounded enumeration of each named folder's held directory;
    only a store file that exists is ever read. Stores are read least recently read first, at
    most :data:`MAX_HELD_AUTHORITY_STORES` per call, and each keeps its own record, so holders
    beyond one call's batch reach a complete census across passes. Nothing expires by time: a
    row leaves the view when it leaves its store, and a store that cannot be read keeps the view
    incomplete instead of empty.
    """

    def __init__(self) -> None:
        self._stamps: dict[str, tuple[int, int, int]] = {}
        self._targets: dict[str, _HeldStoreTarget] = {}
        self._records: dict[str, _StoreRecord] = {}
        self._visits: dict[str, int] = {}
        self._walks: dict[str, ReceiptWalk] = {}
        self._root_walk: ReceiptRootWalk | None = None
        self._root_base = ""
        self._folders: dict[str, tuple[bool, bool]] = {}
        self._folder_discovery = HeldFolderDiscovery()
        self.pending = True
        self.enumerated = 0
        self._store_pending = False
        self._discovery_shed = False
        self._discovery_complete = True
        self._queue_cursor, self._pass_over, self._ordinal = 0, False, 0

    def reset(self) -> None:
        """Forget everything retained, including the receipt listing: the next read is cold."""

        self._close_walks()
        for cache in (self._stamps, self._targets, self._records, self._visits):
            cache.clear()
        self._root_base = ""
        self._folders.clear()
        self._folder_discovery.reset()
        self.pending = True
        self._queue_cursor, self._pass_over, self._ordinal = 0, False, 0

    def read(self, coordination_root: str | Path) -> HeldAuthorityView:
        """One bounded observation of the held stores under one coordination root."""

        self._ordinal += 1
        root = Path(coordination_root)
        if self._root_base != root.as_posix():
            self.reset()
            self._root_base = root.as_posix()
        if self._root_walk is None or not self.pending:
            self._close_walks()
            self._root_walk = ReceiptRootWalk(root)
            self._folders = {folder: (False, True) for folder in self._folders}
            self._discovery_shed = False
            self._discovery_complete = True
        queued = self._queued_receipts(root / "temp" / HELD_QUEUE_FILE)
        discovered = self._discover(queued)
        # Trimming runs whatever the census says: a backlog may not grow the retained view.
        retained = self._retain()
        rows, stores_read, whole = self._read_stores()
        self.pending = (
            not self._root_walk.done
            or any(not walk.done for walk in self._walks.values())
            or any(not done for done, _whole in self._folders.values())
            or self._store_pending
        )
        return HeldAuthorityView(
            rows=tuple(rows),
            complete=queued is not None and discovered and retained and whole,
            stores_read=stores_read,
            stores_known=len(self._targets),
        )

    # --- discovery -----------------------------------------------------------

    def _queued_receipts(self, path: Path) -> list[str] | None:
        """Every receipt the queue names, expired entries included; ``None`` when it is torn."""

        if not queue_is_readable(path):
            return None
        return list(read_queue_file(path))

    def _queue_slice(self, queued: list[str], budget: int) -> tuple[list[str], bool]:
        """A rotating slice of the queue's entries; whether every entry fit in it."""

        if not queued:
            self._queue_cursor = 0
            return [], True
        start = self._queue_cursor % len(queued)
        ordered = queued[start:] + queued[:start]
        self._queue_cursor = (start + min(budget, len(queued))) % len(queued)
        return ordered[:budget], start + budget >= len(queued)

    def _discover(self, queued: list[str] | None) -> bool:
        """Learn this observation's store locations; whether the whole census was named."""

        # Half the budget at most: the walk always advances, so a full queue cannot starve it.
        paths, queue_whole = self._queue_slice(queued or [], MAX_HELD_AUTHORITY_RECEIPTS // 2)
        learned = True
        for path in paths:
            learned = self._learn(path) and learned
        assert self._root_walk is not None
        # One entry allowance across all three traversals. Reserving a third for each prevents
        # a crowded task tree, receipt directory or held folder from starving either sibling.
        share = max(1, ENUMERATION_BUDGET // 3)
        for key in self._root_walk.take(share):
            if key not in self._walks:
                self._walks[key] = ReceiptWalk(Path(key))
        self.enumerated = self._root_walk.examined
        while len(self._walks) > MAX_HELD_AUTHORITY_RETAINED:
            self._walks.pop(next(iter(self._walks))).close()
            self._discovery_shed = True
        walked = self._walk_receipts(
            min(
                share,
                ENUMERATION_BUDGET - self.enumerated,
                MAX_HELD_AUTHORITY_RECEIPTS - len(paths),
            )
        )
        self._discovery_complete = self._discovery_complete and learned and walked
        self._enroll_folders()
        self._pass_over = self._root_walk.done and all(walk.done for walk in self._walks.values())
        return (
            queue_whole
            and self._discovery_complete
            and self._pass_over
            and self._root_walk.complete
            and not self._discovery_shed
            and all(whole for _done, whole in self._folders.values())
        )

    def _walk_receipts(self, remaining: int) -> bool:
        learned = True
        for walk in self._walks.values():
            if remaining <= 0:
                break
            for path in walk.take(remaining, entry_budget=remaining):
                learned = self._learn(path) and learned
            self.enumerated += walk.examined
            remaining -= walk.examined
            learned = walk.complete and learned
        return learned

    def _enroll_folders(self) -> None:
        for folder, (done, _whole) in list(self._folders.items()):
            if done:
                continue
            remaining = ENUMERATION_BUDGET - self.enumerated
            if remaining <= 0:
                break
            listing = self._folder_discovery.read(product_held_dir(folder), budget=remaining)
            self.enumerated += listing.examined
            self._folders[folder] = (listing.done, listing.whole)
            for path in listing.paths:
                with suppress(ValueError):
                    store = store_path_for(folder, path.stem)
                    self._targets.setdefault(store.as_posix(), _HeldStoreTarget(folder, path.stem))

    def _learn(self, path: str) -> bool:
        """Take one receipt's store location when the file changed; whether it read whole."""

        receipt = Path(path)
        stamp = file_stamp(receipt)
        if stamp is None:
            # The queue names a receipt the disk no longer has: the walk reaches its store.
            return True
        if self._stamps.get(path) == stamp:
            return True
        body = _receipt_body(receipt)
        if body is None:
            # A torn receipt may name a store that holds rows: never claim a census for it.
            return False
        self._stamps[path] = stamp
        endpoints = _store_endpoints(body)
        if endpoints is None:
            # A receipt that names no store of ours (no folder, no AR-minted id) holds none.
            return True
        folder, agent_id = endpoints
        store = store_path_for(folder, agent_id)
        if store.is_file():
            self._targets[store.as_posix()] = _HeldStoreTarget(folder, agent_id)
        # Folder discovery advances independently of receipt stamps: an unchanged receipt must
        # not strand a partial folder walk at its first batch (L190-R1-F11).
        self._folders.setdefault(folder, (False, True))
        return True

    def _close_walks(self) -> None:
        if self._root_walk is not None:
            self._root_walk.close()
            self._root_walk = None
        for walk in self._walks.values():
            walk.close()
        self._walks = {}

    # --- the stores ----------------------------------------------------------

    def _read_stores(self) -> tuple[list[HeldAuthorityRow], int, bool]:
        """Read the stores with no record or a stale one; whether every store is current now.

        A record is current while its store's stamp is the one it was read at, so a store read in
        an earlier pass costs one stat and no parse, and one that changed is read again. Only
        :data:`MAX_HELD_AUTHORITY_STORES` stores are read per pass, least recently read first:
        the rest keep their own last rows and cost only the census claim, so rotation over more
        holders than one pass reads still reaches a complete pass.
        """

        stamps = {key: file_stamp(Path(key)) for key in self._targets}
        stale = sorted(
            (self._visits.get(key, 0), key, target)
            for key, target in self._targets.items()
            if key not in self._records or self._records[key].stamp != stamps[key]
        )
        self._store_pending = len(stale) > MAX_HELD_AUTHORITY_STORES
        for _, key, target in stale[:MAX_HELD_AUTHORITY_STORES]:
            self._visits[key] = self._ordinal
            waiting, whole = read_waiting_rows(target.folder, target.agent_id)
            last = self._records.get(key)
            # A torn read states no end: it keeps its own last rows, never another store's, and
            # never a subset of its own. A store read as (valid A, malformed row) must not replace
            # a complete (A, B) with (A): B was neither delivered nor withdrawn, so dropping it
            # would report an end the store never stated (L190-R1-F02). Only a complete read
            # replaces the last rows, and a store with no earlier record publishes what it could
            # read while still claiming incompleteness.
            kept = last.rows if last is not None and not whole else _rows_of(target, waiting)
            self._records[key] = _StoreRecord(stamps[key], kept, whole)
        records = {key: self._records.get(key) for key in self._targets}
        rows = [row for record in records.values() if record for row in record.rows]
        current = all(
            record is not None and record.whole and record.stamp == stamps[key]
            for key, record in records.items()
        )
        ordered = sorted(
            rows, key=lambda row: (row.taken_at, row.recipient_agent_id, row.message_id)
        )
        return (
            ordered[-MAX_HELD_AUTHORITY_ROWS:],
            min(len(stale), MAX_HELD_AUTHORITY_STORES),
            (current and len(ordered) <= MAX_HELD_AUTHORITY_ROWS),
        )

    def _retain(self) -> bool:
        """Keep the retained caches bounded, oldest first out; whether nothing was shed."""

        shed = False
        while len(self._targets) > MAX_HELD_AUTHORITY_RETAINED:
            oldest = next(iter(self._targets))
            for cache in (self._targets, self._records, self._visits):
                cache.pop(oldest, None)
            shed = True
        while len(self._stamps) > MAX_HELD_AUTHORITY_RETAINED:
            del self._stamps[next(iter(self._stamps))]
            shed = True
        while len(self._folders) > MAX_HELD_AUTHORITY_RETAINED:
            del self._folders[next(iter(self._folders))]
            shed = True
        return not shed


_AUTHORITY_READER = HeldAuthorityReader()
"""The process's retained authority view; a caller with its own lifetime keeps its own."""


def read_held_authority(coordination_root: str | Path) -> HeldAuthorityView:
    """The held rows the stores themselves record, from a bounded retained view.

    The module-level reader is the process-wide one; a caller that reads on its own heartbeat
    keeps a :class:`HeldAuthorityReader` of its own, so its retained stamps live and die with it.
    Nothing here sends, holds, withdraws or drains anything.
    """

    return _AUTHORITY_READER.read(coordination_root)


def reset_held_authority() -> None:
    """Forget the shared reader's retained view (a cold start, and between test modules)."""

    _AUTHORITY_READER.reset()
