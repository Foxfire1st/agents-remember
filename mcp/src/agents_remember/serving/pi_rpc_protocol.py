"""Strict Pi RPC JSONL framing, launch preservation, and schema parsing."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal, cast

from agents_remember.errors import HarnessControlError
from agents_remember.serving.harness_control_models import LaunchSpec

PI_RPC_PACKAGE = "@earendil-works/pi-coding-agent"
PI_RPC_PROTOCOL = "pi-rpc/jsonl"
PI_RPC_DIALOG_METHODS = frozenset({"select", "confirm", "input", "editor"})
PI_RPC_FIRE_AND_FORGET_METHODS = frozenset(
    {"notify", "setStatus", "setWidget", "setTitle", "set_editor_text"}
)
PiActivityTransition = tuple[Literal["running", "settling"], Literal["queued"]]
PI_RPC_ACTIVITY_EVENTS: Mapping[str, PiActivityTransition] = {
    "agent_start": ("running", "queued"),
    "agent_end": ("settling", "queued"),
    "auto_retry_start": ("settling", "queued"),
    "auto_retry_end": ("settling", "queued"),
    "compaction_start": ("settling", "queued"),
    "compaction_end": ("settling", "queued"),
}


@dataclass(frozen=True)
class PiSessionState:
    """The documented readiness and queue fields returned by ``get_state``."""

    session_id: str
    session_file: str | None
    is_streaming: bool
    is_compacting: bool
    pending_message_count: int
    thinking_level: str
    raw: Mapping[str, object]


@dataclass(frozen=True)
class PiEntries:
    """One durable append-order entry page and its current leaf cursor."""

    entries: tuple[Mapping[str, object], ...]
    leaf_id: str | None


class PiRpcJsonlDecoder:
    """Incrementally decode LF-only UTF-8 JSONL with a bounded frame buffer.

    Pi documents U+2028 and U+2029 as ordinary JSON string content, so only byte ``0x0a``
    delimits records. The frame cap is necessary at this subprocess boundary: without it, a
    malformed or wedged child can grow the adapter buffer without bound.
    """

    def __init__(self, *, max_frame_bytes: int = 1024 * 1024) -> None:
        if max_frame_bytes < 1:
            raise HarnessControlError("Pi RPC max_frame_bytes must be positive")
        self._max_frame_bytes = max_frame_bytes
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> tuple[Mapping[str, object], ...]:
        self._buffer.extend(chunk)
        frames: list[Mapping[str, object]] = []
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                self._require_bounded()
                return tuple(frames)
            if newline > self._max_frame_bytes:
                raise HarnessControlError(
                    f"Pi RPC frame exceeds {self._max_frame_bytes} bytes before its LF delimiter"
                )
            raw = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            frames.append(_decode_frame(raw[:-1] if raw.endswith(b"\r") else raw))

    def finish(self) -> tuple[Mapping[str, object], ...]:
        """Decode Pi's documented/source-compatible final unterminated record, if any."""

        if not self._buffer:
            return ()
        self._require_bounded()
        raw = bytes(self._buffer)
        self._buffer.clear()
        return (_decode_frame(raw[:-1] if raw.endswith(b"\r") else raw),)

    def _require_bounded(self) -> None:
        if len(self._buffer) > self._max_frame_bytes:
            raise HarnessControlError(
                f"Pi RPC frame exceeds {self._max_frame_bytes} bytes without an LF delimiter"
            )


