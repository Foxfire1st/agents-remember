from __future__ import annotations

import hashlib
import inspect
import json
import os
import stat
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from agents_remember.application.role_launch_context import (
    resolve_role_launch_context,
    selection_binding,
)
from agents_remember.cli.role_launch_preparation import _role_report_path
from agents_remember.cli.role_launch_receipts import (
    RECEIPT_SCHEMA,
    _bind_task_report_access,
    _legacy_receipt_path,
    _receipt_path,
)
from agents_remember.cli.role_launch_routes import register_role_launch_routes
from agents_remember.cli.role_report import RoleReportRequest
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient


@pytest.fixture
def config(tmp_path: Path) -> McpRuntimeConfig:
    config = McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path / "coordination",
        workspace_root=tmp_path / "projects",
        transcript_root=tmp_path / "logs",
        repositories={"demo": RepositoryScope(repo_id="demo", path=tmp_path / "code")},
    )
    config.workspace_root.mkdir(parents=True)
    for relative, fields in [
        ("sprint/task.json", {"id": "SPRINT", "kind": "master", "orchestrates": ["master"]}),
        (
            "master/task.json",
            {
                "id": "MASTER",
                "kind": "master",
                "subTasks": [
                    {"number": "L1", "name": "Leaf", "file": "leaf.md", "status": "inProgress"}
                ],
            },
        ),
        ("master/leaf.json", {"id": "L1", "kind": "subTask", "master": "task.md"}),
    ]:
        path = config.coordination_root / "tasks" / "demo" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema": "ar-task-document/v1",
                    "slug": path.stem,
                    "title": path.stem,
                    "repo": "demo",
                    "type": "Test",
                    "createdAt": "2026-10-05",
                    "status": "inProgress",
                    **fields,
                }
            )
        )
    return config


def recorded(config: McpRuntimeConfig, role: str = "architect"):
    body: dict[str, Any] = {"role": role, "requestId": str(uuid.uuid4())}
    if role not in {"architect", "investigator"}:
        body["sprintDocumentRef"] = {"repository": "demo", "path": "sprint/task.json"}
    if role in {"manager", "worker", "reviewer", "curator"}:
        body["masterDocumentRef"] = {"repository": "demo", "path": "master/task.json"}
    if role in {"worker", "reviewer", "curator"}:
        body["taskDocumentRef"] = {"repository": "demo", "path": "master/leaf.json"}
    request = RoleReportRequest.model_validate(body)
    context = resolve_role_launch_context(config, request)
    workspace = {"path": str(config.workspace_root)}
    if role in {"worker", "reviewer", "curator"}:
        reports = config.coordination_root / "tasks" / "demo" / "master" / "notes" / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        workspace["taskReportAccessRoot"] = str(
            _bind_task_report_access(config.workspace_root, reports)
        )
    report = Path(_role_report_path(context, workspace, request_id=request.request_id))
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "role": role,
        "requestId": body["requestId"],
        "selection": selection_binding(request),
        "report": {"path": str(report), "canonicalPath": str(report.resolve())},
    }
    address = _receipt_path(config, request, request.request_id)
    address.parent.mkdir(parents=True, exist_ok=True)
    address.write_text(json.dumps(receipt))
    app = FastAPI()
    register_role_launch_routes(app, config)
    return TestClient(app), body, report, address, receipt


def tree_snapshot(root: Path):
    rows = {}
    for path in [root, *sorted(root.rglob("*"))]:
        info = path.lstat()
        content = (
            hashlib.sha256(path.read_bytes()).hexdigest()
            if stat.S_ISREG(info.st_mode)
            else os.readlink(path)
            if path.is_symlink()
            else None
        )
        rows[str(path.relative_to(root))] = (
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
            content,
        )
    return rows


