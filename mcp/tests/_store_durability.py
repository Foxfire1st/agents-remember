"""Cross-process durability harness for the JSONL record stores (260731-EFA-L5 R10).

Six of them live under ``agents_remember/controlplane``; two more -- ``ProviderMetricsStore`` and
``ProviderDegradationStore`` -- live under ``agents_remember/providers`` and have the identical
shape, which is why they are measured by the identical instrument rather than by a second one.

Every one of these stores is an append-only JSONL log that is also
rewritten in place by a reclaim pass (``compact`` / ``prune_lifecycles`` / ``replace_records``).
The rewrite reads the whole file, filters it, writes a temp and ``os.replace``s it; an append that
lands between the read and the replace is silently discarded, and when the filtered set comes out
empty the rewrite ``unlink``s the log, so an appender still holding an ``"a"``-mode handle writes
into an unlinked inode. Neither loss raises and neither is logged.

This module is the *measurement instrument*, deliberately separate from the assertions:

* imported by ``test_controlplane_store_durability.py`` and ``test_provider_store_durability.py``
  (the pytest contract tests), and
* executed as a script with a JSON config file, which is how one run is pinned to exactly ONE
  source tree. The caller sets ``PYTHONPATH`` to the ``mcp/src`` it wants measured -- the live
  worktree for the contract assertion, a ``git archive`` of the leaf's base commit for the
  reproducible baseline -- and :func:`_require_source_root` refuses to run if the interpreter
  resolved ``agents_remember`` anywhere else. A measurement that cannot name the tree it measured
  is worthless, so that check is fatal, never a warning.

Real processes throughout (``multiprocessing`` with the ``fork`` context): the defect is
cross-process, and threads would let the GIL serialise the very window under test.

Two properties of a *measurement* are enforced here rather than left to the callers, because a
caller that gets either wrong reports a number that looks like the real one:
:func:`harness_work_dir` keys this run's scratch space off ``root`` so two cases can never share a
stop flag, and :func:`require_stress_measurement` refuses incomplete worker or reclaimer work.
"""

from __future__ import annotations

import contextlib
import json
import multiprocessing
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import agents_remember
from _durability_measurement import (
    MIN_SUCCESSFUL_RECLAIMS,
    VacuousRunError,
    require_stress_measurement,
)
from _store_durability_source import extract_base_commit_tree, run_against_source
from agents_remember.controlplane.agent_notifier_signals import (
    AgentNotifierSignalCooldownStore,
    AgentNotifierSignalRecord,
)
from agents_remember.controlplane.attention_dismissals import (
    AttentionDismissalRecord,
    AttentionDismissalStore,
)
from agents_remember.controlplane.expectation_rows import ExpectationRow, ExpectationRowStore
from agents_remember.controlplane.operator_inbox_records import (
    OperatorInboxEntry,
    OperatorInboxState,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.orchestration_nudges import (
    OrchestrationNudgeRecord,
    OrchestrationNudgeStore,
    replace_records,
)
from agents_remember.controlplane.records import GateRecord, GateState
from agents_remember.controlplane.store import GateStore
from agents_remember.providers.degradation import (
    DEGRADATION_EVENT_SCHEMA,
    ProviderDegradationStore,
)
from agents_remember.providers.metrics import (
    PROVIDER_METRICS_SCHEMA,
    ContainerSample,
    MetricsSnapshot,
    ProviderMetricsStore,
)

__all__ = [
    "MIN_SUCCESSFUL_RECLAIMS",
    "VacuousRunError",
    "extract_base_commit_tree",
    "run_against_source",
    "run_case",
]

# Survivors are what the harness accounts for. The anchor is a record that is never prunable and
# never counted: it keeps the reclaimed set non-empty so a reclaim pass exercises the tmp +
# ``os.replace`` rewrite rather than the ``unlink`` branch (which the unlink scenario tests
# separately, and which would otherwise mask a lost update as a vanished file).
SURVIVOR_PREFIX = "survivor-"
ANCHOR_ID = "anchor-keepalive"
DECOY_PREFIX = "decoy-"


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


# --------------------------------------------------------------------------------------------
# Per-store adapters
# --------------------------------------------------------------------------------------------


class StoreAdapter:
    """One control-plane store expressed as the four operations the harness needs.

    ``write`` is whatever that store calls "record this fact" (an ``append``, or -- for the
    attention store, which has no append at all -- a read-modify-write ``dismiss``); ``reclaim``
    is that store's own real reclaim entry point, never a reimplementation of it, so the harness
    measures shipped behaviour and not a model of it.
    """

    name = ""
    log_name = ""
    id_field = "id"
    # Whether this store's own ``read`` raises on a malformed line or skips it (L5-R8). Asserted
    # against shipped behaviour by the torn-line tests; recorded here so the two cannot drift.
    torn_line_policy = ""
    # Stores whose ``write`` is a plain ``open(path, "a")`` append can lose a record into an
    # unlinked inode; a read-modify-write store cannot (its ``os.replace`` recreates the file).
    appends_in_place = True

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:128).
    def open(self, root: Path) -> Any:  # pragma: no cover
        raise NotImplementedError

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:131).
    def write(self, store: Any, record_id: str) -> None:  # pragma: no cover
        raise NotImplementedError

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:134).
    def write_decoy(self, store: Any, tick: int) -> None:  # pragma: no cover
        raise NotImplementedError

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:137).
    def reclaim_now(self, store: Any) -> None:  # pragma: no cover
        raise NotImplementedError

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:140).
    def read(self, store: Any) -> list[Any]:  # pragma: no cover
        raise NotImplementedError

    def log_path(self, root: Path) -> Path:
        return root / "workspace" / self.log_name

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:146).
    def reclaim(self, store: Any, tick: int) -> None:  # pragma: no cover
        """One reclaim tick: produce a prunable row, then run the store's real reclaim.

        The decoy is what makes the tick *rewrite*. Every one of these stores skips the rewrite
        when nothing is prunable (``if len(kept) == len(records): return 0``), so a reclaim pass
        with nothing to drop would never open the window this harness measures.
        """
        self.write_decoy(store, tick)
        self.reclaim_now(store)

    def seed(self, root: Path, *, anchor: bool) -> None:
        store = self.open(root)
        if anchor:
            self.write(store, ANCHOR_ID)
        self.write_decoy(store, -1)


