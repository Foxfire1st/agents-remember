"""Saved step-up starts retain predecessor safety and frozen takeover evidence."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import test_paseo_role_tools as registered_support
import test_role_step_up as step_up_support
from agents_remember.cli import (
    role_handover_artifacts,
    role_launch_preparation,
    role_launch_receipts,
    role_step_up_runtime,
)
from agents_remember.cli.paseo_bridge import BRIDGE_TIMEOUT
from agents_remember.cli.paseo_role_tools import _started
from agents_remember.errors import AgentsRememberError
from agents_remember.models.role_launcher import RoleDispatchRequest
from agents_remember.tasks.document_refs import TaskDocumentTopology

launch_world = step_up_support.launch_world


@pytest.fixture
def recovery_world(launch_world):
    world = launch_world
    defaults = {
        "role": "worker",
        "agent": "codex",
        "model": "gpt-a",
        "stepUp": {"model": "gpt-b"},
    }
    world.replace(
        role_launch_preparation, "_role_defaults", return_value=(defaults, ("codex", "eve"))
    )
    return world


def _replacement(world):
    return RoleDispatchRequest.model_validate(
        {**world.request("worker").model_dump(by_alias=True), "stepUp": "The approach stalled."}
    )


@pytest.mark.parametrize("recovery", ["retry", "history"])
@pytest.mark.parametrize("busy", ["turn", "permission", "unreachable"])
def test_archive_renews_predecessor_read_on_every_recovery(recovery_world, recovery, busy):
    world = recovery_world
    original = world.request("worker")
    assert world.dispatch(original)[1]["status"] == "running"
    holder = world.agent_of(original)
    holder.update(status="idle")
    request = _replacement(world)
    if recovery == "retry":
        world.runtime.fail("agent-archive", BRIDGE_TIMEOUT)
        assert world.dispatch(request)[1]["status"] == "unknown"
    else:
        role_launch_receipts._archive_receipt(world.receipt_path(original), world.receipt(original))
    if busy == "turn":
        holder.update(status="running")
    elif busy == "permission":
        holder.update(pendingPermissions=[{"name": "Edit"}])
    else:
        world.runtime.fail("agent-state", BRIDGE_TIMEOUT)
    world.runtime.calls.clear()
    if busy == "unreachable":
        code, answer = world.dispatch(request)
        assert code == 409 and answer["hostUnreachable"] is True
    else:
        error = world.refused(request)
        assert ("Edit" if busy == "permission" else "turn") in str(error.detail)
    assert holder["archivedAt"] is None
    assert not any(
        command in {"agent-archive", "agent-create"} for command, _ in world.runtime.calls
    )
    holder.update(status="idle", pendingPermissions=[])
    assert world.dispatch(request)[1]["status"] == "running"


def test_retryable_empty_seat_tool_answer_preserves_same_request():
    world = registered_support.RegisteredRoleToolTests()
    world.setUp()
    try:
        catalog = json.loads(json.dumps(step_up_support.LAUNCH_CATALOG))
        catalog["providers"][0]["models"].append(
            {"id": "gpt-b", "label": "Alternative", "efforts": []}
        )
        world.replace(world.runtime, "_catalog", return_value=catalog)
        defaults = {
            "role": "worker",
            "agent": "codex",
            "model": "gpt-a",
            "stepUp": {"model": "gpt-b"},
        }
        world.replace(
            role_launch_preparation, "_role_defaults", return_value=(defaults, ("codex", "eve"))
        )
        selection = {
            key: ref.model_dump(mode="json")
            for key, ref in registered_support.selection_of("worker").items()
        }
        initial = {"role": "worker", "request_id": str(uuid4()), **selection}
        _, first = world.call("role_start", initial)
        world.runtime.agents[first["agentId"]].update(status="idle")
        request = {**initial, "request_id": str(uuid4()), "step_up": "The approach stalled."}
        world.runtime.fail("agent-create", "paseo_call_failed", "refused")
        _, answer = world.call("role_start", request)
        assert answer["ok"] is False and answer["status"] == "rejected"
        assert "same request id and selection" in answer["nextAction"]
        assert "new request id" not in answer["nextAction"]
        _, retried = world.call("role_start", request)
        assert retried["ok"] is True
        assert retried["agentId"] in world.runtime.agents
        recorded = json.loads(Path(retried["log"]).read_text())
        assert recorded["requestId"] == request["request_id"]
    finally:
        world.doCleanups()


def test_nonretryable_tool_rejection_keeps_new_request_guidance(launch_world):
    world = launch_world
    request = world.request("worker")
    world.runtime.fail("agent-create", "paseo_call_failed", "refused")
    assert world.dispatch(request)[1]["canRetry"] is False
    assert "new request id" in _started(world.config, request)["nextAction"]


@pytest.mark.parametrize("old_history", [False, True])
def test_receipt_loss_uses_durable_applied_destination_in_takeover(recovery_world, old_history):
    world = recovery_world
    if old_history:
        original = world.request("worker")
        world.dispatch(original)
        world.agent_of(original).update(status="idle")
    request = _replacement(world)
    assert world.dispatch(request)[1]["status"] == "running"
    world.agent_of(request).update(status="idle", archivedAt="gone")
    world.leaf = TaskDocumentTopology(world.config.coordination_root).resolve(world.leaf.ref)
    world.receipt_path(request).unlink()
    restarted = world.request("worker")
    assert world.dispatch(restarted)[1]["status"] == "running"
    receipt = world.receipt(restarted)
    material = json.loads(Path(receipt["report"]["path"]).with_suffix(".step-up.json").read_text())
    assert material["fromModel"] == "gpt-b"
    assert "from model gpt-b" in Path(receipt["handoverArtifact"]["path"]).read_text()
    assert receipt["stepUp"]["from"]["model"] == "gpt-b"


def test_unconfirmed_prior_destination_does_not_claim_the_configured_base(recovery_world):
    world = recovery_world
    request = _replacement(world)
    world.runtime.fail("agent-create", BRIDGE_TIMEOUT)
    assert world.dispatch(request)[1]["status"] == "unknown"
    world.leaf = TaskDocumentTopology(world.config.coordination_root).resolve(world.leaf.ref)
    world.receipt_path(request).unlink()
    restarted = world.request("worker")
    assert world.dispatch(restarted)[1]["status"] == "running"
    predecessor = world.receipt(restarted)["stepUp"]["from"]
    assert predecessor["model"] is None
    assert "unconfirmed" in predecessor["provenance"]
    receipt = world.receipt(restarted)
    assert "from model unknown" in Path(receipt["handoverArtifact"]["path"]).read_text()
    material = json.loads(Path(receipt["report"]["path"]).with_suffix(".step-up.json").read_text())
    assert material["fromProvenance"] == predecessor["provenance"]


@pytest.mark.parametrize("damage", ["missing", "changed", "symlink", "directory", "fifo"])
def test_saved_takeover_material_is_restored_or_refused_before_create(recovery_world, damage):
    world = recovery_world
    request = _replacement(world)
    world.runtime.fail("agent-create", BRIDGE_TIMEOUT)
    assert world.dispatch(request)[1]["status"] == "unknown"
    receipt = world.receipt(request)
    path = Path(receipt["report"]["path"]).with_suffix(".step-up.json")
    original = path.read_bytes()
    path.unlink()
    if damage == "changed":
        path.write_text("{}")
    elif damage == "symlink":
        target = path.with_name("unowned.json")
        target.write_bytes(original)
        path.symlink_to(target)
    elif damage == "directory":
        path.mkdir()
    elif damage == "fifo":
        os.mkfifo(path)
    world.runtime.calls.clear()
    if damage == "missing":
        assert world.dispatch(request)[1]["status"] == "running"
        assert path.read_bytes() == original
        frozen = receipt["stepUpArtifact"]
        assert frozen["sha256"] == hashlib.sha256(original).hexdigest()
        assert frozen["content"].encode() == original
        assert frozen["sha256"] in Path(receipt["handoverArtifact"]["path"]).read_text()
    else:
        error = world.refused(request)
        assert "takeover" in str(error.detail).lower()
        assert str(path) in str(error.detail)
        assert "retry" in str(error.detail).lower()
        assert not any(command == "agent-create" for command, _ in world.runtime.calls)
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()
        assert world.dispatch(request)[1]["status"] == "running"
        assert path.read_bytes() == original


@pytest.mark.parametrize("population", [8, 64])
def test_git_inventory_has_explicit_file_budget(tmp_path, monkeypatch, population):
    root = tmp_path / "candidate"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for number in range(population):
        (root / f"file-{number:04d}").touch()
    monkeypatch.setattr(role_step_up_runtime, "_INVENTORY_MAX_FILES", 16, raising=False)
    monkeypatch.setattr(role_step_up_runtime, "_INVENTORY_MAX_BYTES", 4096, raising=False)
    result = role_step_up_runtime.uncommitted_files({"codeRoot": str(root)})["codeRoot"]
    assert result["complete"] is (population == 8)
    assert len(result["status"]) <= 16
    assert result["bytesCaptured"] <= 4096
    if population > 16:
        assert "file" in result["incomplete"]
        assert "git" in result["recovery"]


def test_git_inventory_caps_bytes_before_serialization(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for number in range(16):
        (tmp_path / (f"file-{number:02d}-" + "x" * 100)).touch()
    monkeypatch.setattr(role_step_up_runtime, "_INVENTORY_MAX_BYTES", 256, raising=False)
    result = role_step_up_runtime.uncommitted_files({"codeRoot": str(tmp_path)})["codeRoot"]
    assert result["complete"] is False
    assert "byte" in result["incomplete"]
    assert result["bytesCaptured"] <= 256
    assert len(json.dumps(result)) < 2048


def test_takeover_publication_is_write_once_and_checks_the_open_file(tmp_path, monkeypatch):
    report = tmp_path / "request.md"
    material = {"fromModel": "original"}
    reference = role_handover_artifacts.write_step_up_artifact(str(report), material)
    path = Path(reference["path"])
    original = path.read_bytes()
    assert path.stat().st_mode & 0o222 == 0
    assert role_handover_artifacts.write_step_up_artifact(str(report), material) == reference
    with pytest.raises(AgentsRememberError):
        role_handover_artifacts.write_step_up_artifact(str(report), {"fromModel": "changed"})
    assert path.read_bytes() == original
    regular = path.lstat()
    target = tmp_path / "unowned.json"
    target.write_bytes(original)
    path.unlink()
    path.symlink_to(target)
    lstat = Path.lstat
    # Model a regular path swapped for a link between its initial stat and open.
    monkeypatch.setattr(Path, "lstat", lambda self: regular if self == path else lstat(self))
    with pytest.raises(AgentsRememberError, match="regular file"):
        role_handover_artifacts.write_step_up_artifact(str(report), material)
    assert target.read_bytes() == original


def test_hung_git_is_reaped_under_start_lock_with_durable_partial_recovery(
    recovery_world, monkeypatch
):
    world = recovery_world
    original = world.request("worker")
    world.dispatch(original)
    world.agent_of(original).update(status="idle")
    request = _replacement(world)
    popen = subprocess.Popen
    children = []

    def hanging_status(command, *args, **kwargs):
        if command[0] == "git" and "--porcelain=v1" in command:
            child = popen([sys.executable, "-c", "import time; time.sleep(2)"], *args, **kwargs)
            children.append(child)
            return child
        return popen(command, *args, **kwargs)

    monkeypatch.setattr(role_step_up_runtime, "_INVENTORY_TIMEOUT_SECONDS", 0.05, raising=False)
    with monkeypatch.context() as child_patch:
        child_patch.setattr(subprocess, "Popen", hanging_status)
        assert world.dispatch(request)[1]["status"] == "running"
    assert children and all(child.poll() is not None and child.returncode < 0 for child in children)
    saved = world.receipt(request)
    material = json.loads(Path(saved["report"]["path"]).with_suffix(".step-up.json").read_text())
    for result in material["uncommittedFiles"].values():
        assert result["complete"] is False
        assert "time" in result["incomplete"]
        assert "git" in result["recovery"]
    # The same bounded material is recoverable, and later starts can take the lock.
    assert saved["stepUpArtifact"]["content"] == json.dumps(material, indent=2) + "\n"
    assert world.dispatch(world.request("reviewer"))[1]["status"] == "running"
