"""The bridge's agent-send and agent-wait commands and the parent of a created agent.

Each call starts the real bridge script; a fake client package stands in for the runtime and
records what it was asked to do.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents_remember.cli.paseo_bridge import PaseoBridgeFailure, bridge_call
from agents_remember.kernel.primitives.paseo_runtime_settings import parse_paseo_runtime_settings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

SERVER_ID = "srv_configured"
AGENT_ID = "f3c1a2b4-5d6e-4f70-8a91-b2c3d4e5f607"
PARENT_ID = "1f3c2f0e-6a57-4f0b-9d4e-0c8f1a2b3c4d"
MESSAGE_ID = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
ARCHIVED_AT = "2026-10-02T01:00:00.000Z"
TURN = {"turnId": "turn-7", "startedAt": "2026-10-02T01:00:00.000Z"}
TAIL = {"direction": "tail", "limit": 200, "projection": "projected"}
READ = {"via": "refresh", "id": AGENT_ID}

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
  getConnectionState() { return this.state }
  getLastServerInfoMessage() { return { serverId: scenario.serverId } }
  async createAgent(options) {
    record({ via: 'daemon.createAgent', options })
    const agent = { id: options.agentId, provider: options.config.provider, status: 'idle', labels: options.labels }
    agents.set(agent.id, agent)
    return agent
  }
}
"""
# A send does what the scenario says the runtime does with it (`afterSend`). A timeline
# subscription delivers the scenario's turn events once it is established. The runtime's own wait
# answers after `waitTakes` milliseconds and leaves the agent as `afterWait` says. Anything that
# would resume, archive or answer for an agent fails.
FAKE_CLIENT_ROOT = """
import { agents, record, scenario } from './daemon-client.js'
function forbidden(via, id) {
  return async () => {
    record({ via, id })
    throw new Error(via + ' is not allowed here')
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
    send: async (text, options) => {
      record({ via: 'send', id, text, options })
      if (scenario.sendError) throw new Error(scenario.sendError)
      Object.assign(agents.get(id), scenario.afterSend ?? {})
    },
    waitForFinish: async (timeoutMs) => {
      record({ via: 'waitForFinish', id, timeoutMs })
      await new Promise((resolve) => setTimeout(resolve, scenario.waitTakes ?? 0))
      Object.assign(agents.get(id), scenario.afterWait ?? {})
      return { status: scenario.waitStatus ?? 'idle', final: null, error: null, lastMessage: null }
    },
    timeline: {
      subscribe: (handler) => {
        record({ via: 'timeline.subscribe', id })
        const unsubscribe = () => record({ via: 'timeline.unsubscribe', id })
        unsubscribe.ready = (async () => {
          if (scenario.subscribeError) throw new Error(scenario.subscribeError)
          for (const event of scenario.events ?? []) {
            setTimeout(() => {
              Object.assign(agents.get(id), scenario.afterEvent ?? {})
              handler({ agentId: id, event })
            }, scenario.eventAfter ?? 20)
          }
        })()
        return unsubscribe
      },
      refetch: async (options) => {
        record({ via: 'timeline.refetch', id, options })
        const items = scenario.timeline ?? []
        return { agent: agents.get(id), entries: items.slice(-options.limit).map((item) => ({ item })), hasOlder: false }
      },
      append: forbidden('timeline.append', id)
    },
    run: forbidden('run', id),
    archive: forbidden('archive', id),
    respondToPermission: forbidden('respondToPermission', id)
  }
}
export function createPaseoApi(daemon) {
  return {
    dispose: async () => {},
    config: { get: async () => ({ config: { providers: scenario.providers ?? {} } }) },
    agents: { ref: handle },
    workspaces: {
      ref: (workspaceId) => ({
        refresh: async () => ({ workspaceDirectory: '/work/' + workspaceId }),
        agents: {
          create: async (options) => {
            record({ via: 'workspace.agents.create', workspaceId, options })
            const agent = { id: options.agentId, provider: 'steering', status: 'idle', labels: options.labels }
            agents.set(agent.id, agent)
            return { current: () => agent }
          }
        }
      })
    }
  }
}
"""


def agent(status: str, **fields: Any) -> dict[str, Any]:
    """An agent snapshot as the runtime reports it, reduced to what the bridge reads."""

    return {
        "id": AGENT_ID,
        "provider": "steering",
        "status": status,
        "archivedAt": None,
        "activeTurn": None,
        "pendingPermissions": [],
        "lastError": None,
        **fields,
    }


