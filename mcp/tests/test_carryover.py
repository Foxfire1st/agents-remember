"""Tests for branch-memory carryover planning and apply (memory/carryover.py)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from agents_remember.kernel.memory_ledger import (
    create_initial_ledger,
    write_ledger,
)
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.memory import carryover as carryover_module
from agents_remember.memory.carryover import (
    FILE_SIDECAR_KIND,
    ROUTE_OVERVIEW_KIND,
    CarryoverApplyOptions,
    CarryoverRequest,
    build_plan_for_request,
)
from agents_remember.memory.carryover import (
    _apply_carryover_for_request as _apply_carryover_owner,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    write_contract,
)


def _apply_carryover_for_request(
    request: CarryoverRequest,
    **kwargs: Any,
) -> dict[str, object]:
    """Drive the private mutator with the same loaded authority as the MCP application."""

    return _apply_carryover_owner(
        request,
        authority=load_config(request.config_path),
        options=CarryoverApplyOptions(**kwargs),
    )


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_carryover.py:24).
def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:  # pragma: no cover
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, **(env or {})},
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def init_repo(repo: Path, branch: str = "main") -> str:
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-b", branch)
    git(repo, "config", "user.email", "agents-remember-tests@example.invalid")
    git(repo, "config", "user.name", "Agents Remember Tests")
    (repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "Initial commit")
    return git(repo, "rev-parse", "HEAD")


def commit_file(repo: Path, path: str, content: str, message: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    git(repo, "add", path)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def write_memory_settings(
    memory_root: Path,
    *,
    includes: list[str] | None = None,
    excludes: list[str] | None = None,
) -> Path:
    path = memory_root / "system" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "onboarding": {
                    "storage": {"mode": "memory-repo"},
                    "pathRules": {
                        "include": {
                            "paths": includes or ["README.md", "src/**"],
                            "fileTypes": [".md", ".py"],
                        },
                        "exclude": {"paths": excludes or ["coverage/**"]},
                    },
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def repository_snapshot(repo: Path) -> tuple[str, str, dict[str, bytes]]:
    return git(repo, "rev-parse", "HEAD"), git(repo, "status", "--porcelain"), tree_snapshot(repo)


def write_file_onboarding(
    onboarding_root: Path, repo_name: str, source_path: str, commit_hash: str
) -> Path:
    path = onboarding_root / f"{source_path}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"# {source_path}",
                "",
                "| Field | Value |",
                "| --- | --- |",
                f"| repository | {repo_name} |",
                f"| path | `{source_path}` |",
                "| doc_type | `file-level-onboarding` |",
                f"| lastVerifiedCommitHash | `{commit_hash}` |",
                "| lastVerifiedCommitDate | 2026-05-09T00:00:00+00:00 |",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def write_route_overview(
    onboarding_root: Path,
    repo_name: str,
    source_route: str,
    commit_hash: str,
    body: str = "Route purpose.",
) -> Path:
    path = (
        onboarding_root / source_route / "overview.md"
        if source_route != "."
        else onboarding_root / "overview.md"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    doc_type = "repo-overview" if source_route == "." else "route-local-overview"
    path.write_text(
        "\n".join(
            [
                f"# {source_route} Overview",
                "",
                "| Field | Value |",
                "| --- | --- |",
                f"| repository | {repo_name} |",
                f"| doc_type | `{doc_type}` |",
                f"| sourceRoute | `{source_route}` |",
                f"| lastVerifiedCommitHash | `{commit_hash}` |",
                "| lastVerifiedCommitDate | 2026-05-09T00:00:00+00:00 |",
                "",
                "## Purpose",
                "",
                body,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_carryover.py:164).
def read_onboarding_field(path: Path, field: str) -> str:  # pragma: no cover
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"| {field} |"):
            return line.split("|", 3)[2].strip().strip("`")
    raise AssertionError(f"{field} was not found in {path}")


class CarryoverFixture:
    """Code repo with a landed task branch plus official and source memory roots."""

    def __init__(self, workspace: Path) -> None:
        self.coordination_root = workspace / "coordination"
        (self.coordination_root / "tasks" / "repo-a").mkdir(parents=True)
        self.code_repo = workspace / "repo-a"
        self.old_base = init_repo(self.code_repo, "main")
        git(self.code_repo, "checkout", "-b", "task/one")
        self.source_head = commit_file(
            self.code_repo, "src/app/feature.py", "VALUE = 'landed'\n", "Add feature"
        )
        git(self.code_repo, "checkout", "main")
        git(self.code_repo, "merge", "--ff-only", "task/one")
        self.official_head = git(self.code_repo, "rev-parse", "main")

        self.target_memory = workspace / "memory-official"
        memory_seed = init_repo(self.target_memory, "main")
        write_ledger(
            self.target_memory / "memory.md",
            create_initial_ledger("repo-a", self.old_base, memory_seed),
        )
        write_memory_settings(self.target_memory)
        git(self.target_memory, "add", "memory.md", "system/settings.json")
        git(self.target_memory, "commit", "-m", "Add memory ledger")
        git(self.target_memory, "checkout", "-b", "carryover-work")
        configured_memory = self.coordination_root / "memory-repos" / "ar-repo-a"
        configured_memory.parent.mkdir(parents=True)
        configured_memory.symlink_to(self.target_memory, target_is_directory=True)
        self.config_path = workspace / "settings.json"
        self.config_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "coordinationRoot": self.coordination_root.as_posix(),
                    "workspaceRoot": workspace.as_posix(),
                    "repositories": {"repo-a": {}},
                }
            ),
            encoding="utf-8",
        )
        for repository in (self.code_repo, self.target_memory):
            main = git(repository, "rev-parse", "main")
            git(repository, "update-ref", "refs/remotes/origin/main", main)
            git(
                repository,
                "symbolic-ref",
                "refs/remotes/origin/HEAD",
                "refs/remotes/origin/main",
            )
        git(self.code_repo, "checkout", "task/one")
        self.target_contract = replace(
            default_contract(
                ContractTask(
                    name="carryover-recovery",
                    repo_name="repo-a",
                    coordination_root=self.coordination_root,
                    workflow_kind="light-task",
                    memory_mode="external",
                ),
                leaf=LeafIdentity(
                    worktree_name="carryover-recovery",
                    leaf_id="CARRYOVER-RECOVERY",
                ),
                code=RepoBranchPlan(
                    repo_path=self.code_repo,
                    source_branch="main",
                    work_branch="task/one",
                    base_commit=self.official_head,
                ),
                memory=RepoBranchPlan(
                    repo_path=self.target_memory,
                    source_branch="main",
                    work_branch="carryover-work",
                    base_commit=git(self.target_memory, "rev-parse", "HEAD"),
                ),
            ),
            code_worktree=self.code_repo,
            memory_worktree=self.target_memory,
            ledger_path=self.target_memory / "memory.md",
        )
        write_contract(self.target_contract.contract_path, self.target_contract)

        self.source_memory = workspace / "memory-branch"
        self.source_memory.mkdir(parents=True)
        write_file_onboarding(
            self.source_memory / "onboarding", "repo-a", "src/app/feature.py", self.source_head
        )

    def commit_target(self, message: str = "Seed target onboarding") -> None:
        git(self.target_memory, "add", "-A")
        git(self.target_memory, "commit", "-m", message)

    def request(self) -> CarryoverRequest:
        return CarryoverRequest(
            config_path=self.config_path,
            target_contract_path=self.target_contract.contract_path,
            code_repository_root=self.code_repo,
            official_code_ref="main",
            source_code_ref="task/one",
            old_base=self.old_base,
            target_memory=self.target_memory,
            source_memory=self.source_memory,
            code_repository_name="repo-a",
        )


def carryover_snapshot(
    fixture: CarryoverFixture,
) -> tuple[tuple[str, str, dict[str, bytes]], dict[str, bytes]]:
    return repository_snapshot(fixture.target_memory), tree_snapshot(fixture.source_memory)


def overview_candidates_of(plan: dict[str, object]) -> list[dict[str, object]]:
    candidates = plan["candidates"]
    assert isinstance(candidates, list)
    return [candidate for candidate in candidates if candidate["kind"] == ROUTE_OVERVIEW_KIND]


class CarryoverOverviewPlanTests(unittest.TestCase):
    def test_direct_cli_exposes_plan_only_and_cannot_apply(self) -> None:
        with self.assertRaises(SystemExit):
            carryover_module.main(["apply"])

    def test_private_writer_requires_the_exact_loaded_runtime_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            other_config = Path(tmp) / "other-settings.json"
            other_config.write_text(
                fixture.config_path.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            before = carryover_snapshot(fixture)

            with self.assertRaisesRegex(RuntimeError, "runtime config authority"):
                _apply_carryover_owner(
                    fixture.request(),
                    authority=load_config(other_config),
                    options=CarryoverApplyOptions(intent_note="attempt mismatched authority"),
                )

            self.assertEqual(carryover_snapshot(fixture), before)

    def test_plan_includes_differing_overview_as_review_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            write_route_overview(
                fixture.source_memory / "onboarding",
                "repo-a",
                "src/app",
                fixture.source_head,
                body="Branch-learned route behavior.",
            )
            plan = build_plan_for_request(fixture.request())
            overviews = overview_candidates_of(plan)
            self.assertEqual(len(overviews), 1)
            self.assertEqual(overviews[0]["source_path"], "src/app")
            self.assertEqual(overviews[0]["decision"], "review-required")
            self.assertEqual(overviews[0]["evidence"], "route-covers-landed-paths")
            self.assertFalse(overviews[0]["target_exists"])

    def test_plan_skips_overview_without_landed_path_under_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            write_route_overview(
                fixture.source_memory / "onboarding", "repo-a", "docs", fixture.source_head
            )
            plan = build_plan_for_request(fixture.request())
            self.assertEqual(overview_candidates_of(plan), [])

    def test_plan_auto_carries_identical_overview_for_reverification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            write_route_overview(
                fixture.source_memory / "onboarding", "repo-a", ".", fixture.old_base
            )
            write_route_overview(
                fixture.target_memory / "onboarding", "repo-a", ".", fixture.old_base
            )
            fixture.commit_target()
            plan = build_plan_for_request(fixture.request())
            overviews = overview_candidates_of(plan)
            self.assertEqual(len(overviews), 1)
            self.assertEqual(overviews[0]["source_path"], ".")
            self.assertEqual(overviews[0]["decision"], "auto-carry")

    def test_sidecar_candidates_keep_default_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            plan = build_plan_for_request(fixture.request())
            candidates = plan["candidates"]
            assert isinstance(candidates, list)
            sidecars = [c for c in candidates if c["kind"] == FILE_SIDECAR_KIND]
            self.assertEqual(len(sidecars), 1)
            self.assertEqual(sidecars[0]["source_path"], "src/app/feature.py")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
