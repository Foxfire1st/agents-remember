"""Distinct terminal-observer health (``LOCR-R17@v1``): the producer stage's own freshness.

The motivating defect is invisible in the health model this module extends: the agent-notifier
loop can report fresh ticks while no lifecycle process calls the terminal liveness sweeper, and
one generic "supervisor healthy" bit preserves exactly that ambiguity. The observer and the
notifier are separate pipeline stages with different recovery actions, so the producer gets its
own durable current record and its own serve-time projection, beside the notifier's:

    observer loop --> this record --> served_state_tail --> /api/state + SSE snapshot
    notifier loop --> AgentNotifierHeartbeatPayload -----> /api/state + SSE snapshot

Three properties are load-bearing, and each is why a simpler shape was rejected:

1. **One current record, atomically replaced** -- never an append history and never a second
   store. ``attemptCount``/``consecutiveFailureCount`` are serving-lifetime counters that must
   survive a FAILED write, so they live in one in-memory transition accumulator
   (:class:`TerminalObserverHealthPublisher`) that is never served directly; the persisted file
   is the sole serve-time source.
2. **The persisted file decides what is served.** A missing, unreadable, wrong-schema, or
   prior-lifetime row omits the wire key instead of serving another process's bytes, and a failed
   later write leaves the last valid row in place -- so the served payload can age into ``stale``
   while the newer accumulator is still unpublished.
3. **Nothing here is a lock or a mutation.** Health publication is diagnostic: it advances no
   catalog cursor, stamps no signal marker, admits no closeout, owns no queue, and the read route
   never rewrites, repairs, creates, or deletes the file.

The failure vocabulary is fixed and phase-selected (:data:`TERMINAL_OBSERVER_FAILURE_TYPES`), so no
module name, class name, message, argument, or traceback text is copied into the record or the log
line. The diagnostic is bounded by construction, which is what makes it safe to serve to a browser.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents_remember.errors import HarnessControlError
from agents_remember.kernel.atomic_write import atomic_write_text

logger = logging.getLogger("agents_remember.serving.app")

TERMINAL_OBSERVER_HEALTH_SCHEMA_VERSION = "ar-terminal-observer-health/v1"
"""The exact schema marker of the one durable observer-health record."""

TERMINAL_OBSERVER_HEALTH_FILE_NAME = "terminal-observer-health.json"
"""The record's fixed name under ``observer_root/workspace``."""

TERMINAL_OBSERVER_HEALTH_STALE_SWEEP_MULTIPLIER = 6
"""Staleness is exactly ``6 * TerminalCatalogLivenessConfig.sweep_interval_seconds``."""

TERMINAL_OBSERVER_HEALTH_MAX_COUNT = 2**32 - 1
"""The unsigned 32-bit ceiling both serving-lifetime counters saturate at."""

TERMINAL_OBSERVER_HEALTH_WRITE_FAILURE_LOG = (
    "terminal observer health write failed; retrying next observation"
)
"""The one line a failed health publication emits: fixed, with no exception text or traceback."""

TerminalObserverHealthStatus = Literal["initializing", "degraded", "healthy", "stale"]
TerminalObserverHealthPhase = Literal["startup", "steady-state"]
TerminalObserverFailureCategory = Literal["startup-refresh-failed", "steady-state-refresh-failed"]
TerminalObserverFailureSummary = Literal[
    "startup terminal observation failed",
    "steady-state terminal observation failed",
]
TerminalObserverFailureType = Literal[
    "HarnessControlError",
    "TimeoutError",
    "ConnectionError",
    "OSError",
    "RuntimeError",
    "ValueError",
    "Exception",
]

TERMINAL_OBSERVER_FAILURE_TYPES: dict[type[BaseException], TerminalObserverFailureType] = {
    HarnessControlError: "HarnessControlError",
    TimeoutError: "TimeoutError",
    ConnectionError: "ConnectionError",
    OSError: "OSError",
    RuntimeError: "RuntimeError",
    ValueError: "ValueError",
    Exception: "Exception",
}
"""The exact classification order: the first matching ``isinstance`` category wins.

Order matters twice over. ``TimeoutError`` and ``ConnectionError`` are both ``OSError``
subclasses in a supported interpreter, so they must precede it, and ``RuntimeError``/``ValueError``
follow. The published NAME comes from this table rather than from the raised object, so a
``SecretTokenError(RuntimeError)`` publishes ``RuntimeError`` and a custom subclass's own class
name can never reach the wire.
"""

