"""The cold first run crosses its own gate, and a refused one tells the whole truth.

Three distinct operations are protected here (L188-B01, L188-B05, L188-B06):

* the scaffold predicate is what the surviving runtime installer actually creates, in both of its
  delivery modes, so a genuinely absent configured root reaches the host stage;
* the selection and the request binding are settled before any stage writes, an execution that
  already answers the request is not prepared a second time, and a cold preparation runs under the
  dispatch lock;
* a failed stage carries every completed stage, their paths and the host state left behind through
  the refusal the start path answers with.

The installer, the authority writer and the product's host reader run for real. Only the
provisioner and the bridge reads are replaced at their owners: no test may touch a live host.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.role_launch_context import selection_binding
from agents_remember.cli import role_launch_first_run as first_run
from agents_remember.cli import role_launch_routes as routes
from agents_remember.cli.role_launch_admission import Admission, admit, prepare_first_run_for
from agents_remember.cli.role_launch_receipts import RECEIPT_SCHEMA, message_binding_projection_path
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    load_config,
    load_paseo_runtime_settings,
)
from agents_remember.models.role_launcher import RoleDispatchRequest
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from fastapi import HTTPException
from fastapi.responses import JSONResponse

MODES = ("legacy", "capsule")


def _workspace_config(base: Path) -> Path:
    """The MCP authority settings file of a workspace whose coordination root is absent."""

    workspace = base / "workspace"
    config_path = workspace / ".claude" / "mcp" / "agents-remember-settings.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "coordinationRoot": (base / "coordination").as_posix(),
                "workspaceRoot": workspace.as_posix(),
                "providers": {},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return config_path


def _stub_host(
    monkeypatch: pytest.MonkeyPatch,
    *,
    provision: dict[str, Any] | None = None,
    runtime_info: dict[str, Any] | None = None,
    stamp: str = "digest-1",
) -> dict[str, Any]:
    """Replace the provisioner and the bridge reads; everything else stays the shipped code."""

    answer = (
        provision
        if provision is not None
        else {
            "ok": True,
            "changed": True,
            "daemon": {"action": "started", "reasons": []},
            "restartRequired": [],
            "changes": [{"step": "daemon", "action": "started"}],
        }
    )
    monkeypatch.setattr(first_run, "provision_for_install", lambda settings: answer)
    monkeypatch.setattr(
        first_run,
        "bridge_call",
        lambda config, command, payload: runtime_info or {"serverId": "server-1"},
    )
    monkeypatch.setattr(first_run, "read_loaded_stamp", lambda home: {"plugin": stamp})
    monkeypatch.setattr(first_run, "installed_plugin_path", lambda home: Path(home) / "plugin")
    monkeypatch.setattr(first_run, "tree_digest", lambda path: stamp)
    return answer


def _select_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    if mode == "capsule":
        monkeypatch.setenv("AR_EXPERIMENT", "role-capsules")
    else:
        monkeypatch.delenv("AR_EXPERIMENT", raising=False)


def _bootstrap(request_id: uuid.UUID | None = None, **extra: Any) -> RoleDispatchRequest:
    return RoleDispatchRequest.model_validate(
        {"role": "bootstrap", "requestId": str(request_id or uuid.uuid4()), **extra}
    )


@pytest.fixture(params=MODES)
def cold(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[McpRuntimeConfig, str]:
    """A config for a genuinely absent coordination root in one delivery mode, and that mode."""

    mode = str(request.param)
    _select_mode(monkeypatch, mode)
    config = load_config(_workspace_config(tmp_path / mode))
    assert not config.coordination_root.exists()
    return config, mode


def _user_settings() -> Path:
    return Path(os.environ["XDG_CONFIG_HOME"]) / "agents-remember" / "settings.json"


# --------------------------------------------------------------------------------------------
# L188-B01: the retained scaffold is what the installer creates, in both delivery modes.
# --------------------------------------------------------------------------------------------


def test_the_installed_scaffold_is_what_the_cold_path_accepts(
    cold: tuple[McpRuntimeConfig, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config, mode = cold
    _stub_host(monkeypatch)

    report = first_run.prepare_first_run(config)

    root = config.coordination_root
    scaffold = report["stages"][0]
    assert scaffold["stage"] == "scaffold"
    assert scaffold["state"] == "created", report
    assert scaffold["delivery"] == mode
    # The retained assets both modes install, and nothing the leaf retired.
    assert (root / "docs").is_dir()
    assert (root / "tasks").is_dir()
    assert not (root / "skills").exists(), "no coordination-root skills copy may be created"
    assert first_run.scaffold_present(config) is True
    # The instruction target the capsule mode withholds is not part of the predicate.
    assert (root / "AGENTS.md").exists() is (mode == "legacy")


def test_the_cold_path_crosses_the_scaffold_gate_to_the_host_stage(
    cold: tuple[McpRuntimeConfig, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config, mode = cold
    _stub_host(monkeypatch)

    report = first_run.prepare_first_run(config)

    assert report["ok"] is True
    assert [entry["stage"] for entry in report["stages"]] == [
        "scaffold",
        "host-authority",
        "host",
        "host-verify",
    ], f"{mode}: the host stage was not reached"
    authority = report["stages"][1]
    assert authority["path"] == config.config_path.as_posix(), "the MCP authority file is the one"
    assert authority["changed"] is True
    # The block the file received is one this build loads back as a usable host.
    written = json.loads(config.config_path.read_text(encoding="utf-8"))
    assert set(written["paseoRuntime"]) == {
        "installPrefix",
        "home",
        "listen",
        "providers",
        "embed",
    }
    assert written["coordinationRoot"] == config.coordination_root.as_posix()
    assert written["workspaceRoot"] == config.workspace_root.as_posix()
    assert config.paseo_runtime is not None, "host_configured must be true after the stage"
    assert load_paseo_runtime_settings(config.config_path) is not None
    # MIK-R137: the developer's user settings file never receives a host block.
    assert not _user_settings().exists()


def test_a_same_request_retry_converges_without_rewriting_anything(
    cold: tuple[McpRuntimeConfig, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _mode = cold
    _stub_host(monkeypatch)
    first = first_run.prepare_first_run(config)
    assert first["ok"] is True
    before = config.config_path.read_bytes()

    retry = first_run.prepare_first_run(config)

    assert retry["ok"] is True
    assert [(entry["stage"], entry.get("state")) for entry in retry["stages"]] == [
        ("scaffold", "present"),
        ("host-authority", "present"),
        ("host", "provisioned"),
        ("host-verify", "running"),
    ]
    assert retry["stages"][1]["changed"] is False
    assert config.config_path.read_bytes() == before, (
        "an existing host block is read, not rewritten"
    )
    assert first_run.recall_first_run_report() == retry


def test_a_request_with_a_binding_but_no_receipt_is_not_prepared_again(
    cold: tuple[McpRuntimeConfig, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """MIK-R196 rule 3 as ruling 887182ab reads it: a same-UUID retry does not prepare again.

    The message-binding file is written once, when the request prepared. A retry whose receipt
    never landed - the stale queued request - must reconcile its own execution instead of
    preparing the root a second time, which on a cold root means a second scaffold and host.
    """

    config, _mode = cold
    request = _bootstrap()
    binding = Path(config.coordination_root) / "notes/reports/paseo-native-executions"
    binding = binding / "message-bindings" / f"{request.request_id}.json"
    binding.parent.mkdir(parents=True, exist_ok=True)
    binding.write_text(
        json.dumps({"selection": selection_binding(request)}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    prepared: list[McpRuntimeConfig] = []

    def record(configuration: McpRuntimeConfig) -> dict[str, Any]:
        prepared.append(configuration)
        return {"ok": True, "stages": []}

    monkeypatch.setattr(first_run, "prepare_first_run", record)

    admission = admit(config, request)
    prepare_first_run_for(config, request, admission)

    assert admission.existing is True, "a binding for this selection is proof the request prepared"
    assert prepared == [], "the retry prepared the root again"


def test_the_root_configuration_refusal_is_kept_and_writes_nothing(tmp_path: Path) -> None:
    base = tmp_path / "refused"
    root = base / "coordination"
    (root / "system").mkdir(parents=True)
    (root / "system" / "settings.json").write_text("{}\n", encoding="utf-8")
    config = McpRuntimeConfig(
        config_path=(base / "mcp-settings.json").resolve(),
        coordination_root=root,
        workspace_root=base / "workspace",
        transcript_root=base / "transcripts",
    )

    with pytest.raises(first_run.FirstRunPreparationError) as failed:
        first_run.prepare_first_run(config)

    assert failed.value.status == "bootstrap-scaffold-failed"
    assert failed.value.report["failedStage"] == "scaffold"
    assert "agentic-settings-location-upgrade-required" in failed.value.detail
    assert not (root / "docs").exists(), "a refused root is not partially installed"
    assert not (root / "tasks").exists()


# --------------------------------------------------------------------------------------------
# L188-B06: a refused stage carries the stages it completed and the host state it left.
# --------------------------------------------------------------------------------------------


def test_a_provision_failure_carries_the_completed_stages_and_the_retained_daemon(
    cold: tuple[McpRuntimeConfig, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _mode = cold
    _stub_host(
        monkeypatch,
        provision={
            "ok": False,
            "changed": True,
            "daemon": {"action": "started", "reasons": []},
            "changes": [{"step": "daemon", "action": "started"}, {"step": "node", "action": "x"}],
            "error": {"code": "install_failed", "step": "install", "message": "the install failed"},
        },
    )

    with pytest.raises(first_run.FirstRunPreparationError) as failed:
        first_run.prepare_first_run(config)

    error = failed.value
    assert error.status == "host-provision-failed"
    assert error.report["failedStage"] == "host"
    assert [entry["stage"] for entry in error.report["stages"]] == ["scaffold", "host-authority"]
    assert "scaffold created at" in error.detail
    assert "host authority written in" in error.detail
    assert "Remaining host state" in error.detail
    assert "daemon action was 'started'" in error.detail
    assert "Provisioner changes before the failure: 2" in error.detail
    assert "call role_start again with the same request id" in str(error)
    assert "host-provision-failed" in str(error)
    # The failed report is readable on this call's context, not only on a return.
    assert first_run.recall_first_run_report() == error.report


def test_a_plugin_load_failure_carries_the_running_daemon(
    cold: tuple[McpRuntimeConfig, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _mode = cold
    _stub_host(monkeypatch, stamp="installed-digest")
    monkeypatch.setattr(first_run, "read_loaded_stamp", lambda home: {"plugin": "older-digest"})

    with pytest.raises(first_run.FirstRunPreparationError) as failed:
        first_run.prepare_first_run(config)

    error = failed.value
    assert error.status == "host-plugin-not-loaded"
    assert error.report["failedStage"] == "host-verify"
    assert [entry["stage"] for entry in error.report["stages"]] == [
        "scaffold",
        "host-authority",
        "host",
    ]
    assert "did not pass the post-provision check" in error.detail
    assert "daemon action was 'started'" in error.detail
    assert "installed-digest" in error.detail


def test_an_unreadable_authority_file_is_a_named_host_authority_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "unreadable"
    config_path = base / "mcp-settings.json"
    base.mkdir(parents=True)
    config_path.write_text("{not json", encoding="utf-8")
    config = McpRuntimeConfig(
        config_path=config_path.resolve(),
        coordination_root=base / "coordination",
        workspace_root=base / "workspace",
        transcript_root=base / "transcripts",
    )

    with pytest.raises(first_run.FirstRunPreparationError) as failed:
        first_run.prepare_first_run(config)

    assert failed.value.status == "host-authority-unreadable"
    assert failed.value.report["failedStage"] == "host-authority"
    assert config_path.read_text(encoding="utf-8") == "{not json"
    # The scaffold stage did complete before that failure and is named.
    assert [entry["stage"] for entry in failed.value.report["stages"]] == ["scaffold"]
    assert "Completed before that failure" in failed.value.detail


def test_an_unknown_delivery_mode_is_an_install_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AR_EXPERIMENT", "no-such-mode")
    config = load_config(_workspace_config(tmp_path / "unknown"))

    with pytest.raises(first_run.FirstRunPreparationError) as failed:
        first_run.prepare_first_run(config)

    assert failed.value.status == "bootstrap-install-refused"
    assert failed.value.report["failedStage"] == "scaffold"
    assert not config.coordination_root.exists(), "a refused install writes nothing"


# --------------------------------------------------------------------------------------------
# L188-B05: validation and reconciliation come before any durable preparation.
# --------------------------------------------------------------------------------------------


def test_an_invalid_document_reference_is_refused_before_any_effect(tmp_path: Path) -> None:
    config = load_config(_workspace_config(tmp_path / "invalid"))

    with pytest.raises(HTTPException) as refused:
        routes._role_launch_dispatch_endpoint(
            config, _bootstrap(taskDocumentRef={"repository": "demo", "path": "leaf/task.json"})
        )

    assert refused.value.status_code == 409
    assert "does not accept a task selection" in str(refused.value.detail)
    assert not config.coordination_root.exists(), (
        "no scaffold was installed for a refused selection"
    )
    assert "paseoRuntime" not in config.config_path.read_text(encoding="utf-8")


def test_an_archived_request_id_is_refused_before_any_effect(tmp_path: Path) -> None:
    config = load_config(_workspace_config(tmp_path / "archived"))
    request = _bootstrap()
    admission = admit(config, request)
    archived = admission.receipt_path.parent / "history" / f"{request.request_id}.json"
    archived.parent.mkdir(parents=True)
    archived.write_text("{}\n", encoding="utf-8")

    with pytest.raises(HTTPException) as refused:
        routes._role_launch_dispatch_endpoint(config, request)

    assert refused.value.status_code == 409
    assert "archived execution" in str(refused.value.detail)
    root = config.coordination_root
    assert not (root / "docs").exists() and not (root / "tasks").exists(), "nothing was installed"
    assert "paseoRuntime" not in config.config_path.read_text(encoding="utf-8")
    assert not _user_settings().exists()


def test_cold_preparation_runs_under_the_dispatch_lock(
    cold: tuple[McpRuntimeConfig, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _mode = cold
    entered = threading.Event()
    release = threading.Event()
    active = 0
    peak = 0
    calls: list[uuid.UUID] = []
    lock = threading.Lock()

    def slow_prepare(_config: McpRuntimeConfig) -> dict[str, Any]:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        entered.set()
        release.wait(HANG_GUARD_SECONDS)
        with lock:
            active -= 1
        return {"ok": True, "stages": [{"stage": "scaffold", "state": "present"}]}

    monkeypatch.setattr(first_run, "prepare_first_run", slow_prepare)
    monkeypatch.setattr(
        routes, "_start_execution", lambda *args, **kwargs: JSONResponse({"status": "running"})
    )
    first = _bootstrap()
    second = _bootstrap()

    def run(request: RoleDispatchRequest) -> None:
        calls.append(request.request_id)
        routes._role_launch_dispatch_endpoint(config, request, lock_wait_seconds=0.0)

    worker = threading.Thread(target=run, args=(first,))
    worker.start()
    assert entered.wait(HANG_GUARD_SECONDS)
    with pytest.raises(routes.LaunchLockBusy):
        routes._role_launch_dispatch_endpoint(config, second, lock_wait_seconds=0.0)
    release.set()
    worker.join(HANG_GUARD_SECONDS)

    assert peak == 1, "two starts prepared the same cold root side by side"
    assert len(calls) == 1, "the second start never reached preparation"


def test_an_existing_receipt_is_reconciled_without_a_second_preparation(
    cold: tuple[McpRuntimeConfig, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _mode = cold
    request = _bootstrap()
    admission = admit(config, request)
    admission.receipt_path.parent.mkdir(parents=True)
    admission.receipt_path.write_text(
        json.dumps(
            {
                "schema": RECEIPT_SCHEMA,
                "requestId": str(request.request_id),
                "role": "bootstrap",
            }
        ),
        encoding="utf-8",
    )

    def never(_config: McpRuntimeConfig) -> dict[str, Any]:
        raise AssertionError("an existing execution must not be prepared again")

    monkeypatch.setattr(first_run, "prepare_first_run", never)
    monkeypatch.setattr(
        routes,
        "_reconcile_prior_execution",
        lambda *args, **kwargs: JSONResponse({"status": "running", "reconciled": True}),
    )

    response = routes._role_launch_dispatch_endpoint(config, request)

    assert json.loads(bytes(response.body))["reconciled"] is True
    root = config.coordination_root
    assert not (root / "docs").exists() and not (root / "tasks").exists(), (
        "a retry installs nothing"
    )


def test_preparation_is_skipped_only_for_an_existing_receipt(
    cold: tuple[McpRuntimeConfig, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _mode = cold
    request = _bootstrap()
    prepared: list[uuid.UUID] = []
    monkeypatch.setattr(
        first_run,
        "prepare_first_run",
        lambda _config: prepared.append(request.request_id) or {"ok": True, "stages": []},
    )

    fresh = admit(config, request)
    assert fresh.existing is False
    prepare_first_run_for(config, request, fresh)
    assert prepared == [request.request_id]

    existing = Admission(receipt_path=fresh.receipt_path, existing=True)
    prepare_first_run_for(config, request, existing)
    assert prepared == [request.request_id], "the reconciled seat answers its own retry"


def test_a_queued_same_request_refreshes_admission_before_preparing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(_workspace_config(tmp_path))
    request = _bootstrap()
    started = threading.Event()
    queued = threading.Event()
    preparations: list[str] = []
    errors: list[BaseException] = []
    replies: list[JSONResponse] = []
    acquire = routes._acquire_dispatch_lock

    def acquire_in_order(seconds: float = 0.0) -> None:
        if threading.current_thread().name == "queued-start":
            queued.set()
        acquire(seconds)

    def start(*_args: Any) -> JSONResponse:
        if threading.current_thread().name == "first-start":
            started.set()
            assert queued.wait(HANG_GUARD_SECONDS)
            binding = message_binding_projection_path(config, request.request_id)
            binding.parent.mkdir(parents=True, exist_ok=True)
            binding.write_text(json.dumps({"selection": selection_binding(request)}))
        return JSONResponse({"agentId": "one-existing-execution"})

    def dispatch() -> None:
        try:
            replies.append(
                routes._role_launch_dispatch_endpoint(
                    config, request, lock_wait_seconds=HANG_GUARD_SECONDS
                )
            )
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(routes, "_acquire_dispatch_lock", acquire_in_order)
    monkeypatch.setattr(routes, "with_caller_harness", lambda _config, selected: selected)
    monkeypatch.setattr(routes, "_start_execution", start)
    monkeypatch.setattr(
        first_run, "prepare_first_run", lambda _config: preparations.append("prepared")
    )
    first = threading.Thread(target=dispatch, name="first-start")
    second = threading.Thread(target=dispatch, name="queued-start")
    first.start()
    assert started.wait(HANG_GUARD_SECONDS)
    second.start()
    first.join(HANG_GUARD_SECONDS)
    second.join(HANG_GUARD_SECONDS)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert len(replies) == 2 and replies[0].body == replies[1].body
    assert preparations == ["prepared"], (
        "the queued request must see the binding published while it waited"
    )
