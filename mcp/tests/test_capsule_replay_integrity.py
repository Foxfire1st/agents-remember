"""Frozen detail snapshots must still be trustworthy when a saved launch is sent."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import test_role_instruction_wording as wording
from agents_remember.cli import (
    role_held_messages,
    role_instruction_details,
    role_launch_preparation,
    role_launch_receipts,
)
from agents_remember.cli.role_handover_artifacts import first_message, write_handover_artifact
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_capsules.types import (
    CapsuleDetailReference,
    compute_content_digest,
)
from agents_remember.serving.capsule_delivery import CapsuleBindingIdentity, CapsuleDelivery
from agents_remember.tasks import write_task_doc


@pytest.fixture
def leaf_preparation(monkeypatch: pytest.MonkeyPatch):
    entry_compiler = role_launch_preparation.compile_launch_capsule
    fixture = wording.HandoverTextWordingTests()
    fixture.setUp()

    def capsule_with_worker_detail(config, request, *, operation):
        capsule = fixture.role_capsule(config, request, operation=operation)
        if request.role != "worker":
            return capsule
        # The shipped implementation capsule has no details; supply one at the compiler seam
        # so preparation and replay also protect a Worker capsule when it does carry details.
        detail = CapsuleDetailReference(
            "reference/worker-fixture.md",
            compute_content_digest(b"Frozen Worker detail.\n"),
            b"Frozen Worker detail.\n",
            "when building",
        )
        delivery = capsule.delivery
        assert delivery is not None
        return replace(
            capsule,
            delivery=replace(
                delivery,
                trusted_instructions=delivery.trusted_instructions
                + "\nRead {{detail:reference/worker-fixture.md}}.",
                semantic_digest=compute_content_digest(detail.content),
                detail_references=(*delivery.detail_references, detail),
            ),
        )

    try:
        # Unwind these overrides before the unittest fixture restores its own patches.
        with monkeypatch.context() as overrides:
            overrides.setattr(role_launch_preparation, "_role_defaults", lambda *_: ({}, ()))
            overrides.setattr(
                role_launch_preparation, "resolve_agent_selection", lambda *_: ("fixture", {})
            )
            overrides.setattr(
                role_launch_preparation, "provider_accepts_tool_servers", lambda *_: True
            )
            overrides.setattr(
                role_launch_preparation, "compile_launch_capsule", capsule_with_worker_detail
            )
            yield fixture
    finally:
        fixture.doCleanups()
        assert role_launch_preparation.compile_launch_capsule is entry_compiler


@pytest.mark.parametrize("role", ["worker", "reviewer", "curator"])
def test_real_leaf_preparation_restores_details_beside_local_handover(
    leaf_preparation, monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    context, workspace = leaf_preparation.launch_of(role)
    assert context.task is not None
    write_task_doc(context.task.path.parent, context.task.document)
    monkeypatch.setattr(role_launch_preparation, "_resolve_workspace", lambda *_: workspace)
    request_id = uuid.uuid4()
    prepared = role_launch_preparation.prepare_role_handover(
        leaf_preparation.config, context, agent_override=None, request_id=request_id
    )
    compiled = prepared.handover
    report = compiled["taskReportPath"]
    artifact = write_handover_artifact(report, compiled["prompt"])
    opening = first_message(artifact, compiled["prompt"])
    receipt = {
        "instructionComposition": compiled["instructionComposition"],
        "report": {"path": report, "canonicalPath": compiled["canonicalTaskReportPath"]},
        "handoverArtifact": artifact,
        "workspace": prepared.workspace,
    }
    access = Path(workspace["path"]) / "role-reports"
    assert access.is_dir() and not access.is_symlink()
    assert Path(artifact["canonicalPath"]).parent == Path(report).parent.resolve()
    assert Path(report).parent.resolve() != Path(receipt["report"]["canonicalPath"]).parent
    references = receipt["instructionComposition"]["detailReferences"]
    assert references, f"The {role} capsule must actually exercise frozen details"
    frozen = {reference["path"]: Path(reference["path"]).read_bytes() for reference in references}
    for reference in references:
        assert Path(reference["path"]).parent == Path(report).parent
        assert reference["path"] in compiled["prompt"]
    receipt_path = Path(workspace["scratchRoot"]) / "receipt.json"
    _validate(receipt)
    Path(artifact["path"]).unlink()
    role_launch_receipts._restore_launch_handover(
        receipt_path, receipt, {"agent": {"prompt": opening}}
    )
    assert Path(artifact["path"]).read_text() == compiled["prompt"]
    assert {path: Path(path).read_bytes() for path in frozen} == frozen
    held = {**receipt, "heldSeat": True, "heldOpeningPrompt": opening}
    role_launch_receipts._restore_launch_handover(receipt_path, held, {})


def _frozen_receipt(report: Path) -> dict[str, Any]:
    references = tuple(
        CapsuleDetailReference(
            f"reference/{name}.md", compute_content_digest(body), body, "when authoring"
        )
        for name, body in (("first", b"First frozen detail.\n"), ("last", b"Last frozen detail.\n"))
    )
    delivery = CapsuleDelivery(
        CapsuleBindingIdentity("fixture", "task.json", "worker", "implementation"),
        "\n".join("Read {{detail:" + entry.source_path + "}}." for entry in references),
        compute_content_digest(b"capsule"),
        references,
    )
    instructions, record = role_instruction_details.compose_launch_instructions(
        delivery, str(report), {}
    )
    return {
        "instructionComposition": record,
        "report": {"path": str(report), "canonicalPath": str(report.resolve())},
        "handoverArtifact": write_handover_artifact(str(report), instructions),
        "savedInstructions": instructions,
    }


def _validate(receipt: dict[str, Any]) -> None:
    validator = getattr(role_instruction_details, "validate_instruction_detail_artifacts", None)
    assert callable(validator), "Saved launches need the public frozen-detail validator"
    validator(receipt)


def test_replay_checks_every_frozen_revision_without_reading_the_current_corpus(
    tmp_path: Path,
) -> None:
    receipt = _frozen_receipt(tmp_path / "request.md")
    _validate(receipt)
    last = receipt["instructionComposition"]["detailReferences"][-1]
    path = Path(last["path"])
    path.chmod(0o644)
    path.write_bytes(b"Changed detail.\n")
    with pytest.raises(ValueError, match=r"detail.*last") as raised:
        _validate(receipt)
    assert "retry" in str(raised.value).lower()
    assert "frozen" in str(raised.value).lower()
    assert path.read_bytes() == b"Changed detail.\n"


def test_replay_refuses_missing_or_nonregular_detail_targets(tmp_path: Path) -> None:
    for kind in ("symlink", "missing", "directory", "fifo"):
        receipt = _frozen_receipt(tmp_path / kind / "request.md")
        reference = receipt["instructionComposition"]["detailReferences"][0]
        path = Path(reference["path"])
        body = path.read_bytes()
        path.unlink()
        if kind == "symlink":
            other = path.with_name("unbound.md")
            other.write_bytes(body)
            path.symlink_to(other)
        elif kind == "directory":
            path.mkdir()
        elif kind == "fifo":
            os.mkfifo(path)
        with pytest.raises(ValueError, match="frozen instruction detail") as raised:
            _validate(receipt)
        assert reference["sourcePath"] in str(raised.value)
        assert str(path) in str(raised.value)
        assert "retry" in str(raised.value).lower()


def test_replay_refuses_a_rebound_report_folder_even_when_detail_bytes_are_unchanged(
    tmp_path: Path,
) -> None:
    original = tmp_path / "original-reports"
    original.mkdir()
    access = tmp_path / role_launch_receipts.REPORT_ACCESS_LINK
    access.symlink_to(original, target_is_directory=True)
    receipt = _frozen_receipt(access / "request.md")
    _validate(receipt)
    replacement = tmp_path / "replacement-reports"
    shutil.copytree(original, replacement)
    access.unlink()
    access.symlink_to(replacement, target_is_directory=True)
    with pytest.raises(ValueError, match="canonical"):
        _validate(receipt)
    access.unlink()
    access.symlink_to(original, target_is_directory=True)
    _validate(receipt)
    receipt["workspace"] = {
        "path": str(tmp_path),
        "taskReportAccessRoot": str(access),
        "taskReportRoot": str(original),
    }
    opening = first_message(receipt["handoverArtifact"], receipt["savedInstructions"])
    access.unlink()
    role_launch_receipts._restore_launch_handover(
        tmp_path / "receipt.json", receipt, {"agent": {"prompt": opening}}
    )
    assert access.is_symlink() and access.resolve() == original
    _validate(receipt)


def test_replay_refuses_incomplete_detail_references_instead_of_skipping_them(
    tmp_path: Path,
) -> None:
    receipt = _frozen_receipt(tmp_path / "request.md")
    valid = receipt["instructionComposition"]["detailReferences"][0]
    for malformed in (None, {}, ["not-a-reference"], [{}], [{**valid, "revision": None}]):
        damaged = deepcopy(receipt)
        damaged["instructionComposition"]["detailReferences"] = malformed
        with pytest.raises(ValueError, match="frozen instruction detail"):
            _validate(damaged)
    for changed in (
        {"sourcePath": "reference/different.md"},
        {"path": str(tmp_path / "copied-detail.md")},
    ):
        damaged = deepcopy(receipt)
        damaged["instructionComposition"]["detailReferences"] = [{**valid, **changed}]
        with pytest.raises(ValueError, match="canonical target"):
            _validate(damaged)
    for artifact in (
        None,
        {},
        {**receipt["handoverArtifact"], "path": str(tmp_path / "other.txt")},
        {**receipt["handoverArtifact"], "canonicalPath": str(tmp_path / "other.txt")},
    ):
        damaged = {**receipt, "handoverArtifact": artifact}
        with pytest.raises(ValueError, match="saved handover"):
            _validate(damaged)
    _validate({})
    _validate({"instructionComposition": {}})


class _HostBoundaryReached(Exception):
    """Stop at the fake host seam: no real agent is created or opened by these tests."""


@pytest.fixture
def saved_launch(tmp_path: Path) -> tuple[McpRuntimeConfig, Path, dict[str, Any]]:
    report = tmp_path / "request.md"
    receipt = _frozen_receipt(report)
    artifact = write_handover_artifact(str(report), receipt.pop("savedInstructions"))
    opening = first_message(artifact, Path(artifact["path"]).read_text())
    receipt.update(
        schema=role_launch_receipts.RECEIPT_SCHEMA,
        requestId=str(uuid.uuid4()),
        agentId=str(uuid.uuid4()),
        role="worker",
        selection={"role": "worker"},
        status="unknown",
        handoverArtifact=artifact,
        heldOpeningPrompt=opening,
        replayRequest={"agent": {"prompt": opening}},
        workspace={"path": str(tmp_path)},
    )
    path = tmp_path / "receipt.json"
    role_launch_receipts._write_receipt(path, receipt)
    config = McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path,
        workspace_root=tmp_path,
        transcript_root=tmp_path / "logs",
    )
    return config, path, receipt


def test_saved_start_replay_refuses_missing_details_before_the_host_call(
    saved_launch: tuple[McpRuntimeConfig, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requires the parent's validator hook in _restore_launch_handover."""
    config, path, receipt = saved_launch
    calls = []

    def fake_host(*args, **kwargs):
        calls.append(args[1])
        raise _HostBoundaryReached

    monkeypatch.setattr(role_launch_receipts, "run_launch_call", fake_host)
    with pytest.raises(_HostBoundaryReached):
        role_launch_receipts._execute_prepared_launch(config, path, receipt)
    assert len(calls) == 1
    calls.clear()
    reference = receipt["instructionComposition"]["detailReferences"][-1]
    Path(reference["path"]).unlink()
    with pytest.raises(ValueError, match="frozen instruction detail"):
        role_launch_receipts._execute_prepared_launch(config, path, receipt)
    assert calls == []
    assert json.loads(path.read_text())["status"] == "unknown"


