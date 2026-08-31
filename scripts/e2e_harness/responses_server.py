"""Deterministic Responses API that drives tools discovered by real Codex."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from agents_remember.mcp.public_surface import validate_dispatch_advertisement
from dispatch_sentinels import dispatch_rejection_sentinels
from responses_sse import assistant_sse, function_sse, tool_search_sse

AMBIENT_PROMPT = "ARSPAWN_E2E_AMBIENT_LAUNCH"
AMBIENT_REPEAT_PROMPT = "ARSPAWN_E2E_AMBIENT_IDEMPOTENCY"
RETIRE_MANAGER_PROMPT = "ARSPAWN_E2E_RETIRE_MANAGER"
REPLACE_MANAGER_PROMPT = "ARSPAWN_E2E_REPLACE_MANAGER"
WORKER_MID_PROMPT = "ARSPAWN_E2E_WORKER_MID_FLIGHT"
WORKER_POST_PROMPT = "ARSPAWN_E2E_WORKER_POST_REPLACEMENT"

PRE_REPLACEMENT = "ARSPAWN-E2E message before manager replacement"
MID_REPLACEMENT = "ARSPAWN-E2E message while manager seat is vacant"
POST_REPLACEMENT = "ARSPAWN-E2E message after manager replacement"


@dataclass(frozen=True)
class ScriptedAddresses:
    sprint: dict[str, str]
    master: dict[str, str]
    leaf: dict[str, str]
    architect_brief: str


@dataclass(frozen=True)
class DiscoveredTool:
    namespace: str | None
    name: str
    definition: dict[str, Any]

    @property
    def label(self) -> str:
        return f"{self.namespace}::{self.name}" if self.namespace is not None else self.name


@dataclass
class ScriptedResponses:
    addresses: ScriptedAddresses
    events: list[dict[str, object]] = field(default_factory=list)
    call_routes: dict[str, str] = field(default_factory=dict)
    completed_calls: set[str] = field(default_factory=set)
    sentinels_proven: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def response_for(self, body: dict[str, Any]) -> str:
        with self._lock:
            output_call = self._completed_call(body)
            if output_call is not None:
                route = self.call_routes[output_call]
                self.completed_calls.add(output_call)
                self.events.append(
                    {
                        "kind": "tool-result",
                        "route": route,
                        "callId": output_call,
                        "result": _function_output(body, output_call),
                    }
                )
                return assistant_sse(f"completed {route}")

            route = _route_for(body)
            if route in {
                "manager-a-message",
                "manager-b",
                "retirement-notification",
                "dead-upstream-notification",
                "state-signal-notification",
            }:
                self.events.append({"kind": "assistant-final", "route": route})
                return assistant_sse(f"completed {route}")
            tool_suffix, arguments = self._action(route)
            tool = _find_tool(body, tool_suffix)
            if tool is None:
                call_id = f"arspawn-search-{uuid.uuid4().hex}"
                self.events.append(
                    {
                        "kind": "tool-search-call",
                        "route": route,
                        "callId": call_id,
                        "query": tool_suffix,
                    }
                )
                return tool_search_sse(tool_suffix, call_id)
            schema_digest: str | None = None
            if tool_suffix == "dispatch_agent":
                schema_digest = validate_dispatch_advertisement(
                    name=tool.name,
                    description=tool.definition.get("description"),
                    input_schema=tool.definition.get("parameters"),
                )
                self._prove_dispatch_sentinels(tool)
            call_id = f"arspawn-{uuid.uuid4().hex}"
            self.call_routes[call_id] = route
            self.events.append(
                {
                    "kind": "tool-call",
                    "route": route,
                    "callId": call_id,
                    "discoveredTool": tool.label,
                    "schemaDigest": schema_digest,
                    "arguments": arguments,
                }
            )
            return function_sse(tool.namespace, tool.name, arguments, call_id)

    def _prove_dispatch_sentinels(self, tool: DiscoveredTool) -> None:
        if self.sentinels_proven:
            return
        self.events.extend(
            dispatch_rejection_sentinels(
                tool_name=tool.name,
                description=tool.definition.get("description"),
                input_schema=tool.definition.get("parameters"),
            )
        )
        self.sentinels_proven = True

    def _completed_call(self, body: dict[str, Any]) -> str | None:
        encoded = json.dumps(body, sort_keys=True)
        return (
            next(
                (
                    call_id
                    for call_id in self.call_routes
                    if call_id not in self.completed_calls and call_id in encoded
                ),
                None,
            )
            if "function_call_output" in encoded
            else None
        )

    def _action(self, route: str) -> tuple[str, dict[str, object]]:
        address = self.addresses
        ambient_dispatch = (
            "dispatch_agent",
            {
                "task_document_ref": address.sprint,
                "role": "architect",
                "brief": address.architect_brief,
            },
        )
        actions: dict[str, tuple[str, dict[str, object]]] = {
            "ambient-launcher": ambient_dispatch,
            "ambient-repeat": ambient_dispatch,
            "architect": (
                "dispatch_agent",
                {
                    "task_document_ref": address.sprint,
                    "role": "orchestrator",
                    "brief": "ROLE BRIEF — orchestrator\n\nDispatch the fixture manager.",
                },
            ),
            "orchestrator-initial": (
                "dispatch_agent",
                {
                    "task_document_ref": address.master,
                    "role": "manager",
                    "brief": "ROLE BRIEF — manager A\n\nDispatch the fixture worker.",
                },
            ),
            "manager-a": (
                "dispatch_agent",
                {
                    "task_document_ref": address.leaf,
                    "role": "worker",
                    "brief": "ROLE BRIEF — worker\n\nPost the fixture parent message.",
                },
            ),
            "worker-initial": ("message_parent", _message_arguments(PRE_REPLACEMENT)),
            "orchestrator-retire": (
                "retire_child",
                {
                    "task_document_ref": address.master,
                    "role": "manager",
                    "reason": "ARSPAWN E2E replacement boundary",
                },
            ),
            "worker-mid": ("message_parent", _message_arguments(MID_REPLACEMENT)),
            "orchestrator-replace": (
                "dispatch_agent",
                {
                    "task_document_ref": address.master,
                    "role": "manager",
                    "brief": "ROLE BRIEF — manager B\n\nReplacement fixture manager.",
                },
            ),
            "worker-post": ("message_parent", _message_arguments(POST_REPLACEMENT)),
        }
        try:
            return actions[route]
        except KeyError as exc:
            raise RuntimeError(f"no scripted action for route {route!r}") from exc


class ResponsesServer:
    def __init__(self, script: ScriptedResponses) -> None:
        handler = _handler(script)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        address = self._server.server_address
        host, port = str(address[0]), int(address[1])
        return f"http://{host}:{port}/v1"

    def __enter__(self) -> ResponsesServer:
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _handler(script: ScriptedResponses) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/v1/responses":
                self.send_error(404)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(400, "invalid request size")
                return
            if size <= 0 or size > 8 * 1024 * 1024:
                self.send_error(400, "invalid request size")
                return
            body: object = None
            try:
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("request is not an object")
                response = script.response_for(body)
            except Exception as exc:  # fail loudly to the real client
                script.events.append(
                    {
                        "kind": "server-error",
                        "error": repr(exc),
                        "request": _request_summary(body) if isinstance(body, dict) else None,
                    }
                )
                encoded = json.dumps({"error": {"message": str(exc)}}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return
            encoded = response.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return Handler


def _route_for(body: dict[str, Any]) -> str:
    text = _latest_user_text(body)
    ordered = (
        (AMBIENT_REPEAT_PROMPT, "ambient-repeat"),
        (RETIRE_MANAGER_PROMPT, "orchestrator-retire"),
        (REPLACE_MANAGER_PROMPT, "orchestrator-replace"),
        (WORKER_MID_PROMPT, "worker-mid"),
        (WORKER_POST_PROMPT, "worker-post"),
        (PRE_REPLACEMENT, "manager-a-message"),
        (AMBIENT_PROMPT, "ambient-launcher"),
        ("ROLE BRIEF — architect", "architect"),
        ("ROLE BRIEF — orchestrator", "orchestrator-initial"),
        ("ROLE BRIEF — manager A", "manager-a"),
        ("ROLE BRIEF — manager B", "manager-b"),
        ("ROLE BRIEF — worker", "worker-initial"),
        ("Stranded inbox rows after retiring ", "retirement-notification"),
        ("Dead-upstream (R4/P-6):", "dead-upstream-notification"),
        ("Agent notifier observed state-signal:", "state-signal-notification"),
    )
    for marker, route in ordered:
        if marker in text:
            return route
    raise RuntimeError("Responses request contains no recognized ARSPAWN E2E prompt")


def _latest_user_text(body: dict[str, Any]) -> str:
    """Return only the current turn's user text, excluding retained thread history."""

    inputs = body.get("input")
    if not isinstance(inputs, list):
        raise RuntimeError("Responses request input is not a list")
    for item in reversed(inputs):
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        return _text_content(item.get("content"))
    raise RuntimeError("Responses request has no user message")