def sent(text: str, message_id: str = MESSAGE_ID) -> dict[str, str]:
    return {"type": "user_message", "text": text, "messageId": message_id}


def reply(text: str) -> dict[str, str]:
    return {"type": "assistant_message", "text": text}


@unittest.skipUnless(shutil.which("node"), "the bridge script needs Node.js")
class RoleBridgeScriptTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        settings = parse_paseo_runtime_settings(
            {
                "installPrefix": (self.root / "prefix").as_posix(),
                "home": (self.root / "home").as_posix(),
                "listen": "127.0.0.1:6845",
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

    def call(self, command: str, payload: dict[str, Any], **scenario: Any) -> dict[str, Any]:
        """Run one command against a runtime that holds the scenario's agent."""

        held = scenario.pop("holds", None)
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

    def recorded(self, *vias: str) -> list[dict[str, Any]]:
        """What the fake client was asked to do by the last call, optionally only some kinds."""

        lines = (self.root / "record.jsonl").read_text(encoding="utf-8").splitlines()
        calls = [json.loads(line) for line in lines]
        return [call for call in calls if not vias or call["via"] in vias]


class AgentSendScriptTests(RoleBridgeScriptTestCase):
    def send(self, **scenario: Any) -> dict[str, Any]:
        payload = {
            "agentId": AGENT_ID,
            "text": "From architect\nreport your plan",
            "messageId": MESSAGE_ID,
        }
        return self.call("agent-send", payload, **scenario)["delivery"]

    def test_only_a_live_agent_with_an_open_session_is_sent_to(self) -> None:
        refused = {
            "the runtime has no such agent": (None, "not-found"),
            "an archived agent": (agent("closed", archivedAt=ARCHIVED_AT), "archived"),
            "an archived agent that is loaded": (agent("idle", archivedAt=ARCHIVED_AT), "archived"),
            "a closed session": (agent("closed"), "closed"),
            "an agent that is still starting": (agent("initializing"), "busy"),
        }
        for label, (held, reason) in refused.items():
            with self.subTest(label):
                delivery = self.send(holds=held)
                self.assertEqual((delivery["delivered"], delivery["refused"]), (False, reason))
                self.assertTrue(delivery["detail"])
                # Nothing was sent: the runtime would un-archive or resume the agent to deliver.
                self.assertEqual(self.recorded(), [READ])

    def test_an_idle_agent_starts_a_turn_and_a_running_turn_takes_the_message_up(self) -> None:
        options = {"messageId": MESSAGE_ID, "activeTurnBehavior": "steer"}
        other_turn = {"turnId": "turn-8", "startedAt": "2026-10-02T01:05:00.000Z"}
        cases: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {
            "an idle agent": (
                agent("idle"),
                {"status": "running", "activeTurn": TURN},
                {"delivered": True, "taken": "started", "turnId": "turn-7"},
            ),
            "a running turn takes it up": (
                agent("running", activeTurn=TURN),
                {},
                {"delivered": True, "taken": "steered", "turnId": "turn-7"},
            ),
            "the turn that took it up has ended already": (
                agent("running", activeTurn=TURN),
                {"status": "idle", "activeTurn": None},
                {"delivered": True, "taken": "steered", "turnId": None},
            ),
            "the runtime replaced the running turn all the same": (
                agent("running", activeTurn=TURN),
                {"activeTurn": other_turn},
                {"delivered": True, "taken": "replaced", "turnId": "turn-8"},
            ),
        }
        for label, (held, after, delivery) in cases.items():
            with self.subTest(label):
                self.assertEqual(self.send(holds=held, afterSend=after), delivery)
                # Always with the steer behaviour, and with the caller's message id.
                self.assertEqual(
                    self.recorded("send"),
                    [
                        {
                            "via": "send",
                            "id": AGENT_ID,
                            "text": "From architect\nreport your plan",
                            "options": options,
                        }
                    ],
                )

    def test_a_provider_without_steering_is_refused_as_busy_and_its_turn_left_alone(self) -> None:
        running = agent("running", activeTurn=TURN, provider="relayed")
        providers = {"relayed": {"extends": "acp", "command": ["relayed", "acp"]}}

        delivery = self.send(holds=running, providers=providers)

        self.assertEqual((delivery["delivered"], delivery["refused"]), (False, "busy"))
        self.assertIn(
            "cannot hand a message to a running turn of this provider", delivery["detail"]
        )
        self.assertEqual(self.recorded(), [READ])
        with self.subTest("the same provider is sent to while it is idle"):
            idle = agent("idle", provider="relayed")
            delivery = self.send(holds=idle, providers=providers, afterSend={"status": "running"})
            self.assertEqual((delivery["delivered"], delivery["taken"]), (True, "started"))
        with self.subTest("a send the runtime refuses is a refused call"):
            with self.assertRaises(PaseoBridgeFailure) as raised:
                self.send(holds=agent("idle"), sendError="Active turn changed before steering")
            self.assertEqual(raised.exception.code, "paseo_call_failed")


class AgentWaitScriptTests(RoleBridgeScriptTestCase):
    def wait(self, payload: dict[str, Any] | None = None, **scenario: Any) -> dict[str, Any]:
        request = {"agentId": AGENT_ID, "turnId": "turn-7", "waitMs": 400}
        return self.call("agent-wait", {**request, **(payload or {})}, **scenario)["wait"]

    def test_a_turn_that_ends_during_the_call_is_reported_by_the_runtimes_own_event(self) -> None:
        timeline = [
            sent("From architect\nreport your plan"),
            reply("The plan: "),
            reply("read, change, test.\n"),
        ]
        events = {
            "finished": {"type": "turn_completed", "turnId": "turn-7"},
            "failed": {"type": "turn_failed", "turnId": "turn-7", "error": "usage limit reached"},
            "cancelled": {"type": "turn_canceled", "turnId": "turn-7"},
        }
        for outcome, event in events.items():
            with self.subTest(outcome):
                began = time.monotonic()
                answer = self.wait(
                    holds=agent("running", activeTurn=TURN),
                    events=[{"type": "turn_started", "turnId": "turn-7"}, event],
                    afterEvent={"status": "idle", "activeTurn": None},
                    waitTakes=8000,
                    timeline=timeline,
                )
                elapsed = time.monotonic() - began
                self.assertEqual(
                    answer,
                    {
                        "state": "ended",
                        "outcome": outcome,
                        **({"error": "usage limit reached"} if outcome == "failed" else {}),
                        "text": "The plan: read, change, test.",
                        "textTruncated": False,
                    },
                )
                # The wait ended with the event, not with the runtime's own wait eight seconds on.
                self.assertLess(elapsed, 6.0)
                self.assertEqual(self.recorded("timeline.refetch")[0]["options"], TAIL)
                self.assertEqual(self.recorded("send", "run", "archive", "respondToPermission"), [])
        with self.subTest("an event of another turn does not end the wait"):
            # The event arrives while the runtime's own wait is still running.
            answer = self.wait(
                holds=agent("running", activeTurn=TURN),
                events=[{"type": "turn_completed", "turnId": "turn-6"}],
                waitTakes=300,
                waitStatus="timeout",
            )
            self.assertEqual(answer, {"state": "running"})
        with self.subTest("without a named turn, the end of the running turn ends the wait"):
            answer = self.wait(
                {"turnId": None},
                holds=agent("running", activeTurn=TURN),
                events=[{"type": "turn_canceled", "turnId": "turn-6"}],
                afterEvent={"status": "idle", "activeTurn": None},
                waitTakes=8000,
                timeline=[sent("From architect\nreport your plan")],
            )
            self.assertEqual((answer["state"], answer["outcome"]), ("ended", "cancelled"))

    def test_a_turn_that_ended_before_the_call_is_read_from_the_agents_state(self) -> None:
        message = sent("From architect\nreport your plan")
        earlier = [sent("an earlier message", "another-id"), reply("An earlier reply.")]
        cases: dict[str, tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]] = {
            "a reply after the message": (
                agent("idle"),
                [*earlier, message, {"type": "tool_call", "name": "shell"}, reply("  Done.  ")],
                {"outcome": "finished", "text": "Done.", "textTruncated": False},
            ),
            "a reply in several parts, behind text that preceded a tool call": (
                agent("idle"),
                [
                    message,
                    reply("Looking."),
                    {"type": "tool_call", "name": "shell"},
                    reply("Do"),
                    reply("ne."),
                ],
                {"outcome": "finished", "text": "Done.", "textTruncated": False},
            ),
            "no reply after the message": (
                agent("idle"),
                [*earlier, message, {"type": "tool_call", "name": "shell"}],
                {"outcome": "cancelled", "text": None, "textTruncated": False},
            ),
            "the message itself closes the timeline": (
                agent("idle"),
                [*earlier, message],
                {"outcome": "cancelled", "text": None, "textTruncated": False},
            ),
            "the agent is in an error state": (
                agent("error", lastError="The model refused the turn."),
                [*earlier, message],
                {
                    "outcome": "failed",
                    "error": "The model refused the turn.",
                    "text": None,
                    "textTruncated": False,
                },
            ),
            "a reply longer than the limit": (
                agent("idle"),
                [message, reply("é" * 20001)],
                {"outcome": "finished", "text": "é" * 20000, "textTruncated": True},
            ),
        }
        for label, (held, timeline, ended) in cases.items():
            with self.subTest(label):
                answer = self.wait(holds=held, timeline=timeline)
                self.assertEqual(answer, {"state": "ended", **ended})
                # Nothing is waited for or subscribed to once the turn is over.
                self.assertEqual(self.recorded("waitForFinish", "timeline.subscribe", "send"), [])

    def test_a_pending_permission_and_a_turn_that_still_runs_are_reported_as_such(self) -> None:
        pending = [{"id": "perm-1", "provider": "steering", "name": "Bash", "kind": "tool"}]
        with self.subTest("a permission is pending when the call begins"):
            answer = self.wait(holds=agent("running", activeTurn=TURN, pendingPermissions=pending))
            self.assertEqual(answer, {"state": "permission", "permission": "Bash"})
            self.assertEqual(self.recorded(), [READ])
        with self.subTest("the agent asks for a permission during the wait"):
            answer = self.wait(
                holds=agent("running", activeTurn=TURN),
                waitStatus="permission",
                afterWait={"pendingPermissions": pending},
            )
            self.assertEqual(answer, {"state": "permission", "permission": "Bash"})
        with self.subTest("the time is up and the turn still runs"):
            answer = self.wait(holds=agent("running", activeTurn=TURN), waitStatus="timeout")
            self.assertEqual(answer, {"state": "running"})
            self.assertEqual(
                self.recorded("waitForFinish"),
                [{"via": "waitForFinish", "id": AGENT_ID, "timeoutMs": 400}],
            )
            self.assertEqual(self.recorded("timeline.refetch", "send"), [])
        with self.subTest("the events cannot be followed: the state afterwards decides"):
            answer = self.wait(
                holds=agent("running", activeTurn=TURN),
                subscribeError="subscription refused",
                afterWait={"status": "idle", "activeTurn": None},
                timeline=[sent("From architect\nreport your plan"), reply("Done.")],
            )
            self.assertEqual(answer["outcome"], "finished")
        with self.subTest("one wait never outlasts the limit of a bridge call"):
            self.wait(
                {"waitMs": 600000}, holds=agent("running", activeTurn=TURN), waitStatus="timeout"
            )
            self.assertLessEqual(self.recorded("waitForFinish")[0]["timeoutMs"], 55000 - 6000)

    def test_an_agent_that_cannot_be_waited_for_is_left_as_it_is(self) -> None:
        unavailable = {
            "the runtime has no such agent": (None, "not-found"),
            "an archived agent": (agent("closed", archivedAt=ARCHIVED_AT), "archived"),
            "a closed session": (agent("closed"), "closed"),
        }
        for label, (held, reason) in unavailable.items():
            with self.subTest(label):
                self.assertEqual(self.wait(holds=held), {"state": "unavailable", "reason": reason})
                # A timeline read would load the agent; none is made.
                self.assertEqual(self.recorded(), [READ])


class AgentParentScriptTests(RoleBridgeScriptTestCase):
    def create(self, **fields: Any) -> dict[str, Any]:
        payload = {
            "agentId": AGENT_ID,
            "idempotencyKey": "ar-role-launch:request",
            "workspaceId": "wks_1",
            "provider": "steering",
            "title": "Worker · LEAF",
            "labels": {"ar.role": "worker"},
            **fields,
        }
        self.call("agent-create", payload)
        return self.recorded("workspace.agents.create", "daemon.createAgent")[0]

    def test_the_starting_agent_is_named_to_the_runtime_as_the_parent(self) -> None:
        public = self.create(model="model-a", parentAgentId=PARENT_ID)
        self.assertEqual(
            (public["via"], public["options"]["parent"]), ("workspace.agents.create", PARENT_ID)
        )
        internal = self.create(parentAgentId=PARENT_ID)
        self.assertEqual(
            (
                internal["via"],
                internal["options"]["callerAgentId"],
                internal["options"]["workspaceId"],
            ),
            ("daemon.createAgent", PARENT_ID, "wks_1"),
        )
        with self.subTest("a start from the launcher names none"):
            self.assertNotIn("parent", self.create(model="model-a")["options"])
            self.assertNotIn("callerAgentId", self.create()["options"])


if __name__ == "__main__":
    unittest.main()
