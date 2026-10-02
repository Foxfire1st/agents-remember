"""The bridge's workspace and agent commands: the real script against a fake client package."""

from __future__ import annotations

import hashlib
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
OTHER_AGENT_ID = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
MISSING_AGENT_ID = "9f8e7d6c-5b4a-4c3d-9e2f-1a0b9c8d7e6f"

FAKE_DAEMON_CLIENT = """
import { createHash } from 'node:crypto'
import { appendFileSync, readFileSync } from 'node:fs'
export const scenario = JSON.parse(readFileSync(process.env.FAKE_PASEO_SCENARIO, 'utf8'))
export const agents = new Map(Object.entries(scenario.agents ?? {}))
export function record(call) {
  appendFileSync(process.env.FAKE_PASEO_RECORD, JSON.stringify(call) + '\\n')
}
// One creation, through the public client ('public') or the daemon client ('daemon'). The first
// message is recorded by size and digest only.
export function create(via, options, workspaceId, cwd) {
  const { prompt, initialPrompt, ...rest } = options
  const message = prompt ?? initialPrompt
  record({
    via,
    workspaceId,
    options: rest,
    prompt: message === undefined ? null : {
      bytes: Buffer.byteLength(message),
      sha256: createHash('sha256').update(message).digest('hex')
    }
  })
  const [provider, model] = String(options.config.provider).split('/')
  const agent = {
    id: scenario.createdId ?? options.agentId,
    provider,
    model: model ?? null,
    thinkingOptionId: options.config.thinkingOptionId ?? null,
    effectiveThinkingOptionId: options.config.thinkingOptionId ?? 'runtime-default',
    title: options.title ?? options.config.title,
    labels: options.labels,
    workspaceId,
    cwd,
    status: 'running',
    archivedAt: null,
    createdAt: '2026-10-02T00:00:00.000Z',
    persistence: { internal: true }
  }
  if (scenario.createError && !scenario.agentExistsAfterError) throw new Error(scenario.createError)
  agents.set(agent.id, agent)
  if (scenario.createError) throw new Error(scenario.createError)
  return agent
}
export class DaemonClient {
  constructor(config) { this.config = config; this.state = { status: 'idle' } }
  async connect() {
    if (scenario.connectError) throw new Error(scenario.connectError)
    if (this.config.url !== scenario.url) throw new Error('unexpected url ' + this.config.url)
    this.state = { status: 'connected' }
  }
  async close() { this.state = { status: 'disposed' } }
  getConnectionState() { return scenario.connectionLost ? { status: 'disconnected' } : this.state }
  getLastServerInfoMessage() { return { serverId: scenario.serverId, version: this.config.appVersion } }
  async createAgent(options) { return create('daemon', options, options.workspaceId, options.config.cwd) }
}
"""
FAKE_CLIENT_ROOT = """
import { agents, create, record, scenario } from './daemon-client.js'
export function createPaseoApi(daemon) {
  return {
    dispose: async () => {},
    workspaces: {
      open: async (cwd) => {
        record({ via: 'workspaces.open', cwd })
        if (scenario.openError) throw new Error(scenario.openError)
        const workspace = {
          id: 'wks_fake',
          workspaceDirectory: cwd,
          name: 'folder',
          projectId: 'prj_fake',
          projectKind: 'non_git',
          scripts: []
        }
        return { id: workspace.id, current: () => workspace }
      },
      ref: (id) => {
        const directory = scenario.workspaceDirectory
        const refresh = async () =>
          directory === null ? null : { id, workspaceDirectory: directory ?? '/work/folder' }
        return {
          id,
          refresh,
          agents: {
            create: async (options) => {
              const snapshot = await refresh()
              if (!snapshot) throw new Error(`Workspace ${id} has no available directory`)
              const agent = create('public', options, id, snapshot.workspaceDirectory)
              return { id: agent.id, current: () => agent }
            }
          }
        }
      }
    },
    agents: {
      ref: (id) => ({
        refresh: async () => {
          if (scenario.lookupError) throw new Error(scenario.lookupError)
          // The runtime also answers an id prefix or a title with the agent it resolves to.
          const agent = agents.get(scenario.resolves?.[id] ?? id)
          if (!agent) throw new Error(`Agent not found: ${id}`)
          return { agent, project: null }
        },
        archive: async () => {
          record({ via: 'archive', id })
          if (scenario.archiveError) {
            // Another caller got there first: by the time this call fails, the agent is
            // archived, or gone, or (for any other error) still live.
            if (scenario.afterArchiveError === 'archived') {
              agents.get(id).archivedAt = '2026-10-02T00:59:00.000Z'
            }
            if (scenario.afterArchiveError === 'gone') agents.delete(id)
            throw new Error(scenario.archiveError)
          }
          agents.get(id).archivedAt = '2026-10-02T01:00:00.000Z'
          return { archivedAt: agents.get(id).archivedAt }
        }
      })
    }
  }
}
"""


