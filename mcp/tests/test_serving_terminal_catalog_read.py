"""``GET /api/terminal/sessions`` projects the stored catalog; it never produces it.

The route serves read-side traffic -- a dashboard poll, an operator glance, a headless caller
checking state -- and read-side traffic is optional and workload-dependent. When the handler
drove the liveness sweep, the projection BECAME the producer: every request probed adapters,
advanced evidence cursors, rewrote catalog rows, and could compact the store. Seat progress
then depended on somebody polling, and ordinary read scaling mutated state.

These cases drive the real registered route with instruments that would notice that coupling
returning. Each instrument states its own reach, and the two adapter instruments are deliberately
separate because they cover disjoint paths by which this process can reach a seat's adapter:

* a probe ledger counting each observation instrument the SWEEPER's probe uses, so "zero adapter
  evidence reads" is counted for the producer rather than inferred from the absence of a sweep;
* a second ledger that counts at the readers themselves -- the plain-name seams a route-side read
  can bind BY NAME, plus an identity sweep of every loaded ``agents_remember`` module for values
  that ARE one of the production readers, so a read bound BY OBJECT (a module-level
  ``import ... as`` alias) is counted too. Its reach, and what is still outside it, is stated where
  the instrument is defined;
* a catalog ledger counting every durable write, plus the catalog file's own bytes;
* a sweeper that counts invocations and can stand in for a stale or failing observer.

* :class:`StoredSnapshotProjectionTests` -- one hundred requests, one hundred equivalent
  answers, and a purity ledger that stays at zero.
* :class:`ProducerSeparationTests` -- probe, cursor advance, row mutation and compaction, each
  asserted on its own instrument instead of as one aggregate.
* :class:`RouteSeamReaderTests` -- the no-probing clause pinned where the request path would have
  to reach for it, instead of only on the producer's probe.
* :class:`ObserverIndependenceTests` -- the catalog changes between two requests because the
  background observer ran, and an observer that has failed still serves the stored snapshot
  instead of being repaired by the request.
* :class:`RequestPathPurityGuardTests` -- the negative guard: no request may enter a catalog
  producer.
* :class:`RouteContractStabilityTests` -- path, declared model, conditional-key behaviour and
  status semantics unchanged, through the real composed app.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import mock

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.conversations.control_wire import AdapterSnapshot
from agents_remember.models.terminal_catalog import (
    DEFAULT_LIVENESS_HYSTERESIS,
    TerminalCatalogEntry,
    TerminalCatalogLivenessConfig,
    TerminalLivenessEvidence,
)
from agents_remember.serving import (
    _app_terminal_routes,
    harness_control_client,
    terminal_evidence,
)
from agents_remember.serving._app_terminal_routes import _register_terminal_session_routes
from agents_remember.serving.app import (
    INFERRED_LIVE_INPUTS,
    ServingCollaborators,
    _build_serving_runtime,
    _ServingRuntime,
    create_app,
)
from agents_remember.serving.harness_control_client import read_control_snapshot
from agents_remember.serving.hosted_interactions import HostedInteractionSynchronizer
from agents_remember.serving.projector import LIVE_PROJECTION_CLOCK, ProjectionCadence
from agents_remember.serving.response_contract import TerminalSessionsResponse
from agents_remember.serving.terminal import TerminalHost, TerminalHostSeams
from agents_remember.serving.terminal_catalog import (
    TERMINATED_RETENTION_SECONDS,
    TerminalCatalog,
    terminal_catalog_path,
)
from agents_remember.serving.terminal_evidence import (
    TerminalEvidenceRead,
    read_entry_terminal_evidence,
)
from agents_remember.serving.terminal_liveness import (
    ControlSnapshotObserver,
    LivenessProbe,
    TerminalCatalogLivenessSweeper,
)
from agents_remember.serving.terminal_paste import capture_pane

PATH = "/api/terminal/sessions"
_TS = "2026-06-14T10:00:00+00:00"

#: Catalog port calls that make or reclaim state. A request path recording any of these has
#: entered a producer. Pure reads are deliberately absent: which read the projection serializes
#: is a reviewable design choice, not something this guard should freeze.
PRODUCER_CALLS = frozenset({"upsert", "record_liveness_probe", "compact", "_write_disk"})


def _config(root: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root,
        transcript_root=root / "logs" / "mcp",
    )


def _entry(session: str, root: Path, **overrides: Any) -> TerminalCatalogEntry:
    fields: dict[str, Any] = {
        "id": session,
        "label": "Worker",
        "kind": "harness",
        "harness": "claude",
        "lifecycle_id": "L1",
        "cwd": root,
        "tmux_name": f"ar-{session}",
        "command": ("claude",),
        "created_at": _TS,
        "last_attached_at": _TS,
        "status": "running",
    }
    fields.update(overrides)
    return TerminalCatalogEntry(**fields)


def _stored_rows(path: Path) -> dict[str, dict[str, Any]]:
    """The durable catalog rows, read from the file so nothing under test is disturbed."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    return {row["id"]: row for row in payload["sessions"]}