class GateAdapter(StoreAdapter):
    name = "gate"
    log_name = "gates.jsonl"
    torn_line_policy = "strict"

    def open(self, root: Path) -> Any:
        return GateStore(root)

    def _record(self, record_id: str, state: GateState) -> GateRecord:
        return GateRecord(id=record_id, ts=_iso(_now()), kind="closeout-approval", state=state)

    def write(self, store: Any, record_id: str) -> None:
        store.append(self._record(record_id, "approved"))

    def write_decoy(self, store: Any, tick: int) -> None:
        # ``expired`` is in PRUNE_IMMEDIATE_GATE_STATES, so the next compact drops it.
        # NOT ``applied``: an applied snapshot of a consumed-approval kind is retained forever
        # (260731-EFA-L5 R1 -- it is the record that stops one approval being spent twice), so a
        # decoy in that state would leave nothing prunable and the reclaim tick would never
        # rewrite, quietly turning this whole harness into a no-op.
        store.append(self._record(f"{DECOY_PREFIX}{os.getpid()}-{tick}", "expired"))

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:185).
    def reclaim_now(self, store: Any) -> None:  # pragma: no cover
        store.compact(None, now=_now())

    def read(self, store: Any) -> list[Any]:
        return store.read(None)


class ExpectationAdapter(StoreAdapter):
    name = "expectation"
    log_name = "expectation-rows.jsonl"
    torn_line_policy = "strict"
    RETAIN_SECONDS = 60.0

    def open(self, root: Path) -> Any:
        return ExpectationRowStore(root)

    def write(self, store: Any, record_id: str) -> None:
        stamp = _iso(_now())
        store.append(
            ExpectationRow(
                id=record_id,
                ts=stamp,
                kind="ack-by",
                state="pending",
                createdAt=stamp,
                dueAt=stamp,
                sourceId="durability-harness",
            )
        )

    def write_decoy(self, store: Any, tick: int) -> None:
        # A terminal row whose terminal stamp is older than the retention window: prunable.
        stale = _iso(_now() - timedelta(seconds=self.RETAIN_SECONDS * 60))
        store.append(
            ExpectationRow(
                id=f"{DECOY_PREFIX}{os.getpid()}-{tick}",
                ts=stale,
                kind="ack-by",
                state="met",
                createdAt=stale,
                dueAt=stale,
                sourceId="durability-harness",
                metAt=stale,
            )
        )

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:231).
    def reclaim_now(self, store: Any) -> None:  # pragma: no cover
        store.compact(now=_now(), retain_seconds=self.RETAIN_SECONDS)

    def read(self, store: Any) -> list[Any]:
        return store.read()


class AttentionAdapter(StoreAdapter):
    name = "attention"
    log_name = "attention-dismissals.jsonl"
    id_field = "itemId"
    torn_line_policy = "tolerant"
    # ``dismiss`` is a whole-file read-modify-write, not an append -- there is no ``"a"`` handle
    # to strand in an unlinked inode, so only the lost-update mechanism applies here.
    appends_in_place = False
    LIVE_LIFECYCLE = "live-lifecycle"

    def open(self, root: Path) -> Any:
        return AttentionDismissalStore(root)

    def write(self, store: Any, record_id: str) -> None:
        store.dismiss(
            AttentionDismissalRecord(
                itemId=record_id,
                dismissedAt=_iso(_now()),
                kind="gate-open",
                lifecycleId=self.LIVE_LIFECYCLE,
            )
        )

    def write_decoy(self, store: Any, tick: int) -> None:
        # A dismissal for a lifecycle that is not in the live set: dropped by prune_lifecycles.
        store.dismiss(
            AttentionDismissalRecord(
                itemId=f"{DECOY_PREFIX}{os.getpid()}-{tick}",
                dismissedAt=_iso(_now()),
                kind="gate-open",
                lifecycleId="retired-lifecycle",
            )
        )

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:272).
    def reclaim_now(self, store: Any) -> None:  # pragma: no cover
        store.prune_lifecycles({self.LIVE_LIFECYCLE})

    def read(self, store: Any) -> list[Any]:
        return store.read()


