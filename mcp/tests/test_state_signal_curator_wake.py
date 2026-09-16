"""Production-wiring manager wake for a curator seat's canonical terminal turn (``LOCR-R07@v1``).

A curator's durable output is its structured coherence authority, not a chat message, so the
curator must not have to hand-author a completion post for its manager to resume. These cases
therefore start where production starts -- the adapter's own evidence frames -- and let the real
``TerminalCatalogLivenessSweeper`` derive the catalog turn truth before the real agent-notifier
sweep relays it. The turn state, the terminal outcome, and the terminal evidence identity asserted
below are all produced by that sweep: no row here is ever written with a turn claim, a terminal
outcome, or an evidence identity, no terminal-session GET is issued, and no curator-authored
completion row exists in any scenario.

What is pinned: the curator seat is eligible through the same shared role predicate, and reaches
the same current-manager routing, that the worker seat uses; the durable payload carries the
curator role, the subject leaf document, the mechanical outcome, and the evidence identity; a
completed ending and an interrupted ending each produce exactly one signal and never a second one
on re-observation; a curator seat whose own master has no current manager fails closed instead of
routing to another master's manager; and the wake neither validates nor declares curator coherence
or memory readiness.
"""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from unittest import mock

from agents_remember.controlplane.agent_notifier_signals import AgentNotifierSignalCooldownStore
from agents_remember.controlplane.expectation_rows import ExpectationRowStore
from agents_remember.controlplane.operator_inbox_records import (
    OperatorInboxEntry,
    state_signal_landed,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.seats import current_seat_occupant
from agents_remember.models.conversations.control_wire import (
    AdapterSnapshot,
    ControlIdentity,
    SubmissionReceipt,
)
from agents_remember.models.conversations.evidence import EvidenceFrame, EvidencePage
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.store import EventStore
from agents_remember.serving.agent_notifier import AgentNotifierContext, run_agent_notifier_sweep
from agents_remember.serving.agent_notifier_heartbeat import AgentNotifierHeartbeatStore
from agents_remember.serving.state_signals import state_signal_ask, state_signal_response
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    TerminalCatalogEntry,
    terminal_catalog_path,
)
from agents_remember.serving.terminal_liveness import (
    DEFAULT_LIVENESS_HYSTERESIS,
    LivenessProbe,
    TerminalCatalogLivenessSweeper,
)
from agents_remember.serving.terminal_paste import PasteResult, TerminalPaster
from agents_remember.serving.terminal_tmux import TmuxProbeResult
from agents_remember.tasks import TaskDocument, write_task_doc

SWEPT_AT = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
SWEPT_AT_TEXT = SWEPT_AT.isoformat()
# One virtual sweep gap past the sweeper's own full-sweep rate limit, so the settling pass is a
# real second observation of the same seat rather than a rate-limited no-op.
SETTLE_AFTER_SECONDS = DEFAULT_LIVENESS_HYSTERESIS.sweep_interval_seconds + 20.0
MASTER = TaskDocumentRef(repository="repo-a", path="260707_master/task.json")
OTHER_MASTER = TaskDocumentRef(repository="repo-a", path="other-master/task.json")
LEAF = TaskDocumentRef(repository="repo-a", path="260707_master/leaf-9.json")
CURATOR_ID = "curator-1"
MANAGER_ID = "manager-master"
OTHER_MANAGER_ID = "manager-other-master"
#: A stale spawn-ancestry address. Routing resolves the owner from the task hierarchy, so nothing
#: may ever select this id.
STALE_SPAWNER_ID = "manager-from-spawn-ancestry"
#: The vocabulary of an acceptance verdict. The relay owns terminal truth only; the manager opens
#: and validates the canonical curator authority itself. Delivery vocabulary is deliberately
#: absent -- "accepted" is a transport fact here, not a verdict about the curator's memory.
VERDICT_VOCABULARY = ("ready", "coherence", "closeout")
_EVIDENCE_READ = "agents_remember.serving.terminal_evidence.read_control_evidence"
_SUBMIT = "agents_remember.serving.inbox_delivery.submit_control_prompt"


