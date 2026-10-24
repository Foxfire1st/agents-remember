"""The launch's role folders, agent identities and preparation failures, against the fake
bridge."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from agents_remember.application.agent_binding import AgentBinding
from agents_remember.cli import (
    leaf_enclosure_start,
    paseo_launch,
    role_launch_routes,
    role_launch_workspace,
)
from agents_remember.cli.paseo_launch import agent_title
from agents_remember.cli.role_launch_workspace import _start_leaf_enclosure
from agents_remember.models.role_launcher import (
    RoleLauncherOptionsRequest,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.task_paths import slugify
from paseo_launch_test_support import (
    SERVER_ID,
)
from test_paseo_launch import (
    LEAF_REF,
    MASTER_REF,
    REPO,
    ROLE_REFS,
    SPRINT_REF,
    FakeEnclosures,
    PaseoLaunchTestCase,
)


class RoleFolderAndIdentityTests(PaseoLaunchTestCase):
    def check_launched_agent(
        self,
        request: Any,
        public: dict[str, Any],
        receipt: dict[str, Any],
        launched: dict[str, Any],
        placed: dict[str, Any],
    ) -> None:
        """The launched agent carries its folder, title, labels and reachable paths."""

        agent_id = launched["agent_id"]
        host = {
            "kind": "paseo-agent",
            "serverId": SERVER_ID,
            "workspaceId": launched["workspace_id"],
            "agentId": agent_id,
        }
        self.assertEqual(public["execution"], host)
        self.assertEqual(receipt["execution"], host)
        self.assertEqual(receipt["agentId"], agent_id)
        self.assertIs(receipt["hostAgentExists"], True)
        self.assertEqual(receipt["agent"], {"id": "codex", "model": "gpt-a", "effort": "low"})
        self.assertEqual(receipt["workspace"]["path"], placed["folder"])
        self.assertNotIn("replayRequest", receipt)
        self.assert_applied_is_recorded(request, launched["definition"], launched["note"])
        self.assertTrue(
            placed["path"].relative_to(self.root).as_posix().startswith(placed["receipt_directory"])
        )

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
                options = role_launch_routes._role_launch_options_endpoint(
                    self.config,
                    RoleLauncherOptionsRequest.model_validate({"role": role, **ROLE_REFS[role]}),
                )
                self.assertEqual(len(json.loads(bytes(options.body))["agents"]), 2)
                self.assertEqual(self.enclosures.start_calls, [])
                self.runtime.calls.clear()
                path = self.receipt_path(request)
                self.watch_launch(path, observed)

                status, public = self.dispatch(request)

                self.assertEqual((status, public["status"]), (200, "running"))
                opened, created = self.runtime.launch_calls()
                task = self.master if role == "manager" else self.leaf
                expected_workspace: dict[str, Any] = (
                    {
                        "cwd": folder,
                        "masterProject": {
                            "directory": self.master.path.parent.as_posix(),
                            "key": MASTER_REF.key,
                            "name": f"MASTER · {self.master.document.title}",
                        },
                        "task": {
                            "key": task.ref.key,
                            "title": f"{task.document.id} · {task.document.title}",
                        },
                    }
                    if role in ("manager", "worker")
                    else {"cwd": folder}
                )
                self.assertEqual(opened, ("workspace-open", expected_workspace))
                agent_id, workspace_id = (
                    created[1]["agentId"],
                    self.runtime.workspaces[self.runtime.workspace_key(expected_workspace)],
                )
                definition, note = self.given_to_agent(request, agent_id, task_labels)
                self.assertEqual(
                    created,
                    (
                        "agent-create",
                        {
                            "agentId": agent_id,
                            "idempotencyKey": f"ar-role-launch:{request.request_id}",
                            "workspaceId": workspace_id,
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
                receipt = self.receipt(request)
                self.check_launched_agent(
                    request,
                    public,
                    receipt,
                    {
                        "agent_id": agent_id,
                        "workspace_id": workspace_id,
                        "definition": definition,
                        "note": note,
                    },
                    {
                        "folder": folder,
                        "path": path,
                        "receipt_directory": receipt_directory,
                    },
                )
                self.runtime.agents.clear()
        # The receipt and artifact were stored before each runtime call.
        self.assertEqual(len(observed), 8)
        for _command, saved, payload, stored in observed:
            self.assertEqual(saved["status"], "starting")
            self.assertEqual(saved["replayRequest"]["agent"]["agentId"], saved["agentId"])
            self.assertEqual(payload.get("agentId", saved["agentId"]), saved["agentId"])
            self.assertTrue(stored)
        # Beside receipts, message bindings and the role index a launch writes its artifact:
        # no harness configuration file, in the folders it uses or in the user's home.
        # The task documents are the fixture's own (resolved_document writes them), and the
        # task-document copies are the launch's publication for the agent (L71): both are
        # enumerated exactly, so an unexpected file still fails this assertion. The two
        # settings files are the fixture's own authority, not the launch's work.
        nown = sorted(
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file()
            and "-native-executions" not in path.as_posix()
            and path != self.config.coordination_root / "system/settings.json"
            and path != self.config.config_path
        )
        task_documents = {
            f"coordination/tasks/{REPO}/sprint/task.json",
            f"coordination/tasks/{REPO}/sprint/task.md",
            f"coordination/tasks/{REPO}/master/task.json",
            f"coordination/tasks/{REPO}/master/task.md",
            f"coordination/tasks/{REPO}/master/01_leaf.json",
            f"coordination/tasks/{REPO}/master/01_leaf.md",
            "enclosure-group/task-documents/INDEX.json",
            "enclosure-group/task-documents/master/01_leaf.md",
            "home/agents-remember/role-agents.json",
            "home/agents-remember/role-agents.json.lock",
        }
        self.assertEqual(
            nown,
            sorted(
                {f"reports/{saved['requestId']}.handover.txt" for _, saved, _, _ in observed}
                | {f"reports/{saved['requestId']}.handover.json" for _, saved, _, _ in observed}
                | task_documents
            ),
        )
        # The user's home holds each launch's locator, and no other file at all.
        locators = self.home / "projects" / ".agents-remember" / "handover-locators"
        self.assertEqual(
            sorted(path.name for path in locators.iterdir() if path.is_symlink()),
            sorted({saved["requestId"] for _, saved, _, _ in observed}),
        )
        self.assertEqual([path for path in self.home.rglob("*") if path.is_file()], [])
        self.assertFalse((self.home / ".claude").exists())
        self.assertEqual(
            self.runtime.workspaces,
            {
                projects: "wks_1",
                f"{MASTER_REF.key}:{MASTER_REF.key}": "wks_2",
                f"{MASTER_REF.key}:{LEAF_REF.key}": "wks_3",
            },
        )
        self.assertEqual(len(self.enclosures.start_calls), 1)
        self.assertEqual(self.enclosures.start_calls[0].leaf_id, "01_LEAF")
        self.assertEqual(self.enclosures.start_calls[0].parent_task, "sprint")
        bindings = self.config.coordination_root / "notes/reports/paseo-native-executions"
        self.assertEqual(len(list((bindings / "message-bindings").glob("*.json"))), 4)
        titles = {
            role: agent_title(self.context(self.config, self.request(role))) for role in ROLE_REFS
        }
        self.assertEqual(titles["investigator"], "Investigator · Projects")
        self.assertEqual(
            {titles[role] for role in ("reviewer", "curator")},
            {"Reviewer · 01_LEAF", "Curator · 01_LEAF"},
        )

    def test_two_taskless_agents_coexist(self) -> None:
        # That receipts of the line this build was copied from are never read is shown where
        # that line's names may be written: test_previous_host_removed.py.
        first, second = self.request("architect"), self.request("architect")
        worker = self.request("worker")

        for request in (first, second, worker):
            self.assertEqual(self.dispatch(request)[0], 200)

        agents = [self.receipt(request)["execution"] for request in (first, second)]
        self.assertEqual(len({agent["agentId"] for agent in agents}), 2)
        self.assertEqual({agent["workspaceId"] for agent in agents}, {"wks_1"})
        self.assertEqual(len(self.runtime.agents), 3)
        options = role_launch_routes._role_launch_options_endpoint(
            self.config, RoleLauncherOptionsRequest(role="architect")
        )
        executions = json.loads(bytes(options.body))["executions"]
        self.assertEqual(
            {execution["requestId"] for execution in executions},
            {str(first.request_id), str(second.request_id)},
        )
        self.assertTrue(all(execution["canStart"] for execution in executions))


class RolePreparationChildTests(PaseoLaunchTestCase):
    """Both entry points prepare in the same child without adopting the caller lifecycle."""

    def setUp(self) -> None:
        super().setUp()
        self.replace(role_launch_workspace, "_start_leaf_enclosure", _start_leaf_enclosure)

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

    def test_both_entry_points_prepare_in_the_existing_child(self) -> None:
        with self.subTest("the shared dispatch owner"):
            calls = self.child_process()
            request = self.request("worker")

            status, public = self.dispatch(request)

            self.assertEqual((status, public["status"]), (200, "running"))
            ((argv, sent),) = calls
            self.assertEqual(
                argv,
                [
                    sys.executable,
                    "-P",
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
                (
                    "workspace-open",
                    paseo_launch.workspace_call(
                        self.context(self.config, self.request("worker")),
                        self.enclosures.group.as_posix(),
                    ),
                ),
            )
            self.assertEqual(self.dispatch(self.request("reviewer"))[0], 200)
            self.assertEqual(len(calls), 1, "an existing enclosure is found, not started again")

    def test_sequential_fresh_leaves_preserve_the_callers_binding(self) -> None:
        binding = AgentBinding(
            agent_id="57e1cf22-3aa2-4490-b90f-ac51cceca621",
            request_id="47e5655e-843a-4fa8-9766-528be233ce34",
            role="manager",
            report_path=str(self.root / "caller.md"),
            sprint_ref=SPRINT_REF,
            master_ref=MASTER_REF,
        )
        with (
            patch.dict(os.environ, binding.environment()),
            patch(
                "agents_remember.application.worktree_tools.worktree_start_tool",
                side_effect=AssertionError("must run in child"),
            ),
        ):
            for number in (1, 2):
                self.enclosures = FakeEnclosures(self.root / str(number))
                self.replace(role_launch_workspace, "worktree_status_tool", self.enclosures.status)
                leaf = ResolvedTaskDocument(
                    ref=TaskDocumentRef(repository=REPO, path=f"master/{number}_leaf.json"),
                    path=self.leaf.path.with_name(f"{number}_leaf.json"),
                    document=self.leaf.document.model_copy(
                        update={"id": f"LEAF-{number}", "slug": f"{number}_leaf", "enclosures": []}
                    ),
                )
                calls = self.child_process()
                role_launch_workspace._ensure_leaf_enclosure(
                    self.config, leaf, parent_task="sprint"
                )
                role_launch_workspace._ensure_leaf_enclosure(
                    self.config, leaf, parent_task="sprint"
                )
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][1]["leafId"], f"LEAF-{number}")
                self.assertEqual(
                    {name: os.environ.get(name) for name in binding.environment()},
                    binding.environment(),
                )

    def test_a_child_that_refuses_fails_or_does_not_end_refuses_the_launch(self) -> None:
        self.replace(
            role_launch_workspace,
            "_leaf_contract_path",
            return_value=Path("/fixture/series-contract.md"),
        )
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
                    "may have left a half-made enclosure",
                    "repaired or abandoned with AR's worktree tools",
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
                self.assertLessEqual(len(str(error.detail)), 800, str(error.detail))
                self.assertEqual(len(calls), 1)
        self.assertEqual(self.enclosures.start_calls, [])
        self.assertEqual(self.runtime.launch_calls(), [])
        self.assertEqual(list(self.root.rglob("*-native-executions")), [])