class OperatorInboxAdapter(StoreAdapter):
    name = "operator_inbox"
    log_name = "operator-inbox.jsonl"
    torn_line_policy = "strict"

    def open(self, root: Path) -> Any:
        return OperatorInboxStore(root)

    def _entry(self, record_id: str, state: OperatorInboxState) -> OperatorInboxEntry:
        stamp = _iso(_now())
        return OperatorInboxEntry(
            id=record_id,
            ts=stamp,
            state=state,
            lifecycleId="live-lifecycle",
            ask="durability harness",
            response="durability harness",
            createdAt=stamp,
            createdBy="durability-harness",
            createdVia="cli",
        )

    def write(self, store: Any, record_id: str) -> None:
        store.append(self._entry(record_id, "pending"))

    def write_decoy(self, store: Any, tick: int) -> None:
        # ``ladder-resolved`` rows drop immediately in ``_keep_inbox_entry``.
        store.append(self._entry(f"{DECOY_PREFIX}{os.getpid()}-{tick}", "ladder-resolved"))

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:308).
    def reclaim_now(self, store: Any) -> None:  # pragma: no cover
        store.compact(now=_now())

    def read(self, store: Any) -> list[Any]:
        return store.read()


class NudgeAdapter(StoreAdapter):
    name = "nudge"
    log_name = "orchestration-nudges.jsonl"
    torn_line_policy = "tolerant"

    def open(self, root: Path) -> Any:
        return OrchestrationNudgeStore(root)

    def _record(self, record_id: str) -> OrchestrationNudgeRecord:
        return OrchestrationNudgeRecord(
            id=record_id,
            ts=_iso(_now()),
            state="sent",
            reason="manual",
            message="durability harness",
        )

    def write(self, store: Any, record_id: str) -> None:
        store.append(self._record(record_id))

    def write_decoy(self, store: Any, tick: int) -> None:
        store.append(self._record(f"{DECOY_PREFIX}{os.getpid()}-{tick}"))

    @staticmethod
    @contextlib.contextmanager
    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:340).
    def _reclaim_lock(path: Path) -> Any:  # pragma: no cover
        """Hold the nudge log's lock across read + filter + rewrite, if the tree has one.

        This store is the only one of the six with no ``compact`` method: ``replace_records`` is
        its documented rewrite entry point, so the read-filter half of a reclaim belongs to the
        caller -- and a caller that reads outside the lock reintroduces the exact lost update the
        lock exists to prevent. The harness therefore reclaims the way a correct compaction owner
        would, so a failure here is the store's and not the harness's.
        """
        try:
            # Deliberately local: this module also runs against a `git archive` of the leaf's
            # base commit, where `durable_store` does not exist yet. A top-level import would
            # make the harness unable to measure the very tree it exists to measure.
            from agents_remember.controlplane.durable_store import (  # noqa: PLC0415
                ORCHESTRATION_NUDGE_OWNERSHIP,
                exclusive_access,
            )
        except ImportError:
            yield  # base commit: there is no lock to hold
            return
        with exclusive_access(path, ORCHESTRATION_NUDGE_OWNERSHIP):
            yield

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:363).
    def reclaim_now(self, store: Any) -> None:  # pragma: no cover
        path = store.log_path()
        with self._reclaim_lock(path):
            kept = [record for record in store.read() if not record.id.startswith(DECOY_PREFIX)]
            replace_records(path, kept)

    def read(self, store: Any) -> list[Any]:
        return store.read()


class AgentNotifierSignalAdapter(StoreAdapter):
    name = "agent_notifier_signal"
    log_name = "supervisor-signals.jsonl"
    torn_line_policy = "tolerant"
    RETAIN_SECONDS = 300.0

    def open(self, root: Path) -> Any:
        return AgentNotifierSignalCooldownStore(root)

    def _record(self, record_id: str, stamp: str) -> AgentNotifierSignalRecord:
        return AgentNotifierSignalRecord(
            id=record_id,
            ts=stamp,
            state="sent",
            findingKind="durability-harness",
            detail="durability harness",
            deliveryState="queued",
        )

    def write(self, store: Any, record_id: str) -> None:
        store.append(self._record(record_id, _iso(_now())))

    def write_decoy(self, store: Any, tick: int) -> None:
        stale = _iso(_now() - timedelta(seconds=self.RETAIN_SECONDS * 60))
        store.append(self._record(f"{DECOY_PREFIX}{os.getpid()}-{tick}", stale))

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:399).
    def reclaim_now(self, store: Any) -> None:  # pragma: no cover
        store.compact(now=_now(), retain_seconds=self.RETAIN_SECONDS)

    def read(self, store: Any) -> list[Any]:
        return store.read()


class ProviderStoreAdapter(StoreAdapter):
    """The two ``providers/`` logs, which sit under a coordination root rather than an observer one.

    ``StoreAdapter.log_path`` resolves ``<root>/workspace/<log>`` because that is where the six
    control-plane logs live. Both provider stores build their own directory from a COORDINATION
    root instead (``<root>/logs/observer/providers``), so the harness root means the same thing to
    them -- a throwaway tree -- and only the path under it differs.
    """

    def log_path(self, root: Path) -> Path:
        return root / "logs" / "observer" / "providers" / self.log_name


