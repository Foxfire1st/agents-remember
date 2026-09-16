"""``LOCR-R17@v1``: the terminal observer's own health record, writer, payload, and served tail.

The producing defect is invisible in the health model this leaf extends: the agent-notifier loop
can report fresh ticks while nothing calls the terminal liveness sweeper, so one generic
"supervisor healthy" bit preserves the ambiguity. These cases pin the answer -- one exact atomic
v1 record, one exact serve-time payload, one ADDITIVE tail key -- and they are deliberately
separate from ``test_serving_observation_loop`` (which owns the lifespan's observation cadence and
R11's failure boundary) and from ``test_serving_startup_prime`` (which owns the prime's ordering):
this module drives the same real lifespan only where PUBLICATION ordering and the write-failure
recovery path are the subject, and otherwise drives the store, the accumulator, and the serve-time
assembly directly.

Every case here is a unit-lane case: no HTTP transport, no server, and no real second. The route
half calls the real ``_state_response`` handler and the real ``stream_events`` generator against a
stub projector, so the ETag/304 branch, the snapshot/delta asymmetry, and the assembled body are
the production ones.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from pydantic import ValidationError

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

import agents_remember.kernel.atomic_write as atomic_write_module
import agents_remember.serving.terminal_observer_health as health_module
from agents_remember.errors import HarnessControlError
from agents_remember.kernel.agentic_settings import load_agentic_settings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.observer.projection import LifecycleProjection, WorkspaceProjection
from agents_remember.serving._app_common import stream_events
from agents_remember.serving._app_lifespan import (
    _agent_notifier_heartbeat_payload,
    _terminal_observer_health_payload,
)
from agents_remember.serving._app_routes import _state_response
from agents_remember.serving.agent_notifier_heartbeat import AgentNotifierHeartbeatStore
from agents_remember.serving.build_info import ServingBuild
from agents_remember.serving.delta import DeltaEvent
from agents_remember.serving.served_state import (
    SERVED_TAIL_FIELDS,
    ServedWorkspaceProjection,
    served_state_tail,
)
from agents_remember.serving.terminal_liveness import TerminalCatalogLivenessConfig
from agents_remember.serving.terminal_observer_health import (
    TERMINAL_OBSERVER_FAILURE_TYPES,
    TERMINAL_OBSERVER_HEALTH_MAX_COUNT,
    TERMINAL_OBSERVER_HEALTH_SCHEMA_VERSION,
    TERMINAL_OBSERVER_HEALTH_WRITE_FAILURE_LOG,
    TerminalObserverHealthPayload,
    TerminalObserverHealthPublisher,
    TerminalObserverHealthRecord,
    TerminalObserverHealthStore,
    classify_terminal_observer_failure,
    saturate_observer_count,
    served_terminal_observer_health,
    terminal_observer_health_path,
)
from test_serving_observation_loop import (
    _advance,
    _RefreshProbe,
    _ServingFixture,
    _wait_until,
)

_STARTED_AT = datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC)
_LOG_NAME = "agents_remember.serving.app"
_SCHEMA_FIELD_NAMES = (
    "schemaVersion",
    "servingStartedAt",
    "lastAttemptAt",
    "lastSuccessAt",
    "attemptCount",
    "consecutiveFailureCount",
    "lastDurationSeconds",
    "initialObservationSucceeded",
    "activeFailureCategory",
    "activeFailureType",
    "activeFailureSummary",
)
"""The exact v1 field set, in the packet's order. Anything else is a schema change."""

_PAYLOAD_ONLY_FIELDS = ("ageSeconds", "lastSuccessAgeSeconds", "staleCutoffSeconds", "status")
"""The serve-time fields: declared on the payload and NEVER persisted beside the record."""


class _Clock:
    """A hand-advanced serving clock: one moment shared by the record and every age."""

    def __init__(self) -> None:
        self.moment = _STARTED_AT

    def now(self) -> datetime:
        return self.moment

    def advance(self, seconds: float) -> datetime:
        self.moment += timedelta(seconds=seconds)
        return self.moment


class _RouteProjector:
    """The projector seam ``_state_response`` reads: one snapshot and one opaque revision."""

    def __init__(self, snapshot: WorkspaceProjection) -> None:
        self.snapshot = snapshot

    def current(self) -> tuple[int, WorkspaceProjection]:
        return 7, self.snapshot

    def revision(self, _seq: int) -> str:
        return "rev-opaque-7"


class _StreamProjector:
    """The subscription seam ``stream_events`` reads: a boot snapshot, then one delta."""

    def __init__(self, items: list[tuple[int, DeltaEvent]]) -> None:
        self._items = items

    async def subscribe(self) -> Any:
        for item in self._items:
            yield item


def _config(tmp: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=tmp / "settings.json",
        coordination_root=tmp,
        workspace_root=tmp,
        transcript_root=tmp / "logs" / "mcp",
    )


def _projection() -> WorkspaceProjection:
    return WorkspaceProjection(generatedAt="2026-08-31T12:00:00Z")


def _lifecycle() -> LifecycleProjection:
    return LifecycleProjection(
        id="seat-1",
        state="running",
        phase="build",
        fleeting=False,
        startedAt="2026-08-31T12:00:00Z",
        lastEventTs="2026-08-31T12:00:00Z",
        tokens=0,
    )


