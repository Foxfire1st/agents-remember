from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agents_remember.controlplane.attention_dismissals import (
    AttentionDismissalRecord,
    AttentionDismissalStore,
)
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    ProviderScope,
)
from agents_remember.observer.projection import WorkspaceProjection
from agents_remember.observer.reducer import WorkspaceStructure, project_workspace
from agents_remember.observer.store import EventStore
from agents_remember.providers.current_state import current_state_path
from agents_remember.serving.projections.paths import observer_root
from agents_remember.serving.projections.projection_store import (
    project_and_write,
    read_lifecycle_logs,
    write_projection,
)
from agents_remember.serving.projections.snapshots import (
    _inspect_result_map,
    read_enclosures,
    read_providers,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    write_contract,
)
from test_observer_projection import FRESH, STALE, T0, _event, _started


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

    def test_inspect_result_map_parses_container_names(self) -> None:
        mapped = _inspect_result_map(
            json.dumps(
                [
                    {"Name": "/cgc-db", "State": {"Running": True}},
                    {"Name": "grepai-watch", "State": {"Running": False}},
                    {"Name": "", "State": {"Running": True}},
                    ["not-a-container"],
                ]
            )
        )

        self.assertEqual(set(mapped), {"cgc-db", "grepai-watch"})
        self.assertTrue(mapped["cgc-db"]["State"]["Running"])
        self.assertFalse(mapped["grepai-watch"]["State"]["Running"])

    def test_inspect_result_map_ignores_unusable_payloads(self) -> None:
        self.assertEqual(_inspect_result_map(None), {})
        self.assertEqual(_inspect_result_map(""), {})
        self.assertEqual(_inspect_result_map("{"), {})
        self.assertEqual(_inspect_result_map('{"Name": "/single"}'), {})

    def test_read_providers_parses_snapshot_with_age(self) -> None:
        config = self._config()
        path = current_state_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "checkedAt": T0,
                    "providers": {
                        "codegraphcontext-code": {
                            "id": "codegraphcontext-code",
                            "state": "ready",
                            "ok": True,
                            "watcherUp": True,
                            "indexingState": "indexed",
                        },
                        "grepai-memory": {
                            "id": "grepai-memory",
                            "state": "stopped",
                            "ok": False,
                            "watcherUp": False,
                            "indexingState": "unknown",
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        nodes = {node.id: node for node in read_providers(config, now=STALE)}
        self.assertEqual(set(nodes), {"codegraphcontext-code", "grepai-memory"})
        self.assertEqual(nodes["codegraphcontext-code"].state, "ready")
        self.assertEqual(nodes["codegraphcontext-code"].snapshotStaleSeconds, 600.0)

    def test_read_providers_projects_cgc_repo_watchers(self) -> None:
        config = self._config()
        path = current_state_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "checkedAt": T0,
                    "providers": {
                        "codegraphcontext-code": {
                            "id": "codegraphcontext-code",
                            "state": "degraded",
                            "ok": False,
                            "indexingState": "mixed",
                            "resources": {
                                "watchers": {
                                    "repo-b": {
                                        "state": "degraded",
                                        "ok": False,
                                        "repoId": "repo-b",
                                        "watcherUp": True,
                                        "indexingState": "empty",
                                    },
                                    "agents-remember": {
                                        "state": "ready",
                                        "ok": True,
                                        "repoId": "agents-remember",
                                        "watcherUp": True,
                                        "indexingState": "indexed",
                                    },
                                }
                            },
                        },
                        "grepai-memory": {
                            "id": "grepai-memory",
                            "state": "ready",
                            "ok": True,
                            "watcherUp": True,
                            "indexingState": "indexed",
                            "resources": {
                                "watcher": {
                                    "state": "ready",
                                }
                            },
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

        nodes = {node.id: node for node in read_providers(config, now=STALE)}

        self.assertEqual(
            set(nodes),
            {
                "codegraphcontext-code:agents-remember",
                "codegraphcontext-code:repo-b",
                "grepai-memory",
            },
        )
        self.assertEqual(nodes["codegraphcontext-code:agents-remember"].repoId, "agents-remember")
        self.assertEqual(nodes["codegraphcontext-code:agents-remember"].scope, "workspace")
        self.assertEqual(nodes["codegraphcontext-code:agents-remember"].role, "code")
        self.assertEqual(nodes["codegraphcontext-code:agents-remember"].state, "ready")
        self.assertTrue(nodes["codegraphcontext-code:agents-remember"].ok)
        self.assertTrue(nodes["codegraphcontext-code:agents-remember"].watcherUp)
        self.assertEqual(nodes["codegraphcontext-code:repo-b"].state, "degraded")
        self.assertEqual(nodes["codegraphcontext-code:repo-b"].indexingState, "empty")
        self.assertIsNone(nodes["grepai-memory"].repoId)
        self.assertEqual(nodes["grepai-memory"].role, "memory")

    def test_read_providers_projects_grepai_target_repos(self) -> None:
        config = self._config()
        path = current_state_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "checkedAt": T0,
                    "providers": {
                        "grepai-memory": {
                            "id": "grepai-memory",
                            "state": "ready",
                            "ok": True,
                            "watcherUp": True,
                            "indexingState": "indexed",
                            "targetRepos": [
                                {
                                    "repoId": "agents-remember",
                                    "path": "/memory/agents-remember",
                                },
                                {
                                    "repoId": "repo-b",
                                    "path": "/memory/repo-b",
                                },
                            ],
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

        nodes = {node.id: node for node in read_providers(config, now=STALE)}

        self.assertEqual(set(nodes), {"grepai-memory:agents-remember", "grepai-memory:repo-b"})
        self.assertEqual(nodes["grepai-memory:agents-remember"].repoId, "agents-remember")
        self.assertEqual(nodes["grepai-memory:agents-remember"].scope, "workspace")
        self.assertEqual(nodes["grepai-memory:agents-remember"].role, "memory")
        self.assertEqual(nodes["grepai-memory:agents-remember"].state, "ready")
        self.assertTrue(nodes["grepai-memory:agents-remember"].ok)
        self.assertTrue(nodes["grepai-memory:agents-remember"].watcherUp)
        self.assertEqual(nodes["grepai-memory:repo-b"].repoId, "repo-b")
        self.assertEqual(nodes["grepai-memory:repo-b"].indexingState, "indexed")

    def test_read_providers_absent_is_empty(self) -> None:
        self.assertEqual(read_providers(self._config(), now=FRESH), [])

    def test_read_providers_includes_per_worktree_stacks(self) -> None:
        # Surfaces 1 + 4: the workspace snapshot plus each worktree's isolated CGC+GrepAI stack,
        # bound to its worktree group + repo + role.
        config = self._config()
        coord = config.coordination_root
        workspace = current_state_path(config)
        workspace.parent.mkdir(parents=True, exist_ok=True)
        workspace.write_text(
            json.dumps(
                {
                    "checkedAt": T0,
                    "providers": {
                        "codegraphcontext-code": {
                            "id": "codegraphcontext-code",
                            "state": "ready",
                            "ok": True,
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        runtime = coord / "worktrees" / "device-management" / "260612-x-ar" / "provider-runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "provider-state.json").write_text(
            json.dumps(
                {
                    "schema": "ar-worktree-provider-state/v1",
                    "repoName": "device-management",
                    "worktreeGroup": str(runtime.parent),
                    "isolatedProviderSettings": {
                        "providers": ["codegraphcontext-code", "grepai-memory"]
                    },
                }
            ),
            encoding="utf-8",
        )
        # a malformed stack is skipped, never fatal
        other = coord / "worktrees" / "device-management" / "broken-ar" / "provider-runtime"
        other.mkdir(parents=True, exist_ok=True)
        (other / "provider-state.json").write_text(json.dumps({"schema": "nope"}), encoding="utf-8")

        nodes = {node.id: node for node in read_providers(config, now=FRESH)}
        self.assertEqual(
            set(nodes),
            {
                "codegraphcontext-code",
                "codegraphcontext-code@260612-x-ar",
                "grepai-memory@260612-x-ar",
            },
        )
        self.assertEqual(nodes["codegraphcontext-code"].scope, "workspace")
        code = nodes["codegraphcontext-code@260612-x-ar"]
        self.assertEqual(
            (code.scope, code.role, code.repoId, code.worktreeGroup),
            ("worktree", "code", "device-management", "260612-x-ar"),
        )
        self.assertIsNone(code.ok)
        self.assertEqual(code.state, "configured")
        self.assertEqual(nodes["grepai-memory@260612-x-ar"].role, "memory")

    def test_read_providers_ignores_unadmitted_worktree_stacks(self) -> None:
        config = self._config()
        runtime = (
            config.coordination_root
            / "worktrees"
            / "device-management"
            / "parked-ar"
            / "provider-runtime"
        )
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "provider-state.json").write_text(
            json.dumps(
                {
                    "schema": "ar-worktree-provider-state/v1",
                    "repoName": "device-management",
                    "worktreeGroup": str(runtime.parent),
                    "isolatedProviderSettings": {
                        "providers": ["codegraphcontext-code", "grepai-memory"]
                    },
                }
            ),
            encoding="utf-8",
        )

        nodes = read_providers(config, now=FRESH, active_worktree_groups=set())

        self.assertEqual(nodes, [])

    def test_read_providers_marks_worktree_stack_ready_from_live_containers(self) -> None:
        config = self._config()
        coord = config.coordination_root
        runtime = coord / "worktrees" / "device-management" / "260612-x-ar" / "provider-runtime"
        settings_path = runtime / "settings" / "provider-settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(
                {
                    "contextProviders": {
                        "providers": {
                            "codegraphcontext-code": {
                                "roots": [{"repoId": "device-management"}],
                                "runtime": {
                                    "runner": {
                                        "containerNameTemplate": "cgc-<repoId>",
                                    }
                                },
                                "backend": {"containerName": "cgc-db"},
                            },
                            "grepai-memory": {
                                "runtime": {"runner": {"containerName": "grepai-watch"}},
                                "backend": {"containerName": "grepai-db"},
                                "embedder": {"backend": {"containerName": "grepai-ollama"}},
                            },
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        (runtime / "provider-state.json").write_text(
            json.dumps(
                {
                    "schema": "ar-worktree-provider-state/v1",
                    "repoName": "device-management",
                    "worktreeGroup": str(runtime.parent),
                    "isolatedProviderSettings": {
                        "path": settings_path.as_posix(),
                        "providers": ["codegraphcontext-code", "grepai-memory"],
                    },
                }
            ),
            encoding="utf-8",
        )

        def inspected(name: str) -> dict[str, object]:
            return {
                "Name": f"/{name}",
                "State": {
                    "Running": True,
                    "Status": "running",
                    "StartedAt": "2026-06-27T12:00:00Z",
                },
            }

        names = {"cgc-device-management", "cgc-db", "grepai-watch", "grepai-db", "grepai-ollama"}
        with mock.patch(
            "agents_remember.serving.projections.snapshots._inspect_containers",
            return_value={name: inspected(name) for name in names},
        ) as inspect:
            nodes = {
                node.id: node
                for node in read_providers(
                    config, now=FRESH, active_worktree_groups={"260612-x-ar"}
                )
            }

        self.assertEqual(inspect.call_args.args[0], names)
        code = nodes["codegraphcontext-code@260612-x-ar"]
        memory = nodes["grepai-memory@260612-x-ar"]
        self.assertEqual((code.state, code.ok, code.watcherUp), ("ready", True, True))
        self.assertEqual((memory.state, memory.ok, memory.watcherUp), ("ready", True, True))

    def test_read_enclosures_from_contract(self) -> None:
        coord = (self.tmp / "coord").resolve()
        contract = default_contract(
            ContractTask(
                name="Observe Lifecycle",
                repo_name="repo-a",
                coordination_root=coord,
                workflow_kind="light-task",
                memory_mode="disabled",
            ),
            leaf=LeafIdentity(worktree_name="observe", lifecycle_id="LC-1"),
            code=RepoBranchPlan(
                repo_path=coord / "repo-a",
                source_branch="main",
                work_branch="ar/observe",
                base_commit="0" * 40,
            ),
        )
        contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
        write_contract(contract.contract_path, contract)
        nodes = read_enclosures(coord)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].repoName, "repo-a")
        self.assertEqual(nodes[0].lifecycleId, "LC-1")

    def test_read_enclosures_absent_is_empty(self) -> None:
        self.assertEqual(read_enclosures((self.tmp / "nope").resolve()), [])

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
        write_contract(contract.contract_path, contract)
        return contract

    def test_read_enclosures_stat_worktree_existence(self) -> None:
        """codeWorktreeExists/memoryWorktreeExists are stat'ed truth at snapshot time.

        Matches how the worktree tools report existence (``status_payload``'s
        ``code_worktree.exists()``): the flags flip as the directories appear on disk,
        with no contract rewrite involved.
        """
        coord = (self.tmp / "coord").resolve()
        contract = self._existence_contract(coord)

        [before] = read_enclosures(coord)
        self.assertEqual((before.codeWorktreeExists, before.memoryWorktreeExists), (False, False))

        contract.code_worktree.mkdir(parents=True)
        [code_only] = read_enclosures(coord)
        self.assertEqual(
            (code_only.codeWorktreeExists, code_only.memoryWorktreeExists), (True, False)
        )

        assert contract.memory_worktree is not None
        contract.memory_worktree.mkdir(parents=True)
        [both] = read_enclosures(coord)
        self.assertEqual((both.codeWorktreeExists, both.memoryWorktreeExists), (True, True))

    def test_read_enclosures_reopened_is_reset_awaiting_restart_not_archived(self) -> None:
        """``cleanup=reopened`` means contract-reset-awaiting-restart, not live work.

        The reopened contract must still project (it is NOT archived like
        completed/abandoned — the leaf is coming back), but with existence False so the
        tasks surface hides it until ``worktree_start`` physically recreates the
        worktrees — at which point the same contract reads visible again.
        """
        coord = (self.tmp / "coord").resolve()
        contract = self._existence_contract(coord)
        write_contract(
            contract.contract_path, replace(contract, cleanup="reopened", lifecycle_id="")
        )

        [reopened] = read_enclosures(coord)
        self.assertEqual(reopened.cleanup, "reopened")
        self.assertEqual(
            (reopened.codeWorktreeExists, reopened.memoryWorktreeExists), (False, False)
        )

        # worktree_start recreates the directories: existence truth flips back with no
        # further contract interpretation needed.
        contract.code_worktree.mkdir(parents=True)
        [restarted] = read_enclosures(coord)
        self.assertTrue(restarted.codeWorktreeExists)

    def test_project_and_write_end_to_end(self) -> None:
        config = self._config()
        store = EventStore(observer_root(config))
        store.append(_started(lifecycle_id="LC1", ts=T0))
        proj = project_and_write(config, now=FRESH)
        self.assertEqual(proj.metrics.lifecycleCount, 1)
        self.assertTrue((observer_root(config) / "latest-state.json").exists())

    def test_project_and_write_prunes_completed_lifecycle_attention_acknowledgement(self) -> None:
        config = self._config()
        root = observer_root(config)
        store = EventStore(root)
        store.append(_started(lifecycle_id="LC1", ts=T0))
        store.append(
            _event(
                "lifecycle.ended",
                lifecycle_id="LC1",
                ts="2026-06-13T18:00:05+00:00",
                outcome="completed",
            )
        )
        dismissals = AttentionDismissalStore(root)
        dismissals.dismiss(
            AttentionDismissalRecord(
                itemId="awaiting-developer:LC1",
                kind="awaiting-developer",
                lifecycleId="LC1",
                dismissedAt="2026-06-13T18:00:04+00:00",
            )
        )

        project_and_write(config, now=FRESH)

        self.assertEqual(dismissals.current(), {})
        # The projection pass pruned the acknowledgement for the completed lifecycle, and the
        # log it lived in is now an EMPTY FILE rather than a deleted one (260731-EFA-L5 R5):
        # rewriting to empty by unlinking stranded a concurrent dismisser's "a"-mode handle in
        # an inode with no name, which is how 31.45% of dismissals were being lost. Same proof,
        # different mechanism -- the row that was written above is gone from the reader and the
        # file holds nothing, which is what "pruned" has to mean now.
        self.assertEqual(dismissals.read(), [])
        self.assertTrue(dismissals.log_path().is_file())
        self.assertEqual(dismissals.log_path().read_bytes(), b"")


class StoreIOTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)

    def test_read_lifecycle_logs_enumerates(self) -> None:
        store = EventStore(self.root)
        store.append(_started(lifecycle_id="LCa", ts=T0))
        store.append(_started(lifecycle_id="LCb", ts=T0))
        logs = read_lifecycle_logs(self.root)
        self.assertEqual(len(logs), 2)

    def test_read_lifecycle_logs_absent_is_empty(self) -> None:
        self.assertEqual(read_lifecycle_logs(self.root), [])

    def test_write_projection_round_trips_atomically(self) -> None:
        proj = project_workspace(
            [[_started()]], structure=WorkspaceStructure(enclosures=[], providers=[]), now=FRESH
        )
        write_projection(self.root, proj)
        state = json.loads((self.root / "latest-state.json").read_text(encoding="utf-8"))
        WorkspaceProjection.model_validate(state)
        metrics = json.loads((self.root / "latest-metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(metrics["lifecycleCount"], 1)
        self.assertEqual(list(self.root.glob("*.tmp")), [])  # no torn temp left behind