class ProviderMetricsAdapter(ProviderStoreAdapter):
    """``providers/metrics.py`` -- the store the leaf's first pass left outside ``controlplane/``.

    The pairing measured here is the shipped one, not a constructed one. ``write`` is
    ``record_index_state``, which the MCP process appends through on its provider-setup thread
    (``providers/provider_setup.py`` ``_record_index_state``, reached from ``worktree_start`` /
    ``runtime_install``); ``reclaim`` is the dashboard's ``_metrics_loop`` in the order that loop
    runs it -- ``record`` and then ``compact`` (``serving/app.py``).

    ``max_bytes=0`` holds the byte-budget guard open so every tick really rewrites, and
    ``retain_rows`` is set far above anything a run can produce so RETENTION never drops a
    survivor. A missing record is then a lost update and cannot be a truncation.
    """

    name = "provider_metrics"
    log_name = "metrics.jsonl"
    torn_line_policy = "tolerant"
    RETAIN_ROWS = 1_000_000

    def open(self, root: Path) -> Any:
        return ProviderMetricsStore(root)

    def write(self, store: Any, record_id: str) -> None:
        store.record_index_state({"id": record_id, "provider": "durability-harness"})

    def write_decoy(self, store: Any, tick: int) -> None:
        store.record(
            MetricsSnapshot(
                schema=PROVIDER_METRICS_SCHEMA,
                sampledAt=_iso(_now()),
                containers=[
                    ContainerSample(
                        name=f"{DECOY_PREFIX}{os.getpid()}-{tick}",
                        provider="durability-harness",
                        instance="harness",
                        running=True,
                        restarts=0,
                    )
                ],
            )
        )

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:461).
    def reclaim_now(self, store: Any) -> None:  # pragma: no cover
        store.compact(retain_rows=self.RETAIN_ROWS, max_bytes=0)

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:464).
    def read(self, store: Any) -> list[Any]:  # pragma: no cover
        return store.read_recent(limit=self.RETAIN_ROWS)


class ProviderDegradationAdapter(ProviderStoreAdapter):
    """``providers/degradation.py`` -- the provider degradation audit log.

    ``compact_events`` reclaims by ROW COUNT and skips the rewrite entirely while the log is
    under ``retain_rows``, so the seed pre-fills the log to exactly that many rows: from the first
    tick onward every reclaim rewrites, and the newest ``retain_rows`` always covers every record
    the run appends, so retention can never be mistaken for loss.

    This is also why the tick writes NO decoy (:meth:`reclaim`). The other seven adapters need one
    because their reclaim drops rows by CONTENT and would otherwise no-op; here the seeded backlog
    is what makes the tick rewrite, and a per-tick decoy would only compete with the survivors for
    the retention window -- turning a legitimate truncation into a reported loss.
    """

    name = "provider_degradation"
    log_name = "degradation-events.jsonl"
    torn_line_policy = "tolerant"
    RETAIN_ROWS = 1_500

    def open(self, root: Path) -> Any:
        return ProviderDegradationStore(root)

    def _event(self, record_id: str) -> dict[str, Any]:
        return {
            "schema": DEGRADATION_EVENT_SCHEMA,
            "id": record_id,
            "at": _iso(_now()),
            "from": "healthy",
            "to": "degraded",
        }

    def write(self, store: Any, record_id: str) -> None:
        store.append_event(self._event(record_id))

    def write_decoy(self, store: Any, tick: int) -> None:
        store.append_event(self._event(f"{DECOY_PREFIX}{os.getpid()}-{tick}"))

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:505).
    def reclaim_now(self, store: Any) -> None:  # pragma: no cover
        store.compact_events(retain_rows=self.RETAIN_ROWS)

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:508).
    def reclaim(self, store: Any, tick: int) -> None:  # pragma: no cover
        del tick  # count-based reclaim: the seeded backlog, not a decoy, is what makes it rewrite
        self.reclaim_now(store)

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:512).
    def read(self, store: Any) -> list[Any]:  # pragma: no cover
        return [
            json.loads(line)
            for line in _read_lines(store.events_path)
            if line.lstrip().startswith("{")
        ]

    def seed(self, root: Path, *, anchor: bool) -> None:
        # Bulk-written rather than appended one call at a time: the seed is setup, not
        # measurement, and 2,000 locked-and-fsynced appends would cost more than the run.
        path = self.log_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for index in range(self.RETAIN_ROWS):
                handle.write(json.dumps(self._event(f"seed-{index}"), sort_keys=True) + "\n")
        super().seed(root, anchor=anchor)


CONTROLPLANE_ADAPTERS: tuple[type[StoreAdapter], ...] = (
    GateAdapter,
    ExpectationAdapter,
    AttentionAdapter,
    OperatorInboxAdapter,
    NudgeAdapter,
    AgentNotifierSignalAdapter,
)
PROVIDER_ADAPTERS: tuple[type[StoreAdapter], ...] = (
    ProviderMetricsAdapter,
    ProviderDegradationAdapter,
)
ADAPTERS: dict[str, type[StoreAdapter]] = {
    adapter.name: adapter for adapter in (*CONTROLPLANE_ADAPTERS, *PROVIDER_ADAPTERS)
}
# ``CASES`` stays the six control-plane stores and ``PROVIDER_CASES`` is separate, so adding the
# provider stores to the shared instrument does not silently widen what the control-plane
# contract test asserts -- each test file names the stores it speaks for.
CASES: tuple[str, ...] = tuple(adapter.name for adapter in CONTROLPLANE_ADAPTERS)
PROVIDER_CASES: tuple[str, ...] = tuple(adapter.name for adapter in PROVIDER_ADAPTERS)
APPEND_CASES: tuple[str, ...] = tuple(name for name in CASES if ADAPTERS[name].appends_in_place)


