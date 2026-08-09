"""Canonical conversation status authority.

One revisioned mapping from adapter evidence to the canonical
:class:`ConversationStatus` vocabulary, consumed identically by the Chats
serving routes (through the active projector) and by orchestration (through
``hosted_control_projection.snapshot_turn_state``). Neither consumer maps
:class:`AdapterSnapshot` on its own, and neither derives state from rendered
events, PTY output, or the last timeline mutation.

The classification is evidence-first: an ``AdapterSnapshot`` plus its harness
produces a process state and, when the evidence supports one, a canonical turn
evidence value from the fixed ``CANONICAL_TURN_STATE_BY_EVIDENCE`` vocabulary.
Unknown evidence never becomes ``ready`` (enforced by the contract model) and a
session without usable turn evidence keeps its last known turn state while its
process/freshness evidence continues to advance honestly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from agents_remember.serving.conversation.models import (
    CANONICAL_TURN_STATE_BY_EVIDENCE,
    ActiveConversationRef,
    CanonicalStatusEvidence,
    ConversationProcessState,
    ConversationProcessStatus,
    ConversationStatus,
    ConversationStatusEvidence,
    ConversationTurnOutcome,
    ConversationTurnState,
    ConversationTurnStatus,
    ConversationTurnWaiting,
    HarnessId,
    ProvenanceStrength,
    StatusFreshness,
)
from agents_remember.serving.harness_control_models import AdapterSnapshot
from agents_remember.serving.terminal_catalog import SeatTurnState

Clock = Callable[[], str]

STALE_AFTER_MS = 15_000
"""One liveness-sweep cadence plus slack; the projector polls far faster."""

EVIDENCE_EXPECTED_TURN_STATES: frozenset[ConversationTurnState] = frozenset(
    {"working", "settling", "retrying", "compacting"}
)
"""The turn states where fresh evidence is EXPECTED to keep flowing.

