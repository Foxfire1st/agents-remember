from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from agents_remember.application.context_packet import ContextPacketRequest, build_context_packet
from agents_remember.application.orca_task_context import resolve_orca_role_context
from agents_remember.application.skill_resources import CapsuleCompileRequest, compile_task_capsule
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.application.task_scoped_mcp import TaskScopedMcpBinding, task_scoped_mcp_config
from agents_remember.application.worktree_services import build_default_worktree_services
from agents_remember.cli.orca_runtime import MAX_PROMPT_BYTES, digest
from agents_remember.cli.orca_scoped_mcp import ScopedNativeMcp
from agents_remember.cli.orca_task_preparation import (
    OrcaHandoverRequest,
    _bind_task_report_access,
    _compile_handover,
)
from agents_remember.kernel.coordination_context.models import EnclosureSelector
from agents_remember.kernel.primitives import checkout_coordination
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.models.orca_launcher import OrcaSelection
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.services import bind_worktree_services, reset_worktree_services
from test_worktree_support import open_external_contract_fixture


class OrcaScopedCapsuleBindingTests(unittest.TestCase):
    def test_leaf_roles_compile_contract_repo_identity_with_scoped_context_and_reports(  # noqa: PLR0915
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict(checkout_coordination._declared, {"mode": "test"}):
                contract = open_external_contract_fixture(root)
            assert contract.memory_worktree is not None
            repo_id = contract.repo_name
            leaf_slug = contract.leaf_id.lower().replace("_", "-")
            sprint_root = contract.coordination_root / "tasks" / repo_id / "sprint"
            write_task_doc(
                sprint_root,
                TaskDocument.model_validate(
                    {
                        "id": "SPRINT",
                        "slug": "sprint",
                        "title": "Binding proof sprint",
                        "kind": "master",
                        "repo": repo_id,
                        "createdAt": "2026-09-24T10:00:00+00:00",
                        "orchestrates": [contract.task_root.name],
                    }
                ),
            )
            write_task_doc(
                contract.task_root,
                TaskDocument.model_validate(
                    {
                        "id": contract.task_id,
                        "slug": "task",
                        "title": contract.task_name,
                        "kind": "master",
                        "repo": repo_id,
                        "createdAt": "2026-09-24T10:01:00+00:00",
                        "subTasks": [
                            {
                                "number": contract.leaf_id,
                                "name": "Scoped capsule binding",
                                "file": f"{leaf_slug}.md",
                                "status": "inProgress",
                            }
                        ],
                    }
                ),
            )
            write_task_doc(
                contract.task_root,
                TaskDocument.model_validate(
                    {
                        "id": contract.leaf_id,
                        "slug": leaf_slug,
                        "title": "Scoped capsule binding",
                        "kind": "subTask",
                        "status": "inProgress",
                        "repo": repo_id,
                        "createdAt": "2026-09-24T10:02:00+00:00",
                        "master": "task.md",
                        "enclosures": [
                            {
                                "leafId": contract.leaf_id,
                                "enclosurePath": contract.contract_path.as_posix(),
                            }
                        ],
                    }
                ),
            )

            settings_path = root / "settings" / "ar.json"
            settings_path.parent.mkdir(parents=True)
            settings_path.write_text(
                json.dumps(
                    {
                        "coordinationRoot": contract.coordination_root.as_posix(),
                        "workspaceRoot": root.as_posix(),
                        "repositories": {repo_id: {}},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            task_ref = TaskDocumentRef(
                repository=repo_id,
                path=f"{contract.task_root.name}/{leaf_slug}.json",
            )
            sprint_ref = TaskDocumentRef(repository=repo_id, path="sprint/task.json")
            master_ref = TaskDocumentRef(
                repository=repo_id,
                path=f"{contract.task_root.name}/task.json",
            )

            with patch.dict(checkout_coordination._declared, {"mode": "test"}):
                base_config = load_config(settings_path)
                scoped_config = task_scoped_mcp_config(
                    base_config,
                    TaskScopedMcpBinding(
                        task_document_ref=task_ref,
                        contract_path=contract.contract_path,
                        workspace_root=contract.worktree_group,
                        code_root=contract.code_worktree,
                        memory_root=contract.memory_worktree,
                    ),
                )
                bind_worktree_services(build_default_worktree_services())
                try:
                    context_packet = build_context_packet(
                        scoped_config,
                        ContextPacketRequest(repo_id=repo_id, include_providers=False),
                    )
                finally:
                    reset_worktree_services()

            self.assertNotEqual(contract.code_worktree.name, repo_id)
            self.assertEqual(scoped_config.repositories[repo_id].path, contract.code_worktree)
            self.assertEqual(
                scoped_config.repositories[repo_id].memory_root, contract.memory_worktree
            )
            workspace_path = contract.worktree_group
            task_reports = contract.task_root / "notes" / "reports"
            task_reports.mkdir(parents=True, exist_ok=True)
            workspace = {
                "id": "fixture-workspace",
                "selector": "id:fixture-workspace",
                "path": workspace_path.as_posix(),
                "contractPath": contract.contract_path.as_posix(),
                "codeRoot": contract.code_worktree.as_posix(),
                "memoryRoot": contract.memory_worktree.as_posix(),
                "taskReportRoot": task_reports.resolve().as_posix(),
                "taskReportAccessRoot": _bind_task_report_access(
                    workspace_path, task_reports
                ).as_posix(),
            }
            native_scope = cast(
                ScopedNativeMcp,
                SimpleNamespace(
                    context_packet=context_packet,
                    launch_args=(),
                    verification={
                        "status": "verified",
                        "workspaceRoot": workspace_path.as_posix(),
                        "codeRoot": contract.code_worktree.as_posix(),
                        "memoryRoot": contract.memory_worktree.as_posix(),
                        "contractPath": contract.contract_path.as_posix(),
                    },
                ),
            )

            for role in ("worker", "reviewer", "curator"):
                with self.subTest(role=role):
                    context = resolve_orca_role_context(
                        scoped_config,
                        OrcaSelection(
                            role=role,
                            sprintDocumentRef=sprint_ref,
                            masterDocumentRef=master_ref,
                            taskDocumentRef=task_ref,
                        ),
                    )
                    prepared = _compile_handover(
                        OrcaHandoverRequest(
                            config=scoped_config,
                            context=context,
                            workspace=workspace,
                            agent_id="codex",
                            native_mcp_scope=native_scope,
                        )
                    )
                    handover = json.loads(
                        prepared["prompt"].rsplit(
                            "\n\nAR owner assignment and canonical task handover:\n", 1
                        )[1]
                    )
                    binding = handover["capsule"]["binding"]
                    canonical_report = Path(prepared["canonicalTaskReportPath"])
                    documents = handover["documents"]
                    self.assertEqual(binding["repositoryId"], repo_id)
                    self.assertEqual(binding["taskPath"], task_ref.key)
                    self.assertEqual(binding["role"], role)
                    self.assertEqual(
                        handover["workspace"]["codeRoot"], contract.code_worktree.as_posix()
                    )
                    self.assertEqual(
                        handover["workspace"]["memoryRoot"], contract.memory_worktree.as_posix()
                    )
                    self.assertEqual(
                        handover["contextPacket"]["repo"]["root"], contract.code_worktree.as_posix()
                    )
                    self.assertEqual(
                        handover["contextPacket"]["paths"]["memoryRoot"],
                        contract.memory_worktree.as_posix(),
                    )
                    self.assertEqual(
                        handover["workspace"]["contractPath"], contract.contract_path.as_posix()
                    )
                    self.assertTrue(canonical_report.is_relative_to(task_reports.resolve()))
                    self.assertLess(
                        len(prepared["prompt"].encode("utf-8")),
                        MAX_PROMPT_BYTES,
                    )
                    self.assertEqual(len(documents), 3)
                    documents_by_ref = {
                        document.ref.key: document
                        for document in (context.sprint, context.master, context.task)
                        if document is not None
                    }
                    for row in documents:
                        resolved = documents_by_ref[row["canonicalTaskPath"]]
                        source = resolved.document.model_dump(
                            mode="json", by_alias=True, exclude_none=True
                        )
                        self.assertNotIn("document", row)
                        self.assertEqual(row["contentDigest"], digest(source))
                        self.assertEqual(row["documentIdentity"]["repositoryId"], source["repo"])
                        read_args = row["taskDocReadArgs"]
                        self.assertEqual(read_args["operation"], "get")
                        self.assertEqual(read_args["repo_id"], resolved.ref.repository)
                        self.assertEqual(read_args["task_name"], resolved.path.parent.name)
                        self.assertEqual(read_args["slug"], resolved.path.stem)
                        self.assertFalse(read_args["slug"].endswith(".json"))
                        read_result = task_doc_tool(
                            scoped_config,
                            TaskDocTarget(
                                repo_id=read_args["repo_id"],
                                task_name=read_args["task_name"],
                                slug=read_args["slug"],
                            ),
                            operation=read_args["operation"],
                            edit=TaskDocEdit(),
                            call=TaskDocCall(),
                        )
                        self.assertTrue(read_result["ok"])
                        self.assertEqual(read_result["docPath"], resolved.path.as_posix())
                    self.assertEqual(prepared["taskDocumentDigest"], digest(documents))

                    outcome = compile_task_capsule(
                        scoped_config,
                        CapsuleCompileRequest(
                            enclosure=EnclosureSelector(contract_path=contract.contract_path),
                            task_path=task_ref.path,
                            operation="orientation",
                            role=role,
                        ),
                    )
                    self.assertTrue(outcome.ok, outcome.explanation())
                    assert outcome.result is not None
                    admitted = outcome.result.capsule.binding.admitted
                    self.assertEqual(admitted.repository_id, repo_id)
                    self.assertEqual(admitted.work_branch, contract.code_work_branch)
                    self.assertEqual(prepared["capsuleDigest"], outcome.result.semantic_digest)
