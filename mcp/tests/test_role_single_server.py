"""A role agent started through the host carries the product's tools once (MIK-R132).

The role start hands the host, for the one launch, what keeps the product's other server
``agents-remember`` out of the agent. The pinned host offers that for Claude alone: the
provider options of the create request carry ``disallowedTools``, which the host hands to
the harness. For every other provider nothing is handed over, the record is absent, and
both the role start's answer and the first message say that a second copy is loaded.
No configuration file is changed and no server of the user is kept out. The rules the
user's configuration of the host denies already are handed over again with the product's
rule, because the host lets a request's list take the place of the configured one.
"""

from __future__ import annotations

import copy
import fnmatch
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents_remember.application.agent_binding import (
    CLAUDE_OTHER_SERVER_RULE,
    EXCLUSION_MODE_CLAUDE,
    OTHER_PRODUCT_TOOL_SERVER,
    TOOL_SERVER_NAME,
    exclusion_agent_options,
    launch_server_exclusion,
)
from agents_remember.application.role_launch_context import RoleLaunchContext
from agents_remember.cli.paseo_launch import (
    RoleLaunch,
    agent_create_input,
    applied_to_agent,
    build_launch_call,
)
from agents_remember.cli.paseo_role_tools import second_copy_loaded
from agents_remember.cli.role_launch_preparation import (
    _AR_OTHER_SERVER_CHOICE,
    _AR_TOOL_SERVER_USAGE,
    _ar_tool_server_usage,
)
from agents_remember.serving.paseo.paseo_daemon_config import provider_denied_tools

# Providers the role settings name on the developer's installation beside Claude, and one
# that no build knows. None of them has a setting of the host for one launch.
_PROVIDERS_WITHOUT_A_WAY = ("codex", "hermes", "pi", "opencode", "custom-unknown")


def _server_and_tool(name: str) -> tuple[str, str | None] | None:
    """``mcp__<server>__<tool>`` as the harness reads it; a rule may name the server alone."""
    prefix, _, rest = name.partition("__")
    server, _, tool = rest.partition("__")
    if prefix != "mcp" or not server:
        return None
    return server, tool or None


def _harness_denies(rule: str, tool: str) -> bool:
    """How Claude Code applies a deny rule to the name of an MCP tool.

    Its permissions documentation: ``mcp__puppeteer`` matches any tool provided by the
    ``puppeteer`` server. A rule that names the server alone is compared with the tool's
    server name for equality; a rule with a star is matched over the whole tool name.
    """
    if "*" in rule:
        return fnmatch.fnmatchcase(tool, rule)
    ruled, named = _server_and_tool(rule), _server_and_tool(tool)
    if ruled is None or named is None or ruled[0] != named[0]:
        return False
    return ruled[1] is None or ruled[1] == named[1]


def _host_merges(configured: dict[str, Any], requested: dict[str, Any]) -> dict[str, Any]:
    """How the pinned host lays a request's provider options over its configured ones.

    ``mergeProviderOptions`` in its ``provider-options.js``: plain objects merge recursively,
    every other value of the request replaces the configured value. A list is such a value.
    """
    merged = dict(configured)
    for key, value in requested.items():
        previous = merged.get(key)
        if isinstance(previous, dict) and isinstance(value, dict):
            merged[key] = _host_merges(previous, value)
        else:
            merged[key] = value
    return merged


def _stdio(command: str) -> dict[str, Any]:
    return {"type": "stdio", "command": command, "args": [], "env": {}}


def _launch_call(
    provider: str,
    *,
    accepts_tool_servers: bool = True,
    host_denied_tools: tuple[str, ...] = (),
) -> dict[str, Any]:
    context = RoleLaunchContext(
        role="architect", sprint=None, master=None, task=None, effective_task=None
    )
    return build_launch_call(
        RoleLaunch(
            agent_id="agent-1",
            request_id=uuid.uuid4(),
            context=context,
            folder="/tmp",
            provider=provider,
            session_options={},
            prompt="hello",
            report_path="/tmp/report.md",
            handover_artifact={"path": "/tmp/artifact.md", "sha256": "0" * 64},
            settings_file=Path("/tmp/settings.json"),
            accepts_tool_servers=accepts_tool_servers,
            host_denied_tools=host_denied_tools,
        )
    )