Staleness claims that expected evidence stopped arriving. A ready-idle, waiting, or
needs-input chat expects no turn events — its quiet is healthy, so it can never be
``stale``; only an active turn's silence is the alarm this threshold was tuned for.
"""

OBSERVATION_BOUND = "poll"
"""The daemon observes the bridge through bounded IPC polls, never PTY output."""


@dataclass(frozen=True)
class TurnEvidence:
    """One canonical turn classification plus its provenance."""

    evidence: CanonicalStatusEvidence
    strength: ProvenanceStrength
    turn_id: str | None = None
    waiting_reason: str | None = None
    interaction_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ProcessEvidence:
    """One canonical process classification."""

    state: ConversationProcessState
    terminal_outcome: Literal["clean-exit", "failed-exit", "signal", "unknown"] | None = None
    detail: str | None = None


@dataclass(frozen=True)
class StatusClassification:
    """The complete canonical reading of one adapter snapshot."""

    process: ProcessEvidence
    turn: TurnEvidence | None


@dataclass(frozen=True)
class TurnTerminalEvidence:
    """A native turn settlement observed by a projector on the evidence stream."""

    outcome: Literal["completed", "interrupted", "failed", "unknown"]
    turn_id: str | None = None
    stop_reason: str | None = None


def classify_process(snapshot: AdapterSnapshot) -> ProcessEvidence:
    """Map the adapter control state to the canonical process vocabulary."""

    if snapshot.control == "ready":
        return ProcessEvidence(state="connected")
    if snapshot.control == "starting":
        return ProcessEvidence(state="starting")
    if snapshot.control == "disconnected":
        return ProcessEvidence(state="disconnected", terminal_outcome="unknown")
    return ProcessEvidence(state="failed", terminal_outcome="unknown")


def classify_turn(
    snapshot: AdapterSnapshot,
    harness_id: HarnessId | None,
) -> TurnEvidence | None:
    """Map adapter activity/raw evidence to one canonical turn evidence value.

    Returns ``None`` when the snapshot carries no usable turn evidence (booting
    or lost authority); callers retain their last known turn state in that case.
    ``harness_id`` selects the vendor-specific raw keys for retry/compaction;
    a ``None`` harness classifies settling generically, which is sufficient for
    the orchestration seat projection.
    """

    if snapshot.pending_interaction is not None:
        return TurnEvidence(
            evidence="pending-interaction",
            strength="exact",
            interaction_id=snapshot.pending_interaction.interaction_id,
            reason="native interaction is pending",
        )
    if snapshot.activity == "blocked":
        return TurnEvidence(
            evidence="declared-external-wait",
            strength="native-only",
            waiting_reason="session is blocked without an answerable interaction",
            reason="adapter reports blocked activity",
        )
    if snapshot.activity == "running":
        return TurnEvidence(
            evidence="active-native-turn",
            strength="native-only",
            turn_id=_active_turn_id(snapshot, harness_id),
        )
    if snapshot.activity == "settling":
        return _classify_settling(snapshot, harness_id)
    if snapshot.activity == "idle" and snapshot.control == "ready":
        return TurnEvidence(evidence="settled-dispatchable", strength="native-only")
    return None


def classify_snapshot(
    snapshot: AdapterSnapshot,
    harness_id: HarnessId | None,
) -> StatusClassification:
    """The one canonical read both Chats and orchestration consume."""

    return StatusClassification(
        process=classify_process(snapshot),
        turn=classify_turn(snapshot, harness_id),
    )


def seat_turn_state_for(
    process: ProcessEvidence,
    turn_state: ConversationTurnState | None,
    *,
    previous: SeatTurnState | None = None,
) -> SeatTurnState | None:
    """Project canonical status onto the orchestration seat vocabulary.

    This is the single projection rule; orchestration never re-derives seat
    state from adapter fields. Live turn states are ``working`` and wait states
    are ``awaiting-input`` regardless of process transitions (activity led the
    legacy mapping), and anything degraded with no live turn evidence is
    ``stale``.

    A ``None`` return makes NO seat claim — the sweep keeps
    the row's last claim (or none) and the control lifecycle's own
    ``starting``/``ready`` renders the calm idle state. Two honest applications,
    both keyed on the row's prior claim (``previous``), never on adapter fields:

    - ``turn-ended`` claims a turn ENDED, so a settled session stamps it only
      when this seat previously claimed a live or degraded turn (working /
      awaiting-input / stale). A fresh ready-idle chat that never ran a turn
      claims nothing instead of faking an ended one.
    - a booting process (``starting``) with no turn evidence and no prior claim
      claims nothing instead of crying ``stale`` through a healthy boot.
    """

    if turn_state in {"working", "settling", "retrying", "compacting"}:
        return "working"
    if turn_state in {"waiting", "needs-input"}:
        return "awaiting-input"
    if process.state == "connected" and turn_state in {"interrupted", "failed"}:
        return "turn-ended"
    if process.state == "connected" and turn_state == "ready":
        return "turn-ended" if previous in {"working", "awaiting-input", "stale"} else None
    if turn_state is None and process.state == "starting" and previous is None:
        return None
    return "stale"


def snapshot_seat_turn_state(
    snapshot: AdapterSnapshot,
    harness_id: HarnessId | None = None,
    *,
    previous: SeatTurnState | None = None,
    terminal: TurnTerminalEvidence | None = None,
) -> SeatTurnState | None:
    """Canonical status -> seat state for the catalog projection consumer.

    ``previous`` is the catalog row's current seat claim; it feeds only the
    settle/boot hysteresis above, never a fabricated state.
    ``terminal`` is the per-vendor turn settlement observed on the evidence stream; it takes
    precedence over the snapshot's own turn classification for that observation (the same
    precedence the canonical status service applies), so an interrupted/failed settlement is
    never re-read as a clean end.
    """

    classification = classify_snapshot(snapshot, harness_id)
    if terminal is not None:
        return seat_turn_state_for(
            classification.process, _terminal_turn_state(terminal), previous=previous
        )
    turn_state = (
        CANONICAL_TURN_STATE_BY_EVIDENCE[classification.turn.evidence]
        if classification.turn is not None
        else None
    )
    return seat_turn_state_for(classification.process, turn_state, previous=previous)


def _terminal_turn_state(terminal: TurnTerminalEvidence) -> ConversationTurnState:
    """The canonical turn state one terminal observation settles into."""
    if terminal.outcome == "interrupted":
        return "interrupted"
    if terminal.outcome == "failed":
        return "failed"
    return "settling"


def _active_turn_id(
    snapshot: AdapterSnapshot,
    harness_id: HarnessId | None,
) -> str | None:
    # Codex stamps its native turn id; claude stamps the accepted operation's id, its only
    # honest turn identity (claude_stream_state._ACTIVE_TURN_ID_KEY), which is exactly the
    # identity the exact-turn interrupt contract resolves back to the active turn. Pi
    # projects no working-turn identity on this surface.
    if harness_id not in {"codex", "claude"}:
        return None
    value = snapshot.raw.get("activeTurnId")
    return value if isinstance(value, str) and value else None


def _classify_settling(
    snapshot: AdapterSnapshot,
    harness_id: HarnessId | None,
) -> TurnEvidence:
    if harness_id == "claude":
        if snapshot.raw.get("claudeStatus") == "compacting":
            return TurnEvidence(
                evidence="native-compaction",
                strength="native-only",
                reason="claude stream status is compacting",
            )
        if snapshot.raw.get("retryAttempt") is not None:
            return TurnEvidence(
                evidence="native-retry",
                strength="native-only",
                reason="claude api retry is in progress",
            )
    if harness_id == "pi":
        if snapshot.raw.get("isCompacting") is True:
            return TurnEvidence(
                evidence="native-compaction",
                strength="native-only",
                reason="pi session reports compacting",
            )
        pi_event = snapshot.raw.get("piEvent")
        if isinstance(pi_event, dict) and pi_event.get("type") == "auto_retry_start":
            return TurnEvidence(
                evidence="native-retry",
                strength="native-only",
                reason="pi auto retry is in progress",
            )
    return TurnEvidence(evidence="native-end-reconciling", strength="native-only")


def _age_ms(last_evidence_at: str | None, now: str) -> int | None:
    if last_evidence_at is None:
        return None
    try:
        then = datetime.fromisoformat(last_evidence_at)
        current = datetime.fromisoformat(now)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return max(0, int((current - then).total_seconds() * 1000))


@dataclass(frozen=True)
class TurnTransition:
    """One proposed turn-state change, and the evidence strength that justifies it.

    The state, its turn, what it is waiting on and how it ended are one observation; the strength
    is what decides whether this observation may overwrite the last one. Deciding them separately
    is how a weak observation overwrites a strong one.
    """

    state: ConversationTurnState
    strength: ProvenanceStrength
    turn_id: str | None = None
    reason: str | None = None
    waiting: ConversationTurnWaiting | None = None
    terminal_outcome: ConversationTurnOutcome | None = None


class ConversationStatusService:
    """The revisioned canonical status authority for one exact active identity.

    Both the Chats page/SSE path (via the active projector) and ad-hoc status
    reads consume envelopes from this service. ``revision`` advances only on
    semantic change (turn/process/evidence/stale transitions); freshness
    timestamps are derived metadata recomputed per observation under the same
    revision, never a semantic mutation trigger.
    """

    def __init__(self, identity: ActiveConversationRef, *, clock: Clock) -> None:
        self._identity = identity
        self._clock = clock
        self._revision = 1
        self._turn = ConversationTurnStatus(
            state="waiting",
            turn_id=None,
            state_since=None,
            waiting=ConversationTurnWaiting(reason="native session state unavailable"),
        )
        self._turn_strength: ProvenanceStrength = "unknown"
        self._turn_reason = "no native turn evidence observed yet"
        self._process = ProcessEvidence(state="starting")
        self._last_evidence_at: str | None = None
        self._last_event_sequence: int | None = None
        self._stale = False

    @property
    def revision(self) -> int:
        return self._revision

    def observe(
        self,
        snapshot: AdapterSnapshot,
        harness_id: HarnessId,
        *,
        terminal: TurnTerminalEvidence | None = None,
    ) -> ConversationStatus:
        """Fold one adapter observation into the canonical envelope."""

        now = self._clock()
        classification = classify_snapshot(snapshot, harness_id)
        changed = self._apply(classification, terminal, now=now)
        self._track_evidence(snapshot, now)
        stale = self._compute_stale(now)
        if changed or stale != self._stale:
            self._revision += 1
        self._stale = stale
        return self._envelope(now)

    def current(self) -> ConversationStatus:
        """Return the current envelope without a new observation."""

        return self._envelope(self._clock())

    def _apply(
        self,
        classification: StatusClassification,
        terminal: TurnTerminalEvidence | None,
        *,
        now: str,
    ) -> bool:
        changed = classification.process != self._process
        self._process = classification.process
        if terminal is not None:
            state = _terminal_turn_state(terminal)
            return (
                self._set_turn(
                    TurnTransition(
                        state=state,
                        strength="exact",
                        turn_id=terminal.turn_id or self._turn.turn_id,
                        reason="native turn settlement observed on the evidence stream",
                        terminal_outcome=ConversationTurnOutcome(
                            state=terminal.outcome,
                            stop_reason=terminal.stop_reason,
                        ),
                    ),
                    now=now,
                )
                or changed
            )
        if classification.turn is not None:
            mapped = CANONICAL_TURN_STATE_BY_EVIDENCE[classification.turn.evidence]
            prior_outcome = self._turn.terminal_outcome
            outcome = (
                prior_outcome
                if mapped == "ready"
                and prior_outcome is not None
                and prior_outcome.state == "completed"
                else None
            )
            return (
                self._set_turn(
                    TurnTransition(
                        state=mapped,
                        strength=classification.turn.strength,
                        turn_id=classification.turn.turn_id,
                        reason=classification.turn.reason,
                        waiting=self._waiting_for(classification.turn),
                        terminal_outcome=outcome,
                    ),
                    now=now,
                )
                or changed
            )
        return changed

    def _set_turn(self, transition: TurnTransition, *, now: str) -> bool:
        strength, reason = transition.strength, transition.reason
        candidate = ConversationTurnStatus(
            state=transition.state,
            turn_id=transition.turn_id,
            state_since=self._turn.state_since,
            waiting=transition.waiting,
            terminal_outcome=transition.terminal_outcome,
        )
        if (
            candidate.state == self._turn.state
            and candidate.turn_id == self._turn.turn_id
            and candidate.waiting == self._turn.waiting
            and candidate.terminal_outcome == self._turn.terminal_outcome
            and strength == self._turn_strength
        ):
            return False
        self._turn = candidate.model_copy(update={"state_since": now})
        self._turn_strength = strength
        self._turn_reason = reason
        return True

    @staticmethod
    def _waiting_for(turn: TurnEvidence) -> ConversationTurnWaiting | None:
        if turn.evidence == "pending-interaction":
            assert turn.interaction_id is not None
            return ConversationTurnWaiting(
                reason="native interaction requires an answer",
                interaction_id=turn.interaction_id,
            )
        if turn.evidence == "declared-external-wait":
            assert turn.waiting_reason is not None
            return ConversationTurnWaiting(reason=turn.waiting_reason)
        return None

    def _track_evidence(self, snapshot: AdapterSnapshot, now: str) -> bool:
        if self._last_event_sequence is None:
            self._last_event_sequence = snapshot.last_event_sequence
            self._last_evidence_at = now
            return True
        if snapshot.last_event_sequence != self._last_event_sequence:
            self._last_event_sequence = snapshot.last_event_sequence
            self._last_evidence_at = now
            return True
        return False

    def _compute_stale(self, now: str) -> bool:
        # The threshold fits a working turn's cadence, so staleness
        # is claimed only while a turn is active and evidence is genuinely expected. An
        # idle ready/waiting/needs-input chat is live and quiet — its age_ms keeps
        # advancing honestly in the freshness block, but quiet is never the alarm.
        if self._turn.state not in EVIDENCE_EXPECTED_TURN_STATES:
            return False
        age = _age_ms(self._last_evidence_at, now)
        return age is not None and age > STALE_AFTER_MS

    def _envelope(self, now: str) -> ConversationStatus:
        return ConversationStatus(
            identity=self._identity,
            revision=self._revision,
            observed_at=now,
            freshness=StatusFreshness(
                state=(
                    "stale"
                    if self._stale
                    else ("fresh" if self._last_evidence_at is not None else "unknown")
                ),
                last_evidence_at=self._last_evidence_at,
                age_ms=_age_ms(self._last_evidence_at, now),
                stale_after_ms=STALE_AFTER_MS,
                observation_bound=OBSERVATION_BOUND,
            ),
            process=ConversationProcessStatus(
                state=self._process.state,
                generation=self._identity.bridge_epoch,
                terminal_outcome=self._process.terminal_outcome,
                detail=self._process.detail,
            ),
            turn=self._turn,
            evidence=ConversationStatusEvidence(
                strength=self._turn_strength,
                origin="adapter-snapshot",
                reason=self._turn_reason,
            ),
        )


__all__ = [
    "EVIDENCE_EXPECTED_TURN_STATES",
    "OBSERVATION_BOUND",
    "STALE_AFTER_MS",
    "ConversationStatusService",
    "ProcessEvidence",
    "StatusClassification",
    "TurnEvidence",
    "TurnTerminalEvidence",
    "classify_snapshot",
    "seat_turn_state_for",
    "snapshot_seat_turn_state",
]