def _seat_entry(session_id: str, **overrides: object) -> TerminalCatalogEntry:
    """One registered seat row: identity, bridge address, and nothing about its turn."""

    fields: dict[str, object] = dict(
        id=session_id,
        label=f"Chat {session_id}",
        kind="harness",
        harness="codex",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=("codex",),
        created_at=SWEPT_AT_TEXT,
        last_attached_at=SWEPT_AT_TEXT,
        status="running",
        control_state="ready",
        control_endpoint=Path(f"/tmp/{session_id}.sock"),
    )
    fields.update(overrides)
    return TerminalCatalogEntry(**fields)  # type: ignore[arg-type]


def _adapter_snapshot(entry: TerminalCatalogEntry) -> AdapterSnapshot:
    """A settled, dispatchable bridge snapshot: the adapter reports no live turn."""

    return AdapterSnapshot(
        identity=ControlIdentity(entry.id, entry.tmux_name, entry.created_at),
        control="ready",
        activity="idle",
        acceptance="immediate",
        vendor_session_id=f"vendor-{entry.id}",
        raw={},
    )


def _turn_completed_frame(sequence: int, status: str) -> EvidenceFrame:
    """One native codex ``turn/completed`` envelope, mapped by the real vendor projector."""

    return EvidenceFrame(
        sequence=sequence,
        kind="completed",
        created_at=SWEPT_AT_TEXT,
        raw={
            "turn": {
                "id": f"turn-{sequence}",
                "status": status,
                "items": [],
                "completedAt": SWEPT_AT_TEXT,
            }
        },
    )


def _evidence_page(*frames: EvidenceFrame, latest_sequence: int = 0) -> EvidencePage:
    return EvidencePage(
        frames=frames,
        latest_sequence=max((frame.sequence for frame in frames), default=latest_sequence),
        evicted_before_sequence=0,
        truncated=False,
        bridge_epoch="epoch-1",
    )


class _CuratorEvidence:
    """The adapter's own evidence surface for one scenario.

    The first read of the curator row hands back the native terminal envelope; every later read
    hands back the consumed tail, exactly as the daemon's deque does once the cursor has advanced.
    Every other row reads an empty page, so the curator's evidence cannot leak into another seat.
    """

    def __init__(self, sequence: int, status: str) -> None:
        self.sequence = sequence
        self.status = status
        self.reads = 0

    def __call__(self, entry: TerminalCatalogEntry, after_sequence: int = 0) -> EvidencePage:
        if entry.id != CURATOR_ID:
            return _evidence_page(latest_sequence=after_sequence)
        self.reads += 1
        if self.reads == 1:
            return _evidence_page(_turn_completed_frame(self.sequence, self.status))
        return _evidence_page(latest_sequence=self.sequence)


class _AliveHost:
    """A tmux host for the registered seats; delivery submissions never reach a real process."""

    def get(self, _session_id: str) -> None:
        return None

    def has_session(self, tmux_name: str) -> bool:
        del tmux_name
        return True

    def probe_session(self, tmux_name: str) -> TmuxProbeResult:
        del tmux_name
        return TmuxProbeResult(exists=True, evidence="alive")


class _AcceptedPaster:
    def paste(
        self, _tmux_name: str, _text: str, *, submit: bool = False, **_kwargs: object
    ) -> PasteResult:
        return PasteResult(delivered=True, submitted=submit)


def _task_doc(**values: object) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": values.pop("id"),
            "slug": values.pop("slug"),
            "title": values.pop("title"),
            "kind": values.pop("kind"),
            "repo": "repo-a",
            "createdAt": "2026-07-13T00:00",
            **values,
        }
    )


def _write_task_topology(coordination_root: Path) -> None:
    """The production task-document tree: one sprint, the curator's master, and a second master."""

    root = coordination_root / "tasks" / "repo-a"
    write_task_doc(
        root / "sprint",
        _task_doc(
            id="SPRINT",
            slug="sprint",
            title="Sprint",
            kind="master",
            orchestrates=["260707_master", "other-master"],
        ),
    )
    for directory, task_id, leaves in (
        ("260707_master", "MASTER", ("leaf-9", "leaf-rebound")),
        ("other-master", "OTHER", ("leaf",)),
    ):
        write_task_doc(
            root / directory,
            _task_doc(
                id=task_id,
                slug=directory,
                title=directory,
                kind="master",
                subTasks=[
                    {"number": leaf, "name": leaf, "file": f"{leaf}.md", "status": "inProgress"}
                    for leaf in leaves
                ],
            ),
        )
        for leaf in leaves:
            write_task_doc(
                root / directory,
                _task_doc(id=leaf, slug=leaf, title=leaf, kind="subTask", master="task.md"),
            )


