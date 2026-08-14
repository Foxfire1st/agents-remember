"""Child-local native-history projection outcomes and visible recovery state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agents_remember.models.conversations.content import (
    ConversationAgentRef,
    ConversationItem,
    TextBlock,
)
from agents_remember.models.conversations.identity import (
    ProvenanceEvidence,
)
from agents_remember.serving.conversation.projectors.common import MappedItem

AgentHistoryStatus = Literal["hydrated", "already-hydrated", "unavailable", "not-eligible"]


@dataclass(frozen=True)
class AgentHistoryHydration:
    """One selected child's history outcome; never parent bridge state."""

    status: AgentHistoryStatus
    thread_id: str
    detail: str | None = None
    code: str | None = None


def agent_history_state_item(
    *,
    agent: ConversationAgentRef,
    status: Literal["unavailable", "recovered"],
    detail: str,
    observed_at: str,
) -> MappedItem:
    """Mint/upsert one child-bound, recoverable history state row."""

    unavailable = status == "unavailable"
    text = (
        "Native history is temporarily unavailable. Select this agent again to retry."
        if unavailable
        else "Native history backfill recovered."
    )
    return MappedItem(
        item=ConversationItem(
            item_id=f"agent-history:{agent.agent_id}",
            revision=1,
            global_ordinal=1,
            lane="harness",
            source="native-history",
            provenance=ProvenanceEvidence(
                strength="native-only",
                origin="selected child native-history acquisition",
                producer="harness",
                observed_at=observed_at,
                reason=detail,
            ),
            role="system",
            kind="error" if unavailable else "notice",
            phase="failed" if unavailable else "completed",
            blocks=(
                TextBlock(
                    block_id="history-state",
                    text=text,
                ),
            ),
            agent=agent,
            created_at=observed_at,
        )
    )