def _durable_tree(root: Path) -> dict[str, bytes]:
    """Every durable file under one root: the catalog, and the observer's inbox/gate stores."""

    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class _ProbeLedger:
    """How many times each observation instrument of the sweep's probe was read.

    ``adapter_reads`` is the count a projection must keep at zero: the bridge snapshot and the
    per-vendor terminal outcome are the two reads that leave this process for a seat's adapter.
    """

    def __init__(self) -> None:
        self.pane_captures = 0
        self.snapshot_reads = 0
        self.terminal_reads = 0

    @property
    def adapter_reads(self) -> int:
        return self.snapshot_reads + self.terminal_reads


def _counting_probe(
    ledger: _ProbeLedger,
    hysteresis: TerminalCatalogLivenessConfig,
    on_control_snapshot: ControlSnapshotObserver,
) -> LivenessProbe:
    """The production probe with every instrument counted and delegated to its real reader."""

    def capture(tmux_name: str) -> str:
        ledger.pane_captures += 1
        return capture_pane(tmux_name)

    def snapshot(entry: TerminalCatalogEntry) -> AdapterSnapshot:
        ledger.snapshot_reads += 1
        return read_control_snapshot(entry)

    def terminal(entry: TerminalCatalogEntry) -> TerminalEvidenceRead:
        ledger.terminal_reads += 1
        return read_entry_terminal_evidence(entry)

    return LivenessProbe(
        hysteresis=hysteresis,
        pane_capturer=capture,
        snapshot_reader=snapshot,
        terminal_reader=terminal,
        on_control_snapshot=on_control_snapshot,
    )


class _ReaderLedger:
    """Adapter reads that reach the READERS themselves, whoever asked for them.

    ``_ProbeLedger`` only sees reads routed through the sweeper's ``LivenessProbe``. A read invoked
    by the request path resolves its reader some other way, so it needs its own instrument: this one
    counts at the reader, which is the one place both paths have in common.
    """

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()

    @property
    def total(self) -> int:
        return sum(self.calls.values())


#: The plain names a route-side adapter read resolves through when it is bound by NAME: the route
#: module's own globals (its binding today, absent -- ``create=True`` installs the seam a regression
#: would use and removes it again) and the canonical attributes on the defining modules, which cover
#: a lookup performed at call time (an import or ``getattr`` inside the handler).
_READER_SEAMS: tuple[tuple[Any, str], ...] = (
    (_app_terminal_routes, "read_control_snapshot"),
    (_app_terminal_routes, "read_entry_terminal_evidence"),
    (harness_control_client, "read_control_snapshot"),
    (terminal_evidence, "read_entry_terminal_evidence"),
)

_PRODUCTION_READERS: dict[str, Callable[..., Any]] = {
    "read_control_snapshot": read_control_snapshot,
    "read_entry_terminal_evidence": read_entry_terminal_evidence,
}


