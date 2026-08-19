from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.models.closeout_queue import (
    CandidateAdmissionFacts,
    CloseoutQueueRequest,
    CloseoutQueueState,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees.closeout_queue import (
    CloseoutQueueError,
    QueueActor,
    _ActionContext,
    _active_lane_owner,
    _admission,
    _apply_action,
    _apply_candidate_action,
    _authorize_candidate_action,
    _authorize_status_scope,
    _bind_queue_contract,
    _blocked_legal_operations,
    _candidate_or_error,
    _declaration_identity,
    _declare_candidate,
    _declared_legal_operations,
    _graph_context,
    _group_name,
    _in_flight_legal_operations,
    _initial_state,
    _leaf_identity,
    _lifecycle_operation_legal,
    _owned_lifecycle_operation,
    _queue_action,
    _release_selection,
    _request_fingerprint,
    _required_candidate_ref,
    closeout_queue_tool,
)
from agents_remember.worktrees.closeout_queue_blocker import (
    _abort_blocker,
    _acquire_blocker,
    _release_blocker,
)
from agents_remember.worktrees.closeout_queue_candidate_evidence import (
    operation_owner_fingerprint,
)
from agents_remember.worktrees.closeout_queue_errors import queue_task_ref
from test_closeout_queue import (
    LEAF_A,
    MASTER_A,
    MASTER_B,
    NOW,
    RATIONALE,
    SPRINT,
    QueueFixture,
)


class CloseoutQueueActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _fixture(self, **kwargs: Any) -> QueueFixture:
        return QueueFixture(Path(self.temp.name), **kwargs)

    @staticmethod
    def _context(
        fixture: QueueFixture,
    ) -> tuple[TaskDocumentTopology, Any, CloseoutQueueState]:
        topology = TaskDocumentTopology(fixture.coord)
        graph = _graph_context(topology, SPRINT)
        initial = _initial_state(SPRINT, graph.revision, NOW)
        state = CloseoutQueueStore(fixture.coord, SPRINT).read(initial)
        return topology, graph, state

    def test_status_scope_and_candidate_mutation_authority_is_exact(self) -> None:
        fixture = self._fixture()
        _, graph, _ = self._context(fixture)
        for role in ("architect", "strategist", "orchestrator"):
            _authorize_status_scope(QueueActor(role=role, task_document_ref=SPRINT), graph)
        _authorize_status_scope(QueueActor(role="manager", task_document_ref=MASTER_A), graph)
        for actor in (
            QueueActor(role="worker", task_document_ref=SPRINT),
            QueueActor(
                role="manager",
                task_document_ref=TaskDocumentRef(
                    repository=SPRINT.repository, path="foreign/task.json"
                ),
            ),
        ):
            with (
                self.subTest(actor=actor),
                self.assertRaisesRegex(CloseoutQueueError, "access requires"),
            ):
                _authorize_status_scope(actor, graph)

        fixture.declare(MASTER_A)
        _, graph, state = self._context(fixture)
        candidate = state.candidates[LEAF_A.key]
        orchestrator = QueueActor(role="orchestrator", task_document_ref=SPRINT)
        manager = QueueActor(role="manager", task_document_ref=MASTER_A)
        for action in ("set-grade", "select", "release-selection"):
            _authorize_candidate_action(orchestrator, graph, cast(Any, action), candidate)
            with (
                self.subTest(action=action),
                self.assertRaisesRegex(CloseoutQueueError, "authority"),
            ):
                _authorize_candidate_action(manager, graph, cast(Any, action), candidate)
        _authorize_candidate_action(manager, graph, "set-admission", candidate)
        _authorize_candidate_action(manager, graph, "withdraw", candidate)
        _authorize_candidate_action(orchestrator, graph, "withdraw", candidate)
        wrong_manager = QueueActor(role="manager", task_document_ref=MASTER_B)
        for action in ("set-admission", "withdraw"):
            with (
                self.subTest(action=action),
                self.assertRaisesRegex(CloseoutQueueError, "owning manager"),
            ):
                _authorize_candidate_action(wrong_manager, graph, cast(Any, action), candidate)
        with self.assertRaisesRegex(AssertionError, "unhandled"):
            _authorize_candidate_action(orchestrator, graph, cast(Any, "declare"), candidate)

    def test_public_tool_refuses_blank_time_missing_id_stale_revision_and_completed_mutation(
        self,
    ) -> None:
        fixture = self._fixture()
        actor = QueueActor(role="orchestrator", task_document_ref=SPRINT)
        with self.assertRaisesRegex(CloseoutQueueError, "timestamp"):
            closeout_queue_tool(
                fixture.cfg,
                CloseoutQueueRequest(action="status", sprint_task_document_ref=SPRINT),
                actor=actor,
                now=" ",
            )
        missing_id = CloseoutQueueRequest.model_construct(
            action="withdraw",
            sprint_task_document_ref=SPRINT,
            request_id=None,
            expected_revision=0,
            candidate_task_document_ref=LEAF_A,
            contract_path=None,
            blocker_master_ref=None,
            admission=None,
            grade=None,
            blocker_judgment_id=None,
            rationale="",
        )
        with self.assertRaisesRegex(CloseoutQueueError, "stable request_id"):
            closeout_queue_tool(fixture.cfg, missing_id, actor=actor, now=NOW)

        stale = CloseoutQueueRequest(
            action="withdraw",
            sprint_task_document_ref=SPRINT,
            request_id="stale",
            expected_revision=99,
            candidate_task_document_ref=LEAF_A,
        )
        with self.assertRaisesRegex(CloseoutQueueError, "current is 0"):
            closeout_queue_tool(fixture.cfg, stale, actor=actor, now=NOW)

        sprint = fixture.status()
        self.assertEqual(sprint["revision"], 0)
        sprint_path = fixture.tasks / "sprint"
        current = read_task_doc(sprint_path / "task.json")
        write_task_doc(sprint_path, current.model_copy(update={"status": "Completed"}))
        self.assertEqual(
            closeout_queue_tool(
                fixture.cfg,
                CloseoutQueueRequest(action="status", sprint_task_document_ref=SPRINT),
                actor=actor,
                now=NOW,
            )["state"],
            "projected",
        )
        with self.assertRaisesRegex(CloseoutQueueError, "cannot accept mutations"):
            closeout_queue_tool(fixture.cfg, stale, actor=actor, now=NOW)

    def test_mutation_rechecks_sprint_completion_inside_the_store_lock(self) -> None:
        fixture = self._fixture()
        topology, graph, _ = self._context(fixture)
        completed_graph = replace(
            graph,
            sprint=replace(
                graph.sprint,
                document=graph.sprint.document.model_copy(update={"status": "Completed"}),
            ),
        )
        request = CloseoutQueueRequest(
            action="withdraw",
            sprint_task_document_ref=SPRINT,
            request_id="withdraw",
            expected_revision=0,
            candidate_task_document_ref=LEAF_A,
        )
        with (
            mock.patch(
                "agents_remember.worktrees.closeout_queue.TaskDocumentTopology",
                return_value=topology,
            ),
            mock.patch(
                "agents_remember.worktrees.closeout_queue._graph_context",
                side_effect=[graph, completed_graph],
            ),
            self.assertRaisesRegex(CloseoutQueueError, "sprint-completed"),
        ):
            closeout_queue_tool(
                fixture.cfg,
                request,
                actor=QueueActor(role="orchestrator", task_document_ref=SPRINT),
                now=NOW,
            )

    def test_candidate_action_missing_noop_immutable_and_release_matrix(self) -> None:
        fixture = self._fixture()
        topology, graph, state = self._context(fixture)
        orchestrator = QueueActor(role="orchestrator", task_document_ref=SPRINT)
        withdraw = CloseoutQueueRequest(
            action="withdraw",
            sprint_task_document_ref=SPRINT,
            request_id="withdraw",
            expected_revision=0,
            candidate_task_document_ref=LEAF_A,
        )
        context = _ActionContext(fixture.cfg, topology, graph, withdraw, "withdraw", NOW)
        self.assertIs(_apply_candidate_action(state, context, orchestrator), state)

        select = CloseoutQueueRequest(
            action="select",
            sprint_task_document_ref=SPRINT,
            request_id="select",
            expected_revision=0,
            candidate_task_document_ref=LEAF_A,
        )
        with self.assertRaisesRegex(CloseoutQueueError, "closeout-candidate-not-declared"):
            _apply_candidate_action(
                state,
                _ActionContext(fixture.cfg, topology, graph, select, "select", NOW),
                orchestrator,
            )

        fixture.declare(MASTER_A)
        topology, graph, state = self._context(fixture)
        candidate = state.candidates[LEAF_A.key]
        self.assertIs(_release_selection(candidate), candidate)
        selected = candidate.model_copy(update={"state": "selected"})
        self.assertEqual(_release_selection(selected).state, "declared")
        lifecycle_owned = candidate.model_copy(
            update={"state": "closeout-in-flight", "inFlightOwnerFingerprint": "f" * 64}
        )
        with self.assertRaisesRegex(CloseoutQueueError, "task-addressed"):
            _release_selection(lifecycle_owned)

        frozen_state = state.model_copy(update={"candidates": {LEAF_A.key: selected}})
        admission_facts = CandidateAdmissionFacts(
            resourceReady=False,
            resourceReason="capacity is reserved for another leaf",
        )
        admission = CloseoutQueueRequest(
            action="set-admission",
            sprint_task_document_ref=SPRINT,
            request_id="admission",
            expected_revision=state.revision,
            candidate_task_document_ref=LEAF_A,
            admission=admission_facts,
        )
        admitted = _apply_candidate_action(
            state,
            _ActionContext(fixture.cfg, topology, graph, admission, "set-admission", NOW),
            QueueActor(role="manager", task_document_ref=MASTER_A),
        )
        self.assertEqual(admitted.candidates[LEAF_A.key].admission, admission_facts)
        with self.assertRaisesRegex(CloseoutQueueError, "frozen"):
            _apply_candidate_action(
                frozen_state,
                _ActionContext(fixture.cfg, topology, graph, admission, "set-admission", NOW),
                QueueActor(role="manager", task_document_ref=MASTER_A),
            )

    def test_declaration_identity_refuses_missing_nonleaf_moved_and_foreign_scope(self) -> None:
        fixture = self._fixture()
        topology, graph, state = self._context(fixture)
        contract = fixture.contracts[MASTER_A]
        valid = CloseoutQueueRequest(
            action="declare",
            sprint_task_document_ref=SPRINT,
            request_id="declare",
            expected_revision=0,
            contract_path=contract.contract_path.as_posix(),
        )
        context = _ActionContext(fixture.cfg, topology, graph, valid, "declare", NOW)
        missing = valid.model_copy(update={"contract_path": None})
        with self.assertRaisesRegex(CloseoutQueueError, "contract-required"):
            _declaration_identity(
                _ActionContext(fixture.cfg, topology, graph, missing, "declare", NOW)
            )
        with (
            mock.patch(
                "agents_remember.worktrees.closeout_queue.load_contract",
                return_value=replace(contract, kind="series"),
            ),
            self.assertRaisesRegex(CloseoutQueueError, "leaf-required"),
        ):
            _declaration_identity(context)
        with (
            mock.patch(
                "agents_remember.worktrees.closeout_queue.load_contract",
                return_value=replace(contract, closeout_status="completed"),
            ),
            self.assertRaisesRegex(CloseoutQueueError, "too-late"),
        ):
            _declaration_identity(context)
        with (
            mock.patch.object(topology, "parent", return_value=None),
            self.assertRaisesRegex(CloseoutQueueError, "master-mismatch"),
        ):
            _declaration_identity(context)
        with (
            mock.patch.object(topology, "parent", side_effect=[MASTER_A, MASTER_B]),
            self.assertRaisesRegex(CloseoutQueueError, "sprint-mismatch"),
        ):
            _declaration_identity(context)
        with self.assertRaisesRegex(CloseoutQueueError, "only the owning"):
            _declare_candidate(
                state,
                context,
                QueueActor(role="manager", task_document_ref=MASTER_B),
            )

    def test_queue_contract_binding_is_idempotent_and_immutable(self) -> None:
        fixture = self._fixture()
        contract = fixture.contracts[MASTER_A]
        bound = _bind_queue_contract(
            contract,
            sprint_ref=SPRINT,
            candidate_ref=LEAF_A,
        )
        self.assertEqual(
            (bound.queue_sprint_task_document, bound.queue_candidate_task_document),
            (SPRINT.key, LEAF_A.key),
        )
        self.assertIs(
            _bind_queue_contract(bound, sprint_ref=SPRINT, candidate_ref=LEAF_A),
            bound,
        )
        conflicting = replace(bound, queue_candidate_task_document="repo-a/other.json")
        with self.assertRaisesRegex(CloseoutQueueError, "binding-mismatch"):
            _bind_queue_contract(
                conflicting,
                sprint_ref=SPRINT,
                candidate_ref=LEAF_A,
            )

    def test_apply_action_closed_and_unhandled_actions_are_fail_closed(self) -> None:
        fixture = self._fixture()
        topology, graph, state = self._context(fixture)
        actor = QueueActor(role="orchestrator", task_document_ref=SPRINT)
        request = CloseoutQueueRequest(action="status", sprint_task_document_ref=SPRINT)
        context = _ActionContext(fixture.cfg, topology, graph, request, "status", NOW)
        with self.assertRaisesRegex(CloseoutQueueError, "completed"):
            _apply_action(state.model_copy(update={"closed": True}), context, actor)
        with self.assertRaisesRegex(AssertionError, "unhandled"):
            _apply_action(state, context, actor)

    def test_acquire_blocker_refusal_matrix_and_idempotency(self) -> None:
        fixture = self._fixture(atomic_b=True)
        _, graph, state = self._context(fixture)
        valid = CloseoutQueueRequest(
            action="acquire-blocker",
            sprint_task_document_ref=SPRINT,
            request_id="blocker",
            expected_revision=0,
            blocker_master_ref=MASTER_B,
            rationale=RATIONALE,
        )
        acquired = _acquire_blocker(graph, state, valid, NOW, "orchestrator")
        assert acquired.activeBlocker is not None
        self.assertIs(_acquire_blocker(graph, acquired, valid, NOW, "orchestrator"), acquired)

        unknown = valid.model_copy(
            update={
                "blocker_master_ref": TaskDocumentRef(
                    repository=SPRINT.repository, path="missing/task.json"
                )
            }
        )
        with self.assertRaisesRegex(CloseoutQueueError, "not in the sprint graph"):
            _acquire_blocker(graph, state, unknown, NOW, "orchestrator")
        non_atomic = valid.model_copy(update={"blocker_master_ref": MASTER_A})
        with self.assertRaisesRegex(CloseoutQueueError, "only an atomic"):
            _acquire_blocker(graph, state, non_atomic, NOW, "orchestrator")

        stale_blocker = acquired.activeBlocker.model_copy(update={"graphRevision": "0" * 64})
        with self.assertRaisesRegex(CloseoutQueueError, "older graph revision"):
            _acquire_blocker(
                graph,
                state.model_copy(update={"activeBlocker": stale_blocker}),
                valid,
                NOW,
                "orchestrator",
            )
        other_blocker = acquired.activeBlocker.model_copy(update={"master": MASTER_A})
        with self.assertRaisesRegex(CloseoutQueueError, "already held"):
            _acquire_blocker(
                graph,
                state.model_copy(update={"activeBlocker": other_blocker}),
                valid,
                NOW,
                "orchestrator",
            )

        fixture.declare(MASTER_A)
        _, graph, declared = self._context(fixture)
        selected = declared.candidates[LEAF_A.key].model_copy(update={"state": "selected"})
        occupied = declared.model_copy(update={"candidates": {LEAF_A.key: selected}})
        with self.assertRaisesRegex(CloseoutQueueError, "lane is not drained"):
            _acquire_blocker(graph, occupied, valid, NOW, "orchestrator")
        blank = valid.model_copy(update={"rationale": " "})
        with self.assertRaisesRegex(CloseoutQueueError, "requires rationale"):
            _acquire_blocker(graph, declared, blank, NOW, "orchestrator")

        edge_fixture = QueueFixture(Path(self.temp.name) / "edge", edge=True, atomic_b=True)
        _, edge_graph, edge_state = self._context(edge_fixture)
        with self.assertRaisesRegex(CloseoutQueueError, "predecessors are incomplete"):
            _acquire_blocker(edge_graph, edge_state, valid, NOW, "orchestrator")

    def test_acquire_blocker_requires_current_source_bases(self) -> None:
        fixture = self._fixture(atomic_b=True)
        _, graph, state = self._context(fixture)
        valid = CloseoutQueueRequest(
            action="acquire-blocker",
            sprint_task_document_ref=SPRINT,
            request_id="blocker",
            expected_revision=0,
            blocker_master_ref=MASTER_B,
            rationale=RATIONALE,
        )
        with (
            mock.patch(
                "agents_remember.worktrees.closeout_queue_blocker.require_source_bases_current",
                side_effect=CloseoutQueueError(
                    "closeout-candidate-code-source-moved", "code source moved"
                ),
            ),
            self.assertRaisesRegex(CloseoutQueueError, "code source moved"),
        ):
            _acquire_blocker(graph, state, valid, NOW, "orchestrator")

    def test_release_and_abort_blocker_require_exact_owner_and_empty_block(self) -> None:
        fixture = self._fixture(atomic_b=True)
        _, graph, state = self._context(fixture)
        valid = CloseoutQueueRequest(
            action="acquire-blocker",
            sprint_task_document_ref=SPRINT,
            request_id="blocker",
            expected_revision=0,
            blocker_master_ref=MASTER_B,
            rationale=RATIONALE,
        )
        with self.assertRaisesRegex(CloseoutQueueError, "no atomic blocker"):
            _release_blocker(graph, state, valid, fixture.cfg)
        with self.assertRaisesRegex(CloseoutQueueError, "no atomic blocker"):
            _abort_blocker(graph, state, valid)
        held = _acquire_blocker(graph, state, valid, NOW, "orchestrator")
        with self.assertRaisesRegex(CloseoutQueueError, "master-incomplete"):
            _release_blocker(graph, held, valid, fixture.cfg)
        wrong = valid.model_copy(update={"blocker_master_ref": MASTER_A})
        with self.assertRaisesRegex(CloseoutQueueError, "belongs to"):
            _release_blocker(graph, held, wrong, fixture.cfg)
        with self.assertRaisesRegex(CloseoutQueueError, "belongs to"):
            _abort_blocker(graph, held, wrong)

        fixture.declare(MASTER_B)
        _, graph, candidates = self._context(fixture)
        blocked = candidates.model_copy(update={"activeBlocker": held.activeBlocker})
        with self.assertRaisesRegex(CloseoutQueueError, "candidates"):
            _release_blocker(graph, blocked, valid, fixture.cfg)
        with self.assertRaisesRegex(CloseoutQueueError, "candidates"):
            _abort_blocker(graph, blocked, valid)

        completed_master = replace(
            graph.masters[MASTER_B],
            document=graph.masters[MASTER_B].document.model_copy(update={"status": "Completed"}),
        )
        completed_graph = replace(
            graph,
            masters={**graph.masters, MASTER_B: completed_master},
        )
        release = valid.model_copy(update={"action": "release-blocker"})
        blank_release = release.model_copy(update={"rationale": " "})
        with (
            mock.patch(
                "agents_remember.worktrees.closeout_queue_blocker.require_atomic_master_landed"
            ),
            self.assertRaisesRegex(CloseoutQueueError, "rationale-required"),
        ):
            _release_blocker(completed_graph, held, blank_release, fixture.cfg)
        with mock.patch(
            "agents_remember.worktrees.closeout_queue_blocker.require_atomic_master_landed"
        ) as landing_check:
            self.assertIsNone(
                _release_blocker(completed_graph, held, release, fixture.cfg).activeBlocker
            )
        landing_check.assert_called_once()
        self.assertEqual(landing_check.call_args.args[0], completed_master)
        abort = CloseoutQueueRequest(
            action="abort-blocker",
            sprint_task_document_ref=SPRINT,
            request_id="abort",
            expected_revision=0,
            blocker_master_ref=MASTER_B,
            blocker_judgment_id="ABORT-1",
        )
        with mock.patch(
            "agents_remember.worktrees.closeout_queue_blocker.canonical_blocker_abort"
        ) as abort_check:
            self.assertIsNone(_abort_blocker(graph, held, abort).activeBlocker)
        abort_check.assert_called_once_with(
            "ABORT-1",
            authority=graph.grade_authority,
            master_ref=MASTER_B,
            graph_revision=graph.revision,
        )

    def test_projection_legal_operations_are_actor_and_state_specific(self) -> None:
        fixture = self._fixture()
        fixture.declare(MASTER_A)
        _, graph, state = self._context(fixture)
        candidate = state.candidates[LEAF_A.key]
        orchestrator = QueueActor(role="orchestrator", task_document_ref=SPRINT)
        manager = QueueActor(role="manager", task_document_ref=MASTER_A)
        stranger = QueueActor(role="worker", task_document_ref=LEAF_A)
        self.assertEqual(
            _declared_legal_operations(graph, candidate, orchestrator, ready=True),
            ["select", "set-grade", "withdraw"],
        )
        self.assertEqual(
            _declared_legal_operations(graph, candidate, orchestrator, ready=False),
            ["set-grade", "withdraw"],
        )
        self.assertEqual(
            _declared_legal_operations(graph, candidate, manager, ready=True),
            ["set-admission", "withdraw"],
        )
        self.assertEqual(_declared_legal_operations(graph, candidate, stranger, ready=True), [])

        selected = candidate.model_copy(update={"state": "selected"})
        self.assertEqual(
            _blocked_legal_operations(graph, selected, orchestrator), ["release-selection"]
        )
        self.assertEqual(_blocked_legal_operations(graph, selected, manager), [])
        certified = candidate.model_copy(
            update={
                "state": "certified",
                "closeoutCodeCommit": "a" * 40,
                "closeoutMemoryContentCommit": "b" * 40,
                "closeoutLedgerCommit": "c" * 40,
            }
        )
        self.assertEqual(_blocked_legal_operations(graph, certified, manager), [])
        self.assertEqual(
            _in_flight_legal_operations(graph, selected, manager),
            ["worktree_closeout_apply"],
        )
        self.assertEqual(
            _in_flight_legal_operations(graph, selected, orchestrator), ["release-selection"]
        )
        self.assertEqual(_in_flight_legal_operations(graph, selected, stranger), [])
        self.assertEqual(
            _in_flight_legal_operations(graph, certified, manager),
            ["worktree_integrate"],
        )
        self.assertEqual(_in_flight_legal_operations(graph, certified, orchestrator), [])

    def test_lifecycle_legal_operations_follow_exact_owner_record_and_boundary(self) -> None:
        fixture = self._fixture()
        fixture.declare(MASTER_A)
        _, graph, state = self._context(fixture)
        candidate = state.candidates[LEAF_A.key].model_copy(
            update={
                "state": "closeout-in-flight",
                "inFlightOwnerFingerprint": "a" * 64,
            }
        )
        manager = QueueActor(role="manager", task_document_ref=MASTER_A)
        orchestrator = QueueActor(role="orchestrator", task_document_ref=SPRINT)
        with mock.patch(
            "agents_remember.worktrees.closeout_queue._owned_lifecycle_operation",
            return_value=None,
        ):
            self.assertEqual(_lifecycle_operation_legal(graph, candidate, manager), [])
        cases = (
            ("failed", False, ["worktree_closeout_apply"]),
            ("cancelled", True, []),
            (
                "running",
                False,
                ["worktree_closeout_apply", "worktree_operation_cancel"],
            ),
            ("running", True, ["worktree_closeout_apply"]),
            ("completed", True, []),
        )
        for status, crossed, expected in cases:
            record = mock.Mock(
                status=status,
                irreversibleBoundaryEntered=crossed,
            )
            with (
                self.subTest(status=status, crossed=crossed),
                mock.patch(
                    "agents_remember.worktrees.closeout_queue._owned_lifecycle_operation",
                    return_value=record,
                ),
            ):
                self.assertEqual(_lifecycle_operation_legal(graph, candidate, manager), expected)
        self.assertEqual(_lifecycle_operation_legal(graph, candidate, orchestrator), [])
        self.assertIsNone(_owned_lifecycle_operation(candidate))
        missing_contract = candidate.model_copy(update={"contractPath": "/missing.md"})
        self.assertIsNone(_owned_lifecycle_operation(missing_contract))

    def test_owned_lifecycle_operation_requires_exact_kind_contract_and_fingerprint(self) -> None:
        fixture = self._fixture()
        fixture.declare(MASTER_A)
        _, _, state = self._context(fixture)
        key = "a" * 64
        candidate = state.candidates[LEAF_A.key].model_copy(
            update={
                "state": "closeout-in-flight",
                "inFlightOwnerFingerprint": operation_owner_fingerprint(key),
            }
        )
        valid = SimpleNamespace(
            operationKind="closeout",
            contractPath=candidate.contractPath,
            operationKey=key,
        )

        def owned(record: object) -> object | None:
            store = mock.Mock()
            store.read.return_value = record
            with mock.patch(
                "agents_remember.worktrees.closeout_queue.LifecycleOperationStore",
                return_value=store,
            ):
                return _owned_lifecycle_operation(candidate)

        self.assertIs(owned(valid), valid)
        for changed in (
            None,
            SimpleNamespace(**{**valid.__dict__, "operationKind": "integrate"}),
            SimpleNamespace(**{**valid.__dict__, "contractPath": "/other.md"}),
            SimpleNamespace(**{**valid.__dict__, "operationKey": "b" * 64}),
        ):
            with self.subTest(changed=changed):
                self.assertIsNone(owned(changed))

    def test_small_helpers_refuse_invalid_refs_actions_and_candidates(self) -> None:
        self.assertEqual(queue_task_ref(LEAF_A, "leaf"), LEAF_A)
        self.assertEqual(queue_task_ref(LEAF_A.model_dump(), "leaf"), LEAF_A)
        with self.assertRaisesRegex(CloseoutQueueError, "is required"):
            queue_task_ref(None, "leaf")
        with self.assertRaisesRegex(CloseoutQueueError, "reference-invalid"):
            queue_task_ref({"repository": "repo-a", "path": "../bad"}, "leaf")
        with self.assertRaisesRegex(CloseoutQueueError, "is required"):
            _required_candidate_ref(
                CloseoutQueueRequest.model_construct(candidate_task_document_ref=None)
            )
        with self.assertRaisesRegex(CloseoutQueueError, "closeout-candidate-not-declared"):
            _candidate_or_error(
                _initial_state(SPRINT, "a" * 64, NOW),
                LEAF_A,
            )
        self.assertEqual(_queue_action(" status "), "status")
        with self.assertRaisesRegex(CloseoutQueueError, "unsupported"):
            _queue_action("unknown")
        with self.assertRaisesRegex(CloseoutQueueError, "admission-invalid"):
            _admission(cast(Any, {"resourceReady": False, "resourceReason": ""}))
        self.assertEqual(_group_name("in-flight"), "inFlight")
        self.assertEqual(_group_name("ready"), "ready")
        self.assertIsNone(_active_lane_owner(_initial_state(SPRINT, "a" * 64, NOW)))
        fixture = self._fixture()
        topology, _graph, _state = self._context(fixture)
        with (
            mock.patch(
                "agents_remember.worktrees.closeout_queue.resolve_terminal_leaf_doc",
                return_value=None,
            ),
            self.assertRaisesRegex(CloseoutQueueError, "task-document-missing"),
        ):
            _leaf_identity(topology, fixture.contracts[MASTER_A])
        actor = QueueActor(role="manager", task_document_ref=MASTER_A)
        request = CloseoutQueueRequest(action="status", sprint_task_document_ref=SPRINT)
        fingerprint = _request_fingerprint(request, actor)
        self.assertEqual(len(fingerprint), 64)
        self.assertEqual(fingerprint, _request_fingerprint(request, actor))
        self.assertNotEqual(
            fingerprint,
            _request_fingerprint(
                request,
                QueueActor(role="orchestrator", task_document_ref=SPRINT),
            ),
        )


if __name__ == "__main__":
    unittest.main()
