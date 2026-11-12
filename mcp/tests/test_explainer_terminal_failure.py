"""Terminal pre-create failures are durable without pretending a seat was created."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.canvas import explainer_service
from agents_remember.canvas.explainer_maintenance import seat_state
from agents_remember.canvas.explainer_service import ExplainerRefused, build_seat
from agents_remember.canvas.explainer_state import (
    MAX_LEDGER_ENTRIES,
    latest_build_failure,
    ledger_entry,
    ledger_path,
    read_inflight_create,
    read_ledger,
    record_request,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from canvas_host_test_support import LEAF, record
from test_explainer_service import FakeHost, _deps, _scope, _world


def path_for(runtime, scope):
    return ledger_path(runtime, scope.repository_id, scope.task, scope.leaf_id)


@pytest.mark.parametrize("stage", ["comparison", "packet", "checkout-code", "recheck"])
def test_precreate_failure_records_stage_and_reason_without_seat_or_delivery(
    tmp_path, monkeypatch, stage
):
    runtime, bound = _world(tmp_path)
    host = FakeHost()
    scope = _scope()
    deps = _deps(runtime, host, bound, comparison=record(7))

    def fail_comparison(*_args):
        raise ExplainerRefused("evidence-unavailable", "comparison detail")

    def fail_checkout(_deps, _scope, _bound, _prepared, progress):
        progress("checkout-code", {"state": "running"})
        raise ExplainerRefused("evidence-unavailable", "checkout detail")

    if stage == "comparison":
        deps = replace(deps, comparison=fail_comparison)
    elif stage == "packet":
        deps = replace(deps, prepare=fail_comparison)
    elif stage == "checkout-code":
        monkeypatch.setattr(explainer_service, "_build_environment", fail_checkout)
    else:
        deps = replace(deps, recheck=lambda *_args: False)
    with pytest.raises(ExplainerRefused) as refused:
        build_seat(deps, scope, request_id="failed-first", message="explain")
    path = path_for(runtime, scope)
    row = ledger_entry(path, scope.leaf_id, "failed-first")
    assert row is not None
    assert row["state"] == row["outcome"] == "failed"
    assert row["beforeCreate"] is True
    assert row["failedStep"] == row["step"] == stage
    assert row["generation"] == 0
    assert row["reason"] == ("binding-moved" if stage == "recheck" else "evidence-unavailable")
    assert row["error"] == str(refused.value)
    assert row["at"] == deps.stamp()
    assert not {"agentId", "messageId", "delivery"}.intersection(row)
    assert read_inflight_create(path) is None
    assert host.created == host.sent == []
    assert latest_build_failure(path, 0) == row
    assert latest_build_failure(path, 1) is None
    assert seat_state(deps, scope).as_record()["building"] == row


def test_failure_is_readable_by_a_fresh_process_from_existing_ledger(tmp_path):
    runtime, bound = _world(tmp_path)
    scope = _scope()
    deps = _deps(runtime, FakeHost(), bound, comparison=record(7))

    def refused(*_args):
        raise ExplainerRefused("evidence-unavailable", "persistent comparison detail")

    with pytest.raises(ExplainerRefused):
        build_seat(replace(deps, comparison=refused), scope, request_id="cross-process")
    script = """
import json, sys
from pathlib import Path
from types import SimpleNamespace
from agents_remember.canvas.explainer_maintenance import seat_state
from agents_remember.canvas.explainer_service import LeafScope
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
values = json.loads(sys.argv[1])
repositories = {
    key: RepositoryScope(
        repo_id=key, path=Path(value['path']),
        memory_root=None if value['memory_root'] is None else Path(value['memory_root']),
    ) for key, value in values['repositories'].items()
}
config = McpRuntimeConfig(
    **{key: Path(value) for key, value in values['config'].items()}, repositories=repositories
)
class NoHost:
    def seat_state(self, _seat):
        raise AssertionError('pre-create failure must not consult a created seat')