def _serving_build() -> ServingBuild:
    return ServingBuild(version="3.0.0rc8", commit=None, booted_at=_STARTED_AT.isoformat())


def _route_runtime(
    tmp: Path,
    clock: _Clock,
    *,
    liveness_config: TerminalCatalogLivenessConfig | None = None,
) -> SimpleNamespace:
    """A serving runtime as the route and tail helpers read it, with real stores on ``tmp``."""

    return SimpleNamespace(
        config=_config(tmp),
        observer_root=tmp,
        projector=_RouteProjector(_projection()),
        build=_serving_build(),
        heartbeat_store=AgentNotifierHeartbeatStore(tmp),
        liveness_clock=clock.now,
        liveness_config=liveness_config or TerminalCatalogLivenessConfig(),
        observer_health=TerminalObserverHealthPublisher(tmp, clock.now),
    )


def _publisher(tmp: Path, clock: _Clock) -> TerminalObserverHealthPublisher:
    publisher = TerminalObserverHealthPublisher(tmp, clock.now)
    publisher.start_lifetime()
    return publisher


def _raw(tmp: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(terminal_observer_health_path(tmp).read_text()))


def _record(**overrides: object) -> TerminalObserverHealthRecord:
    values: dict[str, object] = {
        "schemaVersion": TERMINAL_OBSERVER_HEALTH_SCHEMA_VERSION,
        "servingStartedAt": _STARTED_AT.isoformat(),
        "lastAttemptAt": None,
        "lastSuccessAt": None,
        "attemptCount": 0,
        "consecutiveFailureCount": 0,
        "lastDurationSeconds": None,
        "initialObservationSucceeded": False,
        "activeFailureCategory": None,
        "activeFailureType": None,
        "activeFailureSummary": None,
    }
    values.update(overrides)
    return TerminalObserverHealthRecord.model_validate(values)


class _SecretTokenError(RuntimeError):
    """A custom subclass whose own class name must never reach the wire."""


