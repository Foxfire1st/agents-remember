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

from agents_remember.cli import paseo_bridge
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
# A send does what the scenario says the runtime does with it (`afterSend`), or loses the
# connection (`sendDisconnects`). From the first look at the agent on, the scenario's `steps` run:
# each, `after` its milliseconds, changes the agent (`set`), adds timeline entries (`timeline`)
# and delivers a turn event to the subscribers (`event`). Anything that would resume, archive or
# answer for an agent fails.
FAKE_CLIENT_ROOT = """
import { agents, record, scenario } from './daemon-client.js'
const timeline = [...(scenario.timeline ?? [])]
const handlers = new Set()
let began = false
let sends = 0
function begin(id) {
  if (began) return
  began = true
  for (const step of scenario.steps ?? []) {
    setTimeout(() => {
      Object.assign(agents.get(id), step.set ?? {})
      timeline.push(...(step.timeline ?? []))
      if (step.event) for (const handler of handlers) handler({ agentId: id, event: step.event })
    }, step.after)
  }
}
function forbidden(via, id) {
  return async () => {
    record({ via, id })
    throw new Error(via + ' is not allowed here')
  }
}
function handle(id, daemon) {
  return {
    refresh: async () => {
      record({ via: 'refresh', id })
      begin(id)
      if (sends > 0 && scenario.lookupFailsAfterSend) throw new Error('lookup failed')
      const agent = agents.get(id)
      if (!agent) throw new Error(`Agent not found: ${id}`)
      return { agent, project: null }
    },
    send: async (text, options) => {
      record({ via: 'send', id, text, options })
      sends += 1
      if (scenario.sendDisconnects) {
        daemon.state = { status: 'disconnected' }
        throw new Error('socket closed')
      }
      if (scenario.sendError) throw new Error(scenario.sendError)
      Object.assign(agents.get(id), scenario.afterSend ?? {})
    },
    waitForFinish: forbidden('waitForFinish', id),
    timeline: {
      subscribe: (handler) => {
        record({ via: 'timeline.subscribe', id })
        const unsubscribe = () => {
          handlers.delete(handler)
          record({ via: 'timeline.unsubscribe', id })
        }
        unsubscribe.ready = (async () => {
          if (scenario.subscribeError) throw new Error(scenario.subscribeError)
          handlers.add(handler)
        })()
        return unsubscribe
      },
      refetch: async (options) => {
        record({ via: 'timeline.refetch', id, options })
        return { agent: agents.get(id), entries: timeline.slice(-options.limit), hasOlder: false }
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
    agents: { ref: (id) => handle(id, daemon) },
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


def sent(text: str, message_id: str = MESSAGE_ID, turn: str | None = "turn-7") -> dict[str, Any]:
    """A timeline entry: a message, recorded with its id in the turn that was running."""

    return {"turnId": turn, "item": {"type": "user_message", "text": text, "messageId": message_id}}


def reply(text: str, turn: str | None = "turn-7") -> dict[str, Any]:
    return {"turnId": turn, "item": {"type": "assistant_message", "text": text}}


def tool(turn: str | None = "turn-7") -> dict[str, Any]:
    return {"turnId": turn, "item": {"type": "tool_call", "name": "shell"}}


def ends(turn: str, after: int, kind: str = "turn_completed", **event: Any) -> dict[str, Any]:
    """A step: the turn ends, the agent is idle, and the runtime says so with its event."""

    return {
        "after": after,
        "set": {"status": "idle", "activeTurn": None},
        "event": {"type": kind, "turnId": turn, **event},
    }


def begins(turn: str, after: int, *entries: dict[str, Any]) -> dict[str, Any]:
    """A step: the agent begins a turn, with the entries the runtime records at its start."""

    return {
        "after": after,
        "set": {"status": "running", "activeTurn": {"turnId": turn}},
        "event": {"type": "turn_started", "turnId": turn},
        "timeline": list(entries),
    }


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

    def test_an_agent_that_waits_for_a_permission_decision_is_not_sent_to(self) -> None:
        # The runtime answers every pending permission with a denial when it delivers a message.
        named = {"id": "perm-1", "provider": "steering", "name": "Bash", "kind": "tool"}
        cases = {
            "the runtime names the permission": ([named], {"permission": "Bash"}),
            "it gives a title only": (
                [{"id": "perm-2", "title": "Edit file"}],
                {"permission": "Edit file"},
            ),
            "it names nothing": ([{"id": "perm-3"}], {}),
            "several are pending: the first is named": (
                [named, {"id": "p", "name": "Edit"}],
                {"permission": "Bash"},
            ),
        }
        for label, (pending, name) in cases.items():
            with self.subTest(label):
                held = agent("running", activeTurn=TURN, pendingPermissions=pending)
                self.assertEqual(
                    self.send(holds=held),
                    {
                        "delivered": False,
                        "refused": "busy",
                        "detail": "the agent waits for a permission decision, which a message "
                        "would answer with a denial",
                        "permissionPending": True,
                        **name,
                    },
                )
                # Nothing was sent, so the permission is still the developer's to answer.
                self.assertEqual(self.recorded(), [READ])
        with self.subTest("a refusal for another reason says nothing of a permission"):
            self.assertNotIn("permissionPending", self.send(holds=agent("initializing")))

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

    def test_a_failure_around_the_send_says_what_is_known_of_the_delivery(self) -> None:
        with self.subTest("the connection is lost while the message is being sent"):
            with self.assertRaises(PaseoBridgeFailure) as raised:
                self.send(holds=agent("idle"), sendDisconnects=True)
            self.assertEqual(raised.exception.code, "paseo_send_outcome_unknown")
            self.assertIn("not known whether the message was delivered", str(raised.exception))
        with self.subTest("the runtime accepted the message and the lookup afterwards fails"):
            cases = {"started": agent("idle"), "steered": agent("running", activeTurn=TURN)}
            for taken, held in cases.items():
                delivery = self.send(holds=held, lookupFailsAfterSend=True)
                self.assertEqual(delivery, {"delivered": True, "taken": taken, "turnId": None})
                self.assertEqual(len(self.recorded("send")), 1)


MESSAGE = sent("From architect\nreport your plan")
FIRST_PROMPT = sent("The first message of the launch.", "the-launch")
ASKED = "What outcome do you want to achieve?"


class AgentWaitScriptTests(RoleBridgeScriptTestCase):
    """The wait for a message that began its turn: that turn consumed it."""

    def wait(self, payload: dict[str, Any] | None = None, **scenario: Any) -> dict[str, Any]:
        request = {
            "agentId": AGENT_ID,
            "messageId": MESSAGE_ID,
            "turnId": "turn-7",
            "waitMs": 400,
        }
        return self.call("agent-wait", {**request, **(payload or {})}, **scenario)["wait"]

    def test_a_turn_that_ends_during_the_call_is_reported_by_the_runtimes_own_event(self) -> None:
        timeline = [MESSAGE, reply("The plan: "), reply("read, change, test.\n")]
        events: dict[str, tuple[str, dict[str, Any]]] = {
            "finished": ("turn_completed", {}),
            "failed": ("turn_failed", {"error": "usage limit reached"}),
            "cancelled": ("turn_canceled", {}),
        }
        for outcome, (kind, fields) in events.items():
            with self.subTest(outcome):
                began = time.monotonic()
                answer = self.wait(
                    {"waitMs": 30000},
                    holds=agent("running", activeTurn=TURN),
                    steps=[ends("turn-7", 150, kind, **fields)],
                    timeline=timeline,
                )
                elapsed = time.monotonic() - began
                self.assertEqual(
                    answer,
                    {
                        "state": "ended",
                        "outcome": outcome,
                        **fields,
                        "text": "The plan: read, change, test.",
                        "textTruncated": False,
                    },
                )
                # The event woke the wait, and a turn the message began is given no time in
                # which another could follow.
                self.assertLess(elapsed, 4.5)
                self.assertEqual(self.recorded("timeline.refetch")[0]["options"], TAIL)
                self.assertEqual(self.recorded("send", "run", "archive", "respondToPermission"), [])
                self.assertEqual(len(self.recorded("timeline.unsubscribe")), 1)
        with self.subTest("the end of another turn is not the outcome of this one"):
            answer = self.wait(
                holds=agent("running", activeTurn=TURN),
                steps=[
                    {
                        "after": 50,
                        "event": {"type": "turn_failed", "turnId": "turn-6", "error": "x"},
                    }
                ],
            )
            self.assertEqual(answer, {"state": "running", "turnId": "turn-7"})
        with self.subTest("the end of another turn, seen before, is not this turn's outcome"):
            # This turn's own event does not arrive; what it left in the timeline decides.
            answer = self.wait(
                {"waitMs": 30000},
                holds=agent("running", activeTurn=TURN),
                steps=[
                    {
                        "after": 50,
                        "event": {"type": "turn_failed", "turnId": "turn-6", "error": "x"},
                    },
                    {"after": 200, "set": {"status": "idle", "activeTurn": None}},
                ],
                timeline=timeline,
            )
            self.assertEqual(
                (answer["outcome"], answer.get("error"), answer["text"]),
                ("finished", None, "The plan: read, change, test."),
            )
        with self.subTest("without a named turn the running turn is followed and named"):
            answer = self.wait(
                {"turnId": None}, holds=agent("running", activeTurn=TURN), timeline=[MESSAGE]
            )
            self.assertEqual(answer, {"state": "running", "turnId": "turn-7"})

    def test_a_turn_that_ended_before_the_call_is_read_from_the_agents_state(self) -> None:
        earlier = [sent("an earlier message", "another-id", "turn-6"), reply("Earlier.", "turn-6")]
        cases: dict[str, tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]] = {
            "a reply after the message": (
                agent("idle"),
                [*earlier, MESSAGE, tool(), reply("  Done.  ")],
                {"outcome": "finished", "text": "Done.", "textTruncated": False},
            ),
            "a reply in several parts, behind text that preceded a tool call": (
                agent("idle"),
                [MESSAGE, reply("Looking."), tool(), reply("Do"), reply("ne.")],
                {"outcome": "finished", "text": "Done.", "textTruncated": False},
            ),
            "no reply after the message": (
                agent("idle"),
                [*earlier, MESSAGE, tool()],
                {"outcome": "cancelled", "text": None, "textTruncated": False},
            ),
            "the message itself closes the timeline": (
                agent("idle"),
                [*earlier, MESSAGE],
                {"outcome": "cancelled", "text": None, "textTruncated": False},
            ),
            "the agent is in an error state": (
                agent("error", lastError="The model refused the turn."),
                [*earlier, MESSAGE],
                {
                    "outcome": "failed",
                    "error": "The model refused the turn.",
                    "text": None,
                    "textTruncated": False,
                },
            ),
            "a reply longer than the limit": (
                agent("idle"),
                [MESSAGE, reply("é" * 20001)],
                {"outcome": "finished", "text": "é" * 20000, "textTruncated": True},
            ),
            "a timeline without turn ids": (
                agent("idle"),
                [
                    sent("an earlier message", "another-id", None),
                    MESSAGE | {"turnId": None},
                    reply("Done.", None),
                ],
                {"outcome": "finished", "text": "Done.", "textTruncated": False},
            ),
        }
        for label, (held, timeline, ended) in cases.items():
            with self.subTest(label):
                began = time.monotonic()
                answer = self.wait(holds=held, timeline=timeline)
                self.assertEqual(answer, {"state": "ended", **ended})
                self.assertLess(time.monotonic() - began, 4.5)
                self.assertEqual(self.recorded("send", "waitForFinish"), [])

    def test_without_a_named_turn_a_message_nothing_follows_yet_is_given_time_to_begin_its_turn(
        self,
    ) -> None:
        # The send could not name the turn, and the agent is idle: the turn may not have begun.
        steps = [
            begins("turn-7", 400),
            {"after": 600, "timeline": [reply("Done.")]},
            ends("turn-7", 700),
        ]
        answer = self.wait(
            {"turnId": None, "waitMs": 30000},
            holds=agent("idle"),
            steps=steps,
            timeline=[MESSAGE],
        )
        self.assertEqual(
            answer,
            {"state": "ended", "outcome": "finished", "text": "Done.", "textTruncated": False},
        )
        with self.subTest("no turn begins: the message was not answered"):
            began = time.monotonic()
            answer = self.wait(
                {"turnId": None, "waitMs": 30000}, holds=agent("idle"), timeline=[MESSAGE]
            )
            self.assertEqual((answer["state"], answer["outcome"]), ("ended", "cancelled"))
            self.assertGreater(time.monotonic() - began, 5.0)
        with self.subTest("the time is up first"):
            answer = self.wait({"turnId": None}, holds=agent("idle"), timeline=[MESSAGE])
            self.assertEqual(answer, {"state": "running", "turnId": None})
        with self.subTest("a reply stands behind the message: its turn is over"):
            began = time.monotonic()
            answer = self.wait(
                {"turnId": None}, holds=agent("idle"), timeline=[MESSAGE, reply("Done.")]
            )
            self.assertEqual((answer["outcome"], answer["text"]), ("finished", "Done."))
            self.assertLess(time.monotonic() - began, 4.5)

    def test_a_turn_the_agent_runs_afterwards_is_not_the_one_the_message_began(self) -> None:
        answered = [MESSAGE, reply("The plan.")]
        with self.subTest("a turn without a message of its own"):
            # The agent's state now is that of the later turn: it is not this turn's outcome.
            answer = self.wait(
                holds=agent("running", activeTurn={"turnId": "turn-8"}, lastError="later failure"),
                timeline=[*answered, tool("turn-8"), reply("Something else.", "turn-8")],
            )
            self.assertEqual(
                answer,
                {
                    "state": "ended",
                    "outcome": "finished",
                    "text": "The plan.",
                    "textTruncated": False,
                },
            )
        with self.subTest("a turn a newer message began"):
            newer = sent("a newer message", "newer-id", "turn-8")
            answer = self.wait(
                holds=agent("running", activeTurn={"turnId": "turn-8"}),
                timeline=[*answered, newer, reply("An answer to the newer one.", "turn-8")],
            )
            self.assertEqual((answer["state"], answer["text"]), ("ended", "The plan."))
        with self.subTest("the message is not among the entries read: nothing is said to answer"):
            for held in (agent("idle"), agent("running", activeTurn={"turnId": "turn-8"})):
                answer = self.wait(
                    holds=held, timeline=[sent("another", "another-id"), reply("Another reply.")]
                )
                self.assertEqual(answer["state"], "undecided")
                self.assertIn("not among the last 200 timeline entries", answer["reason"])
                self.assertNotIn("text", answer)

    def test_a_pending_permission_and_a_turn_that_still_runs_are_reported_as_such(self) -> None:
        pending = [{"id": "perm-1", "provider": "steering", "name": "Bash", "kind": "tool"}]
        with self.subTest("a permission is pending when the call begins"):
            answer = self.wait(holds=agent("running", activeTurn=TURN, pendingPermissions=pending))
            self.assertEqual(answer, {"state": "permission", "permission": "Bash"})
            self.assertEqual(self.recorded("timeline.refetch", "send"), [])
        with self.subTest("the agent asks for a permission during the wait"):
            answer = self.wait(
                {"waitMs": 30000},
                holds=agent("running", activeTurn=TURN),
                steps=[{"after": 100, "set": {"pendingPermissions": pending}}],
            )
            self.assertEqual(answer, {"state": "permission", "permission": "Bash"})
        with self.subTest("the time is up and the turn still runs"):
            began = time.monotonic()
            answer = self.wait(holds=agent("running", activeTurn=TURN))
            self.assertEqual(answer, {"state": "running", "turnId": "turn-7"})
            self.assertLess(time.monotonic() - began, 4.5)
            self.assertEqual(self.recorded("timeline.refetch", "send"), [])
        with self.subTest("the events cannot be followed: the state afterwards decides"):
            answer = self.wait(
                {"waitMs": 30000},
                holds=agent("running", activeTurn=TURN),
                subscribeError="subscription refused",
                steps=[
                    {"after": 100, "set": {"status": "idle", "activeTurn": None}},
                ],
                timeline=[MESSAGE, reply("Done.")],
            )
            self.assertEqual((answer["state"], answer["outcome"]), ("ended", "finished"))
        with (
            self.subTest("one wait never outlasts the limit of a bridge call"),
            patch.object(paseo_bridge, "_SCRIPT_DEADLINE_MS", 7000),
        ):
            began = time.monotonic()
            answer = self.wait({"waitMs": 600000}, holds=agent("running", activeTurn=TURN))
            self.assertEqual(answer["state"], "running")
            # The script's deadline less the reserve of a wait: one second here.
            self.assertLess(time.monotonic() - began, 10.0)

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
        with self.subTest("the agent is archived during the wait"):
            answer = self.wait(
                {"waitMs": 30000},
                holds=agent("running", activeTurn=TURN),
                steps=[{"after": 100, "set": {"status": "closed", "archivedAt": ARCHIVED_AT}}],
            )
            self.assertEqual(answer, {"state": "unavailable", "reason": "archived"})


class SteeredWaitScriptTests(RoleBridgeScriptTestCase):
    """The wait for a message handed to a running turn: that turn or the next consumed it.

    The turn that was running is ``turn-7``; it began with the launch's first message, and the
    runtime recorded ours in it when it was sent.
    """

    def wait(self, payload: dict[str, Any] | None = None, **scenario: Any) -> dict[str, Any]:
        request = {
            "agentId": AGENT_ID,
            "messageId": MESSAGE_ID,
            "turnId": "turn-7",
            "steered": True,
            "waitMs": 30000,
        }
        return self.call("agent-wait", {**request, **(payload or {})}, **scenario)["wait"]

    def test_the_awaited_turn_ends_and_the_following_turn_consumes_the_message(self) -> None:
        held = agent("running", activeTurn=TURN)
        timeline = [FIRST_PROMPT, MESSAGE]
        took_it_up = [
            {"after": 100, "timeline": [reply(ASKED)]},
            ends("turn-7", 150),
            # No message is recorded for this turn: the harness runs the one it had kept.
            begins("turn-8", 700),
            {"after": 900, "timeline": [tool("turn-8"), reply("DONE-C", "turn-8")]},
            ends("turn-8", 1000),
        ]
        began = time.monotonic()

        answer = self.wait(holds=held, steps=took_it_up, timeline=timeline)

        elapsed = time.monotonic() - began
        # The answer says that it is about a turn that began after the one that was running.
        self.assertEqual(
            answer,
            {
                "state": "ended",
                "outcome": "finished",
                "text": "DONE-C",
                "textTruncated": False,
                "laterTurn": True,
            },
        )
        # The wait did not end with the turn that was running, and it ended with the turn that
        # took the message up: that turn is not given time for another.
        self.assertGreater(elapsed, 1.0)
        self.assertLess(elapsed, 5.5)
        with self.subTest("the time is up in the following turn: the answer names that turn"):
            answer = self.wait(
                {"waitMs": 1200}, holds=held, steps=took_it_up[:3], timeline=timeline
            )
            self.assertEqual(answer, {"state": "running", "turnId": "turn-8"})
        with self.subTest("the time is up between the two turns: nothing has ended yet"):
            answer = self.wait({"waitMs": 500}, holds=held, steps=took_it_up[:2], timeline=timeline)
            self.assertEqual(answer, {"state": "running", "turnId": "turn-7"})
        with self.subTest("both turns were over before the call"):
            over = [*timeline, reply(ASKED), tool("turn-8"), reply("DONE-C", "turn-8")]
            began = time.monotonic()
            answer = self.wait(holds=agent("idle"), timeline=over)
            self.assertEqual((answer["outcome"], answer["text"]), ("finished", "DONE-C"))
            self.assertLess(time.monotonic() - began, 4.5)
        with self.subTest("the following turn fails and leaves nothing: its event is the outcome"):
            failing = [*took_it_up[:3], ends("turn-8", 900, "turn_failed", error="usage limit")]
            answer = self.wait(holds=held, steps=failing, timeline=timeline)
            self.assertEqual(
                answer,
                {
                    "state": "ended",
                    "outcome": "failed",
                    "error": "usage limit",
                    "text": None,
                    "textTruncated": False,
                    "laterTurn": True,
                },
            )

    def test_a_turn_that_went_on_behind_the_message_is_the_one_that_consumed_it(self) -> None:
        timeline = [FIRST_PROMPT, MESSAGE]
        read_it = [
            {"after": 100, "timeline": [tool(), reply("Both answered.")]},
            ends("turn-7", 150),
        ]
        began = time.monotonic()

        answer = self.wait(
            holds=agent("running", activeTurn=TURN), steps=read_it, timeline=timeline
        )

        self.assertEqual(
            answer,
            {
                "state": "ended",
                "outcome": "finished",
                "text": "Both answered.",
                "textTruncated": False,
                "laterTurn": False,
            },
        )
        # The turn took another step behind the message, so it read it: no turn is waited for.
        self.assertLess(time.monotonic() - began, 4.5)
        with self.subTest("the turn's own first message is recorded behind ours: it is not newer"):
            answer = self.wait(
                holds=agent("running", activeTurn=TURN),
                steps=[
                    {"after": 100, "timeline": [FIRST_PROMPT, tool(), reply("Both answered.")]},
                    ends("turn-7", 150),
                ],
                timeline=[MESSAGE],
            )
            self.assertEqual((answer["outcome"], answer["text"]), ("finished", "Both answered."))
        with self.subTest("a turn that ended without a step is given the time for another"):
            steps = [{"after": 100, "timeline": [reply("The plan.")]}, ends("turn-7", 150)]
            began = time.monotonic()
            answer = self.wait(
                holds=agent("running", activeTurn=TURN), steps=steps, timeline=timeline
            )
            # The text is the running turn's, and the answer says so.
            self.assertEqual(
                (answer["outcome"], answer["text"], answer["laterTurn"]),
                ("finished", "The plan.", False),
            )
            self.assertGreater(time.monotonic() - began, 5.0)
        with self.subTest("a newer message began the next turn: the wait ends at once"):
            newer = sent("a newer message", "newer-id", "turn-8")
            began = time.monotonic()
            answer = self.wait(
                holds=agent("running", activeTurn=TURN),
                steps=[*steps, begins("turn-8", 400, newer)],
                timeline=timeline,
            )
            self.assertEqual((answer["outcome"], answer["text"]), ("finished", "The plan."))
            self.assertLess(time.monotonic() - began, 4.5)
        with self.subTest("a newer message stands behind ours before the call"):
            newer = sent("a newer message", "newer-id", "turn-8")
            began = time.monotonic()
            answer = self.wait(
                holds=agent("idle"),
                timeline=[*timeline, reply("The plan."), newer, reply("Another.", "turn-8")],
            )
            self.assertEqual((answer["outcome"], answer["text"]), ("finished", "The plan."))
            self.assertLess(time.monotonic() - began, 4.5)


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
