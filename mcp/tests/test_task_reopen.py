from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest import mock

from agents_remember.application.task_docs.task_doc_tools import task_reopen_tool
from agents_remember.application.worktree_tools import (
    StartExecution,
    TaskBases,
    TaskIdentity,
    end_ambient_lifecycle_if_anchored,
    worktree_start_tool,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.mcp.tools import task_doc as task_doc_payload_module
from agents_remember.observer.ambient import AmbientLifecycle, AmbientTiming, install_ambient
from agents_remember.observer.store import EventStore
from agents_remember.tasks import (
    SprintExecutionGraph,
    TaskDocument,
    read_task_doc,
    write_task_doc,
)
from agents_remember.tasks.leaf_doc import (
    LeafLifecycleRestampBlocked,
    find_leaf_doc,
    restamp_leaf_doc_lifecycle,
)
from agents_remember.worktrees import git_worktree_manager as worktree_manager
from agents_remember.worktrees import reopen as reopen_module
from agents_remember.worktrees.modules import start as start_module
from agents_remember.worktrees.modules import start_contract as start_contract_module
from agents_remember.worktrees.modules import start_result as start_result_module
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.reopen import reopen_task
from agents_remember.worktrees.task_resolver import leaf_enclosure_path, series_contract_path
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    default_contract,
    load_contract,
    write_contract,
)
from test_worktree_support import git, init_repo


def _publish_restamp(task_root: Path, document: TaskDocument) -> object:
    return write_task_doc(task_root, document)


def _completed_leaf_contract(workspace: Path):
    coordination_root = workspace / "ar-coordination"
    code_repo = workspace / "repo-a"
    base = init_repo(code_repo, "main")
    git(code_repo, "branch", "super", "main")
    git(code_repo, "branch", "ar/01-demo-leaf", "super")
    task = ContractTask(
        name="260698_demo-series",
        repo_name="repo-a",
        coordination_root=coordination_root,
        workflow_kind="light-task",
        memory_mode="disabled",
    )
    contract = default_contract(
        task,
        leaf=LeafIdentity(worktree_name="01-demo-leaf", leaf_id="260698-l1", lifecycle_id="LC-OLD"),
        code=RepoBranchPlan(
            repo_path=code_repo,
            source_branch="super",
            work_branch="ar/01-demo-leaf",
            base_commit=base,
        ),
    )
    contract = replace(
        contract,
        human_review_status="approved",
        approved_for_commit=True,
        closeout_status="completed",
        code_commit=base,
        integration_status="completed",
        integrated_code_commit=base,
        cleanup="completed",
    )
    write_contract(contract.contract_path, contract)
    return contract


def _runtime_config(root: Path, contract: WorktreeContract) -> McpRuntimeConfig:
    repository = RepositoryScope(repo_id=contract.repo_name, path=contract.code_repo_path)
    return McpRuntimeConfig(
        config_path=contract.coordination_root / "mcp.settings.json",
        coordination_root=contract.coordination_root,
        workspace_root=root,
        transcript_root=contract.coordination_root / "logs",
        repositories={contract.repo_name: repository},
    )


def _external_memory_dirs(coordination_root: Path) -> None:
    for name in ("system", "onboarding"):
        (coordination_root / "memory-repos" / "ar-repo-a" / name).mkdir(parents=True)


def _leaf_doc(
    task_root: Path,
    *,
    lifecycle_id: str | None = "LC-OLD",
    master: str | None = "task.md",
    status: str = "Completed",
    step: dict[str, object] | None = None,
) -> Path:
    doc = TaskDocument.model_validate(
        {
            "id": "260698-L1",
            "slug": "01_demo-leaf",
            "title": "L1 — Demo leaf",
            "kind": "subTask",
            "status": status,
            "repo": "repo-a",
            "createdAt": "2026-07-01T10:00",
            "lifecycleId": lifecycle_id,
            "master": master,
            "steps": [step or {"id": "S1", "title": "do the thing", "status": "done"}],
        }
    )
    json_path, _ = write_task_doc(task_root, doc)
    return json_path


def _master_doc(
    task_root: Path,
    *,
    duplicate_row: bool = False,
    row_number: str = "260698-L1",
    row_file: str = "01_demo-leaf.md",
    statuses: tuple[str, str] = ("Completed", "Completed"),
) -> Path:
    write_task_doc(
        task_root.parent / "260698_demo-sprint",
        TaskDocument(
            id="260698_DEMO-SPRINT",
            slug="task",
            title="Demo Sprint",
            kind="master",
            status="inProgress",
            repo="repo-a",
            createdAt="2026-07-01T08:00",
            orchestrates=[task_root.name],
            integrationBranch="super",
            executionGraph=SprintExecutionGraph.model_validate(
                {
                    "nodes": [
                        {
                            "repository": "repo-a",
                            "path": f"{task_root.name}/task.json",
                        }
                    ],
                    "edges": [],
                }
            ),
        ),
    )
    status, row_status = statuses
    row = {
        "number": row_number,
        "name": "L1 — Demo leaf",
        "file": row_file,
        "status": row_status,
    }
    doc = TaskDocument.model_validate(
        {
            "id": "260698_DEMO-SERIES",
            "slug": "task",
            "title": "Demo Series",
            "kind": "master",
            "status": status,
            "repo": "repo-a",
            "createdAt": "2026-07-01T09:00",
            "executionNature": "organizational",
            "subTasks": [row, dict(row)] if duplicate_row else [row],
        }
    )
    json_path, _ = write_task_doc(task_root, doc)
    return json_path


