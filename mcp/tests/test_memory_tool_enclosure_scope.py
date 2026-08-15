"""L6-R29: which memory tree the three memory tools act on, proved by acting on it.

``memory_quality_check``, ``drift_check`` and ``route_index_refresh`` took ``repo_id`` and
nothing else, so every call resolved the configured OFFICIAL memory repo. ``route_index_refresh``
WRITES, so a call from inside a leaf generated indexes into a repository the session did not own
while the leaf's own memory worktree stayed stale -- and a dirty official memory repo is exactly
what ``worktree_start`` refuses to open the next task on top of.

This is the R16 half, by filesystem rather than by inspection. Each fixture below stands up a
real official memory repo AND a real leaf enclosure whose two worktrees hold content the other
tree does not, so "it read the leaf" and "it read the official repo" are distinguishable facts
rather than two paths that happen to be spelled differently:

* the official onboarding tree holds one route overview;
* the leaf memory worktree holds that overview plus a second route and a file sidecar;
* the leaf CODE worktree holds a source file that exists on no other branch, which is what
  separates "measured against the leaf's code" from "measured against the repository".
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import patch

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application.memory_tools import memory_quality_check_tool
from agents_remember.errors import AuthorityError
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    load_config,
)
from agents_remember.mcp.tools import (
    drift_check_payload,
    memory_quality_check_payload,
    route_index_refresh_payload,
)
from agents_remember.memory_quality.check import DRIFT_CHECK_NAME
from agents_remember.memory_quality.curator_checklist import split_commit_owned_findings
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    default_contract,
    write_contract,
)
from test_config import settings_payload
from test_worktree_support import (
    git,
    init_repo,
    initialized_memory_repo,
    write_file_onboarding,
    write_route_overview,
)

REPO = "agents-remember"
LEAF_ONLY_SOURCE = "leaf_only.py"
LEAF_ONLY_ROUTE = "pkg"


@dataclass(frozen=True)
class _Enclosure:
    """One workspace holding both trees a memory tool could resolve, and the contract."""

    config: McpRuntimeConfig
    contract: WorktreeContract
    official_onboarding: Path
    leaf_onboarding: Path
    leaf_source_commit: str


def _tree_snapshot(root: Path) -> dict[str, str]:
    """Every file under ``root`` by relative posix path and content -- the untouched proof."""
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _enclosure(root: Path) -> _Enclosure:
    coordination_root = root / "ar-coordination"
    workspace_root = root / "workspace"
    code_repo = workspace_root / REPO
    code_base = init_repo(code_repo, "main")
    memory_repo = coordination_root / "memory-repos" / f"ar-{REPO}"
    memory_base = initialized_memory_repo(memory_repo, REPO, "main", "main", code_base)

    contract = default_contract(
        ContractTask(
            name="Leaf Scope",
            repo_name=REPO,
            coordination_root=coordination_root,
            workflow_kind="light-task",
            memory_mode="external",
        ),
        leaf=LeafIdentity(worktree_name="leaf-scope"),
        code=RepoBranchPlan(
            repo_path=code_repo,
            source_branch="main",
            work_branch="ar/leaf-scope",
            base_commit=code_base,
        ),
        memory=RepoBranchPlan(
            repo_path=memory_repo,
            source_branch="main",
            work_branch="ar/leaf-scope",
            base_commit=memory_base,
        ),
    )
    assert contract.memory_worktree is not None
    git(code_repo, "worktree", "add", "-b", "ar/leaf-scope", str(contract.code_worktree), "main")
    git(
        memory_repo,
        "worktree",
        "add",
        "-b",
        "ar/leaf-scope",
        str(contract.memory_worktree),
        "main",
    )
    write_contract(contract.contract_path, contract)

    # The leaf's own code: a file and a whole route, committed on the leaf branch only, so a
    # check measured against the repository rather than this worktree cannot see either.
    (contract.code_worktree / LEAF_ONLY_SOURCE).write_text("VALUE = 1\n", encoding="utf-8")
    (contract.code_worktree / LEAF_ONLY_ROUTE).mkdir()
    (contract.code_worktree / LEAF_ONLY_ROUTE / "mod.py").write_text("X = 2\n", encoding="utf-8")
    git(contract.code_worktree, "add", "-A")
    git(contract.code_worktree, "commit", "-m", "leaf source")
    leaf_source_commit = git(contract.code_worktree, "rev-parse", "HEAD")

    official_onboarding = memory_repo / "onboarding"
    official_onboarding.mkdir(parents=True, exist_ok=True)
    write_route_overview(official_onboarding, REPO, ".", code_base)

    leaf_onboarding = contract.memory_worktree / "onboarding"
    leaf_onboarding.mkdir(parents=True, exist_ok=True)
    write_route_overview(leaf_onboarding, REPO, ".", leaf_source_commit)
    write_route_overview(leaf_onboarding, REPO, LEAF_ONLY_ROUTE, leaf_source_commit)
    write_file_onboarding(leaf_onboarding, REPO, LEAF_ONLY_SOURCE, leaf_source_commit)

    settings_path = root / "mcp-settings.json"
    settings_path.write_text(json.dumps(settings_payload(root), indent=2), encoding="utf-8")
    return _Enclosure(
        config=load_config(settings_path),
        contract=contract,
        official_onboarding=official_onboarding,
        leaf_onboarding=leaf_onboarding,
        leaf_source_commit=leaf_source_commit,
    )


class EnclosureScopeTestCase(unittest.TestCase):
    """One temporary workspace per test, holding both trees."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.enclosure = _enclosure(Path(self._tmp.name))
        self.contract_path = self.enclosure.contract.contract_path.as_posix()

    @property
    def config(self) -> McpRuntimeConfig:
        return self.enclosure.config


