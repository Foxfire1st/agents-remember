"""The developer's message files, their read by message id, and the sent-picks log (MIK-R188).

Every send from the developer's tray is filed before it reaches the host (rule 9, decision 7):
one file per target under ``<coordination>/notes/reports/developer-messages/<repository>/<task>/``,
where ``<task>`` is the id of the task document the target serves, or under
``…/<repository>/taskless/<agent id>/`` for a target without a task (decision 57; the repository
is the one the dashboard page names, L89 W30). The file is written whole or not at all
(``role_message_files.store_message_file``: a staged write, fsync and one atomic link). Its head
names the sender ``developer``, the recipient's role, agent id and task, the message id and the
time; a refused delivery keeps it under a name ending in ``.undelivered.md``. Image and text
picks are saved beside it under their SHA-256, and the message names each such file.

A message file's name ends with its message id, so the product finds it by that id with two
bounded patterns (:func:`read_developer_message`); MIK-R189 reads tray messages from there.

The sent-picks log (rule 10) is one JSON-lines file per recipient,
``…/developer-messages/sent/<agent id>.jsonl``: per filed send its time, target, pick ids,
message id, message file, SHA-256 and state. Lines are only added; the append itself drops the
oldest beyond its bounds (:func:`append_sent`). A held send's line also names its held entry and
the recipient's launch record, and is rewritten once that record says how the send ended
(:func:`settle_held`): a held send that is never delivered is not left reported as held.

The message files and the picks saved beside them are the record, kept like role_message's
files (45e5b434): nothing here deletes a filed message or a pick a message names. A filing that
fails before its message is placed removes the picks it saved itself, and only those
(:func:`message_filing`), so no saved pick is left that no message names.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from fastapi import HTTPException

from agents_remember.cli.role_launch_receipts import _read_receipt as read_receipt
from agents_remember.cli.role_message_files import (
    UNDELIVERED_SUFFIX,
    MessageContent,
    MessageFileError,
    mark_undelivered,
    render_message_file,
    sha256_file,
    store_message_file,
)
from agents_remember.errors import LockCapabilityError
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

DEVELOPER_MESSAGES_DIRECTORY = "developer-messages"
SENT_LOG_DIRECTORY = "sent"
TASKLESS_DIRECTORY = "taskless"
# The resource one folder's filings share; its lock file is ``.filing.lock`` in the folder.
FILING_LOCK = ".filing"
# The sent-picks log of one recipient keeps its latest lines; the folder keeps the logs of the
# recipients written to last. A read takes at most this many bytes from a log's end.
SENT_LOG_MAX_ENTRIES = 200
SENT_LOG_MAX_RECIPIENTS = 256
SENT_LOG_READ_BYTES = 4 * 1024 * 1024
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")
_HEAD_FIELD = re.compile(r"^([A-Za-z]+): (.*)$")


def _safe(value: str) -> str:
    return _SAFE.sub("_", value).strip("._") or "unnamed"


class _CoordinationRooted(Protocol):
    """Everything these paths need of the runtime config: the coordination root."""

    @property
    def coordination_root(self) -> Path: ...


def messages_root(config: _CoordinationRooted) -> Path:
    return config.coordination_root / "notes" / "reports" / DEVELOPER_MESSAGES_DIRECTORY


def target_folder(
    config: _CoordinationRooted, repository: str, task_id: str | None, agent_id: str
) -> Path:
    """The folder of one target's developer messages (rule 9, decisions 7 and 57)."""

    root = messages_root(config) / _safe(repository)
    return root / _safe(task_id) if task_id else root / TASKLESS_DIRECTORY / _safe(agent_id)


def message_stem(when: datetime, role: str, agent_id: str, message_id: str) -> str:
    """Time, recipient and message id: the id last, so a read by id is one pattern."""

    short = _safe(agent_id.replace("-", "")[:8])
    return f"{when.strftime('%Y%m%dT%H%M%SZ')}_developer_to_{_safe(role)}-{short}_{message_id}"


def _save_blob(folder: Path, data: bytes, suffix: str) -> tuple[Path, str, bool]:
    """Save one pick's bytes under their SHA-256, the same bytes once; whether this saved them."""

    digest = hashlib.sha256(data).hexdigest()
    target = folder / f"{digest}{suffix}"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            return target, digest, False
        descriptor, staged = tempfile.mkstemp(prefix=f".{digest}.", dir=folder)
    except OSError as error:
        raise MessageFileError(f"A pick cannot be saved in {folder}: {error}") from error
    created = True
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(staged, target)
        except FileExistsError:
            created = False
    except OSError as error:
        raise MessageFileError(f"A pick cannot be saved in {folder}: {error}") from error
    finally:
        Path(staged).unlink(missing_ok=True)
    return target, digest, created


