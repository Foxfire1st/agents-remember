"""Deterministic dispatch acceptance from harness-owned session JSONL logs.

The delivery seam verifies its own input bytes, not terminal rendering. A message's existing unique
delivery id binds one hosted pane to one session log in the spawn cwd; later inputs reuse that path.
Claude local commands additionally require a non-error ``local-command-stdout`` record.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

_CLAUDE_COMMAND = re.compile(
    r"<command-name>(?P<name>[^<]+)</command-name>.*?"
    r"<command-args>(?P<args>.*?)</command-args>",
    re.DOTALL,
)
_LOCAL_STDOUT = re.compile(r"<local-command-stdout>(?P<text>.*?)</local-command-stdout>", re.DOTALL)
_LOCAL_STDERR = re.compile(r"<local-command-stderr>(?P<text>.*?)</local-command-stderr>", re.DOTALL)
_COMMAND_ERROR = re.compile(
    r"(?:^|\b)(?:error:|invalid argument:|valid options are:)", re.IGNORECASE
)


@dataclass(frozen=True)
class CommandEvidence:
    """Latest log evidence for one local session command."""

    recorded: bool = False
    succeeded: bool = False
    errored: bool = False
    output: str | None = None


@dataclass
class HarnessSessionLog:
    """One hosted harness's discoverable/bound session log.

    ``started_at`` excludes older same-cwd sessions. ``bound_path`` may be supplied from the catalog;
    otherwise the first id-bearing user message finds and binds the correct file.
    """

    harness: str
    cwd: Path
    started_at: datetime
    bound_path: Path | None = None
    claude_root: Path | None = None
    codex_root: Path | None = None

    def __post_init__(self) -> None:
        self.cwd = self.cwd.resolve()
        if self.started_at.tzinfo is None:
            self.started_at = self.started_at.replace(tzinfo=UTC)
        if self.claude_root is None:
            self.claude_root = Path.home() / ".claude" / "projects"
        if self.codex_root is None:
            self.codex_root = Path.home() / ".codex" / "sessions"
        if self.bound_path is not None:
            self.bound_path = self.bound_path.resolve()

    def message_present(self, entry_id: str) -> bool:
        """Bind by ``entry_id`` when needed, then require it in a real user-message record."""
        if self.bound_path is not None:
            return _message_present(self.bound_path, self.harness, entry_id, cwd=self.cwd)
        for path in self._candidate_paths():
            if _message_present(path, self.harness, entry_id, cwd=self.cwd):
                self.bound_path = path.resolve()
                return True
        return False

    def command_evidence(self, command: str) -> CommandEvidence:
        """Return latest bound-log command evidence; unbound/missing logs have no evidence."""
        if self.bound_path is None:
            return CommandEvidence()
        return _command_evidence(self.bound_path, self.harness, command, cwd=self.cwd)

    @property
    def command_evidence_supported(self) -> bool:
        """Whether this harness log has a truthful command-entry/output adapter."""

        return self.harness == "claude"

    def _candidate_paths(self) -> list[Path]:
        threshold = self.started_at.timestamp() - 2.0
        if self.harness == "claude":
            assert self.claude_root is not None
            project = self.claude_root / _claude_project_key(self.cwd)
            candidates = project.glob("*.jsonl") if project.is_dir() else ()
        elif self.harness == "codex":
            assert self.codex_root is not None
            if not self.codex_root.is_dir():
                candidates = ()
            else:
                # Codex partitions rollouts by UTC date. Restrict every 100 ms acceptance poll to
                # the spawn day (plus its adjacent rollover day) instead of recursively rescanning
                # the user's entire session history. Root-level files are retained for older/test
                # layouts.
                dates = {
                    self.started_at.astimezone(UTC).date(),
                    (self.started_at.astimezone(UTC) + timedelta(days=1)).date(),
                    datetime.now(UTC).date(),
                }
                candidates = list(self.codex_root.glob("*.jsonl"))
                for day in dates:
                    candidates.extend((self.codex_root / day.strftime("%Y/%m/%d")).glob("*.jsonl"))
        else:
            return []
        recent: list[tuple[float, Path]] = []
        for path in candidates:
            try:
                modified = path.stat().st_mtime
            except OSError:
                continue
            if modified >= threshold:
                recent.append((modified, path))
        return [path for _modified, path in sorted(recent, reverse=True)]


def _claude_project_key(cwd: Path) -> str:
    return str(cwd).replace("/", "-")


def _records(path: Path) -> list[dict[str, object]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    lines = text.splitlines()
    records: list[dict[str, object]] = []
    for index, line in enumerate(lines):
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            # Session files are append-only; a concurrent read may see the final record mid-write.
            # Ignore only that incomplete final append. Any malformed completed/interior row fails
            # closed rather than letting later records manufacture acceptance through corruption.
            if index == len(lines) - 1 and not text.endswith(("\n", "\r")):
                continue
            return []
        if isinstance(raw, dict):
            records.append(raw)
    return records


def _message_present(path: Path, harness: str, entry_id: str, *, cwd: Path) -> bool:
    records = _records(path)
    if not _cwd_matches(records, harness, cwd):
        return False
    return any(entry_id in text for text in _user_messages(records, harness))


def _cwd_matches(records: list[dict[str, object]], harness: str, cwd: Path) -> bool:
    expected = str(cwd)
    if harness == "claude":
        return any(record.get("cwd") == expected for record in records)
    for record in records:
        if record.get("type") not in ("session_meta", "turn_context"):
            continue
        payload = record.get("payload")
        if isinstance(payload, dict) and payload.get("cwd") == expected:
            return True
    return False


def _claude_user_text(record: dict[str, object]) -> str | None:
    """The developer-typed text of one Claude ``user`` record, or ``None`` if it is not one.

    Meta records and the ``<command-name>`` / ``<local-command-*>`` wrappers are harness bookkeeping
    rather than submitted input; a delivery id found inside one of those would not prove acceptance.
    """

    if record.get("type") != "user" or record.get("isMeta") is True:
        return None
    message = record.get("message")
    if not isinstance(message, dict) or message.get("role") != "user":
        return None
    text = _content_text(message.get("content"))
    if "<command-name>" in text or "<local-command-" in text:
        return None
    return text


def _codex_user_text(record: dict[str, object]) -> str | None:
    """The developer-typed text of one Codex rollout record, or ``None`` if it is not one.

    Codex writes the same submission twice under different envelopes -- a ``response_item`` message
    and an ``event_msg`` ``user_message`` -- and both are accepted here.
    """

    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    if record.get("type") == "response_item" and payload.get("type") == "message":
        if payload.get("role") != "user":
            return None
        return _content_text(payload.get("content"))
    if record.get("type") == "event_msg" and payload.get("type") == "user_message":
        message = payload.get("message")
        if isinstance(message, str):
            return message
    return None


_USER_MESSAGE_READERS: dict[str, Callable[[dict[str, object]], str | None]] = {
    "claude": _claude_user_text,
    "codex": _codex_user_text,
}


def _user_messages(records: list[dict[str, object]], harness: str) -> list[str]:
    read = _USER_MESSAGE_READERS.get(harness)
    if read is None:
        return []
    texts = (read(record) for record in records)
    return [text for text in texts if text is not None]


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in ("text", "input_text"):
            continue
        text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def _command_evidence(
    path: Path,
    harness: str,
    command: str,
    *,
    cwd: Path,
) -> CommandEvidence:
    records = _records(path)
    if not _cwd_matches(records, harness, cwd) or harness != "claude":
        return CommandEvidence()
    expected_name, expected_args = _command_parts(command)
    matches: list[CommandEvidence] = []
    for index, record in enumerate(records):
        content = _record_content(record)
        parsed = _CLAUDE_COMMAND.search(content)
        if parsed is None:
            continue
        if parsed.group("name").strip() != expected_name:
            continue
        if parsed.group("args").strip() != expected_args:
            continue
        matches.append(_following_command_output(records, index))
    return matches[-1] if matches else CommandEvidence()


def _command_parts(command: str) -> tuple[str, str]:
    name, _, args = command.strip().partition(" ")
    return name, args.strip()


def _record_content(record: dict[str, object]) -> str:
    message = record.get("message")
    if isinstance(message, dict):
        return _content_text(message.get("content"))
    content = record.get("content")
    return content if isinstance(content, str) else ""


def _following_command_output(
    records: list[dict[str, object]], command_index: int
) -> CommandEvidence:
    for record in records[command_index + 1 : command_index + 6]:
        content = _record_content(record)
        if _CLAUDE_COMMAND.search(content):
            break
        stderr = _LOCAL_STDERR.search(content)
        if stderr is not None:
            return CommandEvidence(recorded=True, errored=True, output=stderr.group("text"))
        stdout = _LOCAL_STDOUT.search(content)
        if stdout is not None:
            output = stdout.group("text")
            errored = bool(_COMMAND_ERROR.search(output))
            return CommandEvidence(
                recorded=True,
                succeeded=not errored,
                errored=errored,
                output=output,
            )
    return CommandEvidence(recorded=True)
