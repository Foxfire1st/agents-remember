"""Strict Codex app-server capability, thread, state, and interaction mapping."""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from agents_remember.errors import CodexAppServerError
from agents_remember.serving.codex_app_server_protocol import JsonObject, RequestId
from agents_remember.serving.harness_capabilities import EffortOption
from agents_remember.serving.harness_control_models import (
    AcceptanceState,
    ActivityState,
    NativeEvidenceFrame,
    PendingInteraction,
    PromptRequest,
    TerminalOutcome,
    TerminalResult,
    TranscriptEntry,
)

STABLE_SERVER_REQUESTS = frozenset(
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
        "mcpServer/elicitation/request",
    }
)
EXPERIMENTAL_SERVER_REQUESTS = frozenset({"item/tool/requestUserInput"})


@dataclass(frozen=True)
class CodexModelCapability:
    id: str
    model: str
    display_name: str
    description: str
    default_effort: str
    effort_options: tuple[EffortOption, ...]
    hidden: bool
    is_default: bool

    @property
    def supported_efforts(self) -> tuple[str, ...]:
        return tuple(option.key for option in self.effort_options)


@dataclass(frozen=True)
class CodexThreadEvidence:
    thread_id: str
    cli_version: str
    model: str
    model_provider: str
    cwd: str
    effective_effort: str
    status: JsonObject
    turns: tuple[JsonObject, ...]
    raw: JsonObject


@dataclass(frozen=True)
class CodexServerInteraction:
    rpc_id: RequestId
    method: str
    params: JsonObject
    pending: PendingInteraction


@dataclass
class SubmissionEvidence:
    request: PromptRequest
    state: Literal["queued", "accepted", "unknown", "completed", "rejected"]
    model: CodexModelCapability
    effort: str
    turn_id: str | None = None


