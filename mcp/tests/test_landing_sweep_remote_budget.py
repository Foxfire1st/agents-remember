"""MIK-R81 rule 6 (F12): one ls-remote per repository and ref per landing sweep.

Contracts that watch the same origin branches share one probe per sweep -- the count
scales with repositories x refs (feature branch, origin HEAD, protected base), not with
contracts -- failures included. The next sweep probes again, because a remote moves.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agents_remember.serving.projections.landing_state import LandingStateRefresher
from agents_remember.worktrees.modules import landing as landing_module
from agents_remember.worktrees.modules.landing import landing_refs
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    write_contract,
)
from test_serving import _config
from test_worktree_support import git


def _origin_with_branches(root: Path) -> tuple[Path, Path]:
    """A bare origin with main+super, and a code repo tracking it."""

    origin = root / "origin.git"
    origin.mkdir()
    git(origin, "init", "--bare", "-b", "main")
    code = root / "code"
    code.mkdir()
    git(code, "init", "-b", "main")
    (code / "file.txt").write_text("base\n", encoding="utf-8")
    git(code, "add", "-A")
    git(code, "commit", "-m", "base")
    git(code, "branch", "super", "main")
    git(code, "remote", "add", "origin", origin.as_posix())
    git(code, "push", "origin", "main", "super")
    return origin, code


def _plan(repo: Path) -> RepoBranchPlan:
    return RepoBranchPlan(repo, "super", "ar/leaf", "a" * 40)


class LandingSweepRemoteBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def _sweep_ls_remote_count(self, root: Path, code: Path, fleet: int) -> int:
        task = ContractTask("task", "repo", root, "light-task", "external")
        for index in range(fleet):
            # Finishing arcs (closeout completed, cleanup still open) probe every sweep;
            # completed history is never probed just to be re-observed (F15), so the
            # shared-origin budget is measured on finishing arcs.
            contract = replace(
                default_contract(
                    task,
                    leaf=LeafIdentity(f"leaf-{index}"),
                    code=_plan(code),
                    memory=_plan(root / "memory-missing"),
                ),
                closeout_status="completed",
            )
            write_contract(contract.contract_path, contract)
        calls = []
        real_run_git = landing_module.run_git

        def counting_run_git(repo: Path, argv: list[str], *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            if "ls-remote" in argv:
                calls.append((repo.as_posix(), argv[-1]))
            return real_run_git(repo, argv, *args, **kwargs)  # type: ignore[arg-type]

        refresher = LandingStateRefresher(_config(root), observe=landing_refs)
        with mock.patch.object(landing_module, "run_git", counting_run_git):
            await refresher.refresh_once()
        return len(calls)

    async def test_shared_origin_branches_probe_once_per_sweep_at_two_fleet_sizes(self) -> None:
        """Two and four finishing contracts on one origin: three ls-remote per sweep each."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _origin, code = _origin_with_branches(root)
            for fleet in (2, 4):
                count = await self._sweep_ls_remote_count(root, code, fleet)
                self.assertEqual(
                    count,
                    3,
                    f"fleet {fleet}: one origin/super, one origin/HEAD and one origin/main "
                    "probe per sweep",
                )
                # Fresh contracts per fleet size: drop any freeze files the success wrote so
                # the next fleet measures its own sweep (open arcs freeze nothing, but a
                # completed-then-reopened arc could have left one behind).
                for stale in root.glob("tasks/**/landing-final.json"):
                    stale.unlink()

    async def test_broken_remote_fails_once_per_sweep_and_refreshes_next_sweep(self) -> None:
        """Three live contracts on a dead origin: three failed probes per sweep, re-run per sweep.

        Live arcs probe every sweep (the one-attempt budget covers finishing arcs only), so
        two sweeps show the memo refreshing: the same three probes, never nine.
        """

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code = root / "code"
            code.mkdir()
            git(code, "init", "-b", "main")
            (code / "file.txt").write_text("base\n", encoding="utf-8")
            git(code, "add", "-A")
            git(code, "commit", "-m", "base")
            git(code, "branch", "super", "main")
            git(code, "remote", "add", "origin", (root / "no-such-remote.git").as_posix())
            task = ContractTask("task", "repo", root, "light-task", "external")
            for index in range(3):
                contract = replace(
                    default_contract(
                        task,
                        leaf=LeafIdentity(f"leaf-{index}"),
                        code=_plan(code),
                        memory=_plan(root / "memory-missing"),
                    ),
                    closeout_status="completed",
                )
                write_contract(contract.contract_path, contract)
            calls: list[tuple[str, str]] = []
            real_run_git = landing_module.run_git

            def counting_run_git(repo: Path, argv: list[str], *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
                if "ls-remote" in argv:
                    calls.append((repo.as_posix(), argv[-1]))
                return real_run_git(repo, argv, *args, **kwargs)  # type: ignore[arg-type]

            refresher = LandingStateRefresher(_config(root), observe=landing_refs)
            with mock.patch.object(landing_module, "run_git", counting_run_git):
                await refresher.refresh_once()
                first = len(calls)
                await refresher.refresh_once()
                second = len(calls) - first
            self.assertEqual(first, 3, "one failed probe per (repo, ref), not per contract")
            self.assertEqual(second, 3, "the next sweep probes again: a remote moves")