def _reader_patch_targets() -> dict[tuple[str, str], tuple[Any, str, Callable[..., Any], bool]]:
    """Every ``(module, attribute)`` a route-side adapter read could resolve through.

    Two shapes are needed and neither alone is sufficient, because an adapter read can be bound by
    NAME or by OBJECT:

    * the plain-name seams above, for a name resolved at call time;
    * an IDENTITY SWEEP of every loaded ``agents_remember`` module for values that **are** one of
      the production readers. A module-level ``from ... import reader as alias`` binds the function
      object itself into that module's globals at import time, so patching the plain name misses it
      and rebinding the defining module does not touch it either -- the aliased global *is* the
      reader, and only an identity match finds it.

    What this still cannot see, stated so the guarantee matches the instrument: a reader reached
    through anything that is not a module global -- a function default (``LivenessProbe``'s
    ``snapshot_reader`` field), a closure cell, a class attribute, a dict entry, an instance
    attribute. Those are invisible to this ledger; the argument-path shapes of them are covered by
    other instruments instead (constructing a probe and observing a row still interrogates the host,
    which the tmux counter and ``record_liveness_probe`` catch).
    """

    targets: dict[tuple[str, str], tuple[Any, str, Callable[..., Any], bool]] = {
        (module.__name__, name): (module, name, _PRODUCTION_READERS[name], True)
        for module, name in _READER_SEAMS
    }
    for module in list(sys.modules.values()):
        if not isinstance(module, ModuleType) or not module.__name__.startswith("agents_remember"):
            continue
        for name, value in list(vars(module).items()):
            for reader in _PRODUCTION_READERS.values():
                if value is reader:
                    targets[(module.__name__, name)] = (module, name, reader, False)
    return targets


def _counting_reader(
    ledger: _ReaderLedger, label: str, reader: Callable[..., Any]
) -> Callable[..., Any]:
    """The production reader, counted first and delegated to after.

    Delegating matters for falsification: a mutating candidate fails on the count, not on an
    exception the double invented.
    """

    def counted(*args: Any, **kwargs: Any) -> Any:
        ledger.calls[label] += 1
        return reader(*args, **kwargs)

    return counted


@contextmanager
def _monitored_route_readers(ledger: _ReaderLedger) -> Iterator[None]:
    """Count every adapter read a request could reach, by name or by object identity."""

    with ExitStack() as stack:
        for module, name, reader, create in _reader_patch_targets().values():
            double = _counting_reader(ledger, f"{module.__name__}.{name}", reader)
            stack.enter_context(mock.patch.object(module, name, double, create=create))
        yield