# --------------------------------------------------------------------------------------------
# Raw on-disk accounting (independent of any store's own read policy)
# --------------------------------------------------------------------------------------------


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:558).
def surviving_ids(path: Path, id_field: str) -> tuple[set[str], int]:  # pragma: no cover
    """Survivor ids physically present in the log, plus a count of unparseable lines.

    Deliberately NOT the store's own ``read``: a store that raises on a torn line would turn a
    durability measurement into an exception, and a store that skips one would let a torn line be
    reported as a lost record. Loss and tearing are different defects and are counted separately.
    """
    if not path.exists():
        return set(), 0
    found: set[str] = set()
    torn = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            torn += 1
            continue
        if not isinstance(payload, dict):
            torn += 1
            continue
        record_id = payload.get(id_field)
        if isinstance(record_id, str) and record_id.startswith(SURVIVOR_PREFIX):
            found.add(record_id)
    return found, torn


# --------------------------------------------------------------------------------------------
# Child process entry points
# --------------------------------------------------------------------------------------------


# 260731-EFA-L7 R10: durability measurement helper; exercises only under AR_RUN_*_DURABILITY gates.
def _appender_main(spec: dict[str, Any]) -> None:  # pragma: no cover
    """Append survivors, journalling attempts before calls and receipts after returns.

    Each line is flushed at its respective point so the two journals preserve the attempted and
    completed sequence. Both journal file descriptors are fsynced once after the bounded loop.
    A receipt is the appender's own claim "this record is written"; anything on that list and not
    on disk afterwards is a record the store accepted and then lost.
    """
    adapter = ADAPTERS[spec["case"]]()
    store = adapter.open(Path(spec["root"]))
    errors: list[str] = []
    pace = spec.get("append_sleep", 0.0)
    with (
        Path(spec["attempts"]).open("w", encoding="utf-8") as attempts,
        Path(spec["receipt"]).open("w", encoding="utf-8") as receipts,
    ):
        for index in range(spec["count"]):
            record_id = f"{SURVIVOR_PREFIX}{spec['worker']}-{index}"
            attempts.write(record_id + "\n")
            attempts.flush()
            try:
                adapter.write(store, record_id)
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                if pace:
                    time.sleep(pace)
                continue
            receipts.write(record_id + "\n")
            receipts.flush()
            if pace:
                time.sleep(pace)
        os.fsync(attempts.fileno())
        os.fsync(receipts.fileno())
    Path(spec["errors"]).write_text("\n".join(errors), encoding="utf-8")


# 260731-EFA-L7 R10: durability measurement helper; exercises only under AR_RUN_*_DURABILITY gates.
def _reclaimer_main(spec: dict[str, Any]) -> None:  # pragma: no cover
    """Attempt compactions until the appenders finish or the attempt budget runs out.

    A reclaim that raises (a log unlinked mid-read, a torn line under a strict reader) is recorded
    but never counted as successful semantic work. The loop continues so the instrument can report
    every failure and then refuse the entire result rather than silently narrowing the window.
    """
    adapter = ADAPTERS[spec["case"]]()
    store = adapter.open(Path(spec["root"]))
    stop = Path(spec["stop"])
    errors: list[str] = []
    attempts = 0
    successes = 0
    for tick in range(spec["max_ticks"]):
        try:
            adapter.reclaim(store, tick)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        else:
            successes += 1
        attempts += 1
        if stop.exists():
            break
        time.sleep(spec["sleep"])
    Path(spec["errors"]).write_text("\n".join(errors), encoding="utf-8")
    Path(spec["attempts"]).write_text(str(attempts), encoding="utf-8")
    Path(spec["successes"]).write_text(str(successes), encoding="utf-8")


