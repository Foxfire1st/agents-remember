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

from agents_remember.errors import (
    HarnessBridgeEpochMismatchError,
    HarnessControlError,
    HarnessInteractionNotPendingError,
    HarnessRequestConflictError,
    NativeHistoryLimitExceeded,
    NativeHistoryUnavailable,
)
from agents_remember.serving.harness_capabilities import (
    capability_snapshot_json,
    set_result_json,
)
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_models import (
    CONTROL_PROTOCOL_VERSION,
    MAX_NATIVE_EVIDENCE_PAGE,
    MAX_OPERATION_TIMELINE_PAGE,
    MAX_SUBMIT_ASSET_BYTES,
    MAX_SUBMIT_ASSETS,
    SUBMIT_ASSET_MIME_TYPES,
    AssetReference,
    ControlIdentity,
    ControlOperationKind,
    InteractionResponse,
    PromptRequest,
    ReconciliationState,
    ShutdownMode,
    SubmissionSource,
    evidence_page_json,
    interrupt_result_json,
    native_evidence_page_json,
    operation_timeline_json,
    read_asset_bytes,
    receipt_json,
    reconciliation_json,
    snapshot_json,
    submission_authority_json,
    submission_provenance_batch_json,
    submission_status_batch_json,
    transcript_entry_json,
    withdrawal_result_json,
)
from agents_remember.serving.harness_submission_authority import OperationResolution

