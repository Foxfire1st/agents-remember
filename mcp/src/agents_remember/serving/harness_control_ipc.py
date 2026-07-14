"""User-private Unix-socket IPC for one exact hosted harness control bridge."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from agents_remember.errors import HarnessControlError
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_models import (
    CONTROL_PROTOCOL_VERSION,
    ControlIdentity,
    InteractionResponse,
    PromptRequest,
    ReconciliationState,
    ShutdownMode,
    SubmissionSource,
    receipt_json,
    reconciliation_json,
    snapshot_json,
    transcript_entry_json,
)

MAX_CONTROL_MESSAGE_BYTES = 64 * 1024
MAX_TRANSCRIPT_PAGE = 500


@dataclass(frozen=True)
class LocalControlEndpoint:
    """A deterministic private socket path bound to the full exact-session identity."""

    path: Path
    identity: ControlIdentity

    @classmethod
    def for_session(cls, root: Path, identity: ControlIdentity) -> LocalControlEndpoint:
        digest = hashlib.sha256(
            json.dumps(identity.to_json(), sort_keys=True).encode("utf-8")
        ).hexdigest()[:24]
        path = root / f"{digest}.sock"
        if len(os.fsencode(path)) > 103:
            raise HarnessControlError("control socket path exceeds the Unix-domain path limit")
        return cls(path=path, identity=identity)

    def prepare(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        if not self.path.exists() and not self.path.is_symlink():
            return
        mode = self.path.lstat().st_mode
        if not stat.S_ISSOCK(mode):
            raise HarnessControlError("refusing to replace a non-socket control endpoint")
        self.path.unlink()


class HarnessControlServer:
    """One-request-per-connection JSON IPC server for one bridge identity."""

    def __init__(self, endpoint: LocalControlEndpoint, bridge: HarnessControlBridge) -> None:
        if endpoint.identity != bridge.identity:
            raise HarnessControlError("control endpoint identity does not match the bridge")
        self.endpoint = endpoint
        self.bridge = bridge
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        if self._server is not None:
            raise HarnessControlError("control IPC server is already started")
        self.endpoint.prepare()
        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=self.endpoint.path,
            limit=MAX_CONTROL_MESSAGE_BYTES,
        )
        self.endpoint.path.chmod(0o600)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self.endpoint.path.unlink(missing_ok=True)

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        response: dict[str, object]
        try:
            line = await reader.readline()
            if not line:
                raise HarnessControlError("empty control request")
            if len(line) > MAX_CONTROL_MESSAGE_BYTES:
                raise HarnessControlError("control request exceeds the message limit")
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise HarnessControlError("control request must be a JSON object")
            result = await self._dispatch(raw)
            response = {"ok": True, "result": result}
        except (HarnessControlError, KeyError, TypeError, ValueError) as exc:
            response = {"ok": False, "error": str(exc)}
        writer.write(json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def _dispatch(self, request: Mapping[str, object]) -> object:
        if request.get("protocol") != CONTROL_PROTOCOL_VERSION:
            raise HarnessControlError("control request protocol version mismatch")
        raw_identity = request.get("identity")
        if not isinstance(raw_identity, dict):
            raise HarnessControlError("control request requires exact identity")
        if ControlIdentity.from_json(raw_identity) != self.endpoint.identity:
            raise HarnessControlError("control request identity does not match the endpoint")
        action = _required_text(request, "action")
        payload = request.get("payload", {})
        if not isinstance(payload, dict):
            raise HarnessControlError("control request payload must be an object")
        return await self._dispatch_action(action, payload)

    async def _dispatch_action(self, action: str, payload: Mapping[str, object]) -> object:
        if action in {"handshake", "snapshot"}:
            return {
                "protocol": CONTROL_PROTOCOL_VERSION,
                "snapshot": snapshot_json(self.bridge.snapshot()),
            }
        if action == "submit":
            return await self._submit(payload)
        if action == "respond":
            return await self._respond(payload)
        if action == "reconcile":
            return reconciliation_json(
                await self.bridge.reconcile(_required_text(payload, "requestId"))
            )
        if action == "resolve":
            return await self._resolve(payload)
        if action == "transcript":
            return self._transcript(payload)
        if action == "stop":
            return await self._stop(payload)
        raise HarnessControlError(f"unknown control action: {action}")

    async def _submit(self, payload: Mapping[str, object]) -> dict[str, object]:
        source = payload.get("source")
        if source not in {"terminal", "durable"}:
            raise HarnessControlError("submission source must be terminal or durable")
        receipt = await self.bridge.submit(
            PromptRequest(
                request_id=_required_text(payload, "requestId"),
                source=cast(SubmissionSource, source),
                text=_required_text(payload, "text"),
                submitted_at=_required_text(payload, "submittedAt"),
            )
        )
        return receipt_json(receipt)

    async def _respond(self, payload: Mapping[str, object]) -> dict[str, object]:
        snapshot = await self.bridge.respond(
            InteractionResponse(
                interaction_id=_required_text(payload, "interactionId"),
                response=_required_text(payload, "response"),
                responded_at=_required_text(payload, "respondedAt"),
            )
        )
        return snapshot_json(snapshot)

    async def _resolve(self, payload: Mapping[str, object]) -> dict[str, object]:
        state = payload.get("state")
        if state not in {"accepted", "rejected"}:
            raise HarnessControlError("resolution state must be accepted or rejected")
        result = await self.bridge.resolve_unknown(
            _required_text(payload, "requestId"),
            state=cast(ReconciliationState, state),
            detail=_required_text(payload, "detail"),
        )
        return reconciliation_json(result)

    def _transcript(self, payload: Mapping[str, object]) -> dict[str, object]:
        after = _optional_non_negative_int(payload, "afterSequence", default=0)
        limit = min(
            MAX_TRANSCRIPT_PAGE,
            _optional_non_negative_int(payload, "limit", default=MAX_TRANSCRIPT_PAGE),
        )
        return {
            "entries": [
                transcript_entry_json(entry)
                for entry in self.bridge.transcript(after_sequence=after, limit=max(1, limit))
            ]
        }

    async def _stop(self, payload: Mapping[str, object]) -> dict[str, object]:
        mode = payload.get("mode", "graceful")
        if mode not in {"graceful", "forced"}:
            raise HarnessControlError("shutdown mode must be graceful or forced")
        await self.bridge.stop(cast(ShutdownMode, mode))
        return {"stopped": True, "mode": mode}


class HarnessControlClient:
    """Exact-identity client used by a hosted terminal/input surface."""

    def __init__(self, endpoint: LocalControlEndpoint) -> None:
        self.endpoint = endpoint

    async def request(
        self,
        action: str,
        payload: Mapping[str, object] | None = None,
    ) -> object:
        reader, writer = await asyncio.open_unix_connection(
            self.endpoint.path, limit=MAX_CONTROL_MESSAGE_BYTES
        )
        request = {
            "protocol": CONTROL_PROTOCOL_VERSION,
            "identity": self.endpoint.identity.to_json(),
            "action": action,
            "payload": dict(payload or {}),
        }
        writer.write(json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n")
        await writer.drain()
        line = await reader.readline()
        writer.close()
        await writer.wait_closed()
        raw: Any = json.loads(line)
        if not isinstance(raw, dict):
            raise HarnessControlError("control response must be a JSON object")
        if raw.get("ok") is not True:
            raise HarnessControlError(str(raw.get("error") or "control request failed"))
        return raw.get("result")


def _required_text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise HarnessControlError(f"control payload requires non-empty {key}")
    return value


def _optional_non_negative_int(raw: Mapping[str, object], key: str, *, default: int) -> int:
    value = raw.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise HarnessControlError(f"control payload {key} must be a non-negative integer")
    return value