class ReopenResetTests(unittest.TestCase):
    def test_resets_contract_doc_and_master_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            doc_path = _leaf_doc(contract.task_root)
            master_path = _master_doc(contract.task_root)

            result = reopen_task(contract.contract_path)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.payload["state"], "reopened")
            self.assertEqual(result.payload["nextOperation"], "start_reopened_task")
            self.assertEqual(result.payload["nextTool"], "worktree_start")
            next_step = cast("dict[str, object]", result.payload["nextStep"])
            self.assertEqual(next_step["nextTool"], "worktree_start")
            self.assertEqual(next_step["nextArgs"], result.payload["nextArgs"])

            reopened = load_contract(contract.contract_path)
            self.assertEqual(
                (
                    reopened.human_review_status,
                    reopened.approved_for_commit,
                    reopened.closeout_status,
                    reopened.integration_status,
                    reopened.cleanup,
                    reopened.lifecycle_id,
                    reopened.code_commit,
                    reopened.integrated_code_commit,
                ),
                ("pending-review", False, "not-started", "not-started", "reopened", "", "", ""),
            )
            self.assertEqual(reopened.leaf_id, "260698-L1")

            doc = read_task_doc(doc_path)
            self.assertEqual((doc.status, doc.lifecycleId), ("planning", None))
            self.assertTrue(any("reopened" in d.decision for d in doc.decisions))

            master = read_task_doc(master_path)
            self.assertEqual(master.subTasks[0].status, "planning")
            self.assertEqual(master.status, "inProgress")
            self.assertEqual(
                cast("dict[str, object]", result.payload["doc"])["masterIndex"], "reset"
            )

    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            doc_path = _leaf_doc(contract.task_root)
            master_path = _master_doc(contract.task_root)

            result = reopen_task(contract.contract_path, dry_run=True)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.payload["state"], "would-reopen")
            untouched = load_contract(contract.contract_path)
            self.assertEqual(
                (untouched.closeout_status, untouched.cleanup), ("completed", "completed")
            )
            doc = read_task_doc(doc_path)
            self.assertEqual((doc.status, doc.lifecycleId), ("Completed", "LC-OLD"))
            master = read_task_doc(master_path)
            self.assertEqual(master.status, "Completed")
            self.assertEqual(master.subTasks[0].status, "Completed")
            self.assertEqual(
                cast("dict[str, object]", result.payload["doc"])["masterIndex"],
                "would-reset",
            )
            self.assertEqual(result.payload["nextOperation"], "apply_task_reopen")
            self.assertEqual(result.payload["nextTool"], "task_reopen")
            next_step = cast("dict[str, object]", result.payload["nextStep"])
            self.assertEqual(next_step["nextArgs"], result.payload["nextArgs"])
            next_args = cast("dict[str, object]", result.payload["nextArgs"])
            self.assertFalse(next_args["dry_run"])

            applied = reopen_task(Path(str(next_args["contract_path"])), dry_run=False)

            self.assertEqual((applied.returncode, applied.payload["state"]), (0, "reopened"))

    def test_landing_delete_failure_refuses_and_restores_every_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            doc_path = _leaf_doc(contract.task_root)
            master_path = _master_doc(contract.task_root)
            landing = contract.contract_path.parent / "landing-final.json"
            landing.write_text('{"finished": true}\n', encoding="utf-8")
            paths = (
                contract.contract_path,
                doc_path,
                doc_path.with_suffix(".md"),
                master_path,
                master_path.with_suffix(".md"),
                landing,
            )
            before = {path: path.read_bytes() for path in paths}

            with mock.patch.object(Path, "unlink", side_effect=OSError("landing locked")):
                result = reopen_task(contract.contract_path)

            self.assertEqual((result.returncode, result.payload["state"]), (2, "blocked"))
            self.assertIn("landing locked", str(result.payload["summary"]))
            self.assertEqual({path: path.read_bytes() for path in paths}, before)

    def test_contract_publish_failure_rolls_back_docs_and_landing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            doc_path = _leaf_doc(contract.task_root)
            master_path = _master_doc(contract.task_root)
            landing = contract.contract_path.parent / "landing-final.json"
            landing.write_text('{"finished": true}\n', encoding="utf-8")
            paths = (
                contract.contract_path,
                doc_path,
                doc_path.with_suffix(".md"),
                master_path,
                master_path.with_suffix(".md"),
                landing,
            )
            before = {path: path.read_bytes() for path in paths}

            with mock.patch.object(
                reopen_module, "write_contract", side_effect=OSError("contract locked")
            ):
                result = reopen_task(contract.contract_path)

            self.assertEqual((result.returncode, result.payload["state"]), (2, "blocked"))
            self.assertIn("contract locked", str(result.payload["summary"]))
            self.assertEqual({path: path.read_bytes() for path in paths}, before)

    def test_reopen_reports_an_unrecoverable_rollback_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            _leaf_doc(contract.task_root)
            _master_doc(contract.task_root)

            with (
                mock.patch.object(
                    reopen_module, "write_contract", side_effect=OSError("contract locked")
                ),
                mock.patch.object(
                    reopen_module,
                    "_restore_reopen_artifacts",
                    side_effect=OSError("restore locked"),
                ),
                self.assertRaisesRegex(
                    RuntimeError, "reopen publication and rollback both failed: restore locked"
                ),
            ):
                reopen_task(contract.contract_path)

    def test_restore_removes_an_artifact_that_did_not_exist_before_publish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            created_during_publish = Path(tmp) / "new-artifact.json"
            created_during_publish.write_text("new\n", encoding="utf-8")

            reopen_module._restore_reopen_artifacts({created_during_publish: None})

            self.assertFalse(created_during_publish.exists())

    def test_nested_reopen_start_guidance_preserves_the_parent_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            nested = replace(
                contract,
                task_root=contract.task_root.parent / "parent-task" / contract.task_root.name,
            )

            args = reopen_module._start_preview_args(nested)

            self.assertEqual(args["parent_task"], "parent-task")

    def test_public_reopen_payload_keeps_one_coherent_recovery_for_preview_and_apply(self) -> None:
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                contract = _completed_leaf_contract(root)
                _leaf_doc(contract.task_root)
                _master_doc(contract.task_root)
                amb = AmbientLifecycle(
                    EventStore(root / "events"), timing=AmbientTiming(heartbeat_seconds=3600)
                )
                install_ambient(amb)
                try:
                    amb.start()
                    amb.promote(
                        enclosure=contract.contract_path.as_posix(),
                        repo_id=contract.repo_name,
                        scope=contract.repo_name,
                    )
                    result = reopen_task(contract.contract_path, dry_run=dry_run)
                    raw = {
                        **result.payload,
                        "ok": result.returncode == 0,
                        "operation": "task_reopen",
                    }
                    with mock.patch.object(
                        task_doc_payload_module, "task_reopen_tool", return_value=raw
                    ):
                        payload = task_doc_payload_module.task_reopen_payload(
                            mock.Mock(), contract.contract_path.as_posix(), dry_run=dry_run
                        )

                    expected_operation = "apply_task_reopen" if dry_run else "start_reopened_task"
                    expected_tool = "task_reopen" if dry_run else "worktree_start"
                    self.assertEqual(payload["nextOperation"], expected_operation)
                    self.assertEqual(payload["nextTool"], expected_tool)
                    self.assertEqual(payload["nextStep"]["nextOperation"], expected_operation)
                    self.assertEqual(payload["nextStep"]["nextTool"], expected_tool)
                    self.assertEqual(payload["nextStep"]["nextArgs"], payload["nextArgs"])
                finally:
                    install_ambient(None)  # type: ignore[arg-type]

    def test_unreadable_parent_refuses_before_leaf_or_contract_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            doc_path = _leaf_doc(contract.task_root)
            master_path = _master_doc(contract.task_root)
            contract_before = contract.contract_path.read_bytes()
            leaf_before = doc_path.read_bytes()
            master_path.write_text("{", encoding="utf-8")

            result = reopen_task(contract.contract_path)

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.payload["state"], "blocked")
            self.assertIn(
                "cannot read task document",
                " ".join(cast("list[str]", result.payload["blockers"])),
            )
            self.assertEqual(contract.contract_path.read_bytes(), contract_before)
            self.assertEqual(doc_path.read_bytes(), leaf_before)
            self.assertEqual(read_task_doc(doc_path).status, "Completed")

    def test_duplicate_parent_rows_refuse_before_leaf_or_contract_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            doc_path = _leaf_doc(contract.task_root)
            _master_doc(contract.task_root, duplicate_row=True)
            contract_before = contract.contract_path.read_bytes()
            leaf_before = doc_path.read_bytes()

            result = reopen_task(contract.contract_path)

            self.assertEqual(result.returncode, 2)
            self.assertIn(
                "exactly one row",
                " ".join(cast("list[str]", result.payload["blockers"])),
            )
            self.assertEqual(contract.contract_path.read_bytes(), contract_before)
            self.assertEqual(doc_path.read_bytes(), leaf_before)

    def test_explicit_parent_requires_exact_row_number_and_leaf_path(self) -> None:
        cases = (
            ("near-case", "260698-l1", "01_demo-leaf.md", "no exact row"),
            ("whitespace", "260698-L1 ", "01_demo-leaf.md", "no exact row"),
            ("missing", "260698-L9", "01_demo-leaf.md", "no exact row"),
            ("mispointed", "260698-L1", "09_other.md", "points at"),
        )
        for mode, row_number, row_file, expected in cases:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                contract = _completed_leaf_contract(Path(tmp))
                doc_path = _leaf_doc(contract.task_root)
                _master_doc(
                    contract.task_root,
                    row_number=row_number,
                    row_file=row_file,
                )
                contract_before = contract.contract_path.read_bytes()
                leaf_before = doc_path.read_bytes()

                result = reopen_task(contract.contract_path)

                self.assertEqual(result.returncode, 2)
                self.assertIn(
                    expected,
                    " ".join(cast("list[str]", result.payload["blockers"])),
                )
                self.assertEqual(contract.contract_path.read_bytes(), contract_before)
                self.assertEqual(doc_path.read_bytes(), leaf_before)

    def test_explicit_missing_parent_refuses_before_leaf_or_contract_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            doc_path = _leaf_doc(contract.task_root)
            contract_before = contract.contract_path.read_bytes()
            leaf_before = doc_path.read_bytes()

            result = reopen_task(contract.contract_path)

            self.assertEqual(result.returncode, 2)
            self.assertIn(
                "does not exist",
                " ".join(cast("list[str]", result.payload["blockers"])),
            )
            self.assertEqual(contract.contract_path.read_bytes(), contract_before)
            self.assertEqual(doc_path.read_bytes(), leaf_before)

    def test_leaf_without_legacy_master_field_uses_its_canonical_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            _master_doc(contract.task_root)
            doc_path = _leaf_doc(contract.task_root, master=None)

            result = reopen_task(contract.contract_path)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(
                cast("dict[str, object]", result.payload["doc"])["masterIndex"],
                "reset",
            )
            self.assertEqual(read_task_doc(doc_path).status, "planning")

    def test_refuses_a_leaf_without_a_doc_before_mutating_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            _master_doc(contract.task_root)
            before = contract.contract_path.read_bytes()
            result = reopen_task(contract.contract_path)
            self.assertEqual((result.returncode, result.payload["state"]), (2, "blocked"))
            self.assertIn("no canonical task document", str(result.payload["summary"]))
            self.assertEqual(contract.contract_path.read_bytes(), before)


