from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest import mock

from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxMessage,
    InboxPoster,
    InboxRouting,
    create_operator_inbox_entry,
)
from agents_remember.serving import _agent_notifier_actions as actions
from agents_remember.serving.agent_notifier_models import (
    AgentNotifierActionResult,
    AgentNotifierContext,
    AgentNotifierFinding,
    FindingKind,
    SweepState,
)
from agents_remember.tasks.document_refs import TaskDocumentTopology


def _finding(kind: FindingKind, source_id: str | None) -> AgentNotifierFinding:
    return AgentNotifierFinding(kind=kind, detail="expiry", source_id=source_id)


def test_batched_expiry_preparation_fails_closed_on_unaddressable_rows() -> None:
    ctx = cast(AgentNotifierContext, SimpleNamespace(catalog=object()))
    topology = cast(TaskDocumentTopology, object())
    empty = SweepState(inbox_current={}, redeliver_budget=1)
    no_source = actions._prepare_known_expiry(
        ctx, _finding("rebind-expired", None), topology=topology, sweep=empty
    )
    assert isinstance(no_source, AgentNotifierActionResult)
    assert no_source.detail == "no source entry id"
    missing = actions._prepare_known_expiry(
        ctx, _finding("inbox-ttl-expired", "missing"), topology=topology, sweep=empty
    )
    assert isinstance(missing, AgentNotifierActionResult)
    assert missing.detail == "entry not pending"

    row = create_operator_inbox_entry(
        InboxMessage(ask="ask", response="response", message_kind="message"),
        entry_id="e1",
        now="2026-08-12T12:00:00+00:00",
        routing=InboxRouting(address=InboxAddress(agent_id="worker-1")),
        poster=InboxPoster(created_by="system", created_via="cli"),
    )
    sweep = SweepState(inbox_current={row.id: row}, redeliver_budget=1)
    with mock.patch.object(
        actions, "derive_row_owner", return_value=SimpleNamespace(agent_id="replacement")
    ):
        assert (
            actions._prepare_known_expiry(
                ctx, _finding("rebind-expired", row.id), topology=topology, sweep=sweep
            )
            is None
        )
    with mock.patch.object(
        actions, "derive_row_owner", return_value=SimpleNamespace(agent_id="worker-1")
    ):
        result = actions._prepare_known_expiry(
            ctx, _finding("rebind-expired", row.id), topology=topology, sweep=sweep
        )
    assert isinstance(result, AgentNotifierActionResult)
    assert result.detail == "row has no structural task address"