class CodexSubmissionLedger:
    """Bounded correlation evidence; unknown and queued sends are never evicted."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._items: OrderedDict[str, SubmissionEvidence] = OrderedDict()

    def reserve(
        self,
        request: PromptRequest,
        *,
        model: CodexModelCapability,
        effort: str,
    ) -> SubmissionEvidence | None:
        if request.request_id in self._items:
            raise CodexAppServerError(f"duplicate Codex request id {request.request_id!r}")
        while len(self._items) >= self._limit:
            evictable = next(
                (
                    request_id
                    for request_id, evidence in self._items.items()
                    if evidence.state in {"completed", "rejected"}
                ),
                None,
            )
            if evictable is None:
                return None
            self._items.pop(evictable)
        evidence = SubmissionEvidence(
            request=request,
            state="queued",
            model=model,
            effort=effort,
        )
        self._items[request.request_id] = evidence
        return evidence

    def get(self, request_id: str) -> SubmissionEvidence | None:
        return self._items.get(request_id)

    def values(self) -> tuple[SubmissionEvidence, ...]:
        return tuple(self._items.values())


def validate_initialize_response(
    result: Mapping[str, object],
    *,
    client_name: str,
) -> tuple[str, JsonObject]:
    user_agent = required_text(result, "userAgent", context="initialize response")
    match = re.fullmatch(
        rf"{re.escape(client_name)}/(?P<version>\S+)(?: \S(?:.*\S)?)?",
        user_agent,
    )
    if match is None:
        raise CodexAppServerError(
            "Codex initialize response has incompatible userAgent; expected "
            f"{client_name}/<reported-version> with an optional diagnostic suffix, "
            f"received {user_agent!r}"
        )
    cli_version = match.group("version")
    codex_home = required_text(result, "codexHome", context="initialize response")
    platform_family = required_text(result, "platformFamily", context="initialize response")
    platform_os = required_text(result, "platformOs", context="initialize response")
    return cli_version, {
        "userAgent": user_agent,
        "cliVersion": cli_version,
        "codexHomePresent": bool(codex_home),
        "platformFamily": platform_family,
        "platformOs": platform_os,
    }


def parse_model_page(
    result: Mapping[str, object],
) -> tuple[tuple[CodexModelCapability, ...], str | None]:
    data = required_list(result, "data", context="model/list response")
    models: list[CodexModelCapability] = []
    for index, value in enumerate(data):
        model = required_object(value, context=f"model/list data[{index}]")
        supported_values = required_list(
            model,
            "supportedReasoningEfforts",
            context=f"model/list data[{index}]",
        )
        supported: list[EffortOption] = []
        for effort_index, raw_effort in enumerate(supported_values):
            option = required_object(
                raw_effort,
                context=f"model/list data[{index}].supportedReasoningEfforts[{effort_index}]",
            )
            supported.append(
                EffortOption(
                    key=required_text(
                        option,
                        "reasoningEffort",
                        context="reasoning effort option",
                    ),
                    display_name=required_text(
                        option,
                        "reasoningEffort",
                        context="reasoning effort option",
                    ),
                    description=required_text(
                        option,
                        "description",
                        context="reasoning effort option",
                    ),
                )
            )
        default_effort = required_text(
            model,
            "defaultReasoningEffort",
            context=f"model/list data[{index}]",
        )
        supported_keys = tuple(option.key for option in supported)
        if default_effort not in supported_keys:
            raise CodexAppServerError(
                f"Codex model {model.get('model')!r} advertises default effort "
                f"{default_effort!r} outside its supported menu {supported_keys!r}"
            )
        hidden = model.get("hidden")
        is_default = model.get("isDefault")
        if not isinstance(hidden, bool) or not isinstance(is_default, bool):
            raise CodexAppServerError("Codex model hidden/isDefault fields must be booleans")
        models.append(
            CodexModelCapability(
                id=required_text(model, "id", context=f"model/list data[{index}]"),
                model=required_text(model, "model", context=f"model/list data[{index}]"),
                display_name=required_text(
                    model, "displayName", context=f"model/list data[{index}]"
                ),
                description=required_text(
                    model, "description", context=f"model/list data[{index}]"
                ),
                default_effort=default_effort,
                effort_options=tuple(supported),
                hidden=hidden,
                is_default=is_default,
            )
        )
    cursor = result.get("nextCursor")
    if cursor is not None and not isinstance(cursor, str):
        raise CodexAppServerError("Codex model/list nextCursor must be text or null")
    return tuple(models), cursor


def select_model(
    models: Sequence[CodexModelCapability],
    desired_model: str | None,
) -> CodexModelCapability:
    if desired_model is None:
        matches = [model for model in models if model.is_default and not model.hidden]
        label = "the advertised default model"
    else:
        matches = [model for model in models if desired_model in {model.id, model.model}]
        label = repr(desired_model)
    if len(matches) != 1:
        advertised = ", ".join(sorted(model.model for model in models)) or "<none>"
        raise CodexAppServerError(
            f"Codex model selection {label} resolved to {len(matches)} models; "
            f"advertised models: {advertised}"
        )
    return matches[0]


def validate_reasoning_effort(model: CodexModelCapability, desired_effort: str) -> None:
    if desired_effort not in model.supported_efforts:
        advertised = ", ".join(model.supported_efforts) or "<none>"
        raise CodexAppServerError(
            f"Codex model {model.model!r} does not advertise desired reasoning effort "
            f"{desired_effort!r}; advertised options: {advertised}"
        )


def parse_thread_open_response(
    result: Mapping[str, object],
    *,
    method: str,
    desired_effort: str,
) -> CodexThreadEvidence:
    thread = required_object(result.get("thread"), context=f"{method} response.thread")
    effective_effort = required_text(result, "reasoningEffort", context=f"{method} response")
    if effective_effort != desired_effort:
        raise CodexAppServerError(
            f"Codex {method} echoed reasoning effort {effective_effort!r}; "
            f"desired effort was {desired_effort!r}"
        )
    turns_raw = required_list(thread, "turns", context=f"{method} response.thread")
    turns = tuple(
        required_object(turn, context=f"{method} response.thread.turns") for turn in turns_raw
    )
    status = required_object(thread.get("status"), context=f"{method} response.thread.status")
    activity_from_thread_status(status)
    return CodexThreadEvidence(
        thread_id=required_text(thread, "id", context=f"{method} response.thread"),
        cli_version=required_text(thread, "cliVersion", context=f"{method} response.thread"),
        model=required_text(result, "model", context=f"{method} response"),
        model_provider=required_text(result, "modelProvider", context=f"{method} response"),
        cwd=required_text(result, "cwd", context=f"{method} response"),
        effective_effort=effective_effort,
        status=status,
        turns=turns,
        raw=dict(result),
    )


def parse_turn(result: Mapping[str, object], *, context: str) -> JsonObject:
    turn = required_object(result.get("turn"), context=f"{context}.turn")
    required_text(turn, "id", context=f"{context}.turn")
    status = required_text(turn, "status", context=f"{context}.turn")
    if status not in {"completed", "interrupted", "failed", "inProgress"}:
        raise CodexAppServerError(f"{context}.turn has unsupported status {status!r}")
    required_list(turn, "items", context=f"{context}.turn")
    return thread_safe_object(turn)


def activity_from_thread_status(
    status: Mapping[str, object],
) -> tuple[ActivityState, AcceptanceState]:
    status_type = required_text(status, "type", context="thread status")
    if status_type == "idle":
        return "idle", "immediate"
    if status_type == "notLoaded":
        return "unknown", "rejected"
    if status_type == "systemError":
        return "unknown", "rejected"
    if status_type != "active":
        raise CodexAppServerError(f"unsupported Codex thread status type {status_type!r}")
    flags = required_list(status, "activeFlags", context="active thread status")
    if not all(flag in {"waitingOnApproval", "waitingOnUserInput"} for flag in flags):
        raise CodexAppServerError(f"unsupported Codex thread active flags {flags!r}")
    if flags:
        return "blocked", "queued"
    return "running", "queued"


def terminal_result(turn: Mapping[str, object], *, completed_at: str) -> TerminalResult:
    status = required_text(turn, "status", context="turn/completed turn")
    outcome: TerminalOutcome
    if status == "completed":
        outcome = "completed"
    elif status == "interrupted":
        outcome = "cancelled"
    elif status == "failed":
        outcome = "failed"
    else:
        raise CodexAppServerError(f"turn/completed carried non-terminal status {status!r}")
    error = turn.get("error")
    detail = None
    if status == "failed":
        error_object = required_object(error, context="failed turn error")
        detail = required_text(error_object, "message", context="failed turn error")
    return TerminalResult(
        outcome=outcome,
        completed_at=completed_at,
        detail=detail,
        raw={"codexTurnStatus": status},
    )


def transcript_from_item(
    item: Mapping[str, object],
    *,
    sequence: int,
    created_at: str,
    turn_id: str,
) -> TranscriptEntry | None:
    item_type = required_text(item, "type", context="Codex thread item")
    item_id = required_text(item, "id", context="Codex thread item")
    if item_type == "agentMessage":
        return TranscriptEntry(
            sequence=sequence,
            role="assistant",
            text=required_text(item, "text", context="agentMessage item"),
            created_at=created_at,
            vendor_correlation_id=turn_id,
            raw={"codexItemId": item_id, "codexItemType": item_type},
        )
    if item_type != "userMessage":
        return None
    client_id = item.get("clientId")
    if client_id is not None and not isinstance(client_id, str):
        raise CodexAppServerError("userMessage clientId must be text or null")
    return TranscriptEntry(
        sequence=sequence,
        role="user",
        text=user_message_text(item),
        created_at=created_at,
        request_id=client_id,
        vendor_correlation_id=turn_id,
        raw={"codexItemId": item_id, "codexItemType": item_type},
    )


def native_evidence_frames_from_thread(
    thread: Mapping[str, object],
) -> tuple[NativeEvidenceFrame, ...]:
    """Flatten one ``thread/read`` thread into typed native evidence frames, in stored order.

    Every item must carry a unique id and a type; duplicates or missing identity fail closed
    instead of manufacturing a cursor that could overlap or skip items across pages.
    """

    frames: list[NativeEvidenceFrame] = []
    seen: set[str] = set()
    turns = required_list(thread, "turns", context="thread/read response.thread")
    for raw_turn in turns:
        turn = required_object(raw_turn, context="thread/read turn")
        turn_id = required_text(turn, "id", context="thread/read turn")
        items = required_list(turn, "items", context="thread/read turn")
        for raw_item in items:
            item = required_object(raw_item, context="thread/read item")
            item_id = required_text(item, "id", context="Codex thread item")
            item_type = required_text(item, "type", context="Codex thread item")
            if item_id in seen:
                raise CodexAppServerError(
                    f"Codex thread/read repeated item id {item_id!r}; "
                    "native paging cannot continue without exact identity"
                )
            seen.add(item_id)
            frames.append(
                NativeEvidenceFrame(
                    native_id=item_id,
                    native_parent_id=turn_id,
                    native_type=item_type,
                    created_at=None,
                    raw=item,
                )
            )
    return tuple(frames)


def find_request_turn(
    thread: Mapping[str, object],
    *,
    request_id: str,
    request_text: str,
    known_turn_id: str | None,
) -> str | None:
    turns = required_list(thread, "turns", context="thread/read response.thread")
    for raw_turn in turns:
        turn = required_object(raw_turn, context="thread/read turn")
        turn_id = required_text(turn, "id", context="thread/read turn")
        items = required_list(turn, "items", context="thread/read turn")
        for raw_item in items:
            item = required_object(raw_item, context="thread/read item")
            if item.get("type") != "userMessage":
                continue
            if item.get("clientId") == request_id:
                return turn_id
            if known_turn_id == turn_id and user_message_text(item) == request_text:
                return turn_id
    return None


def parse_server_interaction(
    message: Mapping[str, object],
    *,
    created_at: str,
) -> CodexServerInteraction:
    method = required_text(message, "method", context="Codex server request")
    if method not in STABLE_SERVER_REQUESTS:
        qualifier = "experimental" if method in EXPERIMENTAL_SERVER_REQUESTS else "unsupported"
        raise CodexAppServerError(f"Codex server request {method!r} is {qualifier}")
    rpc_id = message.get("id")
    if not isinstance(rpc_id, (str, int)) or isinstance(rpc_id, bool):
        raise CodexAppServerError("Codex server request id must be text or an integer")
    params = required_object(message.get("params"), context=f"{method} params")
    interaction_id = f"codex:{method}:{rpc_id}"
    prompt, choices = interaction_prompt(method, params)
    return CodexServerInteraction(
        rpc_id=rpc_id,
        method=method,
        params=dict(params),
        pending=PendingInteraction(
            interaction_id=interaction_id,
            kind=method,
            prompt=prompt,
            created_at=created_at,
            choices=choices,
            raw={
                "codexRequestId": rpc_id,
                "threadId": params.get("threadId"),
                "turnId": params.get("turnId"),
                "itemId": params.get("itemId"),
            },
        ),
    )


def interaction_result(method: str, response: str, *, params: Mapping[str, object]) -> JsonObject:
    if method in {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    }:
        allowed = {"accept", "acceptForSession", "decline", "cancel"}
        if response not in allowed:
            raise CodexAppServerError(
                f"Codex approval response must be one of {', '.join(sorted(allowed))}"
            )
        return {"decision": response}
    if method == "mcpServer/elicitation/request":
        return mcp_elicitation_result(response, params=params)
    payload = parse_response_object(response, method=method)
    if method == "item/permissions/requestApproval":
        required_object(payload.get("permissions"), context="permissions approval response")
        scope = payload.get("scope", "turn")
        if scope not in {"turn", "session"}:
            raise CodexAppServerError("permissions approval scope must be 'turn' or 'session'")
        return payload
    raise CodexAppServerError(f"unsupported Codex interaction response method {method!r}")


def mcp_elicitation_result(response: str, *, params: Mapping[str, object]) -> JsonObject:
    if response in {"accept", "decline", "cancel"}:
        if response == "accept":
            if not mcp_elicitation_is_empty_form(params):
                raise CodexAppServerError(
                    "accepted non-empty MCP elicitation requires a structured JSON response"
                )
            return {"action": "accept", "content": {}}
        return {"action": response}
    payload = parse_response_object(response, method="mcpServer/elicitation/request")
    action = payload.get("action")
    if action not in {"accept", "decline", "cancel"}:
        raise CodexAppServerError("MCP elicitation action must be accept, decline, or cancel")
    if action == "accept" and "content" not in payload:
        raise CodexAppServerError("accepted MCP elicitation requires content")
    return payload


def interaction_prompt(method: str, params: Mapping[str, object]) -> tuple[str, tuple[str, ...]]:
    if method == "item/commandExecution/requestApproval":
        value = params.get("reason") or params.get("command") or "Codex command approval required"
        return str(value), ("accept", "acceptForSession", "decline", "cancel")
    if method == "item/fileChange/requestApproval":
        value = params.get("reason") or "Codex file-change approval required"
        return str(value), ("accept", "acceptForSession", "decline", "cancel")
    if method == "item/permissions/requestApproval":
        return "Codex requested additional permissions; respond with the structured JSON grant", ()
    prompt = required_text(params, "message", context="MCP elicitation request")
    if mcp_elicitation_is_empty_form(params):
        return prompt, ("accept", "decline", "cancel")
    return prompt, ()


def mcp_elicitation_is_empty_form(params: Mapping[str, object]) -> bool:
    """True only for Codex's action-only MCP approval form (content is exactly ``{}``)."""

    if params.get("mode") != "form":
        return False
    schema = params.get("requestedSchema")
    if not isinstance(schema, Mapping):
        return False
    properties = schema.get("properties")
    return isinstance(properties, Mapping) and not properties