class _RecordingCatalog(TerminalCatalog):
    """The production catalog plus a ledger of the port calls it was asked to perform.

    Subclassing the real catalog rather than proxying the port keeps the ledger honest: a caller
    reaches a write only through a real method. ``_write_disk`` is the one seam EVERY durable
    mutation passes through -- per-row writes and batch commits alike -- so its count is the
    number of times the catalog file was replaced.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.calls: Counter[str] = Counter()

    def _write_disk(self, entries: list[TerminalCatalogEntry]) -> None:
        self.calls["_write_disk"] += 1
        super()._write_disk(entries)

    def list(self, *, include_terminated: bool = False) -> list[TerminalCatalogEntry]:
        self.calls["list"] += 1
        return super().list(include_terminated=include_terminated)

    def list_committed(self, *, include_terminated: bool = False) -> list[TerminalCatalogEntry]:
        self.calls["list_committed"] += 1
        return super().list_committed(include_terminated=include_terminated)

    def upsert(self, entry: TerminalCatalogEntry) -> None:
        self.calls["upsert"] += 1
        super().upsert(entry)

    def record_liveness_probe(
        self,
        session_id: str,
        *,
        alive: bool,
        checked_at: datetime,
        evidence: TerminalLivenessEvidence | None = None,
        hysteresis: TerminalCatalogLivenessConfig = DEFAULT_LIVENESS_HYSTERESIS,
    ) -> TerminalCatalogEntry | None:
        self.calls["record_liveness_probe"] += 1
        return super().record_liveness_probe(
            session_id,
            alive=alive,
            checked_at=checked_at,
            evidence=evidence,
            hysteresis=hysteresis,
        )

    def compact(
        self,
        *,
        now: datetime,
        retain_seconds: float = TERMINATED_RETENTION_SECONDS,
        registered_execution_ids: frozenset[str] = frozenset(),
    ) -> int:
        self.calls["compact"] += 1
        return super().compact(
            now=now,
            retain_seconds=retain_seconds,
            registered_execution_ids=registered_execution_ids,
        )


class _RecordingSweeper(TerminalCatalogLivenessSweeper):
    """The production sweeper, counting invocations and able to model a failed observer.

    ``fail_with`` is the observer-health failure the recovery clause is about: a producer that
    raises. A request path that reached for the sweeper at all would surface it -- as a raised
    error or as a mutation -- instead of quietly serving what is stored.
    """

    def __init__(self, *args: Any, fail_with: Exception | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.refreshes = 0
        self.fail_with = fail_with

    def refresh(self) -> list[TerminalCatalogEntry]:
        self.refreshes += 1
        if self.fail_with is not None:
            raise self.fail_with
        return super().refresh()


class _RouteUnderTest:
    """One catalog, one host, one sweeper and the real route around them, wired like production.

    ``_build_serving_runtime`` is the constructor ``create_app`` itself uses, so the runtime is
    production's; only the sweeper is rebuilt around a counting probe, and only the host's tmux
    seam is faked -- the seam the production host offers for exactly this substitution. The app
    carries no lifespan, so nothing but a request can move anything.
    """

    def __init__(
        self,
        root: Path,
        *,
        alive: bool = True,
        sweeper_failure: Exception | None = None,
    ) -> None:
        self.root = root
        self.catalog_file = terminal_catalog_path(root)
        self.catalog = _RecordingCatalog(self.catalog_file)
        self.probes = _ProbeLedger()
        self.tmux_probes = 0
        self.probed_sessions: list[str] = []
        self.alive = alive
        self.host = TerminalHost(seams=TerminalHostSeams(tmux_probe=self._tmux_probe))
        runtime, _metrics = _build_serving_runtime(
            _config(root),
            ProjectionCadence(interval=100),
            LIVE_PROJECTION_CLOCK,
            INFERRED_LIVE_INPUTS,
            ServingCollaborators(terminal_host=self.host, terminal_catalog=self.catalog),
        )
        self.sweeper = _RecordingSweeper(
            self.catalog,
            self.host,
            probe=_counting_probe(
                self.probes,
                runtime.liveness_config,
                HostedInteractionSynchronizer(runtime.observer_root).observe,
            ),
            fail_with=sweeper_failure,
        )
        self.runtime: _ServingRuntime = replace(runtime, liveness_sweeper=self.sweeper)
        self.app = FastAPI()
        _register_terminal_session_routes(self.app, self.runtime)
        self._stack = ExitStack()
        self.client = self._stack.enter_context(TestClient(self.app))

    def _tmux_probe(self, tmux_name: str) -> bool:
        self.tmux_probes += 1
        self.probed_sessions.append(tmux_name)
        return self.alive

    def seed(self) -> None:
        """Two durable rows, then a cleared ledger so only request-time work is counted."""

        self.catalog.upsert(
            _entry(
                "seat-live",
                self.root,
                control_endpoint=self.root / "control.sock",
                control_state="ready",
                control_protocol="ar-harness-control/v1",
                turn_state="working",
                turn_state_changed_at=_TS,
                terminal_evidence_sequence=41,
                terminal_native_cursor="cursor-41",
            )
        )
        self.catalog.upsert(
            _entry(
                "seat-landed", self.root, status="landed", created_at="2026-06-14T11:00:00+00:00"
            )
        )
        self.catalog.calls.clear()

    def add_reclaimable_tombstone(self) -> None:
        """A terminated row past the retention window: a sweep WOULD reclaim it."""

        self.catalog.upsert(
            _entry(
                "seat-old",
                self.root,
                kind="terminal",
                harness=None,
                status="terminated",
                terminated_at="2026-01-01T00:00:00+00:00",
                created_at="2026-06-14T09:00:00+00:00",
            )
        )
        self.catalog.calls.clear()

    def get(self, count: int = 1) -> list[Any]:
        return [self.client.get(PATH) for _ in range(count)]

    def close(self) -> None:
        self._stack.close()


class _ProjectionTestCase(unittest.TestCase):
    """Shared fixture: a seeded route under test with its pre-request world captured."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.route = _RouteUnderTest(self.root)
        self.addCleanup(self.route.close)
        self.route.seed()
        self.before_bytes = self.route.catalog_file.read_bytes()
        self.before_rows = _stored_rows(self.route.catalog_file)
        self.before_tree = _durable_tree(self.root)


class StoredSnapshotProjectionTests(_ProjectionTestCase):
    """Many requests, equivalent answers, and a purity ledger that never moves."""

    def test_one_hundred_gets_produce_one_hundred_equivalent_answers(self) -> None:
        responses = self.route.get(100)

        self.assertEqual({response.status_code for response in responses}, {200})
        self.assertEqual(len({response.text for response in responses}), 1)
        # The conforming example, at the literal counts it names.
        self.assertEqual(self.route.probes.adapter_reads, 0)
        self.assertEqual(self.route.sweeper.refreshes, 0)
        self.assertEqual(self.route.catalog_file.read_bytes(), self.before_bytes)
        self.assertEqual(_durable_tree(self.root), self.before_tree)