class LeafDocLookupTests(unittest.TestCase):
    def test_finds_by_doc_id_case_insensitively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_root = Path(tmp)
            _leaf_doc(task_root)
            _master_doc(task_root)
            found = find_leaf_doc(task_root, "260698-L1".lower())
            self.assertIsNotNone(found)
            assert found is not None
            self.assertEqual(found[1].id, "260698-L1")

    def test_finds_by_enclosure_ref_and_stem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_root = Path(tmp)
            doc = TaskDocument.model_validate(
                {
                    "id": "unrelated-id",
                    "slug": "02_other-leaf",
                    "title": "Other",
                    "kind": "subTask",
                    "repo": "repo-a",
                    "createdAt": "2026-07-01T10:00",
                    "enclosures": [{"leafId": "260698-L2", "enclosurePath": "/e.md"}],
                }
            )
            write_task_doc(task_root, doc)
            by_ref = find_leaf_doc(task_root, "260698-l2")
            self.assertIsNotNone(by_ref)
            by_stem = find_leaf_doc(task_root, "02_OTHER-LEAF")
            self.assertIsNotNone(by_stem)
            self.assertIsNone(find_leaf_doc(task_root, "260698-l9"))

    def test_restamp_overwrites_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_root = Path(tmp)
            json_path = _leaf_doc(task_root, lifecycle_id=None)
            report = restamp_leaf_doc_lifecycle(
                task_root, "260698-l1", "LC-NEW", publish=_publish_restamp
            )
            self.assertIsNotNone(report)
            assert report is not None
            self.assertTrue(report["changed"])
            self.assertEqual(read_task_doc(json_path).lifecycleId, "LC-NEW")
            again = restamp_leaf_doc_lifecycle(
                task_root, "260698-l1", "LC-NEW", publish=_publish_restamp
            )
            assert again is not None
            self.assertFalse(again["changed"])

    def test_restamp_refuses_false_terminal_and_preserves_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_root = Path(tmp)
            json_path = _leaf_doc(
                task_root,
                step={
                    "id": "S1",
                    "title": "do the thing",
                    "status": "pending",
                    "substeps": [{"id": "S1.1", "title": "nested proof", "status": "blocked"}],
                },
            )
            markdown_path = json_path.with_suffix(".md")
            before = (json_path.read_bytes(), markdown_path.read_bytes())

            with self.assertRaises(LeafLifecycleRestampBlocked) as raised:
                restamp_leaf_doc_lifecycle(
                    task_root, "260698-l1", "LC-NEW", publish=_publish_restamp
                )

            self.assertEqual(
                [blocker.model_dump() for blocker in raised.exception.plan.blockers],
                [
                    {
                        "id": "S1",
                        "parentId": None,
                        "title": "do the thing",
                        "status": "pending",
                    },
                    {
                        "id": "S1.1",
                        "parentId": "S1",
                        "title": "nested proof",
                        "status": "blocked",
                    },
                ],
            )
            self.assertEqual((json_path.read_bytes(), markdown_path.read_bytes()), before)

    def test_same_lifecycle_is_a_true_no_write_even_for_a_legacy_invalid_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_root = Path(tmp)
            json_path = _leaf_doc(
                task_root,
                lifecycle_id="LC-SAME",
                step={"id": "S1", "title": "do the thing", "status": "pending"},
            )
            markdown_path = json_path.with_suffix(".md")
            before = (json_path.read_bytes(), markdown_path.read_bytes())

            report = restamp_leaf_doc_lifecycle(
                task_root, "260698-l1", "LC-SAME", publish=_publish_restamp
            )

            assert report is not None
            self.assertFalse(report["changed"])
            self.assertEqual((json_path.read_bytes(), markdown_path.read_bytes()), before)

    def test_nonterminal_leaf_can_restamp_with_unresolved_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_root = Path(tmp)
            json_path = _leaf_doc(
                task_root,
                status="inProgress",
                step={"id": "S1", "title": "do the thing", "status": "inProgress"},
            )

            report = restamp_leaf_doc_lifecycle(
                task_root, "260698-l1", "LC-NEW", publish=_publish_restamp
            )

            assert report is not None
            self.assertTrue(report["changed"])
            updated = read_task_doc(json_path)
            self.assertEqual((updated.status, updated.lifecycleId), ("inProgress", "LC-NEW"))

    def test_restamp_without_doc_is_a_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(
                restamp_leaf_doc_lifecycle(
                    Path(tmp), "260698-l1", "LC-NEW", publish=_publish_restamp
                )
            )


