"""Behavioural cover for worktree and observer helpers that only had happy-path use.

Every test here asserts the value returned, the file that moved, or the error
raised -- never merely that a line ran. The docker-facing helpers are exercised
through their own module-level ``run_command``/``docker_command`` seam so no
container runtime is ever contacted; the git- and filesystem-facing helpers run
against real throwaway repositories and directory trees, as the rest of the
worktree suite does.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.observer import snapshots
from agents_remember.observer.snapshots import (
    _inspect_containers,
    _inspect_containers_individually,
)
from agents_remember.providers.context import ContextProviderError
from agents_remember.worktrees.modules import provider_teardown
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.cleanup import delete_branch_if_merged
from agents_remember.worktrees.modules.onboarding import (
    route_overview_metadata_refresh_plan_for_context,
)
from agents_remember.worktrees.modules.provider_teardown import _docker_network_rm, _docker_rm_f
from agents_remember.worktrees.modules.start_contract import _parent_series_contract
from agents_remember.worktrees.task_resolver import (
    archive_completed_root_task,
    series_contract_path,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    contract_to_text,
    default_contract,
    default_series_contract,
    write_contract,
)
from test_worktree_support import git, init_repo

FAKE_DOCKER = "/usr/bin/docker-under-test"


def _result(
    *, returncode: int = 0, stdout: str = "", stderr: str = "", timed_out: bool = False
) -> dict[str, Any]:
    """One ``run_command`` return value, shaped exactly as the real runner shapes it."""
    return {
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "timedOut": timed_out,
    }


class _StubRunner:
    """Replay canned ``run_command`` results (or raise them) and record every call."""

    def __init__(self, *results: object) -> None:
        self._results = list(results)
        self.commands: list[list[str]] = []
        self.kwargs: list[dict[str, Any]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> dict[str, Any]:
        self.commands.append(list(command))
        self.kwargs.append(dict(kwargs))
        # The last entry repeats, so a multi-call path can be given one outcome.
        result = self._results.pop(0) if len(self._results) > 1 else self._results[0]
        if isinstance(result, BaseException):
            raise result
        assert isinstance(result, dict)
        return result


def _inspect_json(*names: str) -> str:
    """A ``docker inspect`` payload, with docker's leading-slash container names."""
    return json.dumps([{"Name": f"/{name}", "State": {"Running": True}} for name in names])


