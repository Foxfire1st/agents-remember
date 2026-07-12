"""The one delivery path: paste each input, then verify it in the harness session log.

Session commands and messages are always separate entries. Screen text never decides acceptance;
the pane is read only to prevent a duplicate re-paste and to preserve final failure evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.serving.harness_adapters import HarnessAdapter, get_adapter
from agents_remember.serving.harness_logs import HarnessSessionLog
from agents_remember.serving.terminal_paste import (
    DispatchPastePolicy,
    PasteResult,
    TerminalPaster,
)

DeliveryOutcome = Literal["acked", "landed-unacked", "blocked", "failed"]

# 2026-07-10 real-seat creation-to-user-log calibration (unique inbox ULID -> JSONL user record):
# Claude n=12, p50=5.044 s, observed max=40.260 s; Codex n=190, p50=3.111 s,
# p95=18.476 s, immediate-cohort max=28.949 s. Longer Codex rows were queued/redelivery delays,
# not log flushes. These are the shortest tenth-second windows covering each observed direct cohort.
_LOG_FLUSH_WINDOWS = {"claude": 40.3, "codex": 29.0}
_GENERIC_LOG_FLUSH_WINDOW = 40.3


@dataclass(frozen=True)
class DeliveryRow:
    """One independently accepted dispatch input."""

    kind: str
    entry_id: str
    text: str
    ack_instruction: str | None = None
    submit: bool = True
    envelope: bool = True
    dispatch_policy: DispatchPastePolicy | None = None


@dataclass(frozen=True)
class DeliveryResult:
    """One input's log-backed outcome and final failure pane evidence."""

    outcome: DeliveryOutcome
    reason: str | None = None
    capture: str = ""
    submitted: bool = False
    verification: tuple[str, ...] = ()
    bound_entry_id: str | None = None
    session_log_path: Path | None = None


def envelope_text(row: DeliveryRow) -> str:
    """Guarantee the existing unique entry id is part of every non-command packet."""
    if not row.envelope:
        return row.text
    header = f"[Agents Remember delivery:{row.kind} id={row.entry_id}]"
    if row.ack_instruction:
        header = f"{header}\nack: {row.ack_instruction}"
    return f"{header}\n\n{row.text}" if row.text else header


def deliver(
    row: DeliveryRow,
    *,
    tmux_name: str,
    paster: TerminalPaster,
    harness: str | None,
    session_log: HarnessSessionLog | None,
) -> DeliveryResult:
    """Paste one input and classify it solely from its harness-log record.

    An unbound spawn command is the one intentional deferred case: it is transported first, then
    verified retroactively after the id-bearing brief binds the session log. It cannot be reported
    acked before that check.
    """
    adapter = get_adapter(harness)
    text = envelope_text(row)
    if not row.submit:
        return _classify_transport(
            paster.paste(tmux_name, text, submit=False),
            adapter=adapter,
            session_log=session_log,
        )
    if session_log is None:
        return DeliveryResult(
            "failed",
            reason="harness session log context is unavailable",
            verification=("session-log",),
        )
    if row.kind == "session-command" and session_log.bound_path is None:
        outcome = paster.paste(tmux_name, text, submit=True, accepted=None)
        if not outcome.delivered:
            return _failed_from_capture(
                "session command transport failed before log binding",
                outcome,
                adapter=adapter,
            )
        return DeliveryResult(
            "landed-unacked",
            reason="session command awaits retroactive bound-log verification",
            verification=("command-entry", "command-stdout"),
        )

    accepted = (
        (lambda: session_log.command_evidence(row.text).succeeded)
        if row.kind == "session-command"
        else (lambda: session_log.message_present(row.entry_id))
    )
    flush_window = _LOG_FLUSH_WINDOWS.get(harness or "", _GENERIC_LOG_FLUSH_WINDOW)
    outcome = paster.paste(
        tmux_name,
        text,
        submit=True,
        accepted=accepted,
        flush_window=flush_window,
        dispatch_policy=row.dispatch_policy,
    )
    if outcome.submitted:
        return DeliveryResult(
            "acked",
            submitted=True,
            verification=(
                ("command-entry", "command-stdout")
                if row.kind == "session-command"
                else ("user-message-entry",)
            ),
            bound_entry_id=row.entry_id,
            session_log_path=session_log.bound_path,
        )
    return _failed_from_capture(
        "input absent from the harness session log after bounded recovery",
        outcome,
        adapter=adapter,
        verification=(
            ("command-entry", "command-stdout")
            if row.kind == "session-command"
            else ("user-message-entry",)
        ),
    )


def verify_or_reissue_command(
    row: DeliveryRow,
    *,
    tmux_name: str,
    paster: TerminalPaster,
    harness: str | None,
    session_log: HarnessSessionLog,
) -> DeliveryResult:
    """Retroactively accept a spawn command, reissuing only that command when missing/errored."""
    evidence = session_log.command_evidence(row.text)
    if evidence.succeeded:
        return DeliveryResult(
            "acked",
            submitted=True,
            verification=("command-entry", "command-stdout"),
            bound_entry_id=row.entry_id,
            session_log_path=session_log.bound_path,
        )
    result = deliver(
        row,
        tmux_name=tmux_name,
        paster=paster,
        harness=harness,
        session_log=session_log,
    )
    if result.outcome != "acked" and evidence.errored:
        return DeliveryResult(
            result.outcome,
            reason=f"session command was errored and its isolated re-issue did not verify: {result.reason}",
            capture=result.capture,
            submitted=False,
            verification=result.verification,
            bound_entry_id=result.bound_entry_id,
            session_log_path=result.session_log_path,
        )
    return result


def _classify_transport(
    outcome: PasteResult,
    *,
    adapter: HarnessAdapter,
    session_log: HarnessSessionLog | None,
) -> DeliveryResult:
    if not outcome.delivered:
        return _failed_from_capture("draft transport failed", outcome, adapter=adapter)
    return DeliveryResult(
        "landed-unacked",
        reason="draft-only delivery (submit=False)",
        session_log_path=session_log.bound_path if session_log is not None else None,
    )


def _failed_from_capture(
    reason: str,
    outcome: PasteResult,
    *,
    adapter: HarnessAdapter,
    verification: tuple[str, ...] = (),
) -> DeliveryResult:
    blocked = adapter.blocked_reason(outcome.capture)
    if blocked is not None:
        return DeliveryResult(
            "blocked",
            reason=blocked,
            capture=outcome.capture,
            verification=verification,
        )
    return DeliveryResult(
        "failed",
        reason=reason,
        capture=outcome.capture,
        verification=verification,
    )
