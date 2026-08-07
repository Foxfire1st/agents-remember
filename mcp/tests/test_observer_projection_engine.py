from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.observer.landing_state import LandingStateRefresher
from agents_remember.observer.projection import LedgerRefNode, ProviderNode, SetupProgressNode
from agents_remember.observer.reducer import (
    AnalyticalInputs,
    WorkspaceStructure,
    _process_health,
    _ref_fact_state,
    build_attention_queue,
    build_engine_processes,
    enclosure_actions,
    project_workspace,
)
from agents_remember.observer.snapshots import (
    read_engine_process_facts,
    read_start_progress_entries,
)
from agents_remember.worktrees.start_progress import (
    StartBeat,
    StartingEnclosure,
    clear_start_progress,
    read_start_progress,
    start_progress_path,
    write_start_progress,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    write_contract,
)
from test_observer_projection import FRESH, _enclosure, _facts, _started


class EngineProcessTests(unittest.TestCase):
    """The slice-5e enclosure-centered process map (``build_engine_processes``)."""

    def test_process_fact_state_narrows_raw_status_facts_to_the_closed_vocabulary(self) -> None:
        self.assertEqual(_ref_fact_state(False, True), "missing")
        self.assertEqual(_ref_fact_state(True, True), "observed")
        self.assertEqual(_ref_fact_state(True, False), "derived")
        self.assertEqual(_ref_fact_state(True, "unexpected"), "missing")

    def test_process_health_narrows_raw_process_facts_to_the_closed_vocabulary(self) -> None:
        cases = (
            (("worktree-started", "failed", []), "failed"),
            (("worktree-started", "ok", ["seed: failed"]), "failed"),
            (("worktree-started", "stale", []), "stale"),
            (("worktree-started", "running", []), "running"),
            (("sync-needed", "ok", []), "blocked"),
            (("completed", "ok", []), "complete"),
            (("worktree-started", "ok", []), "nominal"),
        )
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                self.assertEqual(_process_health(*arguments), expected)

    def test_ledger_rows_pass_through_to_the_worktree_coupler(self) -> None:
        # 5h: the worktree coupler popover reads node.ledgerRows/ledgerRowCount. The reducer is a pure
        # fold, so the windowed rows ride in on EngineProcessFacts (read in the I/O layer) and pass through.
        rows = [
            LedgerRefNode(codeCommit="08e9221a", memoryCommit="d60a0511"),
            LedgerRefNode(codeCommit="600f7fa3", memoryCommit="1e667c6d"),
        ]
        node = build_engine_processes([_facts(ledger_rows=rows, ledger_row_count=11)], [], [], [])[
            0
        ]
        self.assertEqual(node.ledgerRows, rows)
        self.assertEqual(node.ledgerRowCount, 11)

    def test_ledger_rows_default_empty(self) -> None:
        node = build_engine_processes([_facts()], [], [], [])[0]
        self.assertEqual(node.ledgerRows, [])
        self.assertEqual(node.ledgerRowCount, 0)

    def test_disposed_worktrees_drop_from_engine_processes(self) -> None:
        # 05l Gap B: a cleaned-up/abandoned worktree (runtime gone) drops from the active engine-room
        # so the frontend animates the removal instead of rendering a phantom. cleanup-pending stays --
        # the de-materialise beat still needs a live node to animate.
        self.assertEqual(
            len(build_engine_processes([_facts(contract={"cleanup": "pending"})], [], [], [])), 1
        )
        self.assertEqual(
            build_engine_processes([_facts(contract={"cleanup": "completed"})], [], [], []), []
        )
        self.assertEqual(
            build_engine_processes([_facts(contract={"cleanup": "abandoned"})], [], [], []), []
        )

    def test_carryover_done_at_surfaces_on_the_node(self) -> None:
        # 05m: the dashboard reads the carryover milestone off the projected node (5k renders it).
        node = build_engine_processes(
            [
                _facts(
                    status={
                        "code_worktree_exists": True,
                        "carryoverDoneAt": "2026-06-21T09:00:00+02:00",
                    }
                )
            ],
            [],
            [],
            [],
        )[0]
        self.assertEqual(node.carryoverDoneAt, "2026-06-21T09:00:00+02:00")

    def test_carryover_done_at_defaults_to_none(self) -> None:
        node = build_engine_processes([_facts(status={"code_worktree_exists": True})], [], [], [])[
            0
        ]
        self.assertIsNone(node.carryoverDoneAt)

    def test_successful_bootstrap_is_observed_and_complete(self) -> None:
        facts = _facts(
            status={
                "code_worktree_exists": True,
                "code_worktree_dirty": False,
                "memory_worktree_exists": True,
                "memory_worktree_dirty": False,
                "freshness": {"state": "current", "code": {"baseBehindSource": 0}},
                "providers": {
                    "state": "ok",
                    "completedPhases": [
                        "codegraphcontext-code seed: ok",
                        "grepai-memory clone: ok",
                    ],
                    "failedPhases": [],
                },
            }
        )
        cgc = ProviderNode(
            id="codegraphcontext-code@grp",
            state="configured",
            ok=True,
            scope="worktree",
            role="code",
            worktreeGroup="grp",
        )
        grepai = ProviderNode(
            id="grepai-memory@grp",
            state="configured",
            ok=True,
            scope="worktree",
            role="memory",
            worktreeGroup="grp",
        )
        nodes = build_engine_processes(
            [facts],
            [],
            [grepai, cgc],
            [SetupProgressNode(group="grp", state="ok", completedCount=4)],
        )
        self.assertEqual(len(nodes), 1)
        node = nodes[0]
        self.assertEqual(node.health, "nominal")
        self.assertEqual(node.codeWorktree.factState, "observed")
        self.assertEqual(node.memoryWorktree.factState, "observed")  # type: ignore[union-attr]
        self.assertEqual([p.role for p in node.providers], ["code", "memory"])  # code before memory
        states = {edge.kind: edge.state for edge in node.edges}
        self.assertEqual(states["worktree-add"], "complete")
        self.assertEqual(states["cgc-seed"], "complete")
        self.assertEqual(states["grepai-clone"], "complete")
        self.assertEqual(node.missingFacts, [])

    def test_provider_setup_running(self) -> None:
        facts = _facts(status={"code_worktree_exists": True, "memory_worktree_exists": True})
        setup = SetupProgressNode(
            group="grp",
            state="running",
            currentPhase="grepai-memory clone",
            heartbeatAgeSeconds=2.0,
        )
        node = build_engine_processes([facts], [], [], [setup])[0]
        self.assertEqual(node.phase, "provider-setup")
        self.assertEqual(node.health, "running")
        states = {edge.kind: edge.state for edge in node.edges}
        self.assertEqual(states["cgc-seed"], "running")
        self.assertEqual(states["grepai-clone"], "running")
        self.assertEqual(node.providers, [])

    def test_missing_provider_stack_projects_missing_engine_slots(self) -> None:
        node = build_engine_processes(
            [
                _facts(
                    status={
                        "code_worktree_exists": True,
                        "memory_worktree_exists": True,
                    }
                )
            ],
            [],
            [],
            [],
        )[0]
        self.assertEqual(
            [
                (provider.role, provider.runtimeState, provider.factState)
                for provider in node.providers
            ],
            [("code", "missing", "missing"), ("memory", "missing", "missing")],
        )
        states = {edge.kind: edge.state for edge in node.edges}
        self.assertEqual(states["cgc-seed"], "planned")
        self.assertEqual(states["grepai-clone"], "planned")
        self.assertTrue(any("provider runtime not observed" in fact for fact in node.missingFacts))

    def test_failed_setup_marks_failed(self) -> None:
        setup = SetupProgressNode(
            group="grp", state="failed", failedPhases=["grepai-memory clone: failed (stalled)"]
        )
        node = build_engine_processes(
            [_facts(status={"code_worktree_exists": True})], [], [], [setup]
        )[0]
        self.assertEqual(node.health, "failed")
        self.assertEqual({e.kind: e.state for e in node.edges}["grepai-clone"], "failed")

    def test_missing_status_degrades_without_crashing(self) -> None:
        node = build_engine_processes([_facts(status=None)], [], [], [])[0]
        self.assertEqual(node.codeWorktree.factState, "missing")
        self.assertTrue(any("not observed" in fact for fact in node.missingFacts))

    def test_disabled_memory_has_no_memory_lane(self) -> None:
        node = build_engine_processes(
            [_facts(contract={"memory_mode": "disabled"}, status={"code_worktree_exists": True})],
            [],
            [],
            [],
        )[0]
        self.assertIsNone(node.memorySource)
        self.assertIsNone(node.memoryWorktree)
        self.assertEqual(node.memoryMode, "disabled")
        self.assertNotIn("grepai-clone", {edge.kind for edge in node.edges})

    def test_sync_needed_when_behind_official(self) -> None:
        facts = _facts(
            status={
                "code_worktree_exists": True,
                "freshness": {"state": "behind-official", "code": {"baseBehindSource": 3}},
            }
        )
        node = build_engine_processes([facts], [], [], [])[0]
        self.assertEqual(node.phase, "sync-needed")
        self.assertEqual(node.health, "blocked")
        self.assertEqual(node.codeSource.behindSource, 3)
        self.assertIn("sync", {edge.kind for edge in node.edges})

    def test_join_uses_worktree_group_basename(self) -> None:
        facts = _facts(
            contract={"worktree_group": "/w/r/260610-grp"}, status={"code_worktree_exists": True}
        )
        prov = ProviderNode(
            id="cgc@260610-grp",
            state="configured",
            ok=True,
            scope="worktree",
            role="code",
            worktreeGroup="260610-grp",
        )
        node = build_engine_processes([facts], [], [prov], [])[0]
        self.assertEqual(
            [p.id for p in node.providers], ["cgc@260610-grp", "missing-memory@260610-grp"]
        )
        self.assertEqual(node.providers[1].factState, "missing")

    def test_actions_reuse_precomputed_enclosure_actions(self) -> None:
        enc = _enclosure(closeoutStatus="completed", integrationStatus="not-started")
        enriched = [enc.model_copy(update={"actions": enclosure_actions(enc)})]
        node = build_engine_processes(
            [_facts(status={"code_worktree_exists": True})], enriched, [], []
        )[0]
        self.assertTrue(any(action.action == "integrate" for action in node.actions))

    def test_deterministic(self) -> None:
        args = ([_facts(status={"code_worktree_exists": True})], [], [], [])
        self.assertEqual(
            build_engine_processes(*args)[0].model_dump(),
            build_engine_processes(*args)[0].model_dump(),
        )

    def test_landing_and_strategy_default_empty(self) -> None:
        # Additive 5h fields: no landing observation + no recorded strategy -> empty/None (no break).
        node = build_engine_processes([_facts(status={"code_worktree_exists": True})], [], [], [])[
            0
        ]
        self.assertEqual(node.landing, [])
        self.assertIsNone(node.integrationStrategy)

    def test_landing_and_strategy_mapped_from_facts(self) -> None:
        node = build_engine_processes(
            [
                _facts(
                    contract={"integration_strategy": "ff-only"},
                    status={
                        "code_worktree_exists": True,
                        "landing": [
                            {
                                "kind": "origin-feat",
                                "label": "origin/feat-x",
                                "state": "pushed",
                                "factState": "stale",
                                "observedAt": "2026-07-12T16:00:00+00:00",
                                "lastAttemptAt": "2026-07-12T16:01:00+00:00",
                                "staleSeconds": 60.0,
                            },
                            {
                                "kind": "pr",
                                "label": "PR #128",
                                "state": "open",
                                "factState": "observed",
                            },
                        ],
                    },
                )
            ],
            [],
            [],
            [],
        )[0]
        self.assertEqual(node.integrationStrategy, "ff-only")
        self.assertEqual([ref.kind for ref in node.landing], ["origin-feat", "pr"])
        self.assertEqual(node.landing[0].factState, "stale")
        self.assertEqual(node.landing[0].staleSeconds, 60.0)
        self.assertEqual(node.landing[1].state, "open")

    def test_frozen_landing_rows_do_not_break_the_reducer(self) -> None:
        # The reducer does LandingRefNode(**ref) unguarded and
        # LandingRefNode is extra="forbid". Rows served by the freeze (landing_state.current()) must
        # be a strict subset of the node's fields, or project_and_write raises ValidationError and
        # every projection tick stalls silently. Assert the served frozen-row shape maps cleanly.
        now = datetime(2026, 7, 12, 16, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = default_contract(
                ContractTask(
                    name="landing-frozen",
                    repo_name="repo-frozen",
                    coordination_root=root,
                    workflow_kind="light-task",
                    memory_mode="disabled",
                ),
                leaf=LeafIdentity(worktree_name="landing-frozen"),
                code=RepoBranchPlan(
                    repo_path=root / "repo-frozen",
                    source_branch="feat/frozen",
                    work_branch="ar/frozen",
                    base_commit="base-frozen",
                ),
            )
            contract = replace(contract, closeout_status="completed", cleanup="completed")
            contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
            write_contract(contract.contract_path, contract)

            def observed(target: object) -> list[dict[str, object]]:
                return [
                    {
                        "kind": "origin-feat",
                        "label": "origin/feat/frozen",
                        "state": "pushed",
                        "factState": "observed",
                    }
                ]

            config = McpRuntimeConfig(
                config_path=root / "settings.json",
                coordination_root=root,
                workspace_root=root,
                transcript_root=root / "logs" / "mcp",
            )
            refresher = LandingStateRefresher(config, observe=observed)
            asyncio.run(refresher.refresh_once(now=now))
            served = refresher.current(contract, now=now)
            assert served is not None

        node = build_engine_processes(
            [_facts(status={"code_worktree_exists": True, "landing": served})], [], [], []
        )[0]
        self.assertEqual([ref.kind for ref in node.landing], ["origin-feat"])
        self.assertEqual(node.landing[0].state, "pushed")
        self.assertEqual(node.landing[0].factState, "observed")

    def test_project_workspace_wires_engine_processes(self) -> None:
        proj = project_workspace(
            [],
            structure=WorkspaceStructure(enclosures=[], providers=[]),
            now=FRESH,
            given=AnalyticalInputs(
                engine_process_facts=[_facts(status={"code_worktree_exists": True})]
            ),
        )
        self.assertEqual(len(proj.analytics.engineProcesses), 1)
        self.assertEqual(proj.version, 2)

    def test_3a_callers_get_empty_engine_processes(self) -> None:
        proj = project_workspace(
            [[_started()]], structure=WorkspaceStructure(enclosures=[], providers=[]), now=FRESH
        )
        self.assertEqual(proj.analytics.engineProcesses, [])

    def test_reader_emits_one_fact_per_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = default_contract(
                ContractTask(
                    name="demo task",
                    repo_name="r",
                    coordination_root=root,
                    workflow_kind="light-task",
                    memory_mode="disabled",
                ),
                leaf=LeafIdentity(worktree_name="demo-wt"),
                code=RepoBranchPlan(
                    repo_path=root / "repo",
                    source_branch="main",
                    work_branch="ar/x",
                    base_commit="abc",
                ),
            )
            contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
            write_contract(contract.contract_path, contract)
            facts = read_engine_process_facts(root)
            self.assertEqual(len(facts), 1)
            self.assertEqual(facts[0].contract["task_name"], "demo task")
            self.assertIn("phase", facts[0].guidance)

    def test_reader_skips_inactive_engine_process_groups_when_filtered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = default_contract(
                ContractTask(
                    name="demo task",
                    repo_name="r",
                    coordination_root=root,
                    workflow_kind="light-task",
                    memory_mode="disabled",
                ),
                leaf=LeafIdentity(worktree_name="demo-wt"),
                code=RepoBranchPlan(
                    repo_path=root / "repo",
                    source_branch="main",
                    work_branch="ar/x",
                    base_commit="abc",
                ),
            )
            contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
            write_contract(contract.contract_path, contract)

            self.assertEqual(
                read_engine_process_facts(root, active_worktree_groups={"other-group"}), []
            )
            facts = read_engine_process_facts(
                root, active_worktree_groups={contract.worktree_group.name}
            )

            self.assertEqual(len(facts), 1)

    def test_start_progress_synthesizes_pre_contract_node(self) -> None:
        entry = {
            "schema": "ar-worktree-start-progress/v1",
            "repoName": "agents-remember",
            "taskName": "dm-v1.2",
            "worktreeName": "v12-feat",
            "worktreeGroup": "/w/agents-remember/v12-feat-ar",
            "phase": "memory-blocked",
            "memoryMode": "external",
            "codeSourceBranch": "main",
            "codeBaseCommit": "abc1234",
            "blockedReason": "no exact ledger mapping for selected code base commit",
            "completedPhases": ["preflight", "code-worktree"],
            "choices": ["reconciliation", "disabled-memory", "custom"],
            "sourceFile": "/w/temp/worktree-start/agents-remember/v12-feat.json",
        }
        nodes = build_engine_processes([], [], [], [], [entry])
        self.assertEqual(len(nodes), 1)
        node = nodes[0]
        self.assertEqual(node.phase, "memory-compatibility")
        self.assertEqual(node.health, "blocked")
        self.assertEqual(node.codeWorktree.factState, "observed")  # code-worktree completed
        self.assertEqual(node.memoryWorktree.factState, "missing")  # type: ignore[union-attr]
        self.assertTrue(any("contract not yet written" in fact for fact in node.missingFacts))
        self.assertEqual(node.nextAction, "reconciliation")

    def test_start_progress_skipped_when_contract_covers_the_group(self) -> None:
        facts = _facts(
            contract={"worktree_group": "/w/agents-remember/v12-feat-ar"},
            status={"code_worktree_exists": True},
        )
        entry = {
            "worktreeGroup": "/w/agents-remember/v12-feat-ar",
            "phase": "memory-blocked",
            "memoryMode": "external",
            "sourceFile": "x",
        }
        nodes = build_engine_processes([facts], [], [], [], [entry])
        self.assertEqual(len(nodes), 1)  # only the contract node — not double-rendered

    def test_start_progress_write_read_clear_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_start_progress(
                root,
                StartingEnclosure(
                    repo_name="r",
                    task_name="t",
                    worktree_name="wt",
                    worktree_group="/w/r/wt-ar",
                    memory_mode="external",
                ),
                StartBeat(
                    phase="memory-blocked",
                    completed_phases=("preflight", "code-worktree"),
                    choices=("reconciliation",),
                    blocked_reason="no ledger mapping",
                ),
            )
            payload = read_start_progress(start_progress_path(root, "r", "wt"))
            assert payload is not None
            self.assertEqual(payload["phase"], "memory-blocked")
            self.assertEqual(payload["blockedReason"], "no ledger mapping")
            entries = read_start_progress_entries(root, now=FRESH)
            self.assertEqual(len(entries), 1)
            self.assertIn("ageSeconds", entries[0])
            clear_start_progress(root, "r", "wt")
            self.assertIsNone(read_start_progress(start_progress_path(root, "r", "wt")))
            self.assertEqual(read_start_progress_entries(root, now=FRESH), [])

    def test_blocked_start_raises_attention_parity(self) -> None:
        # §9: a pre-contract blocked start raises the same master-caution the agent raises in chat.
        blocked = {
            "worktreeGroup": "/w/agents-remember/v12-feat-ar",
            "repoName": "agents-remember",
            "phase": "memory-blocked",
            "blockedReason": "no exact ledger mapping for selected code base commit",
        }
        happy = {"worktreeGroup": "/w/agents-remember/dm-ar", "phase": "code-worktree"}
        items = build_attention_queue(
            [],
            [],
            AnalyticalInputs(
                drift_snapshots=[], setup_progress=[], engine_start_progress=[blocked, happy]
            ),
        )
        self.assertEqual(len(items), 1)  # only the blocked start is an alarm
        item = items[0]
        self.assertEqual(item.kind, "blocked-start")
        self.assertEqual(item.id, "blocked-start:v12-feat-ar")
        self.assertEqual(item.severity, "warn")
        self.assertEqual(item.lane, "worktree")
        self.assertEqual(item.detail, "no exact ledger mapping for selected code base commit")
        self.assertEqual(item.repoId, "agents-remember")

    def test_project_workspace_threads_blocked_start_into_attention(self) -> None:
        # §9 wiring: project_workspace must thread engine_start_progress into the attention queue.
        blocked = {
            "worktreeGroup": "/w/agents-remember/v12-feat-ar",
            "repoName": "agents-remember",
            "phase": "memory-blocked",
            "blockedReason": "no ledger mapping",
        }
        proj = project_workspace(
            [],
            structure=WorkspaceStructure(enclosures=[], providers=[]),
            now=FRESH,
            given=AnalyticalInputs(engine_start_progress=[blocked]),
        )
        kinds = [item.kind for item in proj.analytics.attentionQueue]
        self.assertIn("blocked-start", kinds)

    def test_happy_path_start_progress_is_observable_but_not_an_alarm(self) -> None:
        # §9 gap (a): a happy-path pre-contract beat (no blockedReason) is observable as a synthesized
        # node, but raises no attention item -- only blocked starts are alarms.
        happy = {
            "worktreeGroup": "/w/agents-remember/dm-ar",
            "repoName": "agents-remember",
            "taskName": "dm",
            "phase": "code-worktree",
            "memoryMode": "external",
            "completedPhases": ["preflight"],
        }
        nodes = build_engine_processes([], [], [], [], [happy])
        self.assertEqual(len(nodes), 1)  # observable as a (non-blocked) synthesized node
        self.assertEqual(
            build_attention_queue(
                [],
                [],
                AnalyticalInputs(
                    drift_snapshots=[], setup_progress=[], engine_start_progress=[happy]
                ),
            ),
            [],
        )  # not an alarm
