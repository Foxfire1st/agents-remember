from __future__ import annotations

import json
import tempfile
from pathlib import Path

from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.models.closeout.input import EffectiveCloseoutInput, EnabledCloseoutLeg
from agents_remember.models.closeout.source import CandidateAdmissionFacts, SchedulingGradeInput
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.lifecycles.door import CloseoutDoorRequest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import (
    Section,
    TaskDocument,
    TaskEnclosureRef,
    read_task_doc,
    write_task_doc,
)
from agents_remember.worktrees import git_worktree_manager as worktree_manager
from agents_remember.worktrees.integration.closeout.door_control import closeout_door_tool
from agents_remember.worktrees.modules import closeout as closeout_module
from agents_remember.worktrees.queue.closeout_queue import (
    CloseoutQueueRequest,
    QueueActor,
    closeout_queue_tool,
)
from agents_remember.worktrees.task_resolver import (
    leaf_enclosure_path,
    series_contract_path,
)
from agents_remember.worktrees.worktree_contract import (
    load_contract,
)
from curator_coherence_test_support import write_curator_evidence
from test_worktree_support import (
    WorktreeSupportTests,
    git,
    init_repo,
    initialized_memory_repo,
)


def _publish_and_read_leaf_queue(
    workspace: Path,
    coordination_root: Path,
    leaf_contract,
) -> dict[str, object]:
    candidate_ref = "repo-a/260624_master/15_leaf.json"
    sprint_root = coordination_root / "tasks" / "repo-a" / "260624_sprint"
    (sprint_root / "grade.md").write_text("# Grade\n", encoding="utf-8")
    sprint_document = read_task_doc(sprint_root / "task.json")
    write_task_doc(
        sprint_root,
        sprint_document.model_copy(
            update={
                "sections": [
                    Section.model_validate(
                        {
                            "kind": "freeform",
                            "heading": "Judgment Register (canonical judgment authority)",
                            "body": (
                                "| Judgment id | Kind (dependency meaning, execution nature, "
                                "blast radius, priority, blocker placement, reprioritization, "
                                "or leaf move) | Subject | Decision | Rationale | "
                                "Evidence/fact refs | Author | Confidence | Supersedes |\n"
                                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                                f"| J-15-normal | priority | {candidate_ref} | priority=normal | "
                                "Start candidate | grade.md | orchestrator | high | |"
                            ),
                        }
                    ),
                    Section.model_validate(
                        {
                            "kind": "freeform",
                            "heading": "Priority Register (explicit judgment)",
                            "body": (
                                "| Candidate/master | Grade (critical, high, normal, or low) | "
                                "Affected dependents | Judgment id |\n"
                                "| --- | --- | --- | --- |\n"
                                f"| {candidate_ref} | normal | none | J-15-normal |"
                            ),
                        }
                    ),
                ]
            }
        ),
    )
    settings_path = workspace / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": coordination_root.as_posix(),
                "workspaceRoot": workspace.as_posix(),
                "repositories": {"repo-a": {}},
            }
        ),
        encoding="utf-8",
    )
    config = load_config(settings_path)
    sprint_ref = TaskDocumentRef(repository="repo-a", path="260624_sprint/task.json")
    write_curator_evidence(leaf_contract, caller_ref=sprint_ref)
    declared = closeout_door_tool(
        config,
        CloseoutDoorRequest(
            action="declare",
            contract_path=leaf_contract.contract_path.as_posix(),
            grade=SchedulingGradeInput(priority="normal", judgmentId="J-15-normal"),
            admission=CandidateAdmissionFacts(),
        ),
        actor=DeclaredCaller(
            role="manager",
            task_document_ref=TaskDocumentRef(repository="repo-a", path="260624_master/task.json"),
        ),
        admitted_contract=leaf_contract,
    )
    assert declared["ok"], declared
    return closeout_queue_tool(
        config,
        CloseoutQueueRequest(action="status", sprint_task_document_ref=sprint_ref),
        actor=QueueActor(role="orchestrator", task_document_ref=sprint_ref),
        now="2026-06-24T03:00:00+00:00",
    )


