from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from argparse import Namespace
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

import agents_remember.providers.provider_setup as provider_setup_api
from agents_remember.application import provider_runtime as provider_async
from agents_remember.application.worktree_tools import _settings_owned_by_background
from agents_remember.providers.setup_progress import read_setup_progress
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.modules import abandon as worktree_abandon
from agents_remember.worktrees.modules import cleanup as worktree_cleanup
from agents_remember.worktrees.modules import start as worktree_start
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.guidance import projected_status_payload
from agents_remember.worktrees.modules.models import WorktreeProviderSetupConfig
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    write_contract,
)
from test_worktree_support import git, init_repo


def make_contract(root: Path):
    coordination_root = root / "ar-coordination"
    repo = root / "repo-a"
    base = init_repo(repo, "main")
    git(repo, "update-ref", "refs/remotes/origin/main", base)
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    git(repo, "branch", "super", "main")
    task_root = coordination_root / "tasks" / "repo-a" / "async-task"
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": "ASYNC-MASTER",
                "slug": "task",
                "title": "Async master",
                "kind": "master",
                "repo": "repo-a",
                "createdAt": "2026-08-15T00:00:00+00:00",
                "executionNature": "organizational",
                "subTasks": [
                    {
                        "number": "ASYNC-LEAF",
                        "name": "Async leaf",
                        "file": "async-task.md",
                        "status": "planning",
                    }
                ],
            }
        ),
    )
    write_task_doc(
        task_root.parent / "sprint",
        TaskDocument.model_validate(
            {
                "id": "ASYNC-SPRINT",
                "slug": "task",
                "title": "Async sprint",
                "kind": "master",
                "repo": "repo-a",
                "createdAt": "2026-08-15T00:00:00+00:00",
                "orchestrates": ["async-task"],
                "integrationBranch": "super",
                "executionGraph": {
                    "nodes": [{"repository": "repo-a", "path": "async-task/task.json"}],
                    "edges": [],
                },
            }
        ),
    )
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": "ASYNC-LEAF",
                "slug": "async-task",
                "title": "Async leaf",
                "kind": "subTask",
                "repo": "repo-a",
                "createdAt": "2026-08-15T00:01:00+00:00",
                "master": "task.md",
            }
        ),
    )
    contract = default_contract(
        ContractTask(
            name="async-task",
            repo_name="repo-a",
            coordination_root=coordination_root,
            workflow_kind="light-task",
            memory_mode="disabled",
        ),
        leaf=LeafIdentity(worktree_name="async-task", leaf_id="ASYNC-LEAF"),
        code=RepoBranchPlan(
            repo_path=repo,
            source_branch="super",
            work_branch="ar/async-task",
            base_commit=base,
        ),
        memory=RepoBranchPlan(repo_path=None, source_branch="", work_branch="", base_commit=""),  # type: ignore[arg-type]
    )
    contract.worktree_group.mkdir(parents=True, exist_ok=True)
    return contract


class CapturedThreads:
    def __init__(self) -> None:
        self.threads: list[threading.Thread] = []

    def __call__(self, **kwargs: Any) -> threading.Thread:
        thread = threading.Thread(**kwargs)
        self.threads.append(thread)
        return thread

    def join_all(self) -> None:
        for thread in self.threads:
            thread.join(timeout=10)
            assert not thread.is_alive(), "setup thread did not finish"


class LaunchProviderSetupTests(unittest.TestCase):
    def _launch(self, root: Path, runner, *, settings_cleanup: Path | None = None):
        contract = make_contract(root)
        state_files: list[Path] = []

        def write_state_file(payload: dict[str, Any]) -> Path:
            state_file = contract.worktree_group / "provider-runtime" / "provider-state.json"
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(json.dumps(payload), encoding="utf-8")
            state_files.append(state_file)
            return state_file

        threads = CapturedThreads()
        result = provider_async.launch_provider_setup(
            provider_async.ProviderSetupJob(
                request=mock.Mock(),
                contract=contract,
                write_state_file=write_state_file,
                settings_cleanup=settings_cleanup,
            ),
            runner=runner,
            thread_factory=threads,
        )
        threads.join_all()
        return contract, result, state_files

    def test_successful_setup_writes_state_file_and_finishes_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = root / "temp-settings.json"
            settings.write_text("{}", encoding="utf-8")
            captured: dict[str, Any] = {}

            def runner(request, progress):
                captured["progress"] = progress
                progress.phase_start("grepai", "install")
                progress.phase_done({"ok": True, "provider": "grepai", "action": "install"})
                return {"ok": True, "state": "ok", "resultCounts": {"total": 1, "ok": 1}}

            _, result, state_files = self._launch(root, runner, settings_cleanup=settings)

            self.assertEqual(result["state"], "starting")
            self.assertIn("progressFile", result)
            self.assertEqual(result["pollTool"], "worktree_status")
            progress = read_setup_progress(Path(result["progressFile"]))
            assert progress is not None
            self.assertEqual(progress["state"], "ok")
            self.assertEqual(progress["repoName"], "repo-a")
            self.assertEqual(progress["summary"]["providerStateFile"], state_files[0].as_posix())
            self.assertFalse(settings.exists(), "thread owns the settings unlink")

    def test_failed_payload_finishes_failed_without_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def runner(request, progress):
                return {"ok": False, "state": "failed", "resultCounts": {"failed": 1}}

            _, result, state_files = self._launch(root, runner)
            progress = read_setup_progress(Path(result["progressFile"]))
            assert progress is not None
            self.assertEqual(progress["state"], "failed")
            self.assertEqual(state_files, [])

    def test_runner_exception_finishes_failed_and_cleans_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = root / "temp-settings.json"
            settings.write_text("{}", encoding="utf-8")

            def runner(request, progress):
                raise RuntimeError("docker exploded")

            _, result, state_files = self._launch(root, runner, settings_cleanup=settings)
            progress = read_setup_progress(Path(result["progressFile"]))
            assert progress is not None
            self.assertEqual(progress["state"], "failed")
            self.assertEqual(progress["error"], "RuntimeError: docker exploded")
            self.assertFalse(settings.exists())
            self.assertEqual(state_files, [])