class RouteIndexRefreshWritesTheNamedTreeTests(EnclosureScopeTestCase):
    """The urgent one: it writes, so where it writes is the whole question."""

    def test_a_contract_scoped_refresh_writes_the_leaf_and_leaves_the_official_repo_alone(
        self,
    ) -> None:
        before = _tree_snapshot(self.enclosure.official_onboarding)

        payload = route_index_refresh_payload(self.config, REPO, contract_path=self.contract_path)

        self.assertEqual(payload["onboardingRoot"], self.enclosure.leaf_onboarding.as_posix())
        self.assertEqual(payload["routes"], 2)
        self.assertEqual(payload["written"], 2)
        self.assertTrue((self.enclosure.leaf_onboarding / "overview.index.json").is_file())
        self.assertTrue((self.enclosure.leaf_onboarding / "pkg" / "overview.index.json").is_file())
        self.assertEqual(_tree_snapshot(self.enclosure.official_onboarding), before)

    def test_the_bare_call_still_writes_the_official_repo_and_leaves_the_leaf_alone(self) -> None:
        before = _tree_snapshot(self.enclosure.leaf_onboarding)

        payload = route_index_refresh_payload(self.config, REPO)

        self.assertEqual(payload["onboardingRoot"], self.enclosure.official_onboarding.as_posix())
        self.assertEqual(payload["routes"], 1)
        self.assertTrue((self.enclosure.official_onboarding / "overview.index.json").is_file())
        self.assertEqual(_tree_snapshot(self.enclosure.leaf_onboarding), before)

    def test_a_contract_scoped_dry_run_writes_nothing_to_either_tree(self) -> None:
        official = _tree_snapshot(self.enclosure.official_onboarding)
        leaf = _tree_snapshot(self.enclosure.leaf_onboarding)

        payload = route_index_refresh_payload(
            self.config, REPO, dry_run=True, contract_path=self.contract_path
        )

        self.assertTrue(payload["dryRun"])
        self.assertEqual(payload["written"], 2)
        self.assertEqual(len(payload["staleIndexes"]), 2)
        self.assertEqual(_tree_snapshot(self.enclosure.official_onboarding), official)
        self.assertEqual(_tree_snapshot(self.enclosure.leaf_onboarding), leaf)