# 64 KiB starved real sessions — a single conversation item carrying a large
# tool output exceeds it, and the reader-side cap then fails EVERY page fetch for that session
# regardless of the requested limit (the chats UI renders an empty history for exactly the heavy
# sessions worth inspecting). 16 MiB is a ceiling, not a target — memory per message stays
# bounded by actual payloads. The durable fix is the codex-path byte-budget + per-item clipping
# (read_native_page / arEvidenceContentTruncated) applied to the claude projection page path.
MAX_CONTROL_MESSAGE_BYTES = 16 * 1024 * 1024
MAX_TRANSCRIPT_PAGE = 500
MAX_EVIDENCE_PAGE = 500


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
            response = _error_response(exc)
        try:
            writer.write(json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n")
            await writer.drain()
        except (BrokenPipeError, ConnectionResetError):
            # A synchronous caller may time out after dispatch accepted the action.
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass

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
        if action in {"advertise", "set-model", "set-effort"}:
            return await self._dispatch_capability_action(action, payload)
        if action == "submit":
            return await self._submit(payload)
        if action == "submission-authority":
            return submission_authority_json(self.bridge.submission_authority())
        if action == "submission-status":
            return submission_status_batch_json(
                await self.bridge.submission_status(
                    _required_text(payload, "expectedBridgeEpoch"),
                    _required_text_list(payload, "requestIds", minimum=1, maximum=64),
                    cockpit_only=True,
                )
            )
        if action == "withdraw":
            return withdrawal_result_json(
                await self.bridge.withdraw_submission(
                    _required_text(payload, "expectedBridgeEpoch"),
                    _required_text(payload, "requestId"),
                    cockpit_only=True,
                )
            )
        if action == "respond":
            return await self._respond(payload)
        if action == "reconcile":
            return reconciliation_json(
                await self.bridge.reconcile(
                    _required_text(payload, "requestId"),
                    expected_bridge_epoch=_optional_text(payload, "expectedBridgeEpoch"),
                )
            )
        if action == "resolve":
            return await self._resolve(payload)
        if action == "resolve-operation":
            return await self._resolve_operation(payload)
        if action == "transcript":
            return self._transcript(payload)
        if action == "evidence":
            return self._evidence(payload)
        if action == "evidence-native-page":
            return await self._evidence_native_page(payload)
        if action == "submission-provenance":
            return await self._submission_provenance(payload)
        if action == "interrupt":
            return await self._interrupt(payload)
        if action == "operation-timeline":
            return await self._operation_timeline(payload)
        if action == "stop":
            return await self._stop(payload)
        raise HarnessControlError(f"unknown control action: {action}")

    async def _dispatch_capability_action(
        self, action: str, payload: Mapping[str, object]
    ) -> object:
        if action == "advertise":
            return capability_snapshot_json(self.bridge.advertise())
        if action == "set-model":
            return set_result_json(await self.bridge.set_model(_required_text(payload, "modelKey")))
        if action == "set-effort":
            return set_result_json(await self.bridge.set_effort(_required_text(payload, "effort")))
        raise HarnessControlError(f"unknown capability action: {action}")

    async def _submit(self, payload: Mapping[str, object]) -> dict[str, object]:
        source = payload.get("source")
        if source not in {"cockpit", "terminal", "durable"}:
            raise HarnessControlError("submission source must be cockpit, terminal, or durable")
        expected_bridge_epoch = _optional_text(payload, "expectedBridgeEpoch")
        if source == "cockpit" and expected_bridge_epoch is None:
            raise HarnessControlError("cockpit submission requires expectedBridgeEpoch")
        request_id = _required_text(payload, "requestId")
        assets = await self._submit_assets(payload, request_id)
        receipt = await self.bridge.submit(
            PromptRequest(
                request_id=request_id,
                source=cast(SubmissionSource, source),
                text=_required_text(payload, "text"),
                submitted_at=_required_text(payload, "submittedAt"),
                expected_bridge_epoch=expected_bridge_epoch,
                assets=assets,
            )
        )
        return receipt_json(receipt)

    async def _submit_assets(
        self, payload: Mapping[str, object], request_id: str
    ) -> tuple[AssetReference, ...]:
        """Validate, confine, and verify staged assets; the wire carries references only."""

        raw_assets = payload.get("assets")
        if raw_assets is None:
            return ()
        if not isinstance(raw_assets, list) or not 1 <= len(raw_assets) <= MAX_SUBMIT_ASSETS:
            raise HarnessControlError(
                f"submit assets requires 1..{MAX_SUBMIT_ASSETS} references when present"
            )
        assets_root = (self.endpoint.path.parent / "assets").resolve()
        refs: list[AssetReference] = []
        seen: set[str] = set()
        for raw in raw_assets:
            asset_id, mime_type, byte_size, sha256 = _submit_asset_schema(raw)
            if asset_id in seen:
                raise HarnessControlError("submit asset ids must be unique")
            seen.add(asset_id)
            refs.append(
                await self._verify_staged_asset(
                    assets_root, request_id, asset_id, mime_type, byte_size, sha256
                )
            )
        return tuple(refs)

    async def _verify_staged_asset(
        self,
        assets_root: Path,
        request_id: str,
        asset_id: str,
        mime_type: str,
        byte_size: int,
        sha256: str,
    ) -> AssetReference:
        candidate = _confined_asset_path(assets_root, request_id, asset_id)
        digest, size, _data = await asyncio.to_thread(read_asset_bytes, candidate)
        if size != byte_size:
            raise HarnessControlError("submit asset byte size does not match the staged file")
        if digest != sha256:
            raise HarnessControlError("submit asset digest does not match the staged file")
        return AssetReference(
            asset_id=asset_id,
            mime_type=mime_type,
            byte_size=byte_size,
            sha256=sha256,
            spool_path=candidate,
        )

    async def _interrupt(self, payload: Mapping[str, object]) -> dict[str, object]:
        return interrupt_result_json(
            await self.bridge.interrupt(
                _required_text(payload, "expectedBridgeEpoch"),
                turn_id=_optional_text(payload, "turnId"),
                expected_operation_id=_optional_text(payload, "expectedOperationId"),
            )
        )

    async def _operation_timeline(self, payload: Mapping[str, object]) -> dict[str, object]:
        limit = min(
            MAX_OPERATION_TIMELINE_PAGE,
            _optional_non_negative_int(payload, "limit", default=MAX_OPERATION_TIMELINE_PAGE),
        )
        return operation_timeline_json(
            await self.bridge.operation_timeline(
                _required_text(payload, "expectedBridgeEpoch"),
                after_sequence=_optional_non_negative_int(payload, "afterSequence", default=0),
                limit=max(1, limit),
            )
        )

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

    async def _resolve_operation(self, payload: Mapping[str, object]) -> dict[str, object]:
        operation_kind = payload.get("operationKind")
        if operation_kind not in {"prompt", "set-model", "set-effort"}:
            raise HarnessControlError("operationKind must identify a control operation")
        resolution = payload.get("resolution")
        if resolution not in {"applied", "not-applied"}:
            raise HarnessControlError("resolution must be applied or not-applied")
        await self.bridge.resolve_operation(
            _required_text(payload, "operationId"),
            cast(ControlOperationKind, operation_kind),
            resolution=cast(OperationResolution, resolution),
            detail=_required_text(payload, "detail"),
        )
        return {"resolved": True}

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

    def _evidence(self, payload: Mapping[str, object]) -> dict[str, object]:
        after = _optional_non_negative_int(payload, "afterSequence", default=0)
        limit = min(
            MAX_EVIDENCE_PAGE,
            _optional_non_negative_int(payload, "limit", default=MAX_EVIDENCE_PAGE),
        )
        return evidence_page_json(self.bridge.evidence(after_sequence=after, limit=max(1, limit)))

    async def _evidence_native_page(self, payload: Mapping[str, object]) -> dict[str, object]:
        limit = min(
            MAX_NATIVE_EVIDENCE_PAGE,
            _optional_non_negative_int(payload, "limit", default=MAX_NATIVE_EVIDENCE_PAGE),
        )
        return native_evidence_page_json(
            await self.bridge.native_page(
                cursor=_optional_text(payload, "cursor"),
                limit=max(1, limit),
                # Additive multiplexed-thread selector; absent = parent thread.
                thread_id=_optional_text(payload, "threadId"),
            )
        )

    async def _submission_provenance(self, payload: Mapping[str, object]) -> dict[str, object]:
        return submission_provenance_batch_json(
            await self.bridge.submission_provenance(
                _required_text(payload, "expectedBridgeEpoch"),
                _required_text_list(payload, "requestIds", minimum=1, maximum=64),
            )
        )

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
            _raise_control_response_error(raw)
        return raw.get("result")


def _required_text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise HarnessControlError(f"control payload requires non-empty {key}")
    return value


def _submit_asset_schema(raw: object) -> tuple[str, str, int, str]:
    """Validate one wire asset reference's shape; confinement/verification happen later."""

    if not isinstance(raw, dict):
        raise HarnessControlError("submit asset must be an object")
    asset_id = _required_text(raw, "assetId")
    mime_type = _required_text(raw, "mimeType")
    if mime_type not in SUBMIT_ASSET_MIME_TYPES:
        raise HarnessControlError(
            "submit asset MIME type must be one of " + ", ".join(sorted(SUBMIT_ASSET_MIME_TYPES))
        )
    byte_size = raw.get("byteSize")
    if (
        not isinstance(byte_size, int)
        or isinstance(byte_size, bool)
        or not 1 <= byte_size <= MAX_SUBMIT_ASSET_BYTES
    ):
        raise HarnessControlError(f"submit asset byteSize must be 1..{MAX_SUBMIT_ASSET_BYTES}")
    sha256 = _required_text(raw, "sha256")
    if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
        raise HarnessControlError("submit asset sha256 must be 64 lowercase hex characters")
    return asset_id, mime_type, byte_size, sha256


def _confined_asset_path(assets_root: Path, request_id: str, asset_id: str) -> Path:
    """Resolve the convention path and verify containment before any filesystem touch."""

    for component, label in ((request_id, "requestId"), (asset_id, "assetId")):
        if not component or len(component.encode("utf-8")) > 255:
            raise HarnessControlError(
                f"submit asset {label} must be non-empty and at most 255 bytes"
            )
        if component in {".", ".."} or "/" in component or "\\" in component:
            raise HarnessControlError(
                f"submit asset {label} must not contain path separators or dot segments"
            )
    try:
        candidate = (assets_root / request_id / asset_id).resolve()
    except ValueError as exc:
        raise HarnessControlError(f"submit asset path is invalid: {exc}") from exc
    if not candidate.is_relative_to(assets_root):
        raise HarnessControlError("submit asset path escapes the session asset spool")
    return candidate


def _optional_text(raw: Mapping[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise HarnessControlError(f"control payload {key} must be non-empty text")
    return value


def _required_text_list(
    raw: Mapping[str, object],
    key: str,
    *,
    minimum: int,
    maximum: int,
) -> tuple[str, ...]:
    value = raw.get(key)
    if (
        not isinstance(value, list)
        or not minimum <= len(value) <= maximum
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise HarnessControlError(
            f"control payload {key} requires {minimum}..{maximum} non-empty strings"
        )
    if len(set(value)) != len(value):
        raise HarnessControlError(f"control payload {key} must be unique")
    return tuple(value)


def _error_response(error: Exception) -> dict[str, object]:
    response: dict[str, object] = {"ok": False, "error": str(error)}
    if isinstance(error, HarnessBridgeEpochMismatchError):
        response.update(
            {
                "status": "bridge-epoch-mismatch",
                "expectedBridgeEpoch": error.expected,
                "actualBridgeEpoch": error.actual,
            }
        )
    elif isinstance(error, HarnessRequestConflictError):
        response["status"] = "request-id-conflict"
    elif isinstance(error, HarnessInteractionNotPendingError):
        response["status"] = "interaction-not-pending"
    elif isinstance(error, NativeHistoryLimitExceeded):
        response.update(
            {
                "status": "native-history-limit-exceeded",
                "code": error.code,
                "actualBytes": error.actual_bytes,
                "limitBytes": error.limit_bytes,
            }
        )
    elif isinstance(error, NativeHistoryUnavailable):
        response.update({"status": "native-history-unavailable", "code": error.code})
    return response


def _raise_control_response_error(raw: Mapping[str, object]) -> None:
    detail = str(raw.get("error") or "control request failed")
    status = raw.get("status")
    if status == "bridge-epoch-mismatch":
        raise HarnessBridgeEpochMismatchError(
            _required_text(raw, "expectedBridgeEpoch"),
            _required_text(raw, "actualBridgeEpoch"),
        )
    if status == "request-id-conflict":
        raise HarnessRequestConflictError(detail)
    if status == "interaction-not-pending":
        raise HarnessInteractionNotPendingError(detail)
    if status == "native-history-limit-exceeded":
        raise NativeHistoryLimitExceeded(
            detail,
            actual_bytes=_required_non_negative_int(raw, "actualBytes"),
            limit_bytes=_required_non_negative_int(raw, "limitBytes"),
        )
    if status == "native-history-unavailable":
        code = _required_text(raw, "code")
        raise NativeHistoryUnavailable(detail, code=code)
    raise HarnessControlError(detail)


def _optional_non_negative_int(raw: Mapping[str, object], key: str, *, default: int) -> int:
    value = raw.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise HarnessControlError(f"control payload {key} must be a non-negative integer")
    return value


def _required_non_negative_int(raw: Mapping[str, object], key: str) -> int:
    if key not in raw:
        raise HarnessControlError(f"control payload requires {key}")
    return _optional_non_negative_int(raw, key, default=0)
