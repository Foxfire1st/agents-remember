#!/usr/bin/env python3
"""Deterministic OpenAI-compatible chat-completions stub used as eve's model provider.

This is a *model provider*, not a harness and not a fake adapter. eve runs its real session
protocol, real tool loop, real compaction and real durable stream against it, so a fixture that
drives eve through this stub exercises the production path with a provider whose answers are
reproducible.

The plan is a JSON list; request N is answered by entry N. Each entry is one of:

* ``{"text": "..."}`` — an assistant message streamed in small deltas, ending ``finish_reason:
  stop``;
* ``{"toolCalls": [{"callId": ..., "name": ..., "arguments": {...}}]}`` — one or more streamed
  tool calls, ending ``finish_reason: tool_calls``;
* either may carry ``"delaySeconds": N`` to hold the response open, which is how a fixture gives a
  test time to cancel an in-flight turn.

Usage:
    python eve_fixture_model.py --plan plan.json --state state.json --port 4799
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_LOCK = threading.Lock()

DELTA_STEP = 7
ARGUMENT_STEP = 11


@dataclass(frozen=True)
class FixturePlan:
    """One server's scripted answers plus the request counter that walks them.

    Bound to the server rather than to module state so two fixture providers can run at once,
    which a concurrent-session fixture needs in order to keep the two scripts distinct.
    """

    turns: tuple[dict, ...]
    state_path: Path
    trace_path: Path | None = None

    def next_turn(self) -> tuple[int, dict] | None:
        with _LOCK:
            index = self.read_count()
            if index >= len(self.turns):
                return None
            turn = self.turns[index]
            self.state_path.write_text(str(index + 1), encoding="utf-8")
        return index, turn

    def read_count(self) -> int:
        try:
            return int(self.state_path.read_text(encoding="utf-8").strip() or "0")
        except FileNotFoundError:
            return 0

    def trace(self, index: int, request: dict) -> None:
        """Record one request. ``messages`` is the provider's own view of the effective prompt.

        The four original keys are unchanged, and ``messages`` was added beside them so a fixture can
        read the first effective prompt — the system block and the durable history a model would
        actually receive — instead of inferring it from the plan it wrote.
        """

        if self.trace_path is None:
            return
        with self.trace_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "requestIndex": index,
                        "model": request.get("model"),
                        "stream": bool(request.get("stream")),
                        "messageCount": len(request.get("messages") or []),
                        "messages": request.get("messages") or [],
                    }
                )
                + "\n"
            )


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def plan(self) -> FixturePlan:
        return self.server.plan  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: object) -> None:
        if self.server.verbose:  # type: ignore[attr-defined]
            super().log_message(fmt, *args)

    def do_GET(self) -> None:
        if self.path.rstrip("/").endswith("/models"):
            self._json(200, {"object": "list", "data": [{"id": "fixture-deterministic-1"}]})
            return
        self._json(404, {"error": {"message": f"no route {self.path}"}})

    def do_POST(self) -> None:
        request = self._read_request()
        if request is None:
            return
        selected = self.plan.next_turn()
        if selected is None:
            self._json(
                500,
                {
                    "error": {
                        "message": (f"fixture plan exhausted at request {self.plan.read_count()}"),
                        "type": "fixture_plan_exhausted",
                    }
                },
            )
            return
        index, turn = selected
        self.plan.trace(index, request)
        if turn.get("delaySeconds"):
            time.sleep(float(turn["delaySeconds"]))
        completion_id = f"chatcmpl-fixture-{index}"
        model = str(request.get("model") or "fixture-deterministic-1")
        chunks = _chunks_for(turn, completion_id, model)
        if request.get("stream"):
            self._stream(chunks)
            return
        self._json(200, _aggregate(turn, completion_id, model))

    def _read_request(self) -> dict | None:
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self._json(400, {"error": {"message": "malformed json"}})
            return None
        return payload if isinstance(payload, dict) else None

    def _stream(self, chunks: list[dict]) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "close")
        self.end_headers()
        try:
            for chunk in chunks:
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _chunks_for(turn: dict, completion_id: str, model: str) -> list[dict]:
    base = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
    }
    chunks = [
        {**base, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}
    ]
    if turn.get("toolCalls"):
        for index, call in enumerate(turn["toolCalls"]):
            arguments = json.dumps(call["arguments"])
            # The opening chunk carries the call identity and type, exactly as the provider
            # protocol requires; later chunks stream only the argument text.
            chunks.append(_tool_chunk(base, index, call["name"], "", call_id=call["callId"]))
            for offset in range(0, len(arguments), ARGUMENT_STEP):
                chunks.append(
                    _tool_chunk(base, index, None, arguments[offset : offset + ARGUMENT_STEP])
                )
        chunks.append(
            {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
        )
        return chunks
    text = turn.get("text", "")
    for offset in range(0, len(text), DELTA_STEP):
        chunks.append(
            {
                **base,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": text[offset : offset + DELTA_STEP]},
                        "finish_reason": None,
                    }
                ],
            }
        )
    chunks.append({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    return chunks


def _tool_chunk(
    base: dict,
    index: int,
    name: str | None,
    arguments: str,
    *,
    call_id: str | None = None,
) -> dict:
    function: dict[str, object] = {"arguments": arguments}
    if name is not None:
        function["name"] = name
    delta: dict[str, object] = {"index": index, "function": function}
    if call_id is not None:
        delta["id"] = call_id
        delta["type"] = "function"
    return {
        **base,
        "choices": [{"index": 0, "delta": {"tool_calls": [delta]}, "finish_reason": None}],
    }


def _aggregate(turn: dict, completion_id: str, model: str) -> dict:
    if turn.get("toolCalls"):
        message: dict[str, object] = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call["callId"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call["arguments"]),
                    },
                }
                for call in turn["toolCalls"]
            ],
        }
        finish = "tool_calls"
    else:
        message = {"role": "assistant", "content": turn.get("text", "")}
        finish = "stop"
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def serve(
    *,
    plan: list[dict],
    state_path: Path,
    port: int,
    host: str = "127.0.0.1",
    trace_path: Path | None = None,
) -> ThreadingHTTPServer:
    """Start the stub in-process and return the running server.

    In-process is what a fixture wants: the caller keeps the exact port, the exact plan, and the
    exact request trace without parsing a child process's output. The plan is attached to the
    returned server, so several providers may serve different scripts at the same time.
    """

    bound = FixturePlan(turns=tuple(plan), state_path=state_path, trace_path=trace_path)
    bound.state_path.write_text("0", encoding="utf-8")
    server = ThreadingHTTPServer((host, port), _Handler)
    server.plan = bound  # type: ignore[attr-defined]
    server.verbose = False  # type: ignore[attr-defined]
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--port", type=int, default=4799)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--trace")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    if not isinstance(plan, list):
        raise SystemExit("plan must be a JSON list of turns")
    server = serve(
        plan=plan,
        state_path=Path(args.state),
        port=args.port,
        host=args.host,
        trace_path=Path(args.trace) if args.trace else None,
    )
    server.verbose = args.verbose  # type: ignore[attr-defined]
    print(f"fixture model listening on http://{args.host}:{args.port}/v1", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
