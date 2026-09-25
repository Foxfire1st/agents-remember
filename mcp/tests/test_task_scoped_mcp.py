from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from agents_remember.application.lifecycle.configured_contract_admission import (
    ConfiguredContractAccepted,
)
from agents_remember.application.task_scoped_mcp import (
    TASK_SCOPED_MCP_PROFILE_SCHEMA,
    TaskScopedMcpBinding,
    task_scoped_mcp_config,
    task_scoped_mcp_config_from_profile,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.tasks.task_paths import leaf_enclosure_path
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocation,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


class TaskScopedMcpConfigTests(unittest.TestCase):
    def test_scoped_config_keeps_authority_and_binds_every_tool_to_the_leaf_pair(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            coordinator = root / "ar-coordination"
            task_root = coordinator / "tasks" / "repo" / "master"
            workspace = coordinator / "worktrees" / "repo" / "leaf-group"
            code = workspace / "leaf-code"
            memory = workspace / "leaf-memory"
            contract_path = leaf_enclosure_path(task_root, "L7")
            for path in (code, memory, task_root, contract_path.parent):
                path.mkdir(parents=True, exist_ok=True)
            config_path = root / "settings" / "mcp.json"
            config_path.parent.mkdir()
            config_path.write_text("{}\n", encoding="utf-8")
            doc = TaskDocument.model_validate(
                {
                    "id": "L7",
                    "slug": "l7",
                    "title": "Leaf",
                    "kind": "subTask",
                    "status": "inProgress",
                    "repo": "repo",
                    "createdAt": "2026-09-24T10:00",
                    "master": "task.md",
                    "enclosures": [{"leafId": "L7", "enclosurePath": contract_path.as_posix()}],
                }
            )
            write_task_doc(task_root, doc)
            ref = TaskDocumentRef(repository="repo", path="master/l7.json")
            self.assertEqual(TaskDocumentTopology(coordinator).resolve(ref).document.id, "L7")

            base_scope = RepositoryScope(
                repo_id="repo", path=root / "repo", memory_root=root / "memory"
            )
            base = McpRuntimeConfig(
                config_path=config_path,
                coordination_root=coordinator,
                workspace_root=root,
                transcript_root=coordinator / "logs" / "mcp",
                repositories={"repo": base_scope},
            )
            contract = SimpleNamespace(
                kind="leaf",
                leaf_id="L7",
                code_worktree=code,
                memory_worktree=memory,
            )
            admission = ConfiguredContractAccepted(
                contract_path=contract_path,
                contract=cast(WorktreeContract, contract),
                location=cast(
                    LifecycleOperationLocation,
                    SimpleNamespace(worktree_group=workspace),
                ),
            )
            with patch(
                "agents_remember.application.task_scoped_mcp.admit_configured_contract",
                return_value=admission,
            ):
                scoped = task_scoped_mcp_config(
                    base,
                    TaskScopedMcpBinding(
                        task_document_ref=ref,
                        contract_path=contract_path,
                        workspace_root=workspace,
                        code_root=code,
                        memory_root=memory,
                    ),
                )
                self.assertEqual(scoped.config_path, config_path)
                self.assertEqual(scoped.coordination_root, coordinator)
                self.assertEqual(scoped.workspace_root, workspace)
                self.assertEqual(scoped.repositories["repo"].path, code)
                self.assertEqual(scoped.repositories["repo"].memory_root, memory)
                self.assertEqual(scoped.repositories["repo"].contract_path, contract_path)
                with self.assertRaisesRegex(ValueError, "do not match"):
                    task_scoped_mcp_config(
                        base,
                        TaskScopedMcpBinding(
                            task_document_ref=ref,
                            contract_path=contract_path,
                            workspace_root=workspace,
                            code_root=code,
                            memory_root=root / "wrong-memory",
                        ),
                    )

                profile = {
                    "schema": TASK_SCOPED_MCP_PROFILE_SCHEMA,
                    "baseConfigPath": config_path.as_posix(),
                    "taskDocumentRef": ref.model_dump(mode="json"),
                    "contractPath": contract_path.as_posix(),
                    "workspaceRoot": workspace.as_posix(),
                    "codeRoot": code.as_posix(),
                    "memoryRoot": memory.as_posix(),
                }
                loaded = task_scoped_mcp_config_from_profile(base, profile)
                self.assertEqual(loaded.repositories["repo"].path, code)
                self.assertEqual(loaded.repositories["repo"].memory_root, memory)


if __name__ == "__main__":
    unittest.main()
