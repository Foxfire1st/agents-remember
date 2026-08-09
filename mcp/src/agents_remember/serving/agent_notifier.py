"""P-15 tiers 1+2: the deterministic agent-notifier sweep (260707-HFX2-L2).

"The model is never the polling layer." Every intervention the pilot run needed was detectable by
a MECHANICAL predicate (P-15: pane-state, unacked-row redelivery, seat-liveness, turn-truth state
signals). This module sweeps the authoritative stores on its own cadence, evaluates those
predicates, and relays facts -- redeliver, owner signal, state-signal -- with zero tokens spent
and zero model calls anywhere in the loop. It never interprets: no suspect classification, no
escalation rungs, no expectation judgment.

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

from agents_remember.controlplane import operator_inbox_transitions as inbox_transitions
from agents_remember.controlplane.inbox_backoff import require_redelivery_floor_seconds
from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.serving._agent_notifier_actions import (
    _FINDING_ACTIONS,
    _drain_boundary,
    _emit_compound_idle,
    _emit_non_reaction,
    _emit_state_signal,
    _expire_pending,
    _FindingAction,
    _log_event,
    _rebind_due,
    _rebind_expired,
    _redeliver,
    _signal_dead_upstream,
    _signal_emit,
    act_on_finding,
)
from agents_remember.serving._agent_notifier_evaluation import (
    PERSISTENT_FAILURE_ATTEMPTS,
    _age_seconds,
    _inactivity_signal_chain_progressed,
    _stale_turn_state_due,
    evaluate_dead_upstream_findings,
    evaluate_inbox_findings,
    evaluate_pane_findings,
    evaluate_pending_expiry_findings,
    evaluate_predicates,
    evaluate_rebind_findings,
    evaluate_seat_liveness_findings,
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
from agents_remember.serving.owner_signals import (
    OwnerSignal,
    _find_coalescible,
    _post_owner_signal,
)
from agents_remember.serving.state_signals import (
    COMPOUND_IDLE_SWEEP_LATENCY_SECONDS,
    NON_REACTION_WINDOW_SECONDS,
    compound_idle_response,
    compound_idle_signature,
    evaluate_boundary_drain_findings,
    evaluate_compound_idle_findings,
    evaluate_non_reaction_findings,
    evaluate_state_signal_findings,
    non_reaction_response,
    state_signal_response,
)

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
    # N13 migration fold: pre-migration rows that satisfied the by-rule landing predicate
    # (state-signal + delivered + accepted) gain the formal ``landed`` state exactly once.
    _fold_legacy_landed(ctx, current, now=now)
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


def _fold_legacy_landed(
    ctx: AgentNotifierContext,
    current: dict[str, OperatorInboxEntry],
    *,
    now: datetime,
) -> int:
    """Formally land rows created under the retired by-rule predicate (N13)."""
    folded = 0
    for row in current.values():
        if row.state != "pending" or row.messageKind != "state-signal":
            continue
        if row.deliveryState != "delivered" or row.adapterDeliveryState != "accepted":
            continue
        landed, changed = inbox_transitions.mark_landed(
            ctx.inbox_store,
            row.id,
            now=now.isoformat(),
            reason="legacy-by-rule-landed",
        )
        current[row.id] = landed
        if changed:
            folded += 1
    if folded:
        _log_event(
            ctx,
            "orchestration.agent-notifier.inbox-landed-fold",
            {"count": folded},
        )
    return folded


__all__ = [
    "COMPOUND_IDLE_SWEEP_LATENCY_SECONDS",
    "NON_REACTION_WINDOW_SECONDS",
    "PERSISTENT_FAILURE_ATTEMPTS",
    "_FINDING_ACTIONS",
    "AgentNotifierActionResult",
    "AgentNotifierFinding",
    "OwnerSignal",
    "_FindingAction",
    "_age_seconds",
    "_drain_boundary",
    "_emit_compound_idle",
    "_emit_non_reaction",
    "_emit_state_signal",
    "_expire_pending",
    "_find_coalescible",
    "_inactivity_signal_chain_progressed",
    "_log_event",
    "_post_owner_signal",
    "_rebind_due",
    "_rebind_expired",
    "_redeliver",
    "_signal_dead_upstream",
    "_signal_emit",
    "_stale_turn_state_due",
    "act_on_finding",
    "compound_idle_response",
    "compound_idle_signature",
    "evaluate_boundary_drain_findings",
    "evaluate_compound_idle_findings",
    "evaluate_dead_upstream_findings",
    "evaluate_inbox_findings",
    "evaluate_non_reaction_findings",
    "evaluate_pane_findings",
    "evaluate_pending_expiry_findings",
    "evaluate_predicates",
    "evaluate_rebind_findings",
    "evaluate_seat_liveness_findings",
    "evaluate_state_signal_findings",
    "non_reaction_response",
    "run_agent_notifier_sweep",
    "state_signal_response",
]
