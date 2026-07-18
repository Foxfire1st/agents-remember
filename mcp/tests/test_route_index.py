from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import AuthorityError, RouteIndexCensusError
from agents_remember.kernel.coordination_context.models import StorageSettings
from agents_remember.kernel.git_command import (
    GIT_REPOSITORY_SELECTOR_ENV,
    git_environment,
)
from agents_remember.kernel.route_index import build_route_indexes, sidecar_status
from agents_remember.kernel.route_index_census import (
    route_index_source_files,
    route_index_source_snapshot,
)


def run_git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
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


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    run_git(repo, "init", "-b", "main")
    run_git(repo, "config", "user.email", "route-index@example.invalid")
    run_git(repo, "config", "user.name", "Route Index Tests")


def commit_all(repo: Path, message: str = "fixture") -> None:
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-m", message)


def storage_settings(
    *,
    includes: list[str] | None = None,
    excludes: list[str] | None = None,
    include_file_types: list[str] | None = None,
) -> StorageSettings:
    return StorageSettings(
        mode="memory-repo",
        default="memory-repo",
        path_rules=[
            {
                "path": "",
                "includes": includes or ["*"],
                "excludes": excludes or [],
                "include_file_types": include_file_types or [],
            }
        ],
    )


def route_index_bytes(onboarding_root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(onboarding_root).as_posix(): path.read_bytes()
        for path in sorted(onboarding_root.rglob("overview.index.json"))
    }