class InspectContainersTests(unittest.TestCase):
    """``_inspect_containers``: batch inspect, with per-name fallback and hard-failure Nones."""

    CWD = Path("/tmp/inspect-cwd-under-test")

    def _patched(self, runner: _StubRunner, docker: object = FAKE_DOCKER):
        docker_stub = (
            mock.Mock(side_effect=docker) if isinstance(docker, BaseException) else lambda: docker
        )
        return (
            mock.patch.object(snapshots, "run_command", runner),
            mock.patch.object(snapshots, "docker_command", docker_stub),
        )

    def _run(
        self, names: set[str], runner: _StubRunner, docker: object = FAKE_DOCKER
    ) -> dict[str, dict[str, Any] | None] | None:
        run_patch, docker_patch = self._patched(runner, docker)
        with run_patch, docker_patch:
            return _inspect_containers(names, cwd=self.CWD)

    def test_empty_name_set_returns_empty_map_without_touching_docker(self) -> None:
        runner = _StubRunner(_result())

        self.assertEqual(self._run(set(), runner), {})
        self.assertEqual(runner.commands, [])

    def test_missing_docker_binary_reports_unknown_rather_than_absent(self) -> None:
        runner = _StubRunner(_result())

        result = self._run({"ar-backend"}, runner, ContextProviderError("docker command not found"))

        # None means "could not tell", which is not the same as "not running".
        self.assertIsNone(result)
        self.assertEqual(runner.commands, [])

    def test_runner_oserror_reports_unknown(self) -> None:
        runner = _StubRunner(OSError("exec format error"))

        self.assertIsNone(self._run({"ar-backend"}, runner))

    def test_inspect_timeout_reports_unknown(self) -> None:
        runner = _StubRunner(_result(timed_out=True))

        self.assertIsNone(self._run({"ar-backend"}, runner))

    def test_batch_success_maps_every_requested_name(self) -> None:
        runner = _StubRunner(_result(stdout=_inspect_json("ar-backend")))

        result = self._run({"ar-backend", "ar-watcher"}, runner)

        assert result is not None
        self.assertEqual(sorted(result), ["ar-backend", "ar-watcher"])
        self.assertEqual(result["ar-backend"], {"Name": "/ar-backend", "State": {"Running": True}})
        # Requested but absent from docker's reply: known-missing, recorded as None.
        self.assertIsNone(result["ar-watcher"])
        self.assertEqual(runner.commands, [[FAKE_DOCKER, "inspect", "ar-backend", "ar-watcher"]])
        self.assertEqual(runner.kwargs[0]["cwd"], self.CWD)
        self.assertEqual(runner.kwargs[0]["timeout"], snapshots.WORKTREE_PROVIDER_INSPECT_SECONDS)
        self.assertIs(runner.kwargs[0]["allow_timeout"], True)

    def test_batch_failure_falls_back_to_inspecting_each_name(self) -> None:
        # docker inspect exits non-zero for the whole batch when any one name is unknown.
        runner = _StubRunner(
            _result(returncode=1, stderr="Error: No such object: ar-watcher"),
            _result(stdout=_inspect_json("ar-backend")),
            _result(returncode=1, stderr="Error: No such object: ar-watcher"),
        )

        result = self._run({"ar-backend", "ar-watcher"}, runner)

        self.assertEqual(
            result,
            {"ar-backend": {"Name": "/ar-backend", "State": {"Running": True}}, "ar-watcher": None},
        )
        self.assertEqual(
            runner.commands,
            [
                [FAKE_DOCKER, "inspect", "ar-backend", "ar-watcher"],
                [FAKE_DOCKER, "inspect", "ar-backend"],
                [FAKE_DOCKER, "inspect", "ar-watcher"],
            ],
        )

    def test_unparseable_batch_stdout_maps_every_name_to_none(self) -> None:
        runner = _StubRunner(_result(stdout="<html>proxy error</html>"))

        self.assertEqual(self._run({"ar-backend"}, runner), {"ar-backend": None})


class InspectContainersIndividuallyTests(unittest.TestCase):
    """``_inspect_containers_individually``: per-name results, aborting on runtime failure."""

    CWD = Path("/tmp/inspect-cwd-under-test")

    def _run(self, names: set[str], runner: _StubRunner) -> dict[str, dict[str, Any] | None] | None:
        with mock.patch.object(snapshots, "run_command", runner):
            return _inspect_containers_individually(names, cwd=self.CWD, docker=FAKE_DOCKER)

    def test_no_names_yields_an_empty_map_without_calling_docker(self) -> None:
        runner = _StubRunner(_result())

        self.assertEqual(self._run(set(), runner), {})
        self.assertEqual(runner.commands, [])

    def test_absent_container_is_none_and_does_not_stop_the_sweep(self) -> None:
        runner = _StubRunner(
            _result(returncode=1, stderr="Error: No such object: ar-backend"),
            _result(stdout=_inspect_json("ar-watcher")),
        )

        result = self._run({"ar-backend", "ar-watcher"}, runner)

        self.assertEqual(
            result,
            {"ar-backend": None, "ar-watcher": {"Name": "/ar-watcher", "State": {"Running": True}}},
        )
        # Sorted, so a name that fails first still lets the rest be inspected.
        self.assertEqual(
            runner.commands,
            [[FAKE_DOCKER, "inspect", "ar-backend"], [FAKE_DOCKER, "inspect", "ar-watcher"]],
        )

    def test_reply_naming_a_different_container_yields_none_for_the_request(self) -> None:
        runner = _StubRunner(_result(stdout=_inspect_json("some-other-container")))

        self.assertEqual(self._run({"ar-backend"}, runner), {"ar-backend": None})

    def test_oserror_abandons_the_whole_sweep_as_unknown(self) -> None:
        runner = _StubRunner(OSError("docker socket vanished"))

        self.assertIsNone(self._run({"ar-backend", "ar-watcher"}, runner))
        self.assertEqual(len(runner.commands), 1)

    def test_timeout_abandons_the_whole_sweep_as_unknown(self) -> None:
        runner = _StubRunner(_result(stdout=_inspect_json("ar-backend")), _result(timed_out=True))

        self.assertIsNone(self._run({"ar-backend", "ar-watcher"}, runner))
        self.assertEqual(len(runner.commands), 2)


