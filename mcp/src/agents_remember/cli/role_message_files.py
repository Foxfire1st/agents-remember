"""One file per message between role agents (MIK-R117).

Every :func:`send_role_message` that passes validation writes exactly one
file before the message is handed to the host, and the recipient reads the
file through one line appended to the delivered text. The file has no size
limit; the delivered text does (the settings' limit).

A message file begins with the time, the sender line, the recipient's role,
id and task, and the message id. The message follows, then the full text
(rule 3: ``body`` and ``attach``). A delivery the host refuses keeps its
file under a name ending in ``.undelivered.md`` that names the refusal.

The files of a task lie in one folder in the task's record: the narrowest
task sender and recipient share (the leaf, else the master, else the
sprint). For agents without a shared task the folder lies beside the start
briefs of the sender's class. Nothing of it lies in a Git worktree: every
folder is under the coordination record, and a leaf workspace only carries
a ``messages`` link to its own leaf folder. Cleanup removes the link; no
cleanup deletes a message file.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# The link a leaf workspace carries to its leaf message folder.
MESSAGES_LINK = "messages"
MESSAGES_DIRECTORY = "messages"
# A delivery the host refused keeps its file under this suffix.
UNDELIVERED_SUFFIX = ".undelivered.md"

# Path components of a message file name carry only these characters.
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


class MessageFileError(Exception):
    """A message file (or an attachment) could not be recorded."""


@dataclass(frozen=True, slots=True)
class MessageAttachment:
    """One attached file as the message file records it."""

    source: str
    sha256: str
    copy: str | None = None


@dataclass(frozen=True, slots=True)
class MessageScope:
    """The narrowed folder decision for one message."""

    folder: Path
    record_root: Path
    level: str


def _safe(value: str) -> str:
    return _SAFE.sub("_", value).strip("._") or "unnamed"


def _short_id(agent_id: str) -> str:
    return _safe(agent_id.replace("-", "")[:8])


def utc_stem(
    sender_role: str,
    sender_id: str,
    recipient_role: str,
    recipient_id: str,
    *,
    when: datetime | None = None,
) -> str:
    """The file stem of one message: time, sender and recipient (rule 2)."""
    stamp = (when or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return (
        f"{stamp}_{_safe(sender_role)}-{_short_id(sender_id)}"
        f"_to_{_safe(recipient_role)}-{_short_id(recipient_id)}"
    )


_NEXT_COUNTER: dict[tuple[str, str], int] = {}
_NEXT_COUNTER_MAX = 512


def _remember_next_counter(key: tuple[str, str], value: int) -> None:
    """Remember the next free counter after a placed name, FIFO-bounded.

    The cache only skips names; the atomic link stays the authority, so a
    stale or evicted entry costs retries, never a wrong file (L117-C1-F08).
    """
    if key in _NEXT_COUNTER:
        _NEXT_COUNTER[key] = max(_NEXT_COUNTER[key], value)
    else:
        if len(_NEXT_COUNTER) >= _NEXT_COUNTER_MAX:
            _NEXT_COUNTER.pop(next(iter(_NEXT_COUNTER)))
        _NEXT_COUNTER[key] = value


def store_message_file(folder: Path, stem: str, content: str) -> Path:
    """Write one message file whole or not at all, and return its path.

    The payload is written and synced once into a staged file; the unique name
    comes from a bounded per-folder counter, so a same-second batch costs one
    full write per message and linear link attempts with no directory
    enumeration (L117-C1-F08). A name another message took meanwhile is skipped
    by counter, and the write is atomic: no caller ever sees a partial file.
    """
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise MessageFileError(
            f"The message folder cannot be created at {folder}: {error}"
        ) from error
    encoded = content.encode("utf-8")
    try:
        fd, staged = tempfile.mkstemp(prefix=f".{stem}.", dir=folder)
    except OSError as error:
        raise MessageFileError(f"The message file cannot be staged in {folder}: {error}") from error
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        Path(staged).unlink(missing_ok=True)
        raise MessageFileError(
            f"The message file cannot be written in {folder}: {error}"
        ) from error
    try:
        key = (os.fspath(folder), stem)
        counter = 2
        stemmed = stem
        while True:
            candidate = folder / f"{stemmed}.md"
            try:
                os.link(staged, candidate)
            except FileExistsError:
                if stemmed == stem:
                    cached = _NEXT_COUNTER.get(key)
                    if cached is not None and cached > counter:
                        counter = cached
                stemmed = f"{stem}-{counter}"
                counter += 1
                continue
            if stemmed != stem:
                _remember_next_counter(key, counter)
            return candidate
    except OSError as error:
        raise MessageFileError(f"The message file cannot be placed in {folder}: {error}") from error
    finally:
        Path(staged).unlink(missing_ok=True)


def sha256_file(path: Path) -> str:
    """The SHA-256 hex digest of one file's bytes."""
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    """The SHA-256 hex digest of one text's UTF-8 bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def leaf_messages_folder(task_dir: Path, leaf_key: str) -> Path:
    """The message folder of one leaf: notes/messages/<slug> in its task record."""
    slug = _safe(Path(leaf_key.split(":", 1)[-1]).stem)
    return task_dir / "notes" / MESSAGES_DIRECTORY / slug


def narrowed_scope(
    *,
    coordination_root: Path,
    sender_refs: dict[str, str | None],
    recipient_refs: dict[str, str | None],
    sender_role: str,
    task_dir_of: Any,
) -> MessageScope:
    """The folder of one message: the narrowest task both agents share.

    ``sender_refs``/``recipient_refs`` map ``leaf``/``master``/``sprint`` to
    the reference key (``repository:path``) or ``None``. ``task_dir_of`` maps
    a reference key to the task directory that holds its document. For agents
    without a shared task the folder lies beside the start briefs of the
    sender's class (rule 5).
    """
    leaf = sender_refs.get("leaf")
    if leaf is not None and leaf == recipient_refs.get("leaf"):
        task_dir = Path(task_dir_of(leaf))
        folder = leaf_messages_folder(task_dir, leaf)
        return MessageScope(folder=folder, record_root=task_dir, level="leaf")
    master = sender_refs.get("master")
    if master is not None and master == recipient_refs.get("master"):
        task_dir = Path(task_dir_of(master))
        folder = task_dir / "notes" / MESSAGES_DIRECTORY
        return MessageScope(folder=folder, record_root=task_dir, level="master")
    sprint = sender_refs.get("sprint")
    if sprint is not None and sprint == recipient_refs.get("sprint"):
        task_dir = Path(task_dir_of(sprint))
        folder = task_dir / "notes" / MESSAGES_DIRECTORY
        return MessageScope(folder=folder, record_root=task_dir, level="sprint")
    base = coordination_root / "notes" / "reports" / "paseo-native-executions" / _safe(sender_role)
    return MessageScope(folder=base / MESSAGES_DIRECTORY, record_root=base, level="taskless")


def record_attachments(
    folder: Path,
    stem: str,
    attach: list[str],
    *,
    record_roots: list[Path],
) -> list[MessageAttachment]:
    """Record every attached path with its SHA-256 (rule 3).

    A file that lies in the task's record or in a leaf workspace is named
    with its hash and not copied. Any other file is copied into the folder
    and the copy is named. Every path is checked before anything is written:
    a missing or unreadable path refuses and nothing is written. A failed
    copy removes what this message wrote.
    """
    planned: list[tuple[str, Path, str, bool]] = []
    for raw in attach:
        source = Path(raw)
        if not source.is_file():
            raise MessageFileError(
                f"The attached path does not exist or cannot be read: {raw}. "
                "Nothing was written and nothing was delivered."
            )
        try:
            digest = sha256_file(source)
        except OSError as error:
            raise MessageFileError(
                f"The attached path cannot be read: {raw} ({error}). "
                "Nothing was written and nothing was delivered."
            ) from error
        resolved = source.resolve()
        planned.append(
            (raw, resolved, digest, any(_inside(resolved, root) for root in record_roots))
        )
    return _copy_planned(folder, stem, planned)


def _make_folder(folder: Path) -> None:
    """Create the message folder, or refuse with its path and reason (F01)."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise MessageFileError(
            f"The message folder cannot be created at {folder}: {error}. "
            "Nothing was written and nothing was delivered."
        ) from error


