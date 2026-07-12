"""Policy for one readiness-gated durable dispatch brief."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agents_remember.controlplane.expectation_rows import ExpectationRow, ExpectationRowStore
from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.serving.harness_logs import HarnessSessionLog
from agents_remember.serving.hosted_readiness import (
    HostedReadinessHost,
    HostedReadinessResult,
    hosted_session_identity,
    hosted_session_readiness,
)
from agents_remember.serving.injector import DeliveryRow, verify_or_reissue_command
from agents_remember.serving.terminal import ensure_terminal_input_ready
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_paste import DispatchPastePolicy, TerminalPaster

DISPATCH_BRIEF_KIND = "dispatch-brief"
InputReadyCheck = Callable[[str], bool]
ReadinessCheck = Callable[[TerminalCatalog, HostedReadinessHost, str], HostedReadinessResult]


@dataclass(frozen=True)
class LaunchCommandGate:
    allows_brief: bool
    detail: str | None = None
    capture: str = ""


def _readiness_check(
    catalog: TerminalCatalog,
    host: HostedReadinessHost,
    session_id: str,
) -> HostedReadinessResult:
    return hosted_session_readiness(catalog, host, session_id=session_id)


@dataclass(frozen=True)
class DispatchBriefGate:
    """Immediate copy-mode cancellation plus exact-session readiness recheck."""

    input_ready: InputReadyCheck = ensure_terminal_input_ready
    readiness: ReadinessCheck = _readiness_check

    def check(
        self,
        catalog: TerminalCatalog,
        host: HostedReadinessHost,
        target: TerminalCatalogEntry,
        *,
        recovery: bool = False,
    ) -> str | None:
        observed = self.readiness(catalog, host, target.id)
        failure = _exact_running_failure(
            observed,
            target,
            phase="before copy-mode cancellation",
        )
        if failure is not None:
            return failure
        if not self.input_ready(target.tmux_name):
            return "pane copy mode could not be cancelled and verified; no input sent"
        final = self.readiness(catalog, host, target.id)
        failure = _exact_running_failure(
            final,
            target,
            phase="after copy-mode cancellation",
        )
        if failure is not None:
            return failure
        if _final_readiness_allows_input(final, recovery=recovery):
            return None
        return f"dispatch target is {final.status}: {final.detail or 'not input-ready'}"


def _exact_running_failure(
    observed: HostedReadinessResult,
    target: TerminalCatalogEntry,
    *,
    phase: str,
) -> str | None:
    entry = observed.entry
    if entry is None or hosted_session_identity(entry) != hosted_session_identity(target):
        return f"dispatch target identity changed {phase}; no input sent"
    if entry.status != "running":
        return f"dispatch target is {observed.status}: {observed.detail or 'not running'}"
    return None


def _final_readiness_allows_input(
    observed: HostedReadinessResult,
    *,
    recovery: bool,
) -> bool:
    if observed.status == "ready":
        return True
    return bool(
        recovery
        and observed.status == "not-ready"
        and observed.detail == "harness composer is not ready"
    )


def dispatch_paste_policy(
    entry: OperatorInboxEntry, target: TerminalCatalogEntry
) -> DispatchPastePolicy:
    """Use a fresh one-paste attempt once; every durable retry inspects the existing draft."""

    attempt = "initial" if entry.attemptCount == 0 else "recovery"
    return DispatchPastePolicy(
        attempt=attempt,
        visible_marker=f"entry: {entry.id}",
        harness=target.harness,
    )


def with_prompt_keywords(target: TerminalCatalogEntry, text: str) -> str:
    """Prepend settings-owned prompt keywords as exactly one line on the durable brief."""

    if not target.prompt_keywords:
        return text
    return f"{' '.join(target.prompt_keywords)}\n\n{text}"


def verify_launch_commands(
    target: TerminalCatalogEntry,
    *,
    paster: TerminalPaster,
    session_log: HarnessSessionLog,
) -> LaunchCommandGate:
    """Retro-prove spawn-phase commands after the dispatch row binds the harness log."""

    if not target.session_commands:
        return LaunchCommandGate(True)
    if not session_log.command_evidence_supported:
        return LaunchCommandGate(
            True,
            "harness has no command-entry/output evidence adapter; launch transport remains unproven",
        )
    if session_log.bound_path is None:
        return LaunchCommandGate(False, "dispatch log did not bind launch command evidence")
    failures: list[str] = []
    capture = ""
    for index, command in enumerate(target.session_commands, start=1):
        result = verify_or_reissue_command(
            DeliveryRow(
                kind="session-command",
                entry_id=target.id,
                text=command,
                submit=True,
                envelope=False,
            ),
            tmux_name=target.tmux_name,
            paster=paster,
            harness=target.harness,
            session_log=session_log,
        )
        if result.outcome != "acked":
            failures.append(f"command {index}: {result.reason or result.outcome}")
            capture = result.capture or capture
    if failures:
        return LaunchCommandGate(False, "; ".join(failures), capture)
    return LaunchCommandGate(True)


def delivery_is_briefed(entry: OperatorInboxEntry) -> bool:
    return (
        entry.messageKind == DISPATCH_BRIEF_KIND
        and entry.deliveryState == "delivered"
        and entry.deliveryDetail == "harness-log-confirmed"
    )


def dispatch_stays_on_exact_session(entry: OperatorInboxEntry) -> bool:
    """Pending dispatch rows never enter a ladder that can readdress their exact agent id."""

    return entry.messageKind == DISPATCH_BRIEF_KIND and entry.state == "pending"


def fulfill_briefed_expectation(
    store: ExpectationRowStore,
    entry: OperatorInboxEntry,
    *,
    current: dict[str, ExpectationRow] | None = None,
) -> None:
    """Fulfill the entry-id-addressed brief clock only from both required proof fields."""

    if not delivery_is_briefed(entry):
        return
    row = store.find_by_source(entry.id, kind="briefed-by", current=current)
    if row is not None:
        store.mark_met(row.id, now=entry.deliveredAt or entry.ts, current=current)
