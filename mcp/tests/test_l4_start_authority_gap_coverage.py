"""Focused refusal coverage for L4 task-derived worktree start authority."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.worktrees.modules import integrate as integrate_module
from agents_remember.worktrees.modules import start as start_module
from agents_remember.worktrees.modules import start_contract
from agents_remember.worktrees.modules.args import WorktreeArgs
from integration_branch_authority_test_support import _authority_fixture


def _enter_common_start_patches(stack: ExitStack) -> None:
    stack.enter_context(mock.patch.object(start_module, "_record_start_progress"))
    stack.enter_context(mock.patch.object(start_module, "_record_start_block"))
    stack.enter_context(
        mock.patch.object(start_module, "_parent_lineage_start_block", return_value=None)
    )
    stack.enter_context(mock.patch.object(start_module, "require_parent_series_accepting_leaves"))
    stack.enter_context(mock.patch.object(start_module, "require_ordinary_worktree"))


class StartAuthorityCoverageTests(unittest.TestCase):
    def test_preflight_records_stale_base_and_long_path_refusals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            args = WorktreeArgs(dry_run=True)
            context = SimpleNamespace()
            with (
                mock.patch.object(start_module, "_parent_lineage_start_block", return_value=None),
                mock.patch.object(
                    start_module,
                    "_stale_base_preflight",
                    return_value={"state": "blocked", "summary": "stale"},
                ),
                mock.patch.object(start_module, "_record_start_block") as record,
            ):
                result = start_module._preflighted_contract(context, fixture.leaf_contract, args)
            assert isinstance(result, integrate_module.WorktreeCommandResult)
            self.assertEqual(result.returncode, 2)
            record.assert_called_once()

            with (
                mock.patch.object(start_module, "_parent_lineage_start_block", return_value=None),
                mock.patch.object(start_module, "_stale_base_preflight", return_value=None),
                mock.patch.object(start_module, "require_ordinary_worktree"),
                mock.patch.object(
                    start_module,
                    "_long_path_preflight",
                    return_value={"state": "blocked", "summary": "long"},
                ),
            ):
                result = start_module._preflighted_contract(context, fixture.leaf_contract, args)
            assert isinstance(result, integrate_module.WorktreeCommandResult)
            self.assertEqual(result.returncode, 2)

    def test_start_enclosure_projects_each_memory_and_provider_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            contract = fixture.leaf_contract
            context = SimpleNamespace()
            blocked = integrate_module.WorktreeCommandResult(2, {"state": "blocked"})

            with ExitStack() as stack:
                _enter_common_start_patches(stack)
                stack.enter_context(
                    mock.patch.object(
                        start_module,
                        "prepare_memory_for_start",
                        return_value={"state": "blocked", "reason": "preview blocked"},
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        start_module,
                        "_blocked_memory_start_result",
                        return_value=blocked,
                    )
                )
                result = start_module._create_start_enclosure(
                    context, contract, WorktreeArgs(dry_run=True)
                )
            self.assertIs(result, blocked)

            with ExitStack() as stack:
                _enter_common_start_patches(stack)
                stack.enter_context(
                    mock.patch.object(
                        start_module,
                        "integration_authority_lock",
                        return_value=nullcontext(),
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        start_module,
                        "prepare_memory_for_start",
                        side_effect=[
                            {"state": "ready"},
                            {
                                "state": "blocked",
                                "reason": "apply blocked",
                                "choices": ["abort"],
                            },
                        ],
                    )
                )
                stack.enter_context(
                    mock.patch.object(start_module, "ensure_worktree", return_value="created")
                )
                stack.enter_context(
                    mock.patch.object(
                        start_module,
                        "_blocked_memory_start_result",
                        return_value=blocked,
                    )
                )
                result = start_module._create_start_enclosure(
                    context, contract, WorktreeArgs(dry_run=False)
                )
            self.assertIs(result, blocked)

            with ExitStack() as stack:
                _enter_common_start_patches(stack)
                stack.enter_context(
                    mock.patch.object(
                        start_module,
                        "prepare_memory_for_start",
                        return_value={"state": "ready"},
                    )
                )
                stack.enter_context(
                    mock.patch.object(start_module, "ensure_worktree", return_value="planned")
                )
                stack.enter_context(
                    mock.patch.object(
                        start_module,
                        "_contract_after_memory_start",
                        return_value=contract,
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        start_module,
                        "plan_providers_for_start",
                        return_value={"state": "blocked", "reason": "provider blocked"},
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        start_module,
                        "_blocked_provider_start_result",
                        return_value=blocked,
                    )
                )
                result = start_module._create_start_enclosure(
                    context, contract, WorktreeArgs(dry_run=True)
                )
            self.assertIs(result, blocked)

    def test_task_derived_start_helpers_refuse_wrong_master_and_source_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            context = SimpleNamespace(
                coordination_root=fixture.coordination,
                code_repository_name="repo",
                code_repository_root=fixture.code_repo,
            )
            master_ref = TaskDocumentRef(repository="repo", path="master/task.json")
            sprint_ref = TaskDocumentRef(repository="repo", path="sprint/task.json")
            master = SimpleNamespace(document=SimpleNamespace(executionNature="organizational"))
            topology = SimpleNamespace(
                canonical_ref=lambda *_args: master_ref,
                resolve=lambda ref: (
                    master
                    if ref == master_ref
                    else SimpleNamespace(document=SimpleNamespace(integrationBranch=""))
                ),
                parent=lambda ref: None if ref == master_ref else sprint_ref,
            )
            with (
                mock.patch.object(start_contract, "TaskDocumentTopology", return_value=topology),
                self.assertRaisesRegex(RuntimeError, "only an effective atomic master"),
            ):
                start_contract._declared_integration_source_branch(
                    context, fixture.leaf_contract.task_root
                )

            topology.parent = lambda _ref: sprint_ref
            with (
                mock.patch.object(start_contract, "TaskDocumentTopology", return_value=topology),
                self.assertRaisesRegex(RuntimeError, "must declare integrationBranch"),
            ):
                start_contract._declared_integration_source_branch(
                    context, fixture.leaf_contract.task_root
                )

            args = WorktreeArgs(
                task_name="master",
                worktree_name="master",
                source_branch="wrong",
            )
            with (
                mock.patch.object(
                    start_contract,
                    "_master_execution_nature",
                    return_value="organizational",
                ),
                mock.patch.object(
                    start_contract,
                    "_declared_integration_source_branch",
                    return_value="super",
                ),
                self.assertRaisesRegex(RuntimeError, "does not match its task-derived"),
            ):
                start_contract._start_source_branch(
                    context,
                    args,
                    fixture.leaf_contract.task_root,
                    None,
                    fixture.code_repo,
                )

            task_root = fixture.leaf_contract.task_root
            graph_sprint = SimpleNamespace(executionGraph=SimpleNamespace(nodes=[]))
            for nature, sprint, reason in (
                ("organizational", graph_sprint, "must not carry"),
                ("atomic", None, "would equal the integration branch"),
            ):
                with (
                    self.subTest(nature=nature),
                    mock.patch.object(
                        start_contract, "resolve_active_task_root", return_value=task_root
                    ),
                    mock.patch.object(
                        start_contract, "_master_execution_nature", return_value=nature
                    ),
                    mock.patch.object(
                        start_contract,
                        "_commanding_sprint_document",
                        return_value=sprint,
                    ),
                    self.assertRaisesRegex(RuntimeError, reason),
                ):
                    start_contract._parent_series_contract(context, args, "disabled")

            dry_args = WorktreeArgs(dry_run=True)
            external_fixture = _authority_fixture(Path(tmp) / "external", external_memory=True)
            memory_repo = external_fixture.leaf_contract.memory_repo_path
            assert memory_repo is not None
            with mock.patch.object(start_contract, "branch_exists", return_value=False):
                self.assertEqual(
                    start_contract._start_code_base(
                        fixture.code_repo,
                        "missing",
                        dry_args,
                        fixture.master_contract,
                    ),
                    fixture.master_contract.code_base_commit,
                )
                self.assertEqual(
                    start_contract._start_memory_base(
                        memory_repo,
                        "missing",
                        dry_args,
                        external_fixture.master_contract,
                    ),
                    external_fixture.master_contract.memory_base_commit,
                )


if __name__ == "__main__":
    unittest.main()
