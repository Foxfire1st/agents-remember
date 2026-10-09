"""Every tool call of an agent, written to the Event River (MIK-R114 rule 1).

The river was silent on the host path: only a session lifecycle wrote events, and
an agent started through the host never holds one. So the tool server itself now
writes one ``tool.completed`` event per call, at the place where the answer is
completed, for a binding it snapshotted once at start:

* a process that holds an active lifecycle of the earlier kind keeps writing its
  event as it does today (``AmbientLifecycle.emit_tool``) and writes no second
  one -- that case ends when MIK-R113 removes the lifecycle;
* every other call goes to the river of the workspace (``workspace/events.jsonl``),
  under that river's lock, needing no active lifecycle and no call before it.

The event holds facts and no content: the tool, ``ok``, ``code`` when refused,
``state`` when the answer has one, the answer's token count, the caller (role,
agent, bound task by id and title), and ``targetRole`` for ``role_start`` and
``role_message``. No argument value, no answer text, no message text, and no
caller-given path ever reaches it; a string value longer than
``TOOL_EVENT_VALUE_MAX_CHARS`` is left out. The record keeps the
``ar-observer-event/v1`` schema, so existing readers keep reading it.

Nothing the feed needs lives only in the process: the caller snapshot is read
from the launch binding and the task document once at server start, so a server
started a second ago writes at its first call, and a restart continues with the
next call. For this feed a tool server holds two things in its memory and no
more (rule 4): that caller, and the count of lost events (rule 6). Losing the
count at a restart costs at most an undercount, never the feed; no file records
what was not written.

A write that fails does not break the call: the tool answers as if the event
had been written, the loss is counted, and the next written event carries
``dropped``; the count goes back to 0 when that write has succeeded, and not
before. Every append is admitted under the river's lock (rule 5): over the
ceiling the oldest rows are cut to 8 MiB first, under the same lock with the
stream offset kept right. A row that does not fit after the removal is not
written and is counted; the next append tries the removal again.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from agents_remember.observer import event_retention
from agents_remember.observer.events import Event, now_iso
from agents_remember.observer.store import EventStore
from agents_remember.observer.ulid import new_ulid

# MIK-R114: a value in a tool event is a short scalar of at most this many
# characters; a longer one is left out of the event.
TOOL_EVENT_VALUE_MAX_CHARS = 120
# How long the writer waits for the river lock before the event counts as
# dropped. The packet names no bound here; this is the writer's own, so a stuck
# lock is a counted loss rather than a hung tool call.
TOOL_EVENT_LOCK_TIMEOUT_SECONDS = 2.0
# The raw marker for a caller that is a chat without a role. The panel renders
# it as "chat without a role"; the event carries the short stable value.
CHAT_CALLER_ROLE = "chat"

# The role of the agent a ``role_start`` started, or a ``role_message`` addressed.
# Set by the role payload builders around the ``_tool_payload`` choke point in
# the same task context (for an id-addressed message, ``send_role_message``
# carries it from the resolved recipient's durable receipt), read where the
# answer is completed. A context variable rather than an argument because the
# choke point (``complete_tool_response``) receives the answer, not the call.
call_target_role: ContextVar[str | None] = ContextVar("ar_tool_call_target_role", default=None)


@dataclass(frozen=True)
class ToolCaller:
    """Who the tool server's events name as the caller, snapshotted at start."""

    role: str | None = None
    agent_id: str | None = None
    task_id: str | None = None
    task_title: str | None = None
    task_path: str | None = None
    task_repository: str | None = None
    harness: str | None = None


def _short(value: Any) -> Any:
    """Keep a short scalar; leave out a string past the packet's ceiling."""
    if isinstance(value, str) and len(value) > TOOL_EVENT_VALUE_MAX_CHARS:
        return None
    return value


def _first_str(finalized: Mapping[str, Any], *keys: str) -> str | None:
    """The first of ``keys`` the answer carries as a non-empty string, if any."""
    for key in keys:
        value = finalized.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _answer_facts(
    tool_name: str, finalized: Mapping[str, Any], *, delivered_tokens: int
) -> dict[str, Any]:
    """The fixed fact keys read out of a completed answer.

    Everything else the tool answered -- arguments, texts, details, paths --
    cannot reach the event regardless of what it holds.

    ``delivered_tokens`` is the log-line count of the actually
    delivered answer mapping, computed after the answer guards and log
    write. It is the event's ``tokens``. Whole-result
    token metadata is never read here.
    """
    facts: dict[str, Any] = {}
    short_tool = _short(tool_name)
    facts["tool"] = short_tool if short_tool is not None else "tool"
    ok = finalized.get("ok")
    if isinstance(ok, bool):
        facts["ok"] = ok
    if (
        isinstance(delivered_tokens, int)
        and not isinstance(delivered_tokens, bool)
        and delivered_tokens >= 0
    ):
        facts["tokens"] = delivered_tokens
    code = _first_str(finalized, "code", "refusal")
    if code is not None:
        facts["code"] = code
    state = _first_str(finalized, "state", "status")
    if state is not None:
        facts["state"] = state
    if tool_name in ("role_start", "role_message"):
        facts.update(_target_role_facts(tool_name, finalized))
    return facts


