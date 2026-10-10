"""Read the one launch-recorded report of an exactly addressed role execution."""

from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import Field

from agents_remember.application.role_launch_context import (
    resolve_role_launch_context,
)
from agents_remember.cli.role_answers import alias_response
from agents_remember.cli.role_launch_preparation import REPORTS_DIRECTORY
from agents_remember.cli.role_launch_receipts import (
    EXECUTIONS_DIRECTORY,
    REPORT_ACCESS_LINK,
    _read_receipt,
    _receipt_address_matches,
    _receipt_path,
)
from agents_remember.errors import AuthorityError
from agents_remember.kernel.authority import require_repo
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.sidecar_pairing import confine_rel
from agents_remember.models.role_launcher import RoleResultRequest
from agents_remember.serving.notes import _MAX_FILE_BYTES
from agents_remember.serving.response_contract import SCOPED_READ_RESPONSES, TaskReports
from agents_remember.serving.scope import decode_capped, language_for
from agents_remember.tasks.document_refs import TaskDocumentTopology

# A task page shows at most this many recorded role starts; the rest stay reachable
# through the explorer link. The cap is fixed here and pinned by the listing tests.
_TASK_REPORT_ROWS = 20


class RoleReportRequest(RoleResultRequest):
    request_id: uuid.UUID = Field(default=..., alias="requestId")


def _refusal(status: str, detail: str, code: int) -> JSONResponse:
    return JSONResponse({"status": status, "detail": detail}, status_code=code)


def _complete_task_selection(
    config: McpRuntimeConfig, request: RoleReportRequest
) -> RoleReportRequest:
    """Fill a task page's partial selection from the canonical document chain.

    The page emits ``{role, requestId, taskDocumentRef}`` — the exact recorded report
    identity. The sprint/master refs it omits are derived authoritatively from the task
    document's own parent chain, never guessed: an unknown task or a broken chain keeps
    the existing invalid-selection refusal downstream.
    """
    if request.task_document_ref is None:
        return request
    update: dict[str, Any] = {}
    # Only the exact task-page emission shape — the leaf alone, no partial chain — is
    # completed. A request naming a partial chain (sprint without master or vice versa)
    # stays malformed and keeps the existing invalid-selection refusal.
    if (
        request.role in {"worker", "reviewer", "curator"}
        and request.sprint_document_ref is None
        and request.master_document_ref is None
    ):
        topology = TaskDocumentTopology(config.coordination_root)
        master_ref = topology.parent(request.task_document_ref)
        if master_ref is not None:
            update["master_document_ref"] = master_ref
            sprint_ref = topology.parent(master_ref)
            if sprint_ref is not None:
                update["sprint_document_ref"] = sprint_ref
    if not update:
        return request
    return request.model_copy(update=update)


def _find_recorded_receipt(legacy_path: Path, request: RoleReportRequest) -> dict[str, Any] | None:
    """The receipt the request names, from its legacy address or the task archive.

    A superseded start's receipt moves to ``history/{requestId}.json`` beside the live
    address; the live address then holds a newer execution. Only a receipt whose stored
    selection and request identity both match is returned — a wrong task or request
    still finds nothing here.
    """
    store = legacy_path.parent
    try:
        candidates = sorted(store.glob("*.json"))
        history = store / "history"
        if history.is_dir():
            candidates += sorted(history.glob("*.json"))
    except OSError:
        return None
    for path in candidates:
        if path == legacy_path:
            continue
        try:
            receipt = _read_receipt(path)
        except (HTTPException, OSError, ValueError):
            continue
        if receipt is not None and _receipt_address_matches(receipt, request):
            return receipt
    return None


def read_role_report(config: McpRuntimeConfig, request: RoleReportRequest) -> JSONResponse:
    try:
        request = _complete_task_selection(config, request)
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
        legacy_mismatch = receipt is not None and not _receipt_address_matches(receipt, request)
        if receipt is None or legacy_mismatch:
            # The live address is empty or holds a newer execution; the named one may
            # be archived beside it.
            receipt = _find_recorded_receipt(receipt_path, request)
            if receipt is None and legacy_mismatch:
                return _refusal(
                    "report-execution-mismatch",
                    "The report belongs to another execution or selection.",
                    409,
                )
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
    app.add_api_route(
        "/api/role-launch/reports",
        lambda repo="", master="", doc="": list_task_reports(config, repo, master, doc),
        methods=["GET"],
        response_model=TaskReports,
        responses=SCOPED_READ_RESPONSES,
    )