def encode_pi_rpc_frame(value: Mapping[str, object]) -> bytes:
    """Encode one compact JSON object followed by exactly one LF delimiter."""

    try:
        payload = json.dumps(
            dict(value), ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise HarnessControlError(f"Pi RPC command is not JSON-serializable: {exc}") from exc
    return payload + b"\n"


def pi_rpc_launch(launch: LaunchSpec) -> LaunchSpec:
    """Add ``--mode rpc`` without changing any other argv, cwd, settings, or environment."""

    if not launch.argv:
        raise HarnessControlError("Pi RPC launch requires a non-empty argv")
    for index, argument in enumerate(launch.argv):
        if argument == "--mode":
            if index + 1 >= len(launch.argv) or launch.argv[index + 1] != "rpc":
                raise HarnessControlError("Pi launch already declares a non-RPC --mode")
            return launch
        if argument.startswith("--mode="):
            if argument != "--mode=rpc":
                raise HarnessControlError("Pi launch already declares a non-RPC --mode")
            return launch
    return replace(launch, argv=(launch.argv[0], "--mode", "rpc", *launch.argv[1:]))


def pi_rpc_resume_launch(launch: LaunchSpec, session_file: str) -> LaunchSpec:
    """Select the exact persisted Pi session while preserving all non-session configuration."""

    if not session_file:
        raise HarnessControlError("Pi RPC reconnect requires a non-empty session file")
    argv = list(pi_rpc_launch(launch).argv)
    kept = [argv[0]]
    index = 1
    value_flags = {"--session", "--session-id"}
    switches = {"--continue", "-c", "--resume", "-r", "--no-session"}
    while index < len(argv):
        argument = argv[index]
        if argument in value_flags:
            if index + 1 >= len(argv):
                raise HarnessControlError(f"Pi launch flag {argument} requires a value")
            index += 2
            continue
        if argument in switches:
            index += 1
            continue
        kept.append(argument)
        index += 1
    kept.extend(("--session", session_file))
    return replace(launch, argv=tuple(kept))


def parse_pi_response(
    frame: Mapping[str, object], *, request_id: str, command: str
) -> Mapping[str, object]:
    """Validate exact response correlation and command identity."""

    if frame.get("type") != "response":
        raise HarnessControlError(f"Pi RPC {command} returned a non-response frame")
    if frame.get("id") != request_id:
        raise HarnessControlError(f"Pi RPC {command} response correlation mismatch")
    if frame.get("command") != command:
        raise HarnessControlError(f"Pi RPC response command mismatch for {command}")
    success = frame.get("success")
    if not isinstance(success, bool):
        raise HarnessControlError(f"Pi RPC {command} response requires boolean success")
    return frame


def parse_pi_state(frame: Mapping[str, object]) -> PiSessionState:
    data = _required_mapping(frame, "data")
    pending = data.get("pendingMessageCount")
    if not isinstance(pending, int) or isinstance(pending, bool) or pending < 0:
        raise HarnessControlError("Pi RPC get_state requires non-negative pendingMessageCount")
    state = PiSessionState(
        session_id=_required_text(data, "sessionId"),
        session_file=_optional_text(data, "sessionFile"),
        is_streaming=_required_bool(data, "isStreaming"),
        is_compacting=_required_bool(data, "isCompacting"),
        pending_message_count=pending,
        thinking_level=_required_text(data, "thinkingLevel"),
        raw=dict(data),
    )
    return state


def parse_pi_entries(frame: Mapping[str, object]) -> PiEntries:
    data = _required_mapping(frame, "data")
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list):
        raise HarnessControlError("Pi RPC get_entries requires an entries array")
    entries: list[Mapping[str, object]] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise HarnessControlError("Pi RPC session entry must be an object")
        entries.append(cast(Mapping[str, object], raw))
    leaf_id = data.get("leafId")
    if leaf_id is not None and (not isinstance(leaf_id, str) or not leaf_id):
        raise HarnessControlError("Pi RPC get_entries leafId must be null or non-empty text")
    return PiEntries(entries=tuple(entries), leaf_id=cast(str | None, leaf_id))


def pi_entry_user_text(entry: Mapping[str, object]) -> str | None:
    """Return documented user-message text from one session entry, without heuristic scanning."""

    if entry.get("type") != "message":
        return None
    message = entry.get("message")
    if not isinstance(message, dict) or message.get("role") != "user":
        return None
    return _content_text(message.get("content"), accepted_types={"text"})


