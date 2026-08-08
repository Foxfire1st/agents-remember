"""P-15 tiers 1+2: the deterministic agent-notifier sweep (260707-HFX2-L2).

"The model is never the polling layer." Every intervention the pilot run needed was detectable by
a MECHANICAL predicate (P-15: pane-state, expectation-deadline expiry, turn-report staleness,
unacked-row redelivery, seat-liveness). This module sweeps the authoritative stores on its own
cadence, evaluates those predicates, and acts -- redeliver, auto-nudge, signal-emit, escalate --
with zero tokens spent and zero model calls anywhere in the loop.

R3 (#22 root-cause rule, non-negotiable): every predicate reads :class:`TerminalCatalog` /
:class:`OperatorInboxStore` / :class:`ExpectationRowStore` / the nudge store DIRECTLY. The
projection is a consumer of the ``orchestration.agent-notifier.*`` events this module emits, never a
source -- so this module imports nothing from ``serving/projector.py`` or ``observer/reducer.py``.

Level-triggered by design: any event lost anywhere (a dropped push, a crashed dispatch call) is
found by the NEXT sweep. This is the backstop even protocol-grade push needs (A2A/MCP; the
Inngest Oct-2025 incident is the reference case for "at-least-once push still needs a
reconciliation sweep").
"""

from __future__ import annotations

from datetime import datetime
from time import perf_counter

from agents_remember.controlplane.inbox_backoff import require_redelivery_floor_seconds
from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.serving._agent_notifier_actions import (
    _FINDING_ACTIONS,
    OwnerSignal,
    _auto_nudge,
    _escalate_inbox_entry,
    _escalate_rung,
    _find_coalescible,
    _FindingAction,
    _log_event,
    _mark_expectation_missed,
    _nudge_reason,
    _post_owner_signal,
    _redeliver,
    _resolve_ladder_terminal,
    _respawn_suspect,
    _rung_entry,
    _signal_dead_upstream,
    _signal_emit,
    act_on_finding,
)
from agents_remember.serving._agent_notifier_evaluation import (
    _INACTIVE_EXPECTATION_KINDS,
    DEFAULT_ESCALATION_RUNG_SECONDS,
    DEFAULT_ESCALATION_SLA_SECONDS,
    PERSISTENT_FAILURE_ATTEMPTS,
    EscalationSchedule,
    _age_seconds,
    _delivery_failure_still_retrying,
    _expectation_chain_progressed,
    _inactivity_signal_chain_progressed,
    _ladder_terminal_and_dead,
    _stale_turn_state_due,
    evaluate_dead_upstream_findings,
    evaluate_escalation_findings,
    evaluate_expectation_findings,
    evaluate_inbox_findings,
    evaluate_ladder_terminal_findings,
    evaluate_pane_findings,
    evaluate_predicates,
    evaluate_seat_liveness_findings,
    evaluate_turn_report_findings,
    turn_report_path_for_leaf_key,
)
from agents_remember.serving.agent_notifier_models import (
    AgentNotifierActionResult,
    AgentNotifierContext,
    AgentNotifierFinding,
    AgentNotifierSweepResult,
)
from agents_remember.serving.agent_notifier_models import SweepState as _SweepState
from agents_remember.serving.inbox_reclamation import (
    InboxReclamationPlan,
    plan_confirmed_gone_reclamation,
)

# R1 (260707-HFX2-L4): conservative built-in fallbacks when a caller (a test, or a context built
# before settings are read) supplies no per-kind/per-rung knobs. ``serving/app.py`` always wires
# the settings-driven ``EscalationSettings`` values in (mirroring how it already resolves
# ``agentNotifier``/``expectations`` knobs into this module's plain-primitive fields) -- this module
# stays decoupled from the kernel settings loader, R3's "every predicate reads its store directly"
# extended to knobs: no settings TYPE crosses into this file, only resolved numbers.


# --- R4: actions ---------------------------------------------------------------------------------


# --- the sweep itself ------------------------------------------------------------------------