def _receipt_selection_doc(receipt: dict[str, Any]) -> dict[str, str] | None:
    """The ``{repository, path}`` task reference a receipt was recorded for, if any."""
    selection = receipt.get("selection")
    if not isinstance(selection, dict):
        return None
    ref = selection.get("taskDocumentRef")
    if not isinstance(ref, dict):
        return None
    repository, path = ref.get("repository"), ref.get("path")
    if not isinstance(repository, str) or not isinstance(path, str):
        return None
    return {"repository": repository, "path": path}


def _receipt_row(receipt: dict[str, Any]) -> dict[str, Any]:
    """One listing row from a recorded start; unrecorded report paths stay null."""
    report = receipt.get("report")
    path = report.get("path") if isinstance(report, dict) else None
    canonical = report.get("canonicalPath") if isinstance(report, dict) else None
    if not isinstance(path, str):
        path = None
    if not isinstance(canonical, str):
        canonical = None
    created = receipt.get("createdAt")
    status = receipt.get("status")
    return {
        "role": str(receipt.get("role", "")),
        "requestId": str(receipt.get("requestId", "")),
        "createdAt": created if isinstance(created, str) else "",
        "status": status if isinstance(status, str) else None,
        "path": path,
        "canonicalPath": canonical,
        "available": canonical is not None and Path(canonical).is_file(),
    }


def list_task_reports(
    config: McpRuntimeConfig, repo_id: str, master: str, doc: str
) -> JSONResponse:
    """The role starts recorded for one task document, newest first, at most twenty.

    The rows come from what the product recorded at each start — the receipt files in
    the task's notes/reports execution directory and their history — never from a walk
    of the notes tree. A task with no recorded start answers an empty list, never an
    error; an unreadable record answers 409 so the page can say the reports could not
    be listed.
    """
    try:
        require_repo(config, repo_id)
    except AuthorityError:
        return JSONResponse({"status": "unknown-repo", "repo": repo_id}, status_code=404)
    if "/" in master or "\\" in master or not master or master.startswith("."):
        return JSONResponse(
            {"status": "bad-request", "detail": "reports need a single-segment master"},
            status_code=400,
        )
    # Dashboard doc paths carry the "/tasks/<repo>/" prefix while recorded references
    # are root-relative inside the repository task root; normalize before comparing.
    doc_rel = doc
    prefix = f"/tasks/{repo_id}/"
    if doc_rel.startswith(prefix):
        doc_rel = doc_rel[len(prefix) :]
    if not doc_rel or not doc_rel.startswith(f"{master}/") or ".." in doc_rel.split("/"):
        return JSONResponse(
            {"status": "bad-request", "detail": "reports need the task document under this master"},
            status_code=400,
        )
    store = (
        config.coordination_root
        / "tasks"
        / repo_id
        / master
        / "notes"
        / "reports"
        / EXECUTIONS_DIRECTORY
    )
    try:
        current = sorted(store.glob("*.json")) if store.is_dir() else []
        history_dir = store / "history"
        history = sorted(history_dir.glob("*.json")) if history_dir.is_dir() else []
    except OSError as err:
        return JSONResponse(
            {
                "status": "report-records-unreadable",
                "detail": f"The recorded role starts cannot be read: {err.strerror or err}",
            },
            status_code=409,
        )
    rows = _matching_receipt_rows(current, history, repo_id, doc_rel)
    if rows is None:
        return JSONResponse(
            {
                "status": "report-records-unreadable",
                "detail": "The recorded role starts cannot be read.",
            },
            status_code=409,
        )
    rows.sort(key=lambda row: (row["createdAt"], row["requestId"]), reverse=True)
    total = len(rows)
    shown = rows[:_TASK_REPORT_ROWS]
    return JSONResponse(
        {
            "repo": repo_id,
            "master": master,
            "doc": doc_rel,
            "reports": shown,
            "total": total,
            "explorer": f"reports/{REPORTS_DIRECTORY}" if total > len(shown) else None,
        },
        status_code=200,
    )


def _matching_receipt_rows(
    current: list[Path], history: list[Path], repo_id: str, doc_rel: str
) -> list[dict[str, Any]] | None:
    """The rows recorded for one task document, or ``None`` when a record is unreadable.

    Each receipt names its own ``{repository, path}`` task reference; only receipts naming
    this document become rows. One row per ``(requestId, createdAt)``: a start kept both
    current and in history is not counted twice.
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in current + history:
        try:
            receipt = _read_receipt(path)
        except (HTTPException, OSError, ValueError):
            return None
        if receipt is None:
            continue
        ref = _receipt_selection_doc(receipt)
        if ref is None or ref != {"repository": repo_id, "path": doc_rel}:
            continue
        key = f"{receipt.get('requestId', '')}\0{receipt.get('createdAt', '')}"
        if key in seen:
            continue
        seen.add(key)
        rows.append(_receipt_row(receipt))
    return rows