class ProducerSeparationTests(_ProjectionTestCase):
    """Each prohibited side effect on its own instrument, never as one aggregate."""

    def test_a_request_probes_no_adapter(self) -> None:
        self.route.get(2)

        self.assertEqual(self.route.probes.snapshot_reads, 0, "a bridge snapshot was read")
        self.assertEqual(self.route.probes.terminal_reads, 0, "a terminal outcome was read")
        self.assertEqual(self.route.probes.adapter_reads, 0, "adapter evidence was read")
        self.assertEqual(self.route.probes.pane_captures, 0, "a pane was captured")
        self.assertEqual(self.route.tmux_probes, 0, "a tmux session was interrogated")
        self.assertEqual(self.route.probed_sessions, [])
        self.assertEqual(self.route.sweeper.refreshes, 0, "a liveness sweep was driven")

    def test_a_request_advances_no_evidence_cursor(self) -> None:
        first = self.route.get()[0]
        self.route.get(2)

        live = first.json()["sessions"][0]
        self.assertEqual(live["terminalEvidenceSequence"], 41)
        self.assertEqual(live["terminalNativeCursor"], "cursor-41")
        after = _stored_rows(self.route.catalog_file)["seat-live"]
        self.assertEqual(after["terminalEvidenceSequence"], 41)
        self.assertEqual(after["terminalNativeCursor"], "cursor-41")
        self.assertEqual(after["turnStateChangedAt"], _TS)
        self.assertEqual(self.route.catalog.calls["_write_disk"], 0)

    def test_a_request_mutates_no_catalog_row(self) -> None:
        self.route.get(2)

        self.assertEqual(_stored_rows(self.route.catalog_file), self.before_rows)
        self.assertEqual(self.route.catalog_file.read_bytes(), self.before_bytes)
        self.assertEqual(self.route.catalog.calls["_write_disk"], 0, "the catalog was rewritten")
        self.assertEqual(self.route.catalog.calls["upsert"], 0, "a row was written")

    def test_a_request_compacts_nothing(self) -> None:
        self.route.add_reclaimable_tombstone()

        self.route.get(2)

        self.assertEqual(self.route.catalog.calls["compact"], 0, "compaction was driven")
        self.assertIn("seat-old", _stored_rows(self.route.catalog_file))


class RouteSeamReaderTests(_ProjectionTestCase):
    """The no-probing clause pinned where the request path would have to reach for it.

    The producer's probe ledger cannot see a read the ROUTE makes: that read resolves
    ``read_control_snapshot``/``read_entry_terminal_evidence`` some other way instead of through the
    sweeper's ``LivenessProbe``. This case counts at the readers for both ways a handler can hold
    one -- bound by name (a call-time lookup on the defining module, or the route module's own
    global) and bound by object (a module-level ``import ... as`` alias, whose global *is* the
    production function and is found only by identity) -- so "the projection does not probe an
    adapter" is measured on the request path itself rather than inferred from the absence of a
    sweep.
    """

    def test_the_request_path_reaches_no_adapter_reader(self) -> None:
        ledger = _ReaderLedger()

        with _monitored_route_readers(ledger):
            responses = self.route.get(3)

        self.assertEqual(ledger.total, 0, f"the request path read an adapter: {dict(ledger.calls)}")
        self.assertEqual({response.status_code for response in responses}, {200})
        self.assertEqual(
            responses[-1].json(),
            {
                "sessions": [
                    row for row in self.before_rows.values() if row["status"] != "terminated"
                ]
            },
            "the responses still project the stored snapshot",
        )
        self.assertEqual(self.route.catalog.calls["_write_disk"], 0)


