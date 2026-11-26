"""Brief preamble identity self-check and parent role mismatch handling (BUG-R51 rules 3-4).

Verifies that every compiled first message carries the `server_info` identity self-check in its
brief preamble before the first-read paragraph and handover heading, and that all three parent
role sources (`architect.md`, `orchestrator.md`, `manager.md`) carry the `IDENTITY MISMATCH:`
child-seat recovery rule.
"""

from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents_remember.application.role_capsules.compilation import compile_admitted_capsule
from agents_remember.application.role_capsules.launch import _capsule_launch
from agents_remember.application.role_launch_context import (
    RoleLaunchContext,
    resolve_role_launch_context,
)
from agents_remember.application.skill_resources.capsule import (
    CapsuleSeatAddress,
    routed_admission_for,
)
from agents_remember.cli import role_launch_enclosure, role_launch_preparation
from agents_remember.cli.paseo_launch import StartingAgent
from agents_remember.cli.role_brief_message import (
    HANDOVER_HEADING,
    IDENTITY_SELF_CHECK_PARAGRAPH,
    BriefParts,
    brief_prompt,
)
from agents_remember.cli.role_handover_projection import first_read_paragraph
from agents_remember.cli.role_launch_preparation import (
    RoleHandoverRequest,
    _ar_mcp_context,
    _compile_handover,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
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

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_LIFECYCLE = REPOSITORY_ROOT / "skills" / "l-01-agent-lifecycles"
PARENT_ROLE_FILES = (
    CANONICAL_LIFECYCLE / "roles" / "architect.md",
    CANONICAL_LIFECYCLE / "roles" / "orchestrator.md",
    CANONICAL_LIFECYCLE / "roles" / "manager.md",
)
EXPECTED_PARENT_RULE = (
    "On an `IDENTITY MISMATCH:` notice from a child seat: archive that seat, "
    "check its task folder and the folder named by the reported binding for writes, "
    "and start a replacement."
)
FIRST_READ_MARKER = "Each contentDigest is launch snapshot;"


class LaunchIdentityBriefTests(unittest.TestCase):
    """Rule 3 brief preamble self-check and Rule 4 parent role mismatch handling."""

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
            ("compile_launch_capsule", self._canonical_capsule),
            ("build_context_packet", lambda *_args, **_kwargs: {"repo": {"id": "repo"}}),
            (
                "_read_task_doc",
                lambda _config, resolved: {
                    "canonicalTaskPath": resolved.ref.key,
                    "taskDocumentRef": resolved.ref.model_dump(mode="json"),
                },
            ),
            ("task_scoped_mcp_config_for_reader", lambda config, **_scope: config),
        ):
            patcher = patch.object(role_launch_preparation, name, stub)
            patcher.start()
            self.addCleanup(patcher.stop)
        scope_patcher = patch.object(
            role_launch_enclosure, "publication_scope", lambda *_args, **_kwargs: nullcontext()
        )
        scope_patcher.start()
        self.addCleanup(scope_patcher.stop)

    def _canonical_capsule(
        self, _config: McpRuntimeConfig, request: Any, *, operation: CapsuleOperation
    ) -> Any:
        manifest_bytes = (CANONICAL_LIFECYCLE / "composition-manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        role = request.role
        binding = CapsuleBinding(
            operation=operation,
            admitted=CapsuleAdmittedFacts(
                task_reference=request.task_document_ref.key
                if request.task_document_ref
                else "fixture:Projects",
                task_document_digest=compute_content_digest(b"launch-identity-brief-fixture"),
                seat=CapsuleRoleSeat(role=role, altitude=manifest["roles"][role]["altitude"]),
                repository_id="repo",
                work_branch="fixture",
                tool_policy=CapsuleToolPolicy(granted=frozenset(PUBLIC_TOOLS)),
            ),
        )
        outcome = compile_admitted_capsule(
            binding,
            routed_admission_for(
                CANONICAL_LIFECYCLE,
                "composition-manifest.json",
                manifest_bytes,
                CapsuleSeatAddress(role=role, operation=operation),
            ),
        )
        self.assertTrue(outcome.ok, outcome.render_explanation())
        assert outcome.result is not None
        return _capsule_launch(role, outcome.result)

    def _launch_context(self, role: str) -> tuple[RoleLaunchContext, dict[str, str]]:
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
                    "createdAt": "2026-11-25T00:00:00+00:00",
                    **fields,
                }
            )
            path = root / "tasks" / "repo" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(document.model_dump_json(by_alias=True), encoding="utf-8")

        refs: dict[str, Any] = {}
        if role != "architect":
            refs["sprintDocumentRef"] = {"repository": "repo", "path": "sprint/task.json"}
        if role in {"manager", "worker", "reviewer", "curator"}:
            refs["masterDocumentRef"] = {"repository": "repo", "path": "master/task.json"}
        if role in {"worker", "reviewer", "curator"}:
            refs["taskDocumentRef"] = {"repository": "repo", "path": "master/01_leaf.json"}

        context = resolve_role_launch_context(
            self.config, RoleSelection.model_validate({"role": role, **refs})
        )
        workspace: dict[str, str] = {"path": self.config.workspace_root.as_posix()}
        if context.task is not None:
            enclosure = self.config.workspace_root / "enclosure"
            contract_path = (
                context.task.path.parent / "enclosures" / "01_leaf" / "series-contract.md"
            )
            workspace = {
                "path": enclosure.as_posix(),
                "contractPath": contract_path.as_posix(),
                "taskReportAccessRoot": (enclosure / "role-reports").as_posix(),
                "taskReportRoot": (context.task.path.parent / "notes" / "reports").as_posix(),
            }
        return context, workspace

    def _assert_self_check_clauses(self, paragraph: str) -> None:
        for required in (
            "first action before any other call or write",
            "call `server_info` on `agents-remember-task`",
            "`agentBinding.requestId`",
            "`agentBinding.role`",
            "task document refs match `requestId`, `role`, and `selection` in your handover below",
            "ignore host.parent.agentId, which is your parent's id",
            "On any mismatch, or if `agents-remember-task` does not answer",
            "do no work, make no write, send no `role_message`",
            "at most 300 characters",
            "`IDENTITY MISMATCH: expected requestId=<requestId> role=<role>; got <actual binding or none>`",
        ):
            self.assertIn(required, paragraph)

    def test_brief_prompt_places_identity_self_check_in_preamble(self) -> None:
        self._assert_self_check_clauses(IDENTITY_SELF_CHECK_PARAGRAPH)
        request_id = str(uuid.uuid4())
        handover = {
            "schema": "ar-role-handover/v1",
            "requestId": request_id,
            "role": "worker",
            "operation": "implementation",
            "selection": {
                "sprintDocumentRef": {"repository": "repo", "path": "sprint/task.json"},
                "masterDocumentRef": {"repository": "repo", "path": "master/task.json"},
                "taskDocumentRef": {"repository": "repo", "path": "master/01_leaf.json"},
            },
            "taskReportPath": "/tmp/role-reports/worker.md",
            "host": {"parent": {"agentId": "parent-agent-id", "role": "manager"}},
        }
        parts = BriefParts(
            started_from="agent parent-agent-id (manager · MASTER)",
            direction="Split independent work.",
            ar_tools="Call AR tools on agents-remember-task.",
            first_read="Read your task documents first.",
            instructions="# Worker\n\nDo the work.",
        )
        prompt, projection, _handover_file, _content = brief_prompt(
            handover,
            report_path="/tmp/role-reports/worker.md",
            bound_document=handover["selection"]["taskDocumentRef"],
            parts=parts,
        )

        self.assertEqual(prompt.count(IDENTITY_SELF_CHECK_PARAGRAPH), 1)
        ar_tools_line = f"AR tools: {parts.ar_tools}"
        first_read = first_read_paragraph(parts.first_read)
        self.assertIn(ar_tools_line, prompt)
        self.assertLess(prompt.index(ar_tools_line), prompt.index(IDENTITY_SELF_CHECK_PARAGRAPH))
        self.assertLess(
            prompt.index(IDENTITY_SELF_CHECK_PARAGRAPH), prompt.index(parts.instructions)
        )
        self.assertLess(prompt.index(parts.instructions), prompt.index(first_read))
        self.assertLess(prompt.index(first_read), prompt.index(HANDOVER_HEADING))
        self.assertEqual(projection["requestId"], request_id)
        self.assertEqual(projection["role"], "worker")
        self.assertEqual(
            projection["selection"],
            {k: v for k, v in handover["selection"].items() if v is not None},
        )

    def test_compiled_first_messages_across_roles_include_identity_self_check(self) -> None:
        architect_parent = StartingAgent(
            "1f3c2f0e-6a57-4f0b-9d4e-0c8f1a2b3c4d", "architect", "Projects"
        )
        manager_parent = StartingAgent("3f3c2f0e-6a57-4f0b-9d4e-0c8f1a2b3c4d", "manager", "MASTER")
        role_cases: tuple[tuple[str, StartingAgent | None], ...] = (
            ("architect", None),
            ("orchestrator", architect_parent),
            ("manager", architect_parent),
            ("worker", manager_parent),
            ("reviewer", manager_parent),
            ("curator", manager_parent),
        )
        for role, started_by in role_cases:
            with self.subTest(role=role):
                context, workspace = self._launch_context(role)
                request_id = uuid.uuid4()
                role_launch_preparation._prepare_role_folders(context, workspace, request_id)
                prepared = _compile_handover(
                    RoleHandoverRequest(
                        config=self.config,
                        context=context,
                        workspace=workspace,
                        agent_id="codex",
                        ar_mcp_context=_ar_mcp_context(self.config, context, workspace),
                        request_id=request_id,
                        started_by=started_by,
                    )
                )
                prompt = prepared["prompt"]
                handover = prepared["handover"]
                ar_tools_line = f"AR tools: {handover['host']['arMcpUsage']}"

                self.assertEqual(prompt.count(IDENTITY_SELF_CHECK_PARAGRAPH), 1)
                self.assertIn(ar_tools_line, prompt)
                self.assertLess(
                    prompt.index(ar_tools_line),
                    prompt.index(IDENTITY_SELF_CHECK_PARAGRAPH),
                )
                self.assertLess(
                    prompt.index(IDENTITY_SELF_CHECK_PARAGRAPH),
                    prompt.index(FIRST_READ_MARKER),
                )
                self.assertLess(
                    prompt.index(FIRST_READ_MARKER),
                    prompt.index(HANDOVER_HEADING),
                )

                projection = json.loads(prompt.split(f"{HANDOVER_HEADING}\n", 1)[1])
                self.assertEqual(projection["requestId"], str(request_id))
                self.assertEqual(projection["role"], role)
                self.assertEqual(
                    projection["selection"],
                    {k: v for k, v in handover["selection"].items() if v is not None},
                )
                if role in {"architect", "orchestrator", "manager"}:
                    self.assertIn(EXPECTED_PARENT_RULE, prompt)

    def test_parent_role_sources_carry_identity_mismatch_handling_rule(self) -> None:
        for role_file in PARENT_ROLE_FILES:
            with self.subTest(role=role_file.name):
                text = role_file.read_text(encoding="utf-8")
                self.assertIn(EXPECTED_PARENT_RULE, text)
                self.assertEqual(text.count("IDENTITY MISMATCH:"), 1)


if __name__ == "__main__":
    unittest.main()