class ProviderSetupStatusTests(unittest.TestCase):
    def test_no_progress_and_no_state_file_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = make_contract(Path(tmp))
            self.assertIsNone(provider_async.provider_setup_status(contract))
            self.assertFalse(provider_async.provider_setup_running(contract))

    def test_legacy_state_file_projects_prepared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = make_contract(Path(tmp))
            state_file = contract.worktree_group / "provider-runtime" / "provider-state.json"
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text("{}", encoding="utf-8")
            self.assertEqual(provider_async.provider_setup_status(contract), {"state": "prepared"})

    def test_a_prepared_stack_reaches_the_status_payload(self) -> None:
        """`providers` is only attached when there is something to report, and it was never
        proven to reach the payload -- only that `provider_setup_status` computes it."""
        with tempfile.TemporaryDirectory() as tmp:
            contract = make_contract(Path(tmp))
            without = projected_status_payload(contract, landing=None)
            self.assertNotIn("providers", without)

            state_file = contract.worktree_group / "provider-runtime" / "provider-state.json"
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text("{}", encoding="utf-8")

            payload = projected_status_payload(contract, landing=None)
            self.assertEqual(payload.get("providers"), {"state": "prepared"})

    def test_failed_progress_carries_retry_args(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = make_contract(Path(tmp))
            threads = CapturedThreads()

            def runner(request, progress):
                return {"ok": False, "state": "failed"}

            provider_async.launch_provider_setup(
                provider_async.ProviderSetupJob(
                    request=mock.Mock(),
                    contract=contract,
                    write_state_file=lambda payload: Path(tmp) / "unused.json",
                    settings_cleanup=None,
                ),
                runner=runner,
                thread_factory=threads,
            )
            threads.join_all()
            status = provider_async.provider_setup_status(contract)
            assert status is not None
            self.assertEqual(status["state"], "failed")
            self.assertEqual(
                status["retryArgs"],
                {
                    "repo_id": "repo-a",
                    "task_name": "async-task",
                    "worktree_name": contract.code_worktree.name,
                    "retry_provider_setup": True,
                },
            )
            self.assertFalse(provider_async.provider_setup_running(contract))


class StartOrderingTests(unittest.TestCase):
    def test_contract_is_written_before_provider_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = make_contract(root)
            contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
            context = Namespace(
                code_repository_name="repo-a",
                code_repository_root=root / "repo-a",
                coordination_root=root / "ar-coordination",
                memory_root=None,
            )
            observed: dict[str, Any] = {}

            def fake_launch(context_arg, contract_arg, args_arg, plan_arg):
                observed["contract_exists_at_launch"] = contract_arg.contract_path.exists()
                return {"state": "starting", "progressFile": "x"}

            args = WorktreeArgs(task_name="async-task", worktree_name="async-task")
            with (
                mock.patch.object(worktree_start, "resolve_context", return_value=context),
                mock.patch.object(worktree_start, "build_start_contract", return_value=contract),
                mock.patch.object(worktree_start, "parent_source_lineage", return_value=None),
                mock.patch.object(worktree_start, "ensure_worktree", return_value="created"),
                mock.patch.object(
                    worktree_start,
                    "prepare_memory_for_start",
                    return_value={"state": "disabled"},
                ),
                mock.patch.object(
                    worktree_start,
                    "plan_providers_for_start",
                    return_value={"state": "enabled", "paths": object()},
                ),
                mock.patch.object(
                    worktree_start, "run_or_launch_provider_setup", side_effect=fake_launch
                ),
            ):
                result = worktree_start.start_result(args)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.payload["state"], "started")
            self.assertTrue(observed["contract_exists_at_launch"])
            providers = cast(dict[str, Any], result.payload["providers"])
            self.assertEqual(providers["state"], "starting")
            self.assertIn("background", cast(str, result.payload["summary"]))

    def test_run_or_launch_dry_run_stays_synchronous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = make_contract(root)
            paths = mock.Mock(spec=worktree_start.ProviderStartPaths)
            args = WorktreeArgs(dry_run=True)
            with (
                mock.patch.object(
                    worktree_start, "_provider_setup_request", return_value=mock.Mock()
                ),
                mock.patch.object(
                    provider_setup_api,
                    "run_provider_setup",
                    return_value={"ok": True, "results": []},
                ) as run_mock,
                mock.patch.object(provider_async, "launch_provider_setup") as launch_mock,
            ):
                state = worktree_start.run_or_launch_provider_setup(
                    Namespace(), contract, args, {"state": "enabled", "paths": paths}
                )
            self.assertEqual(state["state"], "planned")
            run_mock.assert_called_once()
            launch_mock.assert_not_called()

    def test_run_or_launch_transfers_settings_only_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = make_contract(root)
            settings_path = root / "temp-settings.json"
            paths = mock.Mock(spec=worktree_start.ProviderStartPaths)
            paths.provider_settings_path = settings_path
            for unlink_after, expected_cleanup in ((True, settings_path), (False, None)):
                config = WorktreeProviderSetupConfig(
                    coordination_root=root,
                    settings_path=settings_path,
                    unlink_settings_after_setup=unlink_after,
                )
                args = WorktreeArgs(provider_setup_config=config)
                with (
                    mock.patch.object(
                        worktree_start, "_provider_setup_request", return_value=mock.Mock()
                    ),
                    mock.patch.object(provider_async, "launch_provider_setup") as launch_mock,
                ):
                    worktree_start.run_or_launch_provider_setup(
                        Namespace(), contract, args, {"state": "enabled", "paths": paths}
                    )
                self.assertEqual(launch_mock.call_args.args[0].settings_cleanup, expected_cleanup)


