"""Shared minimal wire fixture builders for codex sub-agent traffic.

Every shape here is synthesized (never captured user content) and proven field-for-field
against the vendored codex app-server protocol at
``~/.opensrc/repos/github.com/openai/codex/main/codex-rs/``:

- ``app-server-protocol/src/protocol/v2/item.rs`` — ``ThreadItem`` is
  ``#[serde(tag = "type", rename_all = "camelCase")]``; the ``CollabAgentToolCall`` variant
  carries ``id``/``tool``/``status``/``senderThreadId``/``receiverThreadIds``/``prompt?``/
  ``model?``/``reasoningEffort?``/``agentsStates`` with camelCase enums
  (``CollabAgentTool``: ``spawnAgent``/``sendInput``/``resumeAgent``/``wait``/``closeAgent``;
  ``CollabAgentToolCallStatus``: ``inProgress``/``completed``/``failed``;
  ``CollabAgentStatus``: ``pendingInit``/``running``/``interrupted``/``completed``/
  ``errored``/``shutdown``/``notFound``); the ``SubAgentActivity`` variant carries
  ``id``/``kind``(``started``/``interacted``/``interrupted``)/``agentThreadId``/``agentPath``
  (all required). ``ItemStartedNotification``/``ItemCompletedNotification`` params are
  ``{item, threadId, turnId, startedAtMs|completedAtMs}``; ``AgentMessageDeltaNotification``
  params are ``{threadId, turnId, itemId, delta}`` (the vendor always keys deltas by both
  thread AND turn — partial deltas exist only in adapter-defense tests);
  ``CommandExecutionRequestApprovalParams`` are ``{threadId, turnId, itemId, startedAtMs,
  reason?, command?, ...}``.
- ``app-server-protocol/src/protocol/v2/turn.rs`` — ``TurnStarted``/``TurnCompleted``
  notification params are ``{threadId, turn}``; a ``Turn`` carries ``id``/``items``/
  ``status``/``completedAt?``.
- ``app-server-protocol/src/protocol/v2/thread.rs`` — ``ThreadStatusChangedNotification``
  params are ``{threadId, status}`` with ``ThreadStatus`` ``#[serde(tag = "type")]``
  (``active`` carries ``activeFlags``).
- ``app-server-protocol/src/protocol/v2/notification.rs`` —
  ``ServerRequestResolvedNotification`` params are ``{threadId, requestId}``.
- ``app-server/tests/suite/v2/turn_start.rs`` (~3544-3868) — the live V1 spawn
  (``multi_agent_v1`` → ``collabAgentToolCall`` begin/end) and V2 spawn
  (``subAgentActivity``) sequences these builders model.

Fixtures are minimal: only fields a consumer reads are populated, and every field name is
one the vendor emits. Intentionally malformed shapes (for degrade/unknown-vendor tests)
stay inline in the test modules that assert them.
"""

from __future__ import annotations

from agents_remember.serving.codex_app_server_protocol import JsonObject

# Stable synthetic timestamps (seconds and milliseconds); no real capture data.
STARTED_AT_MS = 1784023100000
COMPLETED_AT_MS = 1784023200000
COMPLETED_AT_S = 1784023260


def notification(method: str, params: JsonObject) -> JsonObject:
    """A JSON-RPC notification envelope (``{"method", "params"}``)."""

    return {"method": method, "params": params}


def agent_message_item(item_id: str, text: str) -> JsonObject:
    """A ``ThreadItem::AgentMessage`` wire item."""

    return {"id": item_id, "type": "agentMessage", "text": text}


def collab_agent_tool_call_item(
    item_id: str,
    tool: str,
    *,
    sender_thread_id: str,
    status: str = "completed",
    receiver_thread_ids: list[str] | None = None,
    agents_states: dict[str, JsonObject] | None = None,
    prompt: str | None = None,
) -> JsonObject:
    """A ``ThreadItem::CollabAgentToolCall`` wire item.

    ``senderThreadId`` is always populated: the vendor emits it on every collab call.
    """

    item: JsonObject = {
        "id": item_id,
        "type": "collabAgentToolCall",
        "tool": tool,
        "status": status,
        "senderThreadId": sender_thread_id,
    }
    if receiver_thread_ids is not None:
        item["receiverThreadIds"] = receiver_thread_ids
    if agents_states is not None:
        item["agentsStates"] = agents_states
    if prompt is not None:
        item["prompt"] = prompt
    return item


def sub_agent_activity_item(
    item_id: str,
    *,
    kind: str,
    agent_thread_id: str,
    agent_path: str,
) -> JsonObject:
    """A ``ThreadItem::SubAgentActivity`` wire item (all four fields are required)."""

    return {
        "id": item_id,
        "type": "subAgentActivity",
        "kind": kind,
        "agentThreadId": agent_thread_id,
        "agentPath": agent_path,
    }


def thread_status_changed_params(thread_id: str, *, active: bool = True) -> JsonObject:
    """``thread/status/changed`` params; ``active=False`` yields the bare ``idle`` status."""

    status: JsonObject = {"type": "active", "activeFlags": []} if active else {"type": "idle"}
    return {"threadId": thread_id, "status": status}


def turn_started_params(thread_id: str, turn_id: str) -> JsonObject:
    """``turn/started`` params."""

    return {
        "threadId": thread_id,
        "turn": {"id": turn_id, "status": "inProgress", "items": []},
    }


def turn_completed_params(thread_id: str, turn_id: str) -> JsonObject:
    """``turn/completed`` params."""

    return {
        "threadId": thread_id,
        "turn": {
            "id": turn_id,
            "status": "completed",
            "items": [],
            "completedAt": COMPLETED_AT_S,
        },
    }


def item_started_params(thread_id: str, turn_id: str, item: JsonObject) -> JsonObject:
    """``item/started`` params."""

    return {
        "threadId": thread_id,
        "turnId": turn_id,
        "startedAtMs": STARTED_AT_MS,
        "item": item,
    }


def item_completed_params(thread_id: str, turn_id: str, item: JsonObject) -> JsonObject:
    """``item/completed`` params."""

    return {
        "threadId": thread_id,
        "turnId": turn_id,
        "completedAtMs": COMPLETED_AT_MS,
        "item": item,
    }


def agent_message_delta_params(
    thread_id: str, turn_id: str, item_id: str, delta: str
) -> JsonObject:
    """``item/agentMessage/delta`` params — the proven full shape (thread AND turn keyed)."""

    return {"threadId": thread_id, "turnId": turn_id, "itemId": item_id, "delta": delta}


def command_execution_approval_request(
    request_id: str,
    thread_id: str,
    turn_id: str,
    *,
    command: str = "git status",
    reason: str = "sub-agent approval",
) -> JsonObject:
    """A full ``item/commandExecution/requestApproval`` server-request envelope."""

    return {
        "id": request_id,
        "method": "item/commandExecution/requestApproval",
        "params": {
            "threadId": thread_id,
            "turnId": turn_id,
            "itemId": f"{request_id}-item",
            "startedAtMs": STARTED_AT_MS,
            "command": command,
            "reason": reason,
        },
    }


def server_request_resolved_notification(thread_id: str, request_id: str) -> JsonObject:
    """A ``serverRequest/resolved`` notification envelope."""

    return notification(
        "serverRequest/resolved", {"threadId": thread_id, "requestId": request_id}
    )