def _copy_planned(
    folder: Path, stem: str, planned: list[tuple[str, Path, str, bool]]
) -> list[MessageAttachment]:
    """Copy the planned outside files into the folder and record them all.

    All-or-nothing (L117-C1-F01): a failed copy removes its own partial
    target as well as every earlier copy of this message.
    """
    records: list[MessageAttachment] = []
    copied: list[Path] = []
    _make_folder(folder)
    try:
        for index, (raw, _resolved, digest, inside) in enumerate(planned, start=1):
            if inside:
                records.append(MessageAttachment(source=raw, sha256=digest))
                continue
            target = folder / f"{stem}.attach-{index}-{_safe(Path(raw).name)}"
            duplicate = 2
            while target.exists():
                target = folder / f"{stem}.attach-{index}-{duplicate}-{_safe(Path(raw).name)}"
                duplicate += 1
            try:
                with open(raw, "rb") as origin, open(target, "xb") as clone:
                    for chunk in iter(lambda: origin.read(65536), b""):
                        clone.write(chunk)
            except OSError as error:
                target.unlink(missing_ok=True)
                raise MessageFileError(
                    f"The attached path cannot be copied into the message folder: {raw} ({error})."
                ) from error
            copied.append(target)
            records.append(MessageAttachment(source=raw, sha256=digest, copy=target.as_posix()))
    except Exception:
        for target in copied:
            target.unlink(missing_ok=True)
        raise
    return records