class ObserverIndependenceTests(_ProjectionTestCase):
    """The snapshot moves on the observer's clock, and only on the observer's clock."""

    def test_a_catalog_change_between_reads_comes_from_the_observer(self) -> None:
        first = self.route.get()[0]
        self.assertEqual(self.route.sweeper.refreshes, 0)

        # The background observer runs -- once, on its own call, because nothing polled.
        self.route.alive = False
        self.route.sweeper.refresh()
        self.assertEqual(self.route.sweeper.refreshes, 1)
        self.assertEqual(self.route.catalog.calls["record_liveness_probe"], 1)

        before_request = Counter(self.route.catalog.calls)
        second = self.route.get()[0]

        self.assertNotEqual(first.text, second.text, "the observer's change must be visible")
        self.assertEqual(second.json()["sessions"][0]["status"], "exited")
        self.assertEqual(self.route.sweeper.refreshes, 1, "a GET drove the sweep")
        after_request = self.route.catalog.calls
        self.assertEqual(
            {name: after_request[name] for name in PRODUCER_CALLS},
            {name: before_request[name] for name in PRODUCER_CALLS},
            "the GET made a producer call of its own",
        )
        self.assertEqual(after_request["list"] - before_request["list"], 1, "one read per request")

    def test_a_failed_observer_still_serves_the_stored_snapshot(self) -> None:
        route = _RouteUnderTest(self.root, sweeper_failure=RuntimeError("observer is stale"))
        self.addCleanup(route.close)
        route.seed()
        before_tree = _durable_tree(self.root)

        response = route.get()[0]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"sessions": list(_stored_rows(route.catalog_file).values())},
            "the stored snapshot is served as stored",
        )
        self.assertEqual(route.sweeper.refreshes, 0, "the route reached for the observer")
        self.assertEqual(route.probes.adapter_reads, 0)
        self.assertEqual(route.catalog.calls["_write_disk"], 0, "the route attempted a repair")
        self.assertEqual(route.catalog.calls["compact"], 0)
        self.assertEqual(_durable_tree(self.root), before_tree)
        # The double is a real failure, not an inert one: recovery belongs to the observer's own
        # retry, so no request may have been able to reach it.
        with self.assertRaises(RuntimeError):
            route.sweeper.refresh()


class RequestPathPurityGuardTests(_ProjectionTestCase):
    """The negative guard: no request may enter a catalog producer."""

    def test_the_request_path_enters_no_catalog_producer(self) -> None:
        self.route.get(3)

        entered = {
            name: count
            for name, count in self.route.catalog.calls.items()
            if name in PRODUCER_CALLS
        }
        self.assertEqual(entered, {}, "the request path entered a catalog producer")
        reads = self.route.catalog.calls["list"] + self.route.catalog.calls["list_committed"]
        self.assertEqual(reads, 3, "each request performed exactly one catalog read")
        self.assertEqual(self.route.probes.adapter_reads, 0)
        self.assertEqual(self.route.sweeper.refreshes, 0, "a request drove the liveness sweep")


class RouteContractStabilityTests(_ProjectionTestCase):
    """Path, declared model, conditional keys and status semantics, through the real app."""

    def test_the_composed_app_serves_the_stored_snapshot_under_the_declared_model(self) -> None:
        app = create_app(_config(self.root), cadence=ProjectionCadence(interval=100))
        route = next(
            item
            for item in app.routes
            if isinstance(item, APIRoute) and item.path == PATH and item.methods == {"GET"}
        )
        self.assertIs(route.response_model, TerminalSessionsResponse)
        self.assertTrue(route.response_model_exclude_unset)
        # The composed app serves the very handler definition these cases drive: registration
        # builds a fresh closure per app, so the shared definition is the identity to compare.
        served = next(
            item
            for item in self.route.app.routes
            if isinstance(item, APIRoute) and item.path == PATH
        )
        self.assertEqual(route.endpoint.__qualname__, served.endpoint.__qualname__)

        response = self.route.get()[0]

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload,
            {
                "sessions": [
                    row for row in self.before_rows.values() if row["status"] != "terminated"
                ]
            },
            "the response is the stored snapshot, projected",
        )
        self.assertEqual(len(TerminalSessionsResponse.model_validate(payload).sessions), 2)
        # Conditional keys: what a row never set stays absent instead of arriving as null.
        live, landed = payload["sessions"]
        self.assertEqual(live["controlState"], "ready")
        self.assertNotIn("terminatedAt", live)
        self.assertNotIn("controlActivity", live)
        self.assertNotIn("controlState", landed)
        self.assertNotIn("terminatedAt", landed)
        # Reading the projection changed nothing on the way out.
        self.assertEqual(self.route.catalog_file.read_bytes(), self.before_bytes)
