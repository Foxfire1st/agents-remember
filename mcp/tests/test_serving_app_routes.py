"""Behavioural coverage for the dashboard route arms that only fire when something is wrong.

``serving/app.py``'s happy paths are well covered by ``test_serving.py`` and ``test_terminal_ws.py``.
What was untested is the half of each handler that decides what the cockpit sees when the world does
not cooperate: a request that lands before the first projection tick, a task document that is not
there (or is somewhere it must never be read from), a seat whose tmux pane died between the catalog
write and the request, a harness whose control socket is gone, a rename of a session that was
already retired, and the gate-decision arm the router reaches only for a workspace-level cancel.

Layering, and why each test sits where it does:

* **Boot race** -- ``TestClient`` is deliberately used *without* its context manager so the app's
  lifespan (and therefore ``Projector.prime``) never runs. That is exactly the pre-first-tick window
  the three ``503 projection not ready`` guards exist for, reproduced over real HTTP.
* **Routes** -- driven through the real FastAPI app with a real ``TerminalCatalog`` and real task
  documents; the only double is the terminal host, which stands in for tmux.
* **Helpers below the wire** -- ``_gate_decision_response`` is called directly for the gate-id-only
  decision, because assembling that shape through the app would say nothing the route tests in
  ``test_serving.py`` do not already say about the route.
* **Protocol harness** -- the delivered/undelivered submission mapping runs against the shared
  ``_control_plane`` topology: a real ``HarnessControlBridge`` behind a real Unix-socket server, so
  the operator's text is proven to arrive at the adapter rather than at a mock.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from _control_plane import FakeControlAdapter, drive_activity, make_harness
from agents_remember.controlplane.records import GateAnchor, create_gate
from agents_remember.controlplane.store import GateStore
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import (
    TerminalCatalogEntry,
    TerminalSessionStatus,
)
from agents_remember.observer import observer_root
from agents_remember.observer.store import EventStore
from agents_remember.serving.actions import ActionOutcome, GateDecisionIntent
from agents_remember.serving.app import (
    ServingCollaborators,
    TerminalPasteRequest,
    _gate_decision_response,
    _harness_submit_response,
    create_app,
)
from agents_remember.serving.projector import ProjectionCadence
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
)
from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc

FRESH_GATE_TS = "2999-01-01T10:00:00+00:00"


def _config(tmp: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=tmp / "settings.json",
        coordination_root=tmp,
        workspace_root=tmp,
        transcript_root=tmp / "logs" / "mcp",
    )


class _CatalogOnlyHost:
    """A ``TerminalHost`` duck-type standing in for tmux: which panes exist, and killing them.

    No PTY is involved -- none of these routes attach one. ``on_terminate`` is the seam a test uses
    to interleave another process's catalog write with a retire, which is the only way the
    ``mark_retired`` returned nothing arm can be reached.
    """

    def __init__(self) -> None:
        self.live_panes: set[str] = set()
        self.terminated: list[str] = []
        self.shutdown_called = False
        self.on_terminate: Callable[[str], None] | None = None

    def get(self, _session_id: str) -> None:
        return None

    def has_session(self, tmux_name: str) -> bool:
        return tmux_name in self.live_panes

    def terminate(self, session_id: str, *, tmux_name: str | None = None) -> None:
        target = tmux_name or session_id
        self.terminated.append(target)
        self.live_panes.discard(target)
        if self.on_terminate is not None:
            self.on_terminate(session_id)

    def shutdown(self) -> None:
        self.shutdown_called = True


def _row(session_id: str, cwd: Path, label: str | None, **fields: Any) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=session_id,
        label=label or f"Seat {session_id}",
        lifecycle_id=None,
        cwd=cwd,
        tmux_name=f"ar-{session_id}",
        created_at="2026-07-31T00:00:00Z",
        last_attached_at="2026-07-31T00:00:00Z",
        **fields,
    )


def _terminal(
    session_id: str,
    *,
    cwd: Path,
    status: TerminalSessionStatus = "running",
    label: str | None = None,
) -> TerminalCatalogEntry:
    """A plain shell seat, as the dashboard's own opener writes it."""

    return _row(
        session_id, cwd, label, kind="terminal", harness=None, command=("bash",), status=status
    )


