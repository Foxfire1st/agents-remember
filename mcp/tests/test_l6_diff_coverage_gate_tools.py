"""L6 closeout coverage tests for gate-tool helper branches."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application import gate_tools
from agents_remember.application.gate_tools import (
    _gating_lifecycle,
    _resolve_gate_lifecycle_id,
    _validated_ask,
    _write_verdict_by_row,
    gate_wait_tool,
    record_gate_decision,
    record_lifecycle_gate_decision,
)
from agents_remember.controlplane.records import GateRecord
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)
from agents_remember.models.application_requests import GateDecisionRequest
from agents_remember.observer.lifecycle_state import LifecycleError, LifecycleState


class TestHelperBranches:
    def test_expectation_sla_without_root(self) -> None:
        assert (
            gate_tools._expectation_sla_seconds(
                cast(McpRuntimeConfig, SimpleNamespace()), "verdict-by"
            )
            is not None
        )

    def test_write_verdict_by_row_without_root(self) -> None:
        _write_verdict_by_row(
            cast(McpRuntimeConfig, SimpleNamespace()),
            cast(GateRecord, SimpleNamespace(id="g", kind="x", lifecycleId="L")),
        )

    def test_resolve_gate_lifecycle_id(self) -> None:
        assert _resolve_gate_lifecycle_id("L") == "L"
        with pytest.raises(ValueError, match="must be non-empty"):
            _resolve_gate_lifecycle_id("   ")
        with (
            mock.patch.object(gate_tools, "ambient", return_value=None),
            pytest.raises(LifecycleError, match="requires an active lifecycle"),
        ):
            _resolve_gate_lifecycle_id(None)

    def test_gating_lifecycle(self) -> None:
        current = SimpleNamespace(id="L", state="running")
        typed = cast(LifecycleState, current)
        assert _gating_lifecycle(typed, None) is typed
        assert _gating_lifecycle(typed, "L") is typed
        with pytest.raises(LifecycleError, match="requires an active lifecycle"):
            _gating_lifecycle(None, None)
        with pytest.raises(LifecycleError, match="does not match active lifecycle"):
            _gating_lifecycle(typed, "OTHER")
        with pytest.raises(LifecycleError, match="only running lifecycles gate"):
            _gating_lifecycle(cast(LifecycleState, SimpleNamespace(id="L", state="blocked")), "L")

    def test_validated_ask(self) -> None:
        assert _validated_ask(None) == (None, None, None)
        assert _validated_ask({"kind": "x", "prompt": "p", "options": ["a"]}) == (
            "x",
            "p",
            ["a"],
        )
        with pytest.raises(ValueError, match=r"ask\.kind must be a string"):
            _validated_ask({"kind": 1})
        with pytest.raises(ValueError, match=r"ask\.prompt must be a string"):
            _validated_ask({"prompt": 1})
        with pytest.raises(ValueError, match=r"ask\.options must be a list of strings"):
            _validated_ask({"options": ["a", 1]})
        with pytest.raises(ValueError, match=r"ask\.options must be a list of strings"):
            _validated_ask({"options": "x"})

    def test_record_decisions(self) -> None:
        with pytest.raises(ValueError, match="requires gate_id"):
            record_gate_decision(
                cast(McpRuntimeConfig, SimpleNamespace()),
                cast(GateDecisionRequest, SimpleNamespace(gate_id=None)),
            )
        with pytest.raises(ValueError, match="requires lifecycle_id"):
            record_lifecycle_gate_decision(
                cast(McpRuntimeConfig, SimpleNamespace()),
                cast(GateDecisionRequest, SimpleNamespace(lifecycle_id=None)),
            )


class TestGateWait:
    def test_missing_gate(self) -> None:
        store = SimpleNamespace(current=lambda lifecycle_id: {})
        with (
            mock.patch.object(gate_tools, "_store", return_value=store),
            pytest.raises(KeyError, match="no gate"),
        ):
            gate_wait_tool(cast(McpRuntimeConfig, SimpleNamespace()), gate_id="g", lifecycle_id="L")

    def test_gate_resolved(self) -> None:
        gate = SimpleNamespace(
            id="g",
            state="decided",
            decidedBy="developer",
            decidedVia="dashboard",
            decisionNote="ok",
        )
        store = SimpleNamespace(current=lambda lifecycle_id: {"g": gate})
        with mock.patch.object(gate_tools, "_store", return_value=store):
            result = gate_wait_tool(
                cast(McpRuntimeConfig, SimpleNamespace()), gate_id="g", lifecycle_id="L"
            )
        assert result["ok"] is True and result["state"] == "decided"
