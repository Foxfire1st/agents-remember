"""Conditional body reads retain preparation only while every native input agrees."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agents_remember.kernel.direct_execution_grants import (
    ARCHITECT_GRANTER,
    DirectExecutionGrant,
    grant_path_for,
    write_grant_file,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving._app_common import _ServingRuntime
from agents_remember.serving._app_routes import _register_projection_routes, _task_document_response
from agents_remember.serving.projections import task_body_generation
from agents_remember.serving.projections.snapshots_impl import _task_documents
from agents_remember.serving.task_document_cache import TaskDocumentBodyCache
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from test_observer_projection import FRESH
from test_task_document_body_lookup import _corpus, _write


def _runtime(root: Path, **bounds: int) -> _ServingRuntime:
    return _ServingRuntime(
        config=cast(Any, SimpleNamespace(coordination_root=root)),
        projector=cast(Any, SimpleNamespace(current=lambda: (1, object()))),
        clock=lambda: FRESH,
        build=cast(Any, None),
        interval=0.1,
        task_bodies=TaskDocumentBodyCache(**bounds),
    )


def test_repeated_conditional_read_precedes_native_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path / "tasks/repo-a/m/01.json")
    runtime = _runtime(tmp_path)
    first = _task_document_response(runtime, str(path))
    assert first.status_code == 200
    assert json.loads(bytes(first.body))["objective"] == "Objective of 01"
    assert {
        "id",
        "title",
        "status",
        "repository",
        "docPath",
        "stepsDone",
        "stepsTotal",
        "subTasks",
    }.isdisjoint(json.loads(bytes(first.body)))

    def unexpected_parse(*args: object) -> None:
        pytest.fail("unchanged conditional read parsed a document")

    monkeypatch.setattr(_task_documents, "_projected_document", unexpected_parse)
    second = _task_document_response(runtime, str(path), first.headers["etag"])
    assert second.status_code == 304 and second.body == b""
    assert second.headers["etag"] == first.headers["etag"]


@pytest.mark.parametrize("change", ["restored-mtime", "replacement", "symlink", "deleted"])
def test_conditional_read_invalidates_native_file_changes(tmp_path: Path, change: str) -> None:
    path = _write(tmp_path / "tasks/repo-a/m/01.json")
    runtime = _runtime(tmp_path)
    first = _task_document_response(runtime, str(path))
    before = path.stat()
    changed = path.read_text().replace("Objective of 01", "Changed text 01")
    assert len(changed) == before.st_size
    if change == "restored-mtime":
        path.write_text(changed)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    elif change == "replacement":
        replacement = path.with_suffix(".new")
        replacement.write_text(changed)
        replacement.replace(path)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    elif change == "symlink":
        target = path.with_name("target.json")
        target.write_text(changed)
        path.unlink()
        path.symlink_to(target)
    else:
        path.unlink()
        with pytest.raises(HTTPException) as refusal:
            _task_document_response(runtime, str(path), first.headers["etag"])
        assert refusal.value.status_code == 404
        return
    second = _task_document_response(runtime, str(path), first.headers["etag"])
    assert second.status_code == 200 and second.headers["etag"] != first.headers["etag"]
    assert json.loads(bytes(second.body))["objective"] == "Changed text 01"


def test_graph_body_invalidates_changed_and_new_native_master_inputs(tmp_path: Path) -> None:
    paths = _corpus(tmp_path, unrelated=0)
    runtime = _runtime(tmp_path)
    first = _task_document_response(runtime, str(paths["sprint"]))
    paths["master-a"].write_text(paths["master-a"].read_text().replace("Master A", "Master C"))
    second = _task_document_response(runtime, str(paths["sprint"]), first.headers["etag"])
    assert second.status_code == 200
    assert "Master C" in bytes(second.body).decode()
    _write(tmp_path / "tasks/repo-a/missing/task.json", kind="master", title="Now authored")
    third = _task_document_response(runtime, str(paths["sprint"]), second.headers["etag"])
    assert third.status_code == 200
    assert "Now authored" in bytes(third.body).decode()


def test_body_invalidates_grant_addition_edit_and_removal(tmp_path: Path) -> None:
    path = _write(tmp_path / "tasks/repo-a/m/01.json")
    runtime = _runtime(tmp_path)
    first = _task_document_response(runtime, str(path))
    ref = TaskDocumentRef(repository="repo-a", path="m/01.json")
    grant_path = grant_path_for(tmp_path, ref)
    write_grant_file(
        grant_path,
        DirectExecutionGrant(
            leaf=ref,
            granted_by=ARCHITECT_GRANTER,
            granted_at="2026-10-06T00:00:00+00:00",
            granter_agent_id="test",
        ),
    )
    second = _task_document_response(runtime, str(path), first.headers["etag"])
    assert second.status_code == 200 and json.loads(bytes(second.body))["directGrant"]
    grant_path.write_text(grant_path.read_text().replace("2026-10-06", "2026-10-07"))
    third = _task_document_response(runtime, str(path), second.headers["etag"])
    assert third.status_code == 200
    assert json.loads(bytes(third.body))["directGrant"]["grantedAt"].startswith("2026-10-07")
    grant_path.unlink()
    fourth = _task_document_response(runtime, str(path), third.headers["etag"])
    assert fourth.status_code == 200 and "directGrant" not in json.loads(bytes(fourth.body))


def test_unreadable_metadata_uses_native_uncached_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path / "tasks/repo-a/m/01.json")
    runtime = _runtime(tmp_path)
    first = _task_document_response(runtime, str(path))
    native_identity = task_body_generation._identity
    monkeypatch.setattr(
        task_body_generation,
        "_identity",
        lambda item: None if item == path else native_identity(item),
    )
    second = _task_document_response(runtime, str(path), first.headers["etag"])
    assert second.status_code == 200 and second.body == first.body
    assert runtime.task_bodies.retained == (0, 0)


def test_changing_native_read_is_not_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path / "tasks/repo-a/m/01.json")
    runtime = _runtime(tmp_path)
    native_read = _task_documents._read_json

    def read_and_change(item: Path) -> dict[str, object] | None:
        payload = native_read(item)
        if item == path:
            path.write_text(path.read_text().replace("Objective of 01", "Changed text 01"))
        return payload

    monkeypatch.setattr(_task_documents, "_read_json", read_and_change)
    first = _task_document_response(runtime, str(path))
    assert first.status_code == 200 and runtime.task_bodies.retained == (0, 0)
    second = _task_document_response(runtime, str(path), first.headers["etag"])
    assert second.status_code == 200 and "Changed text 01" in bytes(second.body).decode()


def test_body_lru_has_count_and_byte_bounds(tmp_path: Path) -> None:
    paths = [_write(tmp_path / f"tasks/repo-a/m/{index}.json") for index in range(3)]
    runtime = _runtime(tmp_path, max_entries=2)
    for path in paths:
        assert _task_document_response(runtime, str(path)).status_code == 200
    assert runtime.task_bodies.retained[0] == 2
    # Eviction changes no native read semantics; an edited oldest body is fetched afresh.
    paths[0].write_text(paths[0].read_text().replace("Objective", "Revisited"))
    assert "Revisited" in bytes(_task_document_response(runtime, str(paths[0])).body).decode()
    runtime = _runtime(tmp_path, max_bytes=1)
    assert _task_document_response(runtime, str(paths[0])).status_code == 200
    assert runtime.task_bodies.retained == (0, 0)


def test_real_http_body_route_revalidates_without_reparsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path / "tasks/repo-a/m/01.json")
    app = FastAPI()
    _register_projection_routes(app, _runtime(tmp_path))

    async def request_pair() -> None:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://task-body") as client:
            first = await client.get("/api/task-document", params={"path": "repo-a/m/01.json"})
            assert first.status_code == 200 and first.json()["bodyRevision"]
            assert first.headers["cache-control"] == "no-cache"

            def unexpected_parse(*args: object) -> None:
                pytest.fail("real HTTP conditional route reparsed the unchanged body")

            with monkeypatch.context() as frozen:
                frozen.setattr(_task_documents, "_projected_document", unexpected_parse)
                second = await client.get(
                    "/api/task-document",
                    params={"path": "repo-a/m/01.json"},
                    headers={"If-None-Match": first.headers["etag"]},
                )
            assert second.status_code == 304 and second.content == b""
            path.write_text(path.read_text().replace("Objective of 01", "Changed text 01"))
            third = await client.get(
                "/api/task-document",
                params={"path": "repo-a/m/01.json"},
                headers={"If-None-Match": first.headers["etag"]},
            )
            assert third.status_code == 200 and third.json()["objective"] == "Changed text 01"

    asyncio.run(request_pair())


def test_symlink_retarget_stays_inside_native_body_boundary(tmp_path: Path) -> None:
    path = _write(tmp_path / "tasks/repo-a/m/01.json")
    target = _write(tmp_path / "tasks/repo-a/m/02.json", objective="Second target")
    link = path.with_name("reader.json")
    link.symlink_to(path)
    runtime = _runtime(tmp_path)
    first = _task_document_response(runtime, str(link))
    link.unlink()
    link.symlink_to(target)
    second = _task_document_response(runtime, str(link), first.headers["etag"])
    assert (
        second.status_code == 200 and json.loads(bytes(second.body))["objective"] == "Second target"
    )
    outside = _write(tmp_path / "outside.json")
    link.unlink()
    link.symlink_to(outside)
    with pytest.raises(HTTPException) as refusal:
        _task_document_response(runtime, str(link), second.headers["etag"])
    assert refusal.value.status_code == 404