def _assert_early_closeout_binding_refusals(
    testcase, task_root: Path, leaf_contract, document
) -> None:
    leg = EnabledCloseoutLeg(state="enabled", reason="test", message="test")
    effective = EffectiveCloseoutInput(
        route="worktree",
        contractKind="leaf",
        memoryMode=leaf_contract.memory_mode,
        code=leg,
        memory=leg,
        ledger=leg,
    )
    args = worktree_manager.WorktreeArgs(
        contract_path=leaf_contract.contract_path,
        closeout_input=effective,
        dry_run=True,
    )
    cases = (
        (document.model_copy(update={"enclosures": []}), "task-enclosure-binding-missing"),
        (
            document.model_copy(
                update={
                    "enclosures": [
                        TaskEnclosureRef(
                            leafId=leaf_contract.leaf_id,
                            enclosurePath=(task_root / "wrong-contract.md").as_posix(),
                        )
                    ]
                }
            ),
            "task-enclosure-binding-mismatched",
        ),
    )
    for candidate, status in cases:
        write_task_doc(task_root, candidate)
        _contract, _input, refusal = closeout_module._closeout_entry(args, leaf_contract)
        assert refusal is not None
        testcase.assertEqual(refusal.returncode, 2)
        testcase.assertEqual(refusal.payload["status"], status)
        testcase.assertEqual(refusal.payload["leafId"], leaf_contract.leaf_id)
        testcase.assertEqual(
            refusal.payload["taskDocument"], (task_root / "15_leaf.json").as_posix()
        )
        testcase.assertEqual(
            refusal.payload["contractPath"], leaf_contract.contract_path.as_posix()
        )
        testcase.assertIn("task_doc.replace", refusal.payload["recoveryOperation"])
        testcase.assertIn("worktree_start", refusal.payload["recoveryOperation"])
    write_task_doc(task_root, document)


