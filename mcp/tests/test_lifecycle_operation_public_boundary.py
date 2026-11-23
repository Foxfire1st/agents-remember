"""Registered controls preserve exact identity and replacement arguments for old journals."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.tool_answer_log import tool_call_context
from agents_remember.application.worktree_tool_requests import OperationControlRequest
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.mcp.tools.worktree import worktree_operation_control_payload
from agents_remember.models.lifecycles.operation_kinds import (
    LifecycleControlAction,
    LifecycleOperationKind,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_projection import (
    legal_operation_controls,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_projection import (
    operation_projection,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from test_completed_direct_landing_disposition import _landed
from test_direct_landing import _converted_series_fixture
from test_lifecycle_operation_journal_retired import (
    OLD_WRITER_INTEGRATE_RECORD,
    OLD_WRITER_INTEGRATE_RECORD_SHA256,
    OLD_WRITER_RECORD,
    OLD_WRITER_RECORD_SHA256,
    _seeded_generation,
)

pytestmark = pytest.mark.integration


def _registered_control_with_whole_result(
    config: McpRuntimeConfig, request: OperationControlRequest
) -> tuple[dict[str, Any], dict[str, Any]]:
    with tool_call_context(
        "worktree_operation_control", {"contract_path": request.contract_path}, config=config
    ):
        answer = worktree_operation_control_payload(config, request)
    whole = json.loads(Path(answer["log"]).read_text(encoding="utf-8"))
    return answer, whole


@pytest.mark.parametrize("action", ("retry", "recover", "resume"))
@pytest.mark.parametrize(
    ("kind", "old_fixture", "next_tool"),
    (
        (
            "closeout",
            (OLD_WRITER_RECORD, OLD_WRITER_RECORD_SHA256),
            "worktree_closeout_apply",
        ),
        (
            "integrate",
            (OLD_WRITER_INTEGRATE_RECORD, OLD_WRITER_INTEGRATE_RECORD_SHA256),
            "worktree_integrate",
        ),
    ),
)
def test_the_registered_control_refuses_retained_old_records(
    tmp_path: Path,
    kind: LifecycleOperationKind,
    old_fixture: tuple[Path, str],
    next_tool: str,
    action: LifecycleControlAction,
) -> None:
    """Reject persisted old authority at the registered boundary and keep its audit record."""

    fixture = _converted_series_fixture(tmp_path)
    contract = fixture["contract"]
    fixture_path, digest = old_fixture
    raw = fixture_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == digest
    old_record = json.loads(raw)
    path = operation_record_path(contract.worktree_group, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    store = LifecycleOperationStore(path)
    record = store.read()
    assert record is not None

    contract_path = contract.contract_path.as_posix()
    assert record.contractPath != contract_path
    expected_args = {"contract_path": contract_path}
    if kind == "closeout":
        expected_args["intent_note"] = "<fresh developer intent>"

    answer, whole = _registered_control_with_whole_result(
        fixture["config"],
        OperationControlRequest(
            contract_path=contract_path,
            operation_kind=kind,
            action=action,
            expected_generation=old_record["generation"],
            intent_note=f"registered boundary, {action} of a retired {kind}",
        ),
    )

    assert answer["ok"] is False
    assert answer["status"] == "lifecycle-operation-retired"
    assert answer["nextTool"] == next_tool
    assert answer["nextAction"] == "re-apply"
    assert answer["nextArgs"] == expected_args
    assert whole["observed"] == {
        "operationKind": kind,
        "generation": old_record["generation"],
        "status": old_record["status"],
    }
    assert store.read() == record
    assert path.read_bytes() == raw


def test_a_retired_running_record_is_inspectable_but_not_live(tmp_path: Path) -> None:
    """The journal keeps the leftover record; the projection stops advising on it."""

    fixture = _converted_series_fixture(tmp_path)
    _, generation = _seeded_generation(fixture, "closeout")

    store = LifecycleOperationStore(
        operation_record_path(fixture["contract"].worktree_group, "closeout")
    )
    record = store.read()
    assert record is not None and record.generation == generation

    projection = operation_projection(record)
    assert projection.kind == "closeout"
    assert projection.status == "running"
    assert projection.generation == generation
    # No advice to wait on it, resume it or reach a retired route for it.
    assert projection.recommendedAction is None


def test_a_retired_record_offers_no_control(tmp_path: Path) -> None:
    """Audit keeps the record and its controls list stays empty."""

    fixture = _converted_series_fixture(tmp_path)
    _seeded_generation(fixture, "integrate")

    store = LifecycleOperationStore(
        operation_record_path(fixture["contract"].worktree_group, "integrate")
    )
    record = store.read()
    assert record is not None
    assert legal_operation_controls(fixture["contract"], record) == []


def test_a_direct_landing_keeps_its_recommendation(tmp_path: Path) -> None:
    """The journal's only writer is unchanged: its record still advises and carries controls."""

    fixture = _landed(tmp_path)

    store = LifecycleOperationStore(
        operation_record_path(fixture["contract"].worktree_group, "direct-landing")
    )
    record = store.read()
    assert record is not None and record.operationKind == "direct-landing"

    projection = operation_projection(record)
    assert projection.kind == "direct-landing"
    # A landed generation is finished, so it offers no control; what matters here is that the
    # direct-landing path still reaches its recommendation, which the retired kinds no longer do.
    assert projection.recommendedAction is not None, projection
    assert legal_operation_controls(fixture["contract"], record) == []


@pytest.mark.parametrize(
    ("kind", "replacement"),
    (
        ("closeout", "worktree_closeout_apply"),
        ("integrate", "worktree_integrate"),
        ("direct-landing", None),
    ),
)
def test_absent_generation_names_the_replacement_for_retired_kinds(
    tmp_path: Path, kind: LifecycleOperationKind, replacement: str | None
) -> None:
    fixture = _converted_series_fixture(tmp_path)
    contract_path = fixture["contract"].contract_path.as_posix()

    result, whole = _registered_control_with_whole_result(
        fixture["config"],
        OperationControlRequest(
            contract_path=contract_path,
            operation_kind=kind,
            action="recover",
            expected_generation=1,
            intent_note="Recover the advertised generation.",
        ),
    )

    assert result["ok"] is False, result
    if replacement is None:
        assert result["status"] == "lifecycle-operation-missing"
        assert whole.get("observed", {}) == {}
        assert result.get("nextTool") is None
        return

    assert result["status"] == "lifecycle-operation-retired"
    assert whole["observed"] == {"operationKind": kind}
    assert result["nextTool"] == replacement
    assert result["nextAction"] == "re-apply"
    expected_args = {"contract_path": contract_path}
    if kind == "closeout":
        expected_args["intent_note"] = "<fresh developer intent>"
    assert result["nextArgs"] == expected_args


def test_a_stale_generation_keeps_the_exact_identity_refusal(tmp_path: Path) -> None:
    fixture = _converted_series_fixture(tmp_path)
    _, generation = _seeded_generation(fixture, "closeout")
    result, whole = _registered_control_with_whole_result(
        fixture["config"],
        OperationControlRequest(
            contract_path=fixture["contract"].contract_path.as_posix(),
            operation_kind="closeout",
            action="resume",
            expected_generation=generation + 1,
            intent_note="Resume the advertised generation.",
        ),
    )

    assert result["ok"] is False, result
    assert result["status"] == "lifecycle-generation-changed"
    assert whole["expected"] == {"generation": generation + 1}
    assert whole["observed"] == {"generation": generation}
