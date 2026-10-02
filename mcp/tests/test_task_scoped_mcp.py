from __future__ import annotations

import contextlib
import io
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
    task_scoped_mcp_config_for_reader,
    task_scoped_mcp_config_for_task,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.mcp import server as mcp_server
from agents_remember.models.task_document_ref import TaskDocumentRef, TaskScopedReaderContext
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
                derived = task_scoped_mcp_config_for_task(base, ref, contract_path)
                self.assertIsNot(derived, base)
                self.assertEqual(derived.config_path, config_path)
                self.assertEqual(derived.coordination_root, coordinator)
                self.assertEqual(derived.workspace_root, workspace)
                self.assertEqual(set(derived.repositories), {"repo"})
                self.assertEqual(derived.repositories["repo"].path, code)
                self.assertEqual(derived.repositories["repo"].memory_root, memory)
                self.assertEqual(derived.repositories["repo"].contract_path, contract_path)
                reader_scope = TaskScopedReaderContext(
                    task_document_ref=ref,
                    contract_path=contract_path.as_posix(),
                )
                # A reader call with the leaf's context gets that same derived config; one
                # without a context is answered from the configured Projects roots, unchanged.
                self.assertEqual(
                    task_scoped_mcp_config_for_reader(
                        base, repository_id="repo", task_context=reader_scope
                    ),
                    derived,
                )
                self.assertIs(
                    task_scoped_mcp_config_for_reader(
                        base, repository_id="repo", task_context=None
                    ),
                    base,
                )
                with self.assertRaisesRegex(ValueError, "repo_id must match"):
                    task_scoped_mcp_config_for_reader(
                        base,
                        repository_id="different-repo",
                        task_context=reader_scope,
                    )
                self.assertEqual(base.workspace_root, root)
                self.assertEqual(base.repositories["repo"].path, root / "repo")

    def test_the_tool_server_has_no_scope_profile_start_option(self) -> None:
        """Nothing on this line produces a scope profile, so the server takes none (PNT-R08 4)."""

        refused = io.StringIO()
        with (
            contextlib.redirect_stderr(refused),
            patch.object(mcp_server, "load_config") as load_config,
            patch.object(mcp_server, "run_server") as run_server,
            self.assertRaises(SystemExit) as stopped,
        ):
            mcp_server.main(["--config", "/settings/mcp.json", "--scope-profile", "/tmp/p.json"])
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("unrecognized arguments: --scope-profile", refused.getvalue())
        load_config.assert_not_called()
        run_server.assert_not_called()


if __name__ == "__main__":
    unittest.main()