@pytest.mark.parametrize(
    "role",
    ["architect", "investigator", "orchestrator", "manager", "worker", "reviewer", "curator"],
)
def test_report_of_every_launchable_role_is_read_only(config, role):
    client, body, report, _address, receipt = recorded(config, role)
    report.write_text("# The selected report\n")
    if role in {"architect", "investigator"}:
        legacy = _legacy_receipt_path(config, RoleReportRequest.model_validate(body))
        legacy.write_text(json.dumps({**receipt, "requestId": str(uuid.uuid4())}))
    before = tree_snapshot(config.workspace_root.parent)
    response = client.post("/api/role-launch/report", json=body)
    assert response.status_code == 200
    assert response.json() == {
        "path": report.name,
        "language": "markdown",
        "size": 22,
        "truncated": False,
        "content": "# The selected report\n",
    }
    assert tree_snapshot(config.workspace_root.parent) == before


def test_signature_and_attempted_path_cannot_select_another_file(config):
    client, body, report, _, _ = recorded(config)
    report.write_text("selected")
    other = report.with_name("another-report.md")
    other.write_text("must not be served")
    app = client.app
    assert isinstance(app, FastAPI)
    route = next(
        route for route in app.routes if getattr(route, "path", None) == "/api/role-launch/report"
    )
    assert isinstance(route, APIRoute)
    assert list(inspect.signature(route.endpoint).parameters) == ["request"]
    assert set(RoleReportRequest.model_fields) == {
        "role",
        "request_id",
        "sprint_document_ref",
        "master_document_ref",
        "task_document_ref",
    }
    assert (
        client.post("/api/role-launch/report", json={**body, "path": str(other)}).status_code == 422
    )
    assert client.post("/api/role-launch/report", json={"role": "architect"}).status_code == 422
    assert (
        client.post("/api/role-launch/report?path=" + str(other), json=body).json()["content"]
        == "selected"
    )
    assert client.get("/api/role-launch/report").status_code == 405


@pytest.mark.parametrize(
    "failure",
    ["missing", "unreadable", "directory", "outside", "symlink", "inside-alias", "access-link"],
)
@pytest.mark.parametrize("role", ["architect", "worker"])
def test_report_refusals_are_named(config, failure, role):
    client, body, report, address, receipt = recorded(config, role)
    if failure == "directory":
        report.mkdir()
    elif failure == "unreadable":
        report.write_bytes(b"\xff")
    elif failure in {"outside", "symlink", "inside-alias", "access-link"}:
        outside = (
            report.with_name("other.md")
            if failure == "inside-alias"
            else config.workspace_root / "other.md"
        )
        outside.write_text("outside the report root")
        if failure == "outside":
            receipt["report"] = {"path": str(outside), "canonicalPath": str(outside)}
        elif failure in {"symlink", "inside-alias"}:
            report.symlink_to(outside)
        else:
            receipt["report"]["path"] = str(outside)
        address.write_text(json.dumps(receipt))
    response = client.post("/api/role-launch/report", json=body)
    assert response.status_code == (
        404
        if failure == "missing"
        else 409
        if failure in {"unreadable", "directory", "access-link"}
        else 403
    )
    expected = {
        "missing": {"status": "report-not-written", "detail": "The report is not written yet."},
        "unreadable": {
            "status": "report-unreadable",
            "detail": "The report cannot be read. Check its access and text encoding, then retry.",
        },
        "directory": {
            "status": "report-unreadable",
            "detail": "The recorded report is not a readable regular file.",
        },
        "outside": {
            "status": "report-outside-root",
            "detail": "The recorded report lies outside this execution's report root.",
        },
        "symlink": {
            "status": "report-outside-root",
            "detail": "The recorded report lies outside this execution's report root.",
        },
        "inside-alias": {
            "status": "report-outside-root",
            "detail": "The recorded report path no longer matches its report root.",
        },
        "access-link": {
            "status": "report-access-retargeted",
            "detail": "The report access path points elsewhere. Restore its recorded target and retry.",
        },
    }
    assert response.json() == expected[failure]


