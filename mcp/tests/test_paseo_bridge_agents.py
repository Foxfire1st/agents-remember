"""The bridge's workspace and agent commands: the real script against a fake client package.

Also the catalog's report on tool servers, which a launch reads before it creates an agent.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents_remember.cli import paseo_bridge
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure, bridge_call
from agents_remember.kernel.primitives.paseo_host_contract import PASEO_VERSION
from agents_remember.kernel.primitives.paseo_runtime_settings import parse_paseo_runtime_settings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from paseo_runtime_test_support import write_shared_runtime

SERVER_ID = "srv_configured"
AGENT_ID = "f3c1a2b4-5d6e-4f70-8a91-b2c3d4e5f607"
OTHER_AGENT_ID = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
MISSING_AGENT_ID = "9f8e7d6c-5b4a-4c3d-9e2f-1a0b9c8d7e6f"
# The time the script gives an agent's tool servers before the first message, read from the
# script: the fake client records the beginning and the end of a wait of exactly this length and
# ends it at once. What the script does before the wait has ended is recorded between the two.
TOOL_SERVER_START_MS = int(
    re.findall(
        r"^const TOOL_SERVER_START_MS = (\d+)$",
        Path(paseo_bridge.__file__).with_name("paseo_bridge.mjs").read_text(encoding="utf-8"),
        re.MULTILINE,
    )[0]
)
WAITED = [
    {"via": "wait", "ms": TOOL_SERVER_START_MS},
    {"via": "wait-ended", "ms": TOOL_SERVER_START_MS},
]

FAKE_DAEMON_CLIENT = """
import { createHash } from 'node:crypto'
import { appendFileSync, readFileSync } from 'node:fs'
export const scenario = JSON.parse(readFileSync(process.env.FAKE_PASEO_SCENARIO, 'utf8'))
export const agents = new Map(Object.entries(scenario.agents ?? {}))
export function record(call) {
  appendFileSync(process.env.FAKE_PASEO_RECORD, JSON.stringify(call) + '\\n')
}
// A wait of the length the scenario names is recorded when it begins and when it ends, and ends
// at once; every other timer runs.
const realSetTimeout = globalThis.setTimeout
globalThis.setTimeout = (callback, ms, ...rest) => {
  if (ms !== scenario.endsWaitsOf) return realSetTimeout(callback, ms, ...rest)
  record({ via: 'wait', ms })
  return realSetTimeout(() => {
    record({ via: 'wait-ended', ms })
    callback(...rest)
  }, 0)
}
// A message is recorded by size and digest only.
export function measured(message) {
  return message === undefined ? null : {
    bytes: Buffer.byteLength(message),
    sha256: createHash('sha256').update(message).digest('hex')
  }
}
// One creation, through the public client ('public') or the daemon client ('daemon'), with the
// message it carried, if it carried one.
export function create(via, options, workspaceId, cwd) {
  const { prompt, initialPrompt, ...rest } = options
  record({ via, workspaceId, options: rest, prompt: measured(prompt ?? initialPrompt) })
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
    status: 'idle',
    archivedAt: null,
    createdAt: '2026-10-02T00:00:00.000Z',
    lastUserMessageAt: null,
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
  async listProviderFeatures(options) { record({ via: 'features', options }); return { features: scenario.features ?? [{ type: 'select', id: 'service_tier', options: [{ id: 'default', label: 'Normal' }, { id: 'priority', label: 'Fast' }] }] } }
  async createAgent(options) { return create('daemon', options, options.workspaceId, options.config.cwd) }
  async addProject(cwd) {
    record({ via: 'addProject', cwd })
    if (scenario.projectError) throw new Error(scenario.projectError)
    return { project: { projectId: scenario.projectId ?? 'prj_mapped' } }
  }
  async renameProject(projectId, name) { record({ via: 'renameProject', projectId, name }) }
}
"""
FAKE_CLIENT_ROOT = """
import { agents, create, measured, record, scenario } from './daemon-client.js'
export function createPaseoApi(daemon) {
  return {
    dispose: async () => {},
    providers: {
      waitForReady: async () => ({ entries: scenario.entries ?? [] }),
      listFeatures: async () => ({ features: [] }),
      listModels: async () => ({ models: [] })
    },
    config: {
      get: async () => {
        if (scenario.configError) throw new Error(scenario.configError)
        return { requestId: 'r', config: { providers: scenario.providerEntries ?? {} } }
      }
    },
    workspaces: {
      create: async (options) => {
        record({ via: 'workspaces.create', options })
        if (scenario.workspaceCreateError) throw new Error(scenario.workspaceCreateError)
        const workspace = {
          id: 'wks_' + options.idempotencyKey.slice(-16),
          workspaceDirectory: scenario.mappedDirectory ?? options.source.path,
          projectId: scenario.mappedProjectId ?? options.source.projectId,
          projectKind: 'non_git', archivingAt: scenario.archivingAt ?? null,
          name: 'previous title'
        }
        options.onEvent?.(scenario.workspaceFound ? { phase: 'completed', workspace } : { phase: 'accepted' })
        return {
          refresh: async () => {
            record({ via: 'workspace.refresh', id: workspace.id })
            return scenario.mappedMissing ? null : workspace
          },
          setTitle: async (title) => {
            record({ via: 'workspace.setTitle', id: workspace.id, title })
            return { title }
          }
        }
      },
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
        send: async (message, options) => {
          record({ via: 'send', id, options, message: measured(message) })
          if (scenario.sendError) throw new Error(scenario.sendError)
        },
        timeline: {
          // Reading the timeline of a closed session is the runtime's resume.
          refetch: async (options) => {
            record({ via: 'timeline.refetch', id, options })
            if (scenario.resumeError) throw new Error(scenario.resumeError)
            return { entries: [], hasOlder: false }
          }
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
                "version": PASEO_VERSION,
                "providers": {},
                "embed": [],
            }
        )
        assert settings is not None
        settings.home.mkdir(parents=True)
        (settings.home / "server-id").write_text(SERVER_ID + "\n", encoding="utf-8")
        write_shared_runtime(self.root, settings)
        self.config = McpRuntimeConfig(
            config_path=self.root / "settings" / "mcp.json",
            coordination_root=self.root / "coordination",
            workspace_root=self.root / "projects",
            transcript_root=self.root / "coordination" / "logs" / "mcp",
        )
        fixture_node = self.root / "fixture-node" / "bin" / "node"
        fixture_node.parent.mkdir(parents=True, exist_ok=True)
        executable = shutil.which("node")
        assert executable is not None
        fixture_node.symlink_to(executable)
        node_patch = patch(
            "agents_remember.cli.paseo_bridge.product_node",
            return_value=type("FixtureNode", (), {"node": fixture_node})(),
        )
        node_patch.start()
        self.addCleanup(node_patch.stop)
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
        if command == "catalog":
            payload = {"cwd": "/work/folder", **payload}
        scenario_path = self.root / "scenario.json"
        base = {
            "url": "ws://127.0.0.1:6835/ws",
            "serverId": SERVER_ID,
            "endsWaitsOf": TOOL_SERVER_START_MS,
        }
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

    def test_service_tier_is_native_creation_data_and_unsupported_is_rejected_before_create(
        self,
    ) -> None:
        payload = {
            "agentId": AGENT_ID,
            "idempotencyKey": "ar-role-launch:tier",
            "workspaceId": "wks_fake",
            "provider": "codex",
            "model": "gpt-6.1-sol",
            "thinkingOptionId": "xhigh",
            "title": "Architect",
            "labels": {},
            "featureValues": {"service_tier": "priority"},
        }
        for model in ["gpt-6.1-sol", None]:
            with self.subTest(model=model):
                call = {**payload}
                if model is None:
                    del call["model"]
                self.call("agent-create", call)
                records = self.recorded()
                created = next(row for row in records if row["via"] in {"public", "daemon"})
                self.assertEqual(
                    created["options"]["config"]["featureValues"], {"service_tier": "priority"}
                )
                self.assertEqual(created["options"]["config"]["thinkingOptionId"], "xhigh")
        with self.assertRaisesRegex(PaseoBridgeFailure, "not offered"):
            self.call("agent-create", payload, features=[])
        self.assertFalse(any(row["via"] in {"public", "daemon", "send"} for row in self.recorded()))
        invalid = {**payload, "featureValues": {"fast_mode": True}}
        with self.assertRaisesRegex(PaseoBridgeFailure, "service_tier"):
            self.call("agent-create", invalid)
        self.assertEqual(self.recorded(), [])

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
                "preparation": "opened",
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

    def test_task_workspace_identity_ignores_titles_and_separates_masters_at_one_cwd(self) -> None:
        payload: dict[str, Any] = {
            "cwd": "/projects",
            "masterProject": {
                "directory": "/tasks/master",
                "key": "repo/master/task.json",
                "name": "M · Master",
            },
            "task": {"key": "repo/master/task.json", "title": "M · Master"},
        }
        first = self.call("workspace-open", payload)
        calls = self.recorded()
        expected_key = (
            "ar-task-workspace:v2:"
            + hashlib.sha256(
                json.dumps(
                    [payload["masterProject"]["key"], payload["task"]["key"]], separators=(",", ":")
                ).encode()
            ).hexdigest()
        )
        self.assertEqual(
            calls[:3],
            [
                {"via": "addProject", "cwd": "/tasks/master"},
                {"via": "renameProject", "projectId": "prj_mapped", "name": "M · Master"},
                {
                    "via": "workspaces.create",
                    "options": {
                        "source": {
                            "kind": "directory",
                            "path": "/projects",
                            "projectId": "prj_mapped",
                        },
                        "idempotencyKey": expected_key,
                    },
                },
            ],
        )
        self.assertEqual(len(expected_key), 85)
        self.assertEqual(first["preparation"], "created")
        self.assertEqual(first["workspace"]["projectId"], "prj_mapped")
        self.assertEqual(calls[-1]["via"], "workspace.setTitle")
        payload["task"]["title"] = "M · Edited"
        changed = self.call("workspace-open", payload, workspaceFound=True)
        self.assertEqual(changed["preparation"], "found")
        self.assertEqual(changed["workspace"]["id"], first["workspace"]["id"])
        self.assertEqual(changed["workspace"]["name"], "M · Edited")
        payload["masterProject"].update(directory="/tasks/other", key="repo/other/task.json")
        payload["task"]["key"] = "repo/other/task.json"
        other = self.call("workspace-open", payload, projectId="prj_other")
        self.assertNotEqual(other["workspace"]["id"], first["workspace"]["id"])
        self.assertEqual(other["workspace"]["directory"], first["workspace"]["directory"])

    def test_explicit_workspace_refuses_partial_and_wrong_placement_without_directory_fallback(
        self,
    ) -> None:
        payload: dict[str, Any] = {
            "cwd": "/projects",
            "masterProject": {"directory": "/tasks/m", "key": "repo/m/task.json", "name": "M"},
            "task": {"key": "repo/m/task.json", "title": "M"},
        }
        for incomplete in (
            {"cwd": "/projects", "task": payload["task"]},
            {**payload, "masterProject": None},
            {**payload, "task": {}},
            {**payload, "masterProject": {"directory": "/tasks/m"}},
        ):
            with self.subTest(incomplete=incomplete):
                self.assertEqual(
                    self.failure("workspace-open", incomplete).code, "invalid_bridge_payload"
                )
                self.assertEqual(self.recorded(), [])
        for scenario in (
            {"mappedProjectId": "prj_wrong"},
            {"mappedDirectory": "/wrong"},
            {"mappedMissing": True},
            {"archivingAt": "2026-10-03T00:00:00Z"},
            {"workspaceCreateError": "workspace_request_key_conflict"},
        ):
            with self.subTest(scenario=scenario):
                self.assertEqual(
                    self.failure("workspace-open", payload, **scenario).code, "paseo_call_failed"
                )
                self.assertNotIn("workspaces.open", [call["via"] for call in self.recorded()])
                self.assertNotIn("workspace.setTitle", [call["via"] for call in self.recorded()])

    def test_agent_create_uses_the_given_id_and_tells_created_existing_and_refused_apart(
        self,
    ) -> None:
        labels = {"ar.role": "worker", "ar.request-id": "request-1"}
        # What the runtime keeps with the agent: the recovery note and the one tool server.
        kept = {
            "systemPrompt": "AR role agent: worker.",
            "mcpServers": {
                "agents-remember-task": {
                    "type": "stdio",
                    "command": "/build/.venv/bin/python",
                    "args": ["-m", "agents_remember.mcp", "--config", "/settings/ar.json"],
                    "env": {"AR_PASEO_AGENT_ID": AGENT_ID, "AR_ROLE": "worker"},
                }
            },
        }
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
            **kept,
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
            "status": "idle",
            "archivedAt": None,
            "createdAt": "2026-10-02T00:00:00.000Z",
        }
        identity = {"agentId": AGENT_ID, "idempotencyKey": "ar-role-launch:request-1"}
        with self.subTest("a model: the public client, with the provider's default mode"):
            reply = self.call("agent-create", payload)
            self.assertEqual(reply, {"serverId": SERVER_ID, "existing": False, "agent": agent})
            creation, *afterwards = self.recorded()
            self.assertEqual(
                creation["options"],
                {
                    **identity,
                    "labels": labels,
                    "config": {"provider": "codex/gpt-a", "thinkingOptionId": "low", **kept},
                    "title": "Worker · 01_LEAF",
                },
            )
            self.assertEqual((creation["via"], creation["workspaceId"]), ("public", "wks_fake"))
            # The creation carries no message; the first message follows it as a message.
            self.assertIsNone(creation["prompt"])
            self.assertEqual([row["via"] for row in afterwards], ["wait", "wait-ended", "send"])
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
                        **kept,
                    },
                    "workspaceId": "wks_fake",
                },
            )
            self.assertEqual((creation["via"], creation["prompt"]), ("daemon", None))
        stored = {**agent, "internal": "not passed on"}
        with self.subTest("an agent that already has the id is returned and nothing is created"):
            reply = self.call("agent-create", payload, agents={AGENT_ID: stored})
            self.assertEqual(reply, {"serverId": SERVER_ID, "existing": True, "agent": agent})
            self.assertEqual(
                [row["via"] for row in self.recorded()], ["wait", "wait-ended", "send"]
            )
        with self.subTest("an error although the agent exists afterwards"):
            reply = self.call(
                "agent-create", payload, createError="record lost", agentExistsAfterError=True
            )
            self.assertEqual(
                (reply["existing"], reply["agent"]["id"], reply["creationError"]),
                (True, AGENT_ID, "record lost"),
            )
            self.assertEqual(
                [row["via"] for row in self.recorded()], ["public", "wait", "wait-ended", "send"]
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
            "a system prompt that is not text": (
                "invalid_bridge_payload",
                {**payload, "systemPrompt": ["AR role agent"]},
                {},
            ),
            "tool servers that are not definitions by name": (
                "invalid_bridge_payload",
                {**payload, "mcpServers": [kept["mcpServers"]["agents-remember-task"]]},
                {},
            ),
            "a tool server without its environment": (
                "invalid_bridge_payload",
                {
                    **payload,
                    "mcpServers": {"agents-remember-task": {"type": "stdio", "command": "python"}},
                },
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

    def test_the_first_message_follows_the_creation_and_goes_to_an_agent_that_has_none(
        self,
    ) -> None:
        servers = {
            "agents-remember-task": {
                "type": "stdio",
                "command": "/build/.venv/bin/python",
                "args": ["-m", "agents_remember.mcp"],
                "env": {"AR_ROLE": "worker"},
            }
        }
        payload: dict[str, Any] = {
            "agentId": AGENT_ID,
            "idempotencyKey": "ar-role-launch:request-1",
            "workspaceId": "wks_fake",
            "provider": "codex",
            "model": "gpt-a",
            "title": "Worker · 01_LEAF",
            "labels": {},
            "prompt": "first message",
            "mcpServers": servers,
        }
        # The message id is the same on every run of the call: the runtime delivers a message id
        # once, also to two calls that run at the same time. The steer behaviour keeps the
        # runtime from cancelling a turn that runs by then.
        sent = {
            "via": "send",
            "id": AGENT_ID,
            "options": {
                "messageId": "ar-role-launch:request-1:first-message",
                "activeTurnBehavior": "steer",
            },
            "message": {
                "bytes": len(b"first message"),
                "sha256": hashlib.sha256(b"first message").hexdigest(),
            },
        }
        resumed = {
            "via": "timeline.refetch",
            "id": AGENT_ID,
            "options": {"direction": "tail", "limit": 1},
        }
        self.assertEqual(TOOL_SERVER_START_MS, 6000)
        with self.subTest("a new agent with tool servers: created, given the time, then sent to"):
            self.call("agent-create", payload)
            creation, *afterwards = self.recorded()
            self.assertEqual((creation["via"], creation["prompt"]), ("public", None))
            self.assertEqual(afterwards, [*WAITED, sent])
        with self.subTest("the same through the daemon client"):
            modelless = {key: value for key, value in payload.items() if key != "model"}
            self.call("agent-create", modelless)
            creation, *afterwards = self.recorded()
            self.assertEqual((creation["via"], creation["prompt"]), ("daemon", None))
            self.assertEqual(afterwards, [*WAITED, sent])
        with self.subTest("an agent without tool servers is sent to at once"):
            for without in ({}, {"mcpServers": {}}):
                bare = {key: value for key, value in payload.items() if key != "mcpServers"}
                self.call("agent-create", {**bare, **without})
                self.assertEqual(self.recorded()[1:], [sent])
        with self.subTest("no first message: the agent is left idle"):
            idle = {key: value for key, value in payload.items() if key != "prompt"}
            self.call("agent-create", idle)
            self.assertEqual([row["via"] for row in self.recorded()], ["public"])
        live = {"id": AGENT_ID, "provider": "codex", "status": "idle", "archivedAt": None}
        messaged = {"lastUserMessageAt": "2026-10-02T00:00:05.000Z"}
        repeats: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {
            # The call that created it ended before it sent the message.
            "an agent that never had a message": (live, [*WAITED, sent]),
            "the same with its session closed: opened first": (
                {**live, "status": "closed"},
                [resumed, *WAITED, sent],
            ),
            # An agent that has had a message has its first message: nothing is sent, waited
            # for or opened.
            "an agent that has a message": ({**live, **messaged}, []),
            "the same with its session closed": ({**live, **messaged, "status": "closed"}, []),
            "the same mid-turn": ({**live, **messaged, "status": "running"}, []),
            # Sending to an archived agent would make the runtime un-archive it.
            "an archived agent": ({**live, "archivedAt": "2026-10-02T01:00:00.000Z"}, []),
        }
        for label, (held, expected) in repeats.items():
            with self.subTest(f"a repeat finds {label}"):
                reply = self.call("agent-create", payload, agents={AGENT_ID: held})
                self.assertEqual((reply["existing"], reply["agent"]["id"]), (True, AGENT_ID))
                self.assertEqual(self.recorded(), expected)
        with self.subTest("the runtime does not take the message: a repeat can send it"):
            failure = self.failure(
                "agent-create", payload, sendError="agent_request_outcome_unknown"
            )
            self.assertEqual(
                (failure.code, str(failure)),
                (
                    "paseo_first_message_undelivered",
                    f"Agent {AGENT_ID} exists in the Paseo runtime, but its first message was "
                    "not delivered (agent_request_outcome_unknown). Repeat the request to send "
                    "it.",
                ),
            )
        with self.subTest("a closed session that cannot be opened: no repeat can send it"):
            # A harness can keep nothing of a session that never ran a turn.
            failure = self.failure(
                "agent-create",
                payload,
                agents={AGENT_ID: {**live, "status": "closed"}},
                resumeError="no rollout found for thread id 0199",
            )
            self.assertEqual(
                (failure.code, str(failure)),
                (
                    "paseo_agent_without_message_lost",
                    f"Agent {AGENT_ID} exists in the Paseo runtime without its first message, "
                    "and its closed session cannot be opened again (no rollout found for "
                    "thread id 0199).",
                ),
            )
            # Nothing was waited for and nothing was sent.
            self.assertEqual(self.recorded(), [resumed])
            lost = self.failure(
                "agent-create",
                payload,
                agents={AGENT_ID: {**live, "status": "closed"}},
                resumeError="socket closed",
                connectionLost=True,
            )
            self.assertEqual(lost.code, "paseo_daemon_unreachable")
        with self.subTest("the connection is lost while the message is sent"):
            failure = self.failure(
                "agent-create", payload, sendError="socket closed", connectionLost=True
            )
            self.assertEqual(failure.code, "paseo_daemon_unreachable")

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
        creation, sent = self.recorded()
        self.assertEqual(
            sent["message"],
            {"bytes": 300_000, "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()},
        )
        # A payload without a note and tool servers gives the runtime neither.
        self.assertEqual(creation["options"]["config"], {"provider": "codex/gpt-a"})

    def test_catalog_marks_the_providers_the_runtime_reports_as_taking_no_tool_servers(
        self,
    ) -> None:
        ready = {"status": "ready", "enabled": True}
        entries = [
            {"provider": "codex", "label": "Codex", **ready},
            {"provider": "hermes", "label": "Hermes", **ready},
            {"provider": "eve", "label": "Eve", **ready},
        ]
        # The provider entries of the runtime's configuration, as its configuration call returns
        # them. Only an entry that declares the option to be false withholds tool servers.
        declared = {
            "hermes": {"extends": "acp", "command": ["hermes", "acp"]},
            "eve": {
                "extends": "acp",
                "command": ["eve-launcher"],
                "env": {"MODEL_KEY": "not passed on"},
                "options": {"supportsMcpServers": False},
            },
        }

        reply = self.call("catalog", {}, entries=entries, providerEntries=declared)

        self.assertEqual(
            reply["providers"],
            [
                {"id": "codex", "label": "Codex", "models": []},
                {"id": "hermes", "label": "Hermes", "models": []},
                {"id": "eve", "label": "Eve", "models": [], "acceptsToolServers": False},
            ],
        )
        self.assertNotIn("not passed on", json.dumps(reply))
        accepted = {"options": {"supportsMcpServers": True}}
        reply = self.call("catalog", {}, entries=entries, providerEntries={"eve": accepted})
        self.assertTrue(all("acceptsToolServers" not in row for row in reply["providers"]))
        refused = self.failure("catalog", {}, entries=entries, configError="config unavailable")
        self.assertEqual((refused.code, str(refused)), ("paseo_call_failed", "config unavailable"))


if __name__ == "__main__":
    unittest.main()
