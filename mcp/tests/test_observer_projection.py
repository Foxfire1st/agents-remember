"""Tests for the observer projection read side (slice 3a).

Covers the pure fold (``project_lifecycle``) and its determinism, the inferred
layer (stale -> paused, dormant fleeting -> abandoned, terminal preserved),
append-only corrections, precomputed action availability, the workspace tree
assembly + metrics, the atomic projection write, and the structural surface
readers (provider current-state + worktree enclosures).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.mcp.config import McpRuntimeConfig, ProviderScope
from agents_remember.observer.events import Event
from agents_remember.observer.paths import observer_root
from agents_remember.observer.projection import EnclosureNode, ProviderNode, WorkspaceProjection
from agents_remember.observer.projection_store import (
    project_and_write,
    read_lifecycle_logs,
    write_projection,
)
from agents_remember.observer.reducer import (
    enclosure_actions,
    project_lifecycle,
    project_workspace,
)
from agents_remember.observer.snapshots import read_enclosures, read_providers
from agents_remember.observer.store import EventStore
from agents_remember.observer.ulid import new_ulid
from agents_remember.providers.current_state import current_state_path
from agents_remember.worktrees.worktree_contract import default_contract, write_contract

T0 = "2026-06-13T18:00:00+00:00"
FRESH = datetime(2026, 6, 13, 18, 0, 30, tzinfo=UTC)  # 30s after T0  (< STALE)
STALE = datetime(2026, 6, 13, 18, 10, 0, tzinfo=UTC)  # 600s after T0 (> STALE, < TTL)
DORMANT = datetime(2026, 6, 13, 19, 30, 0, tzinfo=UTC)  # 5400s after T0 (> TTL)


def _event(
    kind: str,
    *,
    lifecycle_id: str = "LC1",
    ts: str = T0,
    trust: str = "declared",
    actor: str = "model",
    enclosure: str | None = None,
    repo_id: str | None = None,
    **data: object,
) -> Event:
    return Event(
        id=new_ulid(),
        ts=ts,
        kind=kind,
        trust=trust,  # type: ignore[arg-type]
        actor=actor,  # type: ignore[arg-type]
        lifecycleId=lifecycle_id,
        enclosure=enclosure,
        repoId=repo_id,
        data=dict(data),
    )


def _started(
    *, ts: str = T0, fleeting: bool = True, phase: str = "request", lifecycle_id: str = "LC1"
) -> Event:
    return _event(
        "lifecycle.started", ts=ts, lifecycle_id=lifecycle_id, fleeting=fleeting, phase=phase
    )


def _enclosure(**overrides: str) -> EnclosureNode:
    base: dict[str, str] = {
        "enclosure": "/c.md",
        "taskId": "T",
        "taskName": "t",
        "repoName": "r",
        "lifecycleId": "",
        "worktreeGroup": "/g",
        "humanReviewStatus": "approved",
        "closeoutStatus": "completed",
        "integrationStatus": "not-started",
        "cleanup": "pending",
    }
    base.update(overrides)
    return EnclosureNode.model_validate(base)


class FoldTests(unittest.TestCase):
    def test_seed_from_started(self) -> None:
        proj = project_lifecycle([_started()], now=FRESH)
        self.assertEqual((proj.id, proj.state, proj.phase, proj.fleeting), ("LC1", "running", "request", True))
        self.assertEqual(proj.startedAt, T0)
        self.assertFalse(proj.inferred)
        self.assertEqual(proj.tokens, 0)

    def test_phase_block_resume(self) -> None:
        log = [
            _started(ts=T0),
            _event("lifecycle.phase-changed", ts="2026-06-13T18:00:05+00:00", phase="build"),
            _event("lifecycle.blocked", ts="2026-06-13T18:00:10+00:00", ask={"kind": "question"}),
            _event("lifecycle.resumed", ts="2026-06-13T18:00:20+00:00"),
        ]
        proj = project_lifecycle(log, now=FRESH)
        self.assertEqual((proj.state, proj.phase), ("running", "build"))
        self.assertIsNone(proj.ask)

    def test_blocked_keeps_ask(self) -> None:
        log = [_started(), _event("lifecycle.blocked", ts="2026-06-13T18:00:10+00:00", ask={"kind": "decision"})]
        proj = project_lifecycle(log, now=FRESH)
        self.assertEqual(proj.state, "blocked")
        self.assertEqual(proj.ask, {"kind": "decision"})

    def test_tokens_aggregate(self) -> None:
        log = [
            _started(),
            _event("tool.completed", ts="2026-06-13T18:00:05+00:00", trust="observed", tool="a", tokens=100, ok=True),
            _event("tool.completed", ts="2026-06-13T18:00:10+00:00", trust="observed", tool="b", tokens=50, ok=True),
        ]
        self.assertEqual(project_lifecycle(log, now=FRESH).tokens, 150)

    def test_promote_makes_persistent(self) -> None:
        log = [
            _started(fleeting=True),
            _event(
                "lifecycle.promoted",
                ts="2026-06-13T18:00:05+00:00",
                trust="observed",
                actor="system",
                enclosure="/c.md",
                repo_id="repo-a",
                scope="repo-a",
            ),
        ]
        proj = project_lifecycle(log, now=FRESH)
        self.assertFalse(proj.fleeting)
        self.assertEqual((proj.scope, proj.enclosure, proj.repoId), ("repo-a", "/c.md", "repo-a"))

    def test_ended_outcomes(self) -> None:
        done = project_lifecycle([_started(), _event("lifecycle.ended", ts="2026-06-13T18:00:05+00:00", outcome="completed")], now=FRESH)
        dropped = project_lifecycle([_started(), _event("lifecycle.ended", ts="2026-06-13T18:00:05+00:00", outcome="abandoned")], now=FRESH)
        self.assertEqual(done.state, "completed")
        self.assertEqual(dropped.state, "abandoned")

    def test_empty_log_raises(self) -> None:
        with self.assertRaises(ValueError):
            project_lifecycle([], now=FRESH)


class DeterminismTests(unittest.TestCase):
    def test_same_log_same_projection(self) -> None:
        log = [
            _started(),
            _event("lifecycle.phase-changed", ts="2026-06-13T18:00:05+00:00", phase="build"),
            _event("tool.completed", ts="2026-06-13T18:00:10+00:00", trust="observed", tool="a", tokens=7, ok=True),
        ]
        first = project_lifecycle(log, now=STALE).model_dump()
        second = project_lifecycle(log, now=STALE).model_dump()
        self.assertEqual(first, second)


class InferredLayerTests(unittest.TestCase):
    def test_fresh_running_stays_running(self) -> None:
        proj = project_lifecycle([_started()], now=FRESH)
        self.assertEqual(proj.state, "running")
        self.assertFalse(proj.inferred)
        self.assertEqual(proj.staleSeconds, 30.0)

    def test_stale_running_projects_paused(self) -> None:
        proj = project_lifecycle([_started()], now=STALE)
        self.assertEqual(proj.state, "paused")
        self.assertTrue(proj.inferred)

    def test_fleeting_dormant_projects_abandoned(self) -> None:
        proj = project_lifecycle([_started(fleeting=True)], now=DORMANT)
        self.assertEqual(proj.state, "abandoned")
        self.assertTrue(proj.inferred)

    def test_persistent_dormant_not_abandoned(self) -> None:
        log = [
            _started(fleeting=True),
            _event("lifecycle.promoted", ts="2026-06-13T18:00:05+00:00", trust="observed", actor="system", scope="repo-a"),
        ]
        proj = project_lifecycle(log, now=DORMANT)
        self.assertEqual(proj.state, "paused")  # stale, but never auto-abandoned
        self.assertTrue(proj.inferred)

    def test_terminal_survives_staleness(self) -> None:
        proj = project_lifecycle([_started(), _event("lifecycle.ended", ts="2026-06-13T18:00:05+00:00", outcome="completed")], now=DORMANT)
        self.assertEqual(proj.state, "completed")
        self.assertFalse(proj.inferred)


class CorrectionTests(unittest.TestCase):
    def test_correction_overrides_state(self) -> None:
        ended = _event("lifecycle.ended", ts="2026-06-13T18:00:05+00:00", outcome="completed")
        correction = _event("correction.recorded", ts="2026-06-13T18:00:10+00:00", trust="inferred", actor="system", corrects=ended.id, state="abandoned")
        proj = project_lifecycle([_started(), ended, correction], now=FRESH)
        self.assertEqual(proj.state, "abandoned")

    def test_malformed_correction_ignored(self) -> None:
        ended = _event("lifecycle.ended", ts="2026-06-13T18:00:05+00:00", outcome="completed")
        bogus = _event("correction.recorded", ts="2026-06-13T18:00:10+00:00", trust="inferred", actor="system", corrects=ended.id, state="not-a-state")
        proj = project_lifecycle([_started(), ended, bogus], now=FRESH)
        self.assertEqual(proj.state, "completed")


class ActionAvailabilityTests(unittest.TestCase):
    def test_resume_enabled_only_when_blocked(self) -> None:
        blocked = project_lifecycle([_started(), _event("lifecycle.blocked", ts="2026-06-13T18:00:10+00:00", ask={"kind": "question"})], now=FRESH)
        running = project_lifecycle([_started()], now=FRESH)
        self.assertTrue(_action(blocked.actions, "resume").enabled)
        self.assertFalse(_action(running.actions, "resume").enabled)

    def test_integrate_available_after_closeout(self) -> None:
        actions = enclosure_actions(_enclosure())
        self.assertTrue(_action(actions, "integrate").enabled)
        self.assertFalse(_action(actions, "cleanup").enabled)

    def test_integrate_blocked_before_closeout(self) -> None:
        actions = enclosure_actions(_enclosure(closeoutStatus="not-started"))
        integrate = _action(actions, "integrate")
        self.assertFalse(integrate.enabled)
        self.assertEqual(integrate.disabledReason, "closeout not complete")

    def test_cleanup_available_after_integration(self) -> None:
        actions = enclosure_actions(_enclosure(integrationStatus="completed"))
        self.assertFalse(_action(actions, "integrate").enabled)
        self.assertTrue(_action(actions, "cleanup").enabled)


class WorkspaceTests(unittest.TestCase):
    def test_tree_and_metrics(self) -> None:
        logs = [
            [_started(lifecycle_id="LC1", ts=T0)],
            [_started(lifecycle_id="LC2", ts=T0), _event("lifecycle.blocked", lifecycle_id="LC2", ts="2026-06-13T18:00:05+00:00", ask={"kind": "question"})],
        ]
        proj = project_workspace(
            logs,
            enclosures=[_enclosure()],
            providers=[ProviderNode(id="cgc", state="ready", ok=True, watcherUp=True, indexingState="indexed")],
            now=FRESH,
        )
        self.assertEqual(len(proj.lifecycles), 2)
        self.assertEqual(proj.metrics.lifecycleCount, 2)
        self.assertEqual(proj.metrics.runningCount, 1)
        self.assertEqual(proj.metrics.blockedCount, 1)
        self.assertEqual(proj.generatedAt, FRESH.isoformat())
        self.assertTrue(_action(proj.enclosures[0].actions, "integrate").enabled)


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
        proj = project_workspace([[_started()]], enclosures=[], providers=[], now=FRESH)
        write_projection(self.root, proj)
        state = json.loads((self.root / "latest-state.json").read_text(encoding="utf-8"))
        WorkspaceProjection.model_validate(state)
        metrics = json.loads((self.root / "latest-metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(metrics["lifecycleCount"], 1)
        self.assertEqual(list(self.root.glob("*.tmp")), [])  # no torn temp left behind


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

    def test_read_providers_parses_snapshot_with_age(self) -> None:
        config = self._config()
        path = current_state_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "checkedAt": T0,
                    "providers": {
                        "codegraphcontext-code": {"id": "codegraphcontext-code", "state": "ready", "ok": True, "watcherUp": True, "indexingState": "indexed"},
                        "grepai-memory": {"id": "grepai-memory", "state": "stopped", "ok": False, "watcherUp": False, "indexingState": "unknown"},
                    },
                }
            ),
            encoding="utf-8",
        )
        nodes = {node.id: node for node in read_providers(config, now=STALE)}
        self.assertEqual(set(nodes), {"codegraphcontext-code", "grepai-memory"})
        self.assertEqual(nodes["codegraphcontext-code"].state, "ready")
        self.assertEqual(nodes["codegraphcontext-code"].snapshotStaleSeconds, 600.0)

    def test_read_providers_absent_is_empty(self) -> None:
        self.assertEqual(read_providers(self._config(), now=FRESH), [])

    def test_read_enclosures_from_contract(self) -> None:
        coord = (self.tmp / "coord").resolve()
        contract = default_contract(
            task_name="Observe Lifecycle",
            repo_name="repo-a",
            workflow_kind="light-task",
            memory_mode="disabled",
            coordination_root=coord,
            code_repo_path=coord / "repo-a",
            code_source_branch="main",
            code_work_branch="ar/observe",
            code_base_commit="0" * 40,
            worktree_name="observe",
            lifecycle_id="LC-1",
        )
        contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
        write_contract(contract.contract_path, contract)
        nodes = read_enclosures(coord)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].repoName, "repo-a")
        self.assertEqual(nodes[0].lifecycleId, "LC-1")

    def test_read_enclosures_absent_is_empty(self) -> None:
        self.assertEqual(read_enclosures((self.tmp / "nope").resolve()), [])

    def test_project_and_write_end_to_end(self) -> None:
        config = self._config()
        store = EventStore(observer_root(config))
        store.append(_started(lifecycle_id="LC1", ts=T0))
        proj = project_and_write(config, now=FRESH)
        self.assertEqual(proj.metrics.lifecycleCount, 1)
        self.assertTrue((observer_root(config) / "latest-state.json").exists())


def _action(actions: list, name: str):  # type: ignore[type-arg]
    return next(action for action in actions if action.action == name)


if __name__ == "__main__":
    unittest.main()