def _inside(path: Path, root: Path) -> bool:
    try:
        return path.is_relative_to(root)
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class MessageContent:
    """The text of one message file: head, message, then the full text."""

    when: datetime
    sender_line: str
    sender_task: str
    recipient_role: str
    recipient_id: str
    recipient_task: str
    message_id: str
    text: str
    body: str | None = None
    attachments: tuple[MessageAttachment, ...] = ()
    refusal: str | None = None
    refusal_detail: str | None = None


def render_message_file(content: MessageContent) -> str:
    """Render one message file: head, message, then the full text of rule 3."""
    lines = [
        f"time: {content.when.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"sender: {content.sender_line}",
        f"senderTask: {content.sender_task}",
        f"recipientRole: {content.recipient_role}",
        f"recipientAgentId: {content.recipient_id}",
        f"recipientTask: {content.recipient_task}",
        f"messageId: {content.message_id}",
    ]
    if content.refusal is not None:
        lines.append(f"delivery: refused as {content.refusal}")
        lines.append(f"refusalDetail: {content.refusal_detail or ''}")
    lines += ["", "## message", content.text]
    if content.body is not None:
        lines += ["", "## body", content.body]
    if content.attachments:
        lines.append("")
        lines.append("## attachments")
        for record in content.attachments:
            if record.copy is not None:
                lines.append(f"- {record.source} sha256:{record.sha256} (copied as {record.copy})")
            else:
                lines.append(f"- {record.source} sha256:{record.sha256}")
    return "\n".join(lines) + "\n"


def file_line(path: Path, digest: str) -> str:
    """The one last line a delivered text carries where a file belongs to it."""
    return f"Message file: {path.as_posix()} (SHA-256 {digest})"


def ensure_messages_link(workspace_path: Path, folder: Path) -> str | None:
    """Carry a leaf workspace's ``messages`` link to its leaf folder.

    A workspace that has none gets it at the first message. Returns a
    warning where the link cannot be made; the message itself is still
    written, because the folder in the record is what the rule requires.
    """
    link = workspace_path / MESSAGES_LINK
    target = folder.resolve()
    try:
        if link.is_symlink():
            if link.resolve() == target:
                return None
            return (
                f"The workspace link {link} leads to {link.resolve()} instead of "
                f"{target}; it was left alone and the message file is only in the record."
            )
        if link.exists():
            return (
                f"The workspace path {link} is occupied by a non-link; it was left alone "
                "and the message file is only in the record."
            )
        workspace_path.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target, target_is_directory=True)
        return None
    except OSError as error:
        return f"The workspace link {link} cannot be made ({error}); the message file is only in the record."


@dataclass
class PreparedMessage:
    """One message ready to hand to the host: its file, digest and text."""

    path: Path
    sha256: str
    delivered_text: str
    has_file_line: bool = False


def mark_undelivered(path: Path, *, refusal: str, refusal_detail: str) -> Path:
    """Keep a refused delivery's file under a name ending in .undelivered.md."""
    target = path.with_name(path.stem + UNDELIVERED_SUFFIX)
    counter = 2
    while target.exists():
        target = path.with_name(f"{path.stem}-{counter}{UNDELIVERED_SUFFIX}")
        counter += 1
    try:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(f"\ndelivery: refused as {refusal}\nrefusalDetail: {refusal_detail}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(path, target)
        return target
    except OSError:
        return path


__all__ = [
    "MESSAGES_DIRECTORY",
    "MESSAGES_LINK",
    "UNDELIVERED_SUFFIX",
    "MessageAttachment",
    "MessageFileError",
    "MessageScope",
    "PreparedMessage",
    "ensure_messages_link",
    "file_line",
    "mark_undelivered",
    "narrowed_scope",
    "record_attachments",
    "render_message_file",
    "sha256_file",
    "sha256_text",
    "store_message_file",
    "utc_stem",
]
