"""The per-task role-start listing (``GET /api/role-launch/reports`` in ``cli/role_report.py``).

MIK-R126 rule 5: a task page shows the reports of the role agents the product started
for that task — newest first, at most twenty — found by what the product recorded at
each start, never by a walk of the tree. These cases pin the ordering, the cap, the
explorer link, the task filter, and the refusal behaviour the page relies on.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_remember.cli.role_report import _TASK_REPORT_ROWS, register_role_report_route
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = "R"
MASTER = "260101_series"
DOC = f"{MASTER}/126_task.json"
OTHER_DOC = f"{MASTER}/127_other.json"


def _client(tmp_path: Path) -> TestClient:
    code_root = tmp_path / "ws" / REPO
    code_root.mkdir(parents=True, exist_ok=True)
    config = McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path / "coord",
        workspace_root=tmp_path / "ws",
        transcript_root=tmp_path / "logs",
        repositories={REPO: RepositoryScope(repo_id=REPO, path=code_root)},
    )
    app = FastAPI()
    register_role_report_route(app, config)
    return TestClient(app)


def _store(tmp_path: Path) -> Path:
    store = (
        tmp_path
        / "coord"
        / "tasks"
        / REPO
        / MASTER
        / "notes"
        / "reports"
        / "paseo-native-executions"
    )
    store.mkdir(parents=True, exist_ok=True)
    (store / "history").mkdir(exist_ok=True)
    return store


def _receipt(
    role: str,
    request_id: str,
    created: str,
    report: dict[str, Any] | None = None,
    **over: Any,
) -> dict[str, Any]:
    return {
        "schema": "ar-role-execution/v1",
        "requestId": request_id,
        "role": role,
        "status": over.get("status", "completed"),
        "createdAt": created,
        "selection": {"taskDocumentRef": {"repository": REPO, "path": over.get("doc", DOC)}},
        **({"report": report} if report is not None else {}),
    }


def _write_receipt(store: Path, name: str, receipt: dict[str, Any], history: bool = False) -> None:
    target = store / ("history" if history else "") / name if history else store / name
    target.write_text(json.dumps(receipt), encoding="utf-8")


def _report_file(tmp_path: Path, name: str) -> str:
    path = tmp_path / "coord" / "tasks" / REPO / MASTER / "notes" / "reports" / "role-launch" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"report")
    return path.as_posix()


def _listing(client: TestClient, params: dict[str, Any]) -> Any:
    return client.get("/api/role-launch/reports", params=params)


def test_newest_first_and_task_filter(tmp_path: Path) -> None:
    """Rows newest-first by createdAt; another task's starts and taskless starts never leak in."""
    store = _store(tmp_path)
    canon = _report_file(tmp_path, "L126-worker-aaa.md")
    _write_receipt(
        store,
        "worker-1.json",
        _receipt(
            "worker",
            "aaa",
            "2026-10-01T00:00:00+00:00",
            report={"path": "x", "canonicalPath": canon},
        ),
    )
    _write_receipt(
        store,
        "reviewer-1.json",
        _receipt(
            "reviewer",
            "bbb",
            "2026-10-03T00:00:00+00:00",
            report={"path": "y", "canonicalPath": canon},
        ),
    )
    _write_receipt(
        store,
        "worker-old.json",
        _receipt(
            "worker",
            "ccc",
            "2026-09-01T00:00:00+00:00",
            report={"path": "z", "canonicalPath": canon},
        ),
        history=True,
    )
    _write_receipt(
        store,
        "other-task.json",
        _receipt(
            "worker",
            "ddd",
            "2026-10-09T00:00:00+00:00",
            doc=OTHER_DOC,
            report={"path": "w", "canonicalPath": canon},
        ),
    )
    _write_receipt(
        store,
        "manager.json",
        {
            "schema": "ar-role-execution/v1",
            "requestId": "eee",
            "role": "manager",
            "status": "completed",
            "createdAt": "2026-10-09T00:00:00+00:00",
            "selection": {"masterDocumentRef": {"repository": REPO, "path": f"{MASTER}/task.json"}},
        },
    )
    with _client(tmp_path) as client:
        response = _listing(client, {"repo": REPO, "master": MASTER, "doc": DOC})
    assert response.status_code == 200
    body = response.json()
    assert [row["requestId"] for row in body["reports"]] == ["bbb", "aaa", "ccc"]
    assert body["total"] == 3 and body["explorer"] is None
    assert all(row["available"] is True for row in body["reports"])
    # Dashboard doc paths carry the /tasks/<repo>/ prefix; they match the same records.
    with _client(tmp_path) as client:
        prefixed = _listing(
            client, {"repo": REPO, "master": MASTER, "doc": f"/tasks/{REPO}/{DOC}"}
        ).json()
    assert prefixed["doc"] == DOC
    assert [row["requestId"] for row in prefixed["reports"]] == ["bbb", "aaa", "ccc"]


def test_cap_of_twenty_with_explorer_link(tmp_path: Path) -> None:
    """Twenty rows at most; the total counts every start and the explorer link appears
    only when more starts exist than the rows show."""
    assert _TASK_REPORT_ROWS == 20
    store = _store(tmp_path)
    canon = _report_file(tmp_path, "L126-worker-x.md")
    for index in range(22):
        _write_receipt(
            store,
            f"worker-{index:02d}.json",
            _receipt(
                "worker",
                f"id-{index:02d}",
                f"2026-10-{index + 1:02d}T00:00:00+00:00",
                report={"path": "p", "canonicalPath": canon},
            ),
        )
    with _client(tmp_path) as client:
        body = _listing(client, {"repo": REPO, "master": MASTER, "doc": DOC}).json()
    assert len(body["reports"]) == 20 and body["total"] == 22
    assert body["reports"][0]["requestId"] == "id-21"
    assert body["explorer"] == "reports/role-launch"


def test_empty_without_starts_and_nulls_without_reports(tmp_path: Path) -> None:
    """A task with no recorded start answers an empty list, never an error; a start
    with no recorded report keeps null paths and available false."""
    store = _store(tmp_path)
    _write_receipt(store, "worker-1.json", _receipt("worker", "aaa", "2026-10-01T00:00:00+00:00"))
    (tmp_path / "coord" / "tasks" / REPO / MASTER / "notes").mkdir(parents=True, exist_ok=True)
    with _client(tmp_path) as client:
        empty = _listing(client, {"repo": REPO, "master": MASTER, "doc": OTHER_DOC}).json()
        assert empty["reports"] == [] and empty["total"] == 0 and empty["explorer"] is None
        row = _listing(client, {"repo": REPO, "master": MASTER, "doc": DOC}).json()["reports"][0]
        assert row["path"] is None and row["canonicalPath"] is None and row["available"] is False


def test_refusals(tmp_path: Path) -> None:
    """Unknown repo, multi-segment master and a document outside this master refuse;
    a malformed receipt answers 409 so the page can say the reports could not be listed."""
    store = _store(tmp_path)
    (store / "broken.json").write_text("{not json", encoding="utf-8")
    with _client(tmp_path) as client:
        assert _listing(client, {"repo": "nope", "master": MASTER, "doc": DOC}).status_code == 404
        assert _listing(client, {"repo": REPO, "master": "a/b", "doc": DOC}).status_code == 400
        assert (
            _listing(client, {"repo": REPO, "master": MASTER, "doc": "other/x.json"}).status_code
            == 400
        )
        bad = _listing(client, {"repo": REPO, "master": MASTER, "doc": DOC})
        assert bad.status_code == 409 and bad.json()["status"] == "report-records-unreadable"