def run_agent_notifier_sweep(
    ctx: AgentNotifierContext, *, now: datetime
) -> AgentNotifierSweepResult:
    """One full R1-R5 sweep: evaluate every predicate, act on every finding, tick the heartbeat.

    Every action is logged as an ``orchestration.agent-notifier.*`` (or the reused ``orchestration.
    nudge``) observer event so the dashboard river shows what code did on whose behalf (R4). The
    heartbeat ticks LAST, unconditionally -- even a sweep with zero findings proves agent-notifier
    liveness (R5).
    """
    started = perf_counter()
    # R1-R8 (260712-TRH-L5): fold once under the inbox writer lock, join one catalog snapshot,
    # terminally resolve only positively-gone agent-notifier alerts, then compact before selecting
    # anything for redelivery. The existing TTL/cap compaction remains the fallback in this same
    # transaction. Holding the lock through resolve+compact makes a concurrent consume authoritative.
    reclamation: InboxReclamationPlan | None = None
    # The catalog snapshot is fetched BEFORE the inbox lock is taken, not inside
    # the reconcile callback. The lock-held transaction itself (fold -> resolve -> compact) is
    # untouched, but no thread may hold one store's lock while acquiring another's -- the
    # liveness sweep held the catalog batch lock while reaching for the inbox lock, the mirror
    # image of this nesting, and the ABBA deadlocked the serving daemon twice on 2026-08-05.
    # The staleness this accepts is one-directional and benign: a subject that terminates after
    # this snapshot reads as non-terminated and is KEPT this sweep (never a false resolve), the
    # tmux snapshot inside the callback is still fresh and fail-closed, and the agent-notifier is
    # level-triggered, so a kept row is simply re-judged on the next sweep.
    catalog_entries = ctx.catalog.list(include_terminated=True)

    def reconcile(current: dict[str, OperatorInboxEntry]) -> dict[str, str]:
        nonlocal reclamation
        reclamation = plan_confirmed_gone_reclamation(
            current,
            catalog_entries=catalog_entries,
            snapshotter=ctx.tmux_name_snapshotter,
        )
        return dict(reclamation.resolve_reasons)

    inbox_removed, current, resolved_entries = ctx.inbox_store.reconcile_and_compact(
        now=now,
        reconcile=reconcile,
    )
    if inbox_removed or resolved_entries:
        data: dict[str, object] = {"removed": inbox_removed, "kept": len(current)}
        if reclamation is not None and reclamation.candidate_row_count:
            data.update(
                {
                    "resolvedRowCount": len(resolved_entries),
                    "uniqueSubjectCount": reclamation.unique_subject_count,
                    "keptCandidateCount": reclamation.kept_row_count,
                    "evidenceClass": reclamation.evidence_class,
                }
            )
        _log_event(
            ctx,
            "orchestration.agent-notifier.inbox-compacted",
            data,
        )
    # CS-6 D2/D3: read + reclaim the append-only signal cooldown log ONCE per sweep. compact()
    # drops rows older than the cooldown window (they can no longer suppress a signal) and returns
    # the kept snapshot, which every per-finding in_cooldown check reads in-memory -- so the store
    # is read once per sweep and bounded on disk, instead of re-parsed once per finding forever.
    signal_retain = require_redelivery_floor_seconds(
        ctx.signal_cooldown_seconds, owner="agent-notifier signal cooldown"
    )
    _signals_removed, signal_snapshot = ctx.signal_cooldown_store.compact(
        now=now, retain_seconds=signal_retain
    )
    # CS-6 D3 (F4): read + reclaim the expectation log ONCE per sweep — compact() folds by id, drops
    # met/missed rows past the retention window, and returns the kept snapshot that both the finding
    # act-phase mark and the sweep reuse. After the first compaction the file stays bounded, so this
    # one read is O(bounded), not O(daemon-lifetime).
    _expectations_removed, expectation_snapshot = ctx.expectation_store.compact(now=now)
    sweep = _SweepState(
        inbox_current=current,
        redeliver_budget=max(1, ctx.redeliver_budget),
        pending_inbox_count=sum(1 for entry in current.values() if entry.state == "pending"),
        redeliverable_entries=ctx.inbox_store.list_redeliverable(
            now=now,
            rate_limit_seconds=ctx.redeliver_rate_limit_seconds,
            current=current,
        ),
        signal_current=signal_snapshot,
        expectation_current=expectation_snapshot,
    )
    findings = evaluate_predicates(ctx, now=now, sweep=sweep)
    actions = tuple(act_on_finding(ctx, finding, now=now, sweep=sweep) for finding in findings)
    duration_seconds = perf_counter() - started
    ctx.heartbeat_store.tick(
        now=now,
        pending_inbox_count=sweep.pending_inbox_count,
        redeliverable_inbox_count=sweep.redeliverable_inbox_count,
        last_sweep_duration_seconds=duration_seconds,
    )
    return AgentNotifierSweepResult(
        findings=tuple(findings),
        actions=actions,
        swept_at=now.isoformat(),
        pending_inbox_count=sweep.pending_inbox_count,
        redeliverable_inbox_count=sweep.redeliverable_inbox_count,
        duration_seconds=duration_seconds,
    )


__all__ = [
    "DEFAULT_ESCALATION_RUNG_SECONDS",
    "DEFAULT_ESCALATION_SLA_SECONDS",
    "PERSISTENT_FAILURE_ATTEMPTS",
    "_FINDING_ACTIONS",
    "_INACTIVE_EXPECTATION_KINDS",
    "AgentNotifierActionResult",
    "AgentNotifierFinding",
    "EscalationSchedule",
    "OwnerSignal",
    "_FindingAction",
    "_age_seconds",
    "_auto_nudge",
    "_delivery_failure_still_retrying",
    "_escalate_inbox_entry",
    "_escalate_rung",
    "_expectation_chain_progressed",
    "_find_coalescible",
    "_inactivity_signal_chain_progressed",
    "_ladder_terminal_and_dead",
    "_log_event",
    "_mark_expectation_missed",
    "_nudge_reason",
    "_post_owner_signal",
    "_redeliver",
    "_resolve_ladder_terminal",
    "_respawn_suspect",
    "_rung_entry",
    "_signal_dead_upstream",
    "_signal_emit",
    "_stale_turn_state_due",
    "act_on_finding",
    "evaluate_dead_upstream_findings",
    "evaluate_escalation_findings",
    "evaluate_expectation_findings",
    "evaluate_inbox_findings",
    "evaluate_ladder_terminal_findings",
    "evaluate_pane_findings",
    "evaluate_predicates",
    "evaluate_seat_liveness_findings",
    "evaluate_turn_report_findings",
    "run_agent_notifier_sweep",
    "turn_report_path_for_leaf_key",
]