class _CuratorRelayWorld:
    """One disposable production wiring: task tree, catalog, bridges, inbox, and the two sweeps."""

    def __init__(self, root: Path, *, with_master_manager: bool = True) -> None:
        self.root = root
        self.coordination_root = root / "ar-coordination"
        _write_task_topology(self.coordination_root)
        self.observer_root = self.coordination_root / "logs" / "observer"
        self.catalog = TerminalCatalog(terminal_catalog_path(self.coordination_root))
        self.inbox = OperatorInboxStore(self.observer_root)
        self.clock = SWEPT_AT
        self.host = _AliveHost()
        self.sweeper = TerminalCatalogLivenessSweeper(
            self.catalog,
            self.host,
            now=lambda: self.clock,
            probe=LivenessProbe(
                pane_capturer=lambda _tmux_name: "", snapshot_reader=_adapter_snapshot
            ),
        )
        self.evidence = _CuratorEvidence(sequence=41, status="completed")
        if with_master_manager:
            self._register(MANAGER_ID, MASTER, "manager")
        self._register(OTHER_MANAGER_ID, OTHER_MASTER, "manager")
        self._register(CURATOR_ID, LEAF, "curator", spawned_by_session=STALE_SPAWNER_ID)

    def _register(
        self,
        session_id: str,
        document: TaskDocumentRef,
        role: str,
        *,
        spawned_by_session: str | None = None,
    ) -> None:
        self.catalog.upsert(
            _seat_entry(
                session_id,
                task_document_ref=document,
                spawn_role=role,
                seat_role=role,
                spawned_by_session=spawned_by_session,
            )
        )

    def observe_adapter_evidence(self) -> None:
        """First observation: the sweep lifts the native terminal envelope onto the seat row."""

        with mock.patch(_EVIDENCE_READ, side_effect=self.evidence):
            self.sweeper.refresh()

    def settle(self) -> None:
        """Second observation past the sweep rate limit: the settled turn stamps ``turn-ended``."""

        self.clock = SWEPT_AT + timedelta(seconds=SETTLE_AFTER_SECONDS)
        with mock.patch(_EVIDENCE_READ, side_effect=self.evidence):
            self.sweeper.refresh()

    def curator(self) -> TerminalCatalogEntry:
        entry = self.catalog.get(CURATOR_ID)
        assert entry is not None
        return entry

    def manager_for(self, document: TaskDocumentRef) -> TerminalCatalogEntry | None:
        return current_seat_occupant(self.catalog.list(), document=document, role="manager")

    def coordination_root_entries(self) -> list[str]:
        """Every name at the coordination root, read the same way before and after a sweep.

        One reader for both the pre-sweep premise and the post-sweep claim, so "the relay wrote no
        governed artifact of its own" is a statement about the same population on both sides.
        """

        return sorted(path.name for path in self.coordination_root.iterdir())

    def addressed_to(self, session_id: str) -> list[tuple[str, str | None, str | None]]:
        """Every row addressed to, delivered to, or itself a state signal -- one reachable read.

        The three filter keys differ from nothing and the comparison target is a literal empty
        list, so no extra or missing key can satisfy it: any row that is a state signal anywhere,
        or that names ``session_id`` as its owner or its delivery target, makes it non-empty.
        """

        return [
            (row.messageKind, row.agentId, row.deliveredToSession)
            for row in self.rows()
            if row.messageKind == "state-signal"
            or session_id in (row.agentId, row.deliveredToSession)
        ]

    def notifier(self) -> list[OperatorInboxEntry]:
        """One real notifier sweep under a bridge that accepts whatever it is handed."""

        ctx = AgentNotifierContext(
            catalog=self.catalog,
            host=cast(TerminalHost, self.host),
            paster=cast(TerminalPaster, _AcceptedPaster()),
            inbox_store=self.inbox,
            expectation_store=ExpectationRowStore(self.observer_root),
            signal_cooldown_store=AgentNotifierSignalCooldownStore(self.observer_root),
            event_store=EventStore(self.observer_root),
            heartbeat_store=AgentNotifierHeartbeatStore(self.observer_root),
            coordination_root=self.coordination_root,
            stale_seat_seconds=60.0,
            redeliver_rate_limit_seconds=900.0,
        )
        with mock.patch(
            _SUBMIT,
            side_effect=lambda _target, _text, submission: SubmissionReceipt(
                request_id=submission.request_id,
                acceptance="immediate",
                submitted_at=SWEPT_AT_TEXT,
                accepted_at=SWEPT_AT_TEXT,
            ),
        ):
            run_agent_notifier_sweep(ctx, now=self.clock + timedelta(seconds=1))
        return self.rows()

    def rows(self) -> list[OperatorInboxEntry]:
        return list(self.inbox.current().values())

    def signals(self) -> list[OperatorInboxEntry]:
        return [row for row in self.rows() if row.messageKind == "state-signal"]