def pi_message_text(message: Mapping[str, object]) -> tuple[Literal["user", "assistant"], str]:
    role = message.get("role")
    if role not in {"user", "assistant"}:
        raise HarnessControlError("Pi RPC message_end requires a user or assistant role")
    text = _content_text(message.get("content"), accepted_types={"text", "thinking"})
    if text is None:
        raise HarnessControlError("Pi RPC message_end contains no documented text content")
    return cast(Literal["user", "assistant"], role), text


def extension_response(
    request: Mapping[str, object], *, interaction_id: str, response: str
) -> dict[str, object]:
    """Map the normalized string response onto Pi's method-specific response schema."""

    if request.get("id") != interaction_id:
        raise HarnessControlError("Pi extension UI response correlation mismatch")
    method = request.get("method")
    if method not in PI_RPC_DIALOG_METHODS:
        raise HarnessControlError("Pi extension UI response targets a non-dialog request")
    if response == "cancelled":
        return {"type": "extension_ui_response", "id": interaction_id, "cancelled": True}
    if method == "confirm":
        lowered = response.strip().lower()
        if lowered not in {"true", "false"}:
            raise HarnessControlError("Pi confirm response must be 'true', 'false', or 'cancelled'")
        return {
            "type": "extension_ui_response",
            "id": interaction_id,
            "confirmed": lowered == "true",
        }
    return {"type": "extension_ui_response", "id": interaction_id, "value": response}


def require_pi_success(response: Mapping[str, object], command: str) -> None:
    if response.get("success") is not True:
        raise HarnessControlError(f"Pi RPC {command} failed: {pi_response_error(response)}")


def pi_response_error(response: Mapping[str, object]) -> str:
    error = response.get("error")
    return error if isinstance(error, str) and error else "unknown Pi RPC command failure"


def required_pi_text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise HarnessControlError(f"Pi RPC event requires non-empty {key}")
    return value


def pi_queue_update_count(frame: Mapping[str, object]) -> int:
    steering = frame.get("steering")
    follow_up = frame.get("followUp")
    if not isinstance(steering, list) or not isinstance(follow_up, list):
        raise HarnessControlError("Pi queue_update requires steering and followUp arrays")
    return len(steering) + len(follow_up)


def pi_extension_display_text(frame: Mapping[str, object]) -> str:
    for key in ("message", "title", "text", "statusText"):
        value = frame.get(key)
        if isinstance(value, str) and value:
            return value
    return required_pi_text(frame, "method")


def non_negative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _decode_frame(raw: bytes) -> Mapping[str, object]:
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(text, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise HarnessControlError(f"malformed Pi RPC JSONL frame: {exc}") from exc
    if not isinstance(value, dict):
        raise HarnessControlError("malformed Pi RPC JSONL frame: top-level value must be an object")
    frame = cast(dict[object, object], value)
    if any(not isinstance(key, str) for key in frame):
        raise HarnessControlError("malformed Pi RPC JSONL frame: object keys must be strings")
    return cast(Mapping[str, object], value)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value}")


def _required_mapping(raw: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise HarnessControlError(f"Pi RPC response requires object {key}")
    return cast(Mapping[str, object], value)


def _required_text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise HarnessControlError(f"Pi RPC payload requires non-empty {key}")
    return value


def _optional_text(raw: Mapping[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise HarnessControlError(f"Pi RPC payload {key} must be non-empty text when present")
    return value


def _required_bool(raw: Mapping[str, object], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise HarnessControlError(f"Pi RPC payload requires boolean {key}")
    return value


def _content_text(content: object, *, accepted_types: set[str]) -> str | None:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts = [
        cast(str, item["text"])
        for item in content
        if isinstance(item, dict)
        and item.get("type") in accepted_types
        and isinstance(item.get("text"), str)
    ]
    return "".join(parts) if parts else None


def has_no_session(argv: Sequence[str]) -> bool:
    return "--no-session" in argv
