from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agents_remember.kernel.memory_ledger import (
    create_initial_ledger,
    prepend_mapping,
    write_ledger,
)
from agents_remember.kernel.primitives.drift_snapshot import drift_snapshot_path
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    ProviderScope,
    RepositoryScope,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check import summary
from agents_remember.memory_quality.integrity.onboarding_drift_check.models import DriftRow
from agents_remember.observer.projection import LEDGER_WINDOW, LedgerRefNode
from agents_remember.observer.store import EventStore
from agents_remember.serving.projections.paths import (
    DRIFT_SNAPSHOT_SCHEMA,
    drift_snapshot_dir,
    observer_root,
)
from agents_remember.serving.projections.projection_store import (
    _gather_repo_surfaces_cached,
    _repo_surface_cache,
    project_and_write,
)
from agents_remember.serving.projections.snapshots import (
    _git_commit_meta,
    _ledger_window,
    read_drift_snapshots,
    read_ledger,
)
from agents_remember.serving.projections.snapshots_impl import _analytics as snapshots_analytics
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    write_contract,
)
from test_observer_projection import FRESH, T0, _started


class LedgerReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.mem = Path(self._dir.name)

    def test_reads_count_and_currency(self) -> None:
        ledger = prepend_mapping(create_initial_ledger("repo-a", "aaaa", "bbbb"), "cccc", "dddd")
        write_ledger(self.mem / "memory.md", ledger)
        node = read_ledger(self.mem)
        self.assertIsNotNone(node)
        assert node is not None
        self.assertEqual(
            (node.repository, node.closeoutCount, node.lastVerifiedCodeCommit),
            ("repo-a", 2, "cccc"),
        )

    def test_missing_ledger_is_none(self) -> None:
        self.assertIsNone(read_ledger(self.mem / "nope"))

    def test_ledger_window_returns_newest_rows_and_total(self) -> None:
        # 5h coupler popover: the newest LEDGER_WINDOW rows (newest-first) + the full total for "+N more".
        ledger = create_initial_ledger("repo-a", "base-code", "base-mem")
        for i in range(LEDGER_WINDOW + 3):  # more rows than the window
            ledger = prepend_mapping(ledger, f"code{i:02d}", f"mem{i:02d}")
        write_ledger(self.mem / "memory.md", ledger)
        rows, total = _ledger_window((self.mem / "memory.md").as_posix())
        self.assertEqual(len(rows), LEDGER_WINDOW)
        self.assertEqual(total, len(ledger.rows))  # total is the full count, not the window
        self.assertIsInstance(rows[0], LedgerRefNode)
        self.assertEqual(rows[0].codeCommit, f"code{LEDGER_WINDOW + 2:02d}")  # newest-first

    def test_ledger_window_missing_or_none_is_empty(self) -> None:
        self.assertEqual(_ledger_window((self.mem / "nope.md").as_posix()), ([], 0))
        self.assertEqual(_ledger_window(None), ([], 0))

    def test_reads_windowed_rows_with_full_count(self) -> None:
        # 5h official coupler: read_ledger surfaces the newest LEDGER_WINDOW rows; closeoutCount stays total.
        ledger = create_initial_ledger("repo-a", "base-code", "base-mem")
        for i in range(LEDGER_WINDOW + 5):
            ledger = prepend_mapping(ledger, f"code{i:02d}", f"mem{i:02d}")
        write_ledger(self.mem / "memory.md", ledger)
        node = read_ledger(self.mem)
        assert node is not None
        self.assertEqual(len(node.rows), LEDGER_WINDOW)
        self.assertEqual(node.closeoutCount, len(ledger.rows))  # the full total, not the window
        self.assertEqual(node.rows[0].codeCommit, f"code{LEDGER_WINDOW + 4:02d}")  # newest-first


