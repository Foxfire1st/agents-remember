"""Dev-time conformance tests for MCP tool response contracts.

Production code already validates every tool payload against its registered model
in ``agents_remember.mcp.tools._tool_payload`` (``model_validate(...).model_dump(
mode="json", exclude_none=True)``). Strict models use ``extra="forbid"`` so
controller drift fails loudly at runtime. These tests move that guarantee into the
suite so drift is caught at dev time instead of in a live call.

For every response-modeled tool payload builder we obtain a *representative*
response payload by invoking the real ``*_payload`` builder against a temporary
fixture workspace, then assert:

* the payload validates against the registered model with no error, and
* round-tripping the payload through the model does not fabricate keys.

We also assert the strict/flexible split is exactly what the response-model
taxonomy intends: every model that is not built on ``FlexibleResponseModel`` keeps
``extra="forbid"``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.controllers.memory_tools import CarryoverSelection
from agents_remember.controllers.provider_tools import (
    GrepaiSearchQuery,
    GrepaiTraceQuery,
    ProviderQueryScope,
)
from agents_remember.controllers.task_doc_tools import TaskDocEdit, TaskDocTarget
from agents_remember.controllers.task_ref import TaskRef
from agents_remember.controllers.worktree_tools import (
    CloseoutApproval,
    CloseoutCommitMessages,
    StartExecution,
    TaskBases,
    TaskIdentity,
)
from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxMessage,
    InboxPoster,
)
from agents_remember.controlplane.records import GateAnchor, GateRequest, GateVerdict
from agents_remember.mcp import tools
from agents_remember.mcp.config import load_config
from agents_remember.mcp.tools.gates import GateRaise, GateWait
from agents_remember.mcp.tools.orchestration import NudgeSubject, NudgeTarget
from agents_remember.mcp.tools.terminal import RetiredSpawnInputs
from agents_remember.models.base import FlexibleResponseModel
from agents_remember.models.tool_registry import TOOL_RESPONSE_MODELS
from agents_remember.observer import (
    AmbientLifecycle,
    EventStore,
    install_ambient,
    reset_ambient,
)
from agents_remember.observer.ambient import AmbientTiming
from agents_remember.serving.supervisor_heartbeat import SupervisorHeartbeatStore
from agents_remember.tasks import TaskDocument, write_task_doc
from test_config import settings_payload
from test_worktree_support import (
    commit_file,
    git,
    init_repo,
    initialized_memory_repo,
    write_file_onboarding,
)

REPO = "agents-remember"
DRY_RUN_SCOPE = ProviderQueryScope(dry_run=True)
DISABLED_MEMORY_BASES = TaskBases(memory_mode="disabled", memory_choice="disabled-memory")


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _run_git(repo: Path, args: list[str]) -> None:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)


def _write_leaf_task(
    coordination_root: Path,
    *,
    repo: str = REPO,
    master: str = "master",
    doc_id: str = "leaf-1",
    slug: str | None = None,
) -> None:
    slug = slug or doc_id
    task_root = coordination_root / "tasks" / repo / master
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": master.upper(),
                "slug": "task",
                "title": "Master",
                "kind": "master",
                "repo": repo,
                "createdAt": "2026-07-07T10:00",
                "subTasks": [
                    {
                        "number": doc_id,
                        "name": "Leaf",
                        "file": f"{slug}.md",
                        "status": "inProgress",
                    }
                ],
            }
        ),
    )
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": doc_id,
                "slug": slug,
                "title": "Leaf",
                "kind": "subTask",
                "repo": repo,
                "createdAt": "2026-07-07T10:01",
                "master": "task.md",
            }
        ),
    )


def _base_fixture(root: Path):
    """Code repo + memory layer + ``.codex/mcp`` settings for the simple tools."""
    repo = root / "workspace" / REPO
    memory = root / "ar-coordination" / "memory-repos" / f"ar-{REPO}"
    (memory / "system").mkdir(parents=True, exist_ok=True)
    (memory / "onboarding").mkdir(parents=True, exist_ok=True)
    (memory / "system" / "settings.md").write_text("# Settings\n", encoding="utf-8")
    _write_leaf_task(root / "ar-coordination")
    repo.mkdir(parents=True, exist_ok=True)
    _run_git(repo, ["init", "-b", "main"])
    _run_git(repo, ["config", "user.email", "agents-remember@example.invalid"])
    _run_git(repo, ["config", "user.name", "Agents Remember"])
    (repo / "README.md").write_text("# Fixture\n", encoding="utf-8")
    _run_git(repo, ["add", "README.md"])
    _run_git(repo, ["commit", "-m", "init"])
    path = root / ".codex" / "mcp" / "settings.json"
    _write_json(path, settings_payload(root))
    return load_config(path)


def _simple_payloads(config) -> dict[str, dict]:
    """Tools whose real ``*_payload`` builder runs against the base fixture."""
    return {
        "ping": tools.ping_payload(),
        "server_info": tools.server_info_payload(config),
        "context_packet": tools.context_packet_payload(config, REPO),
        "read_ar_files": tools.read_ar_files_payload(
            config, REPO, [{"path": "README.md", "source": "full"}]
        ),
        "attach_terminal_session_to_leaf": tools.attach_terminal_session_to_leaf_payload(
            config,
            session_id="missing-session",
            leaf_key=f"{REPO}/master/leaf-1",
        ),
        # Representative refusal payload: a legacy caller-supplied harness short-circuits before any
        # tmux spawn, so the conformance fixture never touches a real terminal host.
        "spawn_agent_session": tools.spawn_agent_session_payload(
            config,
            retired=RetiredSpawnInputs(harness="definitely-not-a-real-harness"),
        ),
        "hosted_session_readiness": tools.hosted_session_readiness_payload(
            config,
            session_id="missing-session",
        ),
        # Representative refusal payloads: neither session id has a catalog row, so both
        # short-circuit before touching a real tmux host.
        "session_retire": tools.session_retire_payload(
            config,
            actor_session_id="missing-actor",
            session_id="missing-session",
        ),
        "session_rename": tools.session_rename_payload(
            config,
            session_id="missing-session",
            label="New Label",
        ),
        "runtime_install": tools.runtime_install_payload(config, install_provider_deps=False),
        "resolve_context": tools.resolve_context_payload(config, TaskRef(repo_id=REPO)),
        "drift_check": tools.drift_check_payload(config, REPO),
        "memory_quality_check": tools.memory_quality_check_payload(config, REPO),
        "route_index_refresh": tools.route_index_refresh_payload(config, REPO),
        "memory_init": tools.memory_init_payload(config, REPO),
        "skills_install": tools.skills_install_payload(config),
        "provider_status": tools.provider_status_payload(config),
        "provider_diagnostics": tools.provider_diagnostics_payload(config),
        "provider_watchers": tools.provider_watchers_payload(config, action="status"),
        "grepai_search": tools.grepai_search_payload(
            config, GrepaiSearchQuery(query="query"), scope=DRY_RUN_SCOPE
        ),
        "grepai_trace": tools.grepai_trace_payload(
            config, GrepaiTraceQuery(trace_action="graph", symbol="sym"), scope=DRY_RUN_SCOPE
        ),
        "cgc_symbol_search": tools.cgc_symbol_search_payload(
            config, REPO, "sym", scope=DRY_RUN_SCOPE
        ),
        "cgc_callers": tools.cgc_callers_payload(config, REPO, "fn", scope=DRY_RUN_SCOPE),
        "cgc_callees": tools.cgc_callees_payload(config, REPO, "fn", scope=DRY_RUN_SCOPE),
        "cgc_dependencies": tools.cgc_dependencies_payload(
            config, REPO, "mod", scope=DRY_RUN_SCOPE
        ),
        "cgc_complexity": tools.cgc_complexity_payload(config, REPO, scope=DRY_RUN_SCOPE),
        "cgc_visualize": tools.cgc_visualize_payload(config, REPO, scope=DRY_RUN_SCOPE),
        "memory_baseline_status": tools.memory_baseline_status_payload(config, REPO),
        "memory_baseline_adopt": tools.memory_baseline_adopt_payload(config, REPO),
        "codex_benchmark_prepare": tools.codex_benchmark_prepare_payload(config),
        "codex_benchmark_run": tools.codex_benchmark_run_payload(config),
    }


def _worktree_payloads(root: Path) -> dict[str, dict]:
    """Drive a real worktree lifecycle (disabled memory) and capture every step."""
    config = _base_fixture(root)
    # worktree_start needs a memory git repo to exist even when memory is disabled.
    tools.memory_init_payload(config, REPO, dry_run=False, initialize_git=True)
    memory_root = root / "ar-coordination" / "memory-repos" / f"ar-{REPO}"
    (memory_root / "memory.md").write_text("# Memory ledger\n", encoding="utf-8")
    _run_git(memory_root, ["add", "-A"])
    _run_git(memory_root, ["commit", "-m", "seed"])
    _write_leaf_task(config.coordination_root, master="demo-task", doc_id="demo-wt")
    _write_leaf_task(config.coordination_root, master="abandon-task", doc_id="abandon-wt")

    payloads: dict[str, dict] = {}
    payloads["worktree_start"] = tools.worktree_start_payload(
        config,
        TaskIdentity(repo_id=REPO, task_name="demo-task", worktree_name="demo-wt"),
        bases=DISABLED_MEMORY_BASES,
        execution=StartExecution(skip_provider_setup=True),
    )
    contract_path = payloads["worktree_start"]["contract_path"]
    payloads["worktree_status"] = tools.worktree_status_payload(
        config, TaskRef(repo_id=REPO, contract_path=contract_path)
    )
    payloads["worktree_attach"] = tools.worktree_attach_payload(
        config, TaskRef(repo_id=REPO, contract_path=contract_path)
    )
    payloads["worktree_sync"] = tools.worktree_sync_payload(config, contract_path, dry_run=True)
    payloads["worktree_closeout_preview"] = tools.worktree_closeout_preview_payload(
        config, contract_path, CloseoutCommitMessages(code="code commit message")
    )
    payloads["worktree_closeout_apply"] = tools.worktree_closeout_apply_payload(
        config,
        contract_path,
        CloseoutCommitMessages(code="code commit message"),
        CloseoutApproval(intent_note="intent note"),
    )
    _run_git(config.workspace_root / REPO, ["checkout", "ar/demo-task"])
    payloads["worktree_integrate"] = tools.worktree_integrate_payload(
        config, contract_path, dry_run=False
    )
    payloads["worktree_cleanup"] = tools.worktree_cleanup_payload(
        config, contract_path, dry_run=False
    )
    payloads["lifecycle_finalize_task"] = tools.lifecycle_finalize_task_payload(
        config, contract_path, dry_run=True
    )
    abandon_start = tools.worktree_start_payload(
        config,
        TaskIdentity(repo_id=REPO, task_name="abandon-task", worktree_name="abandon-wt"),
        bases=DISABLED_MEMORY_BASES,
        execution=StartExecution(skip_provider_setup=True),
    )
    payloads["worktree_abandon"] = tools.worktree_abandon_payload(
        config, abandon_start["contract_path"], dry_run=False, force=True
    )
    # Reopen the fully landed demo-task leaf (closeout+integrate+cleanup completed above) —
    # after the finalize preview so its landed-commit proof still saw the completed contract.
    payloads["task_reopen"] = tools.task_reopen_payload(config, contract_path, dry_run=False)
    return payloads


def _carryover_payloads(root: Path) -> dict[str, dict]:
    """Landed-branch fixture for the c-11-memory-carryover-from-branch skill carryover tools."""
    code_repo = root / "repo-a"
    old_base = init_repo(code_repo, "main")
    git(code_repo, "checkout", "-b", "workbench/reado/v1.2")
    source_head = commit_file(
        code_repo, "feature.py", "def feature():\n    return 'landed'\n", "Add feature"
    )
    git(code_repo, "checkout", "main")
    git(code_repo, "merge", "--ff-only", "workbench/reado/v1.2")

    official_memory = root / "ar-coordination" / "memory-repos" / "ar-repo-a"
    initialized_memory_repo(official_memory, "repo-a", "main", "main", old_base)
    source_memory = root / "ar-coordination" / "memory-source-branch" / "ar-repo-a"
    write_file_onboarding(source_memory / "onboarding", "repo-a", "feature.py", source_head)
    onboarding_file = source_memory / "onboarding" / "feature.py.md"
    onboarding_file.write_text(
        onboarding_file.read_text(encoding="utf-8") + "Branch-learned behavior.\n",
        encoding="utf-8",
    )

    settings = settings_payload(root)
    settings["workspaceRoot"] = str(root)
    settings["repositories"] = {"repo-a": {}}
    path = root / ".codex" / "mcp" / "settings.json"
    _write_json(path, settings)
    config = load_config(path)
    source = source_memory.as_posix()
    return {
        "memory_carryover_plan": tools.memory_carryover_plan_payload(
            config, _carryover_selection(source, old_base)
        ),
        "memory_carryover_apply": tools.memory_carryover_apply_payload(
            config, _carryover_selection(source, old_base), intent_note="intent note"
        ),
    }


def _carryover_selection(source: str, old_base: str) -> CarryoverSelection:
    return CarryoverSelection(
        repo_id="repo-a",
        source_memory=source,
        official_code_ref="main",
        source_code_ref="workbench/reado/v1.2",
        old_base=old_base,
    )


def _stale_supervisor(observer_root: Path) -> None:
    """Tick the supervisor heartbeat into the past so every capture carries the banner.

    Without this, these fixtures were a workspace whose supervisor had NEVER ticked --
    deliberately silent (see ``supervisor_staleness_banner``) -- so ``supervisorBanner``
    never fired and this suite validated the one shape the choke point cannot break.
    A ticked-then-quiet row is the mutation point: it is the state in which the choke
    point adds a key, so it is the state the contract has to be checked in.
    """
    SupervisorHeartbeatStore(observer_root).tick(now=datetime.now(UTC) - timedelta(hours=6))


def _lifecycle_payloads(root: Path) -> dict[str, dict]:
    """Drive the ambient lifecycle through each signal, capturing every payload.

    The signals require an installed ambient; a long heartbeat keeps the capture
    deterministic, and the ambient is reset afterward so other suites see none.
    ``lifecycle_block`` stays here as lower-level compatibility coverage; it is
    not an advertised public MCP tool.
    """
    observer_root = root / "logs" / "observer"
    _stale_supervisor(observer_root)
    install_ambient(
        AmbientLifecycle(EventStore(observer_root), timing=AmbientTiming(heartbeat_seconds=3600))
    )
    try:
        return {
            "lifecycle_start": tools.lifecycle_start_payload(),
            "lifecycle_phase": tools.lifecycle_phase_payload("build"),
            "lifecycle_block": tools.lifecycle_block_payload(
                kind="decision", prompt="ok?", options=["a", "b"]
            ),
            "lifecycle_resume": tools.lifecycle_resume_payload(),
            "lifecycle_end": tools.lifecycle_end_payload("completed"),
            "switch_lifecycle": tools.switch_lifecycle_payload(),
            # Captured last: switch_lifecycle leaves a fresh running lifecycle, the
            # only state await_developer accepts. The notification does not self-dismiss
            # (the choke point guards on its name), so its own payload reports
            # awaiting-developer.
            "lifecycle_turn_end_notification": tools.lifecycle_turn_end_notification_payload(
                "Turn complete; your move."
            ),
        }
    finally:
        reset_ambient()


def _task_doc_payloads(root: Path) -> dict[str, dict]:
    """Author a representative task document (JSON-primary; markdown rendered)."""
    config = _base_fixture(root)
    return {
        "task_doc": tools.task_doc_payload(
            config,
            TaskDocTarget(repo_id=REPO, task_name="demo-task"),
            operation="create",
            edit=TaskDocEdit(
                fields={
                    "id": "DEMO",
                    "slug": "task",
                    "title": "Demo",
                    "kind": "master",
                    "repo": REPO,
                    "type": "Code",
                    "createdAt": "2026-01-01T00:00",
                    "objective": "demo",
                }
            ),
        )
    }


def _gate_payloads(config) -> dict[str, dict]:
    """Control-plane gate substrate, including lower-level compatibility builders."""
    observer_root = config.coordination_root / "logs" / "observer"
    _stale_supervisor(observer_root)
    install_ambient(
        AmbientLifecycle(
            EventStore(observer_root),
            timing=AmbientTiming(heartbeat_seconds=3600),
        )
    )
    try:
        started = tools.lifecycle_start_payload()

        def approve_lifecycle_gate(_seconds: float) -> None:
            open_gates = [
                gate
                for gate in tools.gate_list_payload(config, lifecycle_id=started["lifecycleId"])[
                    "gates"
                ]
                if gate["state"] == "open"
            ]
            tools.gate_decide_payload(
                config,
                gate_id=open_gates[0]["id"],
                lifecycle_id=started["lifecycleId"],
                verdict=GateVerdict(decision="approve", by="developer", via="dashboard"),
            )

        lifecycle_gate = tools.lifecycle_gate_payload(
            config,
            GateRaise(
                kind="agent-question",
                request=GateRequest(packet={"summary": "demo gate"}),
                ask={"kind": "question", "prompt": "Continue?", "options": ["yes", "no"]},
            ),
            wait=GateWait(timeout_seconds=None, sleep=approve_lifecycle_gate),
        )
    finally:
        reset_ambient()

    created = tools.gate_create_payload(
        config,
        kind="closeout-approval",
        anchor=GateAnchor(lifecycle_id="gate-demo"),
    )
    gate_id = created["gateId"]
    return {
        "lifecycle_gate": lifecycle_gate,
        "gate_create": created,
        "gate_decide": tools.gate_decide_payload(
            config,
            gate_id=gate_id,
            lifecycle_id="gate-demo",
            verdict=GateVerdict(decision="approve", by="developer", via="dashboard"),
        ),
        "gate_wait": tools.gate_wait_payload(
            config,
            gate_id=gate_id,
            lifecycle_id="gate-demo",
            wait=GateWait(timeout_seconds=30.0, poll_seconds=1.0, sleep=lambda _s: None),
        ),
        "gate_response_wait": tools.gate_response_wait_payload(
            config,
            gate_id=gate_id,
            lifecycle_id="gate-demo",
            wait=GateWait(sleep=lambda _s: None),
        ),
        "gate_list": tools.gate_list_payload(config, lifecycle_id="gate-demo"),
    }


def _operator_inbox_payloads(config) -> dict[str, dict]:
    """External-chat inbox substrate: post, poll, then consume one entry."""
    posted = tools.operator_inbox_post_payload(
        config,
        address=InboxAddress(lifecycle_id="inbox-demo", agent_id="agent-a"),
        message=InboxMessage(ask="Continue?", response="Yes, proceed.", gate_id="gate-demo"),
        poster=InboxPoster(created_by="developer", created_via="dashboard"),
    )
    return {
        "operator_inbox_post": posted,
        "operator_inbox_poll": tools.operator_inbox_poll_payload(
            config,
            lifecycle_id=None,
            agent_id="agent-a",
        ),
        "operator_inbox_consume": tools.operator_inbox_consume_payload(
            config,
            entry_id=posted["entryId"],
            consumed_by="model",
            consumed_via="cli",
        ),
    }


def _orchestration_payloads(config) -> dict[str, dict]:
    return {
        "orchestration_nudge_manager": tools.orchestration_nudge_manager_payload(
            config,
            reason="missing-turn-report",
            target=NudgeTarget(agent_id="manager-a"),
            subject=NudgeSubject(
                subject="worker 260703-L3",
                agent_id="worker-a",
                artifact_path="notes/reports/260703-L3-worker-report.md",
            ),
        )
    }


def _allowed_keys(model) -> set[str]:
    """Serialized keys the model is allowed to emit (field names plus aliases)."""
    allowed: set[str] = set()
    for name, info in model.model_fields.items():
        allowed.add(name)
        if info.alias:
            allowed.add(info.alias)
        serialization_alias = getattr(info, "serialization_alias", None)
        if serialization_alias:
            allowed.add(serialization_alias)
    return allowed


class ToolResponseConformanceTests(unittest.TestCase):
    payloads: dict[str, dict]
    _temp_dirs: list[str]

    @classmethod
    def setUpClass(cls) -> None:
        cls._temp_dirs = [tempfile.mkdtemp() for _ in range(8)]
        base, worktree, carryover, lifecycle, task_doc_root, gate_root, inbox_root, orch_root = (
            Path(d) for d in cls._temp_dirs
        )
        cls.payloads = {}
        cls.payloads.update(_simple_payloads(_base_fixture(base)))
        cls.payloads.update(_worktree_payloads(worktree))
        cls.payloads.update(_carryover_payloads(carryover))
        cls.payloads.update(_lifecycle_payloads(lifecycle))
        cls.payloads.update(_task_doc_payloads(task_doc_root))
        cls.payloads.update(_gate_payloads(_base_fixture(gate_root)))
        cls.payloads.update(_operator_inbox_payloads(_base_fixture(inbox_root)))
        cls.payloads.update(_orchestration_payloads(_base_fixture(orch_root)))

    @classmethod
    def tearDownClass(cls) -> None:
        # Git worktrees leave read-only pack files; ignore_errors avoids flaky
        # cleanup failures on Windows.
        for path in cls._temp_dirs:
            shutil.rmtree(path, ignore_errors=True)

    def test_every_modeled_tool_has_a_representative_payload(self) -> None:
        self.assertEqual(set(self.payloads), set(TOOL_RESPONSE_MODELS))

    def test_the_choke_point_injections_are_actually_exercised(self) -> None:
        # This suite sits exactly at the mutation point -- ``_tool_payload`` is where the
        # two envelope-wide keys get set -- but it can only catch drift in them if the
        # captures were taken in a state where they FIRE. They are captured in an active
        # lifecycle (``nextStep``) whose supervisor ticked and then went quiet
        # (``supervisorBanner``); assert both, so a fixture that quietly stops producing
        # them is a failure here rather than a silent hole in every assertion below.
        with_next_step = {name for name, body in self.payloads.items() if "nextStep" in body}
        with_banner = {name for name, body in self.payloads.items() if "supervisorBanner" in body}
        self.assertIn("lifecycle_start", with_next_step)
        self.assertIn("lifecycle_start", with_banner)
        self.assertIn("lifecycle_gate", with_banner)

    def test_representative_payloads_conform_to_registered_models(self) -> None:
        for tool_name, model in TOOL_RESPONSE_MODELS.items():
            with self.subTest(tool=tool_name):
                payload = self.payloads[tool_name]
                # (a) The representative payload validates against the model.
                model.model_validate(payload)
                # (b) Round-tripping does not fabricate keys. Strict models may
                # only emit declared fields; intentionally flexible models may
                # also pass through keys that were present on the input payload,
                # so the round trip must not invent keys that are neither
                # declared nor part of the input.
                round_trip = model.model_validate(payload).model_dump(
                    mode="json", exclude_none=True
                )
                allowed = _allowed_keys(model)
                if issubclass(model, FlexibleResponseModel):
                    allowed |= set(payload)
                self.assertLessEqual(
                    set(round_trip),
                    allowed,
                    f"{tool_name} round trip produced undeclared keys: "
                    f"{sorted(set(round_trip) - allowed)}",
                )

    def test_strict_response_models_forbid_extra_fields(self) -> None:
        for tool_name, model in TOOL_RESPONSE_MODELS.items():
            with self.subTest(tool=tool_name):
                # The response-model taxonomy decides strictness: anything not
                # built on FlexibleResponseModel is a strict contract and must
                # keep extra="forbid"; the flexible base must keep extra="allow".
                expected = "allow" if issubclass(model, FlexibleResponseModel) else "forbid"
                self.assertEqual(
                    model.model_config.get("extra"),
                    expected,
                    f"{tool_name} ({model.__name__}) must use extra={expected!r}",
                )


if __name__ == "__main__":
    unittest.main()
