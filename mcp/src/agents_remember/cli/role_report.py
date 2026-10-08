"""Read the one launch-recorded report of an exactly addressed role execution."""

from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import Field

from agents_remember.application.role_launch_context import (
    resolve_role_launch_context,
)
from agents_remember.cli.role_answers import alias_response
from agents_remember.cli.role_launch_preparation import REPORTS_DIRECTORY
from agents_remember.cli.role_launch_receipts import (
    REPORT_ACCESS_LINK,
    _read_receipt,
    _receipt_address_matches,
    _receipt_path,
)
from agents_remember.kernel.authority import require_repo
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.sidecar_pairing import confine_rel
from agents_remember.models.role_launcher import RoleResultRequest
from agents_remember.serving.notes import _MAX_FILE_BYTES
from agents_remember.serving.scope import decode_capped, language_for


class RoleReportRequest(RoleResultRequest):
    request_id: uuid.UUID = Field(default=..., alias="requestId")


def _refusal(status: str, detail: str, code: int) -> JSONResponse:
    return JSONResponse({"status": status, "detail": detail}, status_code=code)


def read_role_report(config: McpRuntimeConfig, request: RoleReportRequest) -> JSONResponse:
    try:
        context = resolve_role_launch_context(config, request)
        if context.effective_task:
            require_repo(config, context.effective_task.ref.repository)
            root = context.effective_task.path.parent / "notes" / "reports" / REPORTS_DIRECTORY
            expected_name = (
                f"{context.effective_task.document.id}-{request.role}-{request.request_id}.md"
            )
        else:
            expected_name = f"{request.request_id}.md"
            root = (
                config.workspace_root
                / ".agents-remember"
                / "reports"
                / REPORTS_DIRECTORY
                / request.role
            )
        receipt_path = _receipt_path(config, request, request.request_id)
    except (OSError, ValueError) as error:
        return _refusal("report-selection-invalid", str(error), 409)
    try:
        receipt = _read_receipt(receipt_path)
    except (HTTPException, OSError, ValueError):
        return _refusal(
            "report-receipt-invalid",
            "The report's saved execution receipt is unreadable or invalid. Restore the receipt and retry.",
            409,
        )
    if receipt is None:
        return _refusal(
            "report-execution-missing", "No execution is recorded for this selection.", 404
        )
    if not _receipt_address_matches(receipt, request):
        return _refusal(
            "report-execution-mismatch",
            "The report belongs to another execution or selection.",
            409,
        )

    recorded_role = str(receipt["role"])
    if recorded_role != request.role:
        if context.effective_task:
            expected_name = (
                f"{context.effective_task.document.id}-{recorded_role}-{request.request_id}.md"
            )
        else:
            root = root.parent / recorded_role
    path = _recorded_report_path(root, expected_name, receipt.get("report"))
    return path if isinstance(path, JSONResponse) else _read_report_file(path)


def _recorded_report_path(root: Path, expected_name: str, report: object) -> Path | JSONResponse:
    if not isinstance(report, dict) or not isinstance(report.get("canonicalPath"), str):
        return _refusal("report-not-written", "The report is not written yet.", 404)
    try:
        root = root.resolve()
        recorded = Path(report["canonicalPath"])
        relative = recorded.relative_to(root).as_posix()
        if recorded.parent != root or recorded.name != expected_name:
            return _refusal(
                "report-path-mismatch",
                "The recorded file is not the report named by this execution.",
                403,
            )
        path = root / confine_rel(root, relative)
        if path != recorded:
            return _refusal(
                "report-outside-root",
                "The recorded report path no longer matches its report root.",
                403,
            )
    except (OSError, ValueError):
        return _refusal(
            "report-outside-root",
            "The recorded report lies outside this execution's report root.",
            403,
        )
    return _report_access_refusal(report.get("path"), path) or path


def _report_access_refusal(access: object, canonical: Path) -> JSONResponse | None:
    if not isinstance(access, str):
        return None
    recorded = Path(access)
    alias = recorded.parent.parent
    present = os.path.lexists(recorded) or (
        alias.name == REPORT_ACCESS_LINK and os.path.lexists(alias)
    )
    try:
        if present and recorded.resolve() != canonical:
            return _refusal(
                "report-access-retargeted",
                "The report access path points elsewhere. Restore its recorded target and retry.",
                409,
            )
    except (OSError, ValueError):
        return _refusal(
            "report-access-invalid",
            "The report access path cannot be resolved. Restore it and retry.",
            409,
        )
    return None


def _read_report_file(path: Path) -> JSONResponse:
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            return _refusal(
                "report-unreadable", "The recorded report is not a readable regular file.", 409
            )
        with path.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            raw = stream.read(_MAX_FILE_BYTES + 4)
        content, truncated = decode_capped(raw, _MAX_FILE_BYTES)
    except FileNotFoundError:
        return _refusal("report-not-written", "The report is not written yet.", 404)
    except (OSError, UnicodeDecodeError):
        return _refusal(
            "report-unreadable",
            "The report cannot be read. Check its access and text encoding, then retry.",
            409,
        )
    return JSONResponse(
        {
            "path": path.name,
            "language": language_for(path),
            "size": size,
            "truncated": truncated,
            "content": content,
        }
    )


def register_role_report_route(app: FastAPI, config: McpRuntimeConfig) -> None:
    def endpoint(request: RoleReportRequest) -> JSONResponse:
        return alias_response(request, read_role_report(config, request))

    app.add_api_route("/api/role-launch/report", endpoint, methods=["POST"])
