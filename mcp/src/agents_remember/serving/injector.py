"""The ONE delivery path (260707-HFX2-L3, R1 + R3): every payload class ends here.

Developer ruling (2026-07-07T22:45, verbatim): "As injector we will then use the paste into chat
method. It's the one that is harness independent as long as you get its method right. This is the
one thing we got all the control over we need." The tmux paste seam
(``serving/terminal_paste.TerminalPaster``) is already the transport this project controls
end-to-end; this module is where every payload class -- spawn briefs, dispatch orders, nudges,
redeliveries, expectation-timeout wake-ups -- funnels through it, so there is exactly one place
that decides what "delivered" means.

R1 CONTRACT: :func:`deliver` returns one of four outcomes, never a bare boolean --

* ``acked``          -- the paste landed, was submitted, and the pane shows the turn started.
* ``landed-unacked`` -- the paste landed but was not (yet) acted on: a draft-only delivery
  (``submit=False``), or a submitted paste whose turn-start could not be confirmed.
* ``blocked(reason)``-- a modal dialog trap (codex quota/rate-limit (#20), a permission/confirmation
  prompt) sits in the pane; a structured NEEDS-ATTENTION signal, never silent non-delivery.
* ``failed(reason)`` -- the paste itself was never capture-verified as landed.

The caller (the L2 supervisor, ``serving/supervisor.py``) OWNS retries against this outcome; this
module never blind-retries -- ``TerminalPaster`` already retries internally, but ONLY after
re-capturing proof the previous attempt did not land (the F-V lesson, ``terminal_paste.py:284-308``,
left untouched by this leaf). A second call to :func:`deliver` for the same row is the caller's
decision, made with the outcome as evidence, exactly like every other supervisor-owned retry.

R4 NON-GOALS (developer-ruled): harness hooks, Agent SDK sessions, and the codex app-server protocol
are NOT delivery channels here or anywhere downstream of this module -- the paste-into-chat method
is the one mechanism, full stop. A future harness adds one :class:`~agents_remember.serving.
harness_adapters.HarnessAdapter` registration, never a second ``deliver``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agents_remember.serving.harness_adapters import get_adapter
from agents_remember.serving.terminal_paste import PasteResult, TerminalPaster

DeliveryOutcome = Literal["acked", "landed-unacked", "blocked", "failed"]


@dataclass(frozen=True)
class DeliveryRow:
    """The standardized payload envelope (R3): every delivery kind carries the same compact header
    fields, whether or not :attr:`envelope` renders them into the pasted text.

    ``kind`` is the payload class (``"brief"``, an ``InboxMessageKind`` like ``"nudge"``/
    ``"escalation"``/``"message"``, ``"session-command"``, ...); ``entry_id`` is the durable row or
    session id a seat's reply is tracked against; ``ack_instruction`` is the human-readable
    instruction for how a seat acks (``None`` when the payload carries no ack expectation, e.g. a
    session command). ``envelope=False`` skips rendering the header into the pasted bytes -- the
    spawn-brief path uses this: a fresh harness's very first paste is the brief/session-command text
    itself (unchanged wire format, since other machinery -- ``briefed-by`` expectation rows, the
    dashboard's spawn payload -- already keys off the session, not a parsed header), while the
    inbox path renders its own header via ``_push_text`` already, so it also sets ``envelope=False``
    and lets its pre-built text carry the header. A payload kind with no header of its own (a future
    dispatch-order/timeout-wake-up path) should leave ``envelope`` at its default ``True``.
    """

    kind: str
    entry_id: str
    text: str
    ack_instruction: str | None = None
    submit: bool = True
    envelope: bool = True


@dataclass(frozen=True)
class DeliveryResult:
    """One :func:`deliver` outcome (R1): the contract's four-way ``DeliveryOutcome`` plus evidence.

    ``capture`` is the final pane snapshot (the loud-failure evidence
    ``terminal_paste.PasteResult.capture`` already carries) -- present regardless of outcome, so a
    ``blocked``/``failed`` result is diagnosable from the result itself.
    """

    outcome: DeliveryOutcome
    reason: str | None = None
    capture: str = ""
    submitted: bool = False


def envelope_text(row: DeliveryRow) -> str:
    """Render ``row`` into pasted text: the compact header (R3) prefixed to the body, or the body
    verbatim when ``row.envelope`` is ``False`` (see :class:`DeliveryRow`'s docstring)."""
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
    harness: str | None = None,
) -> DeliveryResult:
    """The ONE delivery path (R1 + R3): paste ``row`` into ``tmux_name`` and classify the outcome.

    Every payload class -- spawn briefs and session commands (``mcp/tools/terminal.py``), dispatch/
    nudge/redelivery/signal rows (``serving/inbox_delivery.py``, called from
    ``serving/supervisor.py``) -- calls this function; none of them talks to ``TerminalPaster``
    directly any more (R3: the raw-spawn seam's separate delivery loop is retired into this path).

    Classification order: the paste is attempted first (``TerminalPaster.paste`` already does its
    own capture-verified, non-blind-retrying dance across the harness boot window); THEN the final
    pane capture is read for a modal-dialog trap (``blocked`` overrides every other reading -- a
    paste that nominally "landed" into a quota/permission modal is not a delivery a caller should
    treat as acked); only then do delivery/submit flags resolve to ``acked`` / ``landed-unacked`` /
    ``failed``.
    """
    adapter = get_adapter(harness)
    outcome: PasteResult = paster.paste(tmux_name, envelope_text(row), submit=row.submit)
    blocked = adapter.blocked_reason(outcome.capture)
    if blocked is not None:
        return DeliveryResult(
            "blocked", reason=blocked, capture=outcome.capture, submitted=outcome.submitted
        )
    if not outcome.delivered:
        return DeliveryResult(
            "failed", reason="paste was not capture-verified as landed", capture=outcome.capture
        )
    if not row.submit:
        return DeliveryResult(
            "landed-unacked",
            reason="draft-only delivery (submit=False)",
            capture=outcome.capture,
        )
    started = adapter.turn_started(outcome.capture, advanced=outcome.submitted)
    if not started:
        return DeliveryResult(
            "landed-unacked",
            reason="submitted but the turn did not visibly start",
            capture=outcome.capture,
            submitted=False,
        )
    return DeliveryResult("acked", capture=outcome.capture, submitted=True)
