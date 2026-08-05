"""Worktree lifecycle paths that only a refusal or a recovery reaches.

The happy path through start / sync / integrate / cleanup is covered elsewhere. What is
not is the other side of each guard: the contract that refuses an unknown memory mode,
the start that must hand a blocked preflight straight back instead of creating worktrees,
the fast-forward recovery that has to rebuild the contract because the branch tips moved
under it, the sync that aborts a conflicting memory merge, the retirement that has to
step off the branch it is about to delete.

Each of those is the case where getting it wrong is expensive -- worktrees created on a
stale base, a half-integrated pair, a branch deleted while checked out -- so each is
tested for the verdict it produces, not merely for running.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from typing import Any, cast
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.worktrees import leaf_refs
from agents_remember.worktrees.modules import cleanup as cleanup_module
from agents_remember.worktrees.modules import integrate as integrate_module
from agents_remember.worktrees.modules import onboarding as onboarding_module
from agents_remember.worktrees.modules import start as start_module
from agents_remember.worktrees.modules import start_contract as start_contract_module
from agents_remember.worktrees.modules import sync as sync_module
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    default_series_contract,
    write_contract,
)
from test_worktree_support import closed_external_contract_fixture, git, init_repo


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def commit(repo: Path, relative: str, body: str, message: str) -> str:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    run_git(repo, "add", relative)
    run_git(repo, "commit", "-m", message)
    return run_git(repo, "rev-parse", "HEAD")


def contract_for(root: Path, *, memory_mode: str = "disabled", code_repo: Path | None = None):
    repo = code_repo if code_repo is not None else root / "repo-a"
    return default_contract(
        ContractTask(
            name="Edge Task",
            repo_name="repo-a",
            coordination_root=root / "ar-coordination",
            workflow_kind="light-task",
            memory_mode=memory_mode,
        ),
        leaf=LeafIdentity(worktree_name="edge-task"),
        code=RepoBranchPlan(
            repo_path=repo,
            source_branch="main",
            work_branch="ar/edge-task",
            base_commit="abc123",
        ),
        memory=RepoBranchPlan(repo_path=None, source_branch="", work_branch="", base_commit=""),  # type: ignore[arg-type]
    )


class ContractMemoryModeTests(unittest.TestCase):
    """A contract is the durable record of a task; an unknown memory mode is refused
    at construction rather than written out and discovered later."""

    def task(self, memory_mode: str) -> ContractTask:
        return ContractTask(
            name="Edge Task",
            repo_name="repo-a",
            coordination_root=Path("/tmp/ar-coordination"),
            workflow_kind="light-task",
            memory_mode=memory_mode,
        )

    def code_plan(self) -> RepoBranchPlan:
        return RepoBranchPlan(
            repo_path=Path("/tmp/repo-a"),
            source_branch="main",
            work_branch="ar/edge-task",
            base_commit="abc123",
        )

    def test_leaf_contract_refuses_an_unknown_memory_mode(self) -> None:
        with self.assertRaises(ContractError) as raised:
            default_contract(
                self.task("sometimes"),
                leaf=LeafIdentity(worktree_name="edge-task"),
                code=self.code_plan(),
            )

        self.assertIn("memory_mode must be one of", str(raised.exception))

    def test_series_contract_refuses_an_unknown_memory_mode(self) -> None:
        with self.assertRaises(ContractError) as raised:
            default_series_contract(self.task("sometimes"), code=self.code_plan())

        self.assertIn("memory_mode must be one of", str(raised.exception))

    def test_a_known_memory_mode_is_accepted_by_both(self) -> None:
        leaf = default_contract(
            self.task("disabled"),
            leaf=LeafIdentity(worktree_name="edge-task"),
            code=self.code_plan(),
        )
        series = default_series_contract(self.task("disabled"), code=self.code_plan())

        self.assertEqual(leaf.memory_mode, "disabled")
        self.assertEqual(series.kind, "series")

    def test_a_refused_request_leaves_the_start_as_a_result_not_an_exception(self) -> None:
        """The refusal above must reach the caller as a payload.

        ``worktree_start``'s handler has no ``except ContractError`` anywhere on its path --
        not in ``mcp/registration/worktrees.py``, not in ``application/worktree_tools.py``,
        not in ``mcp/tools/worktree.py`` -- so a construction refusal raised out of
        ``build_start_contract`` would leave the tool as a traceback rather than a blocked
        result the agent can read and correct. ``_build_start_contract`` is patched because
        the refusal it raises is the subject; reaching it for real would mean standing up a
        git repository to test an argument check.
        """
        with mock.patch.object(
            start_contract_module,
            "_build_start_contract",
            side_effect=ContractError("workflow_kind must be one of ['chat-task', 'light-task']"),
        ):
            result = start_contract_module.build_start_contract(Namespace(), WorktreeArgs())

        assert isinstance(result, WorktreeCommandResult)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["state"], "invalid-request")
        self.assertIn("workflow_kind must be one of", str(result.payload["summary"]))


class DeclaredLeafCandidateTests(unittest.TestCase):
    """Leaf ids come out of hand-editable task documents, so a blank one is data, not a
    programming error: it is skipped rather than minting a candidate nothing can address."""

    def test_a_blank_declared_leaf_id_produces_no_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_root = Path(tmp) / "260731-EDGE"
            task_root.mkdir(parents=True)
            declared = [("   ", ("blank-slug",)), ("260731-EDGE-L1", ("leaf-one",))]

            with mock.patch.object(leaf_refs, "_declared_leaves", return_value=declared):
                candidates = leaf_refs._leaf_candidates_for_root("repo-a", task_root)

            self.assertEqual([candidate.doc_id for candidate in candidates], ["260731-EDGE-L1"])


class RetireWorkBranchTests(unittest.TestCase):
    """``_retire_work_branch`` deletes a task branch only when it is safe to."""

    def test_the_default_branch_is_never_retired(self) -> None:
        """Retiring the branch the repo falls back to would leave the repo with nowhere
        to check out, so it is refused before any git command runs."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            init_repo(repo, "main")

            out = cleanup_module._retire_work_branch(
                cleanup_module.RetiringBranch(
                    repo=repo, branch="main", source_branch="main", default_branch="main"
                ),
                dry_run=False,
                remote=False,
            )

            self.assertEqual(
                out, {"branch": "main", "deleted": False, "reason": "default-or-empty"}
            )

    def test_an_empty_branch_name_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            init_repo(repo, "main")

            out = cleanup_module._retire_work_branch(
                cleanup_module.RetiringBranch(
                    repo=repo, branch="", source_branch="main", default_branch="main"
                ),
                dry_run=False,
                remote=False,
            )

            self.assertEqual(out["reason"], "default-or-empty")

    def test_a_checked_out_work_branch_is_stepped_off_before_deletion(self) -> None:
        """git refuses to delete the branch HEAD is on. The retirement checks the default
        branch out first, so a merged work branch still disappears."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            init_repo(repo, "main")
            run_git(repo, "checkout", "-b", "ar/edge-task")

            out = cleanup_module._retire_work_branch(
                cleanup_module.RetiringBranch(
                    repo=repo,
                    branch="ar/edge-task",
                    source_branch="main",
                    default_branch="main",
                ),
                dry_run=False,
                remote=False,
            )

            self.assertIs(out["deleted"], True)
            self.assertEqual(run_git(repo, "branch", "--show-current"), "main")
            self.assertEqual(run_git(repo, "branch", "--list", "ar/edge-task"), "")


class StartPipelineTests(unittest.TestCase):
    """``start_result``'s composition: which stage's answer wins, and what start does not
    do once a stage refuses."""

    def test_a_blocked_preflight_is_returned_and_no_enclosure_is_created(self) -> None:
        """The preflight refusal is the whole result. Creating the worktrees anyway would
        put the task on the stale base the preflight just refused."""
        blocked = WorktreeCommandResult(2, {"state": "blocked", "summary": "source is behind"})
        with (
            mock.patch.object(start_module, "resolve_context", return_value=Namespace()),
            mock.patch.object(start_module, "build_start_contract", return_value=object()),
            mock.patch.object(start_module, "_existing_contract_result", return_value=None),
            mock.patch.object(start_module, "_preflighted_contract", return_value=blocked),
            mock.patch.object(start_module, "_create_start_enclosure") as create,
        ):
            result = start_module.start_result(WorktreeArgs())

        self.assertIs(result, blocked)
        create.assert_not_called()

    def test_a_usable_contract_goes_on_to_create_the_enclosure(self) -> None:
        contract = object()
        created = WorktreeCommandResult(0, {"state": "created"})
        with (
            mock.patch.object(start_module, "resolve_context", return_value=Namespace()),
            mock.patch.object(start_module, "build_start_contract", return_value=object()),
            mock.patch.object(start_module, "_existing_contract_result", return_value=None),
            mock.patch.object(start_module, "_preflighted_contract", return_value=contract),
            mock.patch.object(
                start_module, "_create_start_enclosure", return_value=created
            ) as create,
        ):
            result = start_module.start_result(WorktreeArgs())

        self.assertIs(result, created)
        self.assertIs(create.call_args.args[1], contract)


class MemoryDisabledStartTests(unittest.TestCase):
    """The memory-disabled recovery: a start that asked for external memory and could not get it.

    ``memory_choice='disable'`` answers the blocked-memory refusal, and the contract it
    produces has to stop describing a memory topology that will not exist -- the mode, the
    repo path, both branches, the base commit, the worktree and the ledger, together. Half of
    that would leave closeout looking for a memory repository the start declined to make.
    """

    def _external(self, root: Path):
        return default_contract(
            ContractTask(
                name="Edge Task",
                repo_name="repo-a",
                coordination_root=root,
                workflow_kind="light-task",
                memory_mode="external",
            ),
            leaf=LeafIdentity(worktree_name="edge-task"),
            code=RepoBranchPlan(
                repo_path=root / "repo-a",
                source_branch="main",
                work_branch="ar/edge-task",
                base_commit="abc123",
            ),
            memory=RepoBranchPlan(
                repo_path=root / "memory-repos" / "ar-repo-a",
                source_branch="main",
                work_branch="ar/edge-task",
                base_commit="def456",
            ),
        )

    def test_a_disabled_memory_start_drops_the_whole_memory_topology(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = self._external(Path(tmp))
        self.assertEqual(contract.memory_mode, "external")

        disabled = start_module._contract_after_memory_start(contract, {"state": "disabled"})

        self.assertEqual(disabled.memory_mode, "disabled")
        self.assertEqual(disabled.memory_state, "disabled")
        self.assertIsNone(disabled.memory_repo_path)
        self.assertIsNone(disabled.memory_worktree)
        self.assertIsNone(disabled.ledger_path)
        self.assertEqual(disabled.memory_source_branch, "")
        self.assertEqual(disabled.memory_work_branch, "")
        self.assertEqual(disabled.memory_base_commit, "")
        # ...and nothing on the code side moved with it.
        self.assertEqual(disabled.code_work_branch, contract.code_work_branch)

    def test_a_reconciled_memory_base_advances_only_that_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = self._external(Path(tmp))

        advanced = start_module._contract_after_memory_start(
            contract, {"state": "ready", "reconciledMemoryBaseCommit": "999999"}
        )

        self.assertEqual(advanced.memory_base_commit, "999999")
        self.assertEqual(advanced.memory_mode, "external")

    def test_an_unremarkable_memory_start_returns_the_contract_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = self._external(Path(tmp))

        self.assertIs(
            start_module._contract_after_memory_start(contract, {"state": "ready"}), contract
        )


class ExistingContractStartTests(unittest.TestCase):
    """What ``worktree_start`` does when a contract already exists at the path."""

    def _written_contract(self, root: Path):
        contract = contract_for(root)
        contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
        contract.worktree_group.mkdir(parents=True, exist_ok=True)
        write_contract(contract.contract_path, contract)
        return contract

    def test_retry_provider_setup_relaunches_instead_of_reattaching(self) -> None:
        """Without the flag a live contract is attached to; with it the same call is a
        provider-setup retry, which must not report itself as a plain re-attach."""
        with tempfile.TemporaryDirectory() as tmp:
            contract = self._written_contract(Path(tmp))
            retried = WorktreeCommandResult(0, {"state": "provider-setup-retried"})

            with mock.patch.object(
                start_module, "_retry_provider_setup_result", return_value=retried
            ) as retry:
                result = start_module._existing_contract_result(
                    Namespace(), contract, WorktreeArgs(retry_provider_setup=True)
                )

            self.assertIs(result, retried)
            self.assertEqual(retry.call_args.args[1].contract_path, contract.contract_path)

    def test_without_the_retry_flag_the_live_contract_is_attached_to(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = self._written_contract(Path(tmp))

            with mock.patch.object(start_module, "_retry_provider_setup_result") as retry:
                result = start_module._existing_contract_result(
                    Namespace(), contract, WorktreeArgs()
                )

            retry.assert_not_called()
            assert result is not None
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.payload["state"], "attached-existing-contract")


class PreflightedContractTests(unittest.TestCase):
    """``_preflighted_contract`` returns the contract the rest of start must use."""

    def test_a_fast_forward_recovery_rebuilds_the_contract_before_continuing(self) -> None:
        """The recovery moves the source branch, so the contract built before it records
        the pre-recovery base commit. The rebuilt one is what start goes on with."""
        original = cast(Any, object())
        rebuilt = cast(Any, object())
        with (
            mock.patch.object(start_module, "_stale_base_preflight", return_value=None),
            mock.patch.object(start_module, "build_start_contract", return_value=rebuilt) as build,
            mock.patch.object(start_module, "_long_path_preflight", return_value=None),
        ):
            result = start_module._preflighted_contract(
                Namespace(), original, WorktreeArgs(stale_base_choice="fast-forward")
            )

        self.assertIs(result, rebuilt)
        self.assertEqual(build.call_count, 1)

    def test_a_rebuild_that_fails_is_returned_instead_of_the_contract(self) -> None:
        failure = WorktreeCommandResult(2, {"state": "blocked", "summary": "rebuild failed"})
        with (
            mock.patch.object(start_module, "_stale_base_preflight", return_value=None),
            mock.patch.object(start_module, "build_start_contract", return_value=failure),
            mock.patch.object(start_module, "_long_path_preflight") as long_path,
        ):
            result = start_module._preflighted_contract(
                Namespace(), cast(Any, object()), WorktreeArgs(stale_base_choice="fast-forward")
            )

        self.assertIs(result, failure)
        long_path.assert_not_called()

    def test_without_a_fast_forward_choice_the_contract_is_not_rebuilt(self) -> None:
        original = cast(Any, object())
        with (
            mock.patch.object(start_module, "_stale_base_preflight", return_value=None),
            mock.patch.object(start_module, "build_start_contract") as build,
            mock.patch.object(start_module, "_long_path_preflight", return_value=None),
        ):
            result = start_module._preflighted_contract(Namespace(), original, WorktreeArgs())

        self.assertIs(result, original)
        build.assert_not_called()

    def test_the_long_path_preflight_sees_the_rebuilt_contract(self) -> None:
        """A rebuilt contract can point at a different worktree path, so the path-budget
        check must run against the contract start will actually use."""
        rebuilt = cast(Any, object())
        with (
            mock.patch.object(start_module, "_stale_base_preflight", return_value=None),
            mock.patch.object(start_module, "build_start_contract", return_value=rebuilt),
            mock.patch.object(start_module, "_long_path_preflight", return_value=None) as long_path,
        ):
            start_module._preflighted_contract(
                Namespace(), cast(Any, object()), WorktreeArgs(stale_base_choice="fast-forward")
            )

        self.assertIs(long_path.call_args.args[0], rebuilt)


class MemorySyncBlockTests(unittest.TestCase):
    """The two memory-side sync refusals, and what each tells the caller to do next."""

    def test_needs_review_asks_for_an_explicit_memory_sync_choice(self) -> None:
        contract = contract_for(Path("/tmp"))
        result = sync_module._memory_sync_block(
            contract, {"state": "fast-forwarded"}, {"state": "needs-review"}, {"code": "fetched"}
        )

        assert result is not None
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["state"], "blocked")
        self.assertEqual(result.payload["nextRequiredArgs"], ["memory_sync_choice"])
        self.assertIn("merge-memory", str(result.payload["summary"]))

    def test_a_conflicting_merge_reports_the_abort_and_offers_skip_memory(self) -> None:
        """The merge was already aborted, so the only forward move is to defer memory to
        end-of-task carryover; the block says so rather than repeating the choice prompt."""
        contract = contract_for(Path("/tmp"))
        result = sync_module._memory_sync_block(
            contract,
            {"state": "fast-forwarded"},
            {"state": "conflicts", "files": ["memory.md"]},
            {"code": "fetched"},
        )

        assert result is not None
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["state"], "blocked")
        self.assertIn("the merge was aborted", str(result.payload["summary"]))
        self.assertIn("skip-memory", str(result.payload["summary"]))
        self.assertNotIn("nextRequiredArgs", result.payload)
        self.assertEqual(result.payload["memory"], {"state": "conflicts", "files": ["memory.md"]})

    def test_a_clean_memory_sync_does_not_block(self) -> None:
        contract = contract_for(Path("/tmp"))
        self.assertIsNone(
            sync_module._memory_sync_block(
                contract, {"state": "fast-forwarded"}, {"state": "merged"}, {}
            )
        )


class MoveMemoryBranchTests(unittest.TestCase):
    """A merge that cannot be resolved automatically leaves the worktree usable."""

    def test_a_conflicting_merge_is_aborted_and_reported_with_its_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            init_repo(repo, "main")
            commit(repo, "memory.md", "base\n", "base")
            run_git(repo, "checkout", "-b", "ar/edge-task")
            commit(repo, "memory.md", "task line\n", "task edit")
            run_git(repo, "checkout", "main")
            commit(repo, "memory.md", "official line\n", "official edit")
            run_git(repo, "checkout", "ar/edge-task")

            state = sync_module._move_memory_branch(
                repo, "main", move="merge", tip=run_git(repo, "rev-parse", "main")
            )

            self.assertEqual(state["state"], "conflicts")
            self.assertEqual(state["files"], ["memory.md"])
            self.assertEqual(run_git(repo, "rev-parse", "--abbrev-ref", "HEAD"), "ar/edge-task")
            self.assertEqual((repo / "memory.md").read_text(encoding="utf-8"), "task line\n")


class FetchSourceUpstreamsTests(unittest.TestCase):
    """The best-effort pre-sync fetch reports per side rather than failing the sync."""

    def test_a_branch_with_an_upstream_is_fetched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origin = root / "origin.git"
            seed = root / "seed"
            seed.mkdir()
            init_repo(seed, "main")
            run_git(root, "init", "--bare", str(origin))
            run_git(seed, "remote", "add", "origin", str(origin))
            run_git(seed, "push", "-u", "origin", "main")
            run_git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
            clone = root / "repo-a"
            run_git(root, "clone", str(origin), str(clone))

            contract = contract_for(root, code_repo=clone)
            results = sync_module._fetch_source_upstreams(contract)

            self.assertEqual(results, {"code": {"state": "fetched"}})

    def test_a_branch_without_an_upstream_reports_no_upstream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo-a"
            repo.mkdir()
            init_repo(repo, "main")

            results = sync_module._fetch_source_upstreams(contract_for(root, code_repo=repo))

            self.assertEqual(results, {"code": {"state": "no-upstream"}})


class OverviewRevisionTests(unittest.TestCase):
    """Which route overviews the closeout classifier can speak for at all."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.memory_root = self.root / "memory"
        self.memory_root.mkdir(parents=True)
        init_repo(self.memory_root, "main")
        self.baseline = commit(
            self.memory_root, "onboarding/overview.md", "# Overview\n", "seed overview"
        )

    def test_an_overview_outside_the_memory_tree_is_not_classified(self) -> None:
        """Only files under the memory root have a baseline revision to compare against;
        an overview elsewhere is unrelated to the memory tree, not a stale one."""
        outside = self.root / "elsewhere" / "overview.md"
        outside.parent.mkdir(parents=True)
        outside.write_text("# Overview\n", encoding="utf-8")

        revision = onboarding_module._overview_revision(
            outside,
            memory_root=self.memory_root,
            baseline_ref=self.baseline,
            changed_memory=set(),
        )

        self.assertIsNone(revision)

    def test_an_overview_absent_from_the_baseline_is_not_classified(self) -> None:
        """A newly written overview has nothing to have drifted from -- reporting it stale
        would demand history lines for a file whose first version this is."""
        new_overview = self.memory_root / "onboarding" / "new-route" / "overview.md"
        new_overview.parent.mkdir(parents=True)
        new_overview.write_text("# New route\n", encoding="utf-8")

        revision = onboarding_module._overview_revision(
            new_overview,
            memory_root=self.memory_root,
            baseline_ref=self.baseline,
            changed_memory={"onboarding/new-route/overview.md"},
        )

        self.assertIsNone(revision)

    def test_an_overview_present_in_the_baseline_reports_its_body_and_history(self) -> None:
        overview = self.memory_root / "onboarding" / "overview.md"
        overview.write_text("# Overview\n\nNew prose about the route.\n", encoding="utf-8")

        revision = onboarding_module._overview_revision(
            overview,
            memory_root=self.memory_root,
            baseline_ref=self.baseline,
            changed_memory={"onboarding/overview.md"},
        )

        assert revision is not None
        body_changed, added_history = revision
        self.assertIs(body_changed, True)
        self.assertEqual(added_history, [])

    def test_an_unclassifiable_overview_falls_out_of_the_bucket_assignment(self) -> None:
        """``_route_overview_bucket`` must drop what ``_overview_revision`` cannot speak
        for, rather than defaulting it into a gating bucket."""
        outside = self.root / "elsewhere" / "overview.md"
        outside.parent.mkdir(parents=True)
        outside.write_text("# Overview\n", encoding="utf-8")

        bucket = onboarding_module._route_overview_bucket(
            outside,
            memory_root=self.memory_root,
            baseline_ref=self.baseline,
            changed_memory=set(),
            domain_evident=True,
        )

        self.assertIsNone(bucket)


