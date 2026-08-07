from __future__ import annotations

from typing import get_args

import pytest
from agents_remember.serving.conversation.models import (
    ActiveEventCursor,
    CapabilityEvidence,
    ContextMetricValue,
    ConversationAgentRef,
    ConversationAgentStatus,
    ConversationItem,
    ConversationLibraryAgentRow,
    ConversationLibraryPage,
    ConversationLibraryPageScope,
    ConversationLibraryRow,
    ConversationTelemetry,
    FeatureCapability,
    GapMutation,
    HistoricalConversationPage,
    LibraryConversationKey,
    LibraryReadCursor,
    MetricEvidence,
    MetricScope,
    RuntimeFixtureEvidence,
)
from pydantic import ValidationError
from test_conversation_contracts import _active_ref, _history_capabilities, _unknown_input_item


def test_historical_page_total_is_optional_and_native_items_keep_global_ordinals() -> None:
    page = HistoricalConversationPage(
        ref=_active_ref(),
        items=(_unknown_input_item(),),
        older_cursor=LibraryReadCursor("ar-lrc1.read-1"),
        has_older=True,
        historical_capabilities=_history_capabilities(),
    )
    assert page.total_items is None
    assert page.items[0].global_ordinal == 7


def test_gap_contract_closes_and_requires_authoritative_repage() -> None:
    gap = GapMutation(
        requested_after=ActiveEventCursor("ar-aec1.event-9"),
        reason="projector-restart",
    )
    assert gap.requires_repage is True
    assert gap.close_after_event is True
    assert "reset" not in gap.model_dump(mode="json")


def test_every_metric_retains_scope_freshness_precision_and_runtime_evidence() -> None:
    metric = MetricEvidence[ContextMetricValue](
        value=ContextMetricValue(used=1024, limit=4096, percent=25),
        unit="tokens",
        origin="codex thread/tokenUsage/updated",
        scope=MetricScope(kind="conversation", safe_id="thread-digest"),
        observed_at="2026-07-18T08:00:00Z",
        freshness="fresh",
        precision="exact",
        runtime_version="0.144.5",
        fixture_id="codex-0.144.5-installed",
    )
    telemetry = ConversationTelemetry(
        revision=2,
        identity=_active_ref(),
        context=metric,
    )
    assert telemetry.context is not None
    assert telemetry.context.scope.kind == "conversation"
    assert telemetry.context.precision == "exact"
    assert telemetry.context.runtime_version == "0.144.5"


def test_runtime_fixture_is_evidence_and_cannot_enable_capabilities() -> None:
    fixture = RuntimeFixtureEvidence.model_validate(
        {
            "schema": "ar-conversation-runtime-fixture/v1",
            "fixtureId": "claude-2.1.211-installed",
            "harnessId": "claude",
            "runtimeVersion": "2.1.211",
            "helperVersion": "0.3.207",
            "capturedAt": "2026-07-18T08:00:00Z",
            "productionSeam": "ClaudeStreamJsonAdapter.start",
            "redactionPolicy": "allowlist-v1",
            "enablesCapabilities": False,
            "observations": [
                {
                    "operation": "locked-helper/list-read-resume",
                    "shape": [],
                    "result": "not-exercised",
                    "reason": "installed 2.1.211 interoperability remains unproved",
                }
            ],
        }
    )
    assert fixture.enables_capabilities is False
    assert fixture.observations[0].result == "not-exercised"
    claude_history = FeatureCapability(
        state="unverified",
        reason="installed Claude/helper history gate was not exercised",
        evidence_tier="runtime-fixture",
        evidence=CapabilityEvidence(
            runtime_version=fixture.runtime_version,
            helper_version=fixture.helper_version,
            fixture_id=fixture.fixture_id,
            observed_at=fixture.captured_at,
        ),
    )
    assert claude_history.state == "unverified"
    with pytest.raises(ValidationError):
        RuntimeFixtureEvidence.model_validate(
            {**fixture.model_dump(by_alias=True), "enablesCapabilities": True}
        )


