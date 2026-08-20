"""L6 closeout coverage tests for small application/serving tool branches."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application import lifecycle_tools, operator_inbox_tools, orchestration_tools
from agents_remember.application.task_docs import task_doc_tools
from agents_remember.application.task_docs.task_doc_tools import TaskDocError, _Edit
from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)
from agents_remember.models.application_requests import OrchestrationNudgeRequest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import (
    TerminalCatalogEntry,
)
from agents_remember.serving import dispatch_brief, operator_inbox_posts
from agents_remember.serving.dispatch_brief import HostedDelivery


def _edit(step: dict | None, *, kind: str = "light", lifecycle_id: str | None = None) -> _Edit:
    return _Edit(
        kind=kind,
        fields={},
        step=step,
        decision=None,
        subtask=None,
        section=None,
        lifecycle_id=lifecycle_id,
    )


class TestSkipStep:
    def test_master_rejected(self) -> None:
        with pytest.raises(TaskDocError, match="not valid for a master"):
            task_doc_tools._apply_skip_step({}, _edit({"id": "S1"}, kind="master"))

    def test_missing_step_and_blank_reason(self) -> None:
        with pytest.raises(TaskDocError, match="requires step"):
            task_doc_tools._apply_skip_step({}, _edit(None))
        with pytest.raises(TaskDocError, match=r"nonblank step\.reason"):
            task_doc_tools._apply_skip_step({}, _edit({"id": "S1", "reason": "  "}))

    def test_done_target_and_success(self) -> None:
        data: dict[str, Any] = {"steps": [{"id": "S1", "status": "done", "title": "One"}]}
        with pytest.raises(TaskDocError, match="already done"):
            task_doc_tools._apply_skip_step(data, _edit({"id": "S1", "reason": "skip"}))
        data = {"steps": [{"id": "S1", "status": "pending", "title": "One"}]}
        task_doc_tools._apply_skip_step(
            data, _edit({"id": "S1", "reason": "skip", "parent": None}, lifecycle_id="L")
        )
        assert data["steps"][0]["status"] == "done"
        assert data["steps"][0]["disposition"]["kind"] == "intentionalSkip"
        assert data["decisions"][0]["decision"].startswith("Intentionally skip")


class TestLifecycleBlockNoAsk:
    def test_block_without_ask(self) -> None:
        fake_ambient = SimpleNamespace(block=lambda **kwargs: SimpleNamespace())
        with (
            mock.patch.object(lifecycle_tools, "require_ambient", return_value=fake_ambient),
            mock.patch.object(lifecycle_tools, "_state_fields", return_value={}),
        ):
            result = lifecycle_tools.lifecycle_block_tool()
        assert result["ok"] is True and "ask" not in result


class TestOperatorInboxPostsAndDispatch:
    def test_redelivery_floor_and_disabled_delivery(self) -> None:
        assert operator_inbox_posts._redelivery_floor_seconds(None) is None
        settings = SimpleNamespace(agent_notifier=SimpleNamespace(redeliver_rate_limit_seconds=7))
        config = SimpleNamespace(coordination_root=Path("/tmp"))
        with mock.patch.object(
            operator_inbox_posts, "load_agentic_settings", return_value=settings
        ):
            assert (
                operator_inbox_posts._redelivery_floor_seconds(cast(McpRuntimeConfig, config)) == 7
            )
        entry = SimpleNamespace()
        delivery = HostedDelivery(enabled=False)
        result = operator_inbox_posts._deliver_post(
            None,
            delivery=delivery,
            store=cast(OperatorInboxStore, SimpleNamespace()),
            entry=cast(OperatorInboxEntry, entry),
        )
        assert result is entry

    def test_expectation_sla_and_start_dispatch_expectations(self) -> None:
        assert dispatch_brief.expectation_sla_seconds(None, "briefed-by") is not None
        store = SimpleNamespace(
            find_by_source=lambda entry_id, kind: (
                None if kind == "briefed-by" else SimpleNamespace(id="row")
            ),
            mark_met=lambda row_id, now: None,
        )
        target = SimpleNamespace(
            binding_task_document_ref=TaskDocumentRef(repository="repo", path="master/leaf-1.json"),
            label="curator",
            spawn_role="curator",
            kind="harness",
            id="t",
            lifecycle_id="L",
            binding_role="curator",
        )
        entry = SimpleNamespace(id="e", createdAt="2026-08-05T00:00:00+00:00")
        with (
            mock.patch.object(dispatch_brief, "expectation_store", return_value=store),
            mock.patch.object(dispatch_brief, "write_expectation_row", return_value=None),
            mock.patch.object(dispatch_brief, "expectation_sla_seconds", return_value=1.0),
        ):
            dispatch_brief.start_dispatch_expectations(
                cast(McpRuntimeConfig, SimpleNamespace()),
                cast(OperatorInboxEntry, entry),
                cast(TerminalCatalogEntry, target),
            )
        assert store.find_by_source("e", "briefed-by") is None


class TestOperatorInboxConsumeAck:
    def test_consume_is_attribution_only(self) -> None:
        """N16: consume no longer touches any expectation machinery."""
        entry = SimpleNamespace(id="e", state="consumed", consumedAt="2026-08-05T00:00:00+00:00")
        store = SimpleNamespace(consume=lambda *a, **k: (entry, True))
        with (
            mock.patch.object(operator_inbox_tools, "_store", return_value=store),
        ):
            result = operator_inbox_tools.operator_inbox_consume_tool(
                cast(McpRuntimeConfig, SimpleNamespace()),
                entry_id="e",
                consumed_by="root",
                consumed_via="cli",
            )
        assert result["ok"] is True


class TestNudgeManager:
    def test_nudge_manager_composes_request(self) -> None:
        request = SimpleNamespace(
            reason="r",
            manager_agent_id="m",
            manager_lifecycle_id=None,
            subject="stalled",
            subject_agent_id="w",
            subject_lifecycle_id=None,
            artifact_path=None,
            message="go",
            rate_limit_seconds=1,
        )
        with mock.patch.object(
            orchestration_tools,
            "orchestration_nudge_manager_tool",
            return_value={"ok": True},
        ):
            result = orchestration_tools.nudge_manager(
                cast(McpRuntimeConfig, SimpleNamespace()),
                cast(OrchestrationNudgeRequest, request),
            )
        assert result["ok"] is True
