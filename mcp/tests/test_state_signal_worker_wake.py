"""Worker turn owner wake: canonical worker terminal truth wakes the current manager.

A worker's substantive handoff is its turn report, and it is not required to author a second
completion message. These cases pin the wake that must therefore arrive on its own: an owned
``worker`` row whose catalog terminal evidence says ``completed`` or ``interrupted`` becomes a
state-signal finding, and the notifier persists and routes exactly one durable row to the leaf's
current manager through the existing inbox path.

Instrument reach -- what each case measures and what stays outside it:

* the worker row is seeded as adapter/catalog terminal truth, and **no** inbox row is created for
  the worker at all. The whole store is asserted (not just its state-signal subset), so a
  worker-authored row would be visible to the assertion rather than filtered out of it;
* :class:`_RecordingHost` counts every contact the sweep makes with the terminal host. ``get`` is
  the live terminal-session read behind the terminal-session GET route, so an empty
  ``get_calls`` is the "no terminal-session GET" clause; ``has_session_calls`` is populated on the
  delivery path, so the empty read list is a measured zero on a host the sweep demonstrably
  reached rather than a host it never touched;
* task-document bytes are captured around the sweep and compared afterwards, so "the signal does
  not claim the worker satisfied its task" is measured as an unchanged leaf/master document pair
  rather than as an inspection of wording;
* the managerless-master case asserts the complete inbox store, so "does not guess a global owner"
  covers the unrelated manager receiving nothing as well as the worker stamping no marker.

Each positive case also carries the negative half that keeps it falsifiable: the completed and
interrupted cases each assert their own outcome and exclude the other, the task-document case
asserts the wake happened before it asserts nothing else moved, and the no-evidence case re-runs
the same seat with its evidence identity stamped to show the discriminator is evidence, not the
report file on disk.

The eligible-outcome set is pinned from **both** sides. A predicate whose positive members are the
only values any fixture carries leaves its negative half unpinned: ``completed`` and ``interrupted``
wake the manager, and a turn-end whose outcome is neither of them -- ``failed`` or ``unknown`` --
must not, however real its evidence identity is. Symmetrically, the per-turn dedupe is pinned in
both directions: the same evidence identity projected twice mints one row, and a **second distinct
terminal turn** on the same seat mints its own wake instead of being swallowed by the first turn's
marker.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from agents_remember.controlplane.agent_notifier_signals import AgentNotifierSignalCooldownStore
from agents_remember.controlplane.expectation_rows import ExpectationRowStore
from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.store import EventStore
from agents_remember.serving.agent_notifier import AgentNotifierContext, run_agent_notifier_sweep
from agents_remember.serving.agent_notifier_heartbeat import AgentNotifierHeartbeatStore
from agents_remember.serving.state_signals import evaluate_state_signal_findings
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_paste import PasteResult, TerminalPaster
from agents_remember.serving.terminal_tmux import TmuxProbeResult
from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentTopology

NOW = datetime(2026, 7, 13, 15, 41, 0, tzinfo=UTC)
SPRINT = TaskDocumentRef(repository="repo-a", path="sprint/task.json")
MASTER = TaskDocumentRef(repository="repo-a", path="260707_master/task.json")
LEAF = TaskDocumentRef(repository="repo-a", path="260707_master/leaf-9.json")
OTHER_MASTER = TaskDocumentRef(repository="repo-a", path="other-master/task.json")


class _RecordingHost:
    """A terminal host that records every contact the sweep makes with it."""

    def __init__(self) -> None:
        self.has_session_calls: list[str] = []
        self.get_calls: list[str] = []

    def has_session(self, tmux_name: str) -> bool:
        self.has_session_calls.append(tmux_name)
        return True

    def probe_session(self, _tmux_name: str) -> TmuxProbeResult:
        return TmuxProbeResult(exists=True, evidence="alive")

    def get(self, sid: str) -> None:
        # The live terminal-session read. Nothing on the wake path may need it: terminal truth is
        # adapter evidence in the catalog, never a live session this process has to go and ask.
        self.get_calls.append(sid)


def _accepted_paster() -> TerminalPaster:
    class _AcceptedPaster:
        def paste(
            self,
            _tmux_name: str,
            _text: str,
            *,
            submit: bool = False,
            **_kwargs: object,
        ) -> PasteResult:
            return PasteResult(delivered=True, submitted=submit)

    return cast(TerminalPaster, _AcceptedPaster())


def _entry(
    session_id: str,
    *,
    task_document_ref: TaskDocumentRef | None = None,
    **overrides: object,
) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=session_id,
        label=f"Chat {session_id}",
        kind="harness",
        harness="codex",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=("codex",),
        created_at="2026-07-13T00:00:00+00:00",
        last_attached_at="2026-07-13T00:00:00+00:00",
        status="running",
        task_document_ref=task_document_ref,
        **overrides,  # type: ignore[arg-type]
    )


def _manager(
    session_id: str = "manager-1",
    *,
    task_document_ref: TaskDocumentRef = MASTER,
    **overrides: object,
) -> TerminalCatalogEntry:
    return _entry(
        session_id,
        task_document_ref=task_document_ref,
        spawn_role="manager",
        seat_role="manager",
        **overrides,
    )


def _worker(
    session_id: str = "worker-1",
    *,
    task_document_ref: TaskDocumentRef = LEAF,
    outcome: str = "completed",
    evidence_id: str | None = "turn-9",
    **overrides: object,
) -> TerminalCatalogEntry:
    """One owned worker seat reporting canonical terminal truth from the adapter."""

    return _entry(
        session_id,
        task_document_ref=task_document_ref,
        spawn_role="worker",
        seat_role="worker",
        spawned_by_session="manager-1",
        turn_state="turn-ended",
        turn_state_changed_at=NOW.isoformat(),
        terminal_outcome=outcome,
        terminal_outcome_at=NOW.isoformat(),
        terminal_evidence_id=evidence_id,
        **overrides,  # type: ignore[arg-type]
    )


def _sprint_orchestrator(session_id: str = "orchestrator-1") -> TerminalCatalogEntry:
    """The sprint-scoped orchestrator: the manager's own structural owner.

    Present so the sweep's unrelated dead-upstream fact stays quiet and the whole store can be
    asserted for the wake row. A manager whose orchestrator seat has no occupant is a different
    concern from this leaf's.
    """

    return _entry(
        session_id,
        task_document_ref=SPRINT,
        spawn_role="orchestrator",
        seat_role="orchestrator",
        turn_state="working",
        turn_state_changed_at=NOW.isoformat(),
    )


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


class WorkerTurnOwnerWakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.coordination_root = Path(self.tmp.name) / "ar-coordination"
        task_root = self.coordination_root / "tasks" / "repo-a"
        write_task_doc(
            task_root / "sprint",
            _task_doc(
                id="SPRINT",
                slug="sprint",
                title="Sprint",
                kind="master",
                orchestrates=["260707_master", "other-master"],
            ),
        )
        write_task_doc(
            task_root / "260707_master",
            _task_doc(
                id="MASTER",
                slug="260707_master",
                title="260707_master",
                kind="master",
                subTasks=[
                    {
                        "number": "leaf-9",
                        "name": "leaf-9",
                        "file": "leaf-9.md",
                        "status": "inProgress",
                    }
                ],
            ),
        )
        write_task_doc(
            task_root / "260707_master",
            _task_doc(
                id="leaf-9",
                slug="leaf-9",
                title="leaf-9",
                kind="subTask",
                master="task.md",
                steps=[
                    {"id": "S1", "title": "Build", "status": "pending"},
                    {"id": "S2", "title": "Report", "status": "pending"},
                ],
            ),
        )
        write_task_doc(
            task_root / "other-master",
            _task_doc(
                id="OTHER",
                slug="other-master",
                title="other-master",
                kind="master",
                subTasks=[
                    {"number": "leaf", "name": "leaf", "file": "leaf.md", "status": "inProgress"}
                ],
            ),
        )
        write_task_doc(
            task_root / "other-master",
            _task_doc(
                id="leaf",
                slug="leaf",
                title="leaf",
                kind="subTask",
                master="task.md",
            ),
        )
        self.task_root = task_root
        self.topology = TaskDocumentTopology(self.coordination_root)
        observer_root = self.coordination_root / "logs" / "observer"
        self.catalog = TerminalCatalog(Path(self.tmp.name) / "catalog.json")
        self.catalog.upsert(_sprint_orchestrator())
        self.inbox_store = OperatorInboxStore(observer_root)
        self.host = _RecordingHost()

    def _ctx(self) -> AgentNotifierContext:
        observer_root = self.coordination_root / "logs" / "observer"
        return AgentNotifierContext(
            catalog=self.catalog,
            host=cast(TerminalHost, self.host),
            paster=_accepted_paster(),
            inbox_store=self.inbox_store,
            expectation_store=ExpectationRowStore(observer_root),
            signal_cooldown_store=AgentNotifierSignalCooldownStore(observer_root),
            event_store=EventStore(observer_root),
            heartbeat_store=AgentNotifierHeartbeatStore(observer_root),
            coordination_root=self.coordination_root,
            stale_seat_seconds=60.0,
            redeliver_rate_limit_seconds=900.0,
        )

    def _sweep(self) -> None:
        run_agent_notifier_sweep(self._ctx(), now=NOW)

    def _rows(self) -> dict[str, OperatorInboxEntry]:
        return self.inbox_store.current()

    def _state_signal_rows(self) -> list[OperatorInboxEntry]:
        return [row for row in self._rows().values() if row.messageKind == "state-signal"]

    def _addressed_agents(self) -> set[str]:
        """Every agent the whole store names, as address or as owner, and nothing else."""

        return {
            agent
            for row in self._rows().values()
            for agent in (row.agentId, row.ownerAgentId)
            if agent is not None
        }

    def _only_state_signal_row(self) -> OperatorInboxEntry:
        rows = self._state_signal_rows()
        self.assertEqual(len(rows), 1, rows)
        return rows[0]

    def _only_row(self) -> OperatorInboxEntry:
        rows = self._rows()
        self.assertEqual(len(rows), 1, rows)
        return next(iter(rows.values()))

    def test_completed_worker_without_inbox_row_wakes_current_manager(self) -> None:
        """The incident shape: report written, turn ended, no message sent, manager woken.

        The wake is durable, structurally addressed to the leaf's current manager, and carries the
        worker seat, the leaf document, the role, the outcome and the terminal evidence identity.
        It is produced from adapter evidence without any live terminal-session read, and a second
        projection of the same evidence mints no second row.
        """

        # No worker inbox row exists before the sweep, and none is created for it: the whole store
        # holds exactly the one notifier-authored state-signal addressed to the manager.
        self.assertEqual(self._rows(), {})
        self.catalog.upsert(_manager())

        self.catalog.upsert(_worker())
        self._sweep()

        row = self._only_row()
        self.assertEqual(row.messageKind, "state-signal")
        self.assertEqual(row.createdBy, "agent-notifier")
        self.assertEqual(row.agentId, "manager-1")
        self.assertEqual(row.taskDocumentRef, MASTER)
        self.assertEqual(row.recipientRole, "manager")
        self.assertEqual(row.subjectAgentId, "worker-1")
        self.assertEqual(row.subjectTaskDocumentRef, LEAF)
        self.assertEqual(row.seatRole, "worker")
        self.assertIn("turn-9", row.ask)
        self.assertIn("completed", row.ask)
        self.assertNotIn("interrupted", row.ask)
        self.assertIn(f"{LEAF.key} as worker", row.response)
        self.assertIn("turn turn-9", row.response)
        self.assertIn("outcome completed", row.response)

        worker = self.catalog.get("worker-1")
        assert worker is not None
        self.assertEqual(worker.state_signal_emitted_for, "turn-9")

        # The host was reached on the delivery path, so the empty live-read list is a measured
        # zero rather than a path that never touches the host at all.
        self.assertEqual(self.host.has_session_calls, ["ar-manager-1"])
        self.assertEqual(self.host.get_calls, [])

        # Re-projecting the same terminal evidence must not mint a second row.
        self._sweep()
        self.assertEqual(len(self._rows()), 1)

    def test_a_second_terminal_turn_for_the_same_seat_wakes_the_manager_again(self) -> None:
        """The next turn of a resumed worker is a new wake, not a suppressed re-projection.

        Dedupe is per terminal-evidence identity, not per seat: the marker advances to the new turn
        and the manager is woken with the new evidence id, so a resumed worker whose first wake was
        already delivered still reaches its manager on turn two. Asserting only that a re-projected
        *same* evidence mints no second row cannot see this -- the inbox coalesces onto one durable
        row -- which is exactly what this case adds.

        The second turn is projected onto the **stored** row, so the marker turn 1 stamped is still
        present when turn 2 is evaluated. Projecting it as a fresh row would clear that marker and
        leave this case passing against an at-most-once-per-seat relay.
        """

        self.catalog.upsert(_manager())
        self.catalog.upsert(_worker())

        self._sweep()

        first = self._only_state_signal_row()
        self.assertIn("turn-9", first.ask)
        self.assertEqual(first.agentId, "manager-1")
        after_first = self.catalog.get("worker-1")
        assert after_first is not None
        self.assertEqual(after_first.state_signal_emitted_for, "turn-9")

        # The same seat runs a second turn: the SAME stored row advances to a fresh evidence
        # identity and keeps the marker stamped for turn 1. Re-projecting the row from scratch
        # would silently clear that marker, and the case would then be unable to see the dedupe at
        # all -- it would pass against an at-most-once-per-seat relay.
        self.catalog.upsert(
            replace(
                after_first,
                terminal_evidence_id="turn-10",
                terminal_outcome_at=NOW.isoformat(),
            )
        )
        self._sweep()

        second = self.catalog.get("worker-1")
        assert second is not None
        self.assertEqual(second.state_signal_emitted_for, "turn-10")
        rows = self._state_signal_rows()
        self.assertEqual(len(rows), 2, rows)
        woken = [row for row in rows if "turn-10" in row.ask]
        self.assertEqual(len(woken), 1, rows)
        self.assertEqual([row.agentId for row in rows], ["manager-1", "manager-1"])
        self.assertEqual(woken[0].taskDocumentRef, MASTER)
        self.assertEqual(woken[0].subjectAgentId, "worker-1")
        self.assertEqual(woken[0].subjectTaskDocumentRef, LEAF)
        self.assertEqual(woken[0].seatRole, "worker")
        self.assertIn("outcome completed", woken[0].response)

    def test_failed_or_unknown_terminal_turn_never_wakes_the_manager(self) -> None:
        """Only ``completed`` and ``interrupted`` are wake conditions, however real the evidence.

        A turn-end carrying a genuine adapter evidence identity but a package-excluded outcome --
        ``failed`` or ``unknown`` -- must leave the whole store untouched and the seat unstamped, so
        the next sweep can still evaluate it. The final leg re-reports the same seat's same evidence
        identity as ``completed`` and requires the wake, which makes the empty store a measured
        refusal rather than an inert relay and shows the refusal was about the outcome alone.
        """

        self.catalog.upsert(_manager())

        for outcome in ("failed", "unknown"):
            with self.subTest(outcome=outcome):
                self.catalog.upsert(_worker(f"worker-{outcome}", outcome=outcome))

                self._sweep()

                self.assertEqual(self._rows(), {})
                self.assertEqual(evaluate_state_signal_findings(self.catalog, self.topology), [])
                excluded = self.catalog.get(f"worker-{outcome}")
                assert excluded is not None
                self.assertIsNone(excluded.state_signal_emitted_for)

        # Still eligible: the same seat, on the same evidence identity, with an eligible outcome.
        self.catalog.upsert(_worker("worker-failed", outcome="completed"))
        self._sweep()

        row = self._only_state_signal_row()
        self.assertEqual(row.agentId, "manager-1")
        self.assertEqual(row.subjectAgentId, "worker-failed")
        self.assertIn("turn-9", row.ask)
        self.assertIn("outcome completed", row.response)

    def test_interrupted_worker_wakes_manager_as_interrupted(self) -> None:
        """A terminal outcome of ``interrupted`` wakes the manager with that exact outcome.

        The recovery state travels with the wake -- the outcome and its origin both reach the
        manager's row -- and the wake is not mislabelled as a completed turn.
        """

        self.catalog.upsert(_manager())
        self.catalog.upsert(_worker(outcome="interrupted", interrupted_by="developer"))

        self._sweep()

        row = self._only_row()
        self.assertEqual(row.agentId, "manager-1")
        self.assertEqual(row.subjectAgentId, "worker-1")
        self.assertEqual(row.subjectTaskDocumentRef, LEAF)
        self.assertEqual(row.seatRole, "worker")
        self.assertIn("turn-9", row.ask)
        self.assertIn("interrupted", row.ask)
        self.assertNotIn("completed", row.ask)
        self.assertIn("outcome interrupted", row.response)
        self.assertIn("interrupted_by=developer", row.response)

    def test_worker_wake_leaves_task_documents_untouched(self) -> None:
        """The signal reports a seat turn; it never closes task work.

        The wake exists before this case asserts that nothing else moved, so an unchanged document
        pair cannot be satisfied by a sweep that emitted nothing at all.
        """

        leaf_json = self.task_root / "260707_master" / "leaf-9.json"
        master_json = self.task_root / "260707_master" / "task.json"
        documents = (
            leaf_json,
            leaf_json.with_suffix(".md"),
            master_json,
            master_json.with_suffix(".md"),
        )
        before = {path: path.read_bytes() for path in documents}
        self.catalog.upsert(_manager())
        self.catalog.upsert(_worker())

        self._sweep()

        row = self._only_row()
        self.assertEqual(row.messageKind, "state-signal")
        self.assertEqual(row.subjectAgentId, "worker-1")
        self.assertEqual({path: path.read_bytes() for path in documents}, before)
        leaf = read_task_doc(leaf_json)
        self.assertEqual(leaf.status, "planning")
        self.assertEqual([step.status for step in leaf.steps], ["pending", "pending"])
        master = read_task_doc(master_json)
        self.assertEqual([subtask.status for subtask in master.subTasks], ["inProgress"])

    def test_turn_end_without_terminal_evidence_identity_wakes_nobody(self) -> None:
        """A report on disk is not terminal evidence; the evidence identity is the discriminator.

        The same seat wakes its manager as soon as the adapter stamps an evidence identity, so the
        empty store before that stamp is measured against a working wake rather than against a
        relay that never fires.
        """

        report = self.coordination_root / "tasks" / "repo-a" / "260707_master" / "leaf-9-report.md"
        report.write_text("# worker report\n\nthe worker finished its turn\n", encoding="utf-8")
        self.catalog.upsert(_manager())
        self.catalog.upsert(_worker(evidence_id=None))

        self._sweep()

        self.assertEqual(self._rows(), {})
        unstamped = self.catalog.get("worker-1")
        assert unstamped is not None
        self.assertIsNone(unstamped.state_signal_emitted_for)
        self.assertTrue(report.exists())

        # Stamping the adapter evidence identity -- and nothing else -- is what makes it wake.
        self.catalog.upsert(_worker())
        self._sweep()
        self.assertEqual(self._only_row().subjectAgentId, "worker-1")

    def test_worker_below_a_managerless_master_wakes_nobody_and_stays_eligible(self) -> None:
        """No derivable manager means no wake, no marker, and no guessed global owner.

        The only visible manager owns a different master, so waking it would be exactly the
        cross-master misroute the structural route forbids. The source stays eligible: the same
        seat wakes the right manager on the next sweep once that master has one. This case asserts
        the state-signal relay's own surface -- no finding, no row, no marker, no misrouted agent --
        and deliberately says nothing about the pre-existing dead-upstream supervision row the
        sweep separately raises for a subordinate whose owner seat is unoccupied.
        """

        self.catalog.upsert(_manager("manager-other", task_document_ref=OTHER_MASTER))
        self.catalog.upsert(_worker())

        self.assertEqual(evaluate_state_signal_findings(self.catalog, self.topology), [])
        self._sweep()

        # The sweep did raise its own unrelated supervision row here, so the empty state-signal
        # subset below is a measured contrast on a sweep that ran, not a sweep that did nothing.
        self.assertTrue(self._rows())
        self.assertEqual(self._state_signal_rows(), [])
        self.assertNotIn(
            "manager-other",
            self._addressed_agents(),
            "the only visible manager owns another master and must not be woken",
        )
        eligible = self.catalog.get("worker-1")
        assert eligible is not None
        self.assertIsNone(eligible.state_signal_emitted_for)

        self.catalog.upsert(_manager("manager-late"))
        self._sweep()

        row = self._only_state_signal_row()
        self.assertEqual(row.agentId, "manager-late")
        self.assertEqual(row.taskDocumentRef, MASTER)
        self.assertEqual(row.subjectTaskDocumentRef, LEAF)
        self.assertEqual(row.seatRole, "worker")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
