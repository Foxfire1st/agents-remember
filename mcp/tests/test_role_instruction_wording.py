"""PNT-R06 items 12 to 14: what the role instructions and the handover text say about the host.

The surfaces are the ones a launched role agent reads: the router, the role file of each of the
seven launcher roles, every operation file that applies to one of them, the routing manifest, the
handover text built in code, and the descriptions of the two role tools. None of them may name
the previous host or its transport, every passage that tells an agent to call an AR tool names
the tool server ``agents-remember-task``, and the handover says in one sentence that a server
named ``agents-remember`` belongs to another installation.
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args
from unittest.mock import patch

from agents_remember.application.orca_task_context import OrcaRoleContext
from agents_remember.cli import orca_task_preparation
from agents_remember.cli.orca_task_preparation import OrcaHandoverRequest, _compile_handover
from agents_remember.cli.paseo_launch import StartingAgent
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.mcp.registration.role_agents import register_role_agent_tools
from agents_remember.models.orca_launcher import OrcaRole
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.tasks import TaskDocument
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from mcp.server.fastmcp import FastMCP

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE = Path("l-01-agent-lifecycles")
CANONICAL = REPOSITORY_ROOT / "skills"
# The copy the capsule compiler reads; a rewrite is not live until the sync has run.
PACKAGED = (
    REPOSITORY_ROOT / "mcp" / "src" / "agents_remember" / "package_data" / "runtime" / "skills"
)

# PNT-R06 item 12, searched case-insensitively.
FORBIDDEN = (
    "orca",
    "native Run",
    "Run/Task/Dispatch",
    "Dispatch worker",
    "worker-start",
    "run-create",
    "worker_done",
    "dispatch_agent",
    "message_parent",
    "message_child",
)
# The report directory of a role keeps its ONT name until the rename leaf (PNT-R08, master
# decision of 2026-10-02T03:16). It reaches the handover only as this segment of the report and
# artifact paths, which is taken out before the search; with the directory renamed this is a no-op.
RETAINED_REPORT_DIRECTORY = "/orca-native/"

TOOL_SERVER = "agents-remember-task"
OTHER_INSTALLATION = "agents-remember"
LAUNCHER_ROLES: tuple[str, ...] = get_args(OrcaRole)
STARTERS = ("architect", "orchestrator", "manager")
_NAMED_TOOL = re.compile("`(" + "|".join(sorted(map(re.escape, PUBLIC_TOOLS))) + ")`")


def forbidden_in(text: str) -> list[str]:
    """The forbidden strings a text contains, in the packet's spelling."""

    folded = text.casefold()
    return [word for word in FORBIDDEN if word.casefold() in folded]


def without_retained_paths(text: str) -> str:
    return text.replace(RETAINED_REPORT_DIRECTORY, "/")


def instruction_files() -> list[Path]:
    """The router, the launcher roles' files and their operations, relative to a skill tree."""

    manifest = json.loads((CANONICAL / LIFECYCLE / "composition-manifest.json").read_text("utf-8"))
    roles = [manifest["roles"][role]["file"] for role in LAUNCHER_ROLES]
    operations = [
        entry["source"]
        for entry in manifest["operations"].values()
        if set(entry["applies_to_roles"]) & set(LAUNCHER_ROLES)
    ]
    return [LIFECYCLE / name for name in ("SKILL.md", *roles, *sorted(operations))]


def read(relative: Path) -> str:
    return (CANONICAL / relative).read_text(encoding="utf-8")


def role_text(role: str) -> str:
    return " ".join(read(LIFECYCLE / "roles" / f"{role}.md").split())


