"""Public forcing for pre-authority lifecycle-control request validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from agents_remember.application.worktree_tools import OperationControlRequest
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.mcp.tools.worktree import worktree_operation_control_payload
from test_lifecycle_operations import _contract


def _byte_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize(
    ("updates", "expected", "observed"),
    [
        (
            {"expected_generation": 0},
            {"field": "expected_generation", "minimum": 1},
            {"field": "expected_generation", "value": 0},
        ),
        (
            {"intent_note": " \n\t "},
            {"field": "intent_note", "state": "nonblank"},
            {"field": "intent_note", "state": "blank"},
        ),
        (
            {"code_commit_message": "PRIVATE-REQUEST-SENTINEL /secret/input"},
            {"action": "retry", "commitMessageFields": "absent"},
            {"action": "retry", "presentFields": ["code_commit_message"]},
        ),
    ],
)
def test_public_operation_control_payload_refuses_invalid_request_before_authority(
    tmp_path: Path,
    updates: dict[str, object],
    expected: dict[str, object],
    observed: dict[str, object],
) -> None:
    contract = _contract(tmp_path)
    config = load_config(tmp_path / "settings.json")
    before = _byte_tree(tmp_path)
    values: dict[str, object] = {
        "contract_path": contract.contract_path.as_posix(),
        "operation_kind": "closeout",
        "action": "retry",
        "expected_generation": 1,
        "intent_note": "retry exact accepted generation",
    }
    values.update(updates)

    payload = worktree_operation_control_payload(
        config,
        OperationControlRequest(**cast(dict[str, Any], values)),
    )

    assert payload == {
        "ok": False,
        "operation": "worktree_operation_control",
        "state": "refused",
        "status": "lifecycle-control-request-invalid",
        "detail": "the lifecycle operation control request is invalid",
        "expected": expected,
        "observed": observed,
        "nextAction": "correct-request",
    }
    assert not {
        "nextTool",
        "nextArgs",
        "legalControls",
        "lifecycleOperation",
        "operationKey",
        "claimedOperationKey",
    } & set(payload)
    assert "PRIVATE-REQUEST-SENTINEL" not in repr(payload)
    assert _byte_tree(tmp_path) == before
