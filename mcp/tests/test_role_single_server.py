"""Rule 6: a role agent carries the product's tools once (MIK-R112, Architect ruling).

For each harness the product launches, the role start keeps the product's other
server out of that agent at the launch, with what the harness offers for one
launch. No configuration file is changed and no server of the user is kept out.
``pi`` filters tools, ``claude`` denies them (both spellings; task-server tools
proven non-matching), ``codex`` disables the one server with the unquoted dotted
key plus a never-spawned command (safe with and without the server configured;
user servers stay enabled), ``eve`` is host-owned.
"""

from __future__ import annotations

import fnmatch
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents_remember.application.agent_binding import (
    OTHER_PRODUCT_TOOL_SERVER,
    harness_carries_one_server,
    harness_tool_exclusion_argv,
    launch_server_exclusion,
    with_server_exclusion,
)
from agents_remember.application.role_launch_context import RoleLaunchContext
from agents_remember.cli.paseo_launch import (
    RoleLaunch,
    agent_create_input,
    applied_to_agent,
    build_launch_call,
    exclusion_applied_agent,
)
from agents_remember.cli.role_launch_preparation import (
    _AR_OTHER_SERVER_CHOICE,
    _AR_TOOL_SERVER_USAGE,
    _ar_tool_server_usage,
)

_TASK_TOOL = "mcp__agents-remember-task__server_info"
_TASK_TOOL_UNDERSCORED = "mcp__agents_remember_task__server_info"