def test_permission_failure_is_an_unreadable_report(config):
    client, body, report, _, _ = recorded(config)
    report.write_text("selected")
    original = Path.open

    def open_file(path, *args, **kwargs):
        if path == report:
            raise PermissionError("denied")
        return original(path, *args, **kwargs)

    with patch.object(Path, "open", open_file):
        response = client.post("/api/role-launch/report", json=body)
    assert response.status_code == 409
    assert response.json()["status"] == "report-unreadable"


@pytest.mark.parametrize("role", ["architect", "worker"])
def test_another_execution_or_selection_cannot_read_the_report(config, role):
    client, body, report, address, receipt = recorded(config, role)
    report.write_text("private to this execution")
    assert client.post(
        "/api/role-launch/report", json={**body, "requestId": str(uuid.uuid4())}
    ).status_code in {404, 409}
    receipt["selection"]["role"] = "reviewer"
    address.write_text(json.dumps(receipt))
    response = client.post("/api/role-launch/report", json=body)
    assert response.status_code == 409
    assert response.json()["status"] == "report-execution-mismatch"


@pytest.mark.parametrize("size", [32, 2 * 1024 * 1024 + 64])
def test_report_read_is_bounded(config, size):
    client, body, report, _, _ = recorded(config)
    report.write_bytes(b"a" * size)
    reads = []
    original = Path.open

    class BoundedReader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, count=-1):
            assert 0 <= count <= 2 * 1024 * 1024 + 4, (
                "The report read must be bounded before decoding."
            )
            reads.append(count)
            return self.stream.read(count)

    def open_file(path, *args, **kwargs):
        stream = original(path, *args, **kwargs)
        return BoundedReader(stream) if path == report.resolve() else stream

    with patch.object(Path, "open", open_file):
        value = client.post("/api/role-launch/report", json=body).json()
    assert reads == [2 * 1024 * 1024 + 4]
    assert value["size"] == size
    assert len(value["content"]) == min(size, 2 * 1024 * 1024)
    assert value["truncated"] is (size > 2 * 1024 * 1024)


def test_unregistered_repository_and_incomplete_selection_are_refused(config):
    client, body, report, _, _ = recorded(config, "worker")
    report.write_text("selected")
    incomplete = {key: value for key, value in body.items() if key != "masterDocumentRef"}
    response = client.post("/api/role-launch/report", json=incomplete)
    assert response.status_code == 409
    assert response.json()["status"] == "report-selection-invalid"
    config.repositories.clear()
    response = client.post("/api/role-launch/report", json=body)
    assert response.status_code == 409
    assert response.json()["status"] == "report-selection-invalid"
    assert "content" not in response.json()


@pytest.mark.parametrize("role", ["architect", "worker"])
def test_a_fifo_is_refused_before_opening_it(config, role):
    client, body, report, _, _ = recorded(config, role)
    os.mkfifo(report)
    original = Path.open

    def open_file(path, *args, **kwargs):
        assert path != report.resolve(), "A FIFO must be rejected before attempting to open it."
        return original(path, *args, **kwargs)

    before = tree_snapshot(config.workspace_root.parent)
    with patch.object(Path, "open", open_file):
        response = client.post("/api/role-launch/report", json=body)
    assert response.status_code == 409
    assert response.json() == {
        "status": "report-unreadable",
        "detail": "The recorded report is not a readable regular file.",
    }
    assert tree_snapshot(config.workspace_root.parent) == before


