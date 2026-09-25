"""Durable request-addressed Orca execution receipts and public projections."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from agents_remember.application.orca_task_context import (
    LEAF_ROLES,
    TASKLESS_ROLES,
    OrcaRoleContext,
    selection_binding,
)
from agents_remember.cli.orca_runtime import (
    OrcaRuntimeFailure,
)
from agents_remember.cli.orca_runtime import (
    digest as _digest,
)
from agents_remember.cli.orca_runtime import (
    runtime_call as _runtime_call,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.orca_launcher import (
    OrcaDispatchRequest,
    OrcaResultRequest,
    OrcaSelection,
)
from agents_remember.tasks.document_refs import TaskDocumentTopology


@dataclass(frozen=True, slots=True)
class _TasklessLegacyMigration:
    config: McpRuntimeConfig
    selection: OrcaSelection
    legacy: Path
    expected_selection: dict[str, Any]
    seen_ids: set[str]


def _execute_prepared_launch(path: Path, receipt: dict[str, Any]) -> JSONResponse:
    launch_request = receipt.get("replayRequest")
    if not isinstance(launch_request, dict):
        raise HTTPException(
            status_code=409,
            detail="The unresolved launch has no retained replay payload; reconcile the native runtime before retrying.",
        )
    try:
        result = _runtime_call("launch-replay", {"request": launch_request})
    except OrcaRuntimeFailure as error:
        rejected = error.code in {
            "agent_launch_replay_unsupported",
            "agent_session_operation_conflict",
            "invalid_argument",
            "worktree_create_collision",
        }
        receipt.update(
            status="rejected" if rejected else "unknown",
            detail=(
                f"Orca rejected the prepared launch before creating a session ({error.code})."
                if rejected
                else f"Orca launch outcome is unresolved ({error.code}); retry this same request to replay its saved operationId."
            ),
            updatedAt=_now_iso(),
        )
        if rejected:
            receipt.pop("replayRequest", None)
        _write_receipt(path, receipt)
        return JSONResponse(
            _public_execution(receipt),
            status_code=502 if rejected else 202,
        )

    prompt = _prompt_reference(result.get("prompt"))
    if prompt is not None:
        receipt["prompt"] = prompt
    outcome = result.get("outcome")
    if not isinstance(outcome, dict) or not isinstance(result.get("worktreeId"), str):
        receipt.update(
            status="unknown",
            detail="Orca returned no confirmed native session identity; retry this same request to replay its saved operationId.",
            updatedAt=_now_iso(),
        )
        _write_receipt(path, receipt)
        return JSONResponse(_public_execution(receipt), status_code=202)
    receipt["execution"] = _execution_reference(outcome, result["worktreeId"])
    if not receipt["execution"]:
        receipt.update(
            status="unknown",
            detail="Orca returned an unrecognized native session surface; retry this same request to replay its saved operationId.",
            updatedAt=_now_iso(),
        )
        _write_receipt(path, receipt)
        return JSONResponse(_public_execution(receipt), status_code=202)
    if isinstance(result.get("warning"), str):
        receipt["warning"] = result["warning"][:1000]
    receipt.update(
        status="running",
        detail="Orca accepted the native role session; AR acceptance remains with its owner.",
        updatedAt=_now_iso(),
    )
    receipt.pop("replayRequest", None)
    _write_receipt(path, receipt)
    return JSONResponse(_public_execution(receipt))


def _receipt_path(
    config: McpRuntimeConfig,
    selection: OrcaSelection,
    request_id: uuid.UUID | None = None,
) -> Path:
    if selection.role in TASKLESS_ROLES:
        if request_id is None:
            raise ValueError("A taskless Orca receipt requires its durable requestId.")
        return _taskless_session_directory(config, selection.role) / f"{request_id}.json"
    return _legacy_receipt_path(config, selection)


def _legacy_receipt_path(config: McpRuntimeConfig, selection: OrcaSelection) -> Path:
    topology = TaskDocumentTopology(config.coordination_root)
    role = selection.role
    ref = (
        selection.task_document_ref
        if role in LEAF_ROLES
        else selection.master_document_ref
        if role == "manager"
        else selection.sprint_document_ref
        if role == "orchestrator"
        else None
    )
    key = _digest(selection_binding(selection))[:24]
    if ref:
        parent = topology.path_for_ref(ref).parent
        return parent / "notes" / "reports" / "orca-native-executions" / f"{role}-{key}.json"
    return (
        config.coordination_root
        / "notes"
        / "reports"
        / "orca-native-executions"
        / f"{role}-{key}.json"
    )


def _taskless_session_directory(config: McpRuntimeConfig, role: str) -> Path:
    if role not in TASKLESS_ROLES:
        raise ValueError("Per-request Orca receipts are reserved for taskless project roles.")
    return (
        config.coordination_root
        / "notes"
        / "reports"
        / "orca-native-executions"
        / role
        / "sessions"
    )


def _migrate_taskless_legacy_receipt(config: McpRuntimeConfig, selection: OrcaSelection) -> None:
    """Move the bounded old taskless receipt set to request-ID addresses once."""
    if selection.role not in TASKLESS_ROLES:
        return
    legacy = _legacy_receipt_path(config, selection)
    sources = _legacy_taskless_receipt_sources(legacy)
    expected_selection = selection_binding(selection)
    pending: list[tuple[Path, Path]] = []
    seen_ids: set[str] = set()
    migration = _TasklessLegacyMigration(
        config=config,
        selection=selection,
        legacy=legacy,
        expected_selection=expected_selection,
        seen_ids=seen_ids,
    )
    for source in sources:
        target = _taskless_legacy_migration_target(migration, source)
        if target is not None:
            pending.append((source, target))
    _move_taskless_legacy_receipts(pending)


def _legacy_taskless_receipt_sources(legacy: Path) -> list[Path]:
    sources = [legacy]
    history = legacy.parent / "history"
    try:
        history_mode = history.lstat().st_mode
    except FileNotFoundError:
        return sources
    except OSError as error:
        raise HTTPException(
            status_code=409,
            detail="The legacy taskless execution history cannot be inspected safely.",
        ) from error
    if not stat.S_ISDIR(history_mode):
        raise HTTPException(
            status_code=409, detail="The legacy taskless execution history is not a safe directory."
        )
    sources.extend(sorted(history.glob("*.json")))
    return sources


def _taskless_legacy_migration_target(
    migration: _TasklessLegacyMigration,
    source: Path,
) -> Path | None:
    receipt = _read_receipt(source)
    if receipt is None:
        return None
    selection_matches = (
        receipt.get("role") == migration.selection.role
        and receipt.get("selection") == migration.expected_selection
    )
    if source != migration.legacy and not selection_matches:
        return None
    if not selection_matches:
        raise HTTPException(
            status_code=409, detail="A legacy taskless receipt does not match this role selection."
        )
    try:
        request_id = uuid.UUID(str(receipt.get("requestId")))
    except (ValueError, TypeError, AttributeError) as error:
        raise HTTPException(
            status_code=409, detail="A legacy taskless receipt has no valid requestId."
        ) from error
    request_key = str(request_id)
    if request_key in migration.seen_ids:
        raise HTTPException(
            status_code=409,
            detail="Legacy taskless receipts collide on requestId; no records were moved.",
        )
    migration.seen_ids.add(request_key)
    target = _receipt_path(migration.config, migration.selection, request_id)
    try:
        target.lstat()
    except FileNotFoundError:
        return target
    except OSError as error:
        raise HTTPException(
            status_code=409,
            detail="A request-addressed taskless receipt cannot be inspected safely.",
        ) from error
    raise HTTPException(
        status_code=409,
        detail="A request-addressed taskless receipt already exists; legacy migration refused to overwrite it.",
    )


def _move_taskless_legacy_receipts(pending: list[tuple[Path, Path]]) -> None:
    for source, target in pending:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, target, follow_symlinks=False)
            source.unlink()
        except FileExistsError as error:
            raise HTTPException(
                status_code=409,
                detail="A request-addressed taskless receipt appeared during migration; no receipt was overwritten.",
            ) from error
        except OSError as error:
            raise HTTPException(
                status_code=409,
                detail="Legacy taskless receipts could not be moved without risking an overwrite.",
            ) from error


def _taskless_execution_receipts(
    config: McpRuntimeConfig, selection: OrcaSelection
) -> list[tuple[Path, dict[str, Any]]]:
    directory = _taskless_session_directory(config, selection.role)
    try:
        mode = directory.lstat().st_mode
    except FileNotFoundError:
        return []
    except OSError as error:
        raise HTTPException(
            status_code=409, detail="Taskless Orca sessions cannot be inspected safely."
        ) from error
    if not stat.S_ISDIR(mode):
        raise HTTPException(
            status_code=409, detail="The taskless Orca session store is not a directory."
        )
    expected_selection = selection_binding(selection)
    records: list[tuple[Path, dict[str, Any]]] = []
    for path in directory.glob("*.json"):
        receipt = _read_receipt(path)
        if receipt is None:
            continue
        try:
            request_id = uuid.UUID(str(receipt.get("requestId")))
        except (ValueError, TypeError, AttributeError) as error:
            raise HTTPException(
                status_code=409, detail="A saved taskless Orca receipt has no valid requestId."
            ) from error
        if (
            path.name != f"{request_id}.json"
            or receipt.get("requestId") != str(request_id)
            or receipt.get("role") != selection.role
            or receipt.get("selection") != expected_selection
        ):
            raise HTTPException(
                status_code=409,
                detail="A saved taskless Orca receipt does not match its request address and role selection.",
            )
        records.append((path, receipt))
    return sorted(
        records,
        key=lambda row: (str(row[1].get("createdAt", "")), str(row[1].get("requestId", ""))),
        reverse=True,
    )


def _receipt_address_matches(
    receipt: dict[str, Any], selection: OrcaDispatchRequest | OrcaResultRequest
) -> bool:
    request_id = selection.request_id
    if selection.role in TASKLESS_ROLES:
        request_matches = request_id is not None and receipt.get("requestId") == str(request_id)
    elif isinstance(selection, OrcaDispatchRequest) and selection.action == "revive":
        request_matches = True
    else:
        request_matches = request_id is None or receipt.get("requestId") == str(request_id)
    return receipt.get("selection") == selection_binding(selection) and request_matches


def _request_digest(context: OrcaRoleContext, request: OrcaDispatchRequest) -> str:
    override = (
        request.agent_override.model_dump(mode="json", by_alias=True, exclude_none=True)
        if request.agent_override
        else None
    )
    return _digest(
        {
            "selection": selection_binding(context),
            "agentOverride": override,
        }
    )


def _execution_reference(outcome: dict[str, Any], worktree_id: str) -> dict[str, str]:
    kind = outcome.get("kind")
    handle = outcome.get("handle")
    if kind not in {"structured", "terminal"} or not isinstance(handle, str) or not handle:
        return {}
    reference = {"kind": kind, "handle": handle, "worktreeId": worktree_id}
    pane_key = outcome.get("paneKey")
    if kind == "terminal" and isinstance(pane_key, str) and pane_key:
        reference["paneKey"] = pane_key
    session_id = outcome.get("sessionId")
    if kind == "structured" and isinstance(session_id, str) and session_id:
        reference["sessionId"] = session_id
    return reference


def _prompt_reference(prompt: Any) -> dict[str, str] | None:
    if not isinstance(prompt, dict):
        return None
    delivery = prompt.get("delivery")
    outcome = prompt.get("outcome")
    if delivery not in {"submit", "draft"} or outcome not in {
        "journaled",
        "handed-to-terminal",
        "not-delivered",
    }:
        return None
    reference = {"delivery": delivery, "outcome": outcome}
    if outcome == "journaled":
        message_id = prompt.get("messageId")
        if not isinstance(message_id, str) or not message_id:
            return None
        reference["messageId"] = message_id
    return reference


def _public_execution(receipt: dict[str, Any]) -> dict[str, Any]:
    status = receipt.get("status")
    public = {
        key: receipt[key]
        for key in (
            "requestId",
            "role",
            "status",
            "createdAt",
            "updatedAt",
            "terminalObservedAt",
            "revivedAt",
            "detail",
            "warning",
            "result",
            "prompt",
            "nativeMcpScope",
            "resumeResult",
            "canRevive",
        )
        if key in receipt
    }
    if receipt.get("role") in TASKLESS_ROLES:
        # Taskless roles admit a fresh deliberate session under a new requestId; unresolved requests remain guarded.
        public["canStart"] = status in {
            "running",
            "stopped",
            "completed",
            "failed",
            "rejected",
            "interrupted",
        }
    else:
        public["canStart"] = status in {"completed", "failed", "stopped", "rejected"}
    public["canRevive"] = receipt.get("canRevive") is True
    public["canRetry"] = status in {"starting", "unknown"} and isinstance(
        receipt.get("replayRequest"), dict
    )
    report = receipt.get("report")
    if isinstance(report, dict):
        path = report.get("path")
        canonical_path = report.get("canonicalPath")
        if isinstance(path, str) and isinstance(canonical_path, str):
            public["report"] = {
                "path": path,
                "canonicalPath": canonical_path,
                "available": Path(canonical_path).is_file(),
            }
    if public["canRetry"]:
        selection = receipt.get("selection")
        if isinstance(selection, dict):
            retry_payload = {key: value for key, value in selection.items() if value is not None}
            override = receipt.get("requestedAgentOverride")
            if isinstance(override, dict):
                retry_payload["agentOverride"] = override
            public["retryPayload"] = retry_payload
    reference = receipt.get("execution")
    if isinstance(reference, dict):
        public["execution"] = {
            key: reference[key]
            for key in ("kind", "handle", "sessionId", "worktreeId", "paneKey")
            if isinstance(reference.get(key), str)
        }
    return public


def _read_receipt(path: Path) -> dict[str, Any] | None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None
    except OSError as error:
        raise HTTPException(
            status_code=409,
            detail="The saved Orca execution receipt cannot be inspected; reconcile it before launching.",
        ) from error
    if not stat.S_ISREG(mode):
        raise HTTPException(
            status_code=409,
            detail="The Orca execution receipt is not a regular file; refusing a new launch.",
        )
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=409,
            detail="The Orca execution receipt is unreadable; reconcile it before launching.",
        ) from error
    if not isinstance(receipt, dict) or receipt.get("schema") != "ar-orca-native-execution/v1":
        raise HTTPException(
            status_code=409,
            detail="The Orca execution receipt is malformed; reconcile it before launching.",
        )
    return receipt


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _archive_receipt(path: Path, receipt: dict[str, Any]) -> None:
    request_id = receipt.get("requestId")
    if not isinstance(request_id, str):
        raise ValueError(
            "The prior Orca receipt has no request identity and cannot be archived safely."
        )
    history = path.parent / "history" / f"{request_id}.json"
    if history.exists():
        raise ValueError("A prior Orca execution archive already has this request identity.")
    history.parent.mkdir(parents=True, exist_ok=True)
    path.replace(history)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