class TerminalObserverHealthRecordTests(unittest.TestCase):
    """The durable record, its atomic writer, and the transitions that advance it."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.clock = _Clock()

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_atomic_replacement_never_exposes_a_partial_or_wrong_record(self) -> None:
        store = TerminalObserverHealthStore(self.tmp)
        first = _record(attemptCount=4, lastAttemptAt=_STARTED_AT.isoformat())
        second = _record(attemptCount=5, lastAttemptAt=_STARTED_AT.isoformat())
        store.write(first)
        first_bytes = store.path.read_bytes()
        at_replacement: list[dict[str, Any]] = []
        real_replace = atomic_write_module.os.replace

        def observing_replace(source: Path, destination: Path) -> None:
            # The destination as it exists at the exact instant of replacement. A writer that
            # edited the destination in place would show the new document's partial bytes here.
            at_replacement.append(cast(dict[str, Any], json.loads(destination.read_bytes())))
            real_replace(source, destination)

        with mock.patch.object(atomic_write_module.os, "replace", observing_replace):
            store.write(second)

        self.assertEqual(at_replacement, [first.model_dump(mode="json")])
        second_bytes = store.path.read_bytes()
        self.assertNotEqual(first_bytes, second_bytes)
        self.assertEqual(store.read(), second)

        with (
            mock.patch.object(atomic_write_module.os, "replace", side_effect=OSError("no space")),
            self.assertRaises(OSError),
        ):
            store.write(_record(attemptCount=6))

        # The interrupted replacement left the previous COMPLETE document readable, and its own
        # private temp file was removed rather than left behind as a second candidate record.
        self.assertEqual(store.path.read_bytes(), second_bytes)
        self.assertEqual(store.read(), second)
        self.assertEqual([entry.name for entry in store.path.parent.iterdir()], [store.path.name])

        # Bounded by construction, proven at two sizes: reclamation is the atomic replacement
        # itself, so N publications leave exactly one record of constant size -- no history, no
        # accumulation, and nothing for a compactor to own.
        for attempt in range(25):
            store.write(_record(attemptCount=attempt))
        self.assertEqual([entry.name for entry in store.path.parent.iterdir()], [store.path.name])
        self.assertLess(store.path.stat().st_size, 1024)
        self.assertEqual(store.read(), _record(attemptCount=24))

    def test_the_persisted_row_is_the_exact_v1_schema(self) -> None:
        publisher = _publisher(self.tmp, self.clock)
        raw = _raw(self.tmp)
        self.assertEqual(tuple(raw), _SCHEMA_FIELD_NAMES)
        self.assertEqual(raw["schemaVersion"], "ar-terminal-observer-health/v1")
        self.assertIsNone(raw["lastAttemptAt"])
        self.assertIsNone(raw["activeFailureSummary"])
        self.assertEqual(
            terminal_observer_health_path(self.tmp).name, "terminal-observer-health.json"
        )
        self.assertEqual(terminal_observer_health_path(self.tmp).parent.name, "workspace")

        publisher.record_success(started_at=self.clock.advance(1))
        # A transition advances VALUES, never the field set: no computed serve-time field and no
        # second-store bookkeeping key is ever persisted beside the eleven declared fields.
        self.assertEqual(tuple(_raw(self.tmp)), _SCHEMA_FIELD_NAMES)
        self.assertEqual(tuple(TerminalObserverHealthRecord.model_fields), _SCHEMA_FIELD_NAMES)
        self.assertEqual(
            tuple(TerminalObserverHealthPayload.model_fields)[:11], _SCHEMA_FIELD_NAMES
        )
        self.assertEqual(
            tuple(TerminalObserverHealthPayload.model_fields)[11:], _PAYLOAD_ONLY_FIELDS
        )

    def test_serving_lifetime_counts_saturate_at_the_unsigned_32_bit_maximum(self) -> None:
        self.assertEqual(TERMINAL_OBSERVER_HEALTH_MAX_COUNT, 4294967295)
        self.assertEqual(saturate_observer_count(0), 0)
        self.assertEqual(saturate_observer_count(TERMINAL_OBSERVER_HEALTH_MAX_COUNT), 4294967295)
        self.assertEqual(
            saturate_observer_count(TERMINAL_OBSERVER_HEALTH_MAX_COUNT + 1), 4294967295
        )
        self.assertEqual(saturate_observer_count(2**64), 4294967295)

        # The ceiling is a READ boundary too, on BOTH counters: the packet's record declares
        # unsigned 32-bit counts, so a row above it is not a row this contract serves. The writer's
        # saturating increments can never produce one, which is exactly why the bound belongs on the
        # model rather than only in the producer.
        for field in ("attemptCount", "consecutiveFailureCount"):
            for count in (
                TERMINAL_OBSERVER_HEALTH_MAX_COUNT,
                TERMINAL_OBSERVER_HEALTH_MAX_COUNT + 1,
            ):
                with self.subTest(field=field, count=count):
                    values = _record().model_dump()
                    values[field] = count
                    if count > TERMINAL_OBSERVER_HEALTH_MAX_COUNT:
                        with self.assertRaises(ValidationError):
                            TerminalObserverHealthRecord.model_validate(values)
                    else:
                        validated = TerminalObserverHealthRecord.model_validate(values)
                        self.assertEqual(getattr(validated, field), count)
        # ... and the bound is stated in the generated contract, not only enforced in Python.
        properties = TerminalObserverHealthRecord.model_json_schema()["properties"]
        self.assertEqual(properties["attemptCount"]["maximum"], TERMINAL_OBSERVER_HEALTH_MAX_COUNT)
        self.assertEqual(
            properties["consecutiveFailureCount"]["maximum"], TERMINAL_OBSERVER_HEALTH_MAX_COUNT
        )

    def test_a_success_then_failures_then_a_success_advance_the_exact_fields(self) -> None:
        publisher = _publisher(self.tmp, self.clock)
        store = TerminalObserverHealthStore(self.tmp)
        self.assertEqual(store.read(), _record())

        started = self.clock.now()
        completed = self.clock.advance(2)
        publisher.record_success(started_at=started)
        self.assertEqual(
            store.read(),
            _record(
                lastAttemptAt=completed.isoformat(),
                lastSuccessAt=completed.isoformat(),
                attemptCount=1,
                lastDurationSeconds=2.0,
                initialObservationSucceeded=True,
            ),
        )

        failed_at = self.clock.advance(1)
        publisher.record_failure(
            ValueError("steady-state terminal observation failed"),
            phase="steady-state",
            started_at=completed,
        )
        first_failure = store.read()
        assert first_failure is not None
        # Failure advances the attempt stamp, KEEPS the success stamp, sets the sticky success
        # flag, and records the phase-selected category with the bounded failure type.
        self.assertEqual(first_failure.lastAttemptAt, failed_at.isoformat())
        self.assertEqual(first_failure.lastSuccessAt, completed.isoformat())
        self.assertEqual(first_failure.attemptCount, 2)
        self.assertEqual(first_failure.consecutiveFailureCount, 1)
        self.assertEqual(first_failure.lastDurationSeconds, 1.0)
        self.assertTrue(first_failure.initialObservationSucceeded)
        self.assertEqual(first_failure.activeFailureCategory, "steady-state-refresh-failed")
        self.assertEqual(first_failure.activeFailureType, "ValueError")
        self.assertEqual(
            first_failure.activeFailureSummary, "steady-state terminal observation failed"
        )

        publisher.record_failure(OSError("again"), phase="steady-state", started_at=failed_at)
        second_failure = store.read()
        assert second_failure is not None
        self.assertEqual(second_failure.attemptCount, 3)
        self.assertEqual(second_failure.consecutiveFailureCount, 2)
        self.assertEqual(second_failure.lastSuccessAt, completed.isoformat())

        recovered_at = self.clock.advance(4)
        publisher.record_success(started_at=failed_at)
        self.assertEqual(
            store.read(),
            _record(
                lastAttemptAt=recovered_at.isoformat(),
                lastSuccessAt=recovered_at.isoformat(),
                attemptCount=4,
                lastDurationSeconds=4.0,
                initialObservationSucceeded=True,
            ),
        )

    def test_a_failed_write_keeps_serving_the_last_record_not_the_newer_accumulator(self) -> None:
        publisher = _publisher(self.tmp, self.clock)
        published_at = self.clock.advance(2)
        publisher.record_success(started_at=self.clock.now() - timedelta(seconds=2))
        published = TerminalObserverHealthStore(self.tmp).read()
        assert published is not None
        self.assertEqual(published.attemptCount, 1)

        with mock.patch.object(
            health_module, "atomic_write_text", side_effect=OSError("read-only")
        ):
            self.clock.advance(3)
            publisher.record_failure(
                RuntimeError("lost"), phase="steady-state", started_at=published_at
            )

        # The accumulator moved (attempt 2, failed) and the FILE did not: the newer in-memory
        # state is never substituted into a response, so the last valid payload is what serves.
        self.assertEqual(TerminalObserverHealthStore(self.tmp).read(), published)
        within_cutoff = publisher.served_payload(now=self.clock.now(), sweep_interval_seconds=10.0)
        assert within_cutoff is not None
        self.assertEqual(within_cutoff.attemptCount, 1)
        self.assertEqual(within_cutoff.status, "healthy")
        self.assertIsNone(within_cutoff.activeFailureCategory)

        # ... and that untouched payload ages into ``stale`` past the exact cutoff, which is the
        # only honest reading of a producer that stopped publishing.
        self.clock.advance(58)
        aged = publisher.served_payload(now=self.clock.now(), sweep_interval_seconds=10.0)
        assert aged is not None
        self.assertEqual((aged.ageSeconds, aged.status), (61.0, "stale"))

        # The next successful write publishes the accumulator's COMPLETE current record: the
        # failed write never lost the failed attempt, and the new success increments from it.
        publisher.record_success(started_at=self.clock.now())
        final = publisher.served_payload(now=self.clock.now(), sweep_interval_seconds=10.0)
        assert final is not None
        self.assertEqual(final.attemptCount, 3)
        self.assertEqual(final.consecutiveFailureCount, 0)
        self.assertEqual(final.status, "healthy")
        self.assertIsNone(final.activeFailureType)

    def test_status_is_initializing_degraded_healthy_then_stale_at_the_exact_cutoff(self) -> None:
        publisher = _publisher(self.tmp, self.clock)

        # Before any attempt the age is measured from the serving lifetime's own start, and the
        # cutoff is the configured full-observation cadence x 6 -- never a browser-driven value.
        initial = publisher.served_payload(now=self.clock.now(), sweep_interval_seconds=10.0)
        assert initial is not None
        self.assertEqual(initial.status, "initializing")
        self.assertEqual((initial.ageSeconds, initial.staleCutoffSeconds), (0.0, 60.0))
        self.assertIsNone(initial.lastAttemptAt)
        self.assertIsNone(initial.lastSuccessAgeSeconds)

        self.clock.advance(59.9)
        just_inside = publisher.served_payload(now=self.clock.now(), sweep_interval_seconds=10.0)
        assert just_inside is not None
        self.assertEqual(just_inside.status, "initializing")

        self.clock.advance(0.1)
        # ``ageSeconds >= staleCutoffSeconds``: exactly 60.0 is already stale, with no attempt.
        at_cutoff = publisher.served_payload(now=self.clock.now(), sweep_interval_seconds=10.0)
        assert at_cutoff is not None
        self.assertEqual((at_cutoff.ageSeconds, at_cutoff.status), (60.0, "stale"))

        publisher.record_failure(
            RuntimeError("x"), phase="steady-state", started_at=self.clock.now()
        )
        degraded = publisher.served_payload(now=self.clock.now(), sweep_interval_seconds=10.0)
        assert degraded is not None
        self.assertEqual((degraded.status, degraded.ageSeconds), ("degraded", 0.0))
        self.assertIsNone(degraded.lastSuccessAgeSeconds)

        self.clock.advance(1)
        publisher.record_success(started_at=self.clock.now() - timedelta(seconds=1))
        healthy = publisher.served_payload(now=self.clock.now(), sweep_interval_seconds=10.0)
        assert healthy is not None
        self.assertEqual(
            (healthy.status, healthy.ageSeconds, healthy.lastSuccessAgeSeconds),
            ("healthy", 0.0, 0.0),
        )

    def test_the_cutoff_is_exactly_six_configured_sweeps(self) -> None:
        self.assertEqual(TerminalCatalogLivenessConfig().sweep_interval_seconds, 10.0)
        default_runtime = _route_runtime(self.tmp, self.clock)
        default_runtime.observer_health.start_lifetime()
        default_runtime.observer_health.record_success(started_at=self.clock.now())
        default_payload = default_runtime.observer_health.served_payload(
            now=self.clock.now(),
            sweep_interval_seconds=default_runtime.liveness_config.sweep_interval_seconds,
        )
        assert default_payload is not None
        self.assertEqual(default_payload.staleCutoffSeconds, 60.0)

        other = _route_runtime(
            self.tmp,
            self.clock,
            liveness_config=TerminalCatalogLivenessConfig(sweep_interval_seconds=2.5),
        )
        other.observer_health.start_lifetime()
        other.observer_health.record_success(started_at=self.clock.now())
        # The published cutoff tracks the CONFIGURED cadence of the full-observation sweeper: a
        # handler that re-derived it from the loop's one-second tick, or from the notifier's
        # interval, would publish a different number here.
        wired = _terminal_observer_health_payload(cast(Any, other))
        assert wired is not None
        self.assertEqual(wired.staleCutoffSeconds, 15.0)
        self.assertEqual(15.0, 6 * other.liveness_config.sweep_interval_seconds)

    def test_every_unusable_source_omits_health_and_is_never_repaired(self) -> None:
        runtime = _route_runtime(self.tmp, self.clock)
        publisher = runtime.observer_health
        publisher.start_lifetime()
        current = terminal_observer_health_path(self.tmp)
        good = current.read_bytes()
        self.assertIsNotNone(TerminalObserverHealthStore(self.tmp).read())

        sources: dict[str, bytes | None] = {
            "missing": None,
            "unreadable": b"{not json",
            "not-an-object": b"[]",
            "wrong-schema": json.dumps({**_raw(self.tmp), "schemaVersion": "v2"}).encode(),
            "extra-key": json.dumps({**_raw(self.tmp), "extra": 1}).encode(),
            "missing-key": json.dumps(
                {key: value for key, value in _raw(self.tmp).items() if key != "attemptCount"}
            ).encode(),
            "wrong-type": json.dumps({**_raw(self.tmp), "attemptCount": "many"}).encode(),
            "count-above-ceiling": json.dumps(
                {**_raw(self.tmp), "attemptCount": TERMINAL_OBSERVER_HEALTH_MAX_COUNT + 1}
            ).encode(),
            "unparseable-stamp": json.dumps(
                {**_raw(self.tmp), "lastAttemptAt": "yesterday"}
            ).encode(),
            "naive-stamp": json.dumps(
                {**_raw(self.tmp), "servingStartedAt": "2026-08-31T12:00:00"}
            ).encode(),
            "prior-lifetime": _record(
                servingStartedAt=(_STARTED_AT - timedelta(days=1)).isoformat()
            )
            .model_dump_json()
            .encode(),
        }
        for name, content in sources.items():
            with self.subTest(source=name):
                if content is None:
                    current.unlink(missing_ok=True)
                else:
                    current.write_bytes(content)
                self.assertIsNone(
                    served_terminal_observer_health(
                        store=TerminalObserverHealthStore(self.tmp),
                        serving_started_at=publisher.serving_started_at,
                        now=self.clock.now(),
                        sweep_interval_seconds=10.0,
                    )
                )
                self.assertIsNone(
                    publisher.served_payload(now=self.clock.now(), sweep_interval_seconds=10.0)
                )
                if content is not None:
                    # The read route never rewrites, repairs, deletes, or creates the file: the
                    # bytes after the read are the bytes it was handed, invalid ones included.
                    self.assertEqual(current.read_bytes(), content)

        current.write_bytes(good)
        self.assertIsNotNone(
            publisher.served_payload(now=self.clock.now(), sweep_interval_seconds=10.0)
        )
        # A serving lifetime that has not started has nothing of its own to report either.
        unstarted = TerminalObserverHealthPublisher(self.tmp, self.clock.now)
        self.assertIsNone(unstarted.serving_started_at)
        self.assertIsNone(
            unstarted.served_payload(now=self.clock.now(), sweep_interval_seconds=10.0)
        )

    def test_failure_classification_is_bounded_ordered_and_secret_safe(self) -> None:
        self.assertEqual(
            tuple(TERMINAL_OBSERVER_FAILURE_TYPES.values()),
            (
                "HarnessControlError",
                "TimeoutError",
                "ConnectionError",
                "OSError",
                "RuntimeError",
                "ValueError",
                "Exception",
            ),
        )
        vectors: tuple[tuple[BaseException, str], ...] = (
            (HarnessControlError("x"), "HarnessControlError"),
            (TimeoutError("x"), "TimeoutError"),
            (ConnectionError("x"), "ConnectionError"),
            (OSError("x"), "OSError"),
            (RuntimeError("x"), "RuntimeError"),
            (_SecretTokenError("sk-secret"), "RuntimeError"),
            (ValueError("x"), "ValueError"),
            (KeyError("x"), "Exception"),
        )
        for error, expected in vectors:
            with self.subTest(error=type(error).__name__):
                self.assertEqual(classify_terminal_observer_failure(error), expected)

        # Secret-safety end to end: the message, its substrings, the arguments, and the custom
        # class name are absent from the durable bytes -- only the fixed vocabulary is copied.
        secret = "Bearer sk-secret\nfull prompt"
        publisher = _publisher(self.tmp, self.clock)
        publisher.record_failure(
            ValueError(secret), phase="steady-state", started_at=self.clock.now()
        )
        publisher.record_failure(
            _SecretTokenError(secret), phase="startup", started_at=self.clock.now()
        )
        persisted = terminal_observer_health_path(self.tmp).read_text()
        for leak in ("sk-secret", "Bearer", "full prompt", "SecretTokenError"):
            with self.subTest(leak=leak):
                self.assertNotIn(leak, persisted)
        final = TerminalObserverHealthStore(self.tmp).read()
        assert final is not None
        # The summary is selected SOLELY from the observer phase: the startup prime's failure
        # carries the startup category even though the exception itself names no phase.
        self.assertEqual(final.activeFailureCategory, "startup-refresh-failed")
        self.assertEqual(final.activeFailureSummary, "startup terminal observation failed")
        self.assertEqual(final.activeFailureType, "RuntimeError")


class TerminalObserverHealthLifespanTests(unittest.IsolatedAsyncioTestCase):
    """Publication from the real lifespan: the initial row, the prime, and the steady owner."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    async def test_the_prime_and_every_steady_pass_publish_success_and_failure_distinctly(
        self,
    ) -> None:
        probe = _RefreshProbe(fail_on=2)
        fixture = _ServingFixture(self.tmp, probe)
        store = TerminalObserverHealthStore(self.tmp)

        async with fixture.running():
            # Call 1 is the pre-serve prime and it is already published: the serving lifetime's
            # first transition is the prime's own outcome.
            prime_record = store.read()
            assert prime_record is not None
            self.assertEqual(prime_record.attemptCount, 1)
            self.assertTrue(prime_record.initialObservationSucceeded)
            self.assertEqual(prime_record.lastDurationSeconds, 0.0)
            self.assertIsNone(prime_record.activeFailureCategory)
            self.assertEqual(prime_record.servingStartedAt, _STARTED_AT.isoformat())

            # Call 2 is the recurring owner's own pass; its injected failure must be published as
            # a STEADY failure, distinct from the success that preceded it.
            await _wait_until(lambda: probe.calls == 2 and fixture.clock.requested.count(1.0) == 1)
            self.assertEqual(probe.outcomes, ["ok", "failed"])
            failed = store.read()
            assert failed is not None
            self.assertEqual(failed.attemptCount, 2)
            self.assertEqual(failed.consecutiveFailureCount, 1)
            self.assertEqual(failed.activeFailureCategory, "steady-state-refresh-failed")
            self.assertEqual(
                failed.activeFailureSummary, "steady-state terminal observation failed"
            )
            self.assertEqual(failed.activeFailureType, "RuntimeError")
            self.assertEqual(failed.lastSuccessAt, prime_record.lastSuccessAt)
            degraded = fixture.runtime.observer_health.served_payload(
                now=fixture.clock.now(), sweep_interval_seconds=10.0
            )
            assert degraded is not None
            self.assertEqual((degraded.status, degraded.ageSeconds), ("degraded", 0.0))

            fixture.clock.release()
            await _wait_until(lambda: probe.calls == 3 and fixture.clock.requested.count(1.0) == 2)
            self.assertEqual(probe.outcomes, ["ok", "failed", "ok"])
            healed = store.read()
            assert healed is not None
            # Recovery clears the failure fields WITHOUT resetting attemptCount, and the same
            # persisted row now serves ``healthy``.
            self.assertEqual(healed.attemptCount, 3)
            self.assertEqual(healed.consecutiveFailureCount, 0)
            self.assertIsNone(healed.activeFailureType)
            self.assertTrue(healed.initialObservationSucceeded)
            healthy = fixture.runtime.observer_health.served_payload(
                now=fixture.clock.now(), sweep_interval_seconds=10.0
            )
            assert healthy is not None
            self.assertEqual(healthy.status, "healthy")

    async def test_a_health_write_failure_logs_only_the_fixed_line_and_retries_publication(
        self,
    ) -> None:
        probe = _RefreshProbe()
        fixture = _ServingFixture(self.tmp, probe)
        store = TerminalObserverHealthStore(self.tmp)
        real_write = health_module.atomic_write_text
        attempts: list[int] = []

        def flaky_write(path: Path, text: str) -> None:
            attempts.append(len(attempts) + 1)
            if len(attempts) <= 2:
                raise OSError("Bearer sk-secret")
            real_write(path, text)

        with (
            mock.patch.object(health_module, "atomic_write_text", flaky_write),
            self.assertLogs(_LOG_NAME, level="WARNING") as captured,
        ):
            async with fixture.running():
                # The initial write and the prime's write both failed, so nothing is served
                # and the key is OMITTED -- while the prime itself still ran to completion.
                self.assertEqual(probe.calls, 1)
                self.assertIsNone(store.read())
                self.assertIsNone(
                    fixture.runtime.observer_health.served_payload(
                        now=fixture.clock.now(), sweep_interval_seconds=10.0
                    )
                )

                await _advance(fixture.clock, lambda: store.read() is not None)

        self.assertEqual(
            [record.getMessage() for record in captured.records],
            [TERMINAL_OBSERVER_HEALTH_WRITE_FAILURE_LOG] * 2,
        )
        self.assertNotIn("sk-secret", captured.output)
        self.assertNotIn("OSError", captured.output)
        # The next observation retried publication and wrote the accumulator's COMPLETE record.
        # Its count is 2 -- the prime's success AND the owner's first steady pass -- which is the
        # ordering proof that the initial write happened BEFORE the prime: a prime-first order
        # would have let the late initial row (count 0) overwrite it, leaving 1.
        published = store.read()
        assert published is not None
        self.assertEqual(published.attemptCount, 2)
        self.assertEqual(published.servingStartedAt, _STARTED_AT.isoformat())
        self.assertIsNone(published.activeFailureCategory)
        self.assertGreaterEqual(probe.calls, 2)