class SingleServerTests(unittest.TestCase):
    def test_only_the_product_other_server_is_named(self) -> None:
        self.assertEqual(OTHER_PRODUCT_TOOL_SERVER, "agents-remember")

    def test_exclusion_argv_per_harness(self) -> None:
        pi = harness_tool_exclusion_argv("pi")
        self.assertEqual(pi[0], "--exclude-tools")
        self.assertIn("mcp__agents-remember__*", pi[1])
        claude = harness_tool_exclusion_argv("claude")
        self.assertEqual(claude[0], "--disallowedTools")
        self.assertIn("mcp__agents-remember__*", claude[1])
        self.assertIn("mcp__agents_remember__*", claude[1])
        codex = harness_tool_exclusion_argv("codex")
        self.assertEqual(
            codex,
            (
                "-c",
                "mcp_servers.agents-remember.enabled=false",
                "-c",
                'mcp_servers.agents-remember.command="/bin/true"',
            ),
        )
        for harness in ("eve", "custom-unknown", None):
            self.assertEqual(harness_tool_exclusion_argv(harness), (), str(harness))

    def test_exclusion_patterns_never_match_the_task_server(self) -> None:
        for harness in ("pi", "claude"):
            patterns = harness_tool_exclusion_argv(harness)[1].split(",")
            for tool in (_TASK_TOOL, _TASK_TOOL_UNDERSCORED):
                self.assertFalse(
                    any(fnmatch.fnmatchcase(tool, pattern) for pattern in patterns),
                    f"{harness}:{tool}",
                )
        patterns = harness_tool_exclusion_argv("pi")[1].split(",")
        self.assertTrue(
            any(
                fnmatch.fnmatchcase("mcp__agents-remember__server_info", pattern)
                for pattern in patterns
            )
        )

    def test_one_server_mapping(self) -> None:
        for harness in ("pi", "claude", "codex", "eve"):
            self.assertTrue(harness_carries_one_server(harness), harness)
        for harness in ("custom-unknown", None):
            self.assertFalse(harness_carries_one_server(harness), str(harness))

    def test_spawn_args_gain_exclusion_and_keep_caller_args(self) -> None:
        self.assertEqual(
            with_server_exclusion("pi", ["--model", "x"]),
            ["--model", "x", "--exclude-tools", "mcp__agents-remember__*,mcp__agents_remember__*"],
        )
        self.assertIsNone(with_server_exclusion("custom-unknown", None))

    def test_caller_filters_merge_instead_of_bypassing(self) -> None:
        """MIK-R112 F07: an unrelated caller filter cannot bypass the exclusion."""
        merged = with_server_exclusion("pi", ["--exclude-tools", "ask_question"])
        self.assertEqual(
            merged,
            ["--exclude-tools", "ask_question,mcp__agents-remember__*,mcp__agents_remember__*"],
        )
        merged_claude = with_server_exclusion("claude", ["--disallowedTools", "Bash"])
        self.assertEqual(
            merged_claude,
            ["--disallowedTools", "Bash,mcp__agents-remember__*,mcp__agents_remember__*"],
        )
        merged_eq = with_server_exclusion("pi", ["--exclude-tools=mine"])
        self.assertEqual(
            merged_eq, ["--exclude-tools=mine,mcp__agents-remember__*,mcp__agents_remember__*"]
        )
        # Caller args are never dropped or reordered around the merge.
        self.assertEqual(
            with_server_exclusion("pi", ["--model", "x", "--exclude-tools", "ask_question"]),
            [
                "--model",
                "x",
                "--exclude-tools",
                "ask_question,mcp__agents-remember__*,mcp__agents_remember__*",
            ],
        )

    def test_terminal_spawn_argv_per_harness_pins_native_exclusion(self) -> None:
        """MIK-R112 F07: pin what the native spawn argv carries per harness (model-free).

        This is the applied capability the terminal seat-spawn path merges at
        TerminalLaunchRequest knobs: pi/claude filter or deny by argv, codex
        disables by override pair, eve and unknown harnesses pass caller args
        through unchanged (host-owned, or no verified mechanism).
        """
        patterns = "mcp__agents-remember__*,mcp__agents_remember__*"
        self.assertEqual(
            with_server_exclusion("pi", []),
            ["--exclude-tools", patterns],
        )
        self.assertEqual(
            with_server_exclusion("claude", ["--flag", "v"]),
            ["--flag", "v", "--disallowedTools", patterns],
        )
        self.assertEqual(
            with_server_exclusion("codex", None),
            [
                "-c",
                "mcp_servers.agents-remember.enabled=false",
                "-c",
                'mcp_servers.agents-remember.command="/bin/true"',
            ],
        )
        self.assertEqual(with_server_exclusion("eve", ["--whatever"]), ["--whatever"])
        self.assertEqual(with_server_exclusion("custom-unknown", ["--x"]), ["--x"])
        self.assertIsNone(with_server_exclusion(None, None))
        self.assertEqual(
            with_server_exclusion("pi", ["-xt", "mine"]),
            ["-xt", f"mine,{patterns}"],
        )
        self.assertEqual(
            with_server_exclusion("claude", ["--disallowed-tools", "Bash"]),
            ["--disallowed-tools", f"Bash,{patterns}"],
        )

    def test_applied_exclusion_record_per_harness(self) -> None:
        """MIK-R112 F07: the record is what the launch carries, not the harness name."""
        self.assertEqual(
            launch_server_exclusion("pi"),
            {"harness": "pi", "mode": "task-server-only", "servers": ["agents-remember-task"]},
        )
        self.assertEqual(
            launch_server_exclusion("eve"),
            {"harness": "eve", "mode": "host-owned", "servers": ["agents-remember-task"]},
        )
        self.assertIsNone(launch_server_exclusion("custom-unknown"))
        self.assertIsNone(launch_server_exclusion(None))
        self.assertIsNone(launch_server_exclusion("pi", servers=()))

    def test_native_launch_call_carries_task_only_servers_and_the_record(self) -> None:
        """MIK-R112 F07: pin the native host call per harness (model-free)."""
        for harness, mode in (
            ("pi", "task-server-only"),
            ("claude", "task-server-only"),
            ("codex", "task-server-only"),
            ("eve", "host-owned"),
        ):
            context = RoleLaunchContext(
                role="architect", sprint=None, master=None, task=None, effective_task=None
            )
            call = build_launch_call(
                RoleLaunch(
                    agent_id="agent-1",
                    request_id=uuid.uuid4(),
                    context=context,
                    folder="/tmp",
                    provider=harness,
                    session_options={},
                    prompt="hello",
                    report_path="/tmp/report.md",
                    handover_artifact={"path": "/tmp/artifact.md", "sha256": "0" * 64},
                    settings_file=Path("/tmp/settings.json"),
                    accepts_tool_servers=True,
                )
            )
            servers = call["agent"].get("mcpServers", {})
            self.assertEqual(sorted(servers), ["agents-remember-task"], harness)
            self.assertNotIn("agents-remember", servers, harness)
            self.assertEqual(
                call["serverExclusion"],
                {
                    "harness": harness,
                    "mode": mode,
                    "servers": ["agents-remember-task"],
                },
                harness,
            )
        unknown_context = RoleLaunchContext(
            role="architect", sprint=None, master=None, task=None, effective_task=None
        )
        unknown_call = build_launch_call(
            RoleLaunch(
                agent_id="agent-1",
                request_id=uuid.uuid4(),
                context=unknown_context,
                folder="/tmp",
                provider="custom-unknown",
                session_options={},
                prompt="hello",
                report_path="/tmp/report.md",
                handover_artifact={"path": "/tmp/artifact.md", "sha256": "0" * 64},
                settings_file=Path("/tmp/settings.json"),
                accepts_tool_servers=True,
            )
        )
        self.assertNotIn("serverExclusion", unknown_call)

    def test_brief_sentence_only_with_two_servers(self) -> None:
        # MIK-R112 F07: the brief derives from (harness, servers-listed) plus the
        # applied route, never the harness name alone. Host route (default):
        # nothing is applied, so the choice sentence stays everywhere.
        for harness in ("pi", "claude", "codex", "eve", "custom-unknown", None):
            self.assertIn(
                _AR_OTHER_SERVER_CHOICE,
                _ar_tool_server_usage(harness, servers=("agents-remember-task",)),
                str(harness),
            )
        # Terminal route (exclusion applied): the choice sentence leaves only
        # where the record exists; unknown acceptance keeps it (no claim).
        for harness in ("pi", "claude", "codex", "eve"):
            self.assertNotIn(
                _AR_OTHER_SERVER_CHOICE,
                _ar_tool_server_usage(
                    harness, servers=("agents-remember-task",), exclusion_applied=True
                ),
                harness,
            )
        for harness in ("custom-unknown", None):
            self.assertIn(
                _AR_OTHER_SERVER_CHOICE,
                _ar_tool_server_usage(
                    harness, servers=("agents-remember-task",), exclusion_applied=True
                ),
                str(harness),
            )
        self.assertIn(
            _AR_OTHER_SERVER_CHOICE,
            _ar_tool_server_usage("pi", servers=()),
            "no servers listed keeps the sentence",
        )
        # Unknown acceptance claims no exclusion: byte-identical to the old brief.
        self.assertEqual(_ar_tool_server_usage(None), _AR_TOOL_SERVER_USAGE)
        self.assertEqual(_ar_tool_server_usage("pi"), _AR_TOOL_SERVER_USAGE)

    def test_native_agent_create_input_applies_task_server_only(self) -> None:
        """MIK-R112 F07: the sent native input retains the task server and the record."""
        context = RoleLaunchContext(
            role="architect", sprint=None, master=None, task=None, effective_task=None
        )

        def build(harness: str) -> dict:
            return build_launch_call(
                RoleLaunch(
                    agent_id="agent-1",
                    request_id=uuid.uuid4(),
                    context=context,
                    folder="/tmp",
                    provider=harness,
                    session_options={},
                    prompt="hello",
                    report_path="/tmp/report.md",
                    handover_artifact={"path": "/tmp/artifact.md", "sha256": "0" * 64},
                    settings_file=Path("/tmp/settings.json"),
                    accepts_tool_servers=True,
                )
            )

        for harness in ("pi", "claude", "codex"):
            sending = agent_create_input(build(harness), "workspace-1")
            # Retained: our task-server entry crosses; only the other copy is suppressed.
            self.assertIn("agents-remember-task", sending["mcpServers"], harness)
            self.assertEqual(
                sending["serverExclusion"],
                {
                    "harness": harness,
                    "mode": "task-server-only",
                    "servers": ["agents-remember-task"],
                },
                harness,
            )
            self.assertEqual(sending["workspaceId"], "workspace-1", harness)
            self.assertEqual(sending["agentId"], "agent-1", harness)
        eve_sending = agent_create_input(build("eve"), "workspace-1")
        self.assertIn("agents-remember-task", eve_sending["mcpServers"], "eve keeps its copy")
        self.assertEqual(eve_sending["serverExclusion"]["mode"], "host-owned")
        unknown_sending = agent_create_input(build("custom-unknown"), "workspace-1")
        self.assertNotIn("serverExclusion", unknown_sending)
        self.assertIn("agents-remember-task", unknown_sending["mcpServers"])

    def test_exclusion_suppresses_only_the_other_copy(self) -> None:
        """MIK-R112 F07: non-circular fixture — task retained, other removed, user kept."""

        task_entry = {"type": "stdio", "command": "x", "args": [], "env": {}}
        user_entry = {"type": "stdio", "command": "u", "args": ["--x"], "env": {"K": "V"}}
        other_entry = {"type": "stdio", "command": "o", "args": [], "env": {}}
        call = {
            "agent": {
                "agentId": "a",
                "mcpServers": {
                    "agents-remember-task": dict(task_entry),
                    "user-server": dict(user_entry),
                    "agents-remember": dict(other_entry),
                },
            },
            "serverExclusion": {
                "harness": "codex",
                "mode": "task-server-only",
                "servers": ["agents-remember-task"],
            },
        }
        applied = exclusion_applied_agent(call)
        self.assertEqual(applied["mcpServers"]["agents-remember-task"], task_entry)
        self.assertEqual(applied["mcpServers"]["user-server"], user_entry)
        self.assertNotIn("agents-remember", applied["mcpServers"])
        # Without the record the map crosses verbatim.
        verbatim = exclusion_applied_agent({"agent": call["agent"]})
        self.assertEqual(
            verbatim["mcpServers"],
            {
                "agents-remember-task": task_entry,
                "user-server": user_entry,
                "agents-remember": other_entry,
            },
        )

    def test_applied_receipt_derives_from_filtered_agent(self) -> None:
        """MIK-R112 F07: the receipt tool server reflects what crossed the bridge."""

        context = RoleLaunchContext(
            role="architect", sprint=None, master=None, task=None, effective_task=None
        )
        call = build_launch_call(
            RoleLaunch(
                agent_id="agent-1",
                request_id=uuid.uuid4(),
                context=context,
                folder="/tmp",
                provider="codex",
                session_options={},
                prompt="hello",
                report_path="/tmp/report.md",
                handover_artifact={"path": "/tmp/artifact.md", "sha256": "0" * 64},
                settings_file=Path("/tmp/settings.json"),
                accepts_tool_servers=True,
            )
        )
        applied = applied_to_agent(call)
        self.assertTrue(applied["toolServer"]["applied"])
        self.assertEqual(applied["serverExclusion"]["mode"], "task-server-only")


if __name__ == "__main__":
    unittest.main()