class MessageFiling:
    """One filing in one folder: the picks it saves beside the message, then the message."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.created: list[Path] = []
        self.filed = False

    def save_blob(self, data: bytes, suffix: str) -> tuple[Path, str]:
        """Save one pick's bytes beside the message under their SHA-256; its path and digest."""

        path, digest, created = _save_blob(self.folder, data, suffix)
        if created:
            self.created.append(path)
        return path, digest

    def file_message(self, stem: str, content: MessageContent) -> tuple[Path, str]:
        """File the message whole before it is sent; its path and SHA-256."""

        path = store_message_file(self.folder, stem, render_message_file(content))
        self.filed = True
        return path, sha256_file(path)


@contextmanager
def message_filing(folder: Path) -> Iterator[MessageFiling]:
    """One filing under the folder's lock: a failure before the message is placed leaves none
    of the picks this filing saved (MIK-R188 rule 9, 45e5b434).

    Picks are shared by digest, so a pick saved before the filing, which another message may
    name, is only named again and never removed. The lock keeps another filing of the folder
    from naming a pick this one saved until this one's message is placed or the pick is gone.
    """

    filing = MessageFiling(folder)
    try:
        with exclusive_file_lock(folder / FILING_LOCK, "developer message filing"):
            try:
                yield filing
            finally:
                if not filing.filed:
                    for path in filing.created:
                        with suppress(OSError):
                            path.unlink()
    except (OSError, LockCapabilityError) as error:
        raise MessageFileError(f"The message cannot be filed in {folder}: {error}") from error


@dataclass(frozen=True, slots=True)
class FiledDeveloperMessage:
    """One filed developer message, read back by its message id."""

    path: Path
    sha256: str
    message_id: str
    recipient_agent_id: str
    delivered: bool
    text: str

    def head(self) -> dict[str, str]:
        return _head(self.text)


def read_developer_message(
    config: McpRuntimeConfig, message_id: str, *, recipient_agent_id: str | None = None
) -> tuple[FiledDeveloperMessage, ...]:
    """Every filed developer message with this id (one per target), oldest name first.

    The id is the end of the file's name, before ``.md`` or ``.undelivered.md``; a file is only
    taken when its head names the same message id. A file whose send was held is read after its
    recipient's held sends are settled (:func:`settle_held`), so its name says how it ended.
    """

    found = _filed(config, message_id, recipient_agent_id)
    recipients = {filed.recipient_agent_id for filed in found if filed.delivered}
    for recipient in sorted(recipients):
        settle_held(config, recipient)
    return _filed(config, message_id, recipient_agent_id) if recipients else found


def _filed(
    config: McpRuntimeConfig, message_id: str, recipient_agent_id: str | None
) -> tuple[FiledDeveloperMessage, ...]:
    root = messages_root(config)
    if not re.fullmatch(r"[0-9a-f-]{36}", message_id) or not root.is_dir():
        return ()
    patterns = (f"*/*/*_{message_id}*.md", f"*/{TASKLESS_DIRECTORY}/*/*_{message_id}*.md")
    found: list[FiledDeveloperMessage] = []
    for path in sorted({path for pattern in patterns for path in root.glob(pattern)}):
        try:
            data = path.read_bytes()
            text = data.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        head = _head(text)
        recipient = head.get("recipientAgentId", "")
        if head.get("messageId") != message_id or head.get("sender") != "developer":
            continue
        if recipient_agent_id is not None and recipient != recipient_agent_id:
            continue
        found.append(
            FiledDeveloperMessage(
                path=path,
                sha256=hashlib.sha256(data).hexdigest(),
                message_id=message_id,
                recipient_agent_id=recipient,
                delivered=not path.name.endswith(UNDELIVERED_SUFFIX),
                text=text,
            )
        )
    return tuple(found)


def _head(text: str) -> dict[str, str]:
    head: dict[str, str] = {}
    for line in text.splitlines():
        if not line:
            break
        match = _HEAD_FIELD.match(line)
        if match is not None:
            head.setdefault(match.group(1), match.group(2))
    return head


# ---------------------------------------------------------------------------------------------
# The sent-picks log
# ---------------------------------------------------------------------------------------------


def sent_log_path(config: McpRuntimeConfig, agent_id: str) -> Path:
    return messages_root(config) / SENT_LOG_DIRECTORY / f"{_safe(agent_id)}.jsonl"


def append_sent(config: McpRuntimeConfig, agent_id: str, entry: Mapping[str, Any]) -> None:
    """Add one line for one filed send; the oldest lines and logs go beyond the bounds.

    The log is a working surface of the tray, never the record: the message files are. So it is
    bounded by construction (coding guidelines D2/D4): a recipient's log keeps its latest
    ``SENT_LOG_MAX_ENTRIES`` lines, and the folder keeps the logs of the
    ``SENT_LOG_MAX_RECIPIENTS`` recipients written to last. This append is their compactor.
    """

    path = sent_log_path(config, agent_id)
    line = json.dumps(dict(entry), ensure_ascii=False, separators=(",", ":")) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(path, "developer sent-picks log"):
        try:
            kept = path.read_text(encoding="utf-8").splitlines(keepends=True)
        except FileNotFoundError:
            kept = []
        if len(kept) < SENT_LOG_MAX_ENTRIES:
            with open(path, "a", encoding="utf-8") as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
        else:
            _replace(path, "".join([*kept[len(kept) - SENT_LOG_MAX_ENTRIES + 1 :], line]))
    _evict_logs(path.parent)


