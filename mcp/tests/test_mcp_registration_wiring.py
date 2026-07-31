"""What each advertised MCP tool does with the arguments it is handed.

``agents_remember.mcp.registration`` is the tool surface: one module per family, each
declaring ``@server.tool()`` bodies that translate a flat MCP argument list into the
parameter objects the controllers take. The tool-list test in ``test_tools.py`` proves
the surface *advertises* the right names; nothing proved what a call to one of those
names actually does.

That translation is the whole content of these bodies and it is exactly where a split
goes wrong: an argument dropped on the floor, a flag landing in the wrong parameter
object, a default that silently changes what a call means (``codex_benchmark_run``
defaulting to a real run rather than a preview, ``gate_decide`` attributing a
developer-delegated decision to the model). Each test below calls the tool through the
live ``FastMCP`` instance -- so the registered schema, its defaults and its coercions are
in the path -- with the family's payload builder recorded, and states what the builder is
handed and that the tool returns its result unchanged.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.mcp.config import load_config
from agents_remember.mcp.server import create_server
from test_config import settings_payload

# The recorded builders return this; every test asserts the tool hands it back untouched,
# which is the other half of "the body is a delegation" -- it must not reshape the result.
SENTINEL: dict[str, Any] = {"ok": True, "marker": "payload-result"}


class Recorder:
    """Stands in for a payload builder and remembers the one call it received."""

    def __init__(self) -> None:
        self.args: tuple[Any, ...] = ()
        self.kwargs: dict[str, Any] = {}
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.args = args
        self.kwargs = kwargs
        self.calls += 1
        return SENTINEL


class RegistrationWiringTests(unittest.TestCase):
    """One live server per test, with the family's payload builder recorded."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        settings_path = self.root / "mcp-settings.json"
        settings_path.write_text(
            json.dumps(settings_payload(self.root), indent=2), encoding="utf-8"
        )
        self.config = load_config(settings_path)
        self.server = create_server(self.config)

    @contextmanager
    def recorded(self, target: str) -> Iterator[Recorder]:
        recorder = Recorder()
        with patch(target, recorder):
            yield recorder

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Invoke one advertised tool the way a client does, and return its structured result."""
        _content, structured = asyncio.run(self.server.call_tool(name, arguments or {}))
        return structured

    def invoke(self, name: str, target: str, arguments: dict[str, Any] | None = None) -> Recorder:
        """Call ``name`` with ``target`` recorded, asserting the delegation happened once."""
        with self.recorded(target) as recorder:
            result = self.call(name, arguments)
        self.assertEqual(recorder.calls, 1, f"{name} did not call {target} exactly once")
        self.assertEqual(result, SENTINEL, f"{name} reshaped the payload builder's result")
        return recorder

    # ---- core ------------------------------------------------------------------

    def test_ping_takes_no_configuration(self) -> None:
        """``ping`` is a liveness check: it must not depend on resolved settings."""
        recorder = self.invoke("ping", "agents_remember.mcp.registration.core.ping_payload")

        self.assertEqual(recorder.args, ())
        self.assertEqual(recorder.kwargs, {})

    def test_server_info_reports_the_config_the_server_was_built_with(self) -> None:
        recorder = self.invoke(
            "server_info", "agents_remember.mcp.registration.core.server_info_payload"
        )

        self.assertEqual(recorder.args, (self.config,))

    def test_context_packet_defaults_to_providers_only(self) -> None:
        """The docstring's "optionally provider status, drift and freshness": providers are
        on by default, the two costlier reads (drift, and the freshness fetch) are off."""
        recorder = self.invoke(
            "context_packet",
            "agents_remember.mcp.registration.core.context_packet_payload",
            {"repo_id": "agents-remember"},
        )

        self.assertEqual(recorder.args, (self.config, "agents-remember"))
        self.assertEqual(
            recorder.kwargs,
            {"include_providers": True, "include_drift": False, "include_freshness": False},
        )

    def test_context_packet_forwards_each_inclusion_flag(self) -> None:
        recorder = self.invoke(
            "context_packet",
            "agents_remember.mcp.registration.core.context_packet_payload",
            {
                "repo_id": "agents-remember",
                "include_providers": False,
                "include_drift": True,
                "include_freshness": True,
            },
        )

        self.assertEqual(
            recorder.kwargs,
            {"include_providers": False, "include_drift": True, "include_freshness": True},
        )

    def test_read_ar_files_forwards_the_file_requests_verbatim(self) -> None:
        files = [{"path": "mcp/src/agents_remember/mcp/server.py", "source": "full"}]

        recorder = self.invoke(
            "read_ar_files",
            "agents_remember.mcp.registration.core.read_ar_files_payload",
            {"repo_id": "agents-remember", "files": files},
        )

        self.assertEqual(recorder.args, (self.config, "agents-remember", files))
        self.assertEqual(recorder.kwargs, {"refresh": False})

    def test_resolve_context_packs_the_locators_into_a_task_ref(self) -> None:
        """``TaskRef`` carries "the repo a task belongs to and the identifiers that locate
        its contract" -- so repo/task/leaf/parent/contract go inside it, while the
        worktree name and the topology override stay separate arguments."""
        recorder = self.invoke(
            "resolve_context",
            "agents_remember.mcp.registration.core.resolve_context_payload",
            {
                "repo_id": "agents-remember",
                "task_name": "260731-EFA",
                "parent_task": "260731-EFA-MASTER",
                "leaf_id": "260731-EFA-L2",
                "contract_path": "/tmp/contract.yaml",
                "worktree_name": "efa-l2",
                "topology": "external",
            },
        )

        config, task_ref = recorder.args
        self.assertIs(config, self.config)
        self.assertEqual(task_ref.repo_id, "agents-remember")
        self.assertEqual(task_ref.task_name, "260731-EFA")
        self.assertEqual(task_ref.parent_task, "260731-EFA-MASTER")
        self.assertEqual(task_ref.leaf_id, "260731-EFA-L2")
        self.assertEqual(task_ref.contract_path, "/tmp/contract.yaml")
        self.assertEqual(recorder.kwargs, {"worktree_name": "efa-l2", "topology": "external"})

    def test_resolve_context_leaves_unheld_locators_unset(self) -> None:
        recorder = self.invoke(
            "resolve_context",
            "agents_remember.mcp.registration.core.resolve_context_payload",
            {"repo_id": "agents-remember"},
        )

        _config, task_ref = recorder.args
        self.assertEqual(task_ref.task_name, None)
        self.assertEqual(task_ref.parent_task, None)
        self.assertEqual(task_ref.leaf_id, None)
        self.assertEqual(task_ref.contract_path, None)
        self.assertEqual(recorder.kwargs, {"worktree_name": None, "topology": None})

    def test_runtime_install_installs_provider_deps_unless_told_otherwise(self) -> None:
        """The documented defaults: a real install (not a preview) that builds provider
        images with the layer cache, and leaves benchmark fixtures out."""
        recorder = self.invoke(
            "runtime_install", "agents_remember.mcp.registration.core.runtime_install_payload"
        )

        self.assertEqual(recorder.args, (self.config,))
        self.assertEqual(
            recorder.kwargs,
            {
                "dry_run": False,
                "include_benchmarks": False,
                "install_provider_deps": True,
                "no_cache": False,
            },
        )

    def test_runtime_install_forwards_the_from_scratch_rebuild_request(self) -> None:
        recorder = self.invoke(
            "runtime_install",
            "agents_remember.mcp.registration.core.runtime_install_payload",
            {"dry_run": True, "include_benchmarks": True, "no_cache": True},
        )

        self.assertEqual(
            recorder.kwargs,
            {
                "dry_run": True,
                "include_benchmarks": True,
                "install_provider_deps": True,
                "no_cache": True,
            },
        )

    def test_skills_install_neither_overwrites_nor_archives_by_default(self) -> None:
        recorder = self.invoke(
            "skills_install", "agents_remember.mcp.registration.core.skills_install_payload"
        )

        self.assertEqual(recorder.args, (self.config,))
        self.assertEqual(
            recorder.kwargs,
            {"dry_run": False, "overwrite": False, "archive_existing": False},
        )

    # ---- sessions --------------------------------------------------------------

    def test_attach_terminal_session_forwards_the_seat_role(self) -> None:
        recorder = self.invoke(
            "attach_terminal_session_to_leaf",
            "agents_remember.mcp.registration.sessions.attach_terminal_session_to_leaf_payload",
            {"session_id": "sess-1", "leaf_key": "260731-EFA-L2", "role": "worker"},
        )

        self.assertEqual(recorder.args, (self.config,))
        self.assertEqual(
            recorder.kwargs,
            {"session_id": "sess-1", "leaf_key": "260731-EFA-L2", "role": "worker"},
        )

    def test_spawn_agent_session_splits_its_arguments_into_the_three_declared_groups(
        self,
    ) -> None:
        """The seat the caller declares, the spend controls it is no longer allowed to
        declare, and who spawned it -- each argument lands in exactly one of the three."""
        recorder = self.invoke(
            "spawn_agent_session",
            "agents_remember.mcp.registration.sessions.spawn_agent_session_payload",
            {
                "leaf_key": "260731-EFA-L2",
                "replacement_for_leaf": "260731-EFA-L1",
                "level": "master",
                "label": "seam reviewer",
                "env": {"AR_SPAWN_ROLE": "reviewer"},
                "kind": "chat",
                "context": "legacy brief",
                "submit": True,
                "harness": "claude",
                "model": "opus",
                "effort": "high",
                "launch_args": ["--flag"],
                "prompt_keywords": ["kw"],
                "session_commands": ["cmd"],
                "spawned_by_session": "sess-parent",
                "spawned_by_lifecycle": "life-parent",
            },
        )

        self.assertEqual(recorder.args, (self.config,))
        seat = recorder.kwargs["seat"]
        self.assertEqual(seat.kind, "chat")
        self.assertEqual(seat.leaf_key, "260731-EFA-L2")
        self.assertEqual(seat.replacement_for_leaf, "260731-EFA-L1")
        self.assertEqual(seat.level, "master")
        self.assertEqual(seat.label, "seam reviewer")
        self.assertEqual(seat.env, {"AR_SPAWN_ROLE": "reviewer"})

        retired = recorder.kwargs["retired"]
        self.assertEqual(retired.context, "legacy brief")
        self.assertIs(retired.submit, True)
        self.assertEqual(retired.harness, "claude")
        self.assertEqual(retired.model, "opus")
        self.assertEqual(retired.effort, "high")
        self.assertEqual(retired.launch_args, ["--flag"])
        self.assertEqual(retired.prompt_keywords, ["kw"])
        self.assertEqual(retired.session_commands, ["cmd"])

        spawned_by = recorder.kwargs["spawned_by"]
        self.assertEqual(spawned_by.session_id, "sess-parent")
        self.assertEqual(spawned_by.lifecycle_id, "life-parent")

    def test_spawn_agent_session_defaults_to_a_harness_seat_with_no_retired_inputs(
        self,
    ) -> None:
        """An ordinary caller declares a seat and nothing else; every retired spend control
        must arrive unset so the refusal path is never triggered by the wiring itself."""
        recorder = self.invoke(
            "spawn_agent_session",
            "agents_remember.mcp.registration.sessions.spawn_agent_session_payload",
            {"leaf_key": "260731-EFA-L2"},
        )

        self.assertEqual(recorder.kwargs["seat"].kind, "harness")
        retired = recorder.kwargs["retired"]
        self.assertEqual(retired.context, None)
        self.assertIs(retired.submit, False)
        self.assertEqual(
            [retired.harness, retired.model, retired.effort],
            [None, None, None],
        )
        self.assertEqual(
            [retired.launch_args, retired.prompt_keywords, retired.session_commands],
            [None, None, None],
        )

    def test_hosted_session_readiness_does_not_wait_unless_asked(self) -> None:
        recorder = self.invoke(
            "hosted_session_readiness",
            "agents_remember.mcp.registration.sessions.hosted_session_readiness_payload",
            {"session_id": "sess-1"},
        )

        self.assertEqual(recorder.kwargs, {"session_id": "sess-1", "wait_seconds": 0.0})

    def test_hosted_session_readiness_forwards_the_callers_finite_wait(self) -> None:
        recorder = self.invoke(
            "hosted_session_readiness",
            "agents_remember.mcp.registration.sessions.hosted_session_readiness_payload",
            {"session_id": "sess-1", "wait_seconds": 12.5},
        )

        self.assertEqual(recorder.kwargs["wait_seconds"], 12.5)

    def test_session_retire_carries_the_retiring_seats_own_id_and_a_reason(self) -> None:
        """Authority is checked against ``actor_session_id``, so the tool must keep the two
        session ids distinct rather than collapsing them."""
        recorder = self.invoke(
            "session_retire",
            "agents_remember.mcp.registration.sessions.session_retire_payload",
            {"actor_session_id": "sess-manager", "session_id": "sess-worker"},
        )

        self.assertEqual(
            recorder.kwargs,
            {
                "actor_session_id": "sess-manager",
                "session_id": "sess-worker",
                "reason": "manual retire",
            },
        )

    def test_session_rename_passes_only_identity_text(self) -> None:
        recorder = self.invoke(
            "session_rename",
            "agents_remember.mcp.registration.sessions.session_rename_payload",
            {"session_id": "sess-1", "label": "renamed seat"},
        )

        self.assertEqual(recorder.args, (self.config,))
        self.assertEqual(recorder.kwargs, {"session_id": "sess-1", "label": "renamed seat"})

    # ---- memory ----------------------------------------------------------------

    def test_drift_check_defaults_to_fifty_findings(self) -> None:
        recorder = self.invoke(
            "drift_check",
            "agents_remember.mcp.registration.memory.drift_check_payload",
            {"repo_id": "agents-remember"},
        )

        self.assertEqual(recorder.args, (self.config, "agents-remember"))
        self.assertEqual(recorder.kwargs, {"detail_limit": 50})

    def test_memory_quality_check_runs_every_check_when_none_are_named(self) -> None:
        recorder = self.invoke(
            "memory_quality_check",
            "agents_remember.mcp.registration.memory.memory_quality_check_payload",
            {"repo_id": "agents-remember"},
        )

        self.assertEqual(recorder.kwargs, {"checks": None, "detail_limit": 50})

    def test_memory_quality_check_forwards_a_named_subset(self) -> None:
        recorder = self.invoke(
            "memory_quality_check",
            "agents_remember.mcp.registration.memory.memory_quality_check_payload",
            {"repo_id": "agents-remember", "checks": ["drift"], "detail_limit": 5},
        )

        self.assertEqual(recorder.kwargs, {"checks": ["drift"], "detail_limit": 5})

    def test_route_index_refresh_writes_unless_previewed(self) -> None:
        recorder = self.invoke(
            "route_index_refresh",
            "agents_remember.mcp.registration.memory.route_index_refresh_payload",
            {"repo_id": "agents-remember"},
        )

        self.assertEqual(recorder.args, (self.config, "agents-remember"))
        self.assertEqual(recorder.kwargs, {"dry_run": False})

    def test_memory_init_initializes_git_by_default(self) -> None:
        recorder = self.invoke(
            "memory_init",
            "agents_remember.mcp.registration.memory.memory_init_payload",
            {"repo_id": "agents-remember"},
        )

        self.assertEqual(recorder.kwargs, {"dry_run": False, "initialize_git": True})

    def test_memory_baseline_status_takes_only_the_repo(self) -> None:
        recorder = self.invoke(
            "memory_baseline_status",
            "agents_remember.mcp.registration.memory.memory_baseline_status_payload",
            {"repo_id": "agents-remember"},
        )

        self.assertEqual(recorder.args, (self.config, "agents-remember"))
        self.assertEqual(recorder.kwargs, {})

    def test_memory_baseline_adopt_groups_the_two_branches_and_gates_on_drift(self) -> None:
        recorder = self.invoke(
            "memory_baseline_adopt",
            "agents_remember.mcp.registration.memory.memory_baseline_adopt_payload",
            {
                "repo_id": "agents-remember",
                "source_branch": "main",
                "work_branch": "task/260731",
            },
        )

        self.assertEqual(recorder.args, (self.config, "agents-remember"))
        self.assertIs(recorder.kwargs["accept_drift"], False)
        self.assertIs(recorder.kwargs["dry_run"], False)
        branches = recorder.kwargs["branches"]
        self.assertEqual(branches.source_branch, "main")
        self.assertEqual(branches.work_branch, "task/260731")

    def test_memory_carryover_plan_packs_the_selection(self) -> None:
        """Every ref the plan compares travels in one ``CarryoverSelection``; the plan is
        non-mutating so nothing else is passed."""
        recorder = self.invoke(
            "memory_carryover_plan",
            "agents_remember.mcp.registration.memory.memory_carryover_plan_payload",
            {
                "repo_id": "agents-remember",
                "source_memory": "memory/task",
                "official_code_ref": "main",
                "source_code_ref": "task/260731",
                "old_base": "abc123",
                "replace_existing": True,
            },
        )

        config, selection = recorder.args
        self.assertIs(config, self.config)
        self.assertEqual(selection.repo_id, "agents-remember")
        self.assertEqual(selection.source_memory, "memory/task")
        self.assertEqual(selection.official_code_ref, "main")
        self.assertEqual(selection.source_code_ref, "task/260731")
        self.assertEqual(selection.old_base, "abc123")
        self.assertIs(selection.replace_existing, True)
        self.assertEqual(recorder.kwargs, {})

    def test_memory_carryover_apply_carries_the_intent_note_and_default_messages(self) -> None:
        recorder = self.invoke(
            "memory_carryover_apply",
            "agents_remember.mcp.registration.memory.memory_carryover_apply_payload",
            {
                "repo_id": "agents-remember",
                "source_memory": "memory/task",
                "official_code_ref": "main",
                "source_code_ref": "task/260731",
                "old_base": "abc123",
                "intent_note": "code landed on main",
            },
        )

        _config, selection = recorder.args
        self.assertEqual(selection.old_base, "abc123")
        self.assertIs(selection.replace_existing, False)
        self.assertEqual(recorder.kwargs["intent_note"], "code landed on main")
        self.assertEqual(recorder.kwargs["include_review_required"], None)
        messages = recorder.kwargs["messages"]
        self.assertEqual(messages.memory, "Carry over landed branch memory")
        self.assertEqual(messages.ledger, "Record branch memory carryover")

    def test_memory_carryover_apply_forwards_caller_commit_messages(self) -> None:
        recorder = self.invoke(
            "memory_carryover_apply",
            "agents_remember.mcp.registration.memory.memory_carryover_apply_payload",
            {
                "repo_id": "agents-remember",
                "source_memory": "memory/task",
                "official_code_ref": "main",
                "source_code_ref": "task/260731",
                "old_base": "abc123",
                "intent_note": "note",
                "include_review_required": ["onboarding/a.md"],
                "memory_commit_message": "memory msg",
                "ledger_commit_message": "ledger msg",
            },
        )

        self.assertEqual(recorder.kwargs["include_review_required"], ["onboarding/a.md"])
        messages = recorder.kwargs["messages"]
        self.assertEqual((messages.memory, messages.ledger), ("memory msg", "ledger msg"))

    # ---- providers -------------------------------------------------------------

    def test_provider_status_defaults_to_twenty_detail_rows(self) -> None:
        recorder = self.invoke(
            "provider_status",
            "agents_remember.mcp.registration.providers.provider_status_payload",
        )

        self.assertEqual(recorder.args, (self.config,))
        self.assertEqual(recorder.kwargs, {"detail_limit": 20})

    def test_provider_diagnostics_forwards_the_detail_limit(self) -> None:
        recorder = self.invoke(
            "provider_diagnostics",
            "agents_remember.mcp.registration.providers.provider_diagnostics_payload",
            {"detail_limit": 3},
        )

        self.assertEqual(recorder.kwargs, {"detail_limit": 3})

    def test_provider_watchers_runs_for_real_unless_previewed(self) -> None:
        recorder = self.invoke(
            "provider_watchers",
            "agents_remember.mcp.registration.providers.provider_watchers_payload",
            {"action": "restart"},
        )

        self.assertEqual(recorder.args, (self.config,))
        self.assertEqual(recorder.kwargs, {"action": "restart", "dry_run": False})

    # ---- code search -----------------------------------------------------------

    def test_grepai_search_splits_query_repo_scope_and_execution_scope(self) -> None:
        recorder = self.invoke(
            "grepai_search",
            "agents_remember.mcp.registration.code_search.grepai_search_payload",
            {
                "query": "worktree contract",
                "limit": 3,
                "output_format": "toon",
                "repo_ids": ["agents-remember"],
                "all_repos": False,
                "worktree": "efa-l2",
                "dry_run": True,
                "timeout": 45,
            },
        )

        config, query = recorder.args
        self.assertIs(config, self.config)
        self.assertEqual(query.query, "worktree contract")
        self.assertEqual(query.limit, 3)
        self.assertEqual(query.output_format, "toon")
        repos = recorder.kwargs["repos"]
        self.assertEqual(repos.repo_ids, ["agents-remember"])
        self.assertIs(repos.all_repos, False)
        scope = recorder.kwargs["scope"]
        self.assertEqual(scope.worktree, "efa-l2")
        self.assertIs(scope.dry_run, True)
        self.assertEqual(scope.timeout, 45)

    def test_grepai_trace_carries_the_trace_action_and_depth(self) -> None:
        recorder = self.invoke(
            "grepai_trace",
            "agents_remember.mcp.registration.code_search.grepai_trace_payload",
            {"trace_action": "graph", "symbol": "create_server", "depth": 2},
        )

        _config, query = recorder.args
        self.assertEqual(query.trace_action, "graph")
        self.assertEqual(query.symbol, "create_server")
        self.assertEqual(query.depth, 2)
        self.assertEqual(query.output_format, "json")
        self.assertEqual(recorder.kwargs["scope"].worktree, None)

    def test_cgc_symbol_search_passes_repo_and_name_positionally(self) -> None:
        recorder = self.invoke(
            "cgc_symbol_search",
            "agents_remember.mcp.registration.code_search.cgc_symbol_search_payload",
            {"repo_id": "agents-remember", "name": "TaskRef", "worktree": "efa-l2"},
        )

        self.assertEqual(recorder.args, (self.config, "agents-remember", "TaskRef"))
        self.assertEqual(recorder.kwargs["scope"].worktree, "efa-l2")

    def test_cgc_callers_forwards_the_disambiguating_file(self) -> None:
        recorder = self.invoke(
            "cgc_callers",
            "agents_remember.mcp.registration.code_search.cgc_callers_payload",
            {
                "repo_id": "agents-remember",
                "function": "measure",
                "file": "diff_coverage.py",
            },
        )

        self.assertEqual(recorder.args, (self.config, "agents-remember", "measure"))
        self.assertEqual(recorder.kwargs["file"], "diff_coverage.py")

    def test_cgc_callees_takes_no_file_disambiguator(self) -> None:
        recorder = self.invoke(
            "cgc_callees",
            "agents_remember.mcp.registration.code_search.cgc_callees_payload",
            {"repo_id": "agents-remember", "function": "measure", "timeout": 30},
        )

        self.assertEqual(recorder.args, (self.config, "agents-remember", "measure"))
        self.assertEqual(set(recorder.kwargs), {"scope"})
        self.assertEqual(recorder.kwargs["scope"].timeout, 30)

    def test_cgc_dependencies_passes_the_module(self) -> None:
        recorder = self.invoke(
            "cgc_dependencies",
            "agents_remember.mcp.registration.code_search.cgc_dependencies_payload",
            {"repo_id": "agents-remember", "module": "agents_remember.mcp.server"},
        )

        self.assertEqual(
            recorder.args, (self.config, "agents-remember", "agents_remember.mcp.server")
        )

    def test_cgc_complexity_reports_the_whole_repo_when_no_function_is_named(self) -> None:
        recorder = self.invoke(
            "cgc_complexity",
            "agents_remember.mcp.registration.code_search.cgc_complexity_payload",
            {"repo_id": "agents-remember"},
        )

        self.assertEqual(recorder.args, (self.config, "agents-remember"))
        self.assertEqual(recorder.kwargs["function"], None)

    def test_cgc_visualize_serves_on_port_8000_by_default(self) -> None:
        recorder = self.invoke(
            "cgc_visualize",
            "agents_remember.mcp.registration.code_search.cgc_visualize_payload",
            {"repo_id": "agents-remember"},
        )

        self.assertEqual(recorder.args, (self.config, "agents-remember"))
        self.assertEqual(recorder.kwargs["port"], 8000)
        self.assertEqual(recorder.kwargs["context"], None)

    # ---- worktrees -------------------------------------------------------------

    def test_worktree_start_splits_identity_bases_and_execution(self) -> None:
        """Who the task is, what it is cut from, and how the start runs -- the three
        parameter objects the controller takes, each carrying its own arguments."""
        recorder = self.invoke(
            "worktree_start",
            "agents_remember.mcp.registration.worktrees.worktree_start_payload",
            {
                "repo_id": "agents-remember",
                "task_name": "260731-EFA",
                "worktree_name": "efa-l2",
                "leaf_id": "260731-EFA-L2",
                "parent_task": "260731-EFA-MASTER",
                "workflow_kind": "chat-task",
                "source_branch": "main",
                "work_branch": "task/260731",
                "memory_mode": "internal",
                "memory_choice": "reuse",
                "stale_base_choice": "rebase",
                "dry_run": True,
                "skip_provider_setup": True,
                "retry_provider_setup": True,
            },
        )

        config, identity = recorder.args
        self.assertIs(config, self.config)
        self.assertEqual(identity.repo_id, "agents-remember")
        self.assertEqual(identity.task_name, "260731-EFA")
        self.assertEqual(identity.worktree_name, "efa-l2")
        self.assertEqual(identity.leaf_id, "260731-EFA-L2")
        self.assertEqual(identity.parent_task, "260731-EFA-MASTER")
        self.assertEqual(identity.workflow_kind, "chat-task")

        bases = recorder.kwargs["bases"]
        self.assertEqual(bases.source_branch, "main")
        self.assertEqual(bases.work_branch, "task/260731")
        self.assertEqual(bases.memory_mode, "internal")
        self.assertEqual(bases.memory_choice, "reuse")
        self.assertEqual(bases.stale_base_choice, "rebase")

        execution = recorder.kwargs["execution"]
        self.assertIs(execution.dry_run, True)
        self.assertIs(execution.skip_provider_setup, True)
        self.assertIs(execution.retry_provider_setup, True)

    def test_worktree_start_defaults_to_a_real_light_task_start(self) -> None:
        recorder = self.invoke(
            "worktree_start",
            "agents_remember.mcp.registration.worktrees.worktree_start_payload",
            {
                "repo_id": "agents-remember",
                "task_name": "260731-EFA",
                "worktree_name": "efa-l2",
            },
        )

        _config, identity = recorder.args
        self.assertEqual(identity.workflow_kind, "light-task")
        execution = recorder.kwargs["execution"]
        self.assertEqual(
            [execution.dry_run, execution.skip_provider_setup, execution.retry_provider_setup],
            [False, False, False],
        )

    def test_worktree_attach_locates_the_task_by_reference(self) -> None:
        recorder = self.invoke(
            "worktree_attach",
            "agents_remember.mcp.registration.worktrees.worktree_attach_payload",
            {"repo_id": "agents-remember", "contract_path": "/tmp/contract.yaml"},
        )

        _config, task_ref = recorder.args
        self.assertEqual(task_ref.repo_id, "agents-remember")
        self.assertEqual(task_ref.contract_path, "/tmp/contract.yaml")

    def test_worktree_status_locates_the_task_by_reference(self) -> None:
        recorder = self.invoke(
            "worktree_status",
            "agents_remember.mcp.registration.worktrees.worktree_status_payload",
            {"repo_id": "agents-remember", "leaf_id": "260731-EFA-L2"},
        )

        _config, task_ref = recorder.args
        self.assertEqual(task_ref.repo_id, "agents-remember")
        self.assertEqual(task_ref.leaf_id, "260731-EFA-L2")

    def test_worktree_sync_takes_the_contract_path_directly(self) -> None:
        recorder = self.invoke(
            "worktree_sync",
            "agents_remember.mcp.registration.worktrees.worktree_sync_payload",
            {"contract_path": "/tmp/contract.yaml", "memory_sync_choice": "merge-memory"},
        )

        self.assertEqual(recorder.args, (self.config, "/tmp/contract.yaml"))
        self.assertEqual(recorder.kwargs, {"memory_sync_choice": "merge-memory", "dry_run": False})

    # ---- closeout --------------------------------------------------------------

    def test_closeout_preview_groups_the_three_commit_messages(self) -> None:
        recorder = self.invoke(
            "worktree_closeout_preview",
            "agents_remember.mcp.registration.closeout.worktree_closeout_preview_payload",
            {
                "contract_path": "/tmp/contract.yaml",
                "code_commit_message": "code msg",
                "memory_commit_message": "memory msg",
                "ledger_commit_message": "ledger msg",
            },
        )

        config, contract_path, messages = recorder.args
        self.assertIs(config, self.config)
        self.assertEqual(contract_path, "/tmp/contract.yaml")
        self.assertEqual(messages.code, "code msg")
        self.assertEqual(messages.memory, "memory msg")
        self.assertEqual(messages.ledger, "ledger msg")

    def test_closeout_apply_keeps_the_approval_separate_from_the_messages(self) -> None:
        """``CloseoutApproval`` is the gate-bearing half: the intent note and whether this
        is a preview. Folding it into the commit messages would let a dry run read as an
        approved apply."""
        recorder = self.invoke(
            "worktree_closeout_apply",
            "agents_remember.mcp.registration.closeout.worktree_closeout_apply_payload",
            {
                "contract_path": "/tmp/contract.yaml",
                "code_commit_message": "code msg",
                "intent_note": "approved by developer",
                "dry_run": True,
            },
        )

        config, contract_path, messages, approval = recorder.args
        self.assertIs(config, self.config)
        self.assertEqual(contract_path, "/tmp/contract.yaml")
        self.assertEqual(messages.code, "code msg")
        self.assertEqual(approval.intent_note, "approved by developer")
        self.assertIs(approval.dry_run, True)

    def test_worktree_integrate_defaults_to_a_fast_forward_only_landing(self) -> None:
        recorder = self.invoke(
            "worktree_integrate",
            "agents_remember.mcp.registration.closeout.worktree_integrate_payload",
            {"contract_path": "/tmp/contract.yaml"},
        )

        self.assertEqual(recorder.args, (self.config, "/tmp/contract.yaml"))
        self.assertEqual(recorder.kwargs["strategy"], "ff-only")
        self.assertIs(recorder.kwargs["dry_run"], False)

    def test_worktree_cleanup_tears_down_providers_by_default(self) -> None:
        recorder = self.invoke(
            "worktree_cleanup",
            "agents_remember.mcp.registration.closeout.worktree_cleanup_payload",
            {"contract_path": "/tmp/contract.yaml"},
        )

        self.assertEqual(recorder.args, (self.config, "/tmp/contract.yaml"))
        self.assertEqual(recorder.kwargs, {"dry_run": False, "teardown_providers": True})

    def test_worktree_abandon_refuses_dirty_worktrees_unless_forced(self) -> None:
        recorder = self.invoke(
            "worktree_abandon",
            "agents_remember.mcp.registration.closeout.worktree_abandon_payload",
            {"contract_path": "/tmp/contract.yaml"},
        )

        self.assertEqual(recorder.args, (self.config, "/tmp/contract.yaml"))
        self.assertEqual(recorder.kwargs, {"dry_run": False, "force": False})

    # ---- tasks -----------------------------------------------------------------

    def test_task_reopen_takes_the_contract_path_and_a_preview_flag(self) -> None:
        recorder = self.invoke(
            "task_reopen",
            "agents_remember.mcp.registration.tasks.task_reopen_payload",
            {"contract_path": "/tmp/contract.yaml", "dry_run": True},
        )

        self.assertEqual(recorder.args, (self.config, "/tmp/contract.yaml"))
        self.assertEqual(recorder.kwargs, {"dry_run": True})

    def test_lifecycle_finalize_task_groups_the_documents_it_ticks(self) -> None:
        recorder = self.invoke(
            "lifecycle_finalize_task",
            "agents_remember.mcp.registration.tasks.lifecycle_finalize_task_payload",
            {
                "contract_path": "/tmp/contract.yaml",
                "task_doc_path": "/tmp/leaf.json",
                "master_doc_path": "/tmp/master.json",
                "subtask_number": "2",
            },
        )

        self.assertEqual(recorder.args, (self.config, "/tmp/contract.yaml"))
        docs = recorder.kwargs["docs"]
        self.assertEqual(docs.task_doc_path, "/tmp/leaf.json")
        self.assertEqual(docs.master_doc_path, "/tmp/master.json")
        self.assertEqual(docs.subtask_number, "2")
        self.assertIs(recorder.kwargs["dry_run"], False)
        self.assertIs(recorder.kwargs["teardown_providers"], True)

    def test_task_doc_splits_the_document_target_from_the_edit(self) -> None:
        """Which document to edit is a different question from what the edit is; the two
        parameter objects keep the locator fields out of the edit payload."""
        recorder = self.invoke(
            "task_doc",
            "agents_remember.mcp.registration.tasks.task_doc_payload",
            {
                "repo_id": "agents-remember",
                "operation": "set_step",
                "task_name": "260731-EFA",
                "contract_path": "/tmp/contract.yaml",
                "slug": "efa",
                "step": {"id": "1", "title": "do it", "status": "done"},
                "fields": {"status": "in-progress"},
                "decision": {"at": "2026-07-31", "decision": "d", "rationale": "r"},
                "subtask": {"number": "2", "name": "leaf"},
                "section": {"heading": "Notes"},
                "dry_run": True,
            },
        )

        config, target = recorder.args
        self.assertIs(config, self.config)
        self.assertEqual(target.repo_id, "agents-remember")
        self.assertEqual(target.task_name, "260731-EFA")
        self.assertEqual(target.contract_path, "/tmp/contract.yaml")
        self.assertEqual(target.slug, "efa")

        self.assertEqual(recorder.kwargs["operation"], "set_step")
        self.assertIs(recorder.kwargs["dry_run"], True)
        edit = recorder.kwargs["edit"]
        self.assertEqual(edit.step, {"id": "1", "title": "do it", "status": "done"})
        self.assertEqual(edit.fields, {"status": "in-progress"})
        self.assertEqual(edit.decision, {"at": "2026-07-31", "decision": "d", "rationale": "r"})
        self.assertEqual(edit.subtask, {"number": "2", "name": "leaf"})
        self.assertEqual(edit.section, {"heading": "Notes"})

    def test_task_doc_leaves_every_edit_slot_unset_for_a_read(self) -> None:
        recorder = self.invoke(
            "task_doc",
            "agents_remember.mcp.registration.tasks.task_doc_payload",
            {"repo_id": "agents-remember", "operation": "get"},
        )

        edit = recorder.kwargs["edit"]
        self.assertEqual(
            [edit.fields, edit.step, edit.decision, edit.subtask, edit.section],
            [None, None, None, None, None],
        )

    # ---- benchmarks ------------------------------------------------------------

    def test_codex_benchmark_prepare_defaults_to_a_preview(self) -> None:
        """ "Defaults to dry_run=true because a real prepare clones repos" -- the default
        the docstring promises is on the preparation object, not lost in the wiring."""
        recorder = self.invoke(
            "codex_benchmark_prepare",
            "agents_remember.mcp.registration.benchmarks.codex_benchmark_prepare_payload",
        )

        self.assertEqual(recorder.args, (self.config,))
        selection = recorder.kwargs["selection"]
        self.assertEqual(selection.target, "all")
        self.assertEqual(selection.case_id, None)
        preparation = recorder.kwargs["preparation"]
        self.assertIs(preparation.dry_run, True)
        self.assertIs(preparation.force_clone, False)
        self.assertEqual(preparation.skill_exposure_mode, "copy")
        self.assertEqual(preparation.provider_timeout, 1800)

    def test_codex_benchmark_run_defaults_to_a_preview_in_codex_own_sandbox(self) -> None:
        recorder = self.invoke(
            "codex_benchmark_run",
            "agents_remember.mcp.registration.benchmarks.codex_benchmark_run_payload",
            {"case_id": "case-1"},
        )

        self.assertEqual(recorder.kwargs["selection"].case_id, "case-1")
        self.assertIs(recorder.kwargs["preparation"].dry_run, True)
        self.assertEqual(recorder.kwargs["run"].codex_sandbox, "default")

    def test_codex_benchmark_run_forwards_the_execution_knobs(self) -> None:
        recorder = self.invoke(
            "codex_benchmark_run",
            "agents_remember.mcp.registration.benchmarks.codex_benchmark_run_payload",
            {
                "target": "case",
                "case_id": "case-1",
                "prompt": "do the thing",
                "variant": "b",
                "repetitions": 3,
                "jobs": 2,
                "dry_run": False,
                "skip_prepare": True,
                "codex_sandbox": "danger-full-access",
            },
        )

        self.assertIs(recorder.kwargs["preparation"].dry_run, False)
        run = recorder.kwargs["run"]
        self.assertEqual(run.prompt, "do the thing")
        self.assertEqual(run.variant, "b")
        self.assertEqual(run.repetitions, 3)
        self.assertEqual(run.jobs, 2)
        self.assertIs(run.skip_prepare, True)
        self.assertEqual(run.codex_sandbox, "danger-full-access")

    # ---- lifecycle -------------------------------------------------------------

    def test_lifecycle_start_acts_on_the_ambient_lifecycle_not_the_config(self) -> None:
        """The registrar takes the config only to keep one signature; these six payloads
        act on the process-wide ambient lifecycle and must be called without it."""
        recorder = self.invoke(
            "lifecycle_start",
            "agents_remember.mcp.registration.lifecycle.lifecycle_start_payload",
        )

        self.assertEqual(recorder.args, ())
        self.assertEqual(recorder.kwargs, {})

    def test_lifecycle_resume_takes_no_arguments(self) -> None:
        recorder = self.invoke(
            "lifecycle_resume",
            "agents_remember.mcp.registration.lifecycle.lifecycle_resume_payload",
        )

        self.assertEqual(recorder.args, ())

    def test_lifecycle_turn_end_notification_forwards_the_summary(self) -> None:
        recorder = self.invoke(
            "lifecycle_turn_end_notification",
            "agents_remember.mcp.registration.lifecycle.lifecycle_turn_end_notification_payload",
            {"summary": "turn done"},
        )

        self.assertEqual(recorder.args, ("turn done",))

    def test_lifecycle_end_forwards_the_outcome(self) -> None:
        recorder = self.invoke(
            "lifecycle_end",
            "agents_remember.mcp.registration.lifecycle.lifecycle_end_payload",
            {"outcome": "completed"},
        )

        self.assertEqual(recorder.args, ("completed",))

    def test_switch_lifecycle_defaults_to_no_unsaved_answer(self) -> None:
        recorder = self.invoke(
            "switch_lifecycle",
            "agents_remember.mcp.registration.lifecycle.switch_lifecycle_payload",
        )

        self.assertEqual(recorder.args, (None,))

    def test_switch_lifecycle_forwards_the_unsaved_answer(self) -> None:
        recorder = self.invoke(
            "switch_lifecycle",
            "agents_remember.mcp.registration.lifecycle.switch_lifecycle_payload",
            {"on_unsaved": "save"},
        )

        self.assertEqual(recorder.args, ("save",))

    def test_lifecycle_phase_forwards_the_phase(self) -> None:
        recorder = self.invoke(
            "lifecycle_phase",
            "agents_remember.mcp.registration.lifecycle.lifecycle_phase_payload",
            {"phase": "build"},
        )

        self.assertEqual(recorder.args, ("build",))

    # ---- gates -----------------------------------------------------------------

    def test_lifecycle_gate_packs_the_anchor_and_the_request_and_blocks_by_default(
        self,
    ) -> None:
        recorder = self.invoke(
            "lifecycle_gate",
            "agents_remember.mcp.registration.gates.lifecycle_gate_payload",
            {
                "kind": "plan-approval",
                "ask": {"kind": "decision", "question": "ship?"},
                "lifecycle_id": "life-1",
                "enclosure": "260731-EFA-MASTER",
                "repo_id": "agents-remember",
                "packet": {"summary": "the plan"},
                "required_decision": ["approve", "reject"],
                "evidence_refs": [{"path": "plan.md"}],
            },
        )

        config, raise_request = recorder.args
        self.assertIs(config, self.config)
        self.assertEqual(raise_request.kind, "plan-approval")
        self.assertEqual(raise_request.ask, {"kind": "decision", "question": "ship?"})
        self.assertEqual(raise_request.anchor.lifecycle_id, "life-1")
        self.assertEqual(raise_request.anchor.enclosure, "260731-EFA-MASTER")
        self.assertEqual(raise_request.anchor.repo_id, "agents-remember")
        self.assertEqual(raise_request.request.packet, {"summary": "the plan"})
        self.assertEqual(raise_request.request.required_decision, ["approve", "reject"])
        self.assertEqual(raise_request.request.evidence_refs, [{"path": "plan.md"}])
        self.assertIs(recorder.kwargs["wait"].block, True)

    def test_lifecycle_gate_wait_false_raises_without_a_timeout(self) -> None:
        """A non-blocking raise has nothing to time out; the wait it passes must say so
        rather than inheriting ``GateWait``'s blocking default of 300 seconds."""
        recorder = self.invoke(
            "lifecycle_gate",
            "agents_remember.mcp.registration.gates.lifecycle_gate_payload",
            {"kind": "master-handover-approval", "wait": False},
        )

        wait = recorder.kwargs["wait"]
        self.assertIs(wait.block, False)
        self.assertEqual(wait.timeout_seconds, None)

    def test_gate_decide_attributes_a_plain_decision_to_the_model_via_cli(self) -> None:
        recorder = self.invoke(
            "gate_decide",
            "agents_remember.mcp.registration.gates.gate_decide_payload",
            {"gate_id": "gate-1", "decision": "approve", "note": "looks right"},
        )

        self.assertEqual(recorder.args, (self.config,))
        self.assertEqual(recorder.kwargs["gate_id"], "gate-1")
        verdict = recorder.kwargs["verdict"]
        self.assertEqual(verdict.decision, "approve")
        self.assertEqual(verdict.by, "model")
        self.assertEqual(verdict.via, "cli")
        self.assertEqual(verdict.note, "looks right")
        self.assertEqual(verdict.deciding_role, None)

    def test_gate_decide_with_a_deciding_role_attributes_via_orchestration(self) -> None:
        """A role-attributed decision is checked against the gate policy, so it must not
        arrive claiming to be the model's own cli decision: ``by`` is left for the server
        to fill from the role and ``via`` becomes orchestration."""
        recorder = self.invoke(
            "gate_decide",
            "agents_remember.mcp.registration.gates.gate_decide_payload",
            {
                "gate_id": "gate-1",
                "decision": "approve",
                "deciding_role": "manager",
                "evidence_refs": [{"path": "review.md"}],
            },
        )

        verdict = recorder.kwargs["verdict"]
        self.assertEqual(verdict.deciding_role, "manager")
        self.assertEqual(verdict.via, "orchestration")
        self.assertEqual(verdict.by, "")
        self.assertEqual(recorder.kwargs["evidence_refs"], [{"path": "review.md"}])

    def test_gate_list_defaults_to_the_ambient_lifecycle(self) -> None:
        recorder = self.invoke(
            "gate_list", "agents_remember.mcp.registration.gates.gate_list_payload"
        )

        self.assertEqual(recorder.args, (self.config,))
        self.assertEqual(recorder.kwargs, {"lifecycle_id": None})

    # ---- orchestration ---------------------------------------------------------

    def test_operator_inbox_post_splits_address_message_poster_and_delivery(self) -> None:
        recorder = self.invoke(
            "operator_inbox_post",
            "agents_remember.mcp.registration.orchestration.operator_inbox_post_payload",
            {
                "ask": "status?",
                "response": "green",
                "lifecycle_id": "life-1",
                "agent_id": "agent-1",
                "gate_id": "gate-1",
                "sender_agent_id": "agent-0",
                "sender_role": "manager",
                "recipient_role": "worker",
                "message_kind": "dispatch-brief",
                "artifact_path": "/tmp/brief.md",
            },
        )

        self.assertEqual(recorder.args, (self.config,))
        address = recorder.kwargs["address"]
        self.assertEqual(address.lifecycle_id, "life-1")
        self.assertEqual(address.agent_id, "agent-1")
        self.assertEqual(address.recipient_role, "worker")

        message = recorder.kwargs["message"]
        self.assertEqual(message.ask, "status?")
        self.assertEqual(message.response, "green")
        self.assertEqual(message.message_kind, "dispatch-brief")
        self.assertEqual(message.gate_id, "gate-1")
        self.assertEqual(message.artifact_path, "/tmp/brief.md")

        poster = recorder.kwargs["poster"]
        self.assertEqual(poster.sender_agent_id, "agent-0")
        self.assertEqual(poster.sender_role, "manager")
        self.assertIs(recorder.kwargs["delivery"].enabled, True)

    def test_operator_inbox_post_over_mcp_is_always_attributed_to_the_model(self) -> None:
        """The docstring is explicit that this route is the model's: attribution is fixed
        here, not taken from the caller, so an agent cannot post as the developer."""
        recorder = self.invoke(
            "operator_inbox_post",
            "agents_remember.mcp.registration.orchestration.operator_inbox_post_payload",
            {"ask": "status?", "response": "green", "agent_id": "agent-1"},
        )

        poster = recorder.kwargs["poster"]
        self.assertEqual(poster.created_by, "model")
        self.assertEqual(poster.created_via, "cli")

    def test_operator_inbox_post_can_opt_out_of_hosted_push_delivery(self) -> None:
        recorder = self.invoke(
            "operator_inbox_post",
            "agents_remember.mcp.registration.orchestration.operator_inbox_post_payload",
            {
                "ask": "status?",
                "response": "green",
                "agent_id": "agent-1",
                "deliver_to_hosted": False,
            },
        )

        self.assertIs(recorder.kwargs["delivery"].enabled, False)

    def test_operator_inbox_poll_forwards_every_mailbox_key(self) -> None:
        recorder = self.invoke(
            "operator_inbox_poll",
            "agents_remember.mcp.registration.orchestration.operator_inbox_poll_payload",
            {"lifecycle_id": "life-1", "agent_id": "agent-1", "recipient_role": "worker"},
        )

        self.assertEqual(recorder.args, (self.config,))
        self.assertEqual(
            recorder.kwargs,
            {"lifecycle_id": "life-1", "agent_id": "agent-1", "recipient_role": "worker"},
        )

    def test_operator_inbox_consume_records_the_model_as_the_consumer(self) -> None:
        recorder = self.invoke(
            "operator_inbox_consume",
            "agents_remember.mcp.registration.orchestration.operator_inbox_consume_payload",
            {"entry_id": "entry-1"},
        )

        self.assertEqual(
            recorder.kwargs,
            {"entry_id": "entry-1", "consumed_by": "model", "consumed_via": "cli"},
        )

    def test_orchestration_nudge_manager_separates_target_from_subject(self) -> None:
        """The manager being nudged and the seat the nudge is about are different agents;
        collapsing them would nudge the wrong mailbox."""
        recorder = self.invoke(
            "orchestration_nudge_manager",
            "agents_remember.mcp.registration.orchestration.orchestration_nudge_manager_payload",
            {
                "reason": "inactive",
                "subject": "worker stalled",
                "manager_agent_id": "agent-manager",
                "manager_lifecycle_id": "life-manager",
                "subject_agent_id": "agent-worker",
                "subject_lifecycle_id": "life-worker",
                "artifact_path": "/tmp/report.md",
            },
        )

        self.assertEqual(recorder.args, (self.config,))
        self.assertEqual(recorder.kwargs["reason"], "inactive")
        target = recorder.kwargs["target"]
        self.assertEqual(target.agent_id, "agent-manager")
        self.assertEqual(target.lifecycle_id, "life-manager")
        subject = recorder.kwargs["subject"]
        self.assertEqual(subject.subject, "worker stalled")
        self.assertEqual(subject.agent_id, "agent-worker")
        self.assertEqual(subject.lifecycle_id, "life-worker")
        self.assertEqual(subject.artifact_path, "/tmp/report.md")

    def test_orchestration_nudge_manager_rate_limits_to_fifteen_minutes_by_default(
        self,
    ) -> None:
        recorder = self.invoke(
            "orchestration_nudge_manager",
            "agents_remember.mcp.registration.orchestration.orchestration_nudge_manager_payload",
            {"reason": "missing-turn-report", "subject": "no report"},
        )

        self.assertEqual(recorder.kwargs["rate_limit_seconds"], 900)


if __name__ == "__main__":
    unittest.main()
