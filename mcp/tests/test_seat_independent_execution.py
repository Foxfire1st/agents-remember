"""Seat-independent task-execution fallback (L16): ambient declared-caller paths.

The plane injects a hosted seat into AR-launched processes; external/ambient
agents have none. L16 lets those callers supply their structural identity as
request data (``caller``) which the consuming mechanism validates against the
same policy a hosted seat would face. These tests prove the ambient path works
end-to-end for the closeout queue lifecycle and for the structural gate tools,
and that hosted seats remain authoritative when present.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from agents_remember.application import closeout_queue as public_queue
from agents_remember.application.structural import gate_tools as gates
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import write_task_doc
from agents_remember.worktrees.queue.closeout_queue import (
    CloseoutQueueError,
    CloseoutQueueRequest,
)
from agents_remember.worktrees.queue.closeout_queue_lifecycle import (
    claim_queue_candidate_for_closeout,
)
from test_closeout_queue import (
    LEAF_A,
    LEAF_B,
    MASTER_A,
    MASTER_B,
    SPRINT,
    QueueFixture,
    _grade,
)


class AmbientCloseoutQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _ambient(
        self,
        fixture: QueueFixture,
        action: str,
        caller: DeclaredCaller,
        **values: Any,
    ) -> dict[str, Any]:
        """Drive the public queue boundary as an ambient caller (no plane seat)."""
        candidate = cast(TaskDocumentRef | None, values.get("candidate"))
        blocker = cast(TaskDocumentRef | None, values.get("blocker"))
        payload: dict[str, Any] = {
            "action": action,
            "sprint_task_document_ref": SPRINT.model_dump(),
            "caller": caller.model_dump(),
        }
        if action != "status":
            stable_request_id = cast(str | None, values.get("request_id")) or (
                fixture.next_request_id(action)
            )
            payload["request_id"] = stable_request_id
            payload["expected_revision"] = fixture.request_revision(stable_request_id)
        for key, value in (
            ("contract_path", values.get("contract_path")),
            ("grade", values.get("grade")),
            ("admission", values.get("admission")),
            ("blocker_judgment_id", values.get("blocker_judgment_id")),
            ("rationale", values.get("rationale")),
        ):
            if value is not None:
                payload[key] = value
        if candidate is not None:
            payload["candidate_task_document_ref"] = candidate.model_dump()
        if blocker is not None:
            payload["blocker_master_ref"] = blocker.model_dump()
        request = CloseoutQueueRequest.model_validate(payload)
        with mock.patch.object(
            public_queue,
            "resolve_ambient_seat",
            side_effect=public_queue.AmbientSeatError(
                "ambient-seat-unavailable", "no hosted identity"
            ),
        ):
            return public_queue.closeout_queue_tool(fixture.cfg, request)

    def test_ambient_declared_caller_runs_declare_grade_select_closeout(self) -> None:
        fixture = QueueFixture(Path(self.temp.name) / "ambient")
        orchestrator = DeclaredCaller(role="orchestrator", task_document_ref=SPRINT)
        manager = DeclaredCaller(role="manager", task_document_ref=MASTER_A)

        # status: the declared sprint orchestrator may read the frontier.
        status = self._ambient(fixture, "status", orchestrator)
        self.assertEqual(status["sprintTaskDocumentRef"], SPRINT.model_dump())
        self.assertEqual(status["state"], "projected")

        # declare: only the owning master manager may declare its leaf candidate.
        contract = fixture.contracts[MASTER_A]
        fixture.set_priority(LEAF_A, "normal")
        declared = self._ambient(
            fixture,
            "declare",
            manager,
            contract_path=contract.contract_path.as_posix(),
        )
        # Declared but ungraded candidates wait on an explicit register-backed
        # grade; the mechanism never fabricates one for an ambient caller either.
        self.assertEqual(declared["waiting"][0]["taskDocumentRef"], LEAF_A.model_dump())
        self.assertEqual(declared["waiting"][0]["reasons"], ["explicit-grade-required"])

        # set-grade + select: sprint orchestrator authority, resolved against
        # the canonical registers exactly as a seat caller would.
        graded = self._ambient(
            fixture,
            "set-grade",
            orchestrator,
            candidate=LEAF_A,
            grade=_grade("normal", LEAF_A),
        )
        self.assertEqual(graded["ready"][0]["taskDocumentRef"], LEAF_A.model_dump())
        selected = self._ambient(fixture, "select", orchestrator, candidate=LEAF_A)
        self.assertEqual(selected["inFlight"][0]["taskDocumentRef"], LEAF_A.model_dump())

        # The lifecycle claim binds the ambient-selected candidate (the claim is
        # queue-state-owned, not seat-gated).
        claimed = claim_queue_candidate_for_closeout(fixture.contracts[MASTER_A], "a" * 64)
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed.state, "closeout-in-flight")

    def test_ambient_declared_caller_acquires_an_atomic_blocker(self) -> None:
        fixture = QueueFixture(Path(self.temp.name) / "blocker", edge=True, atomic_b=True)
        orchestrator = DeclaredCaller(role="orchestrator", task_document_ref=SPRINT)
        fixture.declare(MASTER_A)
        queued = fixture.declare(MASTER_B)
        self.assertEqual(
            queued["waiting"][0]["reasons"],
            [f"predecessor-incomplete: {MASTER_A.key}", "atomic-blocker-required"],
        )
        completed = fixture.master_docs[MASTER_A].model_copy(update={"status": "Completed"})
        write_task_doc(fixture.tasks / "master-a", completed)
        acquired = self._ambient(
            fixture,
            "acquire-blocker",
            orchestrator,
            blocker=MASTER_B,
            rationale="Sequential framework block.",
        )
        self.assertEqual(acquired["ready"][0]["taskDocumentRef"], LEAF_B.model_dump())

    def test_ambient_declared_identity_is_validated_like_a_seat(self) -> None:
        fixture = QueueFixture(Path(self.temp.name) / "validated")
        fixture.set_priority(LEAF_A, "normal")
        worker = DeclaredCaller(role="worker", task_document_ref=LEAF_A)
        with self.assertRaisesRegex(CloseoutQueueError, "requires the sprint"):
            self._ambient(fixture, "status", worker)

        # A declared manager may not grade: only the sprint orchestrator can.
        manager = DeclaredCaller(role="manager", task_document_ref=MASTER_A)
        fixture.declare(MASTER_A)
        with self.assertRaisesRegex(CloseoutQueueError, "orchestrator authority"):
            self._ambient(
                fixture,
                "set-grade",
                manager,
                candidate=LEAF_A,
                grade=_grade("normal", LEAF_A),
            )

    def test_ambient_without_declared_caller_is_refused(self) -> None:
        fixture = QueueFixture(Path(self.temp.name) / "no-caller")
        with (
            mock.patch.object(
                public_queue,
                "resolve_ambient_seat",
                side_effect=public_queue.AmbientSeatError(
                    "ambient-seat-unavailable", "no hosted identity"
                ),
            ),
            self.assertRaisesRegex(CloseoutQueueError, "closeout-queue-caller-required"),
        ):
            public_queue.closeout_queue_tool(
                fixture.cfg,
                CloseoutQueueRequest(action="status", sprint_task_document_ref=SPRINT),
            )

    def test_hosted_seat_still_wins_over_a_matching_declared_caller(self) -> None:
        fixture = QueueFixture(Path(self.temp.name) / "seat")
        request = CloseoutQueueRequest(
            action="status",
            sprint_task_document_ref=SPRINT,
            caller=DeclaredCaller(role="orchestrator", task_document_ref=SPRINT),
        )
        with mock.patch.object(
            public_queue,
            "resolve_ambient_seat",
            return_value=SimpleNamespace(
                binding_role="orchestrator",
                binding_task_document_ref=SPRINT,
            ),
        ):
            response = public_queue.closeout_queue_tool(fixture.cfg, request)
        self.assertEqual(response["sprintTaskDocumentRef"], SPRINT.model_dump())

    def test_non_unavailable_seat_error_is_reraises_wrapped(self) -> None:
        """closeout_queue.py:45 -- a seat refusal other than ambient-unavailable is
        re-raised as a CloseoutQueueError carrying the seat status, never swallowed."""
        fixture = QueueFixture(Path(self.temp.name) / "seat-expired")
        request = CloseoutQueueRequest(action="status", sprint_task_document_ref=SPRINT)
        with (
            mock.patch.object(
                public_queue,
                "resolve_ambient_seat",
                side_effect=public_queue.AmbientSeatError("seat-expired", "identity revoked"),
            ),
            self.assertRaises(CloseoutQueueError) as ctx,
        ):
            public_queue.closeout_queue_tool(fixture.cfg, request)
        self.assertEqual(ctx.exception.status, "seat-expired")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class DeclaredCallerModelTests(unittest.TestCase):
    """models/declared_caller.py:38 -- the blank-role refusal."""

    def test_blank_role_is_refused(self) -> None:
        from pydantic import ValidationError  # noqa: PLC0415

        with self.assertRaises(ValidationError):
            DeclaredCaller(role="   ", task_document_ref=SPRINT)

    def test_role_is_stripped(self) -> None:
        caller = DeclaredCaller(role="  orchestrator  ", task_document_ref=SPRINT)
        self.assertEqual(caller.role, "orchestrator")


class AmbientStructuralGateFallbackTests(unittest.TestCase):
    """Committed behavioral tests for the gate-tools declared-caller fallback (F1).

    These exercise the real ``_context`` fallback branches and the duck-typed
    ``DeclaredGateCaller`` through the structural authorization -- not just the
    registration-to-payload forwarding covered by the wiring tests.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = cast(
            McpRuntimeConfig,
            SimpleNamespace(
                coordination_root=self.root,
                repositories={},
            ),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _context(self, declared: DeclaredCaller | None, *, seat=None):
        if seat is not None:
            seat_side_effect = None
            seat_return = seat
        else:
            seat_side_effect = gates.AmbientSeatError(
                "ambient-seat-unavailable", "no hosted identity"
            )
            seat_return = None
        with mock.patch.object(
            gates,
            "resolve_ambient_seat",
            side_effect=seat_side_effect,
            return_value=seat_return,
        ):
            return gates._context(self.config, environ={}, declared=declared)

    def test_missing_declared_caller_is_refused(self) -> None:
        """An ambient caller with no declared identity gets structural-caller-required."""
        with self.assertRaises(gates.AmbientSeatError) as ctx:
            self._context(None)
        self.assertEqual(ctx.exception.status, "structural-caller-required")

    def test_hosted_seat_conflicts_with_declared_caller(self) -> None:
        """A request-carried caller contradicting the hosted seat is refused."""
        sprint = TaskDocumentRef(repository="repo", path="sprint/task.json")
        master = TaskDocumentRef(repository="repo", path="master/task.json")
        declared = DeclaredCaller(role="manager", task_document_ref=master)
        seat = SimpleNamespace(binding_role="orchestrator", binding_task_document_ref=sprint)
        with self.assertRaises(gates.AmbientSeatError) as ctx:
            self._context(declared, seat=seat)
        self.assertEqual(ctx.exception.status, "structural-caller-conflict")

    def test_declared_caller_passes_authorize_child(self) -> None:
        """DeclaredGateCaller authorizes a gate on its own child document (F1)."""
        sprint = TaskDocumentRef(repository="repo", path="sprint/task.json")
        master = TaskDocumentRef(repository="repo", path="master/task.json")
        declared = DeclaredCaller(role="orchestrator", task_document_ref=sprint)
        _topology, resolver, caller = self._context(declared)
        # The duck-typed caller must satisfy the structural authorization the
        # seat path uses: an orchestrator may authorize a gate on a manager doc.
        resolver.authorize_child = mock.Mock()
        gates._authorize_gate_target(resolver, caller, master)
        resolver.authorize_child.assert_called_once_with(caller, document=master, role="manager")
        # A declared caller with a role that cannot decide is refused by the
        # same structural policy, not silently admitted.
        worker = DeclaredCaller(role="worker", task_document_ref=master)
        _topology, resolver, worker_caller = self._context(worker)
        with self.assertRaises(gates.StructuralSeatError):
            gates._authorize_gate_target(resolver, worker_caller, master)

    def test_lifecycle_gate_tool_uses_declared_caller(self) -> None:
        """The public lifecycle_gate tool raises on the declared document (F1)."""
        sprint = TaskDocumentRef(repository="repo", path="sprint/task.json")
        declared = DeclaredCaller(role="orchestrator", task_document_ref=sprint)
        topology = mock.Mock()
        topology.resolve.return_value = SimpleNamespace(
            ref=sprint, document=SimpleNamespace(id="sprint")
        )
        raw = {
            "ok": True,
            "gate": {"state": "open", "kind": "plan-approval"},
            "wait": {"state": "raised", "timedOut": False},
        }
        request = gates.StructuralLifecycleGateRequest(kind="plan-approval", caller=declared)
        with (
            mock.patch.object(
                gates,
                "resolve_ambient_seat",
                side_effect=gates.AmbientSeatError(
                    "ambient-seat-unavailable", "no hosted identity"
                ),
            ),
            mock.patch.object(gates, "TaskDocumentTopology", return_value=topology),
            mock.patch.object(gates, "raise_lifecycle_gate", return_value=raw),
        ):
            result = gates.structural_lifecycle_gate_tool(self.config, request)
        self.assertEqual(result["status"], "raised")
        self.assertEqual(result["role"], "orchestrator")
        self.assertEqual(result["taskDocumentRef"], sprint.model_dump())

    def test_context_reraises_non_unavailable_seat_error(self) -> None:
        """A seat error other than ambient-seat-unavailable is not swallowed.

        Only ``ambient-seat-unavailable`` opts into the declared-caller fallback;
        every other seat refusal must reach the caller unchanged (the same rule
        the closeout-queue boundary applies).
        """
        with (
            mock.patch.object(
                gates,
                "resolve_ambient_seat",
                side_effect=gates.AmbientSeatError("seat-expired", "identity revoked"),
            ),
            self.assertRaises(gates.AmbientSeatError) as ctx,
        ):
            gates._context(self.config, environ={}, declared=None)
        self.assertEqual(ctx.exception.status, "seat-expired")

    def test_hosted_seat_without_declared_caller_is_unchanged(self) -> None:
        """declared=None with a hosted seat returns the seat, no conflict check fires."""
        sprint = TaskDocumentRef(repository="repo", path="sprint/task.json")
        seat = SimpleNamespace(binding_role="orchestrator", binding_task_document_ref=sprint)
        _topology, _resolver, caller = self._context(None, seat=seat)
        self.assertIs(caller, seat)

    def _decide_context(self, declared: DeclaredCaller):
        topology = mock.Mock()
        topology.resolve.return_value = SimpleNamespace(
            ref=declared.task_document_ref,
            document=SimpleNamespace(
                id=declared.task_document_ref.path.split("/")[-1].split(".")[0]
            ),
        )
        with mock.patch.object(
            gates,
            "resolve_ambient_seat",
            side_effect=gates.AmbientSeatError("ambient-seat-unavailable", "no hosted identity"),
        ):
            _topology, resolver, caller = gates._context(self.config, environ={}, declared=declared)
        return topology, resolver, caller

    def test_structural_gate_decide_tool_declared_caller_full_branches(self) -> None:
        """decide_tool through the declared fallback: decided / missing / ambiguous / refused."""
        sprint = TaskDocumentRef(repository="repo", path="sprint/task.json")
        leaf = TaskDocumentRef(repository="repo", path="master/leaf.json")
        declared = DeclaredCaller(role="orchestrator", task_document_ref=sprint)
        topology, _resolver, caller = self._decide_context(declared)
        # The request targets the leaf document: the candidate filter matches the
        # gate enclosure against the resolved document id, so resolve the leaf ref
        # to the leaf document (the sprint id from _decide_context would match
        # nothing in the store and yield structural-gate-missing).
        topology.resolve.return_value = SimpleNamespace(
            ref=leaf, document=SimpleNamespace(id="leaf")
        )
        store = mock.Mock()
        open_gate = SimpleNamespace(
            id="g1",
            lifecycleId="l1",
            state="open",
            kind="plan-approval",
            enclosure="leaf",
            repoId="repo",
            decidingRole=None,
            evidenceRefs=[],
        )
        request = gates.StructuralGateDecisionRequest(
            task_document_ref=leaf,
            kind="plan-approval",
            decision="approve",
            note="ok",
        )
        with (
            mock.patch.object(gates, "_context", return_value=(topology, mock.Mock(), caller)),
            mock.patch.object(gates, "GateStore", return_value=store),
            mock.patch.object(
                gates,
                "gate_decide_tool",
                return_value={
                    "ok": True,
                    "state": "decided",
                    "decidedVia": "orchestration",
                    "decidingRole": "orchestrator",
                    "evidenceRefs": [],
                },
            ),
        ):
            store.all_current.return_value = {"g1": open_gate}
            decided = gates.structural_gate_decide_tool(self.config, request)
            self.assertEqual(decided["status"], "decided")
            self.assertEqual(decided["detail"], "ok")
            store.all_current.return_value = {}
            self.assertEqual(
                gates.structural_gate_decide_tool(self.config, request)["status"],
                "structural-gate-missing",
            )
            store.all_current.return_value = {"g1": open_gate, "g2": open_gate}
            self.assertEqual(
                gates.structural_gate_decide_tool(self.config, request)["status"],
                "structural-gate-ambiguous",
            )

    def test_structural_gate_decide_tool_refused_via_declared_worker(self) -> None:
        """A declared role that cannot decide is refused through the tool, not admitted."""
        master = TaskDocumentRef(repository="repo", path="master/task.json")
        leaf = TaskDocumentRef(repository="repo", path="master/leaf.json")
        worker = DeclaredCaller(role="worker", task_document_ref=master)
        topology, resolver, caller = self._decide_context(worker)
        request = gates.StructuralGateDecisionRequest(
            task_document_ref=leaf,
            kind="plan-approval",
            decision="approve",
            note="ok",
        )
        with (
            mock.patch.object(gates, "_context", return_value=(topology, resolver, caller)),
            mock.patch.object(gates, "GateStore", return_value=mock.Mock()),
        ):
            refused = gates.structural_gate_decide_tool(self.config, request)
        self.assertEqual(refused["status"], "structural-gate-target-refused")

    def test_structural_gate_list_tool_declared_caller_full_branches(self) -> None:
        """list_tool through the declared fallback: listed with skip/append branches."""
        sprint = TaskDocumentRef(repository="repo", path="sprint/task.json")
        master = TaskDocumentRef(repository="repo", path="master/task.json")
        leaf = TaskDocumentRef(repository="repo", path="master/leaf.json")
        declared = DeclaredCaller(role="orchestrator", task_document_ref=sprint)
        topology = mock.Mock()
        topology.children.return_value = (master, leaf)
        topology.resolve.side_effect = [
            SimpleNamespace(document=SimpleNamespace(id="sprint")),
            SimpleNamespace(document=SimpleNamespace(id="master")),
            SimpleNamespace(document=SimpleNamespace(id="leaf")),
        ]
        with mock.patch.object(
            gates,
            "resolve_ambient_seat",
            side_effect=gates.AmbientSeatError("ambient-seat-unavailable", "no hosted identity"),
        ):
            _topology, resolver, caller = gates._context(self.config, environ={}, declared=declared)
        ignored = SimpleNamespace(enclosure=None, repoId="repo")
        unrelated = SimpleNamespace(enclosure="other", repoId="repo")
        matched = SimpleNamespace(
            enclosure="leaf",
            repoId="repo",
            kind="plan-approval",
            state="open",
            decidingRole="manager",
            evidenceRefs=[],
        )
        store = mock.Mock()
        store.all_current.return_value = {
            "ignored": ignored,
            "unrelated": unrelated,
            "matched": matched,
        }
        with (
            mock.patch.object(gates, "_context", return_value=(topology, resolver, caller)),
            mock.patch.object(gates, "GateStore", return_value=store),
        ):
            listed = gates.structural_gate_list_tool(self.config)
        self.assertEqual(listed["status"], "listed")
        self.assertEqual(len(listed["gates"]), 1)
        self.assertEqual(listed["gates"][0]["kind"], "plan-approval")