class InstructionFileWordingTests(unittest.TestCase):
    def test_the_surfaces_are_the_router_seven_roles_and_their_eight_operations(self) -> None:
        names = [path.relative_to(LIFECYCLE).as_posix() for path in instruction_files()]
        self.assertEqual(names[0], "SKILL.md")
        self.assertEqual(names[1:8], [f"roles/{role}.md" for role in LAUNCHER_ROLES])
        self.assertEqual(
            names[8:],
            [
                f"operations/{name}.md"
                for name in (
                    "closeout",
                    "coordination",
                    "curation",
                    "implementation",
                    "orientation",
                    "planning",
                    "recovery",
                    "review",
                )
            ],
        )

    def test_no_forbidden_host_string_is_left_and_the_packaged_copy_is_the_same_text(self) -> None:
        for relative in (*instruction_files(), LIFECYCLE / "composition-manifest.json"):
            with self.subTest(file=relative.as_posix()):
                self.assertEqual(forbidden_in(read(relative)), [])
                self.assertEqual(
                    (PACKAGED / relative).read_bytes(),
                    (CANONICAL / relative).read_bytes(),
                    "run python3 scripts/sync-skills.py",
                )

    def test_every_passage_that_names_an_ar_tool_names_the_task_tool_server(self) -> None:
        naming = 0
        for relative in instruction_files():
            for passage in re.split(r"\n\s*\n", read(relative)):
                tools = sorted(set(_NAMED_TOOL.findall(passage)))
                if not tools:
                    continue
                naming += 1
                with self.subTest(file=relative.as_posix(), tools=tools):
                    self.assertIn(f"`{TOOL_SERVER}`", passage)
        # The rule is checked on real passages, not satisfied by finding none.
        self.assertGreaterEqual(naming, 20)

    def test_the_router_names_the_host_the_two_tools_and_the_only_tool_server(self) -> None:
        router = " ".join(read(LIFECYCLE / "SKILL.md").split())
        for sentence in (
            "Paseo is the host",
            "Call every AR tool on the tool server named `agents-remember-task`",
            "A tool server named `agents-remember`, if your session has one, belongs to another "
            "installation and must not be used for this assignment.",
            "wait a few seconds and call once more before you report it missing",
            "Put every question for the developer in your own chat",
            "A role started from the dashboard has no parent agent and needs none.",
            "an agent created outside `role_start` on `agents-remember-task` has no capsule and no binding",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, router)
        for tool in ("role_start", "role_message"):
            self.assertIn(tool, PUBLIC_TOOLS)
            self.assertIn(tool, router)

    def test_each_role_is_told_how_to_reach_agents_and_the_developer(self) -> None:
        for role in LAUNCHER_ROLES:
            with self.subTest(role=role):
                text = role_text(role)
                self.assertIn("`role_message`", text)
                self.assertIn("in your own chat", text)
                if role in STARTERS:
                    self.assertIn("`role_start`", text)
                else:
                    self.assertNotIn("`role_start`", text)
                    self.assertRegex(
                        text, r"A (Worker|Reviewer|Curator|System Specialist) starts no role"
                    )
        for role in set(LAUNCHER_ROLES) - {"architect"}:
            with self.subTest(role=role, told="a role without a parent needs none"):
                self.assertRegex(
                    role_text(role),
                    r"started from the dashboard,? (has no parent|has none|needs no parent)",
                )
        coordination = " ".join(read(LIFECYCLE / "operations" / "coordination.md").split())
        for sentence in (
            "an Architect every role except Architect",
            "an Orchestrator Manager, Worker, Reviewer, and Curator under its own sprint",
            "a Manager Worker, Reviewer, and Curator under its own master",
            "call it again with the same request ID",
            "takes the message up without its turn being cancelled",
            "A role started from the dashboard works directly with the developer and needs no parent.",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, coordination)

    def test_the_manifest_grants_the_role_tools_to_the_roles_that_may_use_them(self) -> None:
        manifest = json.loads(read(LIFECYCLE / "composition-manifest.json"))
        for role in LAUNCHER_ROLES:
            with self.subTest(role=role):
                tools = manifest["roles"][role]["tools"]
                self.assertIn("role_message", tools)
                self.assertEqual("role_start" in tools, role in STARTERS)
                self.assertEqual(set(tools) - set(PUBLIC_TOOLS), set())