@contextlib.contextmanager
# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:659).
def parked_rewrite(ready: Any, released: Any, seconds: float) -> Any:  # pragma: no cover
    """Park the next in-process log rewrite between its read and its commit.

    Two interposition points, whichever the implementation reaches first: ``Path.write_text``
    (every one of these stores materialises its temp file that way) and ``os.replace`` (the last
    instruction of every rewrite). Hooking both means the pause lands in the read->commit window
    even if the rewrite above it is restructured -- e.g. a fix that swaps ``write_text`` for an
    explicit handle it can ``fsync`` still commits through ``os.replace``.

    Pausing at ``write_text`` rather than only at ``os.replace`` also matters for correctness of
    the *measurement*: these stores name their temp file after the log with no pid in it, so two
    processes parked at ``os.replace`` collide on one temp path and the scenario degenerates into
    a ``FileNotFoundError`` instead of the silent lost update it is meant to expose.

    ``ready`` fires when the rewrite is parked; the pause then lasts until ``released`` is set or
    ``seconds`` elapse. The timeout is what keeps this terminating: an implementation that
    serialises the other process out (the fix) never sets ``released``, so the rewrite resumes on
    its own instead of deadlocking.
    """
    real_write_text = Path.write_text
    real_replace = os.replace
    armed = [True]

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:682).
    def park() -> None:  # pragma: no cover
        if armed[0]:
            armed[0] = False
            ready.set()
            released.wait(seconds)

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:688).
    def hooked_write_text(self: Path, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        park()
        return real_write_text(self, *args, **kwargs)

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:692).
    def hooked_replace(src: Any, dst: Any) -> None:  # pragma: no cover
        park()
        real_replace(src, dst)

    Path.write_text = hooked_write_text  # type: ignore[assignment]
    os.replace = hooked_replace  # type: ignore[assignment]
    try:
        yield
    finally:
        Path.write_text = real_write_text  # type: ignore[assignment]
        os.replace = real_replace  # type: ignore[assignment]
        ready.set()


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:706).
def _forced_reclaimer_main(
    spec: dict[str, Any], ready: Any, appended: Any
) -> None:  # pragma: no cover
    """Reclaim once, parked inside the rewrite so an append can interleave.

    The decoy is written BEFORE arming, because on a read-modify-write store (attention
    dismissals) writing the decoy is itself a rewrite and would spring the hook early.
    """
    adapter = ADAPTERS[spec["case"]]()
    store = adapter.open(Path(spec["root"]))
    adapter.write_decoy(store, 0)
    try:
        with parked_rewrite(ready, appended, spec["handoff_seconds"]):
            adapter.reclaim_now(store)
    except Exception as exc:
        Path(spec["errors"]).write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:722).
def _forced_appender_main(
    spec: dict[str, Any], ready: Any, appended: Any
) -> None:  # pragma: no cover
    """Append one survivor once the reclaimer is parked mid-rewrite."""
    adapter = ADAPTERS[spec["case"]]()
    store = adapter.open(Path(spec["root"]))
    ready.wait(spec["handoff_seconds"] * 3)
    try:
        adapter.write(store, spec["record_id"])
        Path(spec["receipt"]).write_text(spec["record_id"], encoding="utf-8")
    except Exception as exc:
        Path(spec["errors"]).write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")
    finally:
        appended.set()


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:736).
def _unlink_appender_main(
    spec: dict[str, Any], opened: Any, unlinked: Any
) -> None:  # pragma: no cover
    """Hold an open ``"a"`` handle on the log across the reclaimer's ``unlink``.

    Interposed at ``Path.open`` rather than inside the store, so the scenario measures whatever
    ``append`` currently does. Bounded wait: an implementation that makes the reclaimer wait for
    this appender (the fix) times out here and completes rather than deadlocking.
    """
    adapter = ADAPTERS[spec["case"]]()
    root = Path(spec["root"])
    store = adapter.open(root)
    target = str(adapter.log_path(root))
    real_open = Path.open
    armed = [True]

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:750).
    def hooked(self: Path, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        handle = real_open(self, *args, **kwargs)
        mode = args[0] if args else kwargs.get("mode", "r")
        if armed[0] and str(self) == target and "a" in str(mode):
            armed[0] = False
            opened.set()
            unlinked.wait(spec["handoff_seconds"])
        return handle

    Path.open = hooked  # type: ignore[assignment]
    try:
        adapter.write(store, spec["record_id"])
        Path(spec["receipt"]).write_text(spec["record_id"], encoding="utf-8")
    except Exception as exc:
        Path(spec["errors"]).write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")
    finally:
        Path.open = real_open  # type: ignore[assignment]
        opened.set()


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:770).
def _unlink_reclaimer_main(
    spec: dict[str, Any], opened: Any, unlinked: Any
) -> None:  # pragma: no cover
    """Reclaim the log down to empty (which ``unlink``s it) while the appender holds its handle."""
    adapter = ADAPTERS[spec["case"]]()
    store = adapter.open(Path(spec["root"]))
    opened.wait(spec["handoff_seconds"] * 3)
    try:
        adapter.reclaim_now(store)
    except Exception as exc:
        Path(spec["errors"]).write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")
    finally:
        unlinked.set()


# --------------------------------------------------------------------------------------------
# Scenarios (parent side)
# --------------------------------------------------------------------------------------------


