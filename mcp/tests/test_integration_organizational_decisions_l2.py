"""Public totality for journal-owned organizational task publication."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import organizational_completion_test_support as fixture_mod
from agents_remember.application.lifecycle import lifecycle_operation_worker
from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.application.worktree_tools import (
    OperationControlRequest,
    worktree_operation_control_tool,
    worktree_status_tool,
)
from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.controlplane.integration_authority_lock import integration_authority_lock
from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.worktrees.integration import integration_quality as quality_mod
from agents_remember.worktrees.integration import organizational_completion as completion_mod
from agents_remember.worktrees.integration.lifecycle import (
    lifecycle_operation_controls as controls_mod,
)
from agents_remember.worktrees.modules import integrate as integrate_mod
from agents_remember.worktrees.modules import integration_publication as publication_mod
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.worktree_contract import load_contract
from test_closeout_queue import SPRINT
from test_worktree_support import git


class IntegrationOrganizationalDecisionL2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner = fixture_mod.OrganizationalCompletionFixture()
        self.owner.setUp()
        self.fixture = self.owner.fixture

    def tearDown(self) -> None:
        try:
            self.owner.doCleanups()
        finally:
            self.owner.tearDown()

    def _pending_publication(self):
        contract = self.owner._certified_contract(final=True)
        store, runtime, record = self.owner._integration_runtime(contract)
        with (
            mock.patch.object(
                quality_mod,
                "run_strict_code_quality_gate",
                side_effect=fixture_mod._full_gate(contract),
            ),
            mock.patch.object(
                integrate_mod,
                "prepare_integration_ref_move",
                side_effect=SystemExit("cut after publication intent before refs"),
            ),
            self.assertRaisesRegex(SystemExit, "before refs"),
        ):
            integrate_mod.integrate_result(self.owner._args(contract, runtime, record), contract)
        runtime.fail(RuntimeError("cut after publication intent before refs"))
        retained = store.read()
        assert retained is not None and retained.integrationPublication is not None
        assert retained.integrationPublication.organizationalCompletion is not None
        status = self._status(contract)
        recover = next(row for row in status["legalControls"] if row["action"] == "recover")
        return contract, store, recover

    def _status(self, contract):
        with mock.patch(
            "agents_remember.application.worktree_tools.git_worktree_manager.status_result",
            return_value=WorktreeCommandResult(
                0,
                {
                    "contract_path": contract.contract_path.as_posix(),
                    "task_name": contract.task_name,
                },
            ),
        ):
            status = worktree_status_tool(
                self.fixture.cfg,
                TaskRef(
                    repo_id=contract.repo_name,
                    contract_path=contract.contract_path.as_posix(),
                ),
            )
        return next(row for row in status["lifecycleOperations"] if row["kind"] == "integrate")

    def _edit_master(self, contract) -> None:
        path = self.fixture.tasks / "master-a" / "task.json"
        with integration_authority_lock(contract.coordination_root, contract.repo_name):
            master = read_task_doc(path)
            write_task_doc(
                path.parent,
                master.model_copy(update={"title": f"{master.title} — author edit"}),
            )

    def _snapshots(self, contract, store):
        assert contract.memory_repo_path is not None
        queue = CloseoutQueueStore(self.fixture.coord, SPRINT)
        return {
            "journal": {
                path.relative_to(store.path.parent).as_posix(): path.read_bytes()
                for path in store.path.parent.rglob("*")
                if path.is_file()
            },
            "tasks": {
                path.relative_to(self.fixture.tasks).as_posix(): path.read_bytes()
                for path in self.fixture.tasks.rglob("*")
                if path.is_file() and (path.name == "task.json" or path.suffix == ".md")
            },
            "contract": contract.contract_path.read_bytes(),
            "queue": {
                path.name: path.read_bytes() if path.exists() else None
                for path in (queue.state_path,)
            },
            "refs": {
                "code": git(
                    contract.code_repo_path,
                    "rev-parse",
                    contract.code_source_branch,
                ),
                "memory": git(
                    contract.memory_repo_path,
                    "rev-parse",
                    contract.memory_source_branch,
                ),
            },
        }

    def test_third_task_bytes_make_fresh_status_and_stale_recover_identical(self) -> None:
        contract, store, stale_recover = self._pending_publication()
        self._edit_master(contract)
        before = self._snapshots(contract, store)

        projected = self._status(contract)
        self.assertEqual(projected["legalControls"], [])
        decision = projected["result"]
        self.assertEqual(decision["state"], "organizational-completion-publication-conflict")
        self.assertEqual(decision["nextAction"], "developer-decision")
        with mock.patch.object(controls_mod, "launch_detached_worker") as launch:
            refused = worktree_operation_control_tool(
                self.fixture.cfg,
                OperationControlRequest(**stale_recover["arguments"]),
            )
        self.assertFalse(refused["ok"])
        launch.assert_not_called()
        self.assertEqual(
            {
                "status": decision["state"],
                "detail": decision["decisionSurface"],
                "developerDecisionRequired": decision["developerDecisionRequired"],
                "nextAction": decision["nextAction"],
                "expected": decision["expected"],
                "observed": decision["observed"],
            },
            {
                key: refused[key]
                for key in (
                    "status",
                    "detail",
                    "developerDecisionRequired",
                    "nextAction",
                    "expected",
                    "observed",
                )
            },
        )
        for prohibited in ("nextTool", "nextArgs", "arguments", "apply", "applyStep"):
            self.assertNotIn(prohibited, decision)
            self.assertNotIn(prohibited, refused)
        self.assertEqual(self._snapshots(contract, store), before)

    def test_task_change_after_public_preflight_stops_protected_publication(self) -> None:
        contract, store, recover = self._pending_publication()
        real_compatible = controls_mod.require_lifecycle_operation_compatible
        protected_before = {}

        def compatible_then_edit(*args, **kwargs):
            result = real_compatible(*args, **kwargs)
            if not protected_before:
                self._edit_master(contract)
                protected_before.update(self._snapshots(contract, store))
            return result

        def run_inline(_contract, _requeued):
            inline = lifecycle_operation_worker.OperationRuntime(store)
            current = inline.start()
            lifecycle_operation_worker.execute_operation(current, inline)

        with (
            mock.patch.object(
                controls_mod,
                "require_lifecycle_operation_compatible",
                side_effect=compatible_then_edit,
            ),
            mock.patch.object(
                controls_mod,
                "launch_detached_worker",
                side_effect=run_inline,
            ),
        ):
            result = worktree_operation_control_tool(
                self.fixture.cfg,
                OperationControlRequest(**recover["arguments"]),
            )
        self.assertTrue(result["ok"])
        projected = result["lifecycleOperation"]
        self.assertEqual(projected["legalControls"], [])
        self.assertEqual(
            projected["result"]["state"],
            "organizational-completion-publication-conflict",
        )
        after = self._snapshots(load_contract(contract.contract_path), store)
        self.assertEqual(after["tasks"], protected_before["tasks"])
        self.assertEqual(after["contract"], protected_before["contract"])
        self.assertEqual(after["queue"], protected_before["queue"])
        self.assertEqual(after["refs"], protected_before["refs"])
        durable = store.read()
        assert durable is not None and durable.result is not None
        self.assertEqual(
            durable.result["state"],
            "organizational-completion-publication-conflict",
        )

    def _assert_unreadable_task_side(self, side: str) -> None:
        contract, store, stale_recover = self._pending_publication()
        master_json = self.fixture.tasks / "master-a" / "task.json"
        target = master_json if side == "json" else master_json.with_suffix(".md")
        before = self._snapshots(contract, store)
        real_read_bytes = Path.read_bytes

        def unreadable(path: Path) -> bytes:
            if path == target:
                raise PermissionError(f"private path must not escape: {path}")
            return real_read_bytes(path)

        with (
            mock.patch.object(Path, "read_bytes", unreadable),
            mock.patch.object(controls_mod, "launch_detached_worker") as launch,
        ):
            projected = self._status(contract)
            refused = worktree_operation_control_tool(
                self.fixture.cfg,
                OperationControlRequest(**stale_recover["arguments"]),
            )

        self.assertEqual(projected["legalControls"], [])
        decision = projected["result"]
        self.assertEqual(
            decision["state"],
            "organizational-completion-publication-conflict",
        )
        self.assertEqual(decision["nextAction"], "developer-decision")
        self.assertEqual(
            decision["observed"]["readFailure"],
            {
                "side": side,
                "name": target.name,
                "errorType": "PermissionError",
            },
        )
        self.assertNotIn("private path", repr(decision))
        self.assertFalse(refused["ok"])
        self.assertEqual(
            {
                "status": decision["state"],
                "detail": decision["decisionSurface"],
                "developerDecisionRequired": decision["developerDecisionRequired"],
                "nextAction": decision["nextAction"],
                "expected": decision["expected"],
                "observed": decision["observed"],
            },
            {
                key: refused[key]
                for key in (
                    "status",
                    "detail",
                    "developerDecisionRequired",
                    "nextAction",
                    "expected",
                    "observed",
                )
            },
        )
        launch.assert_not_called()
        self.assertEqual(self._snapshots(contract, store), before)

    def test_unreadable_organizational_json_is_one_bounded_public_decision(self) -> None:
        self._assert_unreadable_task_side("json")

    def test_unreadable_organizational_markdown_is_one_bounded_public_decision(
        self,
    ) -> None:
        self._assert_unreadable_task_side("markdown")

    def test_hostile_task_paths_are_bounded_developer_decisions(self) -> None:
        contract, store, _recover = self._pending_publication()
        record = store.read()
        assert record is not None and record.integrationPublication is not None
        intent = record.integrationPublication.organizationalCompletion
        assert intent is not None
        before = self._snapshots(contract, store)
        hostile = (
            ("embedded-nul\0task.json", ValueError),
            ("platform-\udcff-task.json", UnicodeEncodeError),
        )
        for name, error_type in hostile:
            with self.subTest(name=name):
                candidate = intent.model_copy(update={"masterTaskDocument": name})
                if error_type is UnicodeEncodeError:
                    failure = UnicodeEncodeError("utf-8", "x", 0, 1, "private sentinel")
                    with mock.patch.object(Path, "read_bytes", side_effect=failure):
                        decision = completion_mod.classify_organizational_master_completion(
                            candidate
                        )
                else:
                    decision = completion_mod.classify_organizational_master_completion(candidate)
                self.assertEqual(decision.state, "developer-decision")
                self.assertEqual(
                    decision.observed["readFailure"],
                    {
                        "side": "json",
                        "name": Path(name).name,
                        "errorType": error_type.__name__,
                    },
                )
                self.assertNotIn("private sentinel", repr(decision.decision_payload()))
        self.assertEqual(self._snapshots(contract, store), before)

    def test_embedded_nul_task_path_has_public_status_and_stale_handler_parity(
        self,
    ) -> None:
        contract, store, stale_recover = self._pending_publication()
        record = store.read()
        assert record is not None and record.integrationPublication is not None
        intent = record.integrationPublication.organizationalCompletion
        assert intent is not None
        hostile = intent.model_copy(update={"masterTaskDocument": "private-hostile\0task.json"})
        hostile_publication = record.integrationPublication.model_copy(
            update={"organizationalCompletion": hostile}
        )
        store.update(
            lambda current: current.model_copy(
                update={"integrationPublication": hostile_publication}
            )
        )
        before = self._snapshots(contract, store)

        projected = self._status(contract)
        with mock.patch.object(controls_mod, "launch_detached_worker") as launch:
            refused = worktree_operation_control_tool(
                self.fixture.cfg,
                OperationControlRequest(**stale_recover["arguments"]),
            )
        decision = projected["result"]
        self.assertEqual(projected["legalControls"], [])
        self.assertEqual(decision["nextAction"], "developer-decision")
        self.assertEqual(
            decision["observed"]["readFailure"],
            {
                "side": "json",
                "name": "private-hostile\0task.json",
                "errorType": "ValueError",
            },
        )
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], decision["state"])
        self.assertEqual(refused["expected"], decision["expected"])
        self.assertEqual(refused["observed"], decision["observed"])
        self.assertEqual(refused["nextAction"], decision["nextAction"])
        launch.assert_not_called()
        self.assertEqual(self._snapshots(contract, store), before)

    def test_publication_interruption_reclassifies_before_advertising_recover(self) -> None:
        contract, store, _recover = self._pending_publication()
        record = store.read()
        assert record is not None and record.integrationPublication is not None
        intent = record.integrationPublication.organizationalCompletion
        assert intent is not None
        master_json = Path(intent.masterTaskDocument)
        master_markdown = master_json.with_suffix(".md")
        before = self._snapshots(contract, store)
        success = WorktreeCommandResult(0, {"state": "integrated"})

        with mock.patch.object(
            completion_mod,
            "atomic_write_text",
            side_effect=OSError("private publication interruption"),
        ):
            interrupted = publication_mod.publish_journaled_organizational_completion(
                success,
                record.integrationPublication,
            )
        assert interrupted is not None
        self.assertEqual(
            interrupted.payload["state"],
            "organizational-completion-publication-interrupted",
        )
        self.assertEqual(interrupted.payload["nextAction"], "recover")
        self.assertNotIn("private publication interruption", repr(interrupted.payload))
        self.assertEqual(self._snapshots(contract, store), before)

        calls = 0
        real_atomic_write = completion_mod.atomic_write_text

        def write_json_then_interrupt(path: Path, text: str) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                real_atomic_write(path, text)
                return
            raise OSError("private Markdown interruption")

        with mock.patch.object(
            completion_mod,
            "atomic_write_text",
            side_effect=write_json_then_interrupt,
        ):
            partially_published = publication_mod.publish_journaled_organizational_completion(
                success,
                record.integrationPublication,
            )
        assert partially_published is not None
        self.assertEqual(
            partially_published.payload["state"],
            "organizational-completion-publication-interrupted",
        )
        self.assertEqual(partially_published.payload["nextAction"], "recover")
        self.assertEqual(master_json.read_text(encoding="utf-8"), intent.intendedJson)
        self.assertEqual(master_markdown.read_text(encoding="utf-8"), intent.acceptedMarkdown)

        completed = publication_mod.publish_journaled_organizational_completion(
            success,
            record.integrationPublication,
        )
        assert completed is success
        self.assertEqual(master_markdown.read_text(encoding="utf-8"), intent.intendedMarkdown)

    def test_unexpected_runtime_fault_stays_loud_and_nonmutating(self) -> None:
        contract, store, _recover = self._pending_publication()
        record = store.read()
        assert record is not None and record.integrationPublication is not None
        intent = record.integrationPublication.organizationalCompletion
        assert intent is not None
        before = self._snapshots(contract, store)
        with (
            mock.patch.object(
                completion_mod,
                "atomic_write_text",
                side_effect=RuntimeError("private programming fault"),
            ),
            self.assertRaisesRegex(RuntimeError, "private programming fault"),
        ):
            completion_mod.publish_organizational_master_completion(intent)
        self.assertEqual(self._snapshots(contract, store), before)


if __name__ == "__main__":
    unittest.main()