@pytest.mark.parametrize("role", ["architect", "worker"])
@pytest.mark.parametrize("location", ["other-role", "above-report-root"])
def test_a_recorded_path_in_another_role_or_parent_folder_is_refused(config, role, location):
    client, body, report, address, receipt = recorded(config, role)
    root = report.resolve().parent
    other_root = (
        (config.workspace_root / ".agents-remember/reports/role-launch/investigator")
        if location == "other-role"
        else root.parent
    )
    other_root.mkdir(parents=True, exist_ok=True)
    other = other_root / report.name
    other.write_text("not the selected role's report")
    receipt["report"] = {"path": str(other), "canonicalPath": str(other)}
    address.write_text(json.dumps(receipt))
    before = tree_snapshot(config.workspace_root.parent)
    response = client.post("/api/role-launch/report", json=body)
    assert response.status_code == 403
    assert response.json() == {
        "status": "report-outside-root",
        "detail": "The recorded report lies outside this execution's report root.",
    }
    assert tree_snapshot(config.workspace_root.parent) == before


@pytest.mark.parametrize("role", ["architect", "worker"])
@pytest.mark.parametrize("other_file", ["sibling", "handover", "subfolder"])
def test_a_receipt_cannot_replace_the_deterministic_report_file(config, role, other_file):
    client, body, report, address, receipt = recorded(config, role)
    root = report.resolve().parent
    other = root / (
        "sibling.md"
        if other_file == "sibling"
        else report.stem + ".handover.txt"
        if other_file == "handover"
        else "subfolder/" + report.name
    )
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("another file")
    receipt["report"] = {"path": str(other), "canonicalPath": str(other)}
    address.write_text(json.dumps(receipt))
    response = client.post("/api/role-launch/report", json=body)
    assert response.status_code == 403
    assert response.json() == {
        "status": "report-path-mismatch",
        "detail": "The recorded file is not the report named by this execution.",
    }


@pytest.mark.parametrize(
    "fault", ["malformed", "invalid-schema", "directory", "linked", "unreadable"]
)
def test_damaged_receipts_have_a_receipt_specific_refusal(config, fault):
    client, body, report, address, receipt = recorded(config)
    report.write_text("written")
    if fault == "malformed":
        address.write_text("not JSON")
    elif fault == "invalid-schema":
        address.write_text("{}")
    elif fault == "directory":
        address.unlink()
        address.mkdir()
    elif fault == "linked":
        target = address.with_name("linked-receipt.json")
        target.write_text(json.dumps(receipt))
        address.unlink()
        address.symlink_to(target)
    original = Path.read_text

    def read_text(path, *args, **kwargs):
        if fault == "unreadable" and path == address:
            raise PermissionError("cannot read the receipt")
        return original(path, *args, **kwargs)

    before = tree_snapshot(config.workspace_root.parent)
    with patch.object(Path, "read_text", read_text):
        response = client.post("/api/role-launch/report", json=body)
    assert response.status_code == 409
    assert response.json() == {
        "status": "report-receipt-invalid",
        "detail": "The report's saved execution receipt is unreadable or invalid. Restore the receipt and retry.",
    }
    assert tree_snapshot(config.workspace_root.parent) == before


@pytest.mark.parametrize("role", ["worker", "reviewer", "curator"])
@pytest.mark.parametrize("alias_state", ["absent", "retargeted", "replaced"])
def test_canonical_report_survives_absent_alias_and_names_a_retarget(config, role, alias_state):
    client, body, report, _, _ = recorded(config, role)
    canonical = report.resolve()
    canonical.write_text("canonical report")
    alias = report.parent.parent
    alias.unlink()
    if alias_state == "retargeted":
        target = config.workspace_root / "elsewhere"
        target.mkdir()
        alias.symlink_to(target, target_is_directory=True)
    elif alias_state == "replaced":
        alias.mkdir()
    before = tree_snapshot(config.workspace_root.parent)
    response = client.post("/api/role-launch/report", json=body)
    if alias_state == "absent":
        assert response.status_code == 200
        assert response.json()["content"] == "canonical report"
        assert response.json()["path"] == canonical.name
    else:
        assert response.status_code == 409
        assert response.json() == {
            "status": "report-access-retargeted",
            "detail": "The report access path points elsewhere. Restore its recorded target and retry.",
        }
    assert tree_snapshot(config.workspace_root.parent) == before