class AmbientLifecycleRetirementTests(unittest.TestCase):
    def test_ends_the_ambient_lifecycle_when_it_anchors_the_abandoned_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp))
            amb = AmbientLifecycle(store, timing=AmbientTiming(heartbeat_seconds=3600))
            install_ambient(amb)
            try:
                lc = amb.start()
                end_ambient_lifecycle_if_anchored("SOME-OTHER-LIFECYCLE", outcome="abandoned")
                self.assertIsNotNone(amb.current)  # unrelated id: untouched
                end_ambient_lifecycle_if_anchored(lc.id, outcome="abandoned")
                self.assertIsNone(amb.current)  # anchored id: owner-written ended
                kinds = [event.kind for event in store.read(lc.id)]
                self.assertEqual(kinds[-1], "lifecycle.ended")
            finally:
                install_ambient(None)  # type: ignore[arg-type]

    def test_reopen_retires_the_completed_anchor_so_start_can_mint_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = _completed_leaf_contract(root)
            _leaf_doc(contract.task_root)
            _master_doc(contract.task_root)
            _external_memory_dirs(contract.coordination_root)
            config = _runtime_config(root, contract)
            store = EventStore(root / "events")
            amb = AmbientLifecycle(
                store,
                timing=AmbientTiming(heartbeat_seconds=3600),
                id_factory=lambda: "LC-OLD",
            )
            install_ambient(amb)
            try:
                amb.start()
                amb.promote(
                    enclosure=contract.contract_path.as_posix(),
                    repo_id=contract.repo_name,
                    scope=contract.repo_name,
                )

                result = task_reopen_tool(
                    config,
                    contract_path=contract.contract_path.as_posix(),
                )

                self.assertEqual((result["ok"], result["state"]), (True, "reopened"))
                self.assertIsNone(amb.current)
                events = store.read("LC-OLD")
                self.assertEqual(events[-1].kind, "lifecycle.ended")
                self.assertEqual(events[-1].data.get("outcome"), "completed")

                identity = TaskIdentity(
                    repo_id=contract.repo_name,
                    task_name=contract.task_name,
                    worktree_name=contract.code_worktree.name,
                    leaf_id=contract.leaf_id,
                    workflow_kind=contract.workflow_kind,
                )
                bases = TaskBases(
                    source_branch=contract.code_source_branch,
                    work_branch=contract.code_work_branch,
                    memory_mode=contract.memory_mode,
                )
                preview = worktree_start_tool(
                    config,
                    identity,
                    bases=bases,
                    execution=StartExecution(dry_run=True, skip_provider_setup=True),
                )
                self.assertEqual(preview["state"], "would-start")
                self.assertIsNone(amb.current)

                started = worktree_start_tool(
                    config,
                    identity,
                    bases=bases,
                    execution=StartExecution(skip_provider_setup=True),
                )

                self.assertEqual((started["ok"], started["state"]), (True, "started"))
                fresh_id = str(started["lifecycle_id"])
                self.assertNotEqual(fresh_id, "LC-OLD")
                assert amb.current is not None
                self.assertEqual(amb.current.id, fresh_id)
                recreated = load_contract(contract.contract_path)
                self.assertEqual(recreated.lifecycle_id, fresh_id)
                found = find_leaf_doc(contract.task_root, contract.leaf_id)
                assert found is not None
                self.assertEqual(found[1].lifecycleId, fresh_id)
            finally:
                install_ambient(None)  # type: ignore[arg-type]

    def test_reopen_preview_preserves_the_completed_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = _completed_leaf_contract(root)
            _leaf_doc(contract.task_root)
            _master_doc(contract.task_root)
            config = _runtime_config(root, contract)
            store = EventStore(root / "events")
            amb = AmbientLifecycle(store, timing=AmbientTiming(heartbeat_seconds=3600))
            install_ambient(amb)
            try:
                lifecycle = amb.start()
                amb.promote(
                    enclosure=contract.contract_path.as_posix(),
                    repo_id=contract.repo_name,
                    scope=contract.repo_name,
                )

                result = task_reopen_tool(
                    config,
                    contract_path=contract.contract_path.as_posix(),
                    dry_run=True,
                )

                self.assertEqual((result["ok"], result["state"]), (True, "would-reopen"))
                assert amb.current is not None
                self.assertEqual(amb.current.id, lifecycle.id)
            finally:
                install_ambient(None)  # type: ignore[arg-type]