def user_message_text(item: Mapping[str, object]) -> str:
    content = required_list(item, "content", context="userMessage item")
    parts: list[str] = []
    for value in content:
        entry = required_object(value, context="userMessage content")
        if entry.get("type") == "text":
            parts.append(required_text(entry, "text", context="userMessage text content"))
    return "\n".join(parts)


def iso_from_epoch(value: object, *, fallback: str) -> str:
    if value is None:
        return fallback
    if not isinstance(value, int) or isinstance(value, bool):
        raise CodexAppServerError("Codex completion timestamp must be integer seconds or null")
    return datetime.fromtimestamp(value, tz=UTC).isoformat()


def iso_from_millis(value: object, *, fallback: str) -> str:
    if value is None:
        return fallback
    if not isinstance(value, int) or isinstance(value, bool):
        raise CodexAppServerError("Codex item timestamp must be integer milliseconds or null")
    return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()


def parse_response_object(response: str, *, method: str) -> JsonObject:
    try:
        payload = json.loads(response)
    except json.JSONDecodeError as exc:
        raise CodexAppServerError(f"{method} requires a structured JSON response") from exc
    return required_object(payload, context=f"{method} response")


def required_object(value: object, *, context: str) -> JsonObject:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise CodexAppServerError(f"{context} must be an object")
    return dict(value)


def thread_safe_object(value: Mapping[str, object]) -> JsonObject:
    return dict(value)


def required_text(value: Mapping[str, object], key: str, *, context: str) -> str:
    text = value.get(key)
    if not isinstance(text, str) or not text:
        raise CodexAppServerError(f"{context} requires non-empty {key}")
    return text


def required_list(value: Mapping[str, object], key: str, *, context: str) -> list[object]:
    result = value.get(key)
    if not isinstance(result, list):
        raise CodexAppServerError(f"{context} requires array {key}")
    return result
