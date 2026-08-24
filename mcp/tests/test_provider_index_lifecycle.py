"""Provider index lifecycle (260707-HFX-L2): small diffs UPDATE indexes, never a teardown.

The developer-pinned failure cycle: providers seed fine on the first leaf (the
integration branch has no changes yet); after that leaf integrates, the next
worktree's seed saw a HEAD mismatch and hard-crashed into a full teardown +
re-index from scratch (the refresh-all fallback), while the watchers never
delta-updated. These tests pin the repaired lifecycle: relatable divergence
seeds anyway and hands the delta to the watcher-event catch-up; the mtime sync
leaves exactly the divergent files fresh; the from-zero rebuild is explicit
opt-in only.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar, cast
from unittest import mock

from agents_remember.providers.cgc import seed as cgc_seed
from agents_remember.providers.context import ContextProviderError
from agents_remember.providers.metrics import (
    PROVIDER_INDEX_STATE_SCHEMA,
    ProviderMetricsStore,
)
from agents_remember.providers.provider_setup import (
    _cgc_watcher_container_name,
    _seed_catchup_results,
    _wait_for_cgc_watcher_ready,
)
from agents_remember.worktrees.modules.startup.start_memory import _sync_worktree_memory_mtimes
from agents_remember.worktrees.worktree_contract import WorktreeContract


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@test")
    _git(root, "config", "user.name", "test")


def _commit_file(root: Path, name: str, content: str, message: str) -> str:
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(content, encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


class SeedDivergenceTests(unittest.TestCase):
    def test_relatable_heads_report_the_changed_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            base = _commit_file(repo, "a.py", "one", "base")
            head = _commit_file(repo, "b.py", "two", "integration lands")
            divergence = cgc_seed.seed_commit_divergence(repo, head, base)
        assert divergence is not None
        # Direction: graph@source(head, has b.py) vs tree@target(base, lacks it)
        # = a deletion transition — a phantom the catch-up must report, not touch.
        self.assertEqual(divergence["entries"], [{"status": "D", "path": "b.py"}])
        self.assertEqual(divergence["count"], 1)

    def test_unrelatable_heads_return_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            head = _commit_file(repo, "a.py", "one", "base")
            divergence = cgc_seed.seed_commit_divergence(
                repo,
                head,
                "0" * 40,  # commit that does not exist anywhere
            )
        self.assertIsNone(divergence)


class SeedMismatchTests(unittest.TestCase):
    def _args(self) -> argparse.Namespace:
        return argparse.Namespace(cgc_seed_allow_commit_mismatch=False)

    def test_relatable_divergence_proceeds_and_stashes_the_delta(self) -> None:
        # The developer cycle, seed half: the source repo advanced by one
        # integration commit; the seed must PROCEED (near-perfect graph clone)
        # and record the delta for the catch-up stage — never refuse.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            target_head = _commit_file(repo, "a.py", "one", "leaf-2 checkout state")
            source_head = _commit_file(repo, "integrated.py", "two", "leaf-1 integrated")
            args = self._args()
            refusal = cgc_seed._seed_commit_mismatch(
                args, repo, repo / "wt", source_head, target_head
            )
        self.assertIsNone(refusal)
        divergence = args._cgc_seed_divergence
        self.assertEqual(divergence["entries"], [{"status": "D", "path": "integrated.py"}])
        self.assertEqual(divergence["sourceHead"], source_head)
        self.assertEqual(divergence["targetHead"], target_head)

    def test_equal_heads_proceed_without_divergence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            head = _commit_file(repo, "a.py", "one", "base")
            args = self._args()
            refusal = cgc_seed._seed_commit_mismatch(args, repo, repo / "wt", head, head)
        self.assertIsNone(refusal)
        self.assertFalse(hasattr(args, "_cgc_seed_divergence"))

    def test_unrelatable_heads_still_refuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            head = _commit_file(repo, "a.py", "one", "base")
            args = self._args()
            refusal = cgc_seed._seed_commit_mismatch(args, repo, repo / "wt", head, "0" * 40)
        assert refusal is not None
        self.assertFalse(refusal["ok"])
        self.assertIn("unrelated", refusal["reason"])


class SeedCatchupTests(unittest.TestCase):
    def _args(self, tmp: Path, divergence: dict | None, **overrides) -> argparse.Namespace:
        values = {
            "dry_run": False,
            "coordination_root": tmp / "coord",
            "cgc_seed_target_repo_root": tmp / "target",
            "cgc_seed_delta_max_files": 0,
            "cgc_seed_repo_id": "repo-a",
        }
        values.update(overrides)
        args = argparse.Namespace(**values)
        if divergence is not None:
            args._cgc_seed_divergence = divergence
        return args

    def _ready(self):
        return mock.patch(
            "agents_remember.providers.provider_setup._wait_for_cgc_watcher_ready",
            return_value={"ready": True, "container": "c", "waitedSeconds": 0.0},
        )

    def test_small_diff_touches_exactly_the_delta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            target = tmp / "target"
            target.mkdir()
            changed = target / "changed.py"
            untouched = target / "untouched.py"
            changed.write_text("x", encoding="utf-8")
            untouched.write_text("y", encoding="utf-8")
            old = time.time() - 3600
            os.utime(changed, (old, old))
            os.utime(untouched, (old, old))
            divergence = {
                "entries": [
                    {"status": "M", "path": "changed.py"},
                    {"status": "M", "path": "gone.py"},
                ],
                "count": 2,
                "sourceHead": "s",
                "targetHead": "t",
            }
            with self._ready():
                results = _seed_catchup_results(self._args(tmp, divergence), {})
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["touched"], 1)
            # Honesty (review L2/B2): the undeliverable path is residual
            # staleness — never blessed as caught up.
            self.assertFalse(results[0]["caughtUp"])
            residuals = results[0]["staleIndex"]["residuals"]
            self.assertEqual(residuals, [{"kind": "missing-on-disk", "path": "gone.py"}])
            self.assertGreater(changed.stat().st_mtime, old + 1)  # fresh → re-indexed
            self.assertLess(abs(untouched.stat().st_mtime - old), 1)  # untouched stays
            store = ProviderMetricsStore(tmp / "coord")
            rows = store.read_recent()
            self.assertEqual(rows[-1]["schema"], PROVIDER_INDEX_STATE_SCHEMA)
            self.assertEqual(rows[-1]["touched"], 1)
            self.assertEqual(rows[-1]["repoId"], "repo-a")

    def test_clean_delta_to_ready_watcher_is_caught_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            target = tmp / "target"
            target.mkdir()
            (target / "changed.py").write_text("x", encoding="utf-8")
            divergence = {
                "entries": [{"status": "M", "path": "changed.py"}],
                "count": 1,
                "sourceHead": "s",
                "targetHead": "t",
            }
            with self._ready():
                results = _seed_catchup_results(self._args(tmp, divergence), {})
        self.assertTrue(results[0]["caughtUp"])
        self.assertNotIn("staleIndex", results[0])

    def test_watcher_not_ready_means_no_touch_and_honest_staleness(self) -> None:
        # Review L2/B1: inotify has no replay — an early touch is silently
        # lost. No ready marker ⇒ no touch, surfaced staleness.
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            target = tmp / "target"
            target.mkdir()
            changed = target / "changed.py"
            changed.write_text("x", encoding="utf-8")
            old = time.time() - 3600
            os.utime(changed, (old, old))
            divergence = {
                "entries": [{"status": "M", "path": "changed.py"}],
                "count": 1,
                "sourceHead": "s",
                "targetHead": "t",
            }
            with mock.patch(
                "agents_remember.providers.provider_setup._wait_for_cgc_watcher_ready",
                return_value={"ready": False, "reason": "no marker"},
            ):
                results = _seed_catchup_results(self._args(tmp, divergence), {})
            self.assertTrue(results[0]["skipped"])
            self.assertIn("not ready", results[0]["staleIndex"]["reason"])
            self.assertLess(abs(changed.stat().st_mtime - old), 1)  # NOT touched

    def test_deletions_and_renames_are_phantom_residuals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            target = tmp / "target"
            target.mkdir()
            (target / "renamed_new.py").write_text("x", encoding="utf-8")
            divergence = {
                "entries": [
                    {"status": "D", "path": "removed.py"},
                    {"status": "R", "path": "renamed_new.py", "from": "renamed_old.py"},
                ],
                "count": 2,
                "sourceHead": "s",
                "targetHead": "t",
            }
            with self._ready():
                results = _seed_catchup_results(self._args(tmp, divergence), {})
        self.assertEqual(results[0]["touched"], 1)  # the rename's new path
        self.assertFalse(results[0]["caughtUp"])
        kinds = sorted(r["kind"] for r in results[0]["staleIndex"]["residuals"])
        self.assertEqual(kinds, ["deleted-phantom", "rename-source-phantom"])

    def test_big_diff_serves_stale_and_never_touches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            (tmp / "target").mkdir()
            divergence = {
                "entries": [{"status": "M", "path": f"f{i}.py"} for i in range(5)],
                "count": 5,
                "sourceHead": "s",
                "targetHead": "t",
            }
            results = _seed_catchup_results(
                self._args(tmp, divergence, cgc_seed_delta_max_files=3), {}
            )
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["skipped"])
        stale = results[0]["staleIndex"]
        self.assertTrue(stale["served"])
        self.assertEqual(stale["behindFiles"], 5)
        self.assertIn("explicit", stale["reindex"])

    def test_no_divergence_or_dry_run_is_a_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            self.assertEqual(_seed_catchup_results(self._args(tmp, None), {}), [])
            divergence = {
                "entries": [{"status": "M", "path": "a"}],
                "count": 1,
                "sourceHead": "s",
                "targetHead": "t",
            }
            self.assertEqual(
                _seed_catchup_results(self._args(tmp, divergence, dry_run=True), {}), []
            )


class WatcherReadinessTests(unittest.TestCase):
    _SETTINGS: ClassVar[dict] = {
        "contextProviders": {
            "enabled": True,
            "providers": {
                "codegraphcontext-code": {
                    "enabled": True,
                    "runtime": {"runner": {"containerNameTemplate": "ar-cgc-watch-i1-<repoId>"}},
                }
            },
        }
    }

    def _args(self) -> argparse.Namespace:
        return argparse.Namespace(cgc_seed_repo_id="repo-a", coordination_root=Path("/tmp"))

    def test_container_name_expands_the_template(self) -> None:
        name = _cgc_watcher_container_name(self._args(), self._SETTINGS)
        assert name is not None
        self.assertTrue(name.startswith("ar-cgc-watch-i1-"))
        self.assertNotIn("<", name)

    def test_container_name_unresolvable_without_provider_or_repo(self) -> None:
        self.assertIsNone(_cgc_watcher_container_name(self._args(), {}))
        args = argparse.Namespace(cgc_seed_repo_id=None, coordination_root=Path("/tmp"))
        self.assertIsNone(_cgc_watcher_container_name(args, self._SETTINGS))

    def test_ready_when_marker_appears_in_logs(self) -> None:
        with (
            mock.patch(
                "agents_remember.providers.provider_setup.docker_command",
                return_value="docker",
            ),
            mock.patch(
                "agents_remember.providers.provider_setup.run_command",
                return_value={
                    "returncode": 0,
                    "stdout": "[watch-guard] ...\nMonitoring for file changes\n",
                    "stderr": "",
                },
            ),
        ):
            result = _wait_for_cgc_watcher_ready(self._args(), self._SETTINGS, timeout_seconds=1)
        self.assertTrue(result["ready"])

    def test_not_ready_times_out_without_marker(self) -> None:
        with (
            mock.patch(
                "agents_remember.providers.provider_setup.docker_command",
                return_value="docker",
            ),
            mock.patch(
                "agents_remember.providers.provider_setup.run_command",
                return_value={"returncode": 0, "stdout": "booting...", "stderr": ""},
            ),
        ):
            result = _wait_for_cgc_watcher_ready(self._args(), self._SETTINGS, timeout_seconds=0)
        self.assertFalse(result["ready"])
        self.assertIn("no watch-ready marker", result["reason"])

    def test_unresolved_container_and_dockerless_are_not_ready(self) -> None:
        result = _wait_for_cgc_watcher_ready(self._args(), {}, timeout_seconds=0)
        self.assertFalse(result["ready"])
        self.assertIn("unresolved", result["reason"])
        with mock.patch(
            "agents_remember.providers.provider_setup.docker_command",
            side_effect=ContextProviderError("docker command not found"),
        ):
            result = _wait_for_cgc_watcher_ready(self._args(), self._SETTINGS, timeout_seconds=0)
        self.assertFalse(result["ready"])


class MemoryMtimeSyncDivergenceTests(unittest.TestCase):
    def test_divergent_files_keep_fresh_mtimes(self) -> None:
        # The developer cycle, grepai half: the memory worktree carries a delta
        # relative to the source checkout; stamping old mtimes onto that delta
        # would make the watcher skip it (silent staleness). The delta must
        # stay fresh; everything else syncs so the cloned index is reused.
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            source = tmp / "memory"
            _init_repo(source)
            _commit_file(source, "same.md", "unchanged", "base")
            _commit_file(source, "delta.md", "old content", "base 2")
            _git(source, "branch", "task-branch")
            worktree = tmp / "memory-wt"
            _git(source, "worktree", "add", "-q", str(worktree), "task-branch")
            _commit_file(worktree, "delta.md", "NEW content", "the divergence")
            fresh = time.time()
            old = fresh - 3600
            for repo in (source, worktree):
                for name in ("same.md", "delta.md"):
                    path = repo / name
                    if repo is source:
                        os.utime(path, (old, old))  # source carries old mtimes
                    else:
                        os.utime(path, (fresh, fresh))  # checkout stamped fresh
            contract = cast(
                WorktreeContract,
                SimpleNamespace(memory_repo_path=source, memory_worktree=worktree),
            )
            result = _sync_worktree_memory_mtimes(contract, dry_run=False)
            self.assertEqual(result["state"], "synced")
            self.assertEqual(result["divergentLeftFresh"], 1)
            same_mtime = (worktree / "same.md").stat().st_mtime
            delta_mtime = (worktree / "delta.md").stat().st_mtime
        self.assertLess(abs(same_mtime - old), 1)  # synced to source: watcher reuses index
        self.assertGreater(delta_mtime, old + 1)  # left fresh: watcher re-embeds the delta

    def test_equal_heads_sync_everything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            source = tmp / "memory"
            _init_repo(source)
            _commit_file(source, "same.md", "unchanged", "base")
            _git(source, "branch", "task-branch")
            worktree = tmp / "memory-wt"
            _git(source, "worktree", "add", "-q", str(worktree), "task-branch")
            contract = cast(
                WorktreeContract,
                SimpleNamespace(memory_repo_path=source, memory_worktree=worktree),
            )
            result = _sync_worktree_memory_mtimes(contract, dry_run=False)
        self.assertEqual(result["state"], "synced")
        self.assertEqual(result["divergentLeftFresh"], 0)
        self.assertGreaterEqual(int(str(result["filesSynced"])), 1)


class IntegrationCycleReproTests(unittest.TestCase):
    def test_leaf1_integrates_then_leaf2_seeds_with_catchup(self) -> None:
        """The developer's exact crash cycle, end to end at the seam level.

        leaf 1 integrates (source repo advances) → leaf 2's checkout is one
        commit behind → the seed PROCEEDS (no refusal, no refresh-all) and the
        catch-up stage touches exactly the integrated files so the watchers
        update the index in place. Zero teardowns anywhere in the flow.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            repo = tmp / "repo"
            _init_repo(repo)
            _commit_file(repo, "core.py", "v1", "initial")
            _git(repo, "branch", "leaf-2")
            worktree = tmp / "leaf-2-wt"
            _git(repo, "worktree", "add", "-q", str(worktree), "leaf-2")
            target_head = _git(worktree, "rev-parse", "HEAD")
            # leaf 1 lands on the integration branch (the source moves).
            source_head = _commit_file(repo, "integrated.py", "leaf-1 work", "leaf-1 lands")

            args = argparse.Namespace(
                cgc_seed_allow_commit_mismatch=False,
                dry_run=False,
                coordination_root=tmp / "coord",
                cgc_seed_target_repo_root=worktree,
                cgc_seed_delta_max_files=0,
                cgc_seed_repo_id="repo",
            )
            refusal = cgc_seed._seed_commit_mismatch(args, repo, worktree, source_head, target_head)
            self.assertIsNone(refusal)  # the seed proceeds — no crash-out

            # leaf-2 is BEHIND: the cloned graph carries integrated.py while
            # the tree lacks it — an ahead-content phantom, honestly reported
            # as residual staleness (review L2/B2), never a teardown/reindex.
            with mock.patch(
                "agents_remember.providers.provider_setup._wait_for_cgc_watcher_ready",
                return_value={"ready": True, "container": "c", "waitedSeconds": 0.0},
            ):
                results = _seed_catchup_results(args, {})
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["divergence"]["count"], 1)
            self.assertFalse(results[0]["caughtUp"])
            self.assertEqual(
                results[0]["staleIndex"]["residuals"],
                [{"kind": "deleted-phantom", "path": "integrated.py"}],
            )


if __name__ == "__main__":
    unittest.main()
