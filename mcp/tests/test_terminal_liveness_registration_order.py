"""Behavioral proof that a due full sweep registers terminal evidence before compaction.

These cases exercise the full-sweep registration/compaction boundary at the real
``TerminalCatalog`` and ``TerminalCatalogLivenessSweeper`` seams.  The registrar is a
stand-in for the task-owned execution registrar: it records the exact rows it was
offered, returns only the ids it proved, and can be made to fail.  The compactor
records the proven set it received and delegates to the real ``compact``.
"""

from __future__ import annotations

import contextlib
import sys
import tempfile
import unittest
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.models.conversations.control_wire import (
    AdapterSnapshot,
    ControlIdentity,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.serving.terminal_catalog import TerminalCatalog
from agents_remember.serving.terminal_evidence import TerminalEvidenceRead
from agents_remember.serving.terminal_liveness import (
    LivenessProbe,
    TerminalCatalogLivenessConfig,
    TerminalCatalogLivenessSweeper,
    TerminalLivenessActions,
)
from agents_remember.serving.terminal_tmux import TmuxProbeResult

NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC)
# Older than the 86400s terminated-row retention window, so the row is reclaimable
# as soon as (and only when) its execution id is proven registered.
_RETENTION_EXPIRED_AT = "2026-08-25T00:00:00+00:00"

_TASK_REF = TaskDocumentRef(
    repository="agents-remember",
    path="tasks/agents-remember/260831_lifecycle-owned-completion-relay/1.json",
)

_HYSTERESIS = TerminalCatalogLivenessConfig(
    failure_threshold=3,
    minimum_failure_window_seconds=5.0,
    pane_gone_failure_threshold=1,
    sweep_interval_seconds=10.0,
)


def _row(session_id: str) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=session_id,
        label=f"Chat {session_id}",
        kind="harness",
        harness="codex",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=("codex",),
        created_at="2026-08-31T00:00:00+00:00",
        last_attached_at="2026-08-31T00:00:00+00:00",
        status="running",
        control_state="ready",
        control_endpoint=Path(f"/tmp/{session_id}.sock"),
        control_activity="idle",
        control_acceptance="immediate",
    )


def _terminated_leaf_row(session_id: str) -> TerminalCatalogEntry:
    """A task-bound worker seat row: the one row class compaction may reclaim only by proof."""
    return replace(
        _row(session_id),
        status="terminated",
        terminated_at=_RETENTION_EXPIRED_AT,
        seat_role="worker",
        task_document_ref=_TASK_REF,
    )


class _LiveHost:
    def get(self, _session_id: str) -> None:
        return None

    def has_session(self, tmux_name: str) -> bool:
        return tmux_name.startswith("ar-")

    def probe_session(self, _tmux_name: str) -> TmuxProbeResult:
        return TmuxProbeResult(exists=True, evidence="alive")


def _snapshot(entry: TerminalCatalogEntry) -> AdapterSnapshot:
    return AdapterSnapshot(
        identity=ControlIdentity(entry.id, entry.tmux_name, entry.created_at),
        control="ready",
        activity="running",
        acceptance="immediate",
        vendor_session_id="vendor-1",
        raw={"source": "registration-order-test"},
    )


def _empty_terminal_read(_entry: TerminalCatalogEntry) -> TerminalEvidenceRead:
    return TerminalEvidenceRead(projection=None)


class _Clock:
    """A movable sweep clock, so cadence branches are entered without sleeping."""

    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


class TerminalLivenessRegistrationOrderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self._root = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _sweeper(
        self,
        catalog: TerminalCatalog,
        *,
        clock: _Clock | None = None,
        actions: TerminalLivenessActions | None = None,
    ) -> TerminalCatalogLivenessSweeper:
        return TerminalCatalogLivenessSweeper(
            catalog,
            _LiveHost(),
            now=clock or _Clock(NOW),
            probe=LivenessProbe(
                hysteresis=_HYSTERESIS,
                pane_capturer=lambda _tmux_name: "",
                snapshot_reader=_snapshot,
                terminal_reader=_empty_terminal_read,
            ),
            actions=actions,
        )

    def test_due_sweep_registers_committed_terminated_rows_before_compaction(self) -> None:
        """The full pass orders batch commit, terminated enumeration, registration, compaction.

        The enumeration is instrumented where it happens, not merely where its result is
        consumed: the traced ``list(include_terminated=True)`` records the batch-commit
        state observed AT THE READ, so an enumeration moved inside the observation batch
        (reading the uncommitted buffer) or ahead of it is visible in the chain rather
        than inferred from the registrar's own call site.

        The second phase keeps a different observable on the same seam: with no registrar
        wired at all, compaction is handed the empty proven set rather than an assumed
        registration, so a task-bound row is never reclaimed on the strength of a call
        that never ran.
        """
        catalog = TerminalCatalog(self._root / "ordered.json")
        catalog.upsert(_row("live"))
        catalog.upsert(_terminated_leaf_row("term-a"))
        catalog.upsert(_terminated_leaf_row("term-b"))
        events: list[str] = []

        def register(rows: tuple[TerminalCatalogEntry, ...]) -> frozenset[str]:
            events.append("register")
            self.assertIsNone(catalog._batch)
            self.assertEqual([row.id for row in rows], ["term-a", "term-b"])
            self.assertTrue(all(row.status == "terminated" for row in rows))
            return frozenset()

        arguments: list[frozenset[str]] = []
        real_compact = catalog.compact

        def compact(*, now: datetime, registered_execution_ids: frozenset[str]) -> int:
            events.append("compact")
            arguments.append(registered_execution_ids)
            return real_compact(now=now, registered_execution_ids=registered_execution_ids)

        real_batch = TerminalCatalog.batch
        real_list = TerminalCatalog.list

        def recording_batch(self: TerminalCatalog) -> Iterator[None]:
            events.append("batch-enter")
            with real_batch(self):
                yield
            events.append("batch-exit")

        def recording_list(
            self: TerminalCatalog, *, include_terminated: bool = False
        ) -> list[TerminalCatalogEntry]:
            # The terminated-row read is the pass stage that must see committed state, so
            # the event carries the batch state as it stood at the read itself.
            if include_terminated:
                batch_state = "open" if self._batch is not None else "closed"
                events.append(f"enumerate[include_terminated=True, batch={batch_state}]")
            return real_list(self, include_terminated=include_terminated)

        sweeper = self._sweeper(
            catalog, actions=TerminalLivenessActions(register_execution_evidence=register)
        )
        with (
            mock.patch.object(TerminalCatalog, "batch", contextlib.contextmanager(recording_batch)),
            mock.patch.object(TerminalCatalog, "list", recording_list),
            mock.patch.object(catalog, "compact", side_effect=compact),
        ):
            sweeper.refresh()

        self.assertEqual(
            events,
            [
                "batch-enter",
                "batch-exit",
                "enumerate[include_terminated=True, batch=closed]",
                "register",
                "compact",
            ],
        )
        self.assertEqual(arguments, [frozenset()])

        unwired = TerminalCatalog(self._root / "unwired.json")
        unwired.upsert(_terminated_leaf_row("unregistered"))
        proofs: list[frozenset[str]] = []
        real_unwired_compact = unwired.compact

        def unwired_compact(*, now: datetime, registered_execution_ids: frozenset[str]) -> int:
            proofs.append(registered_execution_ids)
            return real_unwired_compact(now=now, registered_execution_ids=registered_execution_ids)

        with mock.patch.object(unwired, "compact", side_effect=unwired_compact):
            self._sweeper(unwired).refresh()

        self.assertEqual(proofs, [frozenset()])
        unregistered = unwired.get("unregistered")
        self.assertIsNotNone(unregistered)
        assert unregistered is not None
        self.assertEqual(unregistered.status, "terminated")

    def test_partial_registration_compacts_only_the_proven_rows(self) -> None:
        """Two terminated rows are offered; compaction receives the singleton proven set."""
        catalog = TerminalCatalog(self._root / "partial.json")
        catalog.upsert(_terminated_leaf_row("proven"))
        catalog.upsert(_terminated_leaf_row("unproven"))
        offered: list[list[str]] = []

        def register(rows: tuple[TerminalCatalogEntry, ...]) -> frozenset[str]:
            offered.append([row.id for row in rows])
            return frozenset({"proven"})

        arguments: list[frozenset[str]] = []
        real_compact = catalog.compact

        def compact(*, now: datetime, registered_execution_ids: frozenset[str]) -> int:
            arguments.append(registered_execution_ids)
            return real_compact(now=now, registered_execution_ids=registered_execution_ids)

        sweeper = self._sweeper(
            catalog, actions=TerminalLivenessActions(register_execution_evidence=register)
        )
        with mock.patch.object(catalog, "compact", side_effect=compact):
            sweeper.refresh()

        self.assertEqual(offered, [["proven", "unproven"]])
        self.assertEqual(arguments, [frozenset({"proven"})])
        self.assertIsNone(catalog.get("proven"))
        unproven = catalog.get("unproven")
        self.assertIsNotNone(unproven)
        assert unproven is not None
        self.assertEqual(unproven.status, "terminated")

    def test_registration_failure_prevents_compaction_and_leaves_rows_retryable(self) -> None:
        """A raising registrar aborts the pass before compaction and keeps the rows."""
        catalog = TerminalCatalog(self._root / "failure.json")
        catalog.upsert(_terminated_leaf_row("retry-a"))
        catalog.upsert(_terminated_leaf_row("retry-b"))

        def register(_rows: tuple[TerminalCatalogEntry, ...]) -> frozenset[str]:
            raise RuntimeError("registrar unavailable")

        arguments: list[frozenset[str]] = []
        real_compact = catalog.compact

        def compact(*, now: datetime, registered_execution_ids: frozenset[str]) -> int:
            arguments.append(registered_execution_ids)
            return real_compact(now=now, registered_execution_ids=registered_execution_ids)

        sweeper = self._sweeper(
            catalog, actions=TerminalLivenessActions(register_execution_evidence=register)
        )
        with (
            mock.patch.object(catalog, "compact", side_effect=compact),
            self.assertRaisesRegex(RuntimeError, "registrar unavailable"),
        ):
            sweeper.refresh()

        self.assertEqual(arguments, [])
        for session_id in ("retry-a", "retry-b"):
            row = catalog.get(session_id)
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row.status, "terminated")

    def test_restart_after_registration_before_compaction_reregisters_and_loses_nothing(
        self,
    ) -> None:
        """A crash after proven registration leaves the rows for an idempotent second pass."""
        path = self._root / "restart.json"
        catalog = TerminalCatalog(path)
        catalog.upsert(_terminated_leaf_row("seat-a"))
        catalog.upsert(_terminated_leaf_row("seat-b"))
        offered: list[list[str]] = []

        def register(rows: tuple[TerminalCatalogEntry, ...]) -> frozenset[str]:
            offered.append([row.id for row in rows])
            return frozenset(row.id for row in rows)

        def crash(*, now: datetime, registered_execution_ids: frozenset[str]) -> int:
            raise RuntimeError("process stopped before compaction")

        first = self._sweeper(
            catalog, actions=TerminalLivenessActions(register_execution_evidence=register)
        )
        with (
            mock.patch.object(catalog, "compact", side_effect=crash),
            self.assertRaisesRegex(RuntimeError, "process stopped before compaction"),
        ):
            first.refresh()

        self.assertEqual(offered, [["seat-a", "seat-b"]])
        surviving = catalog.list(include_terminated=True)
        self.assertEqual(sorted(row.id for row in surviving), ["seat-a", "seat-b"])

        restarted = TerminalCatalog(path)
        proofs: list[frozenset[str]] = []
        real_compact = restarted.compact

        def compact(*, now: datetime, registered_execution_ids: frozenset[str]) -> int:
            proofs.append(registered_execution_ids)
            return real_compact(now=now, registered_execution_ids=registered_execution_ids)

        second = self._sweeper(
            restarted, actions=TerminalLivenessActions(register_execution_evidence=register)
        )
        with mock.patch.object(restarted, "compact", side_effect=compact):
            second.refresh()

        self.assertEqual(offered, [["seat-a", "seat-b"], ["seat-a", "seat-b"]])
        self.assertEqual(proofs, [frozenset({"seat-a", "seat-b"})])
        self.assertEqual(restarted.list(include_terminated=True), [])

    def test_starting_fast_path_neither_registers_nor_compacts_while_the_due_sweep_does(
        self,
    ) -> None:
        """The starting-row fast path is excluded; the same wiring fires on a due sweep."""
        catalog = TerminalCatalog(self._root / "starting.json")
        catalog.upsert(replace(_row("boot"), control_state="starting"))
        catalog.upsert(_terminated_leaf_row("seat-a"))
        clock = _Clock(NOW)
        offered: list[list[str]] = []

        def register(rows: tuple[TerminalCatalogEntry, ...]) -> frozenset[str]:
            offered.append([row.id for row in rows])
            return frozenset({"seat-a"})

        proofs: list[frozenset[str]] = []
        real_compact = catalog.compact

        def compact(*, now: datetime, registered_execution_ids: frozenset[str]) -> int:
            proofs.append(registered_execution_ids)
            return real_compact(now=now, registered_execution_ids=registered_execution_ids)

        sweeper = self._sweeper(
            catalog,
            clock=clock,
            actions=TerminalLivenessActions(register_execution_evidence=register),
        )
        # Enter the rate-limited branch without waiting for wall-clock time: the same
        # in-memory cadence seam the existing starting-row case uses.
        sweeper._last_sweep_at = clock.moment
        with mock.patch.object(catalog, "compact", side_effect=compact):
            sweeper.refresh()

            self.assertEqual(offered, [])
            self.assertEqual(proofs, [])
            boot = catalog.get("boot")
            self.assertIsNotNone(boot)
            assert boot is not None
            self.assertEqual(boot.control_state, "ready")

            clock.moment = clock.moment + timedelta(seconds=11)
            sweeper.refresh()

        self.assertEqual(offered, [["seat-a"]])
        self.assertEqual(proofs, [frozenset({"seat-a"})])


if __name__ == "__main__":
    unittest.main()