class RetryProviderSetupTests(unittest.TestCase):
    def _existing_contract(self, root: Path):
        contract = make_contract(root)
        contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
        write_contract(contract.contract_path, contract)
        return contract

    def test_retry_refused_while_setup_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = self._existing_contract(Path(tmp))
            args = WorktreeArgs(task_name="async-task", retry_provider_setup=True)
            with mock.patch.object(provider_async, "provider_setup_running", return_value=True):
                result = worktree_start._retry_provider_setup_result(Namespace(), contract, args)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.payload["state"], "blocked")
            self.assertEqual(result.payload["nextTool"], "worktree_status")

    def test_retry_relaunches_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = self._existing_contract(Path(tmp))
            args = WorktreeArgs(task_name="async-task", retry_provider_setup=True)
            with (
                mock.patch.object(
                    provider_async,
                    "provider_setup_running",
                    return_value=False,
                ),
                mock.patch.object(
                    worktree_start,
                    "plan_providers_for_start",
                    return_value={"state": "enabled", "paths": object()},
                ),
                mock.patch.object(
                    worktree_start,
                    "run_or_launch_provider_setup",
                    return_value={"state": "starting", "progressFile": "x"},
                ),
            ):
                result = worktree_start._retry_provider_setup_result(Namespace(), contract, args)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.payload["state"], "provider-setup-retried")
            providers = cast(dict[str, Any], result.payload["providers"])
            self.assertEqual(providers["state"], "starting")


class SettingsOwnershipTests(unittest.TestCase):
    def test_background_ownership_detected_from_providers_state(self) -> None:
        self.assertTrue(_settings_owned_by_background({"providers": {"state": "starting"}}))
        self.assertFalse(_settings_owned_by_background({"providers": {"state": "planned"}}))
        self.assertFalse(_settings_owned_by_background({"providers": "skipped"}))
        self.assertFalse(_settings_owned_by_background({}))
        self.assertFalse(_settings_owned_by_background(None))


class TeardownGuardTests(unittest.TestCase):
    def test_cleanup_blocks_while_setup_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = self._integrated_contract(Path(tmp))
            args = WorktreeArgs(contract_path=contract.contract_path, approved=True)
            with mock.patch.object(provider_async, "provider_setup_running", return_value=True):
                result = worktree_cleanup.cleanup_result(args)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.payload["state"], "blocked")
            self.assertIn("Provider setup is still running", cast(str, result.payload["summary"]))

    def test_abandon_blocks_without_force_while_setup_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = self._integrated_contract(Path(tmp))
            args = WorktreeArgs(contract_path=contract.contract_path, approved=True)
            with mock.patch.object(provider_async, "provider_setup_running", return_value=True):
                result = worktree_abandon.abandon_result(args)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.payload["state"], "blocked")
            self.assertIn("force=true", cast(str, result.payload["summary"]))

    def _integrated_contract(self, root: Path):
        contract = make_contract(root)
        contract = replace(contract, integration_status="completed")
        contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
        write_contract(contract.contract_path, contract)
        return contract


if __name__ == "__main__":
    unittest.main()