def _context() -> Any:
    return multiprocessing.get_context("fork")


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:792).
def _join(processes: list[Any], timeout: float) -> list[str]:  # pragma: no cover
    deadline = time.monotonic() + timeout
    stragglers: list[str] = []
    for process in processes:
        process.join(max(0.0, deadline - time.monotonic()))
        if process.is_alive():
            stragglers.append(process.name)
            process.kill()
            process.join(5.0)
    return stragglers


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def harness_work_dir(root: Path) -> Path:
    """Where one run keeps its stop flag, receipts and error files: a sibling named after ``root``.

    Derived from ``root`` itself rather than from ``root.parent``, and that is the whole point.
    ``root.parent / "harness"`` made this instrument correct only while every caller happened to
    choose a distinct parent -- a footgun that fired. Every ``run_case`` in
    ``test_controlplane_store_durability.py`` passes a sibling root under one ``self.tmp``, so all
    six cases shared one stop flag: the first case to finish set it, and every case after it saw it
    already set and left the tick loop after ONE tick. Measured on this tree with the shared
    parent, all eight stores reported 0.00% loss -- the first stress case over 25 reclaim ticks and
    the other seven over exactly 1 apiece. The same layout also let one case's ``forced.id`` receipt
    and ``*.err`` files stand in for the next case's, so a run that wrote nothing was scored off its
    predecessor's files.

    A SIBLING rather than a directory inside ``root``, deliberately:

    * ``root`` is the tree under measurement and the accounting reads it as raw bytes
      (:func:`surviving_ids`, ``ProviderDegradationAdapter.read``). Nothing the harness writes for
      its own bookkeeping belongs inside the thing being weighed.
    * ``root`` does not name one place. The six control-plane adapters resolve their log under
      ``root/workspace``, the two provider adapters under ``root/logs/observer/providers``, and
      ``GateStore`` walks ``root/lifecycles`` besides (``find``, ``lifecycle_ids``). "Inside
      ``root``" is a different neighbourhood per store, each of them a directory some store already
      owns or scans; the sibling is one rule that holds for all eight.
    * A path has exactly one name, so this is unique whenever ``root`` is -- and two cases sharing
      a ``root`` would collide on the LOG itself, which is a collision no caller can overlook.
    """
    return root.with_name(root.name + "-harness")


def _prepared_work_dir(root: Path) -> Path:
    work = harness_work_dir(root)
    work.mkdir(parents=True, exist_ok=True)
    return work


def run_stress(config: dict[str, Any]) -> dict[str, Any]:
    """N appender processes racing one reclaiming process: the natural-operation loss rate."""
    case = config["case"]
    adapter = ADAPTERS[case]()
    root = Path(config["root"])
    work = _prepared_work_dir(root)
    adapter.seed(root, anchor=True)

    stop = work / "stop"
    ctx = _context()
    reclaimer_spec = {
        "case": case,
        "root": str(root),
        "stop": str(stop),
        "max_ticks": config["max_ticks"],
        "sleep": config["reclaim_sleep"],
        "errors": str(work / "reclaimer.err"),
        "attempts": str(work / "reclaimer.attempts"),
        "successes": str(work / "reclaimer.successes"),
    }
    reclaimer = ctx.Process(target=_reclaimer_main, args=(reclaimer_spec,), name="reclaimer")
    reclaimer.start()

    appenders = []
    for worker in range(config["appenders"]):
        spec = {
            "case": case,
            "root": str(root),
            "worker": worker,
            "count": config["per_appender"],
            "append_sleep": config.get("append_sleep", 0.0),
            "attempts": str(work / f"appender-{worker}.attempts"),
            "receipt": str(work / f"appender-{worker}.ids"),
            "errors": str(work / f"appender-{worker}.err"),
        }
        process = ctx.Process(target=_appender_main, args=(spec,), name=f"appender-{worker}")
        process.start()
        appenders.append(process)

    stragglers = _join(appenders, config["timeout"])
    stop.write_text("stop", encoding="utf-8")
    stragglers += _join([reclaimer], config["timeout"])

    attempted_ids: set[str] = set()
    claimed: set[str] = set()
    append_errors: list[str] = []
    for worker in range(config["appenders"]):
        attempted_ids.update(_read_lines(work / f"appender-{worker}.attempts"))
        claimed.update(_read_lines(work / f"appender-{worker}.ids"))
        append_errors += _read_lines(work / f"appender-{worker}.err")
    present, torn = surviving_ids(adapter.log_path(root), adapter.id_field)
    lost = sorted(claimed - present)
    reclaim_errors = _read_lines(work / "reclaimer.err")
    return require_stress_measurement(
        {
            "case": case,
            "scenario": "stress",
            "requested": config["appenders"] * config["per_appender"],
            "attempted": len(attempted_ids),
            "completed": len(claimed),
            "surviving": len(claimed & present),
            "lost": len(lost),
            "lost_sample": lost[:5],
            "torn_lines": torn,
            "reclaim_attempts": int((_read_lines(work / "reclaimer.attempts") or ["0"])[0]),
            "successful_reclaims": int((_read_lines(work / "reclaimer.successes") or ["0"])[0]),
            "reclaim_error_count": len(reclaim_errors),
            "reclaim_errors": sorted({error.split(":")[0] for error in reclaim_errors}),
            "append_error_count": len(append_errors),
            "append_errors": sorted({error.split(":")[0] for error in append_errors}),
            "stragglers": stragglers,
        },
        work,
    )


def _forced_result(
    scenario: str, root: Path, adapter: StoreAdapter, stragglers: list[str]
) -> dict[str, Any]:
    case = adapter.name
    work = harness_work_dir(root)
    claimed = set(_read_lines(work / "forced.id"))
    present, torn = surviving_ids(adapter.log_path(root), adapter.id_field)
    lost = sorted(claimed - present)
    return {
        "case": case,
        "scenario": scenario,
        "attempted": len(claimed),
        "surviving": len(claimed & present),
        "lost": len(lost),
        "lost_sample": lost,
        "torn_lines": torn,
        "reclaim_errors": _read_lines(work / "forced-reclaimer.err"),
        "append_errors": _read_lines(work / "forced-appender.err"),
        "stragglers": stragglers,
    }