class DockerRemoveHelpersTests(unittest.TestCase):
    """``_docker_rm_f`` / ``_docker_network_rm``: dry run, success, already-gone, failure."""

    CWD = Path("/tmp/teardown-cwd-under-test")

    def _patched(self, runner: _StubRunner):
        return (
            mock.patch.object(provider_teardown, "run_command", runner),
            mock.patch.object(provider_teardown, "docker_command", lambda: FAKE_DOCKER),
        )

    def _rm(self, runner: _StubRunner, *, dry_run: bool = False) -> dict[str, Any]:
        run_patch, docker_patch = self._patched(runner)
        with run_patch, docker_patch:
            return _docker_rm_f("ar-backend", cwd=self.CWD, dry_run=dry_run)

    def _network_rm(self, runner: _StubRunner, *, dry_run: bool = False) -> dict[str, Any]:
        run_patch, docker_patch = self._patched(runner)
        with run_patch, docker_patch:
            return _docker_network_rm("ar-net", cwd=self.CWD, dry_run=dry_run)

    def test_container_dry_run_promises_removal_without_running_docker(self) -> None:
        runner = _StubRunner(_result())

        result = self._rm(runner, dry_run=True)

        self.assertEqual(
            result, {"container": "ar-backend", "removed": False, "would_remove": True}
        )
        self.assertEqual(runner.commands, [])

    def test_container_removal_reports_removed_and_forces(self) -> None:
        runner = _StubRunner(_result(stdout="ar-backend\n"))

        self.assertEqual(self._rm(runner), {"container": "ar-backend", "removed": True})
        self.assertEqual(runner.commands, [[FAKE_DOCKER, "rm", "-f", "ar-backend"]])
        self.assertEqual(runner.kwargs[0]["cwd"], self.CWD)

    def test_container_already_gone_is_not_an_error(self) -> None:
        runner = _StubRunner(
            _result(
                returncode=1, stderr="Error response from daemon: No such container: ar-backend"
            )
        )

        self.assertEqual(
            self._rm(runner),
            {"container": "ar-backend", "removed": False, "reason": "already-absent"},
        )

    def test_container_removal_failure_surfaces_the_docker_stderr(self) -> None:
        runner = _StubRunner(
            _result(returncode=1, stderr="  Cannot connect to the Docker daemon  \n")
        )

        self.assertEqual(
            self._rm(runner),
            {
                "container": "ar-backend",
                "removed": False,
                "reason": "Cannot connect to the Docker daemon",
            },
        )

    def test_container_removal_failure_without_stderr_falls_back_to_a_reason(self) -> None:
        runner = _StubRunner(_result(returncode=137, stderr="   "))

        self.assertEqual(
            self._rm(runner),
            {"container": "ar-backend", "removed": False, "reason": "docker rm failed"},
        )

    def test_network_dry_run_promises_removal_without_running_docker(self) -> None:
        runner = _StubRunner(_result())

        result = self._network_rm(runner, dry_run=True)

        self.assertEqual(result, {"network": "ar-net", "removed": False, "would_remove": True})
        self.assertEqual(runner.commands, [])

    def test_network_removal_reports_removed(self) -> None:
        runner = _StubRunner(_result(stdout="ar-net\n"))

        self.assertEqual(self._network_rm(runner), {"network": "ar-net", "removed": True})
        self.assertEqual(runner.commands, [[FAKE_DOCKER, "network", "rm", "ar-net"]])

    def test_network_already_gone_is_not_an_error(self) -> None:
        runner = _StubRunner(_result(returncode=1, stderr="Error: network ar-net not found"))

        self.assertEqual(
            self._network_rm(runner),
            {"network": "ar-net", "removed": False, "reason": "already-absent"},
        )

    def test_network_removal_failure_surfaces_the_docker_stderr(self) -> None:
        runner = _StubRunner(
            _result(returncode=1, stderr="Error: network ar-net has active endpoints\n")
        )

        self.assertEqual(
            self._network_rm(runner),
            {
                "network": "ar-net",
                "removed": False,
                "reason": "Error: network ar-net has active endpoints",
            },
        )

    def test_network_removal_failure_without_stderr_falls_back_to_a_reason(self) -> None:
        runner = _StubRunner(_result(returncode=1, stderr=""))

        self.assertEqual(
            self._network_rm(runner),
            {"network": "ar-net", "removed": False, "reason": "docker network rm failed"},
        )