class TerminalObserverHealthServedTailTests(unittest.TestCase):
    """The additive serve-time surface: the tail key, the route, the SSE snapshot, the table."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.clock = _Clock()

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _served_body(self, runtime: SimpleNamespace) -> dict[str, Any]:
        response = _state_response(cast(Any, runtime), None)
        self.assertEqual(response.status_code, 200)
        return cast(dict[str, Any], json.loads(bytes(response.body)))

    def _publish_healthy(self, runtime: SimpleNamespace) -> TerminalObserverHealthPayload:
        runtime.observer_health.start_lifetime()
        runtime.observer_health.record_success(started_at=self.clock.now())
        payload = runtime.observer_health.served_payload(
            now=self.clock.now(),
            sweep_interval_seconds=runtime.liveness_config.sweep_interval_seconds,
        )
        assert payload is not None
        return payload

    def test_the_tail_is_additive_and_leaves_every_existing_field_unchanged(self) -> None:
        runtime = _route_runtime(self.tmp, self.clock)
        self.assertEqual(runtime.heartbeat_store.tick(now=self.clock.now()).sweepCount, 1)
        heartbeat_payload = _agent_notifier_heartbeat_payload(cast(Any, runtime))
        before = served_state_tail(build=runtime.build, heartbeat=heartbeat_payload)
        self.assertEqual(
            set(before), {"servingBuild", "agentNotifierHeartbeat", "supervisorHeartbeat"}
        )
        self.assertEqual(before["agentNotifierHeartbeat"], before["supervisorHeartbeat"])

        after = served_state_tail(
            build=runtime.build,
            heartbeat=heartbeat_payload,
            observer_health=self._publish_healthy(runtime),
        )
        self.assertEqual(set(after) - set(before), {"terminalObserverHealth"})
        self.assertEqual(
            {key: value for key, value in after.items() if key != "terminalObserverHealth"},
            before,
        )
        # Omitting the argument reproduces the pre-change tail byte for byte.
        self.assertEqual(
            served_state_tail(build=runtime.build, heartbeat=heartbeat_payload), before
        )

        # The key is declared on the served model, named in the tail contract, and ABSENT from the
        # projection the memo caches and the projector revises.
        self.assertIn("terminalObserverHealth", SERVED_TAIL_FIELDS)
        self.assertIn("terminalObserverHealth", ServedWorkspaceProjection.model_fields)
        self.assertNotIn("terminalObserverHealth", WorkspaceProjection.model_fields)
        validated = ServedWorkspaceProjection.model_validate(
            {**_projection().model_dump(), **after}
        )
        assert validated.terminalObserverHealth is not None
        self.assertEqual(validated.terminalObserverHealth.status, "healthy")

    def test_the_state_route_serves_the_key_without_touching_revision_etag_or_304(self) -> None:
        runtime = _route_runtime(self.tmp, self.clock)
        self._publish_healthy(runtime)
        body = self._served_body(runtime)
        self.assertEqual(body["terminalObserverHealth"]["status"], "healthy")
        served = ServedWorkspaceProjection.model_validate(body)
        assert served.terminalObserverHealth is not None
        self.assertEqual(served.terminalObserverHealth.attemptCount, 1)

        response = _state_response(cast(Any, runtime), None)
        self.assertEqual(response.headers["etag"], 'W/"rev-opaque-7"')
        cached = _state_response(cast(Any, runtime), 'W/"rev-opaque-7"')
        self.assertEqual((cached.status_code, cached.body), (304, b""))

        # The same revision and the same handler with the record GONE: the key disappears and
        # every other served field is byte-identical. Nothing about health can bust the ETag, and
        # the request never creates the file back.
        terminal_observer_health_path(self.tmp).unlink()
        absent = self._served_body(runtime)
        self.assertNotIn("terminalObserverHealth", absent)
        self.assertEqual(
            {key: value for key, value in body.items() if key != "terminalObserverHealth"}, absent
        )
        self.assertEqual(
            _state_response(cast(Any, runtime), None).headers["etag"], 'W/"rev-opaque-7"'
        )
        self.assertFalse(terminal_observer_health_path(self.tmp).exists())

    def test_the_sse_snapshot_carries_health_while_a_delta_carries_no_tail(self) -> None:
        runtime = _route_runtime(self.tmp, self.clock)
        payload = self._publish_healthy(runtime)
        items = [
            (7, DeltaEvent("snapshot", _projection())),
            (8, DeltaEvent("lifecycle", _lifecycle())),
        ]
        heartbeat_payload = _agent_notifier_heartbeat_payload(cast(Any, runtime))

        async def frames(observer_health: TerminalObserverHealthPayload | None) -> list[Any]:
            events = stream_events(
                cast(Any, _StreamProjector(items)),
                build=runtime.build,
                agent_notifier_heartbeat=heartbeat_payload,
                terminal_observer_health=observer_health,
            )
            async with contextlib.aclosing(events) as generator:
                return [event async for event in generator]

        with_health = asyncio.run(frames(payload))
        without_health = asyncio.run(frames(None))
        snapshot, delta = with_health
        self.assertEqual((snapshot.event, delta.event), ("snapshot", "lifecycle"))
        self.assertEqual(snapshot.data["terminalObserverHealth"]["status"], "healthy")
        self.assertIn("agentNotifierHeartbeat", snapshot.data)
        # A delta is one projection node and carries none of the tail -- unchanged by this leaf.
        self.assertEqual(delta.data, _lifecycle().model_dump(by_alias=True, exclude_none=True))
        self.assertNotIn("terminalObserverHealth", without_health[0].data)
        self.assertEqual(
            {key: value for key, value in snapshot.data.items() if key != "terminalObserverHealth"},
            without_health[0].data,
        )
        self.assertEqual(delta.data, without_health[1].data)

    def test_a_fresh_notifier_cannot_mask_a_stale_or_failed_observer(self) -> None:
        runtime = _route_runtime(self.tmp, self.clock)
        runtime.observer_health.start_lifetime()
        runtime.observer_health.record_success(started_at=self.clock.now())
        notifier_cutoff = load_agentic_settings(self.tmp).agent_notifier.stale_cutoff_seconds

        # Observation is current and the notifier has NEVER ticked: permitted, and the reason the
        # two payloads stay separate -- ``stale=true`` there says nothing about the observer.
        never_ticked = self._served_body(runtime)
        self.assertTrue(never_ticked["agentNotifierHeartbeat"]["stale"])
        self.assertIsNone(never_ticked["agentNotifierHeartbeat"]["lastTickAt"])
        self.assertEqual(never_ticked["terminalObserverHealth"]["status"], "healthy")

        # A notifier that sweeped FRESH right now, over an observer whose last completion is past
        # the observer cutoff: consumer freshness cannot make the producer current.
        self.clock.advance(notifier_cutoff + 60.0)
        runtime.heartbeat_store.tick(now=self.clock.now())
        stale = self._served_body(runtime)
        self.assertFalse(stale["agentNotifierHeartbeat"]["stale"])
        self.assertEqual(stale["agentNotifierHeartbeat"]["ageSeconds"], 0.0)
        self.assertEqual(stale["terminalObserverHealth"]["status"], "stale")
        self.assertEqual(
            stale["terminalObserverHealth"]["staleCutoffSeconds"],
            6 * runtime.liveness_config.sweep_interval_seconds,
        )

        # And with the notifier still fresh, a CURRENT failed attempt reports ``degraded``: the
        # failure is published with its bounded type and cannot be overridden either.
        runtime.observer_health.record_failure(
            TimeoutError("sweep timed out"), phase="steady-state", started_at=self.clock.now()
        )
        degraded = self._served_body(runtime)
        self.assertFalse(degraded["agentNotifierHeartbeat"]["stale"])
        self.assertEqual(degraded["terminalObserverHealth"]["status"], "degraded")
        self.assertEqual(degraded["terminalObserverHealth"]["activeFailureType"], "TimeoutError")
        self.assertEqual(
            degraded["terminalObserverHealth"]["activeFailureCategory"],
            "steady-state-refresh-failed",
        )

    def test_a_current_success_beside_a_fresh_notifier_reads_healthy_from_its_own_row(self) -> None:
        runtime = _route_runtime(self.tmp, self.clock)
        runtime.observer_health.start_lifetime()
        completed_at = self.clock.advance(4)
        runtime.observer_health.record_success(started_at=self.clock.now() - timedelta(seconds=4))
        observed = TerminalObserverHealthStore(self.tmp).read()
        assert observed is not None
        self.assertEqual(observed.attemptCount, 1)

        # Row 4 of the packet's cross-read table: BOTH stages are current in ONE served body. The
        # notifier sweeps one second AFTER the observer's last completion, so the two halves carry
        # distinguishable instants and the health half's provenance is checkable.
        ticked_at = self.clock.advance(1)
        runtime.heartbeat_store.tick(now=ticked_at)
        body = self._served_body(runtime)
        self.assertFalse(body["agentNotifierHeartbeat"]["stale"])
        self.assertEqual(body["terminalObserverHealth"]["status"], "healthy")

        # ... and every health fact is the OBSERVER's own persisted row, not the notifier's tick:
        # the tick moved the heartbeat's instants, advanced no health counter, and left the served
        # health payload reading exactly what the observer published.
        health = body["terminalObserverHealth"]
        heartbeat = body["agentNotifierHeartbeat"]
        self.assertEqual(heartbeat["lastTickAt"], ticked_at.isoformat())
        self.assertEqual(health["lastAttemptAt"], observed.lastAttemptAt)
        self.assertEqual(health["lastSuccessAt"], observed.lastSuccessAt)
        self.assertEqual(health["attemptCount"], observed.attemptCount)
        self.assertEqual(
            health["initialObservationSucceeded"], observed.initialObservationSucceeded
        )
        self.assertNotEqual(health["lastAttemptAt"], heartbeat["lastTickAt"])
        self.assertEqual(health["ageSeconds"], 1.0)
        self.assertIsNone(health["activeFailureCategory"])
        self.assertEqual(health["lastSuccessAt"], completed_at.isoformat())