state = seat_state(SimpleNamespace(config=config, host=NoHost()), LeafScope(**values['scope']))
print(json.dumps(state.as_record()['building']))
"""
    values = {
        "config": {
            key: str(getattr(runtime, key))
            for key in ("workspace_root", "coordination_root", "config_path", "transcript_root")
        },
        "repositories": {
            key: {
                "path": str(repository.path),
                "memory_root": None
                if repository.memory_root is None
                else str(repository.memory_root),
            }
            for key, repository in runtime.repositories.items()
        },
        "scope": {
            "repository_id": scope.repository_id,
            "task": scope.task,
            "leaf_id": scope.leaf_id,
        },
    }
    result = subprocess.run(
        [sys.executable, "-c", script, json.dumps(values)],
        env={
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(Path(explainer_service.__file__).parents[2]),
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=HANG_GUARD_SECONDS,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == latest_build_failure(path_for(runtime, scope), 0)


def test_same_request_can_retry_after_precreate_failure_then_deduplicates_success(tmp_path):
    runtime, bound = _world(tmp_path)
    host = FakeHost()
    scope = _scope()
    deps = _deps(runtime, host, bound, comparison=record(7))

    def refused(*_args):
        raise ExplainerRefused("evidence-unavailable", "first attempt only")

    with pytest.raises(ExplainerRefused):
        build_seat(replace(deps, comparison=refused), scope, request_id="retry-same")
    first = build_seat(deps, scope, request_id="retry-same")
    second = build_seat(deps, scope, request_id="retry-same")
    assert not first.duplicate and second.duplicate
    assert len(host.created) == len(host.sent) == 1
    assert latest_build_failure(path_for(runtime, scope), first.generation) is None
    assert "latestBuildFailure" not in read_ledger(path_for(runtime, scope))


def test_retirement_and_later_normal_request_keep_old_failure_from_returning(tmp_path):
    path = tmp_path / "requests.json"
    row = {
        "state": "failed",
        "outcome": "failed",
        "beforeCreate": True,
        "generation": 0,
        "at": "2026-10-25T10:00:00+00:00",
        "step": "comparison",
        "failedStep": "comparison",
        "reason": "missing",
        "error": "original error",
    }
    record_request(path, LEAF, "old", row)
    assert latest_build_failure(path, 0, "2026-10-25T09:59:00Z") is not None
    assert latest_build_failure(path, 0, "2026-10-25T10:00:00Z") is None
    assert latest_build_failure(path, 0, "2026-10-25T10:01:00Z") is None
    record_request(path, LEAF, "new", {"generation": 1, "delivery": "delivered", "agentId": "seat"})
    assert ledger_entry(path, LEAF, "old") is not None
    assert latest_build_failure(path, 0) is None
    assert latest_build_failure(path, 1) is None


def test_host_create_failure_is_not_misreported_as_before_create(tmp_path, monkeypatch):
    runtime, bound = _world(tmp_path)
    host = FakeHost()
    scope = _scope()
    deps = _deps(runtime, host, bound, comparison=record(7))
    attempted = []

    def host_failed(**_kwargs):
        attempted.append(True)
        raise OSError("host create failed after entry")

    monkeypatch.setattr(host, "create_seat", host_failed)
    with pytest.raises(OSError, match="host create failed after entry"):
        build_seat(deps, scope, request_id="host-failed")
    assert attempted == [True]
    assert "latestBuildFailure" not in read_ledger(path_for(runtime, scope))


def test_capped_ledger_preserves_selected_failure_with_tied_times_after_reload(tmp_path):
    path = tmp_path / "requests.json"
    row = {
        "state": "failed",
        "outcome": "failed",
        "beforeCreate": True,
        "generation": 0,
        "at": "2026-10-25T10:00:00+00:00",
        "step": "packet",
        "failedStep": "packet",
        "reason": "missing",
        "error": "original error",
        "leaf": LEAF,
        "requestId": "a-current",
    }
    selected = f"{LEAF}:a-current"
    # Readable older/oversized ledgers are trimmed on the next write. Serialized
    # key order puts the current failure first; tied timestamps cannot select it.
    requests = {selected: row}
    for number in range(MAX_LEDGER_ENTRIES):
        requests[f"{LEAF}:z-{number:02}"] = {
            "leaf": LEAF,
            "requestId": f"z-{number:02}",
            "at": row["at"],
            "generation": 0,
            "delivery": "delivered",
            "agentId": "previous-seat",
        }
    path.write_text(
        json.dumps({"requests": requests, "latestBuildFailure": selected}, sort_keys=True)
    )
    record_request(path, LEAF, "a-current", row)
    saved = read_ledger(path)
    assert len(saved["requests"]) == MAX_LEDGER_ENTRIES
    assert saved["latestBuildFailure"] == selected
    assert latest_build_failure(path, 0) == row
    assert saved["requests"][selected] == row
    record_request(
        path, LEAF, "z-success", {"at": row["at"], "generation": 0, "delivery": "delivered"}
    )
    assert len(read_ledger(path)["requests"]) == MAX_LEDGER_ENTRIES
    assert "latestBuildFailure" not in read_ledger(path)
    assert latest_build_failure(path, 0) is None