def test_held_opening_refuses_changed_details_and_preserves_waiting_messages(
    saved_launch: tuple[McpRuntimeConfig, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requires the parent's validator hook in the delayed held-seat opening path."""
    config, path, receipt = saved_launch
    receipt.update(status="held", heldSeat=True)
    folder = receipt["workspace"]["path"]
    agent_id = receipt["agentId"]
    role_held_messages.hold_message(
        folder,
        agent_id,
        role_held_messages.HeldMessage("Manager:", "sender", "Begin review.", "held-seat"),
    )
    claimed = role_held_messages._claim_batch(folder, agent_id)
    assert claimed is not None
    messages, claim = claimed
    batch = role_held_messages._DrainBatch(config, path, receipt, folder, agent_id, messages, claim)
    calls = []

    def fake_host(*args, **kwargs):
        calls.append(args[1])
        raise _HostBoundaryReached

    monkeypatch.setattr(role_held_messages, "run_launch_call", fake_host)
    with pytest.raises(_HostBoundaryReached):
        role_held_messages._open_held_seat(batch)
    assert len(calls) == 1
    assert "Begin review." in calls[0]["agent"]["prompt"]
    calls.clear()
    reference = receipt["instructionComposition"]["detailReferences"][-1]
    detail = Path(reference["path"])
    detail.chmod(0o644)
    detail.write_bytes(b"Changed after the seat was held.\n")
    refused = role_held_messages._open_held_seat(batch)
    assert refused["outcome"] == "cannot-open"
    assert "frozen instruction detail" in refused["detail"]
    assert calls == []
    assert role_held_messages.waiting_total(folder, agent_id) == 1
    assert role_held_messages._claim_batch(folder, agent_id) is not None
