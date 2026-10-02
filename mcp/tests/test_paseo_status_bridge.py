"""The bridge's agent-state and agent-resume commands: the real script against a fake client."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents_remember.cli.paseo_bridge import PaseoBridgeFailure, bridge_call
from agents_remember.kernel.primitives.paseo_runtime_settings import parse_paseo_runtime_settings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

SERVER_ID = "srv_configured"
AGENT_ID = "f3c1a2b4-5d6e-4f70-8a91-b2c3d4e5f607"
MISSING_AGENT_ID = "9f8e7d6c-5b4a-4c3d-9e2f-1a0b9c8d7e6f"
ARCHIVED_AT = "2026-10-02T01:00:00.000Z"
READ = {"via": "refresh", "id": AGENT_ID}
TAIL = {"direction": "tail", "limit": 20, "projection": "projected"}
NO_ROLLOUT = "Failed to resume Codex thread 01a0: no rollout found for thread id 01a0"

FAKE_DAEMON_CLIENT = """
import { appendFileSync, readFileSync } from 'node:fs'
export const scenario = JSON.parse(readFileSync(process.env.FAKE_PASEO_SCENARIO, 'utf8'))
export const agents = new Map(Object.entries(scenario.agents ?? {}))
export function record(call) {
  appendFileSync(process.env.FAKE_PASEO_RECORD, JSON.stringify(call) + '\\n')
}
export class DaemonClient {
  constructor(config) { this.config = config; this.state = { status: 'idle' } }
  async connect() { this.state = { status: 'connected' } }
  async close() { this.state = { status: 'disposed' } }
  getConnectionState() { return scenario.lost ? { status: 'disconnected' } : this.state }
  getLastServerInfoMessage() { return { serverId: scenario.serverId } }
}
"""
# Every call on an agent is recorded. A timeline read does what the runtime does: it loads the
# agent, which resumes a closed session. Anything else a command could do to an agent fails.
FAKE_CLIENT_ROOT = """
import { agents, record, scenario } from './daemon-client.js'
function forbidden(via, id) {
  return async () => {
    record({ via, id })
    throw new Error(via + ' is not a read')
  }
}
function handle(id) {
  return {
    refresh: async () => {
      record({ via: 'refresh', id })
      const agent = agents.get(id)
      if (!agent) throw new Error(`Agent not found: ${id}`)
      return { agent, project: null }
    },
    timeline: {
      refetch: async (options) => {
        record({ via: 'timeline.refetch', id, options })
        const agent = agents.get(id)
        if (agent.status === 'closed') {
          if (scenario.lostOnResume) scenario.lost = true
          if (scenario.resumeError) throw new Error(scenario.resumeError)
          agent.status = 'idle'
        }
        const items = scenario.timeline ?? []
        return {
          agent,
          entries: items.slice(-options.limit).map((item) => ({ item })),
          hasOlder: items.length > options.limit
        }
      },
      append: forbidden('timeline.append', id)
    },
    send: forbidden('send', id),
    run: forbidden('run', id),
    waitForFinish: forbidden('waitForFinish', id),
    archive: forbidden('archive', id),
    respondToPermission: forbidden('respondToPermission', id)
  }
}
export function createPaseoApi() {
  return { dispose: async () => {}, agents: { ref: handle, create: forbidden('create', null) } }
}
"""


def agent(status: str, **fields: Any) -> dict[str, Any]:
    """An agent snapshot as the runtime reports it, reduced to what the bridge reads."""

    return {
        "id": AGENT_ID,
        "status": status,
        "archivedAt": None,
        "activeTurn": None,
        "pendingPermissions": [],
        "lastUserMessageAt": None,
        **fields,
    }


def state(status: str, **fields: Any) -> dict[str, Any]:
    """The bridge's state of an agent."""

    return {
        "id": AGENT_ID,
        "status": status,
        "archivedAt": None,
        "turnActive": False,
        "pendingPermissions": [],
        "lastError": None,
        "attentionReason": None,
        "lastTurn": None,
        **fields,
    }


def user(text: str) -> dict[str, str]:
    return {"type": "user_message", "text": text}


def reply(text: str) -> dict[str, str]:
    return {"type": "assistant_message", "text": text}