class LedgerCommitMetaTests(unittest.TestCase):
    """5h Tier 2: best-effort commit message + date enrichment of the popover ledger window."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.tmp = Path(self._dir.name)

    def _git(self, repo: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def _repo_with_commits(self, subjects: list[str]) -> tuple[Path, list[str]]:
        repo = (self.tmp / "repo").resolve()
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
        shas: list[str] = []
        for subject in subjects:
            self._git(repo, "commit", "--allow-empty", "-m", subject)
            shas.append(self._git(repo, "rev-parse", "HEAD"))
        return repo, shas

    def test_git_commit_meta_batches_and_maps(self) -> None:
        repo, shas = self._repo_with_commits(["first subject", "second subject"])
        meta = _git_commit_meta(repo.as_posix(), shas)
        self.assertEqual(set(meta), set(shas))
        date, subject = meta[shas[1]]
        self.assertEqual(subject, "second subject")
        self.assertRegex(date, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")  # committer ISO date

    def test_git_commit_meta_drops_unknown_and_tolerates_bad_input(self) -> None:
        repo, shas = self._repo_with_commits(["only one"])
        # a bogus sha is dropped (no HEAD fallback); the real one still resolves
        meta = _git_commit_meta(
            repo.as_posix(), [shas[0], "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"]
        )
        self.assertEqual(set(meta), {shas[0]})
        # best-effort: a non-repo path, empty root, or empty commit list -> {}
        self.assertEqual(_git_commit_meta((self.tmp / "nope").as_posix(), shas), {})
        self.assertEqual(_git_commit_meta("", shas), {})
        self.assertEqual(_git_commit_meta(repo.as_posix(), []), {})

    def test_ledger_window_enriches_rows_when_commits_are_local(self) -> None:
        repo, shas = self._repo_with_commits(["code change", "memory change"])
        mem = (self.tmp / "mem").resolve()
        mem.mkdir()
        ledger = prepend_mapping(create_initial_ledger("repo-a", "base", "base"), shas[0], shas[1])
        write_ledger(mem / "memory.md", ledger)
        rows, total = _ledger_window(
            (mem / "memory.md").as_posix(), code_root=repo.as_posix(), memory_root=repo.as_posix()
        )
        self.assertEqual(total, len(ledger.rows))
        self.assertEqual(rows[0].codeSubject, "code change")
        self.assertEqual(rows[0].memorySubject, "memory change")
        self.assertIsNotNone(rows[0].codeDate)
        self.assertIsNotNone(rows[0].memoryDate)

    def test_ledger_window_leaves_meta_none_when_not_local(self) -> None:
        # honest fallback: no probe roots -> rows still served with hashes, no message/date (never faked)
        mem = (self.tmp / "mem2").resolve()
        mem.mkdir()
        ledger = prepend_mapping(create_initial_ledger("repo-a", "base", "base"), "cccc", "dddd")
        write_ledger(mem / "memory.md", ledger)
        rows, _ = _ledger_window((mem / "memory.md").as_posix())
        self.assertEqual(rows[0].codeCommit, "cccc")
        self.assertIsNone(rows[0].codeSubject)
        self.assertIsNone(rows[0].codeDate)
        self.assertIsNone(rows[0].memorySubject)
        self.assertIsNone(rows[0].memoryDate)

    def test_a_wedged_git_log_degrades_to_hash_only_rows_instead_of_failing_the_tick(
        self,
    ) -> None:
        # TimeoutExpired is a SubprocessError, and SubprocessError is not an OSError. The
        # probe moved from a runner with no timeout onto one with the local bound, so a
        # wedged `git log` now raises something `except OSError` cannot catch -- it would
        # escape _git_commit_meta -> _enrich_ledger_rows and take the whole projection tick
        # down, which is exactly what both entry points below promise never happens.
        repo, shas = self._repo_with_commits(["code change", "memory change"])
        ledger = prepend_mapping(create_initial_ledger("repo-a", "base", "base"), shas[0], shas[1])
        write_ledger(repo / "memory.md", ledger)
        wedged = subprocess.TimeoutExpired(cmd=["git", "log"], timeout=300)

        with mock.patch.object(snapshots_analytics, "run_git", side_effect=wedged):
            rows, total = _ledger_window(
                (repo / "memory.md").as_posix(),
                code_root=repo.as_posix(),
                memory_root=repo.as_posix(),
            )
            node = read_ledger(repo, code_root=repo)

        self.assertEqual(total, len(ledger.rows))
        self.assertEqual(rows[0].codeCommit, shas[0])  # the hash survives
        self.assertIsNone(rows[0].codeSubject)  # the enrichment does not, and is not faked
        self.assertIsNone(rows[0].memoryDate)
        assert node is not None  # the LedgerNode builder degrades the same way
        self.assertIsNone(node.rows[0].codeSubject)

    def test_read_ledger_enriches_official_rows_with_code_root(self) -> None:
        # memory.md lives in the (git) memory repo so its memory commits resolve; code_root carries the code side
        repo, shas = self._repo_with_commits(["official code", "official memory"])
        ledger = prepend_mapping(create_initial_ledger("repo-a", "base", "base"), shas[0], shas[1])
        write_ledger(repo / "memory.md", ledger)
        node = read_ledger(repo, code_root=repo)
        assert node is not None
        self.assertEqual(node.rows[0].codeSubject, "official code")
        self.assertEqual(node.rows[0].memorySubject, "official memory")


class DriftSnapshotProducerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.tmp = Path(self._dir.name)

    def test_producer_write_is_readable_by_reducer(self) -> None:
        repo = (self.tmp / "repo-x").resolve()
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "feat-x", str(repo)], check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.email=t@example.com",
                "-c",
                "user.name=t",
                "commit",
                "--allow-empty",
                "-m",
                "init",
            ],
            check=True,
            capture_output=True,
        )
        coord = (self.tmp / "coord").resolve()
        onboarding = (self.tmp / "onb").resolve()
        memory = (self.tmp / "memory").resolve()
        onboarding.mkdir()
        memory.mkdir()
        context = SimpleNamespace(
            coordination_root=coord,
            onboarding_root=onboarding,
            memory_root=memory,
        )
        rows = [
            DriftRow(
                "onboarding/a.md",
                "a.py",
                "repo-x",
                "external",
                "h",
                "d",
                "up to date",
                "high",
                "none",
                "ok",
            ),
            DriftRow(
                "onboarding/b.md",
                "b.py",
                "repo-x",
                "external",
                "h",
                "d",
                "drifted",
                "medium",
                "logic",
                "changed",
            ),
        ]
        summary._write_drift_snapshot(repo, context, rows)
        expected_path = drift_snapshot_path(coord, repository="repo-x", branch="feat-x")
        self.assertTrue(expected_path.exists())
        nodes = read_drift_snapshots(coord, now=FRESH)
        self.assertEqual(len(nodes), 1)
        self.assertEqual((nodes[0].repository, nodes[0].branch), ("repo-x", "feat-x"))
        self.assertEqual(nodes[0].counts["drifted"], 1)
        self.assertEqual(nodes[0].counts["up to date"], 1)
        self.assertEqual(nodes[0].actionableCount, 1)
        self.assertEqual(nodes[0].sourceRoot, repo.as_posix())
        self.assertEqual(nodes[0].memoryRoot, memory.as_posix())
        self.assertIsNotNone(nodes[0].checkedAt)


class ProjectAndWriteAnalyticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.tmp = Path(self._dir.name)
        self.coord = (self.tmp / "coord").resolve()
        self.coord.mkdir()
        self.mem = (self.tmp / "mem-repo-a").resolve()
        (self.mem / "onboarding").mkdir(parents=True)

    def _config(self) -> McpRuntimeConfig:
        return McpRuntimeConfig(
            config_path=self.coord / "mcp.settings.json",
            coordination_root=self.coord,
            workspace_root=(self.tmp / "ws").resolve(),
            transcript_root=self.coord / "logs",
            repositories={
                "repo-a": RepositoryScope(
                    repo_id="repo-a",
                    path=(self.tmp / "ws" / "repo-a").resolve(),
                    memory_root=self.mem,
                )
            },
            providers={
                "codegraphcontext-code": ProviderScope(
                    provider_id="codegraphcontext-code",
                    runtime_root=self.coord / "rt",
                    log_root=self.coord / "lg",
                    instance_id="projects",
                    scope="workspace",
                )
            },
        )

    def test_analytics_populated_end_to_end(self) -> None:
        config = self._config()
        EventStore(observer_root(config)).append(_started(lifecycle_id="LC1", ts=T0))
        directory = drift_snapshot_dir(self.coord)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "repo-a__main.json").write_text(
            json.dumps(
                {
                    "schema": DRIFT_SNAPSHOT_SCHEMA,
                    "repository": "repo-a",
                    "branch": "main",
                    "checkedAt": T0,
                    "counts": {"drifted": 1},
                    "actionableCount": 1,
                    "rows": [],
                }
            ),
            encoding="utf-8",
        )
        write_ledger(
            self.mem / "memory.md",
            prepend_mapping(create_initial_ledger("repo-a", "aaaa", "bbbb"), "cccc", "dddd"),
        )
        (self.mem / "onboarding" / "a.py.md").write_text(
            "| Field | Value |\n| --- | --- |\n| doc_type | `file-level-onboarding` |\n| lastVerifiedCommitDate | 2026-06-13T17:00:00+00:00 |\n",
            encoding="utf-8",
        )
        write_task_doc(
            self.coord / "tasks" / "repo-a" / "demo",
            TaskDocument.model_validate(
                {
                    "id": "D",
                    "slug": "task",
                    "title": "Demo",
                    "kind": "light",
                    "repo": "repo-a",
                    "createdAt": "2026-01-01T00:00",
                    "lifecycleId": "LC1",
                    "steps": [{"id": "S1", "title": "a", "status": "done"}],
                }
            ),
        )
        proj = project_and_write(config, now=FRESH)
        self.assertEqual(len(proj.analytics.driftSnapshots), 1)
        self.assertEqual(proj.analytics.driftSnapshots[0].counts["drifted"], 1)
        self.assertEqual(proj.analytics.ledgers[0].closeoutCount, 2)
        self.assertEqual(len(proj.analytics.stalestSidecars), 1)
        self.assertEqual(proj.metrics.stalenessHistogram["<7d"], 1)
        self.assertEqual(len(proj.analytics.taskDocuments), 1)
        self.assertEqual(proj.analytics.taskDocuments[0].lifecycleId, "LC1")
        state = json.loads(
            (observer_root(config) / "latest-state.json").read_text(encoding="utf-8")
        )
        self.assertIn("analytics", state)

    def test_repo_surface_cache_reuses_recent_repo_reads(self) -> None:
        config = self._config()
        _repo_surface_cache.clear()
        self.addCleanup(_repo_surface_cache.clear)
        first = ([], [], [])
        refreshed = ([], [], [])
        with mock.patch(
            "agents_remember.serving.projections.projection_store._gather_repo_surfaces",
            side_effect=[first, refreshed],
        ) as gather:
            # REPO_SURFACE_REFRESH_TTL_SECONDS is 120s: a second
            # read 10s after the fill still hits the cache; only a read past the 120s TTL refreshes.
            one = _gather_repo_surfaces_cached(config, FRESH)
            two = _gather_repo_surfaces_cached(config, datetime(2026, 6, 13, 18, 0, 40, tzinfo=UTC))
            three = _gather_repo_surfaces_cached(
                config, datetime(2026, 6, 13, 18, 2, 31, tzinfo=UTC)
            )

        self.assertIs(one, first)
        self.assertIs(two, first)
        self.assertIs(three, refreshed)
        self.assertEqual(gather.call_count, 2)

    def test_project_and_write_keeps_provider_reads_on_fast_path_with_cached_surfaces(self) -> None:
        config = self._config()
        _repo_surface_cache.clear()
        self.addCleanup(_repo_surface_cache.clear)
        with (
            mock.patch(
                "agents_remember.serving.projections.projection_store._gather_repo_surfaces",
                return_value=([], [], []),
            ) as gather,
            mock.patch(
                "agents_remember.serving.projections.projection_inputs.read_providers",
                return_value=[],
            ) as providers,
        ):
            project_and_write(config, now=FRESH)
            project_and_write(config, now=datetime(2026, 6, 13, 18, 0, 40, tzinfo=UTC))

        self.assertEqual(gather.call_count, 1)
        self.assertEqual(providers.call_count, 2)

    def test_project_and_write_prunes_orphaned_worktree_drift_snapshots(self) -> None:
        config = self._config()
        official = self._write_snapshot("repo-a", "main")
        active_contract = default_contract(
            ContractTask(
                name="active task",
                repo_name="repo-a",
                coordination_root=self.coord,
                workflow_kind="light-task",
                memory_mode="disabled",
            ),
            leaf=LeafIdentity(worktree_name="active-worktree"),
            code=RepoBranchPlan(
                repo_path=(self.tmp / "ws" / "repo-a").resolve(),
                source_branch="feat/dashboard",
                work_branch="ar/active",
                base_commit="base",
            ),
        )
        active_contract.code_worktree.mkdir(parents=True)
        write_contract(active_contract.contract_path, active_contract)
        publish_new_lifecycle_operation_location(
            active_contract,
            contract_text=active_contract.contract_path.read_text(encoding="utf-8"),
        )
        active = self._write_snapshot(active_contract.code_worktree.name, "ar/active")
        orphaned = self._write_snapshot("deleted-worktree", "ar/deleted")
        invalid = drift_snapshot_dir(self.coord) / "invalid.json"
        invalid.write_text(
            json.dumps({"schema": "other/v9", "repository": "deleted", "branch": "ar/x"}),
            encoding="utf-8",
        )

        proj = project_and_write(config, now=FRESH)

        self.assertTrue(official.exists())
        self.assertTrue(active.exists())
        self.assertFalse(orphaned.exists())
        self.assertTrue(invalid.exists())
        self.assertEqual(
            {(node.repository, node.branch) for node in proj.analytics.driftSnapshots},
            {("repo-a", "main"), (active_contract.code_worktree.name, "ar/active")},
        )

    def _write_snapshot(self, repository: str, branch: str) -> Path:
        path = drift_snapshot_path(self.coord, repository=repository, branch=branch)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema": DRIFT_SNAPSHOT_SCHEMA,
                    "repository": repository,
                    "branch": branch,
                    "checkedAt": T0,
                    "counts": {"drifted": 1},
                    "actionableCount": 1,
                    "rows": [],
                }
            ),
            encoding="utf-8",
        )
        return path
