from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from agents_remember.application.orca_task_context import OrcaRoleContext
from agents_remember.cli import (
    leaf_enclosure_start,
    orca_task_preparation,
    orca_task_receipts,
    orca_task_routes,
    paseo_catalog,
    paseo_launch,
    paseo_status,
)
from agents_remember.cli.orca_runtime import digest
from agents_remember.cli.orca_task_preparation import OrcaHandoverRequest
from agents_remember.cli.orca_task_receipts import _message_binding_projection_reference
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure
from agents_remember.cli.paseo_catalog import forget_launcher_catalogs
from agents_remember.cli.paseo_launch import agent_title
from agents_remember.kernel.primitives.paseo_runtime_settings import parse_paseo_runtime_settings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.models.orca_launcher import (
    OrcaDispatchRequest,
    OrcaLauncherOptionsRequest,
    OrcaResultRequest,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument
from agents_remember.tasks.document import TaskEnclosureRef
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.task_paths import leaf_enclosure_path, slugify
from fastapi import HTTPException
from paseo_launch_test_support import GivenToAgentExpectations

REPO = "agents-remember"
SERVER_ID = "srv_configured"
SPRINT_REF = TaskDocumentRef(repository=REPO, path="sprint/task.json")
MASTER_REF = TaskDocumentRef(repository=REPO, path="master/task.json")
LEAF_REF = TaskDocumentRef(repository=REPO, path="master/01_leaf.json")
ROLE_REFS: dict[str, dict[str, TaskDocumentRef]] = {
    "architect": {},
    "system-specialist": {},
    "orchestrator": {"sprintDocumentRef": SPRINT_REF},
    "manager": {"sprintDocumentRef": SPRINT_REF, "masterDocumentRef": MASTER_REF},
    **{
        role: {
            "sprintDocumentRef": SPRINT_REF,
            "masterDocumentRef": MASTER_REF,
            "taskDocumentRef": LEAF_REF,
        }
        for role in ("worker", "reviewer", "curator")
    },
}
ROLE_DEFAULTS = ({"agent": "codex", "model": "gpt-a", "effort": "low"}, ("codex", "eve"))
CATALOG: dict[str, Any] = {
    "runtime": {"serverId": SERVER_ID, "version": "0.11.0-beta.2"},
    "providers": [
        {
            "id": "codex",
            "label": "Codex",
            "models": [
                {
                    "id": "gpt-a",
                    "label": "GPT A",
                    "efforts": [{"id": "low", "label": "Low"}, {"id": "high", "label": "High"}],
                }
            ],
        },
        # A provider that reports no models is launched without one. The runtime reports this one
        # as taking no tool servers from its host.
        {"id": "eve", "label": "Eve", "models": [], "acceptsToolServers": False},
    ],
}
LAUNCH_COMMANDS = {"agent-archive", "workspace-open", "agent-create"}
# What an agent reports when a refresh closes its execution with the given status (PNT-R07).
CLOSING_STATES: dict[str, dict[str, Any]] = {
    "completed": {"status": "idle", "lastTurn": {"state": "replied", "text": "Done."}},
    "failed": {"status": "error", "lastError": "The model refused the turn."},
    "stopped": {"status": "idle", "lastTurn": {"state": "unreplied"}},
}
NO_ANSWER_CODES = (
    "paseo_bridge_timeout",
    "paseo_daemon_unreachable",
    "paseo_bridge_invalid_reply",
    "paseo_bridge_unavailable",
    "paseo_agent_lookup_failed",
)


class FakeRuntime:
    """Stands in for ``bridge_call``: a small in-memory Paseo runtime that records every call.

    A queued failure is raised instead of the command's answer. With ``after_effect`` the command
    takes effect first, as when the runtime acted and its answer was lost.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.agents: dict[str, dict[str, Any]] = {}
        self.workspaces: dict[str, str] = {}
        self.failures: dict[str, list[tuple[PaseoBridgeFailure, bool]]] = {}
        self.observer: Any = None
        self.opened_directory: str | None = None
        self.creation_error: str | None = None

    def fail(self, command: str, code: str, message: str = "", after_effect: bool = False) -> None:
        failure = PaseoBridgeFailure(code, message or f"{command} failed with {code}")
        self.failures.setdefault(command, []).append((failure, after_effect))

    def __call__(
        self, _config: McpRuntimeConfig, command: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append((command, copy.deepcopy(payload)))
        if self.observer is not None and command in LAUNCH_COMMANDS:
            self.observer(command, payload)
        queued = self.failures.get(command) or []
        failure, after_effect = queued.pop(0) if queued else (None, False)
        if failure is not None and not after_effect:
            raise failure
        reply = getattr(self, "_" + command.replace("-", "_"))(payload)
        if failure is not None:
            raise failure
        return copy.deepcopy(reply)

    def launch_calls(self) -> list[tuple[str, dict[str, Any]]]:
        return [call for call in self.calls if call[0] in LAUNCH_COMMANDS]

    def _catalog(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return CATALOG

    def _workspace_open(self, payload: dict[str, Any]) -> dict[str, Any]:
        cwd = payload["cwd"]
        workspace_id = self.workspaces.setdefault(cwd, f"wks_{len(self.workspaces) + 1}")
        return {
            "serverId": SERVER_ID,
            "workspace": {"id": workspace_id, "directory": self.opened_directory or cwd},
        }

    def _agent_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        agent_id = payload["agentId"]
        existing = agent_id in self.agents
        if not existing:
            self.agents[agent_id] = {
                "id": agent_id,
                "provider": payload["provider"],
                # What the runtime applied: the provider's own default when no model was given.
                "model": payload.get("model") or f"{payload['provider']}-default",
                "thinkingOptionId": payload.get("thinkingOptionId"),
                "title": payload["title"],
                "labels": payload["labels"],
                "workspaceId": payload["workspaceId"],
                "status": "running",
                "archivedAt": None,
            }
        return {
            "serverId": SERVER_ID,
            "existing": existing,
            "agent": self.agents[agent_id],
            **({"creationError": self.creation_error} if self.creation_error else {}),
        }

    def _agent_archive(self, payload: dict[str, Any]) -> dict[str, Any]:
        agent = self.agents.get(payload["agentId"])
        if agent is None:
            return {"serverId": SERVER_ID, "agentId": payload["agentId"], "found": False}
        agent["archivedAt"] = agent["archivedAt"] or "2026-10-02T00:00:00.000Z"
        agent["status"] = "closed"
        return {"serverId": SERVER_ID, "agentId": agent["id"], "found": True, "archived": True}

    def _agent_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        agent = self.agents.get(payload["agentId"])
        return {"serverId": SERVER_ID, "agent": self.state(agent) if agent else None}

    def _agent_resume(self, payload: dict[str, Any]) -> dict[str, Any]:
        agent = self.agents.get(payload["agentId"])
        resume: dict[str, Any] = {"attempted": False, "resumed": False}
        if agent and agent["status"] == "closed" and not agent["archivedAt"]:
            refusal = agent.get("resumeRefusal")
            if not refusal:
                agent["status"] = "idle"
            resume = {"attempted": True, "resumed": not refusal}
            if refusal:
                resume["error"] = refusal
        return {
            "serverId": SERVER_ID,
            "agent": self.state(agent) if agent else None,
            "resume": resume,
        }

    @staticmethod
    def state(agent: dict[str, Any]) -> dict[str, Any]:
        """The bridge's state of an agent; a test sets `status` and what goes with it."""

        permissions = agent.get("pendingPermissions", [])
        return {
            "id": agent["id"],
            "status": agent["status"],
            "archivedAt": agent["archivedAt"],
            "turnActive": agent["status"] == "running" and not permissions,
            "pendingPermissions": permissions,
            "lastError": agent.get("lastError"),
            # The last turn is readable only while the agent is idle with an open session.
            "lastTurn": agent.get("lastTurn", {"state": "none"})
            if agent["status"] == "idle"
            else None,
        }


class FakeEnclosures:
    """Stands in for AR's worktree status and start calls of one leaf."""

    def __init__(self, root: Path) -> None:
        self.group = root / "enclosure-group"
        self.started = False
        self.start_calls: list[Any] = []
        self.start_result: dict[str, Any] = {"ok": True}
        # Called when a start succeeds: the real start records the enclosure in the leaf document.
        self.on_start: Any = None

    def status(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        if not self.started:
            return {"ok": False, "detail": "no enclosure"}
        return {
            "ok": True,
            "worktree_group": self.group.as_posix(),
            "code_worktree": (self.group / "code").as_posix(),
            "memory_worktree": (self.group / "memory").as_posix(),
        }

    def start(self, _config: Any, identity: Any, **_kwargs: Any) -> dict[str, Any]:
        self.start_calls.append(identity)
        if self.start_result.get("ok") is True:
            for path in (self.group / "code", self.group / "memory"):
                path.mkdir(parents=True)
            self.started = True
            if self.on_start is not None:
                self.on_start()
        return self.start_result


def runtime_config(root: Path, *, configured: bool = True) -> McpRuntimeConfig:
    settings = parse_paseo_runtime_settings(
        {
            "installPrefix": (root / "prefix").as_posix(),
            "home": (root / "home").as_posix(),
            "listen": "127.0.0.1:6835",
            "version": "0.11.0-beta.2",
            "providers": {},
            "embed": [],
        }
    )
    return McpRuntimeConfig(
        config_path=root / "settings" / "mcp.json",
        coordination_root=root / "coordination",
        workspace_root=root / "projects",
        transcript_root=root / "coordination" / "logs" / "mcp",
        paseo_runtime=settings if configured else None,
        repositories={
            REPO: RepositoryScope(
                repo_id=REPO, path=root / "projects" / REPO, memory_root=root / "memory" / REPO
            )
        },
    )


def resolved_document(
    config: McpRuntimeConfig, ref: TaskDocumentRef, document_id: str
) -> ResolvedTaskDocument:
    kind = "subTask" if ref == LEAF_REF else "master"
    document = TaskDocument.model_validate(
        {
            "id": document_id,
            "slug": Path(ref.path).stem,
            "title": document_id,
            "kind": kind,
            "repo": REPO,
            "createdAt": "2026-10-02T00:00:00+00:00",
            **({"status": "inProgress", "master": "task.json"} if kind == "subTask" else {}),
        }
    )
    path = config.coordination_root / "tasks" / REPO / ref.path
    return ResolvedTaskDocument(ref=ref, path=path, document=document)


class PaseoLaunchTestCase(GivenToAgentExpectations):
    """The dispatch route against a fake bridge; capsule compilation is stubbed, the rest is real."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config = runtime_config(self.root)
        self.config.coordination_root.mkdir()
        self.runtime = FakeRuntime()
        self.enclosures = FakeEnclosures(self.root)
        self.prompt = "Compiled capsule and handover."
        self.sprint = resolved_document(self.config, SPRINT_REF, "SPRINT")
        self.master = resolved_document(self.config, MASTER_REF, "MASTER")
        self.leaf = resolved_document(self.config, LEAF_REF, "01_LEAF")
        forget_launcher_catalogs()
        self.addCleanup(forget_launcher_catalogs)
        self.enclosures.on_start = self.record_enclosure_in_leaf
        self.resolve_context = self.replace(
            orca_task_routes, "resolve_orca_role_context", side_effect=self.context
        )
        self.replace(orca_task_preparation, "resolve_orca_role_context", side_effect=self.context)
        self.replace(paseo_catalog, "bridge_call", self.runtime)
        self.replace(paseo_launch, "bridge_call", self.runtime)
        self.replace(paseo_status, "bridge_call", self.runtime)
        self.replace(orca_task_preparation, "worktree_status_tool", self.enclosures.status)
        self.replace(orca_task_preparation, "worktree_start_tool", self.enclosures.start)
        self.replace(orca_task_preparation, "_compile_handover", self.compile_handover)
        self.replace(orca_task_preparation, "_ar_mcp_context", lambda *_args: {"scopeKind": "test"})
        self.replace(orca_task_preparation, "_role_defaults", return_value=ROLE_DEFAULTS)
        # The source tree of the launching build, as a checkout outside the interpreter has it.
        self.source = self.root / "build" / "src" / "agents_remember"
        self.replace(paseo_launch, "launching_source_root", return_value=self.source)
        # A launch writes no harness configuration; the user's home is watched for one as well.
        # The launching process has both variables that keep a process from writing into the
        # checkout it runs from.
        (self.root / "home").mkdir()
        home = patch.dict(
            os.environ,
            {
                "HOME": (self.root / "home").as_posix(),
                "GIT_OPTIONAL_LOCKS": "0",
                "PYTHONPYCACHEPREFIX": (self.root / "pycache").as_posix(),
            },
        )
        home.start()
        self.addCleanup(home.stop)
        for absent in ("TMUX_TMPDIR", "AR_DAGGER_AUTHORITY_ROOT"):
            os.environ.pop(absent, None)

    def replace(self, target: Any, name: str, *replacement: Any, **mock: Any) -> Any:
        patcher = patch.object(target, name, *replacement, **mock)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def context(self, _config: McpRuntimeConfig, selection: Any) -> OrcaRoleContext:
        refs = ROLE_REFS[selection.role]
        sprint = self.sprint if "sprintDocumentRef" in refs else None
        master = self.master if "masterDocumentRef" in refs else None
        task = self.leaf if "taskDocumentRef" in refs else None
        return OrcaRoleContext(selection.role, sprint, master, task, task or master or sprint)

    def record_enclosure_in_leaf(self) -> None:
        """What AR's worktree start does to the leaf's task document."""

        contract = leaf_enclosure_path(self.leaf.path.parent, self.leaf.document.id)
        enclosure = TaskEnclosureRef(
            leafId=self.leaf.document.id, enclosurePath=contract.as_posix()
        )
        self.leaf = ResolvedTaskDocument(
            ref=self.leaf.ref,
            path=self.leaf.path,
            document=self.leaf.document.model_copy(update={"enclosures": [enclosure]}),
        )

    def compile_handover(self, request: OrcaHandoverRequest) -> dict[str, Any]:
        assert request.request_id is not None
        report = (self.root / "reports" / f"{request.request_id}.md").as_posix()
        context = request.context
        documents = digest(
            [
                resolved.document.model_dump(mode="json", by_alias=True, exclude_none=True)
                for resolved in (context.sprint, context.master, context.task)
                if resolved is not None
            ]
        )
        binding = {
            "requestId": str(request.request_id),
            "role": context.role,
            "taskDocumentDigest": documents,
        }
        return {
            "prompt": self.prompt,
            "capsuleOperation": "planning",
            "capsuleDigest": "capsule-digest",
            "taskDocumentDigest": documents,
            "taskReportPath": report,
            "canonicalTaskReportPath": report,
            "arMcpContext": request.ar_mcp_context,
            "messageBindingProjection": {
                "requestId": str(request.request_id),
                "binding": binding,
                **_message_binding_projection_reference(self.config, request.request_id, binding),
            },
        }

    def request(self, role: str, request_id: uuid.UUID | None = None, **override: str) -> Any:
        return OrcaDispatchRequest.model_validate(
            {
                "role": role,
                "requestId": request_id or uuid.uuid4(),
                **ROLE_REFS[role],
                **({"agentOverride": override} if override else {}),
            }
        )

    def dispatch(
        self, request: OrcaDispatchRequest, config: McpRuntimeConfig | None = None
    ) -> tuple[int, dict[str, Any]]:
        response = orca_task_routes._orca_dispatch_endpoint(config or self.config, request)
        return response.status_code, json.loads(bytes(response.body))

    def refused(self, request: OrcaDispatchRequest, **kwargs: Any) -> HTTPException:
        with self.assertRaises(HTTPException) as raised:
            self.dispatch(request, **kwargs)
        return raised.exception

    def receipt_path(self, request: OrcaDispatchRequest) -> Path:
        taskless = not ROLE_REFS[request.role]
        return orca_task_receipts._receipt_path(
            self.config, request, request.request_id if taskless else None
        )

    def receipt(self, request: OrcaDispatchRequest) -> dict[str, Any]:
        return json.loads(self.receipt_path(request).read_text(encoding="utf-8"))

    def agent_of(self, request: OrcaDispatchRequest) -> dict[str, Any]:
        """The fake runtime's record of the agent this execution launched."""

        return self.runtime.agents[self.receipt(request)["execution"]["agentId"]]

    def refresh(
        self, request: OrcaDispatchRequest, config: McpRuntimeConfig | None = None
    ) -> dict[str, Any]:
        """Press Result: the result route for the execution of this request."""

        response = orca_task_routes._orca_result_endpoint(
            config or self.config,
            OrcaResultRequest.model_validate(
                {
                    "role": request.role,
                    "requestId": request.request_id,
                    **ROLE_REFS[request.role],
                }
            ),
        )
        return json.loads(bytes(response.body))

    def close_execution(self, request: OrcaDispatchRequest, status: str) -> dict[str, Any]:
        """Let the agent end its turn so that a refresh closes the execution with this status."""

        self.agent_of(request).update(CLOSING_STATES[status])
        self.assertEqual(self.refresh(request)["status"], status)
        return self.receipt(request)

    def receipt_files(self) -> list[str]:
        return sorted(
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*.json")
            if "-native-executions" in path.as_posix() and "message-bindings" not in path.parts
        )


class RoleFolderAndIdentityTests(PaseoLaunchTestCase):
    def test_each_role_class_gets_one_agent_in_its_folder_with_title_and_labels(self) -> None:
        projects = self.config.workspace_root.resolve().as_posix()
        refs = {"sprint": SPRINT_REF.key, "master": MASTER_REF.key, "task": LEAF_REF.key}
        expected: dict[str, tuple[str, str, dict[str, str], str]] = {
            "architect": (
                projects,
                "Architect · Projects",
                {},
                "coordination/notes/reports/paseo-native-executions/architect/sessions/",
            ),
            "orchestrator": (
                projects,
                "Orchestrator · SPRINT",
                {"ar.sprint-ref": refs["sprint"]},
                f"coordination/tasks/{REPO}/sprint/notes/reports/paseo-native-executions/",
            ),
            "manager": (
                projects,
                "Manager · MASTER",
                {"ar.sprint-ref": refs["sprint"], "ar.master-ref": refs["master"]},
                f"coordination/tasks/{REPO}/master/notes/reports/paseo-native-executions/",
            ),
            "worker": (
                self.enclosures.group.as_posix(),
                "Worker · 01_LEAF",
                {
                    "ar.sprint-ref": refs["sprint"],
                    "ar.master-ref": refs["master"],
                    "ar.task-ref": refs["task"],
                },
                f"coordination/tasks/{REPO}/master/notes/reports/paseo-native-executions/",
            ),
        }
        observed: list[tuple[str, Any, Any, bool]] = []
        for role, (folder, title, task_labels, receipt_directory) in expected.items():
            with self.subTest(role):
                request = self.request(role)
                # Selecting in the launcher creates no enclosure; only Start does.
                options = orca_task_routes._orca_options_endpoint(
                    self.config,
                    OrcaLauncherOptionsRequest.model_validate({"role": role, **ROLE_REFS[role]}),
                )
                self.assertEqual(len(json.loads(bytes(options.body))["agents"]), 2)
                self.assertEqual(self.enclosures.start_calls, [])
                self.runtime.calls.clear()
                path = self.receipt_path(request)
                self.watch_launch(path, observed)

                status, public = self.dispatch(request)

                self.assertEqual((status, public["status"]), (200, "running"))
                opened, created = self.runtime.launch_calls()
                self.assertEqual(opened, ("workspace-open", {"cwd": folder}))
                agent_id = created[1]["agentId"]
                definition, note = self.given_to_agent(request, agent_id, task_labels)
                self.assertEqual(
                    created,
                    (
                        "agent-create",
                        {
                            "agentId": agent_id,
                            "idempotencyKey": f"ar-role-launch:{request.request_id}",
                            "workspaceId": self.runtime.workspaces[folder],
                            "provider": "codex",
                            "model": "gpt-a",
                            "thinkingOptionId": "low",
                            "title": title,
                            "labels": {
                                "ar.role": role,
                                "ar.request-id": str(request.request_id),
                                **task_labels,
                            },
                            "prompt": self.first_message(request),
                            "systemPrompt": note,
                            "mcpServers": {"agents-remember-task": definition},
                        },
                    ),
                )
                self.assertEqual(str(uuid.UUID(agent_id)), agent_id)
                self.assertEqual(list(self.runtime.agents), [agent_id])
                host = {
                    "kind": "paseo-agent",
                    "serverId": SERVER_ID,
                    "workspaceId": self.runtime.workspaces[folder],
                    "agentId": agent_id,
                }
                receipt = self.receipt(request)
                self.assertEqual(public["execution"], host)
                self.assertEqual(receipt["execution"], host)
                self.assertEqual(receipt["agentId"], agent_id)
                self.assertIs(receipt["hostAgentExists"], True)
                self.assertEqual(
                    receipt["agent"], {"id": "codex", "model": "gpt-a", "effort": "low"}
                )
                self.assertEqual(receipt["workspace"]["path"], folder)
                self.assertNotIn("replayRequest", receipt)
                self.assert_applied_is_recorded(request, definition, note)
                self.assertTrue(
                    path.relative_to(self.root).as_posix().startswith(receipt_directory)
                )
                self.runtime.agents.clear()
        # The receipt and the artifact were on disk, with the agent id, before each call to the
        # runtime.
        self.assertEqual(len(observed), 8)
        for _command, saved, payload, stored in observed:
            self.assertEqual(saved["status"], "starting")
            self.assertEqual(saved["replayRequest"]["agent"]["agentId"], saved["agentId"])
            self.assertEqual(payload.get("agentId", saved["agentId"]), saved["agentId"])
            self.assertTrue(stored)
        # Beside receipts and message bindings a launch writes its artifact and nothing else: no
        # harness configuration file, in the folders it uses or in the user's home.
        self.assertEqual(
            sorted(
                path.relative_to(self.root).as_posix()
                for path in self.root.rglob("*")
                if path.is_file() and "-native-executions" not in path.as_posix()
            ),
            sorted({f"reports/{saved['requestId']}.handover.txt" for _, saved, _, _ in observed}),
        )
        # One workspace per folder, asked for by directory; AR created the leaf's enclosure.
        self.assertEqual(
            self.runtime.workspaces, {projects: "wks_1", self.enclosures.group.as_posix(): "wks_2"}
        )
        self.assertEqual(len(self.enclosures.start_calls), 1)
        self.assertEqual(self.enclosures.start_calls[0].leaf_id, "01_LEAF")
        self.assertEqual(self.enclosures.start_calls[0].parent_task, "sprint")
        self.assertEqual(list(self.root.rglob("orca-native-executions")), [])
        bindings = self.config.coordination_root / "notes/reports/paseo-native-executions"
        self.assertEqual(len(list((bindings / "message-bindings").glob("*.json"))), 4)
        titles = {
            role: agent_title(self.context(self.config, self.request(role))) for role in ROLE_REFS
        }
        self.assertEqual(titles["system-specialist"], "System specialist · Projects")
        self.assertEqual(
            {titles[role] for role in ("reviewer", "curator")},
            {"Reviewer · 01_LEAF", "Curator · 01_LEAF"},
        )

    def test_two_taskless_agents_coexist_and_ont_receipts_are_never_read(self) -> None:
        first, second = self.request("architect"), self.request("architect")
        worker = self.request("worker")
        ont = self.config.coordination_root / "notes" / "reports" / "orca-native-executions"
        leaf_ont = self.leaf.path.parent / "notes" / "reports" / "orca-native-executions"
        saved = {"schema": "ar-orca-native-execution/v1", "status": "running", "role": "architect"}
        ont_files = {
            ont / self.receipt_path(first).relative_to(ont.with_name("paseo-native-executions")),
            ont / "architect-legacy.json",
            ont / "history" / f"{uuid.uuid4()}.json",
            leaf_ont / self.receipt_path(worker).name,
        }
        for path in ont_files:
            orca_task_receipts._write_receipt(path, {**saved, "requestId": str(uuid.uuid4())})
        before = {path: path.read_bytes() for path in ont_files}

        for request in (first, second, worker):
            self.assertEqual(self.dispatch(request)[0], 200)

        self.assertEqual({path: path.read_bytes() for path in ont_files}, before)
        agents = [self.receipt(request)["execution"] for request in (first, second)]
        self.assertEqual(len({agent["agentId"] for agent in agents}), 2)
        self.assertEqual({agent["workspaceId"] for agent in agents}, {"wks_1"})
        self.assertEqual(len(self.runtime.agents), 3)
        options = orca_task_routes._orca_options_endpoint(
            self.config, OrcaLauncherOptionsRequest(role="architect")
        )
        executions = json.loads(bytes(options.body))["executions"]
        self.assertEqual(
            {execution["requestId"] for execution in executions},
            {str(first.request_id), str(second.request_id)},
        )
        self.assertTrue(all(execution["canStart"] for execution in executions))


class LaunchRefusalTests(PaseoLaunchTestCase):
    def test_a_launch_that_cannot_start_refuses_before_any_runtime_call_or_receipt(self) -> None:
        with self.subTest("no Paseo runtime configured"):
            error = self.refused(
                self.request("architect"), config=runtime_config(self.root, configured=False)
            )
            self.assertEqual(error.status_code, 409)
            self.assertTrue(str(error.detail).startswith("no Paseo runtime configured: "))
            self.assertEqual(self.runtime.calls, [])
            self.resolve_context.assert_not_called()
        with self.subTest("the catalog cannot be loaded for the validation"):
            self.runtime.fail("catalog", "paseo_daemon_unreachable", "The Paseo daemon is down.")
            error = self.refused(self.request("worker"))
            self.assertEqual((error.status_code, error.detail), (409, "The Paseo daemon is down."))
            self.assertEqual([call[0] for call in self.runtime.calls], ["catalog"])
            self.assertEqual(self.enclosures.start_calls, [])
        with self.subTest("the selection fails validation"):
            error = self.refused(self.request("worker", agentId="codex", modelId="gpt-z"))
            self.assertEqual(error.status_code, 409)
            self.assertIn("does not offer model 'gpt-z'", str(error.detail))
            self.assertEqual(self.enclosures.start_calls, [])
        with self.subTest("the leaf enclosure cannot be created"):
            self.enclosures.start_result = {"ok": False, "summary": "base branch is missing"}
            error = self.refused(self.request("worker"))
            self.assertEqual((error.status_code, error.detail), (409, "base branch is missing"))
            self.assertEqual(len(self.enclosures.start_calls), 1)
        self.assertEqual(self.runtime.launch_calls(), [])
        self.assertEqual(list(self.root.rglob("*-native-executions")), [])


class DashboardProcessEnclosureTests(PaseoLaunchTestCase):
    """The worktree start runs in a child process when, and only when, the backend is the dashboard."""

    def child_process(self, outcome: Any = None) -> list[tuple[list[str], dict[str, Any]]]:
        """Replace the child process; without an outcome it creates the enclosure and says ok."""

        calls: list[tuple[list[str], dict[str, Any]]] = []

        def run(argv: list[str], request: dict[str, Any]) -> Any:
            calls.append((argv, request))
            if isinstance(outcome, BaseException):
                raise outcome
            if outcome is not None:
                return outcome
            self.enclosures.start(None, "started by the child process")
            return SimpleNamespace(returncode=0, stdout=b'{"ok": true}\n', stderr=b"")

        self.replace(leaf_enclosure_start, "_run_child", run)
        return calls

    def test_only_the_dashboard_process_starts_the_enclosure_in_a_child_process(self) -> None:
        with self.subTest("the dashboard process"):
            role = self.replace(
                orca_task_preparation, "declared_process_role", return_value="dashboard"
            )
            calls = self.child_process()
            request = self.request("worker")

            status, public = self.dispatch(request)

            self.assertEqual((status, public["status"]), (200, "running"))
            ((argv, sent),) = calls
            self.assertEqual(
                argv,
                [
                    sys.executable,
                    "-m",
                    "agents_remember.cli",
                    "start-leaf-enclosure",
                    "--config",
                    self.config.config_path.as_posix(),
                ],
            )
            worktree_name = (
                f"{slugify('01_leaf')}-{hashlib.sha256(LEAF_REF.key.encode()).hexdigest()[:10]}"
            )
            # The identity of the start, and the roots this backend holds for the child to check.
            self.assertEqual(
                sent,
                {
                    "repoId": REPO,
                    "taskName": "master",
                    "worktreeName": worktree_name,
                    "leafId": "01_LEAF",
                    "parentTask": "sprint",
                    "coordinationRoot": self.config.coordination_root.as_posix(),
                    "codeRoot": (self.root / "projects" / REPO).as_posix(),
                    "memoryRoot": (self.root / "memory" / REPO).as_posix(),
                },
            )
            # The worktree owner was not called in this process.
            self.assertEqual(self.enclosures.start_calls, ["started by the child process"])
            self.assertEqual(
                self.runtime.launch_calls()[0],
                ("workspace-open", {"cwd": self.enclosures.group.as_posix()}),
            )
            self.assertEqual(self.dispatch(self.request("reviewer"))[0], 200)
            self.assertEqual(len(calls), 1, "an existing enclosure is found, not started again")
            role.return_value = None
        for role_name in (None, "mcp"):
            with self.subTest("another process", role=role_name):
                self.enclosures = FakeEnclosures(self.root / f"other-{role_name}")
                self.replace(orca_task_preparation, "worktree_status_tool", self.enclosures.status)
                self.replace(orca_task_preparation, "worktree_start_tool", self.enclosures.start)
                self.replace(orca_task_preparation, "declared_process_role", return_value=role_name)
                calls = self.child_process(AssertionError("no child process is started here"))
                (identity,) = (
                    self.enclosures.start_calls
                    if orca_task_preparation._ensure_leaf_enclosure(
                        self.config, self.leaf, parent_task="sprint"
                    )
                    else []
                )
                self.assertEqual((identity.leaf_id, identity.parent_task), ("01_LEAF", "sprint"))
                self.assertEqual(calls, [])

    def test_a_child_that_refuses_fails_or_does_not_end_refuses_the_launch(self) -> None:
        self.replace(orca_task_preparation, "declared_process_role", return_value="dashboard")
        refusal = json.dumps(
            {
                "ok": False,
                "error": {
                    "code": "atomic-series-admission-failed",
                    "message": "Atomic-series admission refused: the base branch is missing.",
                },
            }
        ).encode()

        def ended(status: int, output: bytes, errors: bytes = b"") -> SimpleNamespace:
            return SimpleNamespace(returncode=status, stdout=output, stderr=errors)

        outcomes: dict[str, tuple[Any, tuple[str, ...]]] = {
            "the child refuses": (
                ended(1, refusal),
                ("atomic-series-admission-failed", "the base branch is missing."),
            ),
            "the child is cut off at the limit": (
                subprocess.TimeoutExpired("python", 120, stderr=b"git fetch: still receiving"),
                (
                    "leaf_enclosure_start_timeout",
                    "was cut off after 120 seconds",
                    "may have left partial state, which a later Start completes",
                    "The child process said: git fetch: still receiving",
                ),
            ),
            "the child ends without a reply, and its last error output is kept": (
                ended(1, b"", b"Traceback (most recent call last):\nMemoryError"),
                ("leaf_enclosure_start_unreadable", "status 1", "said: Traceback", "MemoryError"),
            ),
            "the child's output is not UTF-8": (
                ended(1, b"\xff\xfe{", b"caf\xe9"),
                ("leaf_enclosure_start_unreadable", "status 1", "The child process said: caf"),
            ),
            "the child says ok but failed": (
                ended(3, b'{"ok": true}'),
                ("leaf_enclosure_start_unreadable", "status 3"),
            ),
            "the child cannot be started": (
                OSError("no such interpreter"),
                ("leaf_enclosure_start_unavailable", "no such interpreter"),
            ),
        }
        for label, (outcome, reasons) in outcomes.items():
            with self.subTest(label):
                calls = self.child_process(outcome)
                error = self.refused(self.request("worker"))
                self.assertEqual(error.status_code, 409)
                for reason in reasons:
                    self.assertIn(reason, str(error.detail))
                self.assertLessEqual(len(str(error.detail)), 800)
                self.assertEqual(len(calls), 1)
        self.assertEqual(self.enclosures.start_calls, [])
        self.assertEqual(self.runtime.launch_calls(), [])
        self.assertEqual(list(self.root.rglob("*-native-executions")), [])


class LaunchOutcomeTests(PaseoLaunchTestCase):
    def test_each_answer_of_the_runtime_is_written_to_the_receipt(self) -> None:
        with self.subTest("created: what the runtime applied is recorded"):
            request = self.request("architect", agentId="eve")
            status, public = self.dispatch(request)
            created = self.runtime.launch_calls()[-1][1]
            self.assertEqual((status, public["status"]), (200, "running"))
            self.assertNotIn("model", created)
            self.assertNotIn("thinkingOptionId", created)
            self.assertEqual(self.receipt(request)["agent"], {"id": "eve", "model": "eve-default"})
            # The runtime reports that this provider takes no tool servers: the definition is
            # left out, the receipt says so, and note and artifact line are sent all the same.
            self.assertNotIn("mcpServers", created)
            self.assertEqual(
                self.receipt(request)["toolServer"],
                {
                    "name": "agents-remember-task",
                    "applied": False,
                    "detail": "tool server not applied: not supported by provider",
                },
            )
            self.assertEqual(created["systemPrompt"], self.receipt(request)["recoveryNote"])
            self.assertEqual(created["prompt"], self.first_message(request))
            self.assertEqual(
                (public["canStart"], public["canRetry"], public["canRevive"]), (True, False, False)
            )

        refusals: dict[str, tuple[str, str, int]] = {
            "the creation is refused": ("agent-create", "Provider codex is not configured", 2),
            "the workspace is refused": ("workspace-open", "Directory not found: /gone", 1),
        }
        for label, (command, message, calls) in refusals.items():
            with self.subTest(label):
                self.runtime.calls.clear()
                self.runtime.fail(command, "paseo_call_failed", message)
                request = self.request("orchestrator", uuid.uuid4())
                status, public = self.dispatch(request)
                receipt = self.receipt(request)
                self.assertEqual((status, public["status"]), (502, "rejected"))
                self.assertIn(message, public["detail"])
                self.assertEqual(len(self.runtime.launch_calls()), calls)
                self.assertEqual(str(uuid.UUID(receipt["agentId"])), receipt["agentId"])
                self.assertNotIn(receipt["agentId"], self.runtime.agents)
                self.assertIs(receipt["hostAgentExists"], False)
                self.assertEqual((receipt["execution"], public["execution"]), ({}, {}))
                self.assertNotIn("replayRequest", receipt)
                self.assertEqual((public["canStart"], public["canRetry"]), (True, False))

        with self.subTest("a workspace of another folder is not used"):
            self.runtime.calls.clear()
            self.runtime.opened_directory = (self.root / "elsewhere").as_posix()
            status, public = self.dispatch(self.request("architect"))
            self.runtime.opened_directory = None
            self.assertEqual((status, public["status"]), (502, "rejected"))
            self.assertEqual([call[0] for call in self.runtime.launch_calls()], ["workspace-open"])

        with self.subTest("an agent that exists although its creation reported an error"):
            self.runtime.creation_error = "the first prompt was not accepted"
            request = self.request("architect")
            status, public = self.dispatch(request)
            self.assertEqual((status, public["status"]), (200, "running"))
            self.assertIn("the first prompt was not accepted", public["warning"])
            self.assertIn(self.receipt(request)["agentId"], self.runtime.agents)

    def test_a_launch_without_a_usable_answer_stays_retryable(self) -> None:
        no_answers = [("agent-create", code) for code in NO_ANSWER_CODES]
        for command, code in [*no_answers, ("workspace-open", "paseo_daemon_unreachable")]:
            with self.subTest("no usable answer", command=command, code=code):
                self.runtime.fail(command, code)
                request = self.request("architect", agentId="codex", modelId="gpt-a")
                status, public = self.dispatch(request)
                receipt = self.receipt(request)
                self.assertEqual((status, public["status"]), (202, "unknown"))
                self.assertIn(code, public["detail"])
                self.assertEqual(receipt["replayRequest"]["agent"]["agentId"], receipt["agentId"])
                self.assertEqual(
                    receipt["replayRequest"]["agent"]["prompt"], self.first_message(request)
                )
                self.assertNotIn("hostAgentExists", receipt)
                self.assertEqual((public["canStart"], public["canRetry"]), (False, True))
                self.assertEqual(
                    public["retryPayload"],
                    {
                        "role": "architect",
                        "agentOverride": {"agentId": "codex", "modelId": "gpt-a"},
                    },
                )

    def test_a_300_000_byte_first_message_launches_as_data(self) -> None:
        filler = "Rollenauftrag für den Worker · 役割 — "
        self.prompt = filler * (300_000 // len(filler.encode("utf-8")))
        self.prompt += "x" * (300_000 - len(self.prompt.encode("utf-8")))
        self.assertEqual(len(self.prompt.encode("utf-8")), 300_000)
        request = self.request("worker")
        self.runtime.fail("agent-create", "paseo_bridge_timeout")

        self.assertEqual(self.dispatch(request)[0], 202)
        receipt = self.receipt(request)
        self.assertEqual(receipt["replayRequest"]["agent"]["prompt"], self.first_message(request))
        self.assertEqual(receipt["handoverArtifact"]["bytes"], 300_000)
        self.assertEqual(
            Path(receipt["handoverArtifact"]["path"]).read_bytes(), self.prompt.encode("utf-8")
        )
        status, public = self.dispatch(request)

        self.assertEqual((status, public["status"]), (200, "running"))
        first, second = (call[1] for call in self.runtime.calls if call[0] == "agent-create")
        self.assertEqual(first["prompt"], self.first_message(request))
        self.assertEqual(second, first)


class RepeatAndConflictTests(PaseoLaunchTestCase):
    def test_a_repeated_request_converges_on_the_same_agent(self) -> None:
        with self.subTest("the answer was lost after the agent was created"):
            request = self.request("worker")
            self.runtime.fail("agent-create", "paseo_bridge_timeout", after_effect=True)
            self.assertEqual(self.dispatch(request)[1]["status"], "unknown")
            agent_id = self.receipt(request)["agentId"]
            self.assertEqual(list(self.runtime.agents), [agent_id])

            status, public = self.dispatch(request)

            self.assertEqual((status, public["status"]), (200, "running"))
            self.assertEqual(public["execution"]["agentId"], agent_id)
            self.assertEqual(list(self.runtime.agents), [agent_id])
            first, second = (call[1] for call in self.runtime.calls if call[0] == "agent-create")
            self.assertEqual(second, first)
            self.assertEqual(len(self.enclosures.start_calls), 1)
            # A resolved request is answered by a refresh of its execution: a read, no launch.
            launches = len(self.runtime.launch_calls())
            status, again = self.dispatch(request)
            self.assertEqual((status, again["status"]), (200, "running"))
            self.assertEqual(again["execution"], public["execution"])
            self.assertEqual(self.runtime.calls[-1], ("agent-state", {"agentId": agent_id}))
            self.assertEqual(len(self.runtime.launch_calls()), launches)
        with self.subTest("the process ended between the receipt and the call"):
            request = self.request("architect")
            crash = RuntimeError("the backend process ended here")
            with (
                patch.object(orca_task_receipts, "run_launch_call", side_effect=crash),
                self.assertRaises(RuntimeError),
            ):
                self.dispatch(request)
            saved = self.receipt(request)
            self.assertEqual(saved["status"], "starting")
            self.assertNotIn(saved["agentId"], self.runtime.agents)
            self.assertTrue(orca_task_receipts._public_execution(saved)["canRetry"])

            status, public = self.dispatch(request)

            self.assertEqual((status, public["status"]), (200, "running"))
            self.assertEqual(public["execution"]["agentId"], saved["agentId"])
            self.assertEqual(len(self.runtime.agents), 2)

    def test_a_conflicting_or_second_request_is_refused_and_nothing_changes(self) -> None:
        running = self.request("worker")
        unresolved = self.request("manager")
        self.dispatch(running)
        self.runtime.fail("agent-create", "paseo_daemon_unreachable")
        self.dispatch(unresolved)
        # The receipt already says what the agent is doing, so a further refresh changes nothing.
        self.assertEqual(self.refresh(running)["status"], "running")
        files = self.receipt_files()
        saved = {path: (self.root / path).read_bytes() for path in files}
        calls = len(self.runtime.calls)
        refused: dict[str, tuple[Any, str]] = {
            "same request id, another override": (
                self.request("worker", running.request_id, agentId="codex", effortId="high"),
                "already bound to different AR task or agent-selection content",
            ),
            "same request id, another selection": (
                self.request("reviewer", running.request_id),
                "already has a different immutable message-binding projection",
            ),
            "new request id while the execution runs": (
                self.request("worker"),
                f"open execution (request {running.request_id}, status running)",
            ),
            "new request id while the execution is unresolved": (
                self.request("manager"),
                f"open execution (request {unresolved.request_id}, status unknown)",
            ),
        }
        for label, (request, reason) in refused.items():
            with self.subTest(label):
                error = self.refused(request)
                self.assertEqual(error.status_code, 409)
                self.assertIn(reason, str(error.detail))
        self.assertEqual(self.receipt_files(), files)
        self.assertEqual({path: (self.root / path).read_bytes() for path in files}, saved)
        # The one call a refusal makes is the read of the open execution's agent.
        self.assertEqual(
            self.runtime.calls[calls:],
            [("agent-state", {"agentId": self.receipt(running)["agentId"]})],
        )
        self.assertEqual(len(self.runtime.agents), 1)

    def test_a_new_start_on_a_closed_execution_archives_the_old_receipt_and_agent(self) -> None:
        previous = self.request("worker")
        self.dispatch(previous)
        for status in ("completed", "failed", "stopped"):
            with self.subTest(status):
                closed = self.close_execution(previous, status)
                old_agent = closed["execution"]["agentId"]
                successor = self.request("worker")
                seen: list[tuple[str, str, str]] = []
                self.runtime.observer = lambda command, _payload, request=successor, seen=seen: (
                    seen.append(
                        (
                            command,
                            self.receipt(request)["requestId"],
                            self.receipt(request)["status"],
                        )
                    )
                )
                self.runtime.calls.clear()

                status_code, public = self.dispatch(successor)

                self.assertEqual((status_code, public["status"]), (200, "running"))
                # The old execution is refreshed first; closed, its agent is archived.
                self.assertEqual(
                    [call[0] for call in self.runtime.calls],
                    ["agent-state", "agent-archive", "workspace-open", "agent-create"],
                )
                self.assertEqual(self.runtime.calls[0][1], {"agentId": old_agent})
                self.assertEqual(self.runtime.calls[1][1], {"agentId": old_agent})
                # The new receipt replaced the old one before the runtime was called at all.
                self.assertEqual(
                    {(request_id, state) for _command, request_id, state in seen},
                    {(str(successor.request_id), "starting")},
                )
                self.assertIsNotNone(self.runtime.agents[old_agent]["archivedAt"])
                self.assertIsNone(self.runtime.agents[public["execution"]["agentId"]]["archivedAt"])
                history = self.receipt_path(previous).parent / "history"
                archived = json.loads(
                    (history / f"{previous.request_id}.json").read_text(encoding="utf-8")
                )
                self.assertEqual(archived, closed)
                previous = successor
        self.runtime.observer = None

        with self.subTest("the runtime gives no answer for the old agent, then the retry succeeds"):
            old_agent = self.close_execution(previous, "completed")["execution"]["agentId"]
            successor = self.request("worker")
            self.runtime.fail("agent-archive", "paseo_daemon_unreachable")
            self.runtime.calls.clear()
            self.assertEqual(self.dispatch(successor)[1]["status"], "unknown")
            self.assertEqual(self.dispatch(successor)[1]["status"], "running")
            self.assertEqual(
                [call[0] for call in self.runtime.calls],
                ["agent-state", "agent-archive", "agent-archive", "workspace-open", "agent-create"],
            )
            self.assertIsNotNone(self.runtime.agents[old_agent]["archivedAt"])
            previous = successor
        with self.subTest(
            "an old agent the runtime would not archive is archived by the next start"
        ):
            old_agent = self.close_execution(previous, "failed")["execution"]["agentId"]
            rejected = self.request("worker")
            self.runtime.fail("agent-archive", "paseo_call_failed", "archive refused")
            self.assertEqual(self.dispatch(rejected)[1]["status"], "rejected")
            self.assertEqual(self.receipt(rejected)["pendingArchiveAgentId"], old_agent)
            self.assertIsNone(self.runtime.agents[old_agent]["archivedAt"])
            self.runtime.calls.clear()
            self.assertEqual(self.dispatch(self.request("worker"))[1]["status"], "running")
            self.assertEqual(self.runtime.calls[0], ("agent-archive", {"agentId": old_agent}))
            self.assertIsNotNone(self.runtime.agents[old_agent]["archivedAt"])
        with self.subTest("a rejected execution left no agent to archive"):
            self.runtime.fail("agent-create", "paseo_call_failed", "refused")
            rejected = self.request("orchestrator")
            self.assertEqual(self.dispatch(rejected)[1]["status"], "rejected")
            self.runtime.calls.clear()
            self.assertEqual(self.dispatch(self.request("orchestrator"))[1]["status"], "running")
            self.assertEqual(
                [call[0] for call in self.runtime.calls], ["workspace-open", "agent-create"]
            )

    def test_a_task_bound_receipt_is_created_exclusively(self) -> None:
        with self.subTest("of many simultaneous creations exactly one succeeds"):
            path = self.root / "race" / "worker-selection.json"
            receipts = [
                {"schema": "ar-orca-native-execution/v1", "requestId": str(n)} for n in range(24)
            ]
            with ThreadPoolExecutor(max_workers=24) as pool:
                created = list(
                    pool.map(
                        lambda receipt: orca_task_receipts._create_receipt(path, receipt), receipts
                    )
                )
            self.assertEqual(created.count(True), 1)
            winner = receipts[created.index(True)]
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), winner)
            self.assertEqual([entry.name for entry in path.parent.iterdir()], [path.name])
        # The other process's receipt is still open, or already closed, when this launch loses.
        losing = {
            "starting": "open execution (request {theirs}, status starting)",
            "rejected": "started at the same moment",
        }
        for status, reason in losing.items():
            with self.subTest("the launch that loses is refused and calls nothing", status=status):
                mine = self.request("worker")
                theirs = self.request("worker")
                path = self.receipt_path(mine)
                competitor = {
                    "schema": "ar-orca-native-execution/v1",
                    "requestId": str(theirs.request_id),
                    "role": "worker",
                    "status": status,
                    "execution": {},
                }
                write_binding = orca_task_routes._write_message_binding_projection

                def another_process_starts_first(
                    *args: Any,
                    path: Path = path,
                    competitor: dict[str, Any] = competitor,
                    write_binding: Any = write_binding,
                ) -> dict[str, str]:
                    # This launch has found no receipt; the other process creates its own now.
                    self.assertTrue(orca_task_receipts._create_receipt(path, competitor))
                    return write_binding(*args)

                with patch.object(
                    orca_task_routes,
                    "_write_message_binding_projection",
                    side_effect=another_process_starts_first,
                ):
                    error = self.refused(mine)
                self.assertEqual(error.status_code, 409)
                self.assertIn(reason.format(theirs=theirs.request_id), str(error.detail))
                self.assertEqual(self.runtime.launch_calls(), [])
                history = path.parent / "history" / f"{theirs.request_id}.json"
                kept = path if status == "starting" else history
                self.assertEqual(json.loads(kept.read_text(encoding="utf-8")), competitor)
                kept.unlink()


if __name__ == "__main__":
    unittest.main()