class MemoryQualityCheckReadsTheNamedTreeTests(EnclosureScopeTestCase):
    """The closeout gate, now runnable on the change-set before the manager runs it.

    Scoped to the drift-integrity check by name: which TREE was read is the question here,
    and the style checks would make these counts a function of every check in the suite.
    """

    def test_a_contract_scoped_check_reads_the_leaf_tree(self) -> None:
        payload = memory_quality_check_payload(
            self.config, REPO, checks=[DRIFT_CHECK_NAME], contract_path=self.contract_path
        )

        self.assertEqual(payload["onboardingRoot"], self.enclosure.leaf_onboarding.as_posix())
        self.assertEqual(payload["checks"][DRIFT_CHECK_NAME]["checkedCount"], 3)
        self.assertTrue(payload["ok"])

    def test_full_contract_check_replaces_one_enclosure_local_curator_report(self) -> None:
        expected = self.enclosure.contract.worktree_group / "reports" / "curator-memory-quality.md"

        first = memory_quality_check_payload(self.config, REPO, contract_path=self.contract_path)
        expected.write_text("obsolete predecessor\n", encoding="utf-8")
        second = memory_quality_check_payload(self.config, REPO, contract_path=self.contract_path)

        self.assertEqual(first["reportPath"], expected.as_posix())
        self.assertEqual(second["reportPath"], expected.as_posix())
        report = expected.read_text(encoding="utf-8")
        self.assertNotEqual(report, "obsolete predecessor\n")
        self.assertIn("# Curator Memory Quality Checklist", report)
        self.assertEqual(
            {path.name for path in expected.parent.iterdir()},
            {expected.name, expected.with_suffix(".json").name},
        )
        self.assertIn(second["checklistStatus"], {"action-required", "ready-for-closeout"})
        self.assertEqual(
            second["curatorActionableCount"],
            second["memoryRepairCount"]
            + second["missingOnboardingCount"]
            + second["staleRouteIndexCount"],
        )

    def test_subset_contract_check_does_not_replace_the_curator_report(self) -> None:
        report = self.enclosure.contract.worktree_group / "reports" / "curator-memory-quality.md"
        report.parent.mkdir(parents=True)
        report.write_text("keep me\n", encoding="utf-8")

        payload = memory_quality_check_payload(
            self.config,
            REPO,
            checks=[DRIFT_CHECK_NAME],
            contract_path=self.contract_path,
        )

        self.assertNotIn("reportPath", payload)
        self.assertEqual(report.read_text(encoding="utf-8"), "keep me\n")

    def test_only_untracked_card_provenance_is_deferred_to_closeout(self) -> None:
        tracked_path = f"{LEAF_ONLY_SOURCE}.md"
        new_path = "new_source.py.md"
        assert self.enclosure.contract.memory_worktree is not None
        git(self.enclosure.contract.memory_worktree, "add", "onboarding")
        git(self.enclosure.contract.memory_worktree, "commit", "-m", "track current onboarding")
        (self.enclosure.leaf_onboarding / new_path).write_text("# New source\n", encoding="utf-8")
        findings = [
            {"code": "citation_provenance_missing", "path": tracked_path},
            {"code": "citation_provenance_missing", "path": new_path},
        ]

        repairable, closeout_owned = split_commit_owned_findings(
            findings, self.enclosure.leaf_onboarding
        )

        self.assertEqual(repairable, [findings[0]])
        self.assertEqual(closeout_owned, [findings[1]])

    def test_the_bare_call_still_reads_the_official_repo(self) -> None:
        payload = memory_quality_check_payload(self.config, REPO, checks=[DRIFT_CHECK_NAME])

        self.assertEqual(payload["onboardingRoot"], self.enclosure.official_onboarding.as_posix())
        self.assertEqual(payload["checks"][DRIFT_CHECK_NAME]["checkedCount"], 1)

    def test_a_contract_scoped_check_uses_the_leaf_base_for_unstamped_claims(self) -> None:
        with patch(
            "agents_remember.application.memory_tools.run_memory_quality_check",
            return_value={"ok": True, "findingCount": 0, "findings": []},
        ) as run_check:
            memory_quality_check_tool(
                self.config,
                repo_id=REPO,
                contract_path=self.contract_path,
            )

        drift_context = run_check.call_args.kwargs["drift_context"]
        self.assertEqual(
            drift_context.unstamped_code_commit,
            self.enclosure.contract.code_base_commit,
        )

    def test_the_bare_check_does_not_invent_unstamped_claim_provenance(self) -> None:
        with patch(
            "agents_remember.application.memory_tools.run_memory_quality_check",
            return_value={"ok": True, "findingCount": 0, "findings": []},
        ) as run_check:
            memory_quality_check_tool(self.config, repo_id=REPO)

        drift_context = run_check.call_args.kwargs["drift_context"]
        self.assertIsNone(drift_context.unstamped_code_commit)