@contextmanager
def _world(*, with_master_manager: bool = True) -> Iterator[_CuratorRelayWorld]:
    with tempfile.TemporaryDirectory() as directory:
        yield _CuratorRelayWorld(Path(directory), with_master_manager=with_master_manager)


def _single_signal(world: _CuratorRelayWorld) -> OperatorInboxEntry:
    signals = world.signals()
    if len(signals) != 1:
        raise AssertionError(f"expected exactly one durable state signal, got {signals}")
    return signals[0]


class CuratorTurnOwnerWakeTests(unittest.TestCase):
    def test_a_completed_curator_turn_wakes_the_current_manager_with_one_durable_signal(
        self,
    ) -> None:
        with _world() as world:
            registered = world.curator()
            # Premise: the row is registration only -- no turn claim, no terminal truth.
            self.assertIsNone(registered.turn_state)
            self.assertIsNone(registered.terminal_outcome)
            self.assertIsNone(registered.terminal_evidence_id)

            world.observe_adapter_evidence()
            world.settle()

            observed = world.curator()
            self.assertEqual(observed.turn_state, "turn-ended")
            self.assertEqual(observed.terminal_outcome, "completed")
            self.assertEqual(observed.terminal_evidence_id, "turn-41")

            world.notifier()
            signal = _single_signal(world)
            self.assertEqual(signal.agentId, MANAGER_ID)
            self.assertEqual(signal.recipientRole, "manager")
            self.assertEqual(signal.taskDocumentRef, MASTER)
            self.assertEqual(signal.subjectTaskDocumentRef, LEAF)
            self.assertEqual(signal.subjectAgentId, CURATOR_ID)
            self.assertEqual(signal.seatRole, "curator")
            for fragment in ("completed", "turn-41"):
                self.assertIn(fragment, signal.ask)
            for fragment in (LEAF.key, "as curator", "turn-41", "completed"):
                self.assertIn(fragment, signal.response)
            # The wake is delivered, and it is durable rather than a live-only notification.
            self.assertTrue(state_signal_landed(signal))
            self.assertEqual(signal.deliveredToSession, MANAGER_ID)
            self.assertEqual(
                [
                    row.id
                    for row in OperatorInboxStore(world.observer_root).current().values()
                    if row.messageKind == "state-signal"
                ],
                [signal.id],
            )
            # No curator-authored completion row: the curator never had to post anything.
            self.assertEqual([row.id for row in world.rows() if row.senderRole == "curator"], [])
            self.assertEqual(world.curator().state_signal_emitted_for, "turn-41")

            # Re-observation of the same terminal evidence mints no second signal.
            world.notifier()
            self.assertEqual([row.id for row in world.signals()], [signal.id])

    def test_an_interrupted_curator_turn_wakes_the_manager_with_interruption_truth(self) -> None:
        with _world() as world:
            world.evidence = _CuratorEvidence(sequence=77, status="interrupted")
            world.catalog.upsert(
                _seat_entry(
                    CURATOR_ID,
                    task_document_ref=LEAF,
                    spawn_role="curator",
                    seat_role="curator",
                    spawned_by_session=STALE_SPAWNER_ID,
                    interrupt_requested_by="developer",
                    interrupt_requested_at=SWEPT_AT_TEXT,
                    interrupt_requested_turn_id="turn-77",
                )
            )
            self.assertIsNone(world.curator().terminal_evidence_id)

            world.observe_adapter_evidence()

            observed = world.curator()
            self.assertEqual(observed.turn_state, "turn-ended")
            self.assertEqual(observed.terminal_outcome, "interrupted")
            self.assertEqual(observed.terminal_evidence_id, "turn-77")

            world.notifier()
            signal = _single_signal(world)
            self.assertEqual(signal.agentId, MANAGER_ID)
            self.assertEqual(signal.subjectTaskDocumentRef, LEAF)
            self.assertEqual(signal.seatRole, "curator")
            self.assertIn("interrupted", signal.ask)
            self.assertIn("turn-77", signal.ask)
            self.assertNotIn("completed", signal.ask)
            self.assertIn("outcome interrupted", signal.response)
            self.assertIn("interrupted_by=developer", signal.response)
            self.assertTrue(state_signal_landed(signal))

            # Re-observation of the same terminal evidence mints no second durable signal, and the
            # re-emission guard is stamped on the interrupted path exactly as on the completed one.
            world.notifier()
            self.assertEqual([row.id for row in world.signals()], [signal.id])
            self.assertEqual(world.curator().state_signal_emitted_for, "turn-77")

    def test_a_failed_curator_turn_never_wakes_the_manager(self) -> None:
        """The canonical outcome vocabulary is closed: ``failed`` is not a manager wake.

        ``LOCR-R07@v1`` scopes the wake to ``completed`` and ``interrupted``, and the sibling packet
        states the same boundary as an explicit Exclusion ("Failed or unknown terminal outcomes
        unless separately handled by existing failure supervision"). A third native status the codex
        projector maps to a real outcome is the negative control that keeps the boundary honest from
        the outside: the seat reaches terminal truth, and the relay still emits nothing.
        """

        with _world() as world:
            world.evidence = _CuratorEvidence(sequence=91, status="failed")
            self.assertIsNone(world.curator().terminal_evidence_id)

            world.observe_adapter_evidence()

            observed = world.curator()
            self.assertEqual(observed.turn_state, "turn-ended")
            self.assertEqual(observed.terminal_outcome, "failed")
            self.assertEqual(observed.terminal_evidence_id, "turn-91")

            world.notifier()

            self.assertEqual(world.signals(), [])
            # Not consumed: the seat stays eligible, so a later canonical outcome can still wake.
            self.assertIsNone(world.curator().state_signal_emitted_for)

    def test_the_curator_wake_neither_validates_nor_declares_curator_coherence(self) -> None:
        with _world() as world:
            # Premise: this world carries no curator artifact at all -- the coordination root is
            # the task tree plus the observer's own durable rows.
            self.assertEqual(world.coordination_root_entries(), ["logs", "tasks"])

            world.observe_adapter_evidence()
            world.settle()
            world.notifier()

            signal = _single_signal(world)
            observed = world.curator()
            # The payload is the canonical terminal-truth derivation, with no verdict appended.
            self.assertEqual(signal.ask, state_signal_ask(observed, "turn-41"))
            self.assertEqual(signal.response, state_signal_response(observed))
            for text in (signal.ask, signal.response):
                for token in VERDICT_VOCABULARY:
                    self.assertNotIn(token, text.lower())
            # The claim is about post-sweep state, so it is read after the sweep: a relay that wrote
            # its own coherence/readiness artifact during the wake would show up here and nowhere
            # else. Read through the same helper as the premise, so both sides are one population.
            self.assertEqual(world.coordination_root_entries(), ["logs", "tasks"])
            # No acceptance row joins it, and the manager -- not the relay -- owns consumption.
            self.assertEqual(
                [row.messageKind for row in world.rows() if row.subjectAgentId == CURATOR_ID],
                ["state-signal"],
            )
            self.assertIsNone(signal.consumedAt)

    def test_a_curator_seat_without_a_current_manager_fails_closed_without_global_routing(
        self,
    ) -> None:
        with _world(with_master_manager=False) as world:
            # Premise: another master's manager is live and current, so a global owner search
            # would have somewhere to route.
            self.assertIsNotNone(world.manager_for(OTHER_MASTER))
            self.assertIsNone(world.manager_for(MASTER))

            world.observe_adapter_evidence()
            world.settle()
            self.assertEqual(world.curator().terminal_outcome, "completed")

            world.notifier()

            # One reachable assertion, deliberately not split: a state signal anywhere, or any row
            # naming that live manager as owner or delivery target, is the failure. Splitting it
            # would leave the second half shadowed by the first in exactly the scenario it exists
            # for, so the broader addressing check IS the reachable check.
            self.assertEqual(world.addressed_to(OTHER_MANAGER_ID), [])
            # Fail closed, not consume: the seat stays eligible for a later sweep.
            self.assertIsNone(world.curator().state_signal_emitted_for)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