class SingleServerTests(unittest.TestCase):
    def test_the_rule_names_the_other_server_and_carries_no_star(self) -> None:
        self.assertEqual(OTHER_PRODUCT_TOOL_SERVER, "agents-remember")
        self.assertEqual(CLAUDE_OTHER_SERVER_RULE, "mcp__agents-remember")
        self.assertNotIn("*", CLAUDE_OTHER_SERVER_RULE)

    def test_no_tool_of_the_task_server_and_no_server_of_a_user_is_matched(self) -> None:
        """MIK-R132 rule 3: exactly the product's other server."""
        rule = CLAUDE_OTHER_SERVER_RULE
        for tool in ("role_start", "role_message", "server_info", "task_doc"):
            self.assertTrue(_harness_denies(rule, f"mcp__agents-remember__{tool}"), tool)
            self.assertFalse(_harness_denies(rule, f"mcp__{TOOL_SERVER_NAME}__{tool}"), tool)
        for users_tool in (
            "mcp__agents-remember-notes__search",
            "mcp__agents-remember2__search",
            "mcp__my-agents-remember__search",
            "mcp__agents_remember__search",
            "mcp__xapi__post",
            "Bash",
        ):
            self.assertFalse(_harness_denies(rule, users_tool), users_tool)
        # Why the rule carries no star: with one it is a match over the whole name and
        # takes the task server's tools and a user's similar name with it.
        starred = f"{rule}*"
        self.assertTrue(_harness_denies(starred, f"mcp__{TOOL_SERVER_NAME}__role_start"))
        self.assertTrue(_harness_denies(starred, "mcp__agents-remember-notes__search"))

    def test_the_record_is_what_the_launch_hands_over(self) -> None:
        self.assertEqual(
            launch_server_exclusion("claude"),
            {
                "harness": "claude",
                "mode": EXCLUSION_MODE_CLAUDE,
                "disallowedTools": ["mcp__agents-remember"],
                "servers": [TOOL_SERVER_NAME],
            },
        )
        for provider in (*_PROVIDERS_WITHOUT_A_WAY, None):
            self.assertIsNone(launch_server_exclusion(provider), str(provider))
        # Without the task server the other one stays: the agent had no product tools else.
        self.assertIsNone(launch_server_exclusion("claude", servers=()))
        self.assertEqual(exclusion_agent_options(None), {})
        self.assertEqual(
            exclusion_agent_options(launch_server_exclusion("claude")),
            {"providerOptions": {"disallowedTools": ["mcp__agents-remember"]}},
        )

    def test_the_host_is_handed_the_provider_options_for_claude_alone(self) -> None:
        """What the role start hands the host for each provider (model-free)."""
        call = _launch_call("claude")
        self.assertEqual(
            call["agent"]["providerOptions"], {"disallowedTools": ["mcp__agents-remember"]}
        )
        self.assertEqual(sorted(call["agent"]["mcpServers"]), [TOOL_SERVER_NAME])
        self.assertEqual(call["serverExclusion"]["mode"], EXCLUSION_MODE_CLAUDE)
        sending = agent_create_input(call, "workspace-1")
        self.assertEqual(sending["providerOptions"], call["agent"]["providerOptions"])
        self.assertEqual(sending["workspaceId"], "workspace-1")
        self.assertEqual(sending["agentId"], "agent-1")
        # The record stays with the product; the host is handed no key it does not read.
        self.assertNotIn("serverExclusion", sending)
        for provider in _PROVIDERS_WITHOUT_A_WAY:
            other = _launch_call(provider)
            self.assertNotIn("providerOptions", other["agent"], provider)
            self.assertNotIn("serverExclusion", other, provider)
            self.assertEqual(sorted(other["agent"]["mcpServers"]), [TOOL_SERVER_NAME], provider)
            self.assertNotIn("providerOptions", agent_create_input(other, "workspace-1"))

    def test_a_claude_launch_without_the_task_server_hands_over_nothing(self) -> None:
        call = _launch_call("claude", accepts_tool_servers=False)
        self.assertNotIn("providerOptions", call["agent"])
        self.assertNotIn("serverExclusion", call)

    def test_what_the_host_s_configuration_denies_stays_denied(self) -> None:
        """MIK-R132 rules 2 and 3: the user's rules stay in force, one rule is added."""
        configured = {"disallowedTools": ["Bash", "mcp__user-server"], "sandbox": {"enabled": True}}
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            self.assertEqual(provider_denied_tools(home, "claude"), ())
            entries = {
                # The entry's own list is the host's to add to every agent; it is not repeated.
                "claude": {"options": configured, "disallowedTools": ["WebFetch"]},
                "older": {"params": {"disallowedTools": ["Edit", 7, ""]}},
                "plain": {"command": ["plain"]},
            }
            document = {"agents": {"providers": entries}}
            (home / "config.json").write_text(json.dumps(document), encoding="utf-8")
            denied = provider_denied_tools(home, "claude")
            self.assertEqual(denied, ("Bash", "mcp__user-server"))
            self.assertEqual(provider_denied_tools(home, "older"), ("Edit",))
            self.assertEqual(provider_denied_tools(home, "plain"), ())
            self.assertEqual(provider_denied_tools(home, "absent"), ())
        call = _launch_call("claude", host_denied_tools=denied)
        handed = agent_create_input(call, "workspace-1")["providerOptions"]
        self.assertEqual(
            _host_merges(configured, handed),
            {
                "disallowedTools": ["Bash", "mcp__user-server", CLAUDE_OTHER_SERVER_RULE],
                "sandbox": {"enabled": True},
            },
        )
        # Why they are repeated: the product's rule alone takes the place of the user's list.
        alone = agent_create_input(_launch_call("claude"), "workspace-1")["providerOptions"]
        self.assertEqual(
            _host_merges(configured, alone)["disallowedTools"], [CLAUDE_OTHER_SERVER_RULE]
        )
        # The record names the product's rule alone; a rule the user has already is not doubled.
        self.assertEqual(call["serverExclusion"]["disallowedTools"], [CLAUDE_OTHER_SERVER_RULE])
        doubled = _launch_call("claude", host_denied_tools=(CLAUDE_OTHER_SERVER_RULE, "Bash"))
        self.assertEqual(
            doubled["agent"]["providerOptions"]["disallowedTools"],
            [CLAUDE_OTHER_SERVER_RULE, "Bash"],
        )
        # Where nothing is handed over the configured list applies as it is.
        self.assertNotIn(
            "providerOptions", _launch_call("codex", host_denied_tools=denied)["agent"]
        )

    def test_a_supplied_map_loses_the_other_server_s_entry_and_nothing_else(self) -> None:
        """The filter of MIK-R112 on a map of servers that a launch call is supplied with."""
        supplied = {
            TOOL_SERVER_NAME: _stdio("x"),
            "user-server": {"type": "stdio", "command": "u", "args": ["--x"], "env": {"K": "V"}},
            "agents-remember-notes": _stdio("n"),
            OTHER_PRODUCT_TOOL_SERVER: _stdio("o"),
        }
        kept = {name: entry for name, entry in supplied.items() if name != "agents-remember"}
        # A call as an earlier build saved it: its record, and both of the product's servers.
        earlier = {
            "workspace": {"cwd": "/tmp"},
            "agent": {"agentId": "a", "provider": "codex", "mcpServers": copy.deepcopy(supplied)},
            "serverExclusion": {
                "harness": "codex",
                "mode": "task-server-only",
                "servers": [TOOL_SERVER_NAME],
            },
        }
        saved = copy.deepcopy(earlier)
        sending = agent_create_input(earlier, "workspace-1")
        self.assertEqual(
            sending,
            {"agentId": "a", "provider": "codex", "mcpServers": kept, "workspaceId": "workspace-1"},
        )
        self.assertEqual(earlier, saved)
        # A call of this build with a supplied map, with a record and without one.
        for provider in ("claude", "codex", "custom-unknown"):
            call = _launch_call(provider)
            definition = call["agent"]["mcpServers"][TOOL_SERVER_NAME]
            call["agent"]["mcpServers"] = {**copy.deepcopy(supplied), TOOL_SERVER_NAME: definition}
            sending = agent_create_input(call, "workspace-1")
            self.assertEqual(
                sending["mcpServers"], {**kept, TOOL_SERVER_NAME: definition}, provider
            )
            self.assertNotIn("serverExclusion", sending, provider)
            applied = applied_to_agent(call)["toolServer"]
            self.assertEqual(applied["command"][0], definition["command"], provider)
            self.assertIn(OTHER_PRODUCT_TOOL_SERVER, call["agent"]["mcpServers"], provider)
        # Without the task server the other one is the agent's only copy and stays; under
        # the earlier build's record it went then, and the emptied map was left out.
        only_other = {"agent": {"agentId": "a", "mcpServers": {"agents-remember": _stdio("o")}}}
        self.assertEqual(
            agent_create_input(only_other, "w")["mcpServers"], {"agents-remember": _stdio("o")}
        )
        recorded = {
            "agent": {**only_other["agent"], "systemPrompt": ""},
            "serverExclusion": earlier["serverExclusion"],
        }
        self.assertNotIn("mcpServers", agent_create_input(recorded, "w"))
        self.assertFalse(applied_to_agent(recorded)["toolServer"]["applied"])

    def test_the_receipt_keeps_the_record(self) -> None:
        applied = applied_to_agent(_launch_call("claude"))
        self.assertTrue(applied["toolServer"]["applied"])
        self.assertEqual(applied["serverExclusion"]["mode"], EXCLUSION_MODE_CLAUDE)
        self.assertNotIn("serverExclusion", applied_to_agent(_launch_call("codex")))

    def test_the_answer_says_loaded_unless_the_record_of_the_host_s_mode_is_there(self) -> None:
        """MIK-R132 rule 5: derived from what was applied, never from the harness's name."""
        applied = {"toolServer": {"applied": True}}
        record = launch_server_exclusion("claude")
        self.assertFalse(
            second_copy_loaded({**applied, "replayRequest": {"serverExclusion": record}})
        )
        # The receipt drops the replay request after the launch; the record stays at its top.
        self.assertFalse(second_copy_loaded({**applied, "serverExclusion": record}))
        self.assertFalse(
            second_copy_loaded({**applied, **applied_to_agent(_launch_call("claude"))})
        )
        # No record: nothing was handed over, whatever the harness is called.
        self.assertTrue(second_copy_loaded({**applied, "agent": {"id": "claude"}}))
        self.assertTrue(second_copy_loaded(applied_to_agent(_launch_call("codex"))))
        # A record of an earlier build handed the host nothing: the copy was loaded.
        earlier = {"harness": "codex", "mode": "task-server-only", "servers": [TOOL_SERVER_NAME]}
        self.assertTrue(second_copy_loaded({**applied, "serverExclusion": earlier}))
        self.assertTrue(second_copy_loaded({"replayRequest": {"serverExclusion": earlier}}))
        # An agent that was given no task server carries no second copy.
        self.assertFalse(second_copy_loaded({"toolServer": {"applied": False}}))

    def test_the_first_message_keeps_the_choice_sentence_unless_the_copy_is_kept_out(self) -> None:
        """MIK-R132 rule 5: derived from the same record as the launch call."""
        servers = (TOOL_SERVER_NAME,)
        kept_out = _ar_tool_server_usage("claude", servers=servers)
        self.assertNotIn(_AR_OTHER_SERVER_CHOICE, kept_out)
        self.assertIn(TOOL_SERVER_NAME, kept_out)
        for provider in _PROVIDERS_WITHOUT_A_WAY:
            self.assertEqual(
                _ar_tool_server_usage(provider, servers=servers), _AR_TOOL_SERVER_USAGE, provider
            )
        self.assertIn(_AR_OTHER_SERVER_CHOICE, _AR_TOOL_SERVER_USAGE)
        # Unknown acceptance, no task server, no resolved agent: no claim, the sentence stays.
        self.assertEqual(_ar_tool_server_usage("claude"), _AR_TOOL_SERVER_USAGE)
        self.assertEqual(_ar_tool_server_usage("claude", servers=()), _AR_TOOL_SERVER_USAGE)
        self.assertEqual(_ar_tool_server_usage(None, servers=servers), _AR_TOOL_SERVER_USAGE)


if __name__ == "__main__":
    unittest.main()
