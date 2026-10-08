"""Durable request-addressed execution receipts and public projections.

Receipts of this line live under ``paseo-native-executions`` and carry this line's schema name;
nothing here reads a receipt directory of the line this build was copied from.
"""

from __future__ import annotations

import hashlib
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

from agents_remember.application.role_launch_context import (
    LEAF_ROLES,
    TASKLESS_ROLES,
    RoleLaunchContext,
    selection_binding,
)
from agents_remember.cli.paseo_launch import PASEO_AGENT_KIND, LaunchOutcome, run_launch_call
from agents_remember.cli.role_handover_artifacts import restore_handover_artifact
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import (
    RoleDispatchRequest,
    RoleResultRequest,
    RoleSelection,
)
from agents_remember.tasks.document_refs import TaskDocumentTopology

# Where this line keeps its receipts, under a task's or the coordination root's notes/reports.
EXECUTIONS_DIRECTORY = "paseo-native-executions"
# The schema name every receipt of this line carries; a file without it is not read as a receipt.
RECEIPT_SCHEMA = "ar-role-execution/v1"
# The link in a leaf's enclosure group folder through which its agents reach the task's reports.
REPORT_ACCESS_LINK = "task-reports"


def digest(value: Any) -> str:
    """The SHA-256 of a value's canonical JSON text: sorted keys, no spaces, UTF-8."""

    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class _TasklessLegacyMigration:
    config: McpRuntimeConfig
    selection: RoleSelection
    legacy: Path
    expected_selection: dict[str, Any]
    seen_ids: set[str]


def _execute_prepared_launch(
    config: McpRuntimeConfig,
    path: Path,
    receipt: dict[str, Any],
) -> JSONResponse:
    """Run the receipt's saved launch call and write what the runtime answered.

    The receipt already names the agent id, so running the same call again reaches the same agent.
    """

    with exclusive_file_lock(path, "role execution receipt"):
        current = _same_request_receipt(path, receipt)
        closing = _leaf_is_closing(current)
    if closing:
        return _refuse_closing_launch(config, path, current)
    launch_call = current.get("replayRequest")
    if not isinstance(launch_call, dict):
        raise HTTPException(
            status_code=409,
            detail="The unresolved launch has no saved launch call; reconcile the Paseo runtime before retrying.",
        )
    artifact = current.get("handoverArtifact")
    if isinstance(artifact, dict):
        # The first message names this file; it must hold that message whenever it is sent.
        _rebind_report_access(current)
        restore_handover_artifact(artifact, _saved_first_message(launch_call), receipt=path)
    if current.get("role") in LEAF_ROLES:
        outcome = run_launch_call(
            config, launch_call, before_create=lambda: _leaf_before_create(path, current)
        )
    else:
        outcome = run_launch_call(config, launch_call)
    with exclusive_file_lock(path, "role execution receipt"):
        current = _same_request_receipt(path, receipt)
        if not _leaf_is_closing(current):
            return _publish_launch_outcome(path, current, launch_call, outcome)
        if outcome.kind == "created":
            current.update(execution=outcome.execution, agent=outcome.applied, hostAgentExists=True)
            _write_receipt(path, current)
        elif outcome.lost_agent_id:
            current["hostAgentExists"] = True
            _write_receipt(path, current)
    return _refuse_closing_launch(
        config, path, current, creation_returned=outcome.kind == "created"
    )