class DeleteBranchIfMergedTests(unittest.TestCase):
    """``delete_branch_if_merged``: refuses to lose unmerged work, and says why."""

    @staticmethod
    def _branches(repo: Path) -> list[str]:
        listed = git(repo, "branch", "--format=%(refname:short)")
        return [line.strip() for line in listed.splitlines() if line.strip()]

    def test_absent_branch_is_reported_as_already_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_repo(repo)

            result = delete_branch_if_merged(repo, "ar/never-existed", False)

            self.assertEqual(
                result, {"branch": "ar/never-existed", "deleted": False, "reason": "already-absent"}
            )

    def test_dry_run_promises_deletion_and_keeps_the_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_repo(repo)
            git(repo, "branch", "ar/merged")

            result = delete_branch_if_merged(repo, "ar/merged", True)

            self.assertEqual(
                result, {"branch": "ar/merged", "deleted": False, "would_delete": True}
            )
            self.assertIn("ar/merged", self._branches(repo))

    def test_merged_branch_is_actually_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_repo(repo)
            git(repo, "branch", "ar/merged")

            result = delete_branch_if_merged(repo, "ar/merged", False)

            self.assertEqual(result, {"branch": "ar/merged", "deleted": True})
            self.assertNotIn("ar/merged", self._branches(repo))

    def test_unmerged_branch_is_refused_and_survives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_repo(repo)
            git(repo, "checkout", "-q", "-b", "ar/unmerged")
            (repo / "work.txt").write_text("unmerged work\n", encoding="utf-8")
            git(repo, "add", "work.txt")
            git(repo, "commit", "-m", "Unmerged work")
            git(repo, "checkout", "-q", "main")

            result = delete_branch_if_merged(repo, "ar/unmerged", False)

            self.assertEqual(result["branch"], "ar/unmerged")
            self.assertIs(result["deleted"], False)
            self.assertIn("not fully merged", str(result["reason"]))
            # The refusal must leave the commit reachable, not just report a failure.
            self.assertIn("ar/unmerged", self._branches(repo))