class IntegrationRefusalTests(unittest.TestCase):
    """Integration refuses before it moves any branch."""

    def test_ff_only_refuses_once_the_source_branch_has_moved_past_the_closeout(self) -> None:
        """A source branch that advanced past the landed commit cannot be fast-forwarded
        onto it; the block names replay as the way through and leaves main where it is."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = closed_external_contract_fixture(root)
            main_before = git(contract.code_repo_path, "rev-parse", "main")
            commit(contract.code_repo_path, "parallel.txt", "parallel\n", "parallel work")
            moved_main = git(contract.code_repo_path, "rev-parse", "main")

            result = integrate_module.integrate_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    approved=True,
                    strategy="ff-only",
                    dry_run=False,
                )
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.payload["state"], "blocked-non-ff")
            self.assertIn("--strategy replay", str(result.payload["summary"]))
            self.assertNotEqual(moved_main, main_before)
            self.assertEqual(git(contract.code_repo_path, "rev-parse", "main"), moved_main)

    def test_a_non_fast_forward_memory_ledger_aborts_before_the_code_branch_moves(
        self,
    ) -> None:
        """Both fast-forwards are validated first so a memory-side problem cannot leave the
        code branch advanced and memory behind. Official memory moving in parallel is what
        makes the memory fast-forward impossible while the code one is still fine."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = closed_external_contract_fixture(root)
            assert contract.memory_repo_path is not None
            code_main_before = git(contract.code_repo_path, "rev-parse", "main")
            commit(
                contract.memory_repo_path, "unrelated.md", "unrelated\n", "parallel official memory"
            )
            memory_main_before = git(contract.memory_repo_path, "rev-parse", "main")

            with self.assertRaises(RuntimeError) as raised:
                integrate_module._merge_integrated_commits(
                    contract,
                    integrate_module.IntegratedCommits(
                        code=contract.code_commit,
                        memory_content=contract.memory_content_commit,
                        ledger=contract.ledger_commit,
                    ),
                )

            self.assertEqual(
                str(raised.exception),
                "integrated memory ledger commit is not a fast-forward from the current "
                "memory branch",
            )
            self.assertEqual(git(contract.code_repo_path, "rev-parse", "main"), code_main_before)
            self.assertEqual(
                git(contract.memory_repo_path, "rev-parse", "main"), memory_main_before
            )


if __name__ == "__main__":
    unittest.main()