class DriftCheckReadsTheNamedTreesTests(EnclosureScopeTestCase):
    """Drift needs BOTH roots right: the leaf's onboarding and the leaf's code."""

    def test_a_contract_scoped_check_measures_leaf_onboarding_against_leaf_code(self) -> None:
        payload = drift_check_payload(self.config, REPO, contract_path=self.contract_path)

        self.assertEqual(payload["onboardingRoot"], self.enclosure.leaf_onboarding.as_posix())
        self.assertEqual(payload["count"], 3)
        # Zero is the load-bearing number, and it is evidence about the CODE root rather than
        # the memory root: the leaf-only file and the leaf-only route exist on no other
        # branch, so measured against the repository's own checkout both rows would come back
        # `orphaned` and actionable. Nothing here is clean by accident.
        self.assertEqual(payload["actionableCount"], 0)

    def test_the_bare_call_still_measures_the_official_repo(self) -> None:
        payload = drift_check_payload(self.config, REPO)

        self.assertEqual(payload["onboardingRoot"], self.enclosure.official_onboarding.as_posix())
        self.assertEqual(payload["count"], 1)


class RefusalTests(EnclosureScopeTestCase):
    """Every way the enclosure cannot be honoured refuses. None of them falls back.

    A fallback is the defect: an agent that asked for its leaf and silently got the official
    memory repo is how ``route_index_refresh`` dirtied a repository nobody was working in.
    """

    def test_a_contract_for_another_repo_is_refused_rather_than_resolved(self) -> None:
        foreign = self.enclosure.contract.contract_path.parent / "foreign-contract.md"
        write_contract(
            foreign,
            _replaced(self.enclosure.contract, repo_name="other-repo", contract_path=foreign),
        )

        with self.assertRaises(AuthorityError) as raised:
            route_index_refresh_payload(self.config, REPO, contract_path=foreign.as_posix())

        self.assertIn("names repo 'other-repo'", str(raised.exception))
        self.assertIn(f"repo_id is {REPO!r}", str(raised.exception))

    def test_a_contract_whose_memory_worktree_is_gone_is_refused(self) -> None:
        memory_worktree = self.enclosure.contract.memory_worktree
        assert memory_worktree is not None
        git(
            self.enclosure.contract.memory_repo_path or Path(),
            "worktree",
            "remove",
            "--force",
            str(memory_worktree),
        )

        with self.assertRaises(ValueError) as raised:
            memory_quality_check_payload(self.config, REPO, contract_path=self.contract_path)

        self.assertIn("has no onboarding tree at", str(raised.exception))
        self.assertNotIn("memory-repos", str(raised.exception))

    def test_an_internal_memory_contract_has_no_leaf_memory_tree_to_check(self) -> None:
        internal = self.enclosure.contract.contract_path.parent / "internal-contract.md"
        write_contract(
            internal,
            _replaced(
                self.enclosure.contract,
                memory_mode="internal",
                memory_worktree=None,
                ledger_path=None,
                contract_path=internal,
            ),
        )

        with self.assertRaises(ValueError) as raised:
            drift_check_payload(self.config, REPO, contract_path=internal.as_posix())

        self.assertIn("carries no memory worktree", str(raised.exception))

    def test_a_contract_path_outside_the_coordinator_root_is_refused(self) -> None:
        outside = (Path(self._tmp.name) / "elsewhere" / "series-contract.md").as_posix()

        with self.assertRaises(AuthorityError) as raised:
            route_index_refresh_payload(self.config, REPO, contract_path=outside)

        self.assertIn("contract_path must stay inside coordination_root", str(raised.exception))

    def test_a_repo_with_no_memory_root_still_reports_which_repo(self) -> None:
        config = _config_without_memory_root(self.config)

        with self.assertRaises(ValueError) as raised:
            drift_check_payload(config, REPO)

        self.assertEqual(str(raised.exception), f"repo_id {REPO!r} does not have a memory root")


def _replaced(contract: WorktreeContract, **overrides: object) -> WorktreeContract:
    """A copy of the contract with cells overridden, written to disk by the caller."""
    return replace(contract, **overrides)  # type: ignore[arg-type]


def _config_without_memory_root(config: McpRuntimeConfig) -> McpRuntimeConfig:
    """The settings shape whose repository scope carries no memory root at all."""
    scope = replace(config.repositories[REPO], memory_root=None)
    return replace(config, repositories={REPO: scope})


if __name__ == "__main__":
    unittest.main()