class WorktreeSupport1(WorktreeSupportTests):
    def test_master_start_and_abandon_preserve_parent_series(self) -> None:  # noqa: PLR0915
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            code_repo = workspace / "repo-a"
            code_base = init_repo(code_repo, "main")
            git(code_repo, "branch", "super", "main")
            coordination_root = workspace / "ar-coordination"
            memory_root = coordination_root / "memory-repos" / "ar-repo-a"
            initialized_memory_repo(memory_root, "repo-a", "main", "main", code_base)
            git(memory_root, "branch", "super", "main")
            task_root = coordination_root / "tasks" / "repo-a" / "260624_master"
            write_task_doc(
                coordination_root / "tasks" / "repo-a" / "260624_sprint",
                TaskDocument.model_validate(
                    {
                        "id": "sprint",
                        "slug": "task",
                        "title": "Sprint",
                        "kind": "master",
                        "status": "inProgress",
                        "repo": "repo-a",
                        "createdAt": "2026-06-24T01:00",
                        "orchestrates": ["260624_master"],
                        "integrationBranch": "super",
                        "executionGraph": {
                            "nodes": [
                                {
                                    "repository": "repo-a",
                                    "path": "260624_master/task.json",
                                }
                            ],
                            "edges": [],
                        },
                    }
                ),
            )
            write_task_doc(
                task_root,
                TaskDocument.model_validate(
                    {
                        "id": "master",
                        "slug": "task",
                        "title": "Master Series",
                        "kind": "master",
                        "status": "inProgress",
                        "repo": "repo-a",
                        "createdAt": "2026-06-24T02:00",
                        "executionNature": "atomic",
                        "subTasks": [
                            {
                                "number": "15",
                                "name": "Leaf task",
                                "file": "15_leaf.md",
                                "status": "inProgress",
                            }
                        ],
                    }
                ),
            )
            write_task_doc(
                task_root,
                TaskDocument.model_validate(
                    {
                        "id": "15",
                        "slug": "15_leaf",
                        "title": "Leaf task",
                        "kind": "subTask",
                        "status": "inProgress",
                        "repo": "repo-a",
                        "createdAt": "2026-06-24T02:01",
                        "master": "task.md",
                    }
                ),
            )

            result = worktree_manager.start_result(
                worktree_manager.WorktreeArgs(
                    code_repository_name="repo-a",
                    workspace_root=workspace,
                    coordination_root=coordination_root,
                    code_repository_root=code_repo,
                    topology="external",
                    task_name="260624_master",
                    worktree_name="15_leaf",
                    leaf_id="15_leaf",
                    workflow_kind="light-task",
                    memory_mode="external",
                    skip_provider_setup=True,
                    lifecycle_id="LC-LEAF",
                )
            )

            self.assertEqual(result.returncode, 0)
            root_contract = load_contract(series_contract_path(task_root))
            leaf_contract = load_contract(leaf_enclosure_path(task_root, "15"))
            self.assertEqual(
                (root_contract.kind, root_contract.code_source_branch), ("series", "super")
            )
            self.assertEqual(root_contract.code_work_branch, "ar/260624_master")
            self.assertEqual(root_contract.code_worktree, code_repo)
            self.assertEqual((leaf_contract.kind, leaf_contract.leaf_id), ("leaf", "15"))
            self.assertEqual(leaf_contract.code_source_branch, "ar/260624_master")
            self.assertEqual(leaf_contract.code_work_branch, "ar/15_leaf")
            self.assertEqual(leaf_contract.parent_contract_path, root_contract.contract_path)
            self.assertEqual(
                result.payload["enclosure_path"], leaf_contract.contract_path.as_posix()
            )
            leaf_document = read_task_doc(task_root / "15_leaf.json")
            self.assertEqual(leaf_document.lifecycleId, "LC-LEAF")
            self.assertEqual(
                [ref.model_dump() for ref in leaf_document.enclosures],
                [
                    {
                        "leafId": "15",
                        "enclosurePath": leaf_contract.contract_path.as_posix(),
                    }
                ],
            )
            reattached = worktree_manager.attach_result(
                worktree_manager.WorktreeArgs(contract_path=leaf_contract.contract_path)
            )
            self.assertEqual(reattached.returncode, 0, reattached.payload)
            self.assertEqual(
                read_task_doc(task_root / "15_leaf.json").enclosures,
                leaf_document.enclosures,
            )
            _assert_early_closeout_binding_refusals(
                self,
                task_root,
                leaf_contract,
                leaf_document,
            )
            queue = _publish_and_read_leaf_queue(workspace, coordination_root, leaf_contract)
            self.assertEqual(queue["state"], "valid-built", queue)
            self.assertEqual(queue["sourceProblems"], [], queue)
            members = queue.get("members")
            assert isinstance(members, list)
            self.assertEqual(len(members), 1, queue)
            member = members[0]
            assert isinstance(member, dict)
            task_document_ref = member.get("taskDocumentRef")
            assert isinstance(task_document_ref, dict)
            self.assertEqual(
                task_document_ref.get("path"),
                "260624_master/15_leaf.json",
            )
            self.assertIn(
                "ar/260624_master", git(code_repo, "branch", "--list", "ar/260624_master")
            )
            self.assertIn("ar/15_leaf", git(code_repo, "branch", "--list", "ar/15_leaf"))

            abandoned = worktree_manager.abandon_result(
                worktree_manager.WorktreeArgs(
                    contract_path=leaf_contract.contract_path,
                    approved=True,
                    teardown_providers=False,
                )
            )
            self.assertEqual(abandoned.returncode, 0, abandoned.payload)
            self.assertEqual(abandoned.payload["state"], "abandoned")
            self.assertFalse(leaf_contract.code_worktree.exists())
            self.assertEqual(git(code_repo, "branch", "--list", "ar/15_leaf"), "")
            self.assertTrue(root_contract.contract_path.exists())
            self.assertIn(
                "ar/260624_master", git(code_repo, "branch", "--list", "ar/260624_master")
            )