def _same_request_receipt(path: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    current = _read_receipt(path)
    if current is None or current.get("requestId") != receipt.get("requestId"):
        raise HTTPException(status_code=409, detail="The prepared launch receipt was replaced.")
    return current


def _leaf_is_closing(receipt: dict[str, Any]) -> bool:
    selection = receipt.get("selection")
    archive = receipt.get("leafArchive")
    return (
        receipt.get("role") in LEAF_ROLES
        and isinstance(selection, dict)
        and selection.get("role") == receipt.get("role")
        and isinstance(selection.get("taskDocumentRef"), dict)
        and isinstance(archive, dict)
        and archive.get("closing") is True
        and archive.get("taskDocumentRef") == selection["taskDocumentRef"]
    )


def _leaf_before_create(path: Path, receipt: dict[str, Any]) -> bool:
    with exclusive_file_lock(path, "role execution receipt"):
        current = _same_request_receipt(path, receipt)
        if _leaf_is_closing(current):
            return False
        current["leafCreateEntered"] = True
        _write_receipt(path, current)
        return True


def _refuse_closing_launch(
    config: McpRuntimeConfig,
    path: Path,
    receipt: dict[str, Any],
    *,
    creation_returned: bool = False,
) -> JSONResponse:
    # Archival already depends on receipts; resolve that owner only on the retired-start route.
    from agents_remember.cli.role_launch_archive import refuse_retired_start  # noqa: PLC0415

    refused = refuse_retired_start(config, path, receipt, creation_returned=creation_returned)
    return JSONResponse(_public_execution(refused), status_code=409)


def _publish_launch_outcome(
    path: Path, receipt: dict[str, Any], launch_call: dict[str, Any], outcome: LaunchOutcome
) -> JSONResponse:
    if outcome.kind == "created":
        receipt["execution"] = outcome.execution
        receipt["agent"] = outcome.applied
        if outcome.workspace_preparation in {"created", "found", "opened"}:
            receipt.setdefault("preparation", {})["workspace"] = outcome.workspace_preparation
        if outcome.warning:
            receipt["warning"] = outcome.warning
        receipt.update(
            status="running",
            hostAgentExists=True,
            detail="The Paseo runtime created the role agent; AR acceptance remains with its owner.",
            updatedAt=_now_iso(),
        )
        receipt.pop("replayRequest", None)
        _write_receipt(path, receipt)
        return JSONResponse(_public_execution(receipt))
    if outcome.kind == "refused":
        if outcome.lost_agent_id:
            # The agent exists and can never be given its first message; the next launch on a
            # task-bound selection archives it, as it archives the agent of an execution it
            # replaces. A later start of a taskless role archives nothing.
            receipt["pendingArchiveAgentId"] = outcome.lost_agent_id
        elif not outcome.predecessor_settled:
            # The agent this launch was to replace is still live; the next launch archives it.
            receipt["pendingArchiveAgentId"] = launch_call["archiveAgentId"]
        receipt.update(
            status="rejected",
            hostAgentExists=outcome.lost_agent_id is not None,
            detail=(
                "The agent of this launch never got its first message and the Paseo runtime "
                "cannot open its session again, so this launch is closed. "
                f"{_lost_agent_advice(receipt, outcome.lost_agent_id)} {outcome.message}"
                if outcome.lost_agent_id
                else "The Paseo runtime refused the launch; no agent exists under the minted "
                f"agent id. {outcome.message}"
            ),
            updatedAt=_now_iso(),
        )
        receipt.pop("replayRequest", None)
        _write_receipt(path, receipt)
        return JSONResponse(_public_execution(receipt), status_code=502)
    receipt.update(
        status="unknown",
        detail=(
            f"The launch has no usable answer ({outcome.code}): {outcome.message} Retry this "
            "same request to repeat the saved call under the same agent id."
        ),
        updatedAt=_now_iso(),
    )
    _write_receipt(path, receipt)
    return JSONResponse(_public_execution(receipt), status_code=202)


def _lost_agent_advice(receipt: dict[str, Any], lost_agent_id: str) -> str:
    """What becomes of an agent that can never be given its first message, by role class."""

    if receipt.get("role") in TASKLESS_ROLES:
        # A taskless role keeps one receipt per request, and no later start archives an agent.
        return (
            "Start the role again; a start of this role archives no agent, so archive agent "
            f"{lost_agent_id} in Paseo by hand."
        )
    return "Start the role again; that launch archives the agent."


def _bind_task_report_access(workspace: Path, task_reports: Path) -> Path:
    """Expose only this task's canonical reports from inside its enclosure group folder.

    The one place that creates the link, for a first launch and for a retry alike.
    """

    link = workspace / REPORT_ACCESS_LINK
    target = task_reports.resolve()
    if link.is_symlink():
        if link.resolve() != target:
            raise ValueError(
                "The enclosure task-report link points outside the selected task's report folder."
            )
        return link
    if link.exists():
        raise ValueError(
            "The enclosure task-report path is occupied by a non-link; refusing to replace it."
        )
    link.symlink_to(target, target_is_directory=True)
    return link


def _rebind_report_access(receipt: dict[str, Any]) -> None:
    """Put a leaf's report-access link back when it is gone, as its first launch created it.

    A leaf agent is given its report and its artifact through that link, and a retry does not
    pass through the preparation that binds it. Only a missing link is created, and only where
    the receipt agrees with itself: the link has the name and the folder a first launch gives
    it, the artifact's path runs through it, and the artifact's recorded place lies under the
    folder the link is to lead to. Whatever else is at the link's name, and a receipt that does
    not agree with itself, is left to the artifact check, which then refuses and says why.
    """

    workspace = receipt.get("workspace")
    artifact = receipt.get("handoverArtifact")
    if not isinstance(workspace, dict) or not isinstance(artifact, dict):
        return
    folder = workspace.get("path")
    access = workspace.get("taskReportAccessRoot")
    reports = workspace.get("taskReportRoot")
    if not isinstance(folder, str) or not isinstance(access, str) or not isinstance(reports, str):
        return
    link = Path(access)
    if (
        link != Path(folder) / REPORT_ACCESS_LINK
        or not _lies_under(artifact.get("path"), link)
        or not _lies_under(artifact.get("canonicalPath"), Path(reports))
    ):
        return
    if link.is_symlink() or link.exists() or not link.parent.is_dir() or not Path(reports).is_dir():
        return
    _bind_task_report_access(link.parent, Path(reports))


def _lies_under(path: Any, folder: Path) -> bool:
    """Whether a recorded path names a place below ``folder``, read as it is written."""

    if not isinstance(path, str) or ".." in Path(path).parts:
        return False
    return Path(path) != folder and Path(path).is_relative_to(folder)


def _saved_first_message(launch_call: dict[str, Any]) -> str:
    agent = launch_call.get("agent")
    message = agent.get("prompt") if isinstance(agent, dict) else None
    if not isinstance(message, str):
        raise ValueError("The saved launch call has no first message for its handover artifact.")
    return message


def _replaced_agent_id(receipt: dict[str, Any]) -> str | None:
    """The agent a new execution on the same selection archives before it creates its own.

    That is the closed execution's agent, or the agent that execution itself still had to archive
    when the runtime refused it.
    """

    reference = receipt.get("execution")
    if isinstance(reference, dict) and reference.get("kind") == PASEO_AGENT_KIND:
        agent_id = reference.get("agentId")
        if isinstance(agent_id, str) and agent_id:
            return agent_id
    pending = receipt.get("pendingArchiveAgentId")
    return pending if isinstance(pending, str) and pending else None


def _receipt_path(
    config: McpRuntimeConfig,
    selection: RoleSelection,
    request_id: uuid.UUID | None = None,
) -> Path:
    if selection.role in TASKLESS_ROLES:
        if request_id is None:
            raise ValueError("A taskless role execution receipt requires its durable requestId.")
        return _taskless_session_directory(config, selection.role) / f"{request_id}.json"
    return _legacy_receipt_path(config, selection)


def _legacy_receipt_path(config: McpRuntimeConfig, selection: RoleSelection) -> Path:
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
    key = digest(selection_binding(selection))[:24]
    if ref:
        parent = topology.path_for_ref(ref).parent
        return parent / "notes" / "reports" / EXECUTIONS_DIRECTORY / f"{role}-{key}.json"
    return (
        config.coordination_root / "notes" / "reports" / EXECUTIONS_DIRECTORY / f"{role}-{key}.json"
    )


def _taskless_session_directory(config: McpRuntimeConfig, role: str) -> Path:
    if role not in TASKLESS_ROLES:
        raise ValueError(
            "Per-request role execution receipts are reserved for taskless project roles."
        )
    return config.coordination_root / "notes" / "reports" / EXECUTIONS_DIRECTORY / role / "sessions"


def _message_binding_projection_reference(
    config: McpRuntimeConfig,
    request_id: uuid.UUID,
    binding: dict[str, Any],
) -> dict[str, str]:
    body = _message_binding_projection_bytes(binding)
    path = _message_binding_projection_path(config, request_id)
    return {
        "path": path.as_posix(),
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def _place_message_binding_projection(
    config: McpRuntimeConfig,
    request_id: uuid.UUID,
    binding: dict[str, Any],
    expected_reference: dict[str, str],
) -> bool:
    """Create or reuse the binding file of one request id; say whether this call created it.

    Only the launch that created the file may take it back when it is refused afterwards.
    """
    reference = _message_binding_projection_reference(config, request_id, binding)
    if reference != expected_reference:
        raise ValueError("The message-binding projection reference does not match its content.")
    path = Path(reference["path"])
    body = _message_binding_projection_bytes(binding)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if _existing_message_binding_projection_matches(path, body):
        return False

    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            if not _existing_message_binding_projection_matches(path, body):
                raise ValueError(
                    "This request ID already has a different immutable message-binding projection."
                ) from None
            return False
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


def _verify_message_binding_projection(
    config: McpRuntimeConfig,
    request_id: uuid.UUID,
    binding: dict[str, Any],
    expected_reference: dict[str, str],
) -> None:
    reference = _message_binding_projection_reference(config, request_id, binding)
    if reference != expected_reference:
        raise ValueError("This request ID is already bound to different message content.")
    if not _existing_message_binding_projection_matches(
        Path(reference["path"]), _message_binding_projection_bytes(binding)
    ):
        raise ValueError("The saved native message-binding projection is missing.")


def _message_binding_projection_path(config: McpRuntimeConfig, request_id: uuid.UUID) -> Path:
    return (
        config.coordination_root
        / "notes"
        / "reports"
        / EXECUTIONS_DIRECTORY
        / "message-bindings"
        / f"{request_id}.json"
    ).resolve(strict=False)


def _message_binding_projection_bytes(binding: dict[str, Any]) -> bytes:
    return (
        json.dumps(binding, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _existing_message_binding_projection_matches(path: Path, expected: bytes) -> bool:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(mode):
        raise ValueError("The message-binding projection path is not a regular file.")
    if path.read_bytes() != expected:
        raise ValueError(
            "This request ID already has a different immutable message-binding projection."
        )
    return True


def _migrate_taskless_legacy_receipt(config: McpRuntimeConfig, selection: RoleSelection) -> None:
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
    config: McpRuntimeConfig, selection: RoleSelection
) -> list[tuple[Path, dict[str, Any]]]:
    directory = _taskless_session_directory(config, selection.role)
    try:
        mode = directory.lstat().st_mode
    except FileNotFoundError:
        return []
    except OSError as error:
        raise HTTPException(
            status_code=409, detail="Taskless role executions cannot be inspected safely."
        ) from error
    if not stat.S_ISDIR(mode):
        raise HTTPException(
            status_code=409, detail="The taskless role execution store is not a directory."
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
                status_code=409,
                detail="A saved taskless role execution receipt has no valid requestId.",
            ) from error
        if (
            path.name != f"{request_id}.json"
            or receipt.get("requestId") != str(request_id)
            or receipt.get("role") != selection.role
            or receipt.get("selection") != expected_selection
        ):
            raise HTTPException(
                status_code=409,
                detail="A saved taskless role execution receipt does not match its request address and role selection.",
            )
        records.append((path, receipt))
    return sorted(
        records,
        key=lambda row: (str(row[1].get("createdAt", "")), str(row[1].get("requestId", ""))),
        reverse=True,
    )


def _receipt_address_matches(
    receipt: dict[str, Any], selection: RoleDispatchRequest | RoleResultRequest
) -> bool:
    request_id = selection.request_id
    if selection.role in TASKLESS_ROLES:
        request_matches = request_id is not None and receipt.get("requestId") == str(request_id)
    elif isinstance(selection, RoleDispatchRequest) and selection.action == "revive":
        request_matches = True
    else:
        request_matches = request_id is None or receipt.get("requestId") == str(request_id)
    return receipt.get("selection") == selection_binding(selection) and request_matches


def _request_digest(context: RoleLaunchContext, request: RoleDispatchRequest) -> str:
    override = (
        request.agent_override.model_dump(mode="json", by_alias=True, exclude_none=True)
        if request.agent_override
        else None
    )
    return digest(
        {
            "selection": selection_binding(context),
            "agentOverride": override,
        }
    )


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
            "capsuleOperation",
            "arMcpContext",
            "sessionOptions",
            "preparation",
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
    if _leaf_is_closing(receipt) and receipt["leafArchive"].get("startRefused") is True:
        # Unsettled archive status also prevents a different request moving an in-flight receipt.
        own = receipt["leafArchive"].get("outcomes", {}).get(receipt.get("agentId"), {})
        if own.get("state") in {"archived", "gone"}:
            public["status"] = "rejected"
        public.update(startRefused=True, canStart=False, canRevive=False, canRetry=False)
        public.pop("retryPayload", None)
    reference = receipt.get("execution")
    if isinstance(reference, dict):
        public["execution"] = {
            key: reference[key]
            for key in ("kind", "serverId", "workspaceId", "agentId")
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
            detail="The saved role execution receipt cannot be inspected; reconcile it before launching.",
        ) from error
    if not stat.S_ISREG(mode):
        raise HTTPException(
            status_code=409,
            detail="The role execution receipt is not a regular file; refusing a new launch.",
        )
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=409,
            detail="The role execution receipt is unreadable; reconcile it before launching.",
        ) from error
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA:
        raise HTTPException(
            status_code=409,
            detail="The role execution receipt is malformed; reconcile it before launching.",
        )
    return receipt


def _write_receipt(path: Path, receipt: dict[str, Any], *, archive_update: bool = False) -> None:
    with exclusive_file_lock(path, "role execution receipt"):
        current = _read_receipt(path)
        if (
            current is not None
            and current.get("requestId") == receipt.get("requestId")
            and current.get("leafCreateEntered") is True
        ):
            receipt["leafCreateEntered"] = True
        if (
            not archive_update
            and current is not None
            and current.get("requestId") == receipt.get("requestId")
            and _leaf_is_closing(current)
        ):
            receipt["leafArchive"] = current["leafArchive"]
            if "pendingArchiveAgentId" in current:
                receipt["pendingArchiveAgentId"] = current["pendingArchiveAgentId"]
            else:
                receipt.pop("pendingArchiveAgentId", None)
            if current["leafArchive"].get("startRefused") is True:
                for key in ("status", "detail", "replayRequest"):
                    if key in current:
                        receipt[key] = current[key]
                    else:
                        receipt.pop(key, None)
                receipt["canRevive"] = False
        temporary = _write_receipt_aside(path, receipt)
        try:
            temporary.replace(path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise


def _create_receipt(path: Path, receipt: dict[str, Any]) -> bool:
    """Create a receipt only if none exists at its address; say whether this call created it.

    The complete file appears under its name in one step, and that step fails when the name is
    taken. Of two processes that launch the same selection at the same moment, one creates the
    receipt and the other is told that it did not.
    """

    temporary = _write_receipt_aside(path, receipt)
    try:
        os.link(temporary, path, follow_symlinks=False)
    except FileExistsError:
        return False
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _write_receipt_aside(path: Path, receipt: dict[str, Any]) -> Path:
    """Write the receipt to a synced temporary file beside its address."""

    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return Path(temporary)


def _archive_receipt(path: Path, receipt: dict[str, Any]) -> None:
    request_id = receipt.get("requestId")
    if not isinstance(request_id, str):
        raise ValueError(
            "The prior role execution receipt has no request identity and cannot be archived safely."
        )
    history = path.parent / "history" / f"{request_id}.json"
    if history.exists():
        raise ValueError("A prior role execution archive already has this request identity.")
    history.parent.mkdir(parents=True, exist_ok=True)
    path.replace(history)


def _archived_receipt_agent_id(path: Path, selection: RoleSelection) -> str | None:
    """The agent to archive when a task-bound selection has no receipt but an archived one.

    The newest receipt in the history that belongs to this selection names it, as
    :func:`_replaced_agent_id` reads it. Archiving an agent a second time is harmless. Taskless
    roles keep one receipt per request and archive none.
    """

    if selection.role in TASKLESS_ROLES:
        return None
    expected = selection_binding(selection)
    archived: list[dict[str, Any]] = []
    for candidate in (path.parent / "history").glob("*.json"):
        try:
            receipt = _read_receipt(candidate)
        except HTTPException:
            continue  # an unreadable archived receipt names no agent and blocks no launch
        if receipt and receipt.get("selection") == expected:
            archived.append(receipt)
    if not archived:
        return None
    return _replaced_agent_id(max(archived, key=lambda row: str(row.get("createdAt", ""))))


def _refuse_reused_request_id(
    config: McpRuntimeConfig, path: Path, request: RoleDispatchRequest
) -> None:
    """Refuse a request id that already belongs elsewhere, before anything is prepared for it.

    Called for a request whose id no receipt at its address carries. Its id may be the id of an
    archived execution of the task folder, which can neither be launched again nor archived a
    second time, or of a request on another selection, whose message-binding file is written
    once. A message-binding file under the id that does not record this selection, whatever it
    holds instead, refuses the request as well: the write-once rule would refuse it later, after
    the enclosure and the handover were prepared.
    """

    archived = path.parent / "history" / f"{request.request_id}.json"
    if archived.exists() or archived.is_symlink():
        raise HTTPException(
            status_code=409,
            detail=(
                f"Request id {request.request_id} belongs to an archived execution of this "
                "AR role selection's task folder; start again under a new request id."
            ),
        )
    binding_file = _message_binding_projection_path(config, request.request_id)
    if not binding_file.exists():
        return
    try:
        bound = json.loads(binding_file.read_text("utf-8"))
    except (OSError, ValueError):
        bound = None
    selection = bound.get("selection") if isinstance(bound, dict) else None
    if selection == selection_binding(request):
        return
    if isinstance(selection, dict):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Request id {request.request_id} is already bound to another AR role selection "
                f"({selection.get('role')}); nothing was prepared for this request."
            ),
        )
    raise HTTPException(
        status_code=409,
        detail=(
            f"Request id {request.request_id} already has a message-binding file that does not "
            f"record this AR role selection ({binding_file}); nothing was prepared for this "
            "request. Start again under a new request id."
        ),
    )


def _discard_message_binding_projection(config: McpRuntimeConfig, request_id: uuid.UUID) -> None:
    """Remove the binding file of a request that was refused after the file was written."""

    _message_binding_projection_path(config, request_id).unlink(missing_ok=True)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