@unittest.skipUnless(shutil.which("node"), "the bridge script needs Node.js")
class AgentStateScriptTests(unittest.TestCase):
    """Each call starts the real script; the fake client records what it was asked to do."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        settings = parse_paseo_runtime_settings(
            {
                "installPrefix": (self.root / "prefix").as_posix(),
                "home": (self.root / "home").as_posix(),
                "listen": "127.0.0.1:6837",
                "version": "0.11.0-beta.2",
                "providers": {},
                "embed": [],
            }
        )
        assert settings is not None
        settings.home.mkdir(parents=True)
        (settings.home / "server-id").write_text(SERVER_ID + "\n", encoding="utf-8")
        self.config = McpRuntimeConfig(
            config_path=self.root / "settings" / "mcp.json",
            coordination_root=self.root / "coordination",
            workspace_root=self.root / "projects",
            transcript_root=self.root / "coordination" / "logs" / "mcp",
            paseo_runtime=settings,
        )
        package = self.root / "prefix" / "node_modules" / "@getpaseo" / "client"
        (package / "dist").mkdir(parents=True)
        exports = {
            ".": {"default": "./dist/index.js"},
            "./internal/daemon-client": {"default": "./dist/daemon-client.js"},
        }
        (package / "package.json").write_text(
            json.dumps({"name": "@getpaseo/client", "type": "module", "exports": exports}),
            encoding="utf-8",
        )
        (package / "dist" / "daemon-client.js").write_text(FAKE_DAEMON_CLIENT, encoding="utf-8")
        (package / "dist" / "index.js").write_text(FAKE_CLIENT_ROOT, encoding="utf-8")

    def call(self, command: str, **scenario: Any) -> dict[str, Any]:
        """Run one command for AGENT_ID against a runtime that holds the scenario's agent."""

        held = scenario.pop("holds", None)
        payload = scenario.pop("payload", {"agentId": AGENT_ID})
        scenario_path = self.root / "scenario.json"
        agents = {AGENT_ID: held} if held else {}
        scenario_path.write_text(
            json.dumps({"serverId": SERVER_ID, "agents": agents, **scenario}), encoding="utf-8"
        )
        (self.root / "record.jsonl").write_text("", encoding="utf-8")
        environment = {
            "FAKE_PASEO_SCENARIO": scenario_path.as_posix(),
            "FAKE_PASEO_RECORD": (self.root / "record.jsonl").as_posix(),
        }
        with patch.dict(os.environ, environment):
            return bridge_call(self.config, command, payload)

    def recorded(self) -> list[dict[str, Any]]:
        """What the fake client was asked to do by the last call."""

        lines = (self.root / "record.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    def test_agent_state_reports_an_agent_without_reading_its_timeline(self) -> None:
        pending = {"id": "perm-1", "provider": "claude", "name": "Bash", "kind": "tool"}
        snapshots: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
            "a closed session": (agent("closed", lastUserMessageAt="t"), state("closed")),
            "an archived agent": (
                agent("closed", archivedAt=ARCHIVED_AT),
                state("closed", archivedAt=ARCHIVED_AT),
            ),
            "a turn in progress": (
                agent("running", activeTurn={"turnId": "turn-1", "startedAt": "t"}),
                state("running", turnActive=True),
            ),
            "a pending permission request": (
                agent("running", pendingPermissions=[pending, {"title": "Edit a file"}]),
                state(
                    "running",
                    pendingPermissions=[
                        {"name": "Bash", "kind": "tool"},
                        {"name": "Edit a file", "kind": None},
                    ],
                ),
            ),
            "an error state": (
                agent("error", lastError="The model is not supported.", attentionReason="error"),
                state("error", lastError="The model is not supported.", attentionReason="error"),
            ),
            # The runtime's error mark outlives a closed session; its error text does not.
            "a closed session that keeps the error mark": (
                agent("closed", attentionReason="error"),
                state("closed", attentionReason="error"),
            ),
            "a session that is starting": (agent("initializing"), state("initializing")),
            # The runtime's word is passed through as it is, also one this build does not know.
            "a state with an unknown word": (agent("hibernating"), state("hibernating")),
        }
        for label, (held, expected) in snapshots.items():
            with self.subTest(label):
                answer = self.call("agent-state", holds=held)
                self.assertEqual(answer, {"serverId": SERVER_ID, "agent": expected})
                # One lookup and nothing else: a timeline read would load, and so resume, it.
                self.assertEqual(self.recorded(), [READ])
        with self.subTest("the runtime has no such agent"):
            self.assertEqual(self.call("agent-state"), {"serverId": SERVER_ID, "agent": None})
            self.assertEqual(self.recorded(), [READ])
        with self.subTest("an agent id is required"), self.assertRaises(PaseoBridgeFailure) as bad:
            self.call("agent-state", payload={})
        self.assertEqual(bad.exception.code, "invalid_bridge_payload")

    def test_agent_state_reads_how_the_last_turn_of_an_idle_agent_ended(self) -> None:
        ran = agent("idle", lastUserMessageAt="2026-10-02T01:22:05.725Z")
        tool_call = {"type": "tool_call", "name": "shell", "status": "running"}
        long_reply = "Ergebnis 役割 🙂 " * 400
        endings: dict[str, tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]] = {
            "no turn yet": (agent("idle"), [], {"state": "none"}),
            "a reply": (ran, [user("go"), reply("READY")], {"state": "replied", "text": "READY"}),
            "a reply in pieces, then reasoning": (
                ran,
                [
                    user("go"),
                    reply("old"),
                    user("more"),
                    reply("AG"),
                    reply("AIN"),
                    {"type": "reasoning"},
                ],
                {"state": "replied", "text": "AGAIN"},
            ),
            "a tool call that never finished": (
                ran,
                [user("go"), reply("Running the command."), tool_call],
                {"state": "unreplied"},
            ),
            "a message without any answer": (ran, [user("go")], {"state": "unreplied"}),
            "a failed turn after a resume: the error mark and no reply": (
                {**ran, "attentionReason": "error"},
                [user("go")],
                {"state": "unreplied"},
            ),
            "a turn the timeline no longer shows": (ran, [], {"state": "unreplied"}),
            "a long reply is cut to 3,000 characters": (
                ran,
                [user("go"), reply(long_reply)],
                {"state": "replied", "text": long_reply[:3000]},
            ),
        }
        for label, (held, timeline, expected) in endings.items():
            with self.subTest(label):
                answer = self.call("agent-state", holds=held, timeline=timeline)
                mark = held.get("attentionReason")
                self.assertEqual(
                    answer["agent"], state("idle", lastTurn=expected, attentionReason=mark)
                )
                self.assertEqual(
                    self.recorded(),
                    [READ, {"via": "timeline.refetch", "id": AGENT_ID, "options": TAIL}],
                )
        self.assertEqual(len(long_reply[:3000]), 3000)
        self.assertGreater(len(long_reply[:3000].encode("utf-16-le")) // 2, 3000)

    def test_agent_resume_opens_a_closed_session_and_leaves_every_other_agent_alone(self) -> None:
        resume_read = {
            "via": "timeline.refetch",
            "id": AGENT_ID,
            "options": {"direction": "tail", "limit": 1},
        }
        last_turn_read = {"via": "timeline.refetch", "id": AGENT_ID, "options": TAIL}
        untouched = {"attempted": False, "resumed": False}
        with self.subTest("a closed session is resumed by one timeline read, with no message"):
            answer = self.call(
                "agent-resume",
                holds=agent("closed", lastUserMessageAt="t"),
                timeline=[user("go"), reply("READY")],
            )
            self.assertEqual(answer["resume"], {"attempted": True, "resumed": True})
            self.assertEqual(
                answer["agent"], state("idle", lastTurn={"state": "replied", "text": "READY"})
            )
            self.assertEqual(self.recorded(), [READ, resume_read, READ, last_turn_read])
        left_alone: dict[str, tuple[dict[str, Any] | None, list[dict[str, Any]]]] = {
            "an open session": (agent("running"), [READ]),
            "an archived agent": (agent("closed", archivedAt=ARCHIVED_AT), [READ]),
            "an agent the runtime does not have": (None, [READ]),
        }
        for label, (held, calls) in left_alone.items():
            with self.subTest(label):
                answer = self.call("agent-resume", holds=held)
                self.assertEqual(answer["resume"], untouched)
                self.assertEqual(answer["agent"] is None, held is None)
                self.assertEqual(self.recorded(), calls)
        with self.subTest("a resume the runtime refuses keeps the session closed, with its text"):
            answer = self.call("agent-resume", holds=agent("closed"), resumeError=NO_ROLLOUT)
            self.assertEqual(
                answer["resume"], {"attempted": True, "resumed": False, "error": NO_ROLLOUT}
            )
            self.assertEqual(answer["agent"], state("closed"))
            self.assertEqual(self.recorded(), [READ, resume_read, READ])
        with (
            self.subTest("a daemon that goes away during the resume is not a refusal"),
            self.assertRaises(PaseoBridgeFailure) as lost,
        ):
            self.call(
                "agent-resume",
                holds=agent("closed"),
                resumeError="socket closed",
                lostOnResume=True,
            )
        self.assertEqual(lost.exception.code, "paseo_daemon_unreachable")


if __name__ == "__main__":
    unittest.main()
