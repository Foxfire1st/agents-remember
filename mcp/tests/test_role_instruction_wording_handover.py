"""The handover text and the role-tool descriptions: what a launched role is told.

Split from ``test_role_instruction_wording.py``. Here the handover a launch builds in code
(developer channels, the reader context, the tool server and its two tools, a role without a
parent) and the descriptions of the role tools, which name no forbidden host string. The skill
trees, the forbidden list and the matcher come from ``role_instruction_wording_support``.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents_remember.application.role_capsules.compilation import (
    compile_admitted_capsule,
)
from agents_remember.application.role_capsules.launch import _capsule_launch
from agents_remember.application.role_launch_context import (
    RoleLaunchContext,
    resolve_role_launch_context,
)
from agents_remember.application.skill_resources.capsule import (
    CapsuleSeatAddress,
    routed_admission_for,
)
from agents_remember.cli import role_launch_preparation
from agents_remember.cli.paseo_launch import StartingAgent
from agents_remember.cli.role_launch_preparation import (
    RoleHandoverRequest,
    _ar_mcp_context,
    _compile_handover,
)
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.mcp.registration.role_agents import register_role_agent_tools
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleOperation,
    CapsuleRoleSeat,
    CapsuleToolPolicy,
    compute_content_digest,
)
from agents_remember.models.role_launcher import RoleSelection
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.tasks import TaskDocument
from mcp.server.fastmcp import FastMCP
from role_instruction_wording_support import (
    LAUNCHER_ROLES,
    LIFECYCLE,
    OTHER_INSTALLATION,
    PACKAGED,
    REPORT_DIRECTORY,
    REPOSITORY_ROOT,
    TOOL_SERVER,
    forbidden_in,
)


class HandoverTextWordingTests(unittest.TestCase):
    """The handover text as the launch code builds it, with the reader context of the launch.

    The role resolver and role-specific capsule compiler run. Only task-reader/enclosure plumbing
    is isolated; no launch, model or lifecycle operation runs in this fixture.
    """

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.config = McpRuntimeConfig(
            config_path=root / "settings" / "mcp.json",
            coordination_root=root / "coordination",
            workspace_root=root / "projects",
            transcript_root=root / "coordination" / "logs" / "mcp",
            repositories={
                "repo": RepositoryScope(
                    repo_id="repo",
                    path=root / "projects" / "repo",
                    memory_root=root / "memory",
                )
            },
        )
        for name, stub in (
            ("compile_launch_capsule", self.role_capsule),
            (
                "build_context_packet",
                lambda *_args, **_kwargs: {"repo": {"id": "repo"}},
            ),
            (
                "_read_task_doc",
                lambda _config, resolved: {
                    "canonicalTaskPath": resolved.ref.key,
                    "taskDocumentRef": resolved.ref.model_dump(mode="json"),
                },
            ),
            # A leaf's readers are confined to its enclosure, which this test does not create.
            ("task_scoped_mcp_config_for_reader", lambda config, **_scope: config),
        ):
            patcher = patch.object(role_launch_preparation, name, stub)
            patcher.start()
            self.addCleanup(patcher.stop)

    def role_capsule(
        self, _config: McpRuntimeConfig, request: Any, *, operation: CapsuleOperation
    ) -> Any:

        corpus = PACKAGED / LIFECYCLE
        manifest_bytes = (corpus / "composition-manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        role = request.role
        binding = CapsuleBinding(
            operation=operation,
            admitted=CapsuleAdmittedFacts(
                task_reference=request.task_document_ref.key
                if request.task_document_ref
                else "fixture:Projects",
                task_document_digest=compute_content_digest(b"fixed handover comparison inputs"),
                seat=CapsuleRoleSeat(role=role, altitude=manifest["roles"][role]["altitude"]),
                repository_id="repo",
                work_branch="fixture",
                tool_policy=CapsuleToolPolicy(granted=frozenset(PUBLIC_TOOLS)),
            ),
        )
        outcome = compile_admitted_capsule(
            binding,
            routed_admission_for(
                corpus,
                "composition-manifest.json",
                manifest_bytes,
                CapsuleSeatAddress(role=role, operation=operation),
            ),
        )
        self.assertTrue(outcome.ok, outcome.render_explanation())
        assert outcome.result is not None
        return _capsule_launch(role, outcome.result)

    def launch_of(self, role: str) -> tuple[RoleLaunchContext, dict[str, str]]:
        """Taskless, sprint, master and leaf selections admitted by the production resolver."""

        root = self.config.coordination_root
        documents = {
            "sprint/task.json": {
                "id": "SPRINT",
                "kind": "master",
                "orchestrates": ["master"],
            },
            "master/task.json": {
                "id": "MASTER",
                "kind": "master",
                "subTasks": [
                    {
                        "number": "01",
                        "name": "Leaf",
                        "file": "01_leaf.md",
                        "status": "inProgress",
                    }
                ],
            },
            "master/01_leaf.json": {
                "id": "01_LEAF",
                "kind": "subTask",
                "master": "task.md",
                "status": "inProgress",
            },
        }
        for relative, fields in documents.items():
            document = TaskDocument.model_validate(
                {
                    "slug": Path(relative).stem,
                    "title": fields["id"],
                    "repo": "repo",
                    "createdAt": "2026-10-02T00:00:00+00:00",
                    **fields,
                }
            )
            path = root / "tasks" / "repo" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(document.model_dump_json(by_alias=True), encoding="utf-8")
        refs = {}
        # MIK-R196 rule 2: the free agent takes no document at all, so its selection is empty.
        if role not in {"architect", "investigator", "bootstrap"}:
            refs["sprintDocumentRef"] = {
                "repository": "repo",
                "path": "sprint/task.json",
            }
        if role in {"manager", "worker", "reviewer", "curator"}:
            refs["masterDocumentRef"] = {
                "repository": "repo",
                "path": "master/task.json",
            }
        if role in {"worker", "reviewer", "curator"}:
            refs["taskDocumentRef"] = {
                "repository": "repo",
                "path": "master/01_leaf.json",
            }
        context = resolve_role_launch_context(
            self.config, RoleSelection.model_validate({"role": role, **refs})
        )
        workspace = {"path": self.config.workspace_root.as_posix()}
        if context.task is not None:
            enclosure = self.config.workspace_root / "enclosure"
            workspace = {
                "path": enclosure.as_posix(),
                "contractPath": (enclosure / "contract.json").as_posix(),
                "taskReportAccessRoot": (enclosure / "role-reports").as_posix(),
                "taskReportRoot": (context.task.path.parent / "notes" / "reports").as_posix(),
            }
        return context, workspace

    def compiled(self, role: str, started_by: StartingAgent | None) -> tuple[str, dict[str, Any]]:
        context, workspace = self.launch_of(role)
        request_id = uuid.uuid4()
        role_launch_preparation._prepare_role_folders(context, workspace, request_id)
        prepared = _compile_handover(
            RoleHandoverRequest(
                config=self.config,
                context=context,
                workspace=workspace,
                agent_id="some-provider",
                ar_mcp_context=_ar_mcp_context(self.config, context, workspace),
                request_id=request_id,
                started_by=started_by,
            )
        )
        return prepared["prompt"], prepared["handover"]

    def test_developer_question_channel_matches_role_and_parent(self) -> None:
        ordinary = (
            "Put every question for the developer in your own chat: write it as your reply "
            "in this session and end your turn. The developer reads this chat in the "
            "dashboard and answers in it. Never send a developer question to another agent."
        )
        architect = StartingAgent("1f3c2f0e-6a57-4f0b-9d4e-0c8f1a2b3c4d", "architect", "Projects")
        orchestrator = StartingAgent(
            "2f3c2f0e-6a57-4f0b-9d4e-0c8f1a2b3c4d", "orchestrator", "SPRINT"
        )
        for role, parent, routed in (
            ("orchestrator", architect, True),
            ("orchestrator", None, False),
            ("manager", architect, True),
            ("manager", orchestrator, True),
            ("manager", None, False),
            ("worker", architect, True),
            ("worker", orchestrator, True),
            (
                "worker",
                StartingAgent("3f3c2f0e-6a57-4f0b-9d4e-0c8f1a2b3c4d", "manager", "MASTER"),
                True,
            ),
            ("reviewer", architect, True),
            ("curator", architect, True),
            ("investigator", architect, True),
        ):
            with self.subTest(role=role, parent=parent.role if parent else None):
                prompt, handover = self.compiled(role, parent)
                question = handover["host"]["developerQuestions"]
                self.assertIn(question, prompt)
                if routed:
                    assert parent is not None
                    self.assertIn(
                        "send what needs the developer's decision to that parent",
                        question,
                    )
                    # A question stays addressed to the parent's agent ID (MIK-R93
                    # rules 1 and 10; architect QADDR ruling option (a)): rule 10's
                    # return address is not extended to questions.
                    self.assertIn(f"addressed to agent {parent.agent_id}", question)
                    self.assertNotIn("addressed to the return address", question)
                    self.assertIn("role_message on agents-remember-task", question)
                    self.assertIn("takes precedence over generic own-chat guidance", question)
                    self.assertIn("do not end your turn on the question", question)
                else:
                    self.assertEqual(question.encode(), ordinary.encode())
        for role in LAUNCHER_ROLES:
            with self.subTest(dashboard_role=role):
                _prompt, handover = self.compiled(role, None)
                self.assertEqual(handover["host"]["developerQuestions"].encode(), ordinary.encode())

    def test_owner_relation_routes_developer_questions_and_preserves_coordinators(
        self,
    ) -> None:
        parents = (
            StartingAgent("1f3c2f0e-6a57-4f0b-9d4e-0c8f1a2b3c4d", "architect", "Projects"),
            StartingAgent("2f3c2f0e-6a57-4f0b-9d4e-0c8f1a2b3c4d", "orchestrator", "SPRINT"),
            StartingAgent("3f3c2f0e-6a57-4f0b-9d4e-0c8f1a2b3c4d", "manager", "MASTER"),
        )
        final = "Send one further message with the path of your report when the whole assignment is finished or cannot be finished."
        # MIK-R196: no role may start the free agent, so a parented Bootstrap handover cannot
        # exist and is not a shape this wording test can ask the product for.
        for role in (one for one in LAUNCHER_ROLES if one != "bootstrap"):
            for parent in (None, *parents):
                with self.subTest(role=role, parent=parent.role if parent else None):
                    prompt, handover = self.compiled(role, parent)
                    relation = handover["host"]["ownerRelation"]
                    self.assertIn(relation, prompt)
                    if parent is None:
                        expected = "This role was started from the dashboard launcher. It has no parent agent and needs none: the developer who reads this chat owns its decisions. The selected AR sprint/master/leaf is work scope, not a parent."
                        self.assertEqual(relation.encode(), expected.encode())
                    elif role in {"orchestrator", "manager"}:
                        self.assertIn(
                            f"Agent {parent.agent_id} ({parent.role} · {parent.subject}) started this role and is its parent in Paseo.",
                            relation,
                        )
                        # The categories of a developer decision are the role text's, named
                        # rather than repeated: the same passage may not stand twice in one
                        # first message (MIK-R193 rule 6).
                        self.assertIn(
                            "what needs the developer's decision, as your role text defines it",
                            relation,
                        )
                        self.assertIn(final, relation)
                        self.assertIn("addressed to the return address", relation)
                        self.assertNotIn("addressed to its agent id", relation)
                        self.assertNotIn("questions about the assignment and your result", prompt)
                    elif role in {"worker", "reviewer", "curator"}:
                        self.assertIn("Send no routine result to the parent.", relation)
                        self.assertIn("leafSeats.roleMessageArguments", relation)
                        # A leaf's parent is the current Manager of its master, and a
                        # return goes to that role with its task references (rule 10).
                        self.assertIn("the current Manager of this master", relation)
                        self.assertIn("addressed to the return address role manager", relation)
                        self.assertNotIn("addressed to its agent id", relation)
                    else:
                        self.assertIn(
                            f"Agent {parent.agent_id} ({parent.role} · {parent.subject}) started this role and is its parent in Paseo.",
                            relation,
                        )
                        self.assertIn("addressed to the return address", relation)
                        self.assertIn("is not the address of a return", relation)
                        self.assertNotIn("addressed to its agent id", relation)
                        self.assertIn(
                            "your questions about the assignment, questions requiring the "
                            "developer's decision, and your result",
                            relation,
                        )

    def test_the_first_message_names_no_forbidden_host_string(self) -> None:
        parent = StartingAgent("1f3c2f0e-6a57-4f0b-9d4e-0c8f1a2b3c4d", "architect", "Projects")
        for label, role, started_by in (
            ("a taskless role started from the dashboard", "architect", None),
            ("a sprint-bound role started by an agent", "orchestrator", parent),
            ("a leaf role started by an agent", "worker", parent),
        ):
            with self.subTest(label):
                prompt, handover = self.compiled(role, started_by)
                self.assertIn(REPORT_DIRECTORY, prompt)
                self.assertEqual(forbidden_in(prompt), [])
                self.assertEqual(forbidden_in(json.dumps(handover, ensure_ascii=False)), [])
                # The first message carries the reader arguments; the rest of the reader context
                # is in the complete hand-over file the message names (MIK-R193 rule 1).
                projection = json.loads(
                    prompt.split("AR owner assignment and canonical task handover:\n", 1)[1]
                )
                reader = handover["arMcpContext"]
                reader_arguments = reader["readerArguments"]
                expected = (
                    {"readerArguments": reader_arguments}
                    if reader_arguments is not None
                    else {
                        key: reader[key]
                        for key in ("availableRepositoryIds", "repositorySelection")
                        if reader.get(key) is not None
                    }
                )
                self.assertEqual(projection["arMcpContext"], expected)
                self.assertEqual(
                    projection["handoverFile"]["path"],
                    handover["taskReportPath"].rsplit(".", 1)[0] + ".handover.json",
                )

    def test_the_reader_context_names_the_readers_of_the_task_tool_server(self) -> None:
        _prompt, leaf = self.compiled("worker", None)
        _prompt, taskless = self.compiled("architect", None)
        self.assertEqual(
            (leaf["arMcpContext"]["scopeKind"], taskless["arMcpContext"]["scopeKind"]),
            ("canonical-leaf", "configured-projects"),
        )
        self.assertEqual(
            leaf["arMcpContext"]["missingCapabilityAction"],
            f"If the context_packet or the read_ar_files schema of {TOOL_SERVER} lacks "
            "task_context with both task_document_ref and contract_path, stop and report the "
            "missing ar-task-scoped-readers/v1 capability. Do not call a task reader without "
            "task_context or substitute caller-selected roots.",
        )
        self.assertEqual(
            taskless["arMcpContext"]["missingCapabilityAction"],
            f"If the context_packet or the read_ar_files schema of {TOOL_SERVER} is unavailable, "
            "report that; do not invent a repository id or pass caller-selected roots.",
        )
        # "The installed AR" is the developer's other installation: no reader text points there.
        for handover in (leaf, taskless):
            told = json.dumps([handover["arMcpContext"], handover["host"]], ensure_ascii=False)
            self.assertNotIn("installed", told)
            self.assertNotIn("AR MCP tool", told)

    def test_the_handover_names_the_tool_server_the_two_tools_and_the_host(
        self,
    ) -> None:
        prompt, handover = self.compiled("architect", None)
        host = handover["host"]
        self.assertEqual((host["name"], host["arToolServer"]), ("Paseo", TOOL_SERVER))
        self.assertIn(
            f"Call every Agents Remember tool on the tool server named {TOOL_SERVER}",
            host["arMcpUsage"],
        )
        for said in (
            "and its tools are in this session.",
            # A lookup by the server's name as it is spelled here can find nothing, and one by a
            # tool's own name finds another installation's tool: the server's name, in either
            # spelling, is what to look for.
            "A harness shows them in its own way, so find them by the server's name in either "
            f"spelling, {TOOL_SERVER} or agents_remember_task.",
            "Either a tool is declared to you under a name that contains that spelling and ends "
            "in the tool's own name, for example mcp__agents_remember_task__server_info: call "
            "it.",
            "Or your harness's own instructions to you list a server or namespace in that "
            "spelling, for example mcp__agents_remember_task, and say how its tools are "
            "reached, for example through the harness's tool search or its script tool: reach "
            "them that way, and learn a tool's arguments there too.",
            # A tool that proxies other servers answers for those servers only.
            "A tool that lists, describes, connects or proxies tool servers holds only the "
            f"servers it was configured with. When it does not list {TOOL_SERVER} itself, do "
            "not use it for AR tools at all, not even to search for or describe a tool by its "
            "name: what it answers then comes from another installation, and its answer "
            '"server not found" speaks only for that tool.',
            # The developer's own installation, which every harness carries, by its name; and
            # any other AR tool server, whatever its name: not connected to, listed or called.
            f"Do not connect to, list, describe or call a tool server named {OTHER_INSTALLATION} "
            "or any of its tools, nor an AR tool server under any other name: it belongs to "
            "another installation, its tools carry the same tool names, and nothing found there "
            "serves this assignment.",
            f"look once more before reporting {TOOL_SERVER} missing, and report that instead of "
            "substituting another.",
            "add the requested files to read_ar_files as a list of path strings such as "
            '"<path in the repository>". A read names where each file is and carries no file '
            "text: read the named files with your harness's own file tool.",
        ):
            with self.subTest(said=said):
                self.assertIn(said, host["arMcpUsage"])
        # The paragraph tells an agent where to look and names no harness and no harness's tool.
        self.assertNotRegex(host["arMcpUsage"], r"(?i)codemode|tool_search|\bmcp\(")
        # The first message says it in plain text before the capsule, which names AR tools, and
        # again as a field of the handover.
        self.assertEqual(prompt.count(host["arMcpUsage"]), 1)
        self.assertLess(
            prompt.index(f"AR tools, before your first tool call: {host['arMcpUsage']}"),
            prompt.index("AR owner assignment and canonical task handover:"),
        )
        self.assertEqual(
            {key: host["roleTools"][key] for key in ("toolServer", "start", "message")},
            {
                "toolServer": TOOL_SERVER,
                "start": "role_start",
                "message": "role_message",
            },
        )
        usage = host["roleTools"]["usage"]
        for said in (
            f"role_start on {TOOL_SERVER} starts one role agent",
            f"role_message on {TOOL_SERVER} sends one message to one role agent",
            "It never interrupts a running turn.",
            "repeat the same id to reconcile an uncertain start",
            "an agent created another way has no capsule and no binding",
            "A message the host cannot take now is held by the product instead of refused: to "
            "a recipient that waits for a permission decision, whose start has not finished, "
            "or that is in a turn the host cannot write into.",
            "The answer says held and why, and the product delivers what it holds in the order "
            "it took it once the recipient can take it.",
            "The host is never sent a message for a recipient that waits at a permission "
            "prompt, because it would answer the permission with a denial: the developer "
            "answers it in that agent's chat.",
            "A role address never means the caller itself.",
            "Without wait the call answers accepted once the host took the message, or held "
            "once the product held it.",
            "or it says that a permission is pending, also for a wait on a held message, or "
            "that the time was up, and the message stays delivered then.",
            "the recipient's final text, or that the turn failed or was cancelled",
            "It can answer accepted without a text when the host cannot say which turn took "
            "the message, and the text of a message delivered during a turn can be the running "
            "turn's own; detail says which.",
            "Do not wait on an agent that may be waiting on you: two agents that wait on each "
            "other both stand still until one wait runs out.",
            # What a recipient does with a message: its chat reply is the answer.
            'A message another agent sent you begins with a line "From <role> · <task> · agent '
            '<agent id>": your reply in that turn reaches the sender only when it waits on '
            "its own call; otherwise answer with role_message to that agent id.",
        ):
            with self.subTest(said=said):
                self.assertIn(said, usage)
        self.assertIn(
            "Put every question for the developer in your own chat",
            host["developerQuestions"],
        )
        self.assertIn("taskless role has no task document", handover["ownerHandover"])
        self.assertTrue(
            prompt.startswith("AR ROLE BRIEF: the dashboard launcher started this role")
        )
        self.assertEqual(handover["schema"], "ar-role-handover/v1")
        self.assertEqual(handover["agent"], "some-provider")

    def test_a_role_without_a_parent_is_told_it_needs_none_and_a_started_role_who_started_it(
        self,
    ) -> None:
        _prompt, manual = self.compiled("architect", None)
        self.assertEqual(manual["host"]["entryMode"], "dashboard-role-start")
        self.assertIsNone(manual["host"]["parent"])
        self.assertIn("It has no parent agent and needs none", manual["host"]["ownerRelation"])
        parent = StartingAgent("1f3c2f0e-6a57-4f0b-9d4e-0c8f1a2b3c4d", "architect", "Projects")
        prompt, started = self.compiled("orchestrator", parent)
        self.assertEqual(started["host"]["entryMode"], "agent-role-start")
        self.assertEqual(
            started["host"]["parent"],
            {
                "agentId": parent.agent_id,
                "role": "architect",
                "task": "Projects",
                "sprintDocumentRef": None,
                "masterDocumentRef": None,
                "taskDocumentRef": None,
                "returnAddress": {"role": "architect"},
            },
        )
        self.assertIn(
            f"Agent {parent.agent_id} (architect · Projects) started this role and is its parent",
            started["host"]["ownerRelation"],
        )
        self.assertIn(
            "addressed to the return address role architect", started["host"]["ownerRelation"]
        )
        self.assertNotIn("addressed to its agent id", started["host"]["ownerRelation"])
        self.assertTrue(
            prompt.startswith(
                f"AR ROLE BRIEF: agent {parent.agent_id} (architect · Projects) started this role"
            )
        )


class RoleToolDescriptionWordingTests(unittest.TestCase):
    def test_the_three_tool_descriptions_name_no_forbidden_host_string(self) -> None:
        async def descriptions() -> dict[str, str]:
            server = FastMCP("role-tool-descriptions")
            register_role_agent_tools(server, None)  # type: ignore[arg-type]
            return {tool.name: tool.description or "" for tool in await server.list_tools()}

        described = asyncio.run(descriptions())
        self.assertEqual(
            sorted(described),
            ["role_message", "role_start", "role_start_set", "user_settings_init"],
        )
        # The three role-agent tools are the ones only a launched role agent may call; the
        # settings initializer is the setup surface of MIK-R196 rule 11 and claims no such thing.
        role_agent_tools = {
            name: text for name, text in described.items() if name != "user_settings_init"
        }
        self.assertEqual(len(role_agent_tools), 3)
        reference = " ".join(
            (REPOSITORY_ROOT / "docs/reference/mcp-tools.md").read_text("utf-8").split()
        )
        for name, text in role_agent_tools.items():
            with self.subTest(tool=name):
                self.assertEqual(forbidden_in(text), [])
                if name == "role_message":
                    self.assertIn("Reference: docs/reference/mcp-tools.md.", text)
                    self.assertIn("Only a role agent that AR launched can call it", reference)
                else:
                    self.assertIn("Only a role agent that AR launched can call this", text)
        self.assertEqual(forbidden_in(described["user_settings_init"]), [])
        self.assertIn("in Paseo", described["role_start"])
        told = {name: " ".join(text.split()) for name, text in described.items()}
        told["role_message"] += " " + reference
        for name, sentence in (
            (
                "role_start",
                "Only the agent that started an execution repeats its request_id.",
            ),
            ("role_start", "Starts run one at a time."),
            (
                "role_message",
                "When the host cannot say which turn consumed the message, the call "
                "returns `accepted` without a text and detail says the reply must be read later.",
            ),
            (
                "role_message",
                "A reply to a message delivered during a turn can be the running turn's text; "
                "detail says so then.",
            ),
            (
                "role_message",
                "A recipient waiting on a permission decision, whose start has not finished, "
                "or that is in a turn the host cannot write into is answered `held`, not refused:",
            ),
            (
                "role_message",
                "the reason (`heldReason`), what waits and why (`detail`), "
                "the permission as the host names it or null when unnamed (`permission`)",
            ),
            (
                "role_message",
                "Code `recipient-busy` is refused outright only when the recipient already holds "
                "twenty messages",
            ),
            (
                "role_message",
                "Only the host request is promised: without wait the call answers once the host "
                "accepted the message",
            ),
            (
                "role_message",
                "with wait it answers when the consuming turn ends "
                "(finished, failed, cancelled, permission, timeout)",
            ),
            (
                "role_start_set",
                "Start a leaf's Worker, Reviewer and Curator in one call (MIK-R100 rule 1).",
            ),
            (
                "role_start_set",
                "A repeat with the same request id reconciles the same three agents: it "
                "creates no fourth and opens no held seat.",
            ),
            ("role_message", "an address by role never means the caller itself."),
            ("role_start", "rejected (the launch is closed; no usable agent exists)"),
        ):
            with self.subTest(tool=name, sentence=sentence[:40]):
                self.assertIn(sentence, told[name])
        self.assertIn(
            "`From <role> · <task id or Projects> · agent <sender agent id>`",
            told["role_message"],
        )


if __name__ == "__main__":
    unittest.main()