def test_agent_ref_pins_status_vocabulary_and_additive_camel_case_shape() -> None:
    # The agent ref is additive — only agentId is required and the
    # identity fields stay absent (never null-claiming) until native evidence binds them.
    assert get_args(ConversationAgentStatus) == (
        "registered",
        "running",
        "completed",
        "interrupted",
        "failed",
        "unknown",
    )
    minimal = ConversationAgentRef.model_validate({"agentId": "agent-1"})
    assert minimal.status == "unknown"
    assert minimal.model_dump(mode="json", by_alias=True, exclude_none=True) == {
        "agentId": "agent-1",
        "status": "unknown",
    }

    full = ConversationAgentRef.model_validate(
        {
            "agentId": "agent-1",
            "agentPath": "threads/agent-1.jsonl",
            "nickname": "explorer",
            "role": "worker",
            "joinKey": "tool-use-1",
            "parentAgentId": "parent-thread-1",
            "status": "running",
        }
    )
    assert ConversationAgentRef.model_validate(full.model_dump(mode="json", by_alias=True)) == full
    for status in get_args(ConversationAgentStatus):
        assert ConversationAgentRef(agent_id="agent-1", status=status).status == status

    with pytest.raises(ValidationError):
        ConversationAgentRef.model_validate({"agentId": "agent-1", "status": "spun-up"})
    with pytest.raises(ValidationError):
        ConversationAgentRef.model_validate({"status": "running"})
    with pytest.raises(ValidationError):
        ConversationAgentRef.model_validate({"agentId": ""})
    with pytest.raises(ValidationError, match="Extra inputs"):
        ConversationAgentRef.model_validate({"agentId": "agent-1", "vendorGuess": "x"})


def test_item_agent_is_absent_by_default_and_the_pre_l7_wire_stays_identical() -> None:
    item = _unknown_input_item()
    assert item.agent is None
    wire = item.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert "agent" not in wire
    # An old consumer reading the pre-multiplex wire still decodes to no agent.
    assert ConversationItem.model_validate(wire).agent is None

    with_agent = ConversationItem.model_validate(
        {**item.model_dump(), "agent": {"agentId": "agent-1", "status": "running"}}
    )
    agent_wire = with_agent.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert agent_wire["agent"] == {"agentId": "agent-1", "status": "running"}
    # Every other key keeps the exact pre-multiplex shape; the agent is purely additive.
    assert {key: value for key, value in agent_wire.items() if key != "agent"} == wire
    assert ConversationItem.model_validate(agent_wire) == with_agent


def test_library_agent_row_is_additive_and_evidence_bound() -> None:
    key = LibraryConversationKey("ar-lck1.conversation-1")
    minimal = ConversationLibraryAgentRow(
        conversation_key=key,
        identity_digest="digest-1",
        title="agent abcd1234",
    )
    assert minimal.model_dump(mode="json", by_alias=True, exclude_none=True) == {
        "conversationKey": "ar-lck1.conversation-1",
        "identityDigest": "digest-1",
        "title": "agent abcd1234",
    }

    full = ConversationLibraryAgentRow.model_validate(
        {
            "conversationKey": "ar-lck1.conversation-1",
            "identityDigest": "digest-1",
            "title": "explorer",
            "agentPath": "threads/agent-1.jsonl",
            "nickname": "explorer",
            "role": "worker",
            "model": "gpt-5",
            "joinKey": "tool-use-1",
            "safeNativeIdSuffix": "abcd1234",
            "lastActivityAt": "2026-07-18T08:00:00Z",
        }
    )
    assert (
        ConversationLibraryAgentRow.model_validate(full.model_dump(mode="json", by_alias=True))
        == full
    )
    with pytest.raises(ValidationError):
        ConversationLibraryAgentRow.model_validate(
            {"conversationKey": "ar-lck1.conversation-1", "identityDigest": "digest-1"}
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        ConversationLibraryAgentRow.model_validate(
            {**minimal.model_dump(by_alias=True), "vendorGuess": "x"}
        )


def test_library_row_agents_and_page_agents_note_default_to_absent() -> None:
    key = LibraryConversationKey("ar-lck1.conversation-1")
    agent_row = ConversationLibraryAgentRow(
        conversation_key=key,
        identity_digest="digest-2",
        title="agent ef567890",
    )
    row = ConversationLibraryRow(
        conversation_key=key,
        identity_digest="digest-1",
        title="parent conversation",
        capabilities=_history_capabilities(),
    )
    assert row.agents == ()

    grouped = ConversationLibraryRow.model_validate(
        {**row.model_dump(), "agents": [agent_row.model_dump()]}
    )
    assert grouped.agents == (agent_row,)
    assert (
        ConversationLibraryRow.model_validate(grouped.model_dump(mode="json", by_alias=True))
        == grouped
    )

    page = ConversationLibraryPage(
        scope=ConversationLibraryPageScope(
            harness_id="codex",
            canonical_project_scope="/workspace/project",
            query_digest="sort=activity-desc",
        ),
        rows=(row,),
        next_cursor=None,
    )
    assert page.agents_note is None
    assert "agentsNote" not in page.model_dump(mode="json", by_alias=True, exclude_none=True)

    noted = ConversationLibraryPage.model_validate(
        {
            **page.model_dump(by_alias=True, exclude={"agents_note"}),
            "agentsNote": "installed runtime does not expose sub-agent threads",
        }
    )
    assert noted.agents_note == "installed runtime does not expose sub-agent threads"
