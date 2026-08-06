from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents_remember.controlplane.attention_dismissals import (
    AttentionDismissalRecord,
    AttentionDismissalStore,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.records import GateAnchor, create_gate
from agents_remember.controlplane.store import GateStore
from agents_remember.observer.paths import observer_logs_root
from agents_remember.observer.projection import ActionAvailability, WorkspaceProjection
from agents_remember.serving.actions import (
    ActionEvaluationContext,
    DismissalIntent,
    GateDecisionIntent,
    evaluate_action,
)
from agents_remember.serving.app import create_app
from agents_remember.serving.projector import ProjectionCadence
from fastapi.testclient import TestClient
from test_serving import (
    _FRESH_GATE_TS,
    _FRESH_GATE_TS_LATER,
    _TS,
    _config,
    _enclosure,
    _lifecycle,
    _projection,
)


class ActionGateTests(unittest.TestCase):
    """Slice 6b: gate-decision verbs carry an intent (pure) and the router records it."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def test_evaluate_action_emits_gate_intent_for_verbs(self) -> None:
        outcome = evaluate_action(
            _projection(),
            "approve",
            "L1",
            ActionEvaluationContext(actor="developer", now=_TS, gate_id="G1", note="Looks good."),
        )
        self.assertEqual(outcome.status_code, 202)
        self.assertEqual(
            outcome.gate_decision,
            GateDecisionIntent(
                lifecycle_id="L1",
                decision="approve",
                gate_id="G1",
                note="Looks good.",
            ),
        )

    def test_evaluate_action_requires_rejection_reason(self) -> None:
        outcome = evaluate_action(
            _projection(),
            "reject",
            "L1",
            ActionEvaluationContext(actor="developer", now=_TS, gate_id="G1"),
        )
        self.assertEqual(outcome.status_code, 400)
        self.assertEqual(outcome.body["status"], "missing-rejection-reason")
        self.assertIsNone(outcome.gate_decision)

    def test_evaluate_action_allows_gate_id_only_cancel(self) -> None:
        outcome = evaluate_action(
            _projection(),
            "cancel",
            None,
            ActionEvaluationContext(
                actor="developer", now=_TS, gate_id="G1", note="Cleared from attention queue."
            ),
        )
        self.assertEqual(outcome.status_code, 202)
        self.assertEqual(
            outcome.gate_decision,
            GateDecisionIntent(
                lifecycle_id=None,
                decision="cancel",
                gate_id="G1",
                note="Cleared from attention queue.",
            ),
        )

    def test_evaluate_action_transition_keeps_4b_skeleton(self) -> None:
        # a non-gate action on an unknown target stays the 4b no-mutation skeleton
        outcome = evaluate_action(
            _projection(), "integrate", "nope", ActionEvaluationContext(actor="developer", now=_TS)
        )
        self.assertIsNone(outcome.gate_decision)
        self.assertEqual(outcome.status_code, 404)

    def test_api_action_approve_records_developer_decision(self) -> None:
        store = GateStore(observer_logs_root(self.tmp))
        store.append(
            create_gate(
                "closeout-approval",
                gate_id="G1",
                now=_FRESH_GATE_TS,
                anchor=GateAnchor(lifecycle_id="L1"),
            )
        )
        app = create_app(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        with TestClient(app) as client:
            response = client.post(
                "/api/actions/reject",
                json={"target": "L1", "gateId": "G1", "note": "Needs another pass."},
            )
        self.assertEqual(response.status_code, 202)
        gate = response.json()["gate"]
        self.assertEqual(gate["state"], "rejected")
        self.assertEqual(gate["decidedBy"], "developer")  # un-forgeable vs. the agent's model path
        self.assertEqual(gate["decidedVia"], "dashboard")
        self.assertEqual(store.current("L1")["G1"].state, "rejected")
        self.assertEqual(store.current("L1")["G1"].decisionNote, "Needs another pass.")

    def test_api_action_with_stale_gate_id_is_409(self) -> None:
        store = GateStore(observer_logs_root(self.tmp))
        store.append(
            create_gate(
                "agent-question",
                gate_id="A",
                now=_FRESH_GATE_TS,
                anchor=GateAnchor(lifecycle_id="L1"),
            )
        )
        store.append(
            create_gate(
                "closeout-approval",
                gate_id="B",
                now=_FRESH_GATE_TS_LATER,
                anchor=GateAnchor(lifecycle_id="L1"),
            )
        )
        app = create_app(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        with TestClient(app) as client:
            response = client.post("/api/actions/approve", json={"target": "L1", "gateId": "A"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["status"], "stale-gate")
        self.assertEqual(store.current("L1")["A"].state, "open")
        self.assertEqual(store.current("L1")["B"].state, "open")

    def test_api_action_approve_without_open_gate_is_409(self) -> None:
        app = create_app(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        with TestClient(app) as client:
            response = client.post("/api/actions/approve", json={"target": "L1"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["status"], "no-open-gate")

    def test_api_action_cancel_deletes_gate(self) -> None:
        store = GateStore(observer_logs_root(self.tmp))
        store.append(
            create_gate(
                "agent-question",
                gate_id="G1",
                now=_FRESH_GATE_TS,
                anchor=GateAnchor(lifecycle_id="L1"),
            )
        )
        app = create_app(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        with TestClient(app) as client:
            response = client.post(
                "/api/actions/cancel",
                json={"target": "L1", "gateId": "G1", "note": "Dismissed."},
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["gate"]["state"], "cancelled")
        self.assertEqual(store.current("L1"), {})

    def test_api_action_naming_neither_a_lifecycle_nor_a_gate_is_missing_target(self) -> None:
        # The single place an unaddressed gate decision is refused. Everything downstream of
        # `evaluate_action` -- `_recorded_gate_decision`, `_gate_decision_response` -- is written
        # against this guard holding, so it is asserted on the wire and not only as a unit.
        app = create_app(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        with TestClient(app) as client:
            response = client.post("/api/actions/cancel", json={"note": "Nothing named."})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {
                "status": "missing-target",
                "detail": "gate decisions require a lifecycle target unless cancelling a gate id",
                "action": "cancel",
            },
        )

    def test_api_action_cancel_deletes_workspace_gate_by_id_only(self) -> None:
        store = GateStore(observer_logs_root(self.tmp))
        store.append(
            create_gate(
                "agent-question",
                gate_id="G1",
                now=_FRESH_GATE_TS,
                anchor=GateAnchor(lifecycle_id=None),
            )
        )
        app = create_app(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        with TestClient(app) as client:
            response = client.post(
                "/api/actions/cancel",
                json={"gateId": "G1", "note": "Cleared from attention queue."},
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["gate"]["state"], "cancelled")
        self.assertEqual(store.current(None), {})

    def test_api_operator_inbox_records_developer_response(self) -> None:
        app = create_app(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        with TestClient(app) as client:
            response = client.post(
                "/api/operator-inbox",
                json={
                    "lifecycleId": "L1",
                    "agentId": "agent-a",
                    "gateId": "G1",
                    "ask": "Continue?",
                    "response": "Yes, proceed.",
                },
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["operation"], "operator_inbox_post")
        self.assertEqual(body["state"], "pending")

        store = OperatorInboxStore(observer_logs_root(self.tmp))
        entries = store.list_pending(lifecycle_id="L1", agent_id="agent-a")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].gateId, "G1")
        self.assertEqual(entries[0].ask, "Continue?")
        self.assertEqual(entries[0].response, "Yes, proceed.")
        self.assertEqual(entries[0].createdBy, "developer")
        self.assertEqual(entries[0].createdVia, "dashboard")

    def test_api_operator_inbox_dismiss_deletes_entry(self) -> None:
        app = create_app(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        with TestClient(app) as client:
            posted = client.post(
                "/api/operator-inbox",
                json={
                    "lifecycleId": "L1",
                    "gateId": "G1",
                    "ask": "Continue?",
                    "response": "Yes, proceed.",
                },
            )
            entry_id = posted.json()["entryId"]
            response = client.post(f"/api/operator-inbox/{entry_id}/dismiss")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "dismissed")
        self.assertEqual(OperatorInboxStore(observer_logs_root(self.tmp)).read(), [])

    def test_api_operator_inbox_requires_address(self) -> None:
        app = create_app(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        with TestClient(app) as client:
            response = client.post(
                "/api/operator-inbox",
                json={"ask": "Continue?", "response": "Yes, proceed."},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "bad-address")


class ActionDismissTests(unittest.TestCase):
    """Leaf-28 S5.2: the ``dismiss`` verb records compact lifecycle acknowledgements,
    and pairs a ``gate-open`` dismissal with gate deletion."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def test_evaluate_action_emits_dismissal_intent(self) -> None:
        outcome = evaluate_action(
            _projection(),
            "dismiss",
            "L1",
            ActionEvaluationContext(
                actor="developer",
                now=_TS,
                item_id="awaiting-developer:L1",
                kind="awaiting-developer",
            ),
        )
        self.assertEqual(outcome.status_code, 202)
        self.assertIsNone(outcome.gate_decision)
        self.assertEqual(
            outcome.dismissal,
            DismissalIntent(
                item_id="awaiting-developer:L1",
                dismissed_at=_TS,
                kind="awaiting-developer",
                lifecycle_id="L1",
                gate_id=None,
                note=None,
            ),
        )

    def test_evaluate_action_dismiss_requires_item_id(self) -> None:
        outcome = evaluate_action(
            _projection(),
            "dismiss",
            "L1",
            ActionEvaluationContext(actor="developer", now=_TS, kind="blocked-gate"),
        )
        self.assertEqual(outcome.status_code, 400)
        self.assertEqual(outcome.body["status"], "missing-item")
        self.assertIsNone(outcome.dismissal)

    def test_evaluate_action_dismiss_requires_lifecycle_for_non_gate_item(self) -> None:
        outcome = evaluate_action(
            _projection(),
            "dismiss",
            None,
            ActionEvaluationContext(
                actor="developer", now=_TS, item_id="provider-down:cgc", kind="provider-down"
            ),
        )
        self.assertEqual(outcome.status_code, 400)
        self.assertEqual(outcome.body["status"], "missing-lifecycle")
        self.assertIsNone(outcome.dismissal)

    def test_evaluate_action_allows_actionable_drift_without_lifecycle(self) -> None:
        outcome = evaluate_action(
            _projection(),
            "dismiss",
            None,
            ActionEvaluationContext(
                actor="developer",
                now=_TS,
                item_id="actionable-drift:agents-remember:main",
                kind="actionable-drift",
            ),
        )
        self.assertEqual(outcome.status_code, 202)
        self.assertEqual(
            outcome.dismissal,
            DismissalIntent(
                item_id="actionable-drift:agents-remember:main",
                dismissed_at=_TS,
                kind="actionable-drift",
                lifecycle_id=None,
                gate_id=None,
                note=None,
            ),
        )

    def test_attention_store_upserts_and_prunes_lifecycle_rows(self) -> None:
        store = AttentionDismissalStore(observer_logs_root(self.tmp))
        store.dismiss(
            AttentionDismissalRecord(
                itemId="stale-session:L1",
                kind="stale-session",
                lifecycleId="L1",
                dismissedAt=_TS,
            )
        )
        store.dismiss(
            AttentionDismissalRecord(
                itemId="stale-session:L1",
                kind="stale-session",
                lifecycleId="L1",
                dismissedAt="2026-06-14T10:01:00Z",
            )
        )
        self.assertEqual(len(store.read()), 1)
        self.assertEqual(store.current()["stale-session:L1"].dismissedAt, "2026-06-14T10:01:00Z")

        self.assertEqual(store.prune_lifecycles(set()), 1)
        self.assertEqual(store.current(), {})
        # Pruning to nothing leaves an EMPTY FILE, never a missing one (260731-EFA-L5 R5).
        # This line used to assert the log was unlinked; that unlink is the defect the leaf
        # removed. `dismiss` is a whole-file read-modify-write reached from the dashboard's
        # HTTP dismiss route, so a concurrent dismisser holding a handle across the unlink
        # wrote into an inode with no remaining links and the dismissal vanished with the
        # file -- no error, no torn line. The proof that the prune actually happened is
        # unweakened and now reads as emptiness: zero rows through the reader, zero bytes on
        # disk, the same one row having been there a moment ago.
        self.assertEqual(store.read(), [])
        self.assertTrue(store.log_path().is_file())
        self.assertEqual(store.log_path().read_bytes(), b"")

    def test_attention_store_keeps_actionable_drift_current_acknowledgements(self) -> None:
        store = AttentionDismissalStore(observer_logs_root(self.tmp))
        store.dismiss(
            AttentionDismissalRecord(
                itemId="actionable-drift:agents-remember:main",
                kind="actionable-drift",
                dismissedAt=_TS,
            )
        )
        self.assertEqual(store.prune_lifecycles(set()), 0)
        self.assertIn("actionable-drift:agents-remember:main", store.current())

    def test_attention_store_prune_compacts_legacy_duplicate_live_rows(self) -> None:
        store = AttentionDismissalStore(observer_logs_root(self.tmp))
        older = AttentionDismissalRecord(
            itemId="stale-session:L1",
            kind="stale-session",
            lifecycleId="L1",
            dismissedAt=_TS,
        )
        newer = AttentionDismissalRecord(
            itemId="stale-session:L1",
            kind="stale-session",
            lifecycleId="L1",
            dismissedAt="2026-06-14T10:01:00Z",
        )
        path = store.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            older.model_dump_json(by_alias=True, exclude_none=True)
            + "\n"
            + newer.model_dump_json(by_alias=True, exclude_none=True)
            + "\n",
            encoding="utf-8",
        )

        self.assertEqual(store.prune_lifecycles({"L1"}), 1)
        self.assertEqual(len(store.read()), 1)
        self.assertEqual(store.current()["stale-session:L1"].dismissedAt, "2026-06-14T10:01:00Z")

    def test_api_action_dismiss_records_lifecycle_acknowledgement(self) -> None:
        app = create_app(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        with TestClient(app) as client:
            response = client.post(
                "/api/actions/dismiss",
                json={
                    "target": "L1",
                    "itemId": "stale-session:L1",
                    "kind": "stale-session",
                },
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "received")
        dismissals = AttentionDismissalStore(observer_logs_root(self.tmp)).current()
        self.assertEqual(dismissals["stale-session:L1"].lifecycleId, "L1")

    def test_api_action_dismiss_records_actionable_drift_acknowledgement(self) -> None:
        app = create_app(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        with TestClient(app) as client:
            response = client.post(
                "/api/actions/dismiss",
                json={
                    "itemId": "actionable-drift:agents-remember:main",
                    "kind": "actionable-drift",
                },
            )
        self.assertEqual(response.status_code, 202)
        dismissals = AttentionDismissalStore(observer_logs_root(self.tmp)).current()
        self.assertIsNone(dismissals["actionable-drift:agents-remember:main"].lifecycleId)

    def test_api_action_dismiss_scoped_to_nothing_is_missing_lifecycle(self) -> None:
        # The single place an unscoped acknowledgement is refused. `_dismissal_response` writes the
        # row for everything that is not a gate cancel, so an unscoped item getting past this guard
        # would put an un-prunable entry in the dismissal store; assert the refusal on the wire.
        app = create_app(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        with TestClient(app) as client:
            response = client.post(
                "/api/actions/dismiss",
                json={"itemId": "provider-down:cgc", "kind": "provider-down"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "missing-lifecycle")
        self.assertEqual(AttentionDismissalStore(observer_logs_root(self.tmp)).current(), {})

    def test_api_action_dismiss_gate_open_also_cancels_gate(self) -> None:
        store = GateStore(observer_logs_root(self.tmp))
        store.append(
            create_gate(
                "agent-question",
                gate_id="G1",
                now=_FRESH_GATE_TS,
                anchor=GateAnchor(lifecycle_id="L1"),
            )
        )
        app = create_app(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        with TestClient(app) as client:
            response = client.post(
                "/api/actions/dismiss",
                json={
                    "target": "L1",
                    "itemId": "gate:G1",
                    "kind": "gate-open",
                    "gateId": "G1",
                },
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["gate"]["state"], "cancelled")  # gate cancel still fires
        self.assertEqual(store.current("L1"), {})  # cancel deletes the gate
        dismissals = AttentionDismissalStore(observer_logs_root(self.tmp)).current()
        self.assertNotIn("gate:G1", dismissals)  # the deleted gate is the consumed source

    def test_api_action_dismiss_missing_gate_is_already_consumed(self) -> None:
        # A gate-open dismiss whose gate already vanished must not 500: the cancel KeyError
        # is swallowed because the missing gate means the source item is already gone.
        app = create_app(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        with TestClient(app) as client:
            response = client.post(
                "/api/actions/dismiss",
                json={"target": "L1", "itemId": "gate:gone", "kind": "gate-open", "gateId": "gone"},
            )
        self.assertEqual(response.status_code, 202)
        self.assertNotIn("gate", response.json())  # no gate payload when the cancel found nothing
        dismissals = AttentionDismissalStore(observer_logs_root(self.tmp)).current()
        self.assertNotIn("gate:gone", dismissals)


class ActionTests(unittest.TestCase):
    """The POST skeleton's availability + attribution mapping (``serving.actions``)."""

    def _projection(self) -> WorkspaceProjection:
        blocked = _lifecycle("L1", state="blocked").model_copy(
            update={"actions": [ActionAvailability(action="resume", enabled=True)]}
        )
        running = _lifecycle("L2", state="running").model_copy(
            update={
                "actions": [
                    ActionAvailability(
                        action="resume",
                        enabled=False,
                        disabledReason="lifecycle is not blocked",
                        nextSafeAction="resume becomes safe once the gate is resolved",
                    )
                ]
            }
        )
        return _projection(lifecycles=(blocked, running))

    def test_enabled_action_is_received_with_attribution(self) -> None:
        outcome = evaluate_action(
            self._projection(), "resume", "L1", ActionEvaluationContext(actor="developer", now=_TS)
        )
        self.assertEqual(outcome.status_code, 202)
        self.assertEqual(outcome.body["status"], "received")
        intent = outcome.body["intent"]
        self.assertEqual(intent["source"], "dashboard")
        self.assertEqual(intent["actor"], "developer")
        self.assertEqual(intent["action"], "resume")
        self.assertEqual(intent["target"], "L1")
        self.assertEqual(intent["ts"], _TS)

    def test_disabled_action_is_conflict_with_reason(self) -> None:
        outcome = evaluate_action(
            self._projection(), "resume", "L2", ActionEvaluationContext(actor="developer", now=_TS)
        )
        self.assertEqual(outcome.status_code, 409)
        self.assertEqual(outcome.body["status"], "disabled")
        self.assertEqual(outcome.body["detail"], "lifecycle is not blocked")
        self.assertEqual(
            outcome.body["nextSafeAction"], "resume becomes safe once the gate is resolved"
        )

    def test_unknown_action_is_conflict(self) -> None:
        outcome = evaluate_action(
            self._projection(),
            "frobnicate",
            "L1",
            ActionEvaluationContext(actor="developer", now=_TS),
        )
        self.assertEqual(outcome.status_code, 409)
        self.assertEqual(outcome.body["status"], "unavailable")

    def test_unknown_target_is_not_found(self) -> None:
        outcome = evaluate_action(
            self._projection(), "resume", "ZZZ", ActionEvaluationContext(actor="developer", now=_TS)
        )
        self.assertEqual(outcome.status_code, 404)

    def test_enclosure_target_resolves(self) -> None:
        enclosure = _enclosure("e1").model_copy(
            update={"actions": [ActionAvailability(action="integrate", enabled=True)]}
        )
        outcome = evaluate_action(
            _projection(enclosures=(enclosure,)),
            "integrate",
            "e1",
            ActionEvaluationContext(actor="developer", now=_TS),
        )
        self.assertEqual(outcome.status_code, 202)


class ActionEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_post_unknown_target_returns_404(self) -> None:
        app = create_app(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        with TestClient(app) as client:
            response = client.post("/api/actions/resume", json={"target": "nope"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["status"], "unknown-target")

    def test_post_rejects_unknown_actor(self) -> None:
        app = create_app(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        with TestClient(app) as client:
            response = client.post("/api/actions/resume", json={"target": "x", "actor": "intruder"})
        self.assertEqual(response.status_code, 422)