class StartAfterReopenTests(unittest.TestCase):
    def test_started_result_reports_background_provider_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))

            result = start_result_module.started_result(
                contract,
                WorktreeArgs(),
                "created",
                {"state": "disabled"},
                {"state": "starting", "progressFile": "/tmp/provider-progress.json"},
            )

            self.assertEqual(result.payload["state"], "started")
            self.assertIn("poll worktree_status", str(result.payload["summary"]))

    def test_start_preview_preserves_a_nested_parent_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))

            result = start_result_module.started_result(
                contract,
                WorktreeArgs(dry_run=True, parent_task="parent-task"),
                "would-create",
                {"state": "disabled"},
                {"state": "skipped"},
            )

            self.assertEqual(result.payload["state"], "would-start")
            self.assertEqual(
                cast("dict[str, object]", result.payload["nextArgs"])["parent_task"],
                "parent-task",
            )

    def test_first_master_preview_preserves_an_explicit_protected_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            planned_parent = replace(
                contract,
                parent_task_name=contract.task_name,
                parent_contract_path=contract.task_root / "missing-series-contract.md",
            )

            result = start_result_module.started_result(
                planned_parent,
                WorktreeArgs(dry_run=True, source_branch="protected"),
                "would-create",
                {"state": "disabled"},
                {"state": "skipped"},
            )

            next_args = cast("dict[str, object]", result.payload["nextArgs"])
            self.assertEqual(next_args["source_branch"], "protected")

    def test_false_terminal_leaf_blocks_absent_and_reopened_starts_before_any_effect(self) -> None:
        for contract_state in ("absent", "reopened"):
            with self.subTest(contract_state=contract_state), tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                code_repo = workspace / "repo-a"
                base_commit = init_repo(code_repo, "main")
                coordination_root = workspace / "ar-coordination"
                _external_memory_dirs(coordination_root)
                task_root = coordination_root / "tasks" / "repo-a" / "260698_demo-series"
                _master_doc(task_root)
                _leaf_doc(
                    task_root,
                    step={
                        "id": "S1",
                        "title": "do the thing",
                        "status": "pending",
                        "substeps": [{"id": "S1.1", "title": "nested proof", "status": "blocked"}],
                    },
                )
                contract = default_contract(
                    ContractTask(
                        name="260698_demo-series",
                        repo_name="repo-a",
                        coordination_root=coordination_root,
                        workflow_kind="light-task",
                        memory_mode="disabled",
                    ),
                    leaf=LeafIdentity(
                        worktree_name="01-demo-leaf",
                        leaf_id="260698-l1",
                        lifecycle_id="LC-OLD",
                    ),
                    code=RepoBranchPlan(
                        repo_path=code_repo,
                        source_branch="main",
                        work_branch="ar/01-demo-leaf",
                        base_commit=base_commit,
                    ),
                )
                if contract_state == "reopened":
                    write_contract(contract.contract_path, replace(contract, cleanup="reopened"))
                before = {
                    path.relative_to(coordination_root): path.read_bytes()
                    for path in coordination_root.rglob("*")
                    if path.is_file()
                }

                with (
                    mock.patch.object(
                        start_contract_module, "_require_bootstrap_ref"
                    ) as publish_branch,
                    mock.patch.object(start_contract_module, "write_contract") as publish_contract,
                    mock.patch.object(start_module, "ensure_worktree") as ensure_worktree,
                    mock.patch.object(start_module, "prepare_memory_for_start") as prepare_memory,
                    mock.patch.object(start_module, "plan_providers_for_start") as plan_providers,
                    mock.patch.object(
                        start_module, "run_or_launch_provider_setup"
                    ) as launch_providers,
                    mock.patch.object(start_module, "_record_start_progress") as start_progress,
                    mock.patch.object(
                        start_module, "restamp_leaf_doc_lifecycle"
                    ) as persist_restamp,
                ):
                    result = worktree_manager.start_result(
                        WorktreeArgs(
                            code_repository_name="repo-a",
                            workspace_root=workspace,
                            coordination_root=coordination_root,
                            code_repository_root=code_repo,
                            topology="external",
                            task_name="260698_demo-series",
                            worktree_name="01-demo-leaf",
                            leaf_id="260698-l1",
                            workflow_kind="light-task",
                            memory_mode="disabled",
                            skip_provider_setup=True,
                            lifecycle_id="LC-NEW",
                        )
                    )

                self.assertEqual(
                    (result.returncode, result.payload["state"]), (2, "task-steps-blocked")
                )
                self.assertEqual(
                    result.payload["blockers"],
                    [
                        {
                            "id": "S1",
                            "parentId": None,
                            "title": "do the thing",
                            "status": "pending",
                        },
                        {
                            "id": "S1.1",
                            "parentId": "S1",
                            "title": "nested proof",
                            "status": "blocked",
                        },
                    ],
                )
                after = {
                    path.relative_to(coordination_root): path.read_bytes()
                    for path in coordination_root.rglob("*")
                    if path.is_file()
                }
                self.assertEqual(after, before)
                self.assertFalse(series_contract_path(task_root).exists())
                self.assertFalse(contract.code_worktree.exists())
                publish_branch.assert_not_called()
                publish_contract.assert_not_called()
                ensure_worktree.assert_not_called()
                prepare_memory.assert_not_called()
                plan_providers.assert_not_called()
                launch_providers.assert_not_called()
                start_progress.assert_not_called()
                persist_restamp.assert_not_called()

    def test_start_recreates_fresh_and_restamps_the_doc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            code_repo = workspace / "repo-a"
            base_commit = init_repo(code_repo, "main")
            git(code_repo, "branch", "super", "main")
            coordination_root = workspace / "ar-coordination"
            _external_memory_dirs(coordination_root)
            task_root = coordination_root / "tasks" / "repo-a" / "260698_demo-series"
            _master_doc(task_root, statuses=("inProgress", "planning"))
            doc_path = _leaf_doc(
                task_root,
                lifecycle_id=None,
                status="planning",
                step={"id": "S1", "title": "do the thing", "status": "pending"},
            )

            contract = default_contract(
                ContractTask(
                    name="260698_demo-series",
                    repo_name="repo-a",
                    coordination_root=coordination_root,
                    workflow_kind="light-task",
                    memory_mode="disabled",
                ),
                leaf=LeafIdentity(worktree_name="01-demo-leaf", leaf_id="260698-l1"),
                code=RepoBranchPlan(
                    repo_path=code_repo,
                    source_branch="super",
                    work_branch="ar/01-demo-leaf",
                    base_commit=base_commit,
                ),
            )
            contract = replace(contract, cleanup="reopened")
            write_contract(contract.contract_path, contract)

            result = worktree_manager.start_result(
                WorktreeArgs(
                    code_repository_name="repo-a",
                    workspace_root=workspace,
                    coordination_root=coordination_root,
                    code_repository_root=code_repo,
                    topology="external",
                    task_name="260698_demo-series",
                    worktree_name="01-demo-leaf",
                    leaf_id="260698-l1",
                    workflow_kind="light-task",
                    memory_mode="disabled",
                    skip_provider_setup=True,
                    lifecycle_id="LC-NEW",
                )
            )

            self.assertEqual(result.returncode, 0)
            self.assertNotEqual(result.payload.get("state"), "attached-existing-contract")
            recreated = load_contract(leaf_enclosure_path(task_root, "260698-l1"))
            self.assertEqual(recreated.lifecycle_id, "LC-NEW")
            self.assertEqual(recreated.cleanup, "pending")
            self.assertEqual(recreated.closeout_status, "not-started")
            self.assertEqual(read_task_doc(doc_path).lifecycleId, "LC-NEW")

    def test_start_preview_is_actionable_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            code_repo = workspace / "repo-a"
            base_commit = init_repo(code_repo, "main")
            git(code_repo, "branch", "super", "main")
            coordination_root = workspace / "ar-coordination"
            _external_memory_dirs(coordination_root)
            task_root = coordination_root / "tasks" / "repo-a" / "260698_demo-series"
            _master_doc(task_root, statuses=("inProgress", "planning"))
            doc_path = _leaf_doc(
                task_root,
                lifecycle_id=None,
                status="planning",
                step={"id": "S1", "title": "do the thing", "status": "pending"},
            )
            contract = replace(
                default_contract(
                    ContractTask(
                        name="260698_demo-series",
                        repo_name="repo-a",
                        coordination_root=coordination_root,
                        workflow_kind="light-task",
                        memory_mode="disabled",
                    ),
                    leaf=LeafIdentity(worktree_name="01-demo-leaf", leaf_id="260698-l1"),
                    code=RepoBranchPlan(
                        repo_path=code_repo,
                        source_branch="super",
                        work_branch="ar/01-demo-leaf",
                        base_commit=base_commit,
                    ),
                ),
                cleanup="reopened",
            )
            write_contract(contract.contract_path, contract)
            before = {
                path.relative_to(coordination_root): path.read_bytes()
                for path in coordination_root.rglob("*")
                if path.is_file()
            }

            result = worktree_manager.start_result(
                WorktreeArgs(
                    code_repository_name="repo-a",
                    workspace_root=workspace,
                    coordination_root=coordination_root,
                    code_repository_root=code_repo,
                    topology="external",
                    task_name="260698_demo-series",
                    worktree_name="01-demo-leaf",
                    leaf_id="260698-l1",
                    workflow_kind="light-task",
                    memory_mode="disabled",
                    skip_provider_setup=True,
                    lifecycle_id="LC-NEW",
                    dry_run=True,
                )
            )

            self.assertEqual((result.returncode, result.payload["state"]), (0, "would-start"))
            self.assertEqual(result.payload["nextOperation"], "apply_worktree_start")
            self.assertEqual(result.payload["nextTool"], "worktree_start")
            next_args = cast("dict[str, object]", result.payload["nextArgs"])
            next_step = cast("dict[str, object]", result.payload["nextStep"])
            self.assertFalse(next_args["dry_run"])
            self.assertEqual(next_step["nextArgs"], next_args)
            self.assertEqual(read_task_doc(doc_path).lifecycleId, None)
            after = {
                path.relative_to(coordination_root): path.read_bytes()
                for path in coordination_root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(after, before)
            self.assertFalse(contract.code_worktree.exists())
            self.assertEqual(next_args["source_branch"], "super")

            applied = worktree_manager.start_result(
                WorktreeArgs(
                    code_repository_name=str(next_args["repo_id"]),
                    workspace_root=workspace,
                    coordination_root=coordination_root,
                    code_repository_root=code_repo,
                    topology="external",
                    task_name=str(next_args["task_name"]),
                    worktree_name=str(next_args["worktree_name"]),
                    leaf_id=str(next_args["leaf_id"]),
                    workflow_kind=str(next_args["workflow_kind"]),
                    source_branch=(
                        str(next_args["source_branch"]) if "source_branch" in next_args else None
                    ),
                    work_branch=str(next_args["work_branch"]),
                    memory_mode=str(next_args["memory_mode"]),
                    skip_provider_setup=bool(next_args["skip_provider_setup"]),
                    lifecycle_id="LC-NEW",
                    dry_run=bool(next_args["dry_run"]),
                )
            )

            self.assertEqual((applied.returncode, applied.payload["state"]), (0, "started"))
            self.assertFalse(series_contract_path(task_root).exists())
            recreated = load_contract(leaf_enclosure_path(task_root, "260698-l1"))
            self.assertEqual(recreated.code_source_branch, "super")
            self.assertEqual(recreated.lifecycle_id, "LC-NEW")
            self.assertEqual(read_task_doc(doc_path).lifecycleId, "LC-NEW")

    def test_start_still_attaches_a_live_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            code_repo = workspace / "repo-a"
            init_repo(code_repo, "main")
            git(code_repo, "branch", "super", "main")
            coordination_root = workspace / "ar-coordination"
            _external_memory_dirs(coordination_root)
            task_root = coordination_root / "tasks" / "repo-a" / "260698_demo-series"
            _master_doc(task_root)

            initial = worktree_manager.start_result(
                WorktreeArgs(
                    code_repository_name="repo-a",
                    workspace_root=workspace,
                    coordination_root=coordination_root,
                    code_repository_root=code_repo,
                    topology="external",
                    task_name="260698_demo-series",
                    worktree_name="01-demo-leaf",
                    leaf_id="260698-l1",
                    workflow_kind="light-task",
                    memory_mode="disabled",
                    skip_provider_setup=True,
                    lifecycle_id="LC-LIVE",
                )
            )
            self.assertEqual(initial.returncode, 0)

            result = worktree_manager.start_result(
                WorktreeArgs(
                    code_repository_name="repo-a",
                    workspace_root=workspace,
                    coordination_root=coordination_root,
                    code_repository_root=code_repo,
                    topology="external",
                    task_name="260698_demo-series",
                    worktree_name="01-demo-leaf",
                    leaf_id="260698-l1",
                    workflow_kind="light-task",
                    memory_mode="disabled",
                    skip_provider_setup=True,
                    lifecycle_id="LC-NEW",
                )
            )

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.payload["state"], "attached-existing-contract")
            self.assertEqual(
                load_contract(leaf_enclosure_path(task_root, "260698-l1")).lifecycle_id,
                "LC-LIVE",
            )


if __name__ == "__main__":
    unittest.main()