def _replace(path: Path, text: str) -> None:
    descriptor, staged = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, path)
    finally:
        Path(staged).unlink(missing_ok=True)


def _evict_logs(folder: Path) -> None:
    """Drop the logs of the recipients written to least recently, beyond the bound."""

    logs = []
    for log in folder.glob("*.jsonl"):
        with suppress(OSError):
            logs.append((log.stat().st_mtime_ns, log))
    for _time, log in sorted(logs)[: max(0, len(logs) - SENT_LOG_MAX_RECIPIENTS)]:
        with suppress(OSError):
            log.unlink()


def settle_held(config: McpRuntimeConfig, agent_id: str) -> None:
    """Write how this recipient's held sends ended into its log (rules 9 and 10).

    A held send's line says ``held`` and names its held entry and the recipient's launch record.
    The send ends later, in a drain, a retirement or a replacement this module does not run; the
    end is a line of that record (``heldMessageLines``: the held id and how it ended). A send the
    record says was delivered is ``delivered``; any other end is ``undelivered``, and its file is
    kept under its undelivered name (Failure and Recovery), which the line then names with its
    SHA-256. An end the record no longer lists (it keeps its latest lines) leaves the line held.
    """

    path = sent_log_path(config, agent_id)
    if not path.is_file():
        return
    with exclusive_file_lock(path, "developer sent-picks log"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        except (OSError, UnicodeDecodeError):
            return
        held = {index: row for index, row in enumerate(map(_held_row, lines)) if row is not None}
        ends: dict[str, str] = {}
        for record in {(row["receipt"], row.get("requestId")) for row in held.values()}:
            ends.update(_held_ends(*record))
        settled = {
            index: _settled(row, ends[row["heldMessageId"]])
            for index, row in held.items()
            if row["heldMessageId"] in ends
        }
        for index, row in settled.items():
            lines[index] = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        if settled:
            _replace(path, "".join(lines))


def _held_row(line: str) -> dict[str, Any] | None:
    """One log line of a send that was held, with the record that tells how it ended."""

    try:
        row = json.loads(line)
    except ValueError:
        return None
    if not isinstance(row, dict) or row.get("state") != "held":
        return None
    named = (row.get("heldMessageId"), row.get("receipt"))
    return row if all(isinstance(value, str) and value for value in named) else None


def _held_ends(receipt: str, request_id: object) -> dict[str, str]:
    """How the held messages one launch record lists ended, by held id.

    A record a later launch replaced is read where it was moved, ``history/<request id>.json``.
    """

    path = Path(receipt)
    if not path.is_file() and isinstance(request_id, str) and request_id:
        path = path.parent / "history" / f"{_safe(request_id)}.json"

    try:
        record = read_receipt(path)
    except HTTPException:
        return {}
    lines = record.get("heldMessageLines") if record else None
    return {
        str(line["messageId"]): str(line.get("how") or "")
        for line in (lines if isinstance(lines, list) else [])
        if isinstance(line, dict) and isinstance(line.get("messageId"), str)
    }


def _settled(row: dict[str, Any], how: str) -> dict[str, Any]:
    if how == "delivered":
        return {**row, "state": "delivered"}
    filed = Path(str(row.get("messageFile") or ""))
    kept = filed
    if filed.is_file() and not filed.name.endswith(UNDELIVERED_SUFFIX):
        kept = mark_undelivered(
            filed,
            refusal=how or "not-delivered",
            refusal_detail="The held message ended without being delivered: the recipient was "
            "archived, retired or lost before it could take it.",
        )
    digest = sha256_file(kept) if kept.is_file() else row.get("sha256")
    return {
        **row,
        "state": "undelivered",
        "reason": how or "not-delivered",
        "messageFile": kept.as_posix(),
        "sha256": digest,
    }


def read_sent(config: McpRuntimeConfig, agent_id: str, limit: int) -> dict[str, Any]:
    """The latest ``limit`` sends to one recipient, newest first, its held sends settled."""

    settle_held(config, agent_id)
    path = sent_log_path(config, agent_id)
    try:
        size = path.stat().st_size
        with open(path, "rb") as stream:
            stream.seek(max(0, size - SENT_LOG_READ_BYTES))
            data = stream.read(SENT_LOG_READ_BYTES)
    except FileNotFoundError:
        return {"agentId": agent_id, "entries": [], "more": False}
    except OSError as error:
        return {"agentId": agent_id, "state": "unavailable", "detail": str(error)}
    lines = data.decode("utf-8", errors="replace").splitlines()
    if size > SENT_LOG_READ_BYTES and lines:
        lines = lines[1:]  # the first line read may be cut
    entries = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return {
        "agentId": agent_id,
        "entries": entries[:limit],
        "more": len(entries) > limit or size > SENT_LOG_READ_BYTES,
    }