_PHASE_FAILURE_PUBLICATION: dict[
    TerminalObserverHealthPhase,
    tuple[TerminalObserverFailureCategory, TerminalObserverFailureSummary],
] = {
    "startup": ("startup-refresh-failed", "startup terminal observation failed"),
    "steady-state": ("steady-state-refresh-failed", "steady-state terminal observation failed"),
}
"""The summary is selected SOLELY from the observer phase -- never from the exception."""


def terminal_observer_health_path(observer_root: Path) -> Path:
    """The one durable health row's exact path."""

    return observer_root / "workspace" / TERMINAL_OBSERVER_HEALTH_FILE_NAME


def saturate_observer_count(count: int) -> int:
    """Clamp one serving-lifetime counter to the unsigned 32-bit wire ceiling."""

    return min(count, TERMINAL_OBSERVER_HEALTH_MAX_COUNT)


def classify_terminal_observer_failure(error: BaseException) -> TerminalObserverFailureType:
    """The bounded failure vocabulary for ``error``: a fixed name, never its own class or text."""

    for exception_type, name in TERMINAL_OBSERVER_FAILURE_TYPES.items():
        if isinstance(error, exception_type):
            return name
    return "Exception"


class TerminalObserverHealthRecord(BaseModel):
    """The exact ``ar-terminal-observer-health/v1`` durable row.

    Every field is REQUIRED, including the nullable ones, so validation accepts exactly the v1
    field set and rejects a row with a missing key, an extra key, a wrong marker, or a wrong
    type. That is what makes "wrong-schema" a decidable answer at read time rather than a guess,
    and it is why the schema's own ``extra="forbid"`` is part of the contract rather than a
    convenience.

    Both serving-lifetime counters are bounded at BOTH ends: the packet declares them as unsigned
    32-bit counts saturating at :data:`TERMINAL_OBSERVER_HEALTH_MAX_COUNT`, so a row above the
    ceiling is not a row this contract can serve. The writer can never produce one (its increments
    saturate), but the reader is the boundary that decides, and a bounded field that is only
    bounded in the producer is a contract the schema does not state.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["ar-terminal-observer-health/v1"]
    servingStartedAt: str
    lastAttemptAt: str | None
    lastSuccessAt: str | None
    attemptCount: int = Field(ge=0, le=TERMINAL_OBSERVER_HEALTH_MAX_COUNT)
    consecutiveFailureCount: int = Field(ge=0, le=TERMINAL_OBSERVER_HEALTH_MAX_COUNT)
    lastDurationSeconds: float | None = Field(ge=0)
    initialObservationSucceeded: bool
    activeFailureCategory: TerminalObserverFailureCategory | None
    activeFailureType: TerminalObserverFailureType | None
    activeFailureSummary: TerminalObserverFailureSummary | None

    @field_validator("servingStartedAt", "lastAttemptAt", "lastSuccessAt")
    @classmethod
    def _aware_rfc3339(cls, value: str | None) -> str | None:
        """Reject a stamp the age arithmetic could not subtract from."""

        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError("timestamp is not RFC 3339") from error
        if parsed.tzinfo is None:
            raise ValueError("timestamp must carry an offset")
        return value


class TerminalObserverHealthPayload(TerminalObserverHealthRecord):
    """The declared wire form: every record field, plus what is computed ABOUT it at serve time.

    Serialized WITHOUT ``exclude_none``: ``lastAttemptAt: null`` is how "no observer attempt has
    completed in this serving lifetime" travels, exactly as the notifier heartbeat keeps its own
    nulls -- the cockpit distinguishes "not yet attempted" from "this server reports no observer
    health at all". The four computed fields are per-response arithmetic on the persisted row,
    which is why they are on the payload and not on the record, and why neither is a projection
    field: a volatile age must never enter the memoized body or the projector's content revision.
    """

    ageSeconds: float = Field(ge=0)
    lastSuccessAgeSeconds: float | None = Field(ge=0)
    staleCutoffSeconds: float = Field(ge=0)
    status: TerminalObserverHealthStatus


def terminal_observer_health_age_seconds(
    record: TerminalObserverHealthRecord, *, now: datetime
) -> float:
    """Non-negative age of ``lastAttemptAt``, or of ``servingStartedAt`` before one."""

    reference = record.lastAttemptAt or record.servingStartedAt
    return max(0.0, (now - datetime.fromisoformat(reference)).total_seconds())


def terminal_observer_health_status(
    record: TerminalObserverHealthRecord, *, age_seconds: float, stale_cutoff_seconds: float
) -> TerminalObserverHealthStatus:
    """``stale`` outranks every other state; then no-attempt, failed, succeeded."""

    if age_seconds >= stale_cutoff_seconds:
        return "stale"
    if record.lastAttemptAt is None:
        return "initializing"
    if record.activeFailureCategory is not None:
        return "degraded"
    return "healthy"


def terminal_observer_health_payload(
    record: TerminalObserverHealthRecord, *, now: datetime, stale_cutoff_seconds: float
) -> TerminalObserverHealthPayload:
    """The serve-time projection of one valid record for the current serving lifetime."""

    age_seconds = terminal_observer_health_age_seconds(record, now=now)
    last_success_age = (
        None
        if record.lastSuccessAt is None
        else max(0.0, (now - datetime.fromisoformat(record.lastSuccessAt)).total_seconds())
    )
    return TerminalObserverHealthPayload(
        **record.model_dump(),
        ageSeconds=age_seconds,
        lastSuccessAgeSeconds=last_success_age,
        staleCutoffSeconds=stale_cutoff_seconds,
        status=terminal_observer_health_status(
            record, age_seconds=age_seconds, stale_cutoff_seconds=stale_cutoff_seconds
        ),
    )


class TerminalObserverHealthStore:
    """The one durable current health row: one atomic overwrite per completed observer call.

    Not an append log -- there is exactly one current observer state and never a history worth
    folding -- and not a fallback store: the accumulator that survives a failed write is in memory
    and is never served.
    """

    def __init__(self, observer_root: Path) -> None:
        self._path = terminal_observer_health_path(observer_root)

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> TerminalObserverHealthRecord | None:
        """The current valid record, or ``None`` for absent/unreadable/wrong-schema bytes.

        Absence is a VALUE here, not an error: the serve-time contract's first rule is to omit
        the wire key, so every unusable source resolves to the same answer and no reader can ever
        serve a half-validated row. Reading never writes, repairs, or creates the file.
        """

        try:
            raw = self._path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None
        try:
            return TerminalObserverHealthRecord.model_validate_json(raw)
        except ValueError:
            return None

    def write(self, record: TerminalObserverHealthRecord) -> None:
        """Atomically replace the destination with ``record``.

        A reader sees the previous complete row or this one, never a partial document: the bytes
        land in a private temp file first, and only a successful fsync and replace moves them onto
        the destination (``kernel.atomic_write``), which also removes its temp on any failure.
        """

        atomic_write_text(self._path, json.dumps(record.model_dump(mode="json")) + "\n")


def served_terminal_observer_health(
    *,
    store: TerminalObserverHealthStore,
    serving_started_at: str | None,
    now: datetime,
    sweep_interval_seconds: float,
) -> TerminalObserverHealthPayload | None:
    """The serve-time payload for the CURRENT serving lifetime, or ``None`` to omit the key.

    Omission is the answer for every unusable source: no lifetime has started yet, the file is
    missing, unreadable, or wrong-schema, or the row carries another serving process's
    ``servingStartedAt``. Reading never creates, repairs, deletes, or rewrites the file, so no
    validation failure can become a request failure, and a caller can never be handed another
    lifetime's bytes.

    The cutoff is derived from the configured full-observation cadence -- never from browser
    traffic, which is what keeps a closed dashboard from making the observer look fresh.
    """

    if serving_started_at is None:
        return None
    record = store.read()
    if record is None or record.servingStartedAt != serving_started_at:
        return None
    return terminal_observer_health_payload(
        record,
        now=now,
        stale_cutoff_seconds=(
            TERMINAL_OBSERVER_HEALTH_STALE_SWEEP_MULTIPLIER * sweep_interval_seconds
        ),
    )


class TerminalObserverHealthPublisher:
    """This serving lifetime's health owner: one in-memory accumulator, one atomic write.

    The accumulator is deliberately not a second durable store and is never served directly. It
    exists so that a failed file write does not erase attempt counters from the next full-record
    publication attempt; the persisted file stays the sole serve-time source, which is what lets
    the served payload age into ``stale`` while a newer accumulator waits unpublished.
    """

    def __init__(self, observer_root: Path, clock: Callable[[], datetime]) -> None:
        self._store = TerminalObserverHealthStore(observer_root)
        self._clock = clock
        self._record: TerminalObserverHealthRecord | None = None

    @property
    def store(self) -> TerminalObserverHealthStore:
        return self._store

    @property
    def serving_started_at(self) -> str | None:
        """This serving lifetime's start stamp, or ``None`` before startup created the accumulator."""

        return None if self._record is None else self._record.servingStartedAt

    def start_lifetime(self) -> None:
        """Begin the serving lifetime and attempt its initial record write.

        Serving startup calls this BEFORE the startup prime, so the prime's own outcome is the
        first transition the accumulator carries and the initial row is already on disk when the
        first observation completes.
        """

        record = self._initial_record()
        self._record = record
        self._publish(record)

    def record_success(self, *, started_at: datetime) -> None:
        """Publish one completed successful observer call.

        Success advances both timestamps, increments ``attemptCount``, sets the sticky
        ``initialObservationSucceeded``, clears every active failure field, and resets the
        consecutive count.
        """

        record = self._successor_at_success(started_at=started_at)
        self._record = record
        self._publish(record)

    def record_failure(
        self, error: Exception, *, phase: TerminalObserverHealthPhase, started_at: datetime
    ) -> None:
        """Publish one completed failed observer call.

        Failure advances ``lastAttemptAt``, increments both counters, RETAINS ``lastSuccessAt``,
        and records the phase-selected category and the bounded failure type. The accumulator is
        updated first and the write attempted second, so a write failure never costs the counters.
        """

        record = self._successor_at_failure(error, phase=phase, started_at=started_at)
        self._record = record
        self._publish(record)

    def served_payload(
        self, *, now: datetime, sweep_interval_seconds: float
    ) -> TerminalObserverHealthPayload | None:
        """This lifetime's serve-time payload, read from the PERSISTED row, never the accumulator."""

        return served_terminal_observer_health(
            store=self._store,
            serving_started_at=self.serving_started_at,
            now=now,
            sweep_interval_seconds=sweep_interval_seconds,
        )

    def _current(self) -> TerminalObserverHealthRecord:
        """The accumulator, begun on first use so no transition has an unstarted state."""

        if self._record is None:
            self._record = self._initial_record()
        return self._record

    def _initial_record(self) -> TerminalObserverHealthRecord:
        return TerminalObserverHealthRecord(
            schemaVersion=TERMINAL_OBSERVER_HEALTH_SCHEMA_VERSION,
            servingStartedAt=self._clock().isoformat(),
            lastAttemptAt=None,
            lastSuccessAt=None,
            attemptCount=0,
            consecutiveFailureCount=0,
            lastDurationSeconds=None,
            initialObservationSucceeded=False,
            activeFailureCategory=None,
            activeFailureType=None,
            activeFailureSummary=None,
        )

    def _successor_at_success(self, *, started_at: datetime) -> TerminalObserverHealthRecord:
        previous = self._current()
        completed_at = self._clock()
        stamp = completed_at.isoformat()
        return previous.model_copy(
            update={
                "lastAttemptAt": stamp,
                "lastSuccessAt": stamp,
                "attemptCount": saturate_observer_count(previous.attemptCount + 1),
                "consecutiveFailureCount": 0,
                "lastDurationSeconds": _duration_seconds(
                    started_at=started_at, completed_at=completed_at
                ),
                "initialObservationSucceeded": True,
                "activeFailureCategory": None,
                "activeFailureType": None,
                "activeFailureSummary": None,
            }
        )

    def _successor_at_failure(
        self,
        error: Exception,
        *,
        phase: TerminalObserverHealthPhase,
        started_at: datetime,
    ) -> TerminalObserverHealthRecord:
        previous = self._current()
        completed_at = self._clock()
        category, summary = _PHASE_FAILURE_PUBLICATION[phase]
        return previous.model_copy(
            update={
                "lastAttemptAt": completed_at.isoformat(),
                "attemptCount": saturate_observer_count(previous.attemptCount + 1),
                "consecutiveFailureCount": saturate_observer_count(
                    previous.consecutiveFailureCount + 1
                ),
                "lastDurationSeconds": _duration_seconds(
                    started_at=started_at, completed_at=completed_at
                ),
                "activeFailureCategory": category,
                "activeFailureType": classify_terminal_observer_failure(error),
                "activeFailureSummary": summary,
            }
        )

    def _publish(self, record: TerminalObserverHealthRecord) -> None:
        """Attempt one atomic replacement; a failure is logged once and never raised.

        A health-write failure must not roll back catalog truth, abort serving startup, or become
        a request failure, so it degrades to the fixed log line and the next observation retries
        the complete record. The exception text and traceback are deliberately NOT logged: the
        same rule that keeps the durable vocabulary bounded keeps the log line fixed.
        """

        try:
            self._store.write(record)
        except Exception:
            logger.warning(TERMINAL_OBSERVER_HEALTH_WRITE_FAILURE_LOG)


def _duration_seconds(*, started_at: datetime, completed_at: datetime) -> float:
    """The completed call's duration, clamped non-negative.

    ``max`` is not a defensive guard: the contract states the published duration is non-negative,
    and a clock adjustment between the two reads would otherwise publish a negative one.
    """

    return max(0.0, (completed_at - started_at).total_seconds())