class RouteIndexTests(unittest.TestCase):
    def test_builds_route_indexes_from_overviews_and_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_root = root / "repo"
            onboarding_root = root / "memory" / "onboarding"

            init_repo(code_root)
            (code_root / "src" / "app").mkdir(parents=True)
            (code_root / "src" / "app" / "service.py").write_text(
                "def run(): pass\n", encoding="utf-8"
            )
            (code_root / "src" / "app" / "missing.py").write_text(
                "def gap(): pass\n", encoding="utf-8"
            )
            (code_root / "README.md").write_text("# Repo\n", encoding="utf-8")
            commit_all(code_root)

            (onboarding_root / "src" / "app").mkdir(parents=True)
            (onboarding_root / "overview.md").write_text("# Repo Overview\n", encoding="utf-8")
            (onboarding_root / "src" / "app" / "overview.md").write_text(
                (
                    "# App Route\n"
                    "Handles service routing.\n\n"
                    "## Hot Path Summary\n"
                    "Routes service requests through `ServiceRunner`, `APP_MODE`, and service.py.\n"
                    "Use `run_service` when confirming source behavior.\n\n"
                    "## What This Area Is\n"
                    "Detailed route prose.\n"
                ),
                encoding="utf-8",
            )
            (onboarding_root / "src" / "app" / "service.py.md").write_text(
                "# service.py\n",
                encoding="utf-8",
            )

            result = build_route_indexes(
                code_root=code_root,
                onboarding_root=onboarding_root,
                repository="fixture",
                storage=storage_settings(),
            )

            self.assertEqual(result.routes, 2)
            self.assertEqual(result.written, 2)

            root_index = json.loads(
                (onboarding_root / "overview.index.json").read_text(encoding="utf-8")
            )
            self.assertEqual(root_index["route"], "")
            self.assertEqual(root_index["childRoutes"][0]["route"], "src/app")

            route_index = json.loads(
                (onboarding_root / "src" / "app" / "overview.index.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(route_index["sourceScope"], ["src/app/**"])
            self.assertEqual(route_index["coveredFiles"], ["src/app/service.py"])
            self.assertEqual(route_index["coverageCounts"]["fileSidecars"], 1)
            self.assertEqual(route_index["coverageCounts"]["sourceFilesInScope"], 2)
            self.assertEqual(route_index["fallback"]["governingOverview"], "src/app/overview.md")
            self.assertIn("app", route_index["routingTerms"])
            self.assertNotIn("handles", route_index["routingTerms"])
            self.assertEqual(
                route_index["hotPath"]["summary"],
                "Routes service requests through `ServiceRunner`, `APP_MODE`, and service.py. "
                "Use `run_service` when confirming source behavior.",
            )
            self.assertIn("src/app", route_index["hotPath"]["candidateHints"])
            self.assertIn("src/app/service.py", route_index["hotPath"]["candidateHints"])
            self.assertIn("ServiceRunner", route_index["hotPath"]["anchorHints"])
            self.assertIn("APP_MODE", route_index["hotPath"]["anchorHints"])
            self.assertIn("service.py", route_index["hotPath"]["anchorHints"])
            self.assertIn("run_service", route_index["hotPath"]["anchorHints"])

    def test_sidecar_status_uses_scope_and_covered_files(self) -> None:
        route_index = {
            "sourceScope": ["src/app/**"],
            "coveredFiles": ["src/app/service.py"],
        }

        self.assertEqual(sidecar_status("src/app/service.py", route_index), "present")
        self.assertEqual(sidecar_status("src/app/missing.py", route_index), "absent")
        self.assertEqual(sidecar_status("src/other/file.py", route_index), "out-of-scope")

    def test_overview_only_route_still_indexes_source_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_root = root / "repo"
            onboarding_root = root / "memory" / "onboarding"

            init_repo(code_root)
            (code_root / "pkg").mkdir(parents=True)
            (code_root / "pkg" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
            commit_all(code_root)
            (onboarding_root / "pkg").mkdir(parents=True)
            (onboarding_root / "overview.md").write_text("# Repo Overview\n", encoding="utf-8")
            (onboarding_root / "pkg" / "overview.md").write_text("# Package\n", encoding="utf-8")

            build_route_indexes(
                code_root=code_root,
                onboarding_root=onboarding_root,
                repository="fixture",
                storage=storage_settings(),
            )

            route_index = json.loads(
                (onboarding_root / "pkg" / "overview.index.json").read_text(encoding="utf-8")
            )
            self.assertEqual(route_index["coveredFiles"], [])
            self.assertEqual(route_index["coverageCounts"]["fileSidecars"], 0)
            self.assertEqual(route_index["hotPath"]["summary"], "")
            self.assertEqual(route_index["hotPath"]["candidateHints"], ["pkg"])
            self.assertEqual(sidecar_status("pkg/module.py", route_index), "absent")

    def test_ignored_generated_and_path_rule_excluded_artifacts_do_not_change_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_root = root / "repo"
            onboarding_root = root / "memory" / "onboarding"
            init_repo(code_root)

            (code_root / ".gitignore").write_text(
                ".cache/\nnode_modules/\nsrc/app/ignored.py\n",
                encoding="utf-8",
            )
            (code_root / "README.md").write_text("# Repo\n", encoding="utf-8")
            (code_root / "src" / "app").mkdir(parents=True)
            (code_root / "src" / "app" / "service.py").write_text(
                "SERVICE = 1\n", encoding="utf-8"
            )
            (code_root / "src" / "app" / "missing.py").write_text(
                "MISSING = 1\n", encoding="utf-8"
            )
            (code_root / "src" / "generated").mkdir(parents=True)
            (code_root / "src" / "generated" / "tracked.py").write_text(
                "GENERATED = 1\n", encoding="utf-8"
            )
            commit_all(code_root)

            (onboarding_root / "src" / "app").mkdir(parents=True)
            (onboarding_root / "overview.md").write_text("# Repo\n", encoding="utf-8")
            (onboarding_root / "src" / "app" / "overview.md").write_text(
                "# App\n", encoding="utf-8"
            )
            (onboarding_root / "src" / "generated").mkdir(parents=True)
            (onboarding_root / "src" / "generated" / "tracked.py.md").write_text(
                "# tracked.py\n", encoding="utf-8"
            )
            settings = storage_settings(
                includes=["README.md", "src/**"],
                excludes=["src/generated/**"],
                include_file_types=[".md", ".py"],
            )
            build_route_indexes(
                code_root=code_root,
                onboarding_root=onboarding_root,
                repository="fixture",
                storage=settings,
            )
            clean_bytes = route_index_bytes(onboarding_root)
            root_index = json.loads(clean_bytes["overview.index.json"])
            app_index = json.loads(clean_bytes["src/app/overview.index.json"])
            self.assertEqual(root_index["coverageCounts"]["sourceFilesInScope"], 3)
            self.assertEqual(app_index["coverageCounts"]["sourceFilesInScope"], 2)
            self.assertIn("src/generated/tracked.py", root_index["coveredFiles"])

            contamination = {
                ".cache/ignored.py": "CACHE = 1\n",
                "node_modules/ignored.js": "export default 1\n",
                "src/app/ignored.py": "IGNORED = 1\n",
                "coverage/report.py": "REPORT = 1\n",
                "src/generated/runtime.py": "GENERATED = 2\n",
                "build/cache.py": "BUILD = 1\n",
            }
            for relative, content in contamination.items():
                target = code_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

            candidates = set(
                run_git(
                    code_root,
                    "ls-files",
                    "--others",
                    "--exclude-standard",
                ).splitlines()
            )
            self.assertNotIn(".cache/ignored.py", candidates)
            self.assertNotIn("node_modules/ignored.js", candidates)
            self.assertNotIn("src/app/ignored.py", candidates)
            self.assertIn("coverage/report.py", candidates)
            self.assertIn("src/generated/runtime.py", candidates)
            self.assertIn("build/cache.py", candidates)

            contaminated = build_route_indexes(
                code_root=code_root,
                onboarding_root=onboarding_root,
                repository="fixture",
                storage=settings,
            )
            self.assertEqual(contaminated.written, 0)
            self.assertEqual(contaminated.unchanged, 2)
            self.assertEqual(route_index_bytes(onboarding_root), clean_bytes)

            (code_root / "src" / "app" / "candidate.py").write_text(
                "CANDIDATE = 1\n", encoding="utf-8"
            )
            candidate_result = build_route_indexes(
                code_root=code_root,
                onboarding_root=onboarding_root,
                repository="fixture",
                storage=settings,
            )
            self.assertEqual(candidate_result.written, 2)
            root_index = json.loads(
                (onboarding_root / "overview.index.json").read_text(encoding="utf-8")
            )
            app_index = json.loads(
                (onboarding_root / "src" / "app" / "overview.index.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(root_index["coverageCounts"]["sourceFilesInScope"], 4)
            self.assertEqual(app_index["coverageCounts"]["sourceFilesInScope"], 3)

            candidate_excluded = storage_settings(
                includes=["README.md", "src/**"],
                excludes=["src/generated/**", "src/app/candidate.py"],
                include_file_types=[".md", ".py"],
            )
            excluded_result = build_route_indexes(
                code_root=code_root,
                onboarding_root=onboarding_root,
                repository="fixture",
                storage=candidate_excluded,
            )
            self.assertEqual(excluded_result.written, 2)
            self.assertEqual(route_index_bytes(onboarding_root), clean_bytes)

    def test_exact_paths_and_symlinks_are_target_independent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_root = root / "repository"
            onboarding_root = root / "onboarding"
            init_repo(code_root)
            (code_root / "src").mkdir()
            (code_root / "src" / "name.py").write_text("VALUE = 1\n", encoding="utf-8")
            (code_root / "src" / "name.py ").write_text("VALUE = 2\n", encoding="utf-8")
            tracked_target = root / "tracked-target.py"
            (code_root / "src" / "tracked-link.py").symlink_to(tracked_target)
            commit_all(code_root)
            onboarding_root.mkdir()
            (onboarding_root / "overview.md").write_text("# Repo\n", encoding="utf-8")
            (onboarding_root / "src").mkdir()
            (onboarding_root / "src" / "tracked-link.py.md").write_text(
                "# tracked-link.py\n", encoding="utf-8"
            )
            settings = storage_settings(
                includes=["src/**"],
                include_file_types=[".py"],
            )

            snapshot = route_index_source_snapshot(
                code_root=code_root,
                storage=settings,
                scoped_repo_path="",
            )
            self.assertEqual(
                list(snapshot.eligible_paths),
                ["src/name.py", "src/name.py ", "src/tracked-link.py"],
            )
            self.assertEqual(
                [(candidate.path, candidate.mode) for candidate in snapshot.candidates],
                [
                    ("src/name.py", "100644"),
                    ("src/name.py ", "100644"),
                    ("src/tracked-link.py", "120000"),
                ],
            )
            first = build_route_indexes(
                code_root=code_root,
                onboarding_root=onboarding_root,
                repository="fixture",
                storage=settings,
            )
            initial_bytes = route_index_bytes(onboarding_root)
            self.assertEqual(first.written, 1)
            self.assertEqual(
                json.loads(initial_bytes["overview.index.json"])["coverageCounts"][
                    "sourceFilesInScope"
                ],
                3,
            )
            initial_index = json.loads(initial_bytes["overview.index.json"])
            self.assertEqual(initial_index["coveredFiles"], ["src/tracked-link.py"])
            self.assertEqual(initial_index["coverageCounts"]["fileSidecars"], 1)

            tracked_target.write_text("TARGET = 1\n", encoding="utf-8")
            target_created = build_route_indexes(
                code_root=code_root,
                onboarding_root=onboarding_root,
                repository="fixture",
                storage=settings,
            )
            self.assertEqual(target_created.written, 0)
            self.assertEqual(route_index_bytes(onboarding_root), initial_bytes)
            tracked_target.unlink()

            candidate_target = root / "candidate-target.py"
            (code_root / "src" / "candidate-link.py").symlink_to(candidate_target)
            (onboarding_root / "src" / "candidate-link.py.md").write_text(
                "# candidate-link.py\n", encoding="utf-8"
            )
            self.assertEqual(
                route_index_source_files(
                    code_root=code_root,
                    storage=settings,
                    scoped_repo_path="",
                ),
                [
                    "src/candidate-link.py",
                    "src/name.py",
                    "src/name.py ",
                    "src/tracked-link.py",
                ],
            )
            candidate_added = build_route_indexes(
                code_root=code_root,
                onboarding_root=onboarding_root,
                repository="fixture",
                storage=settings,
            )
            candidate_bytes = route_index_bytes(onboarding_root)
            self.assertEqual(candidate_added.written, 1)
            self.assertEqual(
                json.loads(candidate_bytes["overview.index.json"])["coverageCounts"][
                    "sourceFilesInScope"
                ],
                4,
            )
            candidate_index = json.loads(candidate_bytes["overview.index.json"])
            self.assertEqual(
                candidate_index["coveredFiles"],
                ["src/candidate-link.py", "src/tracked-link.py"],
            )
            self.assertEqual(candidate_index["coverageCounts"]["fileSidecars"], 2)

            candidate_target.write_text("TARGET = 2\n", encoding="utf-8")
            candidate_target_created = build_route_indexes(
                code_root=code_root,
                onboarding_root=onboarding_root,
                repository="fixture",
                storage=settings,
            )
            self.assertEqual(candidate_target_created.written, 0)
            self.assertEqual(route_index_bytes(onboarding_root), candidate_bytes)

    def test_sparse_worktree_retains_skip_worktree_entries_and_subtracts_real_deletions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = root / "repository"
            sparse_worktree = root / "sparse-worktree"
            init_repo(repository)
            (repository / "kept").mkdir()
            (repository / "kept" / "visible.py").write_text("VISIBLE = 1\n", encoding="utf-8")
            (repository / "hidden").mkdir()
            (repository / "hidden" / "sparse.py").write_text("SPARSE = 1\n", encoding="utf-8")
            commit_all(repository)
            run_git(repository, "worktree", "add", "--detach", str(sparse_worktree), "HEAD")
            run_git(sparse_worktree, "sparse-checkout", "set", "--no-cone", "/kept/")

            self.assertFalse((sparse_worktree / "hidden" / "sparse.py").exists())
            self.assertTrue(
                run_git(sparse_worktree, "ls-files", "-v", "hidden/sparse.py").startswith("S ")
            )
            settings = storage_settings(
                includes=["kept/**", "hidden/**"],
                include_file_types=[".py"],
            )
            self.assertEqual(
                route_index_source_files(
                    code_root=sparse_worktree,
                    storage=settings,
                    scoped_repo_path="",
                ),
                ["hidden/sparse.py", "kept/visible.py"],
            )

            template = root / "template"
            template.mkdir()
            (template / "overview.md").write_text("# Repo\n", encoding="utf-8")
            (template / "hidden").mkdir()
            (template / "hidden" / "sparse.py.md").write_text(
                "# sparse.py\n", encoding="utf-8"
            )
            (template / "kept").mkdir()
            (template / "kept" / "visible.py.md").write_text(
                "# visible.py\n", encoding="utf-8"
            )
            regular_onboarding = root / "regular-onboarding"
            sparse_onboarding = root / "sparse-onboarding"
            shutil.copytree(template, regular_onboarding)
            shutil.copytree(template, sparse_onboarding)
            regular_first = build_route_indexes(
                code_root=repository,
                onboarding_root=regular_onboarding,
                repository="fixture",
                storage=settings,
            )
            sparse_first = build_route_indexes(
                code_root=sparse_worktree,
                onboarding_root=sparse_onboarding,
                repository="fixture",
                storage=settings,
            )
            sparse_repeat = build_route_indexes(
                code_root=sparse_worktree,
                onboarding_root=sparse_onboarding,
                repository="fixture",
                storage=settings,
            )
            self.assertEqual(regular_first.written, 1)
            self.assertEqual(sparse_first.written, 1)
            self.assertEqual(sparse_repeat.written, 0)
            self.assertEqual(
                route_index_bytes(regular_onboarding),
                route_index_bytes(sparse_onboarding),
            )
            sparse_index = json.loads(
                route_index_bytes(sparse_onboarding)["overview.index.json"]
            )
            self.assertEqual(
                sparse_index["coveredFiles"],
                ["hidden/sparse.py", "kept/visible.py"],
            )
            self.assertEqual(sparse_index["coverageCounts"]["fileSidecars"], 2)

            (sparse_worktree / "kept" / "visible.py").unlink()
            self.assertEqual(
                route_index_source_files(
                    code_root=sparse_worktree,
                    storage=settings,
                    scoped_repo_path="",
                ),
                ["hidden/sparse.py"],
            )
            deletion = build_route_indexes(
                code_root=sparse_worktree,
                onboarding_root=sparse_onboarding,
                repository="fixture",
                storage=settings,
            )
            self.assertEqual(deletion.written, 1)
            deletion_index = json.loads(
                route_index_bytes(sparse_onboarding)["overview.index.json"]
            )
            self.assertEqual(deletion_index["coverageCounts"]["sourceFilesInScope"], 1)
            self.assertEqual(deletion_index["coveredFiles"], ["hidden/sparse.py"])
            self.assertEqual(deletion_index["coverageCounts"]["fileSidecars"], 1)

    def test_gitlink_and_its_worktree_contents_are_not_source_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_root = root / "repository"
            onboarding_root = root / "onboarding"
            init_repo(code_root)
            (code_root / "README.md").write_text("# Repo\n", encoding="utf-8")
            commit_all(code_root)
            commit = run_git(code_root, "rev-parse", "HEAD")
            run_git(
                code_root,
                "update-index",
                "--add",
                "--cacheinfo",
                f"160000,{commit},vendor/submodule",
            )
            run_git(code_root, "commit", "-m", "Add gitlink")
            (code_root / "vendor" / "submodule").mkdir(parents=True)
            (code_root / "vendor" / "submodule" / "generated.py").write_text(
                "GENERATED = 1\n", encoding="utf-8"
            )
            onboarding_root.mkdir()
            (onboarding_root / "overview.md").write_text("# Repo\n", encoding="utf-8")
            settings = storage_settings(
                includes=["README.md", "vendor/**"],
                include_file_types=[".md", ".py"],
            )

            self.assertEqual(
                route_index_source_files(
                    code_root=code_root,
                    storage=settings,
                    scoped_repo_path="",
                ),
                ["README.md"],
            )
            result = build_route_indexes(
                code_root=code_root,
                onboarding_root=onboarding_root,
                repository="fixture",
                storage=settings,
            )
            index = json.loads(
                (onboarding_root / "overview.index.json").read_text(encoding="utf-8")
            )
            self.assertEqual(result.written, 1)
            self.assertEqual(index["coverageCounts"]["sourceFilesInScope"], 1)

    def test_ambient_git_repository_selectors_cannot_redirect_the_census(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_root = root / "repository"
            decoy = root / "decoy"
            init_repo(code_root)
            (code_root / ".gitignore").write_text("src/ignored.py\n", encoding="utf-8")
            (code_root / "README.md").write_text("# Repo\n", encoding="utf-8")
            (code_root / "src").mkdir()
            (code_root / "src" / "service.py").write_text("SERVICE = 1\n", encoding="utf-8")
            commit_all(code_root)
            (code_root / "src" / "ignored.py").write_text("IGNORED = 1\n", encoding="utf-8")
            alternate_index = root / "alternate-index"
            run_git(code_root, "read-tree", "HEAD", env={"GIT_INDEX_FILE": str(alternate_index)})
            run_git(
                code_root,
                "add",
                "-f",
                "src/ignored.py",
                env={"GIT_INDEX_FILE": str(alternate_index)},
            )
            init_repo(decoy)
            (decoy / "decoy.py").write_text("DECOY = 1\n", encoding="utf-8")
            commit_all(decoy)
            settings = storage_settings(
                includes=["README.md", "src/**"],
                include_file_types=[".md", ".py"],
            )
            selectors = {
                "GIT_DIR": str(decoy / ".git"),
                "GIT_WORK_TREE": str(decoy),
                "GIT_INDEX_FILE": str(alternate_index),
                "GIT_OBJECT_DIRECTORY": str(decoy / ".git" / "objects"),
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(code_root / ".git" / "objects"),
                "GIT_COMMON_DIR": str(decoy / ".git"),
                "GIT_NAMESPACE": "redirected",
                "GIT_PREFIX": "redirected/",
            }

            with patch.dict(os.environ, selectors):
                self.assertTrue(set(GIT_REPOSITORY_SELECTOR_ENV).isdisjoint(git_environment()))
                self.assertEqual(
                    route_index_source_files(
                        code_root=code_root,
                        storage=settings,
                        scoped_repo_path="",
                    ),
                    ["README.md", "src/service.py"],
                )

    def test_git_census_failure_uses_typed_domain_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code_root = Path(tmp).resolve()
            results = [
                subprocess.CompletedProcess(
                    args=["git"],
                    returncode=0,
                    stdout=f"{code_root}\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=["git"],
                    returncode=1,
                    stdout="",
                    stderr="selector conflict\n",
                ),
            ]
            with patch(
                "agents_remember.kernel.route_index_census.run_git",
                side_effect=results,
            ), self.assertRaisesRegex(
                RouteIndexCensusError,
                "git diff-files deletion census failed: selector conflict",
            ):
                route_index_source_files(
                    code_root=code_root,
                    storage=storage_settings(),
                    scoped_repo_path="",
                )

    def test_git_timeout_and_os_errors_use_typed_domain_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code_root = Path(tmp).resolve()
            root_success = subprocess.CompletedProcess(
                args=["git"],
                returncode=0,
                stdout=f"{code_root}\n",
                stderr="",
            )

            with patch(
                "agents_remember.kernel.route_index_census.run_git",
                side_effect=OSError("git executable unavailable"),
            ), self.assertRaisesRegex(
                AuthorityError,
                "repository authority probe failed: git executable unavailable",
            ) as root_error:
                route_index_source_files(
                    code_root=code_root,
                    storage=storage_settings(),
                    scoped_repo_path="",
                )
            self.assertIsInstance(root_error.exception.__cause__, OSError)

            with patch(
                "agents_remember.kernel.route_index_census.run_git",
                side_effect=[
                    root_success,
                    subprocess.TimeoutExpired(cmd=["git", "diff-files"], timeout=5),
                ],
            ), self.assertRaisesRegex(
                RouteIndexCensusError,
                "git diff-files deletion census failed:.*timed out",
            ) as timeout_error:
                route_index_source_files(
                    code_root=code_root,
                    storage=storage_settings(),
                    scoped_repo_path="",
                )
            self.assertIsInstance(timeout_error.exception.__cause__, subprocess.TimeoutExpired)

            with patch(
                "agents_remember.kernel.route_index_census.run_git",
                side_effect=[root_success, OSError("index unreadable")],
            ), self.assertRaisesRegex(
                RouteIndexCensusError,
                "git diff-files deletion census failed: index unreadable",
            ) as census_error:
                route_index_source_files(
                    code_root=code_root,
                    storage=storage_settings(),
                    scoped_repo_path="",
                )
            self.assertIsInstance(census_error.exception.__cause__, OSError)

    def test_untracked_lstat_error_uses_typed_census_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code_root = Path(tmp) / "repository"
            init_repo(code_root)
            (code_root / "tracked.py").write_text("TRACKED = 1\n", encoding="utf-8")
            commit_all(code_root)
            candidate = code_root / "candidate.py"
            candidate.write_text("CANDIDATE = 1\n", encoding="utf-8")
            original_lstat = Path.lstat

            def fail_candidate_lstat(path: Path):
                if path == candidate:
                    raise PermissionError("candidate metadata denied")
                return original_lstat(path)

            with patch.object(Path, "lstat", fail_candidate_lstat), self.assertRaisesRegex(
                RouteIndexCensusError,
                "candidate.py.*candidate metadata denied",
            ) as captured:
                route_index_source_files(
                    code_root=code_root,
                    storage=storage_settings(),
                    scoped_repo_path="",
                )
            self.assertIsInstance(captured.exception.__cause__, PermissionError)

    def test_non_utf8_git_path_preserves_record_identity(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX byte-path semantics are required")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_root = root / "repository"
            onboarding_root = root / "onboarding"
            init_repo(code_root)
            (code_root / "src").mkdir()
            raw_relative = b"src/non-utf8-\xff.py"
            raw_path = os.fsencode(code_root) + b"/" + raw_relative
            descriptor = os.open(raw_path, os.O_WRONLY | os.O_CREAT, 0o644)
            try:
                os.write(descriptor, b"VALUE = 1\n")
            finally:
                os.close(descriptor)
            commit_all(code_root)
            expected = os.fsdecode(raw_relative)
            (onboarding_root / "src").mkdir(parents=True)
            (onboarding_root / "overview.md").write_text("# Repo\n", encoding="utf-8")
            (onboarding_root / f"{expected}.md").write_text(
                "# non-utf8.py\n", encoding="utf-8"
            )
            settings = storage_settings(
                includes=["src/**"],
                include_file_types=[".py"],
            )

            snapshot = route_index_source_snapshot(
                code_root=code_root,
                storage=settings,
                scoped_repo_path="",
            )
            self.assertEqual(snapshot.repository_paths, (expected,))
            self.assertEqual(snapshot.eligible_paths, (expected,))
            self.assertEqual(snapshot.candidates[0].mode, "100644")
            first = build_route_indexes(
                code_root=code_root,
                onboarding_root=onboarding_root,
                repository="fixture",
                storage=settings,
            )
            repeat = build_route_indexes(
                code_root=code_root,
                onboarding_root=onboarding_root,
                repository="fixture",
                storage=settings,
            )
            index = json.loads(
                (onboarding_root / "overview.index.json").read_text(encoding="utf-8")
            )
            self.assertEqual(first.written, 1)
            self.assertEqual(repeat.written, 0)
            self.assertEqual(index["coveredFiles"], [expected])
            self.assertEqual(index["coverageCounts"]["sourceFilesInScope"], 1)

    def test_regular_checkout_and_linked_worktree_produce_identical_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = root / "repository"
            linked_worktree = root / "linked-worktree"
            init_repo(repository)
            (repository / "README.md").write_text("# Repo\n", encoding="utf-8")
            (repository / "pkg").mkdir()
            (repository / "pkg" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
            commit_all(repository)
            run_git(repository, "worktree", "add", "--detach", str(linked_worktree), "HEAD")

            onboarding_template = root / "template"
            (onboarding_template / "pkg").mkdir(parents=True)
            (onboarding_template / "overview.md").write_text("# Repo\n", encoding="utf-8")
            (onboarding_template / "pkg" / "overview.md").write_text(
                "# Package\n", encoding="utf-8"
            )
            regular_onboarding = root / "regular-onboarding"
            worktree_onboarding = root / "worktree-onboarding"
            shutil.copytree(onboarding_template, regular_onboarding)
            shutil.copytree(onboarding_template, worktree_onboarding)

            settings = storage_settings()
            regular_first = build_route_indexes(
                code_root=repository,
                onboarding_root=regular_onboarding,
                repository="fixture",
                storage=settings,
            )
            worktree_first = build_route_indexes(
                code_root=linked_worktree,
                onboarding_root=worktree_onboarding,
                repository="fixture",
                storage=settings,
            )
            regular_repeat = build_route_indexes(
                code_root=repository,
                onboarding_root=regular_onboarding,
                repository="fixture",
                storage=settings,
            )
            worktree_repeat = build_route_indexes(
                code_root=linked_worktree,
                onboarding_root=worktree_onboarding,
                repository="fixture",
                storage=settings,
            )

            self.assertTrue((repository / ".git").is_dir())
            self.assertTrue((linked_worktree / ".git").is_file())
            self.assertEqual(regular_first.written, 2)
            self.assertEqual(worktree_first.written, 2)
            self.assertEqual(regular_repeat.written, 0)
            self.assertEqual(worktree_repeat.written, 0)
            self.assertEqual(
                route_index_bytes(regular_onboarding),
                route_index_bytes(worktree_onboarding),
            )

            with self.assertRaisesRegex(AuthorityError, "must be the Git repository root"):
                build_route_indexes(
                    code_root=repository / "pkg",
                    onboarding_root=regular_onboarding,
                    repository="fixture",
                    storage=settings,
                    dry_run=True,
                )

    def test_non_git_source_root_fails_instead_of_walking_the_filesystem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_root = root / "not-a-repository"
            onboarding_root = root / "memory" / "onboarding"
            code_root.mkdir()
            onboarding_root.mkdir(parents=True)
            (code_root / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
            (onboarding_root / "overview.md").write_text("# Repo\n", encoding="utf-8")

            with self.assertRaisesRegex(AuthorityError, "requires a Git repository"):
                build_route_indexes(
                    code_root=code_root,
                    onboarding_root=onboarding_root,
                    repository="fixture",
                    storage=storage_settings(),
                )


if __name__ == "__main__":
    unittest.main()