def _text_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [
            str(part["text"])
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        if texts:
            return "\n".join(texts)
    raise RuntimeError("latest Responses user message has no text content")


def _find_tool(body: dict[str, Any], suffix: str) -> DiscoveredTool | None:
    direct_tools = body.get("tools")
    if not isinstance(direct_tools, list):
        raise RuntimeError("Responses request did not advertise tools")
    tools = [*direct_tools, *_tool_search_results(body)]
    matches = _matching_tools(tools, suffix)
    if len(matches) == 1:
        return matches[0]
    if not matches and not _has_completed_tool_search(body, suffix):
        _require_tool_search(direct_tools)
        return None
    raise RuntimeError(
        f"expected one discovered {suffix!r} tool, found {matches!r}; "
        f"advertised inventory={_tool_inventory(tools)!r}"
    )


def _matching_tools(tools: list[object], suffix: str) -> list[DiscoveredTool]:
    return [
        DiscoveredTool(namespace, str(tool["name"]), tool)
        for namespace, tool in _iter_function_tools(tools)
        if _tool_matches(namespace, tool, suffix)
    ]


def _tool_matches(namespace: str | None, tool: dict[str, Any], suffix: str) -> bool:
    name = tool.get("name")
    if not isinstance(name, str):
        return False
    return (
        name == suffix
        or name.endswith(f"__{suffix}")
        or f"{namespace or ''}{name}".endswith(f"__{suffix}")
    )


def _tool_inventory(tools: list[object]) -> list[dict[str, object]]:
    return [
        {
            "namespace": namespace,
            "type": tool.get("type"),
            "name": tool.get("name"),
        }
        for namespace, tool in _iter_function_tools(tools)
    ]


def _tool_search_results(body: dict[str, Any]) -> list[object]:
    inputs = body.get("input")
    if not isinstance(inputs, list):
        return []
    results: list[object] = []
    for item in inputs:
        if not isinstance(item, dict) or item.get("type") != "tool_search_output":
            continue
        tools = item.get("tools")
        if isinstance(tools, list):
            results.extend(tools)
    return results


def _has_completed_tool_search(body: dict[str, Any], suffix: str) -> bool:
    """Return whether this exact tool query already received a client result.

    Codex retains earlier tool-search items in later turns. A generic "some search output
    exists" check therefore mistakes the prior ``dispatch_agent`` result for proof that a
    later ``retire_child`` search ran. Correlate query and output by the response call id.
    """

    inputs = body.get("input")
    if not isinstance(inputs, list):
        return False
    query_call_ids = _query_call_ids(inputs, suffix)
    return any(_is_matching_tool_search_output(item, query_call_ids) for item in inputs)


def _query_call_ids(inputs: list[object], suffix: str) -> set[str]:
    call_ids: set[str] = set()
    for item in inputs:
        if not isinstance(item, dict) or item.get("type") != "tool_search_call":
            continue
        arguments = item.get("arguments")
        call_id = item.get("call_id")
        if (
            isinstance(arguments, dict)
            and arguments.get("query") == suffix
            and isinstance(call_id, str)
        ):
            call_ids.add(call_id)
    return call_ids


def _is_matching_tool_search_output(item: object, query_call_ids: set[str]) -> bool:
    return bool(
        isinstance(item, dict)
        and item.get("type") == "tool_search_output"
        and item.get("call_id") in query_call_ids
    )


def _require_tool_search(tools: list[object]) -> None:
    matches = [
        tool for tool in tools if isinstance(tool, dict) and tool.get("type") == "tool_search"
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "current Codex deferred the requested MCP tool without advertising exactly "
            f"one tool_search entry; found {len(matches)}"
        )


def _iter_function_tools(
    tools: list[object],
) -> list[tuple[str | None, dict[str, Any]]]:
    discovered: list[tuple[str | None, dict[str, Any]]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") != "namespace":
            discovered.append((None, tool))
            continue
        namespace = tool.get("name")
        nested = tool.get("tools")
        if not isinstance(namespace, str) or not isinstance(nested, list):
            continue
        discovered.extend((namespace, child) for child in nested if isinstance(child, dict))
    return discovered


def _request_summary(body: dict[str, Any]) -> dict[str, object]:
    """Bound diagnostic evidence without copying prompts or full tool schemas."""

    tools = body.get("tools")
    inputs = body.get("input")
    top_level_tools = tools if isinstance(tools, list) else []
    input_items = inputs if isinstance(inputs, list) else []
    searchable_tools = (
        [*top_level_tools, *_tool_search_results(body)] if isinstance(tools, list) else []
    )
    return {
        "keys": sorted(body),
        "model": body.get("model"),
        "toolsType": type(tools).__name__,
        "topLevelToolCount": len(top_level_tools) if isinstance(tools, list) else None,
        "functionToolCount": (
            len(_iter_function_tools(top_level_tools)) if isinstance(tools, list) else None
        ),
        "relevantToolNames": _relevant_tool_names(searchable_tools),
        "inputType": type(inputs).__name__,
        "inputCount": len(input_items) if isinstance(inputs, list) else None,
        "toolSearchOutputCount": (
            _tool_search_output_count(input_items) if isinstance(inputs, list) else None
        ),
    }


def _relevant_tool_names(tools: list[object]) -> list[str]:
    return [
        f"{namespace or ''}{tool['name']}"
        for namespace, tool in _iter_function_tools(tools)
        if _is_relevant_tool(namespace, tool)
    ]


def _is_relevant_tool(namespace: str | None, tool: dict[str, Any]) -> bool:
    name = tool.get("name")
    if not isinstance(name, str):
        return False
    qualified = f"{namespace or ''}{name}"
    return any(
        candidate == name or name.endswith(f"__{candidate}") or qualified.endswith(f"__{candidate}")
        for candidate in ("dispatch_agent", "message_parent", "retire_child")
    )


def _tool_search_output_count(inputs: list[object]) -> int:
    return sum(
        1 for item in inputs if isinstance(item, dict) and item.get("type") == "tool_search_output"
    )


def _message_arguments(response: str) -> dict[str, object]:
    return {
        "ask": "ARSPAWN E2E structural delivery",
        "response": response,
        "message_kind": "message",
    }


def _function_output(body: dict[str, Any], call_id: str) -> object:
    inputs = body.get("input")
    if not isinstance(inputs, list):
        return None
    return next(
        (
            item.get("output")
            for item in inputs
            if isinstance(item, dict)
            and item.get("type") == "function_call_output"
            and item.get("call_id") == call_id
        ),
        None,
    )
