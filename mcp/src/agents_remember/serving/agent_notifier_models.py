"""Typed state carried by one agent-notifier sweep."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from agents_remember.controlplane.agent_notifier_signals import (
    AgentNotifierSignalCooldownStore,
    AgentNotifierSignalRecord,
)
from agents_remember.controlplane.expectation_rows import ExpectationRow, ExpectationRowStore
from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.orchestration_nudges import OrchestrationNudgeStore
from agents_remember.observer.store import EventStore
from agents_remember.serving.agent_notifier_heartbeat import AgentNotifierHeartbeatStore
from agents_remember.serving.inbox_reclamation import (
    TmuxSessionNameSnapshotter,
    snapshot_tmux_session_names,
)
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog
from agents_remember.serving.terminal_paste import TerminalPaster

FindingKind = Literal[
    "pane-signal",
    "expectation-overdue",
    "inbox-redeliverable",
    "inbox-ladder-terminal",
    "seat-liveness",
    "escalation-due",
    "dead-upstream",
    "state-signal-due",
    "compound-idle-due",
    "non-reaction-due",
    "boundary-drain",
    "rebind-due",
    "rebind-expired",
    "inbox-ttl-expired",
]
ActionKind = Literal[
    "redeliver",
    "ladder-resolve",
    "auto-nudge",
    "signal-emit",
    "escalate-rung",
    "signal-manager",
    "state-signal",
    "compound-idle",
    "non-reaction",
    "boundary-drain",
    "rebind",
    "expire",
    "none",
]

DEFAULT_RESPAWN_AFTER_RUNG = 2


@dataclass(frozen=True)
class AgentNotifierFinding:
    """One predicate hit: what the sweep saw, read straight off a store."""

    kind: FindingKind
    detail: str
    session_id: str | None = None
    leaf_key: str | None = None
    seat_role: str | None = None
    source_id: str | None = None


@dataclass(frozen=True)
class AgentNotifierActionResult:
    """One action the sweep took (or explicitly skipped) for a finding."""

    action: ActionKind
    finding: AgentNotifierFinding
    outcome: str
    detail: str | None = None


@dataclass(frozen=True)
class AgentNotifierSweepResult:
    findings: tuple[AgentNotifierFinding, ...]
    actions: tuple[AgentNotifierActionResult, ...]
    swept_at: str
    pending_inbox_count: int = 0
    redeliverable_inbox_count: int = 0
    duration_seconds: float | None = None


@dataclass(frozen=True)
class AgentNotifierContext:
    """Stores, delivery seams, and resolved settings required by one sweep."""

    catalog: TerminalCatalog
    host: TerminalHost
    paster: TerminalPaster
    inbox_store: OperatorInboxStore
    expectation_store: ExpectationRowStore
    nudge_store: OrchestrationNudgeStore
    signal_cooldown_store: AgentNotifierSignalCooldownStore
    event_store: EventStore
    heartbeat_store: AgentNotifierHeartbeatStore
    coordination_root: Path
    stale_seat_seconds: float = 120.0
    redeliver_rate_limit_seconds: float | None = None
    signal_cooldown_seconds: float = 900.0
    nudge_rate_limit_seconds: int = 900
    escalation_sla_seconds: dict[str, float] = field(default_factory=dict)
    escalation_rung_seconds: dict[int, float] = field(default_factory=dict)
    respawn_after_rung: int = DEFAULT_RESPAWN_AFTER_RUNG
    redeliver_budget: int = 1
    escalation_budget: int = 250
    tmux_name_snapshotter: TmuxSessionNameSnapshotter = snapshot_tmux_session_names


@dataclass
class SweepState:
    inbox_current: dict[str, OperatorInboxEntry]
    redeliver_budget: int
    pending_inbox_count: int = 0
    redeliverable_entries: list[OperatorInboxEntry] = field(default_factory=list)
    signal_current: list[AgentNotifierSignalRecord] | None = None
    expectation_current: dict[str, ExpectationRow] | None = None
    escalated_entry_ids: set[str] = field(default_factory=set)

    @property
    def redeliverable_inbox_count(self) -> int:
        return len(self.redeliverable_entries)

    def remember(self, entry: OperatorInboxEntry) -> None:
        self.inbox_current[entry.id] = entry

    def remember_signal(self, record: AgentNotifierSignalRecord) -> None:
        if self.signal_current is not None:
            self.signal_current.append(record)

    def remember_expectation(self, row: ExpectationRow) -> None:
        if self.expectation_current is not None:
            self.expectation_current[row.id] = row