def run_forced_lost_update(config: dict[str, Any]) -> dict[str, Any]:
    """One append forced into the reclaim rewrite's read->replace window. Deterministic."""
    case = config["case"]
    adapter = ADAPTERS[case]()
    root = Path(config["root"])
    work = _prepared_work_dir(root)
    adapter.seed(root, anchor=True)

    ctx = _context()
    ready = ctx.Event()
    appended = ctx.Event()
    handoff = config["handoff_seconds"]
    reclaimer_spec = {
        "case": case,
        "root": str(root),
        "handoff_seconds": handoff,
        "errors": str(work / "forced-reclaimer.err"),
    }
    appender_spec = {
        "case": case,
        "root": str(root),
        "record_id": f"{SURVIVOR_PREFIX}forced",
        "handoff_seconds": handoff,
        "receipt": str(work / "forced.id"),
        "errors": str(work / "forced-appender.err"),
    }
    reclaimer = ctx.Process(
        target=_forced_reclaimer_main, args=(reclaimer_spec, ready, appended), name="reclaimer"
    )
    appender = ctx.Process(
        target=_forced_appender_main, args=(appender_spec, ready, appended), name="appender"
    )
    reclaimer.start()
    appender.start()
    stragglers = _join([reclaimer, appender], config["timeout"])
    return _forced_result("forced_lost_update", root, adapter, stragglers)


def run_forced_unlink(config: dict[str, Any]) -> dict[str, Any]:
    """One append held open across a reclaim that empties (and therefore unlinks) the log."""
    case = config["case"]
    adapter = ADAPTERS[case]()
    root = Path(config["root"])
    work = _prepared_work_dir(root)
    # No anchor: the reclaim must be able to reduce the set to empty and take the unlink branch.
    adapter.seed(root, anchor=False)

    ctx = _context()
    opened = ctx.Event()
    unlinked = ctx.Event()
    handoff = config["handoff_seconds"]
    appender_spec = {
        "case": case,
        "root": str(root),
        "record_id": f"{SURVIVOR_PREFIX}unlink",
        "handoff_seconds": handoff,
        "receipt": str(work / "forced.id"),
        "errors": str(work / "forced-appender.err"),
    }
    reclaimer_spec = {
        "case": case,
        "root": str(root),
        "handoff_seconds": handoff,
        "errors": str(work / "forced-reclaimer.err"),
    }
    appender = ctx.Process(
        target=_unlink_appender_main, args=(appender_spec, opened, unlinked), name="appender"
    )
    reclaimer = ctx.Process(
        target=_unlink_reclaimer_main, args=(reclaimer_spec, opened, unlinked), name="reclaimer"
    )
    appender.start()
    reclaimer.start()
    stragglers = _join([appender, reclaimer], config["timeout"])
    return _forced_result("forced_unlink", root, adapter, stragglers)


SCENARIOS = {
    "stress": run_stress,
    "forced_lost_update": run_forced_lost_update,
    "forced_unlink": run_forced_unlink,
}

# One profile shared by the pytest assertion and the reported baseline measurement, so the number
# in the report and the number the suite enforces can never be two different experiments.
STRESS_PROFILE: dict[str, Any] = {
    "appenders": 4,
    "per_appender": 50,
    "append_sleep": 0.002,
    "reclaim_sleep": 0.005,
    "max_ticks": 8000,
    "timeout": 120.0,
}
FORCED_PROFILE: dict[str, Any] = {"handoff_seconds": 2.0, "timeout": 60.0}


def run_case(scenario: str, case: str, root: Path, **overrides: Any) -> dict[str, Any]:
    """Run one scenario against whatever ``agents_remember`` this interpreter already imported."""
    profile = STRESS_PROFILE if scenario == "stress" else FORCED_PROFILE
    config = {**profile, **overrides, "case": case, "root": str(root)}
    return SCENARIOS[scenario](config)


# --------------------------------------------------------------------------------------------
# Script entry point
# --------------------------------------------------------------------------------------------


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:1115).
def _require_source_root(expected: str) -> str:  # pragma: no cover
    """Refuse to measure unless ``agents_remember`` resolved under the tree the caller named."""
    resolved = str(Path(agents_remember.__file__).resolve().parent.parent)
    if Path(resolved) != Path(expected).resolve():
        raise SystemExit(
            f"harness refused: expected agents_remember under {expected!r}, got {resolved!r}"
        )
    return resolved


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_store_durability.py:1125).
def main(argv: list[str]) -> int:  # pragma: no cover
    config = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    source_root = _require_source_root(config["source_root"])
    runs: list[dict[str, Any]] = []
    for run_index in range(config.get("runs", 1)):
        for case in config["cases"]:
            root = Path(config["root"]) / f"run{run_index}" / case / "observer"
            root.mkdir(parents=True, exist_ok=True)
            result = SCENARIOS[config["scenario"]]({**config, "case": case, "root": str(root)})
            result["run"] = run_index
            runs.append(result)
    payload = {"source_root": source_root, "scenario": config["scenario"], "runs": runs}
    Path(config["out"]).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry point
    sys.exit(main(sys.argv))