@unittest.skipUnless(shutil.which("node"), "the bridge script needs Node.js")
class AgentCommandScriptTests(unittest.TestCase):
    """Each call starts the real script; the fake client records what it was asked to do."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        settings = parse_paseo_runtime_settings(
            {
                "installPrefix": (self.root / "prefix").as_posix(),
                "home": (self.root / "home").as_posix(),
                "listen": "127.0.0.1:6835",
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
        scenario_path = self.root / "scenario.json"
        base = {"url": "ws://127.0.0.1:6835/ws", "serverId": SERVER_ID}
        scenario_path.write_text(json.dumps({**base, **scenario}), encoding="utf-8")
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

    def failure(self, command: str, payload: dict[str, Any], **scenario: Any) -> PaseoBridgeFailure:
        with self.assertRaises(PaseoBridgeFailure) as raised:
            self.call(command, payload, **scenario)
        return raised.exception

    def test_workspace_open_reuses_or_creates_through_the_public_client(self) -> None:
        reply = self.call("workspace-open", {"cwd": "/work/folder"})

        self.assertEqual(
            reply,
            {
                "serverId": SERVER_ID,
                "workspace": {
                    "id": "wks_fake",
                    "directory": "/work/folder",
                    "name": "folder",
                    "projectId": "prj_fake",
                    "projectKind": "non_git",
                },
            },
        )
        self.assertEqual(self.recorded(), [{"via": "workspaces.open", "cwd": "/work/folder"}])
        refused = self.failure(
            "workspace-open", {"cwd": "/gone"}, openError="Directory not found: /gone"
        )
        self.assertEqual(
            (refused.code, str(refused)), ("paseo_call_failed", "Directory not found: /gone")
        )
        self.assertEqual(self.failure("workspace-open", {}).code, "invalid_bridge_payload")

    def test_agent_create_uses_the_given_id_and_tells_created_existing_and_refused_apart(
        self,
    ) -> None:
        labels = {"ar.role": "worker", "ar.request-id": "request-1"}
        payload: dict[str, Any] = {
            "agentId": AGENT_ID,
            "idempotencyKey": "ar-role-launch:request-1",
            "workspaceId": "wks_fake",
            "provider": "codex",
            "model": "gpt-a",
            "thinkingOptionId": "low",
            "title": "Worker · 01_LEAF",
            "labels": labels,
            "prompt": "first message",
        }
        agent = {
            "id": AGENT_ID,
            "provider": "codex",
            "model": "gpt-a",
            "thinkingOptionId": "low",
            "title": "Worker · 01_LEAF",
            "labels": labels,
            "workspaceId": "wks_fake",
            "cwd": "/work/folder",
            "status": "running",
            "archivedAt": None,
            "createdAt": "2026-10-02T00:00:00.000Z",
        }
        identity = {"agentId": AGENT_ID, "idempotencyKey": "ar-role-launch:request-1"}
        with self.subTest("a model: the public client, with the provider's default mode"):
            reply = self.call("agent-create", payload)
            self.assertEqual(reply, {"serverId": SERVER_ID, "existing": False, "agent": agent})
            (creation,) = self.recorded()
            self.assertEqual(
                creation["options"],
                {
                    **identity,
                    "labels": labels,
                    "config": {"provider": "codex/gpt-a", "thinkingOptionId": "low"},
                    "title": "Worker · 01_LEAF",
                },
            )
            self.assertEqual((creation["via"], creation["workspaceId"]), ("public", "wks_fake"))
            self.assertEqual(creation["prompt"]["bytes"], len(b"first message"))
        with self.subTest("no model: the daemon client, and the runtime's own defaults"):
            modelless = {key: value for key, value in payload.items() if key != "model"}
            del modelless["thinkingOptionId"], modelless["prompt"]
            reply = self.call("agent-create", {**modelless, "provider": "eve"})
            self.assertEqual(
                reply["agent"],
                {**agent, "provider": "eve", "model": None, "thinkingOptionId": "runtime-default"},
            )
            (creation,) = self.recorded()
            self.assertEqual(
                creation["options"],
                {
                    **identity,
                    "labels": labels,
                    "config": {
                        "provider": "eve",
                        "cwd": "/work/folder",
                        "title": "Worker · 01_LEAF",
                    },
                    "workspaceId": "wks_fake",
                },
            )
            self.assertEqual((creation["via"], creation["prompt"]), ("daemon", None))
        stored = {**agent, "status": "idle", "internal": "not passed on"}
        with self.subTest("an agent that already has the id is returned and nothing is created"):
            reply = self.call("agent-create", payload, agents={AGENT_ID: stored})
            self.assertEqual(
                reply,
                {"serverId": SERVER_ID, "existing": True, "agent": {**agent, "status": "idle"}},
            )
            self.assertEqual(self.recorded(), [])
        with self.subTest("an error although the agent exists afterwards"):
            reply = self.call(
                "agent-create", payload, createError="prompt refused", agentExistsAfterError=True
            )
            self.assertEqual(
                (reply["existing"], reply["agent"]["id"], reply["creationError"]),
                (True, AGENT_ID, "prompt refused"),
            )
        refused: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = {
            "the runtime refuses and no agent exists": (
                "paseo_call_failed",
                payload,
                {"createError": "Provider codex is not configured"},
            ),
            "the workspace is unknown": (
                "paseo_call_failed",
                payload,
                {"workspaceDirectory": None},
            ),
            "the connection is lost during the creation": (
                "paseo_daemon_unreachable",
                payload,
                {"createError": "socket closed", "connectionLost": True},
            ),
            "the agent cannot be looked up": (
                "paseo_agent_lookup_failed",
                payload,
                {"lookupError": "storage is locked"},
            ),
            "the runtime answers with another agent id": (
                "paseo_bridge_invalid_reply",
                payload,
                {"createdId": OTHER_AGENT_ID},
            ),
            "no agent id": ("invalid_bridge_payload", {**payload, "agentId": ""}, {}),
            "labels that are not text": (
                "invalid_bridge_payload",
                {**payload, "labels": {"ar.role": 7}},
                {},
            ),
        }
        for label, (code, request, scenario) in refused.items():
            with self.subTest(label):
                self.assertEqual(self.failure("agent-create", request, **scenario).code, code)
        self.assertEqual(
            str(self.failure("agent-create", payload, createError="Provider x is not configured")),
            "Provider x is not configured",
        )

    def test_agent_get_and_archive_address_exactly_one_agent(self) -> None:
        live = {"id": AGENT_ID, "provider": "codex", "status": "idle", "archivedAt": None}
        archived = {**live, "id": OTHER_AGENT_ID, "archivedAt": "2026-10-01T00:00:00.000Z"}
        agents = {AGENT_ID: live, OTHER_AGENT_ID: archived}
        with self.subTest("get"):
            found = self.call("agent-get", {"agentId": AGENT_ID}, agents=agents)
            self.assertEqual((found["serverId"], found["agent"]["id"]), (SERVER_ID, AGENT_ID))
            self.assertEqual(found["agent"]["labels"], {})
            for asked, scenario in (
                (MISSING_AGENT_ID, {"agents": agents}),
                # The runtime resolved a prefix or a title to another agent.
                ("f3c1a2b4", {"agents": agents, "resolves": {"f3c1a2b4": AGENT_ID}}),
            ):
                reply = self.call("agent-get", {"agentId": asked}, **scenario)
                self.assertEqual(reply, {"serverId": SERVER_ID, "agent": None})
        with self.subTest("archive"):
            reply = self.call("agent-archive", {"agentId": AGENT_ID}, agents=agents)
            self.assertEqual(
                reply,
                {
                    "serverId": SERVER_ID,
                    "agentId": AGENT_ID,
                    "found": True,
                    "archived": True,
                    "alreadyArchived": False,
                    "archivedAt": "2026-10-02T01:00:00.000Z",
                },
            )
            self.assertEqual(self.recorded(), [{"via": "archive", "id": AGENT_ID}])
            reply = self.call("agent-archive", {"agentId": OTHER_AGENT_ID}, agents=agents)
            self.assertEqual(
                (reply["alreadyArchived"], reply["archivedAt"]), (True, archived["archivedAt"])
            )
            reply = self.call("agent-archive", {"agentId": MISSING_AGENT_ID}, agents=agents)
            self.assertEqual((reply["found"], reply["archived"]), (False, False))
            self.assertEqual(self.recorded(), [])
        racing = "Request failed: Unknown agent requestType=archive_agent_request"
        with self.subTest("another caller archives the agent at the same moment"):
            reply = self.call(
                "agent-archive",
                {"agentId": AGENT_ID},
                agents=agents,
                archiveError=racing,
                afterArchiveError="archived",
            )
            self.assertEqual(
                reply,
                {
                    "serverId": SERVER_ID,
                    "agentId": AGENT_ID,
                    "found": True,
                    "archived": True,
                    "alreadyArchived": True,
                    "archivedAt": "2026-10-02T00:59:00.000Z",
                },
            )
            self.assertEqual(self.recorded(), [{"via": "archive", "id": AGENT_ID}])
        with self.subTest("the agent is gone when the archive fails"):
            reply = self.call(
                "agent-archive",
                {"agentId": AGENT_ID},
                agents=agents,
                archiveError=racing,
                afterArchiveError="gone",
            )
            self.assertEqual(
                reply,
                {"serverId": SERVER_ID, "agentId": AGENT_ID, "found": False, "archived": False},
            )
        with self.subTest("the archive fails and the agent is still live"):
            failure = self.failure(
                "agent-archive", {"agentId": AGENT_ID}, agents=agents, archiveError="disk is full"
            )
            self.assertEqual((failure.code, str(failure)), ("paseo_call_failed", "disk is full"))

    def test_a_300_000_byte_first_message_reaches_the_client_as_data(self) -> None:
        # More than twice what one command-line argument can carry (131,072 bytes on Linux).
        filler = "Rollenauftrag für den Worker · 役割 — "
        prompt = filler * (300_000 // len(filler.encode("utf-8")))
        prompt += "x" * (300_000 - len(prompt.encode("utf-8")))
        payload = {
            "agentId": AGENT_ID,
            "idempotencyKey": "ar-role-launch:request-1",
            "workspaceId": "wks_fake",
            "provider": "codex",
            "model": "gpt-a",
            "title": "Worker · 01_LEAF",
            "labels": {},
            "prompt": prompt,
        }

        reply = self.call("agent-create", payload)

        self.assertEqual((reply["existing"], reply["agent"]["id"]), (False, AGENT_ID))
        (creation,) = self.recorded()
        self.assertEqual(
            creation["prompt"],
            {"bytes": 300_000, "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()},
        )


if __name__ == "__main__":
    unittest.main()
