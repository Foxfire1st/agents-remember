from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    ProviderScope,
)
from agents_remember.observer.projection import Analytics, SetupProgressNode, WorkspaceProjection
from agents_remember.observer.worktree_provider_admission import (
    active_enclosure_worktree_groups,
    admitted_worktree_groups,
)
from agents_remember.serving.delta import diff_projection
from agents_remember.serving.projections.paths import observer_root
from agents_remember.serving.projections.projection_store import (
    project_and_write,
)
from agents_remember.serving.projections.snapshots import read_enclosures
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    default_contract,
    write_contract,
)
from test_observer_projection import FRESH


class SnapshotReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.tmp = Path(self._dir.name)

    def _config(self) -> McpRuntimeConfig:
        coord = (self.tmp / "coord").resolve()
        coord.mkdir(parents=True, exist_ok=True)
        return McpRuntimeConfig(
            config_path=coord / "mcp.settings.json",
            coordination_root=coord,
            workspace_root=(self.tmp / "ws").resolve(),
            transcript_root=coord / "logs",
            providers={
                "codegraphcontext-code": ProviderScope(
                    provider_id="codegraphcontext-code",
                    runtime_root=coord / "rt",
                    log_root=coord / "lg",
                    instance_id="projects",
                    scope="workspace",
                )
            },
        )

    @staticmethod
    def _write_addressable_contract(contract: WorktreeContract) -> None:
        write_contract(contract.contract_path, contract)
        publish_new_lifecycle_operation_location(
            contract,
            contract_text=contract.contract_path.read_text(encoding="utf-8"),
        )

    def _existence_contract(self, coord: Path):  # -> WorktreeContract
        contract = default_contract(
            ContractTask(
                name="Observe Lifecycle",
                repo_name="repo-a",
                coordination_root=coord,
                workflow_kind="light-task",
                memory_mode="external",
            ),
            leaf=LeafIdentity(worktree_name="observe", lifecycle_id="LC-1"),
            code=RepoBranchPlan(
                repo_path=coord / "repo-a",
                source_branch="main",
                work_branch="ar/observe",
                base_commit="0" * 40,
            ),
            memory=RepoBranchPlan(
                repo_path=coord / "repo-a-memory",
                source_branch="main",
                work_branch="ar/observe-memory",
                base_commit="1" * 40,
            ),
        )
        contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_addressable_contract(contract)
        return contract

    def test_project_and_write_end_to_end(self) -> None:
        config = self._config()
        proj = project_and_write(config, now=FRESH)
        self.assertNotIn("lifecycles", proj.model_dump())
        self.assertTrue((observer_root(config) / "latest-state.json").exists())

    def test_reopened_enclosure_is_not_runtime_work_but_pending_is(self) -> None:
        config = self._config()
        contract = self._existence_contract(config.coordination_root)
        for cleanup, expected in (("pending", {contract.worktree_group.name}), ("reopened", set())):
            with self.subTest(cleanup=cleanup):
                write_contract(contract.contract_path, replace(contract, cleanup=cleanup))
                nodes = read_enclosures(config.coordination_root)
                self.assertEqual(admitted_worktree_groups(nodes), expected)
                self.assertEqual(active_enclosure_worktree_groups(nodes), expected)

    def test_setup_heartbeat_age_alone_emits_no_analytics_delta(self) -> None:
        def projection(age: float) -> WorkspaceProjection:
            return WorkspaceProjection(
                generatedAt=FRESH.isoformat(),
                analytics=Analytics(
                    setupProgress=[
                        SetupProgressNode(group="work", state="running", heartbeatAgeSeconds=age)
                    ]
                ),
            )

        self.assertEqual(diff_projection(projection(1), projection(12)), [])
        changed = projection(12)
        changed.analytics.setupProgress[0].state = "failed"
        self.assertEqual(
            [event.event for event in diff_projection(projection(1), changed)], ["analytics"]
        )