def _write_overview(path: Path, rows: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Overview", "", "| Field | Value |", "| --- | --- |"]
    lines.extend(f"| {field} | {value} |" for field, value in rows.items())
    path.write_text("\n".join([*lines, ""]), encoding="utf-8")
    return path


class RouteOverviewMetadataRefreshPlanTests(unittest.TestCase):
    """``route_overview_metadata_refresh_plan_for_context``: which overviews a change implicates."""

    ROUTE_ROWS: ClassVar[dict[str, str]] = {
        "repository": "demo-repo",
        "doc_type": "`route-local-overview`",
        "sourceRoute": "`src/app`",
        "lastVerifiedCommitHash": "`" + "0" * 40 + "`",
        "lastVerifiedCommitDate": "2026-05-09T00:00:00+00:00",
    }

    @staticmethod
    def _plan(onboarding_root: Path, changed: list[str]):
        return route_overview_metadata_refresh_plan_for_context(
            SimpleNamespace(onboarding_root=onboarding_root), changed
        )

    def test_empty_onboarding_tree_plans_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            onboarding_root = Path(tmp) / "onboarding"
            onboarding_root.mkdir()

            self.assertEqual(
                self._plan(onboarding_root, ["src/app/feature.py"]),
                {"required": [], "missing_metadata": []},
            )

    def test_a_directory_named_overview_md_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            onboarding_root = Path(tmp) / "onboarding"
            (onboarding_root / "src" / "app" / "overview.md").mkdir(parents=True)

            self.assertEqual(
                self._plan(onboarding_root, ["src/app/feature.py"]),
                {"required": [], "missing_metadata": []},
            )

    def test_non_route_doc_types_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            onboarding_root = Path(tmp) / "onboarding"
            _write_overview(
                onboarding_root / "src" / "app" / "overview.md",
                {**self.ROUTE_ROWS, "doc_type": "`file-level-onboarding`"},
            )

            self.assertEqual(
                self._plan(onboarding_root, ["src/app/feature.py"]),
                {"required": [], "missing_metadata": []},
            )

    def test_overview_whose_route_excludes_the_change_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            onboarding_root = Path(tmp) / "onboarding"
            _write_overview(onboarding_root / "src" / "app" / "overview.md", self.ROUTE_ROWS)

            self.assertEqual(
                self._plan(onboarding_root, ["docs/readme.md"]),
                {"required": [], "missing_metadata": []},
            )

    def test_matched_overview_with_full_metadata_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            onboarding_root = Path(tmp) / "onboarding"
            overview = _write_overview(
                onboarding_root / "src" / "app" / "overview.md", self.ROUTE_ROWS
            )

            plan = self._plan(onboarding_root, ["src/app/feature.py"])

            self.assertEqual(plan["missing_metadata"], [])
            self.assertEqual(
                plan["required"],
                [{"source_route": "src/app", "onboarding_file": overview.as_posix()}],
            )

    def test_repo_overview_matches_every_change_and_reports_its_route_as_dot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            onboarding_root = Path(tmp) / "onboarding"
            overview = _write_overview(
                onboarding_root / "overview.md",
                {**self.ROUTE_ROWS, "doc_type": "`repo-overview`", "sourceRoute": "`<repo-root>`"},
            )

            plan = self._plan(onboarding_root, ["anywhere/at/all.py"])

            self.assertEqual(
                plan["required"],
                [{"source_route": ".", "onboarding_file": overview.as_posix()}],
            )

    def test_missing_verification_metadata_is_reported_relative_and_never_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            onboarding_root = Path(tmp) / "onboarding"
            no_hash = dict(self.ROUTE_ROWS)
            no_hash.pop("lastVerifiedCommitHash")
            _write_overview(onboarding_root / "src" / "app" / "overview.md", no_hash)
            no_date = {**self.ROUTE_ROWS, "sourceRoute": "`src/lib`"}
            no_date.pop("lastVerifiedCommitDate")
            _write_overview(onboarding_root / "src" / "lib" / "overview.md", no_date)

            plan = self._plan(onboarding_root, ["src/app/feature.py", "src/lib/util.py"])

            self.assertEqual(plan["required"], [])
            self.assertEqual(
                plan["missing_metadata"], ["src/app/overview.md", "src/lib/overview.md"]
            )


class ArchiveCompletedRootTaskTests(unittest.TestCase):
    """``archive_completed_root_task``: only finished root tasks move, and only once."""

    REPO = "demo-repo"

    def _roots(self, tmp: Path) -> tuple[Path, Path]:
        repo_task_root = tmp / "tasks" / self.REPO
        task_root = repo_task_root / "demo-task"
        task_root.mkdir(parents=True)
        (task_root / "task.md").write_text("# Demo task\n", encoding="utf-8")
        return repo_task_root, task_root

    def test_a_nested_task_is_never_archived(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo_task_root, task_root = self._roots(root)
            nested = task_root / "sub-task"
            nested.mkdir()

            result = archive_completed_root_task(root, self.REPO, nested, dry_run=False)

            self.assertEqual(
                result,
                {"state": "skipped", "reason": "not-root-task", "taskRoot": nested.as_posix()},
            )
            self.assertTrue(nested.is_dir())

    def test_a_task_whose_series_contract_still_exists_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repo_task_root, task_root = self._roots(root)
            series_contract_path(task_root).write_text("---\n", encoding="utf-8")

            result = archive_completed_root_task(root, self.REPO, task_root, dry_run=False)

            self.assertEqual(
                result,
                {
                    "state": "skipped",
                    "reason": "root-series-still-active",
                    "taskRoot": task_root.as_posix(),
                },
            )
            self.assertTrue(task_root.is_dir())

    def test_an_occupied_archive_slot_blocks_instead_of_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo_task_root, task_root = self._roots(root)
            occupied = repo_task_root / "0_archive" / "demo-task"
            occupied.mkdir(parents=True)
            (occupied / "task.md").write_text("# Earlier run\n", encoding="utf-8")

            result = archive_completed_root_task(root, self.REPO, task_root, dry_run=False)

            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["reason"], "archive-target-exists")
            self.assertEqual(result["archivePath"], occupied.as_posix())
            # Neither side is touched: the earlier archive keeps its own content.
            self.assertEqual((occupied / "task.md").read_text(encoding="utf-8"), "# Earlier run\n")
            self.assertTrue(task_root.is_dir())

    def test_dry_run_names_the_archive_target_without_moving_anything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo_task_root, task_root = self._roots(root)

            result = archive_completed_root_task(root, self.REPO, task_root, dry_run=True)

            self.assertEqual(
                result,
                {
                    "state": "would-archive",
                    "taskRoot": task_root.as_posix(),
                    "archivePath": (repo_task_root / "0_archive" / "demo-task").as_posix(),
                },
            )
            self.assertTrue(task_root.is_dir())
            self.assertFalse((repo_task_root / "0_archive").exists())

    def test_archiving_moves_the_task_tree_into_the_archive_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo_task_root, task_root = self._roots(root)
            target = repo_task_root / "0_archive" / "demo-task"

            result = archive_completed_root_task(root, self.REPO, task_root, dry_run=False)

            self.assertEqual(
                result,
                {
                    "state": "archived",
                    "taskRoot": task_root.as_posix(),
                    "archivePath": target.as_posix(),
                },
            )
            self.assertFalse(task_root.exists())
            self.assertEqual((target / "task.md").read_text(encoding="utf-8"), "# Demo task\n")


class ParentSeriesContractTests(unittest.TestCase):
    """``_parent_series_contract``: adopt an existing series, or mint one for a master task."""

    REPO = "demo-repo"
    TASK = "demo-master"

    def _context(self, root: Path, code_repo: Path) -> SimpleNamespace:
        return SimpleNamespace(
            coordination_root=root,
            code_repository_name=self.REPO,
            code_repository_root=code_repo,
        )

    def _args(self, **overrides: Any) -> WorktreeArgs:
        base: dict[str, Any] = {
            "task_name": self.TASK,
            "worktree_name": "demo-leaf",
            "workflow_kind": "light-task",
            "dry_run": False,
        }
        return WorktreeArgs(**{**base, **overrides})

    def _task_root(self, root: Path) -> Path:
        return root / "tasks" / self.REPO / self.TASK

    def _write_task_artifact(self, root: Path, body: str) -> Path:
        task_root = self._task_root(root)
        task_root.mkdir(parents=True, exist_ok=True)
        (task_root / "task.md").write_text(body, encoding="utf-8")
        return task_root

    @staticmethod
    def _branches(repo: Path) -> list[str]:
        listed = git(repo, "branch", "--format=%(refname:short)")
        return [line.strip() for line in listed.splitlines() if line.strip()]

    def test_no_task_name_means_no_parent_series(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = self._context(root, root / "repo")

            self.assertIsNone(
                _parent_series_contract(context, self._args(task_name=None), "internal")
            )

    def test_a_task_that_is_not_a_master_gets_no_series_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_repo = root / "repo"
            init_repo(code_repo)
            task_root = self._write_task_artifact(root, "# Demo\n\n**Type:** Light\n")

            result = _parent_series_contract(
                self._context(root, code_repo), self._args(), "internal"
            )

            self.assertIsNone(result)
            self.assertFalse(series_contract_path(task_root).exists())

    def test_master_task_mints_the_series_contract_and_integration_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_repo = root / "repo"
            head = init_repo(code_repo)
            task_root = self._write_task_artifact(root, "# Demo\n\n**Type:** Master\n")

            contract = _parent_series_contract(
                self._context(root, code_repo), self._args(), "internal"
            )

            assert contract is not None
            self.assertEqual(contract.kind, "series")
            self.assertEqual(contract.task_name, self.TASK)
            self.assertEqual(contract.code_source_branch, "main")
            self.assertEqual(contract.code_work_branch, f"ar/{self.TASK}")
            self.assertEqual(contract.code_base_commit, head)
            self.assertEqual(contract.memory_mode, "internal")
            # Internal memory: nothing memory-repo shaped is recorded.
            self.assertIsNone(contract.memory_repo_path)
            self.assertEqual(contract.memory_work_branch, "")
            self.assertIn(f"ar/{self.TASK}", self._branches(code_repo))
            self.assertTrue(series_contract_path(task_root).is_file())

    def test_dry_run_mints_no_branch_and_writes_no_contract_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_repo = root / "repo"
            init_repo(code_repo)
            task_root = self._write_task_artifact(root, "# Demo\n\n**Type:** Master\n")

            contract = _parent_series_contract(
                self._context(root, code_repo), self._args(dry_run=True), "internal"
            )

            assert contract is not None
            self.assertEqual(contract.code_work_branch, f"ar/{self.TASK}")
            self.assertNotIn(f"ar/{self.TASK}", self._branches(code_repo))
            self.assertFalse(series_contract_path(task_root).exists())

    def test_leaf_branch_colliding_with_the_integration_branch_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_repo = root / "repo"
            init_repo(code_repo)
            task_root = self._write_task_artifact(root, "# Demo\n\n**Type:** Master\n")

            with self.assertRaises(RuntimeError) as caught:
                _parent_series_contract(
                    self._context(root, code_repo),
                    self._args(work_branch=f"ar/{self.TASK}"),
                    "internal",
                )

            self.assertIn("would equal the integration branch", str(caught.exception))
            # Refused before anything was created, so a retry starts from a clean tree.
            self.assertNotIn(f"ar/{self.TASK}", self._branches(code_repo))
            self.assertFalse(series_contract_path(task_root).exists())

    def test_external_memory_also_mints_the_memory_integration_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_repo = root / "repo"
            init_repo(code_repo)
            memory_repo = root / "memory-repos" / f"ar-{self.REPO}"
            memory_head = init_repo(memory_repo)
            self._write_task_artifact(root, "# Demo\n\n**Type:** Master\n")

            contract = _parent_series_contract(
                self._context(root, code_repo), self._args(), "external"
            )

            assert contract is not None
            self.assertEqual(contract.memory_repo_path, memory_repo)
            self.assertEqual(contract.memory_source_branch, "main")
            self.assertEqual(contract.memory_work_branch, f"ar/{self.TASK}")
            self.assertEqual(contract.memory_base_commit, memory_head)
            self.assertIn(f"ar/{self.TASK}", self._branches(memory_repo))

    def test_an_existing_series_contract_is_adopted_rather_than_rebuilt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_repo = root / "repo"
            head = init_repo(code_repo)
            task_root = self._task_root(root)
            existing = default_series_contract(
                ContractTask(
                    name=self.TASK,
                    repo_name=self.REPO,
                    coordination_root=root,
                    workflow_kind="light-task",
                    memory_mode="internal",
                ),
                code=RepoBranchPlan(
                    repo_path=code_repo,
                    source_branch="main",
                    work_branch="ar/already-there",
                    base_commit=head,
                ),
                task_root=task_root,
            )
            write_contract(series_contract_path(task_root), existing)

            contract = _parent_series_contract(
                self._context(root, code_repo), self._args(), "internal"
            )

            assert contract is not None
            self.assertEqual(contract.kind, "series")
            self.assertEqual(contract.code_work_branch, "ar/already-there")
            # Adoption must not touch the repo: no `ar/<task>` branch is invented.
            self.assertNotIn(f"ar/{self.TASK}", self._branches(code_repo))

    def test_an_unreadable_parent_contract_is_reported_as_such(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_repo = root / "repo"
            init_repo(code_repo)
            task_root = self._task_root(root)
            task_root.mkdir(parents=True)
            series_contract_path(task_root).write_text("no front matter here\n", encoding="utf-8")

            with self.assertRaises(RuntimeError) as caught:
                _parent_series_contract(self._context(root, code_repo), self._args(), "internal")

            self.assertIn("parent task contract is not readable", str(caught.exception))

    def test_a_leaf_contract_in_the_parent_slot_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_repo = root / "repo"
            head = init_repo(code_repo)
            task_root = self._task_root(root)
            task_root.mkdir(parents=True)
            leaf = default_contract(
                ContractTask(
                    name=self.TASK,
                    repo_name=self.REPO,
                    coordination_root=root,
                    workflow_kind="light-task",
                    memory_mode="internal",
                ),
                leaf=LeafIdentity(worktree_name="demo-leaf"),
                code=RepoBranchPlan(
                    repo_path=code_repo,
                    source_branch="main",
                    work_branch="ar/demo-leaf",
                    base_commit=head,
                ),
            )
            series_contract_path(task_root).write_text(contract_to_text(leaf), encoding="utf-8")

            with self.assertRaises(RuntimeError) as caught:
                _parent_series_contract(self._context(root, code_repo), self._args(), "internal")

            self.assertIn("not a series contract", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