def _target_role_facts(tool_name: str, finalized: Mapping[str, Any]) -> dict[str, Any]:
    """The started or addressed role for the two role tools, when it is known."""
    target_role = call_target_role.get()
    if target_role is None and tool_name == "role_start":
        role_value = finalized.get("role")
        target_role = role_value if isinstance(role_value, str) else None
    if isinstance(target_role, str) and target_role:
        return {"targetRole": target_role}
    return {}


def _caller_facts(caller: ToolCaller) -> dict[str, Any]:
    """Who the event names as the caller: a bound agent, or a chat."""
    if caller.agent_id is None:
        facts: dict[str, Any] = {"role": CHAT_CALLER_ROLE}
        if caller.harness:
            facts["harness"] = caller.harness
        return facts
    facts = {}
    if caller.role:
        facts["role"] = caller.role
    facts["agentId"] = caller.agent_id
    if caller.task_id:
        facts["taskId"] = caller.task_id
    if caller.task_title:
        facts["taskTitle"] = caller.task_title
    elif caller.task_path:
        facts["taskPath"] = caller.task_path
    return facts


def build_tool_event(
    tool_name: str,
    finalized: Mapping[str, Any],
    caller: ToolCaller,
    *,
    delivered_tokens: int,
    dropped: int = 0,
) -> Event:
    """Build one facts-only ``tool.completed`` event; no content ever reaches it."""
    data = _answer_facts(tool_name, finalized, delivered_tokens=delivered_tokens)
    data.update(_caller_facts(caller))
    if dropped > 0:
        data["dropped"] = dropped
    pruned = {key: _short(value) for key, value in data.items()}
    data = {key: value for key, value in pruned.items() if value is not None}
    return Event(
        id=new_ulid(),
        ts=now_iso(),
        kind="tool.completed",
        trust="observed",
        actor="model",
        repoId=caller.task_repository,
        data=data,
    )


class ToolEventWriter:
    """The process-wide writer of workspace-river tool events.

    One instance per tool-server process, installed at start with the caller
    snapshot. ``record`` never raises: anything that goes wrong is counted in
    ``dropped`` and the tool answers as if the event had been written.
    """

    def __init__(
        self,
        store: EventStore,
        caller: ToolCaller,
    ) -> None:
        self._store = store
        self._caller = caller
        self._lock = threading.Lock()
        self._dropped = 0

    @property
    def caller(self) -> ToolCaller:
        return self._caller

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    def record(
        self,
        tool_name: str,
        finalized: Mapping[str, Any],
        *,
        delivered_tokens: int,
    ) -> bool:
        """Write one event for a completed call; never raises."""
        with self._lock:
            try:
                event = build_tool_event(
                    tool_name,
                    finalized,
                    self._caller,
                    delivered_tokens=delivered_tokens,
                    dropped=self._dropped,
                )
                admitted = event_retention.admit_workspace_river_row(
                    self._store.root,
                    _event_line(event).encode("utf-8"),
                    max_bytes=event_retention.WORKSPACE_EVENT_MAX_BYTES,
                    trim_to_bytes=event_retention.WORKSPACE_EVENT_TRIM_TO_BYTES,
                    timeout=TOOL_EVENT_LOCK_TIMEOUT_SECONDS,
                )
            except Exception:
                self._dropped += 1
                return False
            if not admitted:
                self._dropped += 1
                return False
            self._dropped = 0
            return True


def _event_line(event: Event) -> str:
    """The exact JSONL row the river stores for ``event`` (admission and reads agree)."""
    return event.model_dump_json(by_alias=True, exclude_none=True)


class _ToolEventRegistry:
    """Holds the one tool-event writer for this server process."""

    instance: ToolEventWriter | None = None


def install_tool_writer(writer: ToolEventWriter) -> None:
    """Install the process writer (once per server start)."""
    _ToolEventRegistry.instance = writer


def reset_tool_writer() -> None:
    """Clear the process writer (test isolation)."""
    _ToolEventRegistry.instance = None


def current_tool_writer() -> ToolEventWriter | None:
    """The installed process writer, or ``None`` outside a server process."""
    return _ToolEventRegistry.instance


def record_tool_completed(
    tool_name: str,
    finalized: Mapping[str, Any],
    *,
    delivered_tokens: int,
) -> bool:
    """Write one workspace-river event for a completed call; never raises.

    No writer is installed outside a server process: there the call is simply
    not observed. A failed write is counted inside the writer and the next
    written event carries the count.
    """
    writer = _ToolEventRegistry.instance
    if writer is None:
        return False
    try:
        return writer.record(tool_name, finalized, delivered_tokens=delivered_tokens)
    except Exception:
        return False