def _harness_row(
    session_id: str,
    *,
    cwd: Path,
    control_endpoint: Path | None = None,
    label: str | None = None,
) -> TerminalCatalogEntry:
    """A harness seat with no spawn role -- the hand-opened shape, optionally protocol-backed."""

    return _row(
        session_id,
        cwd,
        label,
        kind="harness",
        harness="claude",
        command=("claude",),
        status="running",
        control_endpoint=control_endpoint,
    )


def _write_leaf_task(coordination_root: Path, *, repo: str, master: str, doc_id: str) -> Path:
    """Write a real master + leaf pair; returns the leaf document's JSON path."""

    task_root = coordination_root / "tasks" / repo / master
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": master.upper(),
                "slug": "task",
                "title": "Master",
                "kind": "master",
                "repo": repo,
                "createdAt": "2026-07-31T10:00",
                "subTasks": [
                    {
                        "number": doc_id,
                        "name": "Leaf",
                        "file": f"{doc_id}.md",
                        "status": "inProgress",
                    }
                ],
            }
        ),
    )
    json_path, _markdown = write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": doc_id,
                "slug": doc_id,
                "title": "The leaf under test",
                "kind": "subTask",
                "repo": repo,
                "createdAt": "2026-07-31T10:01",
                "master": "task.md",
                "objective": "Prove the on-demand reader serves the whole document body.",
                "requirements": ["the body rides the on-demand endpoint, not the projection"],
            }
        ),
    )
    return json_path