class HandoverTextWordingTests(unittest.TestCase):
    """The handover text as the launch code builds it; the capsule and the reads are stubbed."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.config = McpRuntimeConfig(
            config_path=root / "settings" / "mcp.json",
            coordination_root=root / "coordination",
            workspace_root=root / "projects",
            transcript_root=root / "coordination" / "logs" / "mcp",
        )
        delivery = SimpleNamespace(
            semantic_digest="capsule-digest",
            trusted_instructions="\n\n".join(read(path) for path in instruction_files()),
            binding=SimpleNamespace(
                role="orchestrator", operation="coordination", as_report=lambda: {"role": "x"}
            ),
        )
        capsule = SimpleNamespace(is_refusal=False, codex_delivery=delivery, role="orchestrator")
        for name, stub in (
            ("compile_launch_capsule", lambda *_args, **_kwargs: capsule),
            ("build_context_packet", lambda *_args, **_kwargs: {"repo": {"id": "repo"}}),
            ("_read_task_doc", lambda _config, resolved: {"canonicalTaskPath": resolved.ref.key}),
        ):
            patcher = patch.object(orca_task_preparation, name, stub)
            patcher.start()
            self.addCleanup(patcher.stop)

    def compiled(self, role: str, started_by: StartingAgent | None) -> tuple[str, dict[str, Any]]:
        sprint = None
        if role == "orchestrator":
            ref = TaskDocumentRef(repository="repo", path="sprint/task.json")
            document = TaskDocument.model_validate(
                {
                    "id": "SPRINT",
                    "slug": "task",
                    "title": "Sprint",
                    "kind": "master",
                    "repo": "repo",
                    "createdAt": "2026-10-02T00:00:00+00:00",
                }
            )
            path = self.config.coordination_root / "tasks" / "repo" / ref.path
            sprint = ResolvedTaskDocument(ref=ref, path=path, document=document)
        prepared = _compile_handover(
            OrcaHandoverRequest(
                config=self.config,
                context=OrcaRoleContext(role, sprint, None, None, sprint),  # type: ignore[arg-type]
                workspace={"path": self.config.workspace_root.as_posix()},
                agent_id="some-provider",
                ar_mcp_context={"scopeKind": "configured-projects"},
                request_id=uuid.uuid4(),
                started_by=started_by,
            )
        )
        return prepared["prompt"], prepared["handover"]

    def test_the_first_message_names_no_forbidden_host_string(self) -> None:
        parent = StartingAgent("1f3c2f0e-6a57-4f0b-9d4e-0c8f1a2b3c4d", "architect", "Projects")
        for label, role, started_by in (
            ("started from the dashboard", "architect", None),
            ("started by an agent", "orchestrator", parent),
        ):
            with self.subTest(label):
                prompt, handover = self.compiled(role, started_by)
                self.assertIn(RETAINED_REPORT_DIRECTORY, prompt)
                self.assertEqual(forbidden_in(without_retained_paths(prompt)), [])
                self.assertEqual(
                    forbidden_in(without_retained_paths(json.dumps(handover, ensure_ascii=False))),
                    [],
                )

    def test_the_handover_names_the_tool_server_the_two_tools_and_the_host(self) -> None:
        prompt, handover = self.compiled("architect", None)
        host = handover["host"]
        self.assertEqual((host["name"], host["arToolServer"]), ("Paseo", TOOL_SERVER))
        self.assertIn(
            f"Call every Agents Remember tool on the tool server named {TOOL_SERVER}",
            host["arMcpUsage"],
        )
        # The one sentence about the developer's own installation, which every harness carries.
        self.assertIn(
            f"A tool server named {OTHER_INSTALLATION}, if this session has one, belongs to "
            "another installation and must not be used for this assignment.",
            host["arMcpUsage"],
        )
        self.assertIn(
            "make the call once more before reporting the server missing", host["arMcpUsage"]
        )
        self.assertEqual(
            {key: host["roleTools"][key] for key in ("toolServer", "start", "message")},
            {"toolServer": TOOL_SERVER, "start": "role_start", "message": "role_message"},
        )
        usage = host["roleTools"]["usage"]
        for said in (
            f"role_start on {TOOL_SERVER} starts one role agent",
            f"role_message on {TOOL_SERVER} sends one message to one role agent",
            "It never interrupts a running turn.",
            "repeat the same id to reconcile an uncertain start",
            "an agent created another way has no capsule and no binding",
        ):
            with self.subTest(said=said):
                self.assertIn(said, usage)
        self.assertIn(
            "Put every question for the developer in your own chat", host["developerQuestions"]
        )
        self.assertIn(f"calling task_doc on {TOOL_SERVER}", handover["ownerHandover"])
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
            {"agentId": parent.agent_id, "role": "architect", "task": "Projects"},
        )
        self.assertIn(
            f"Agent {parent.agent_id} (architect · Projects) started this role and is its parent",
            started["host"]["ownerRelation"],
        )
        self.assertIn("role_message, addressed to its agent id", started["host"]["ownerRelation"])
        self.assertTrue(
            prompt.startswith(
                f"AR ROLE BRIEF: agent {parent.agent_id} (architect · Projects) started this role"
            )
        )


class RoleToolDescriptionWordingTests(unittest.TestCase):
    def test_the_two_tool_descriptions_name_no_forbidden_host_string(self) -> None:
        async def descriptions() -> dict[str, str]:
            server = FastMCP("role-tool-descriptions")
            register_role_agent_tools(server, None)  # type: ignore[arg-type]
            return {tool.name: tool.description or "" for tool in await server.list_tools()}

        described = asyncio.run(descriptions())
        self.assertEqual(sorted(described), ["role_message", "role_start"])
        for name, text in described.items():
            with self.subTest(tool=name):
                self.assertEqual(forbidden_in(text), [])
                self.assertIn("Only a role agent that AR launched can call this", text)
        self.assertIn("in Paseo", described["role_start"])
        self.assertIn(
            '"From <role> · <task id or Projects> · agent <sender agent id>"',
            described["role_message"],
        )


if __name__ == "__main__":
    unittest.main()
