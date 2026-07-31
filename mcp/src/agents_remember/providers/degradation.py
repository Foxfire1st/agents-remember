"""Provider degradation detector and response protocol.

The detector consumes the central provider metrics log produced by
``providers.metrics`` and owns the provider-only response protocol:
healthy/degraded/critical state transitions, durable events, inbox alerts, and
the critical provider-stop failsafe. It deliberately stays outside the serving
app so the dashboard loop only has to call one focused service after sampling.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from agents_remember.controllers.provider_tools import provider_watchers_tool
from agents_remember.controlplane.operator_inbox_records import (
    AgentRole,
    InboxAddress,
    InboxMessage,
    InboxPoster,
    InboxRouting,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.mcp.config import ProviderDegradationSettings
from agents_remember.observer import observer_root
from agents_remember.observer.events import now_iso
from agents_remember.observer.ulid import new_ulid
from agents_remember.providers.metrics import (
    PROVIDER_INDEX_STATE_SCHEMA,
    PROVIDER_METRICS_SCHEMA,
    ProviderMetricsStore,
)
from agents_remember.serving.hosted_session_runtime import HostedSessionRuntime
from agents_remember.serving.inbox_delivery import InboxDeliveryLog, deliver_inbox_entry
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog, terminal_catalog_path
from agents_remember.serving.terminal_paste import TerminalPaster

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig

DegradationState = Literal["healthy", "degraded", "critical"]
_LEVELS: dict[DegradationState, int] = {"healthy": 0, "degraded": 1, "critical": 2}

DEGRADATION_STATE_SCHEMA = "ar-provider-degradation-state/v1"
DEGRADATION_EVENT_SCHEMA = "ar-provider-degradation-event/v1"

# 260707-HFX2-L12 F5: cap for the append-only degradation-events audit log. Events fire only on a
# state change (rare), so this bound is generous; it exists to stop unbounded growth over years.
DEGRADATION_EVENT_RETAIN_ROWS = 1_000

ORCHESTRATOR_DEGRADATION_INSTRUCTION = (
    "Dispatch AR_SPAWN_ROLE=system-specialist to investigate this provider degradation "
    "event and write a report. Read that report before ordering a fix. If the report says "
    "the issue is not fixable in-session, stop providers through provider_watchers stop."
)
MANAGER_DEGRADATION_INSTRUCTION = (
    "Do not start provider setup, provider watchers, watcher restart, or retry_provider_setup "
    "until an all-clear degradation event arrives. Managers have no provider kill authority; "
    "escalate provider action to the orchestrator."
)
_ALERT_TARGETS: tuple[tuple[AgentRole, str], ...] = (
    ("orchestrator", ORCHESTRATOR_DEGRADATION_INSTRUCTION),
    ("manager", MANAGER_DEGRADATION_INSTRUCTION),
)


@dataclass(frozen=True)
class ProviderDegradationState:
    """The persisted state-machine position."""

    state: DegradationState
    entered_at: str
    last_evaluated_at: str
    last_event_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": DEGRADATION_STATE_SCHEMA,
            "state": self.state,
            "enteredAt": self.entered_at,
            "lastEvaluatedAt": self.last_evaluated_at,
            "lastEventId": self.last_event_id,
        }


@dataclass(frozen=True)
class DegradationEvidence:
    """One detector finding that contributes to the state decision."""

    level: DegradationState
    reason: str
    affected: str

    def to_payload(self) -> dict[str, str]:
        return {"level": self.level, "reason": self.reason, "affected": self.affected}


class ProviderDegradationStore:
    """Durable state and event log under the central provider observer root."""

    def __init__(self, coordination_root: Path) -> None:
        self._root = coordination_root / "logs" / "observer" / "providers"

    @property
    def state_path(self) -> Path:
        return self._root / "degradation-state.json"

    @property
    def events_path(self) -> Path:
        return self._root / "degradation-events.jsonl"

    def read_state(self, *, now: str) -> ProviderDegradationState:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        state = _coerce_state(data.get("state"))
        return ProviderDegradationState(
            state=state,
            entered_at=str(data.get("enteredAt") or now),
            last_evaluated_at=str(data.get("lastEvaluatedAt") or now),
            last_event_id=(
                str(data["lastEventId"]) if data.get("lastEventId") is not None else None
            ),
        )

    def write_state(self, state: ProviderDegradationState) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_name(f"{self.state_path.name}.tmp")
        tmp.write_text(json.dumps(state.to_payload(), indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_path)

    def append_event(self, event: dict[str, Any]) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")

    def compact_events(self, *, retain_rows: int = DEGRADATION_EVENT_RETAIN_ROWS) -> int:
        """Reclaim degradation-events.jsonl to its newest `retain_rows` events; return rows dropped.

        260707-HFX2-L12 F5/CS-6 D3: degradation events are only written on a state change (rare), so a
        full read-fold on that rare path is cheap; this gives the append-only audit log a bounded cap."""
        path = self.events_path
        if not path.exists():
            return 0
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(lines) <= retain_rows:
            return 0
        kept = lines[-retain_rows:]
        self._root.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".compact.tmp")
        tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return len(lines) - len(kept)


ProviderStopper = Callable[["McpRuntimeConfig"], dict[str, Any]]


def evaluate_provider_degradation(
    config: McpRuntimeConfig,
    *,
    stop_provider_stacks: ProviderStopper | None = None,
) -> dict[str, Any]:
    """Evaluate metrics, emit one event per state change, and run the critical failsafe."""

    settings = config.provider_degradation
    if not settings.enabled:
        return {"ok": True, "state": "disabled", "event": None}

    now = now_iso()
    store = ProviderDegradationStore(config.coordination_root)
    previous = store.read_state(now=now)
    metric_store = ProviderMetricsStore(config.coordination_root)
    rows = metric_store.read_recent(limit=settings.recent_sample_limit)
    state, evidence = classify_degradation(rows, previous.state, settings, now=now)
    event: dict[str, Any] | None = None

    if state != previous.state:
        event = _build_event(
            _DegradationTransition(
                event_id=new_ulid(),
                previous=previous.state,
                state=state,
                at=now,
            ),
            evidence,
            rows=rows,
            metric_store=metric_store,
        )
        if state == "critical" and settings.fail_safe_enabled:
            stopper = stop_provider_stacks or _stop_provider_stacks
            event["criticalFailsafe"] = {
                "enabled": True,
                "action": "provider_watchers stop",
                "result": _run_critical_failsafe(stopper, config),
            }
        elif state == "critical":
            event["criticalFailsafe"] = {"enabled": False}
        store.append_event(event)
        store.compact_events()  # F5: bound the append-only degradation audit log
        _post_degradation_alerts(config, event)

    updated = ProviderDegradationState(
        state=state,
        entered_at=now if state != previous.state else previous.entered_at,
        last_evaluated_at=now,
        last_event_id=event["id"] if event is not None else previous.last_event_id,
    )
    store.write_state(updated)
    return {"ok": True, "state": state, "event": event}


def classify_degradation(
    rows: list[dict[str, Any]],
    previous_state: DegradationState,
    settings: ProviderDegradationSettings,
    *,
    now: str,
) -> tuple[DegradationState, list[DegradationEvidence]]:
    """Pure state-machine decision over recent provider metric rows."""

    if not rows:
        return "healthy", []

    window = max(settings.degraded_samples, settings.critical_samples, settings.healthy_samples)
    tail = rows[-window:]
    now_dt = _parse_time(now) or datetime.now(UTC)
    all_evidence = [finding for row in tail for finding in _row_evidence(row, settings, now_dt)]
    all_evidence.extend(_setup_failure_streak(rows, settings))
    level = _candidate_level(tail, all_evidence, previous_state, settings, now_dt)
    relevant = [item for item in all_evidence if _LEVELS[item.level] >= _LEVELS[level]]
    return level, relevant or all_evidence


def _candidate_level(
    tail: list[dict[str, Any]],
    evidence: list[DegradationEvidence],
    previous_state: DegradationState,
    settings: ProviderDegradationSettings,
    now_dt: datetime,
) -> DegradationState:
    sustained = _sustained_level(evidence)
    if sustained is not None:
        return sustained

    row_levels: list[DegradationState] = [_row_level(row, settings, now_dt) for row in tail]
    threshold = _threshold_level(row_levels, settings)
    if threshold is not None:
        return threshold
    if _has_healthy_tail(row_levels, settings):
        return "healthy"
    return previous_state


def _threshold_level(
    row_levels: list[DegradationState],
    settings: ProviderDegradationSettings,
) -> DegradationState | None:
    critical_count = sum(1 for level in row_levels if level == "critical")
    degraded_count = sum(1 for level in row_levels if _LEVELS[level] >= _LEVELS["degraded"])
    if critical_count >= settings.critical_samples:
        return "critical"
    if degraded_count >= settings.degraded_samples:
        return "degraded"
    return None


def _has_healthy_tail(
    row_levels: list[DegradationState],
    settings: ProviderDegradationSettings,
) -> bool:
    healthy_tail = row_levels[-settings.healthy_samples :]
    return len(healthy_tail) == settings.healthy_samples and all(
        level == "healthy" for level in healthy_tail
    )


def _sustained_level(evidence: list[DegradationEvidence]) -> DegradationState | None:
    sustained_levels = [item.level for item in evidence if item.reason.startswith("sustained")]
    if "critical" in sustained_levels:
        return "critical"
    if "degraded" in sustained_levels:
        return "degraded"
    return None


def _row_level(
    row: dict[str, Any],
    settings: ProviderDegradationSettings,
    now_dt: datetime,
) -> DegradationState:
    level: DegradationState = "healthy"
    for item in _row_evidence(row, settings, now_dt):
        if _LEVELS[item.level] > _LEVELS[level]:
            level = item.level
    return level


def _row_evidence(
    row: dict[str, Any],
    settings: ProviderDegradationSettings,
    now_dt: datetime,
) -> list[DegradationEvidence]:
    evidence: list[DegradationEvidence] = []
    if row.get("schema") == PROVIDER_METRICS_SCHEMA:
        evidence.extend(_container_evidence(row, settings))
    if row.get("schema") == PROVIDER_INDEX_STATE_SCHEMA:
        evidence.extend(_index_evidence(row, settings, now_dt))
    evidence.extend(_probe_evidence(row, settings))
    return evidence


def _container_evidence(
    row: dict[str, Any], settings: ProviderDegradationSettings
) -> list[DegradationEvidence]:
    evidence: list[DegradationEvidence] = []
    containers = row.get("containers")
    if not isinstance(containers, list):
        return evidence
    for raw in containers:
        if not isinstance(raw, dict):
            continue
        affected = _container_affected(raw)
        evidence.extend(_container_memory_evidence(raw, affected, settings))
        restart = raw.get("restarts")
        if isinstance(restart, int) and restart > 0:
            evidence.append(
                DegradationEvidence(
                    level="degraded",
                    reason="container restart-loop signal",
                    affected=affected,
                )
            )
    return evidence


def _container_memory_evidence(
    raw: dict[str, Any],
    affected: str,
    settings: ProviderDegradationSettings,
) -> list[DegradationEvidence]:
    mem_bytes = raw.get("mem_bytes")
    mem_limit = raw.get("mem_limit_bytes")
    if not isinstance(mem_bytes, int) or not isinstance(mem_limit, int) or mem_limit <= 0:
        return []
    ratio = mem_bytes / mem_limit
    if ratio >= settings.memory_critical_ratio:
        return [
            DegradationEvidence(
                level="critical",
                reason=f"memory pressure {ratio:.2%} >= critical threshold",
                affected=affected,
            )
        ]
    if ratio >= settings.memory_degraded_ratio:
        return [
            DegradationEvidence(
                level="degraded",
                reason=f"memory pressure {ratio:.2%} >= degraded threshold",
                affected=affected,
            )
        ]
    return []


def _index_evidence(
    row: dict[str, Any],
    settings: ProviderDegradationSettings,
    now_dt: datetime,
) -> list[DegradationEvidence]:
    stale = row.get("staleIndex")
    if not isinstance(stale, dict):
        return []
    affected = _index_affected(row)
    behind = stale.get("behindFiles")
    evidence: list[DegradationEvidence] = []
    if isinstance(behind, int):
        if behind >= settings.watcher_lag_critical_commits:
            evidence.append(
                DegradationEvidence(
                    level="critical",
                    reason=f"watcher lag {behind} files >= critical threshold",
                    affected=affected,
                )
            )
        elif behind >= settings.watcher_lag_degraded_commits:
            evidence.append(
                DegradationEvidence(
                    level="degraded",
                    reason=f"watcher lag {behind} files >= degraded threshold",
                    affected=affected,
                )
            )
    age_minutes = _row_age_minutes(row, now_dt)
    if stale.get("served") is True and age_minutes is not None:
        if age_minutes >= settings.watcher_lag_critical_minutes:
            evidence.append(
                DegradationEvidence(
                    level="critical",
                    reason=f"sustained watcher lag {age_minutes:.1f}m >= critical threshold",
                    affected=affected,
                )
            )
        elif age_minutes >= settings.watcher_lag_degraded_minutes:
            evidence.append(
                DegradationEvidence(
                    level="degraded",
                    reason=f"sustained watcher lag {age_minutes:.1f}m >= degraded threshold",
                    affected=affected,
                )
            )
    return evidence


def _probe_evidence(
    row: dict[str, Any], settings: ProviderDegradationSettings
) -> list[DegradationEvidence]:
    latency_ms = _latency_ms(row)
    if latency_ms is None:
        return []
    affected = _index_affected(row)
    if latency_ms >= settings.probe_critical_ms:
        return [
            DegradationEvidence(
                level="critical",
                reason=f"probe latency {latency_ms:.0f}ms >= critical threshold",
                affected=affected,
            )
        ]
    if latency_ms >= settings.probe_degraded_ms:
        return [
            DegradationEvidence(
                level="degraded",
                reason=f"probe latency {latency_ms:.0f}ms >= degraded threshold",
                affected=affected,
            )
        ]
    return []


def _setup_failure_streak(
    rows: list[dict[str, Any]], settings: ProviderDegradationSettings
) -> list[DegradationEvidence]:
    streak = 0
    affected = "provider-setup"
    for row in reversed(rows):
        if not _is_setup_row(row):
            continue
        if _setup_failed(row):
            streak += 1
            affected = str(row.get("provider") or row.get("action") or affected)
            continue
        break
    if streak >= settings.setup_failure_critical_streak:
        return [
            DegradationEvidence(
                level="critical",
                reason=f"sustained setup failure streak {streak}",
                affected=affected,
            )
        ]
    if streak >= settings.setup_failure_degraded_streak:
        return [
            DegradationEvidence(
                level="degraded",
                reason=f"sustained setup failure streak {streak}",
                affected=affected,
            )
        ]
    return []


@dataclass(frozen=True)
class _DegradationTransition:
    """One provider degradation state change: its id, from, to and when.

    The four values are the identity of the event; everything else in the
    payload is the evidence that justifies it.
    """

    event_id: str
    previous: DegradationState
    state: DegradationState
    at: str


def _build_event(
    transition: _DegradationTransition,
    evidence: list[DegradationEvidence],
    *,
    rows: list[dict[str, Any]],
    metric_store: ProviderMetricsStore,
) -> dict[str, Any]:
    return {
        "schema": DEGRADATION_EVENT_SCHEMA,
        "id": transition.event_id,
        "at": transition.at,
        "from": transition.previous,
        "to": transition.state,
        "affectedStacks": sorted({item.affected for item in evidence}),
        "evidence": [item.to_payload() for item in evidence],
        "metrics": {
            "current": metric_store.read_current(),
            "recentIndexState": metric_store.read_recent_index_states(limit=10),
            "tail": rows[-5:],
        },
    }


def _post_degradation_alerts(config: McpRuntimeConfig, event: dict[str, Any]) -> None:
    store = OperatorInboxStore(observer_root(config))
    catalog = TerminalCatalog(terminal_catalog_path(config.coordination_root))
    host = TerminalHost()
    paster = TerminalPaster()
    now = str(event["at"])
    for role, instruction in _ALERT_TARGETS:
        recipients = _role_recipients(config.coordination_root, role)
        for agent_id in recipients:
            entry = create_operator_inbox_entry(
                InboxMessage(
                    ask=f"Provider degradation state changed to {event['to']}",
                    response=_alert_response(event, instruction),
                    message_kind="degradation-alert",
                ),
                entry_id=new_ulid(),
                now=now,
                routing=InboxRouting(
                    address=InboxAddress(lifecycle_id=None, agent_id=agent_id, recipient_role=role)
                ),
                poster=InboxPoster(
                    created_by="provider-degradation-detector",
                    created_via="cli",
                    sender_role="system",
                ),
            )
            store.append(entry)
            store.compact(now=datetime.now(UTC))
            deliver_inbox_entry(
                InboxDeliveryLog(store=store, entry=entry),
                sessions=HostedSessionRuntime(catalog=catalog, host=host),
                paster=paster,
            )


def _role_recipients(coordination_root: Path, role: AgentRole) -> list[str | None]:
    catalog = TerminalCatalog(terminal_catalog_path(coordination_root))
    sessions: list[str | None] = [
        entry.id
        for entry in catalog.list()
        if entry.status == "running" and entry.kind == "harness" and entry.binding_role == role
    ]
    if sessions:
        return sessions
    return [None]


def _alert_response(event: dict[str, Any], instruction: str) -> str:
    affected = ", ".join(event.get("affectedStacks") or ["unknown"])
    reasons = "; ".join(
        str(item.get("reason"))
        for item in event.get("evidence", [])
        if isinstance(item, dict) and item.get("reason")
    )
    failsafe = event.get("criticalFailsafe")
    parts = [
        f"event: {event['id']}",
        f"transition: {event['from']} -> {event['to']}",
        f"affected: {affected}",
        f"reasons: {reasons or 'none recorded'}",
        "",
        instruction,
    ]
    if isinstance(failsafe, dict):
        parts.extend(["", f"critical failsafe: {json.dumps(failsafe, sort_keys=True)}"])
    return "\n".join(parts)


def _stop_provider_stacks(config: McpRuntimeConfig) -> dict[str, Any]:
    return provider_watchers_tool(config, action="stop", dry_run=False)


def _run_critical_failsafe(stopper: ProviderStopper, config: McpRuntimeConfig) -> dict[str, Any]:
    # Stop failures must be serialized into the degradation event; otherwise a failed teardown
    # erases the alert path that tells the owner the failsafe failed.
    try:
        return stopper(config)
    except Exception as exc:
        return {"ok": False, "errorType": type(exc).__name__, "error": str(exc)}


def _coerce_state(value: object) -> DegradationState:
    if value in _LEVELS:
        return cast(DegradationState, value)
    return "healthy"


def _container_affected(raw: dict[str, Any]) -> str:
    provider = str(raw.get("provider") or "unknown-provider")
    instance = str(raw.get("instance") or raw.get("name") or "unknown-instance")
    return f"{provider}:{instance}"


def _index_affected(row: dict[str, Any]) -> str:
    provider = str(row.get("provider") or "provider")
    repo = str(row.get("repoId") or row.get("instance") or "unknown")
    return f"{provider}:{repo}"


def _latency_ms(row: dict[str, Any]) -> float | None:
    for key in ("probeLatencyMs", "probeLatencyMillis", "latencyMs"):
        value = row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    seconds = row.get("probeLatencySeconds")
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
        return float(seconds) * 1000
    return None


def _is_setup_row(row: dict[str, Any]) -> bool:
    return (
        row.get("kind") == "provider-setup-summary"
        or row.get("schema") == "ar-provider-setup-summary/v1"
        or "setupState" in row
    )


def _setup_failed(row: dict[str, Any]) -> bool:
    if row.get("ok") is False:
        return True
    state = str(row.get("state") or row.get("setupState") or "")
    return state.startswith("failed") or state == "ready-with-failed-phases"


def _row_age_minutes(row: dict[str, Any], now_dt: datetime) -> float | None:
    sampled_at = row.get("sampledAt")
    if not isinstance(sampled_at, str):
        return None
    then = _parse_time(sampled_at)
    if then is None:
        return None
    return max(0.0, (now_dt - then).total_seconds() / 60)


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