class _AppFixture(unittest.TestCase):
    """A real app over a real catalog and a tmux stand-in."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)
        self.host = _CatalogOnlyHost()
        self.catalog = TerminalCatalog(self.tmp / "terminal-sessions.json")
        self.config = _config(self.tmp)
        self.app = create_app(
            self.config,
            cadence=ProjectionCadence(interval=100),
            collaborators=ServingCollaborators(
                terminal_host=cast(TerminalHost, self.host), terminal_catalog=self.catalog
            ),
        )

    def register(self, entry: TerminalCatalogEntry, *, pane_alive: bool = True) -> None:
        self.catalog.upsert(entry)
        if pane_alive:
            self.host.live_panes.add(entry.tmux_name)


class BeforeTheFirstProjectionTests(_AppFixture):
    """Every read/write that needs a projection refuses honestly during the boot window.

    ``TestClient`` is used bare (no ``with``) so the lifespan -- and therefore the projector's
    prime -- never runs: a request in flight before the first tick is exactly this state.
    """

    def test_state_reports_projection_not_ready(self) -> None:
        response = TestClient(self.app).get("/api/state")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "projection not ready")

    def test_task_document_reports_projection_not_ready(self) -> None:
        # The reader joins enclosure lifecycles onto the document, so it cannot answer either.
        response = TestClient(self.app).get("/api/task-document", params={"path": "anything.json"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "projection not ready")

    def test_actions_refuse_rather_than_acting_on_an_absent_projection(self) -> None:
        # The action router decides availability FROM the projection, so with none there is no
        # basis to accept -- and no gate row may be written on the way past.
        response = TestClient(self.app).post("/api/actions/resume", json={"target": "L1"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "projection not ready")
        self.assertEqual(GateStore(observer_root(self.config)).current("L1"), {})


class TaskDocumentEndpointTests(_AppFixture):
    """``/api/task-document``: the on-demand reader body, and what it refuses to read."""

    def setUp(self) -> None:
        super().setUp()
        self.doc_path = _write_leaf_task(self.tmp, repo="repo", master="master", doc_id="leaf-1")

    def test_serves_the_full_document_body_for_a_tasks_relative_path(self) -> None:
        with TestClient(self.app) as client:
            response = client.get("/api/task-document", params={"path": "repo/master/leaf-1.json"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], "leaf-1")
        self.assertEqual(body["title"], "The leaf under test")
        self.assertEqual(body["kind"], "subTask")
        # The body fields the always-on projection summary deliberately omits.
        self.assertEqual(
            body["objective"], "Prove the on-demand reader serves the whole document body."
        )
        self.assertEqual(
            body["requirements"], ["the body rides the on-demand endpoint, not the projection"]
        )

    def test_serves_parent_and_nested_intentional_skip_dispositions(self) -> None:
        doc = read_task_doc(self.doc_path)
        data = doc.model_dump(by_alias=True)
        disposition = {
            "kind": "intentionalSkip",
            "reason": "Superseded.",
            "recordedAt": "2026-08-03T12:00:00+00:00",
            "recordedVia": "task_doc.skip_step",
        }
        data["steps"] = [
            {
                "id": "S1",
                "title": "Parent",
                "status": "done",
                "disposition": disposition,
                "substeps": [
                    {
                        "id": "C1",
                        "title": "Child",
                        "status": "done",
                        "disposition": disposition,
                    }
                ],
            }
        ]
        write_task_doc(self.doc_path.parent, TaskDocument.model_validate(data))

        with TestClient(self.app) as client:
            response = client.get("/api/task-document", params={"path": "repo/master/leaf-1.json"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["steps"][0]["disposition"]["reason"], "Superseded.")
        self.assertEqual(
            body["steps"][0]["substeps"][0]["disposition"]["recordedVia"],
            "task_doc.skip_step",
        )

    def test_serves_the_same_document_for_a_coordination_root_relative_path(self) -> None:
        with TestClient(self.app) as client:
            response = client.get(
                "/api/task-document", params={"path": "tasks/repo/master/leaf-1.json"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["docPath"], self.doc_path.resolve().as_posix())

    def test_an_unknown_document_is_not_found(self) -> None:
        with TestClient(self.app) as client:
            response = client.get(
                "/api/task-document", params={"path": "repo/master/no-such-leaf.json"}
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "task document not found")

    def test_a_path_outside_the_tasks_root_is_refused_as_not_found(self) -> None:
        # The endpoint is a document reader, never a file reader: an escape out of tasks/ must be
        # indistinguishable from a missing document, and must not disclose the file.
        secret = self.tmp / "secret.json"
        secret.write_text(json.dumps({"schema": "ar-task-document/v1"}), encoding="utf-8")
        with TestClient(self.app) as client:
            response = client.get(
                "/api/task-document", params={"path": "repo/master/../../../secret.json"}
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "task document not found")


class OperatorInboxDismissTests(_AppFixture):
    """Dismissing an inbox row that is already gone is a 404, not a silent success."""

    def test_dismissing_an_unknown_entry_is_not_found(self) -> None:
        with TestClient(self.app) as client:
            response = client.post("/api/operator-inbox/never-existed/dismiss")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"status": "not-found", "entryId": "never-existed"})


class GateDecisionHelperTests(unittest.TestCase):
    """The gate-decision recorder decides a gate the request named no lifecycle for.

    Whether a decision names anything to decide is a question about the request, and
    ``evaluate_action`` is the one place it is answered (400 ``missing-target``, asserted over HTTP
    in ``test_serving.ActionGateTests``). What is left here is the recorder's own arm: a decision
    addressed by gate id alone, which the router reaches for a workspace-level ``cancel``.
    """

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)
        self.config = _config(self.tmp)

    def test_an_addressed_gate_id_decision_records_and_answers_with_the_gate(self) -> None:
        store = GateStore(observer_root(self.config))
        store.append(
            create_gate(
                "agent-question",
                gate_id="G1",
                now=FRESH_GATE_TS,
                anchor=GateAnchor(lifecycle_id=None),
            )
        )
        response = _gate_decision_response(
            self.config,
            ActionOutcome(202, {"status": "received"}),
            GateDecisionIntent(lifecycle_id=None, decision="cancel", gate_id="G1", note="cleared"),
            target=None,
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(json.loads(bytes(response.body))["gate"]["state"], "cancelled")


class LandedCleanupRaceTests(_AppFixture):
    """A landed row that disappears mid-cleanup is reported as skipped, never counted as closed."""

    def test_a_row_deleted_while_it_is_being_closed_is_skipped_not_counted(self) -> None:
        # The catalog file is shared with the MCP process. `retire_entry` kills the pane and only
        # then writes the terminal mark, so a concurrent writer that replaces the catalog in that
        # window makes `mark_retired` find nothing. Killing the pane is the real interleaving point,
        # so that is where the competing write is injected.
        self.register(_terminal("landed-1", cwd=self.tmp, status="landed"))
        self.register(_terminal("landed-2", cwd=self.tmp, status="landed"))

        def evict(session_id: str) -> None:
            if session_id == "landed-1":
                self.catalog.path.write_text("[]", encoding="utf-8")

        self.host.on_terminate = evict

        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/landed-cleanup",
                json={"sessionIds": ["landed-1", "landed-2"]},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["closed"], 0)
        self.assertEqual(body["closedSessions"], [])
        self.assertEqual(
            body["skippedSessions"],
            [
                {"session": "landed-1", "reason": "unknown-session"},
                {"session": "landed-2", "reason": "unknown-session"},
            ],
        )
        # Nothing was closed, so nothing may be announced as retired.
        self.assertEqual(
            [event.kind for event in EventStore(observer_root(self.config)).read(None)], []
        )


class AttachTaskRoleTests(_AppFixture):
    """A hand-opened harness must name a role before occupying a task document."""

    def setUp(self) -> None:
        super().setUp()
        _write_leaf_task(self.tmp, repo="repo", master="master", doc_id="leaf-1")

    def test_a_hand_opened_harness_without_a_role_is_refused_and_stays_unbound(self) -> None:
        # No spawn_role (it was not dispatched) and no persisted seat role: guessing "chat" here is
        # what would silently give an untyped seat a leaf binding, so the claim is refused instead.
        self.register(_harness_row("hand-opened", cwd=self.tmp))
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/hand-opened/attach-task",
                json={
                    "taskDocumentRef": {
                        "repository": "repo",
                        "path": "master/leaf-1.json",
                    }
                },
            )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["status"], "role-required")
        self.assertEqual(
            body["taskDocumentRef"],
            {"repository": "repo", "path": "master/leaf-1.json"},
        )
        self.assertEqual(body["detail"], "role is required for a hand-opened harness session")
        entry = self.catalog.get("hand-opened")
        assert entry is not None
        self.assertIsNone(entry.task_document_ref)

    def test_the_same_seat_binds_once_it_declares_a_role(self) -> None:
        self.register(_harness_row("hand-opened", cwd=self.tmp))
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/hand-opened/attach-task",
                json={
                    "taskDocumentRef": {
                        "repository": "repo",
                        "path": "master/leaf-1.json",
                    },
                    "role": "worker",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["seatRole"], "worker")
        entry = self.catalog.get("hand-opened")
        assert entry is not None
        self.assertEqual(
            entry.task_document_ref,
            TaskDocumentRef(repository="repo", path="master/leaf-1.json"),
        )
        self.assertEqual(entry.binding_role, "worker")


class PasteRouteTests(_AppFixture):
    """``POST /api/terminal/{session}/paste``: who may be pasted into, and how a harness answers."""

    def test_a_running_row_whose_pane_is_gone_is_unknown_not_pasted_into(self) -> None:
        # The catalog still says running; liveness is re-proven per paste so the text is never
        # handed to a pane that no longer exists.
        self.register(_terminal("stale", cwd=self.tmp), pane_alive=False)
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/stale/paste", json={"text": "hello", "submit": True}
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"status": "unknown-session"})

    def test_a_harness_draft_is_refused_rather_than_typed_into_the_pane(self) -> None:
        # A protocol harness takes whole correlated messages only; an unsubmitted draft has no
        # protocol shape, so it stays on the attached terminal surface instead of degrading to keys.
        self.register(
            _harness_row("chat-1", cwd=self.tmp, control_endpoint=self.tmp / "no-such-control.sock")
        )
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/chat-1/paste", json={"text": "draft only", "submit": False}
            )
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body["status"], "draft-not-submitted")
        self.assertEqual(body["session"], "chat-1")

    def test_a_submission_to_an_unreachable_bridge_is_unconfirmed_never_delivered(self) -> None:
        # The socket is gone, so the client cannot know whether bytes were seen: the answer must
        # claim submission and refuse to claim delivery.
        self.register(
            _harness_row("chat-1", cwd=self.tmp, control_endpoint=self.tmp / "no-such-control.sock")
        )
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/chat-1/paste", json={"text": "please run it", "submit": True}
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "unconfirmed")
        self.assertFalse(body["delivered"])
        self.assertTrue(body["submitted"])
        self.assertTrue(body["entryId"])
        self.assertIn("control", body["detail"])

    def test_a_legacy_harness_row_with_no_adapter_is_unsupported(self) -> None:
        self.register(_harness_row("legacy", cwd=self.tmp))
        with TestClient(self.app) as client:
            response = client.post(
                "/api/terminal/legacy/paste", json={"text": "hello", "submit": True}
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["status"], "unsupported")


class HarnessSubmissionTests(unittest.IsolatedAsyncioTestCase):
    """The submit mapping against a real control bridge: acceptance decides `delivered`.

    ``_harness_submit_response`` is driven directly (in a worker thread, as the sync route is) so
    the bridge's asyncio server keeps servicing the socket -- the same shape the control-plane
    suites use. The adapter at the far edge is the only double.
    """

    async def asyncSetUp(self) -> None:
        self.adapter = FakeControlAdapter(harness="claude")
        self.harness = make_harness(self, self.adapter, "chat-live", harness="claude")
        await self.harness.start()
        self.addAsyncCleanup(self.harness.stop)

    def _entry(self) -> TerminalCatalogEntry:
        entry = self.harness.catalog.get(self.harness.session)
        assert entry is not None
        return entry

    async def _submit(self, text: str, *, delivery_id: str) -> dict[str, Any]:
        response = await asyncio.to_thread(
            _harness_submit_response,
            self._entry(),
            self.harness.session,
            TerminalPasteRequest(text=text, submit=True),
            delivery_id=delivery_id,
        )
        self.assertEqual(response.status_code, 200)
        return cast(dict[str, Any], json.loads(bytes(response.body)))

    async def test_an_accepted_submission_reaches_the_adapter_as_one_correlated_message(
        self,
    ) -> None:
        body = await self._submit("run the suite", delivery_id="delivery-1")

        self.assertEqual(body["status"], "delivered")
        self.assertTrue(body["delivered"])
        self.assertTrue(body["submitted"])
        self.assertEqual(body["acceptance"], "immediate")
        self.assertEqual(body["entryId"], "delivery-1")
        # The text crossed the socket whole, under the delivery id the cockpit was handed back.
        self.assertEqual(
            [request.text for request in self.adapter.submit_requests], ["run the suite"]
        )
        self.assertEqual(self.adapter.submit_requests[0].request_id, "delivery-1")

    async def test_a_rejected_acceptance_is_reported_unconfirmed_even_though_it_was_sent(
        self,
    ) -> None:
        # The bytes reached the harness, so `submitted` stays true -- but only immediate/queued
        # acceptance is allowed to claim delivery.
        self.adapter.next_acceptance = "rejected"
        body = await self._submit("run it anyway", delivery_id="delivery-2")

        self.assertEqual(body["status"], "unconfirmed")
        self.assertFalse(body["delivered"])
        self.assertTrue(body["submitted"])
        self.assertEqual(body["acceptance"], "rejected")
        self.assertEqual(len(self.adapter.submit_requests), 1)

    async def test_a_submission_queued_behind_a_running_turn_still_counts_as_delivered(
        self,
    ) -> None:
        # The operator pasted while the harness was mid-turn: the control bridge owns the
        # submission and will dispatch it, so the cockpit is told delivered, not unconfirmed.
        await drive_activity(self.harness, "running")
        body = await self._submit("queue it behind the turn", delivery_id="delivery-3")

        self.assertEqual(body["status"], "delivered")
        self.assertTrue(body["delivered"])
        self.assertEqual(body["acceptance"], "queued")


class TerminateRouteTests(_AppFixture):
    """``POST /api/terminal/{session}/terminate``: nothing to kill, and a bridge that will not stop."""

    def test_terminating_a_session_nobody_has_heard_of_is_not_found(self) -> None:
        with TestClient(self.app) as client:
            response = client.post("/api/terminal/ghost/terminate")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"status": "unknown-session"})
        self.assertEqual(self.host.terminated, [])

    def test_a_failed_graceful_stop_is_reported_and_the_pane_is_still_killed(self) -> None:
        # A controlled harness whose socket is gone must not strand the tmux process: the graceful
        # stop failure becomes evidence on the response, and the kill happens regardless.
        self.register(
            _harness_row("chat-1", cwd=self.tmp, control_endpoint=self.tmp / "no-such-control.sock")
        )
        with TestClient(self.app) as client:
            response = client.post("/api/terminal/chat-1/terminate")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "terminated")
        self.assertEqual(body["tmuxName"], "ar-chat-1")
        self.assertIn("controlStopDetail", body)
        self.assertTrue(body["controlStopDetail"])
        self.assertEqual(self.host.terminated, ["ar-chat-1"])
        entry = self.catalog.get("chat-1")
        assert entry is not None
        self.assertEqual(entry.status, "terminated")

    def test_a_plain_terminal_terminates_without_a_control_stop_note(self) -> None:
        self.register(_terminal("term-1", cwd=self.tmp))
        with TestClient(self.app) as client:
            response = client.post("/api/terminal/term-1/terminate")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("controlStopDetail", response.json())


class RenameRouteTests(_AppFixture):
    """``POST /api/terminal/{session}/rename``: identity text only, and only for a live row."""

    def test_renaming_freezes_the_original_label_and_announces_the_rename(self) -> None:
        self.register(_terminal("term-1", cwd=self.tmp, label="Terminal 1"))
        with TestClient(self.app) as client:
            response = client.post("/api/terminal/term-1/rename", json={"label": "Release cut"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "session": "term-1",
                "status": "renamed",
                "label": "Release cut",
                "spawnedLabel": "Terminal 1",
            },
        )
        entry = self.catalog.get("term-1")
        assert entry is not None
        self.assertEqual(entry.label, "Release cut")
        self.assertEqual(entry.spawned_label, "Terminal 1")

        events = EventStore(observer_root(self.config)).read(None)
        renames = [event for event in events if event.kind == "seat.renamed"]
        self.assertEqual(len(renames), 1)
        self.assertEqual(renames[0].sessionId, "term-1")
        self.assertEqual(renames[0].data["label"], "Release cut")
        self.assertEqual(renames[0].data["spawnedLabel"], "Terminal 1")

    def test_renaming_a_harness_seat_never_touches_its_immutable_seat_role(self) -> None:
        self.register(_harness_row("chat-1", cwd=self.tmp, label="Worker"))
        self.catalog.upsert(
            self.catalog.get("chat-1").with_task_binding(  # type: ignore[union-attr]
                TaskDocumentRef(repository="repo", path="master/leaf-1.json"), "worker"
            )
        )
        with TestClient(self.app) as client:
            response = client.post("/api/terminal/chat-1/rename", json={"label": "Worker B"})
        self.assertEqual(response.status_code, 200)
        entry = self.catalog.get("chat-1")
        assert entry is not None
        self.assertEqual(entry.label, "Worker B")
        self.assertEqual(entry.binding_role, "worker")
        self.assertEqual(
            entry.task_document_ref,
            TaskDocumentRef(repository="repo", path="master/leaf-1.json"),
        )

    def test_renaming_an_unknown_session_is_not_found(self) -> None:
        with TestClient(self.app) as client:
            response = client.post("/api/terminal/ghost/rename", json={"label": "Nope"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"status": "unknown-session"})

    def test_a_terminated_seat_cannot_be_renamed(self) -> None:
        # Retirement is terminal: relabelling a closed row would rewrite history in the archive.
        self.register(_terminal("gone", cwd=self.tmp, status="terminated", label="Old"))
        with TestClient(self.app) as client:
            response = client.post("/api/terminal/gone/rename", json={"label": "New"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"status": "unknown-session"})
        entry = self.catalog.get("gone")
        assert entry is not None
        self.assertEqual(entry.label, "Old")
        self.assertEqual(
            [event.kind for event in EventStore(observer_root(self.config)).read(None)], []
        )


if __name__ == "__main__":
    unittest.main()
