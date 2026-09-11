"""Registered MCP proofs for actionable atomic-series admission and status evidence."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import anyio
from agents_remember.kernel.primitives.checkout_coordination import declare_test_process
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.mcp.server import create_server
from agents_remember.tasks import SubTaskRef, read_task_doc, write_task_doc
from agents_remember.worktrees.activation.atomic_series_activation import (
    activation_path,
    atomic_series_source_pair,
    publish_atomic_series_selection,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    lifecycle_operation_locator_path,
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    contract_publication_text,
    load_contract,
    write_contract,
)
from mcp.shared.memory import create_connected_server_and_client_session
from test_atomic_series_activation import ActivationFixture


class RegisteredActivationAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        declare_test_process()
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = ActivationFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _server(self):
        root = Path(self.temporary.name)
        workspace = root / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "repo-a").symlink_to(self.fixture.code, target_is_directory=True)
        (root / "settings.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "coordinationRoot": self.fixture.coord.as_posix(),
                    "workspaceRoot": workspace.as_posix(),
                    "repositories": {"repo-a": {"path": self.fixture.code.as_posix()}},
                    "providers": {},
                }
            ),
            encoding="utf-8",
        )
        return create_server(
            McpRuntimeConfig(
                config_path=root / "settings.json",
                coordination_root=self.fixture.coord,
                workspace_root=root / "workspace",
                transcript_root=self.fixture.coord / "logs" / "mcp",
                repositories={"repo-a": RepositoryScope(repo_id="repo-a", path=self.fixture.code)},
            )
        )

    @staticmethod
    async def _call(server, name: str, arguments: dict[str, object]):
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            return await client.call_tool(name, arguments)

    @staticmethod
    def _arguments(**values: object) -> dict[str, object]:
        return values

    @staticmethod
    def _text(result) -> str:
        return "\n".join(
            text
            for content in result.content
            if (text := getattr(content, "text", None)) is not None
        )

    def _assert_bounded_parser_refusal(self, result, contract, malformed_bytes) -> None:
        self.assertFalse(result.isError)
        assert result.structuredContent is not None
        payload = result.structuredContent
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["state"], "atomic-series-contract-unreadable")
        self.assertEqual(payload["status"], "atomic-series-contract-unreadable")
        self.assertEqual(payload["contract_path"], contract.contract_path.as_posix())
        self.assertIn("invalid contract kind:", payload["detail"])
        self.assertLessEqual(len(payload["detail"]), 8192)
        self.assertTrue(payload["detail"].endswith("\u2026 [detail truncated]"))

        admission = payload["admission"]
        self.assertEqual(admission["classification"], "corrective-action")
        self.assertEqual(admission["status"], "atomic-series-contract-unreadable")
        self.assertIn("invalid contract kind:", admission["detail"])
        self.assertLessEqual(len(admission["detail"]), 8192)
        self.assertTrue(admission["detail"].endswith("\u2026 [detail truncated]"))
        self.assertEqual(
            admission["requested"]["master"],
            {"repository": "repo-a", "path": "master-a/task.json"},
        )
        self.assertEqual(admission["requested"]["contractPath"], contract.contract_path.as_posix())
        self.assertEqual(admission["expected"]["kind"], "series")
        self.assertEqual(admission["observed"]["errorType"], "ContractError")
        self.assertIn("invalid contract kind:", admission["observed"]["detail"])
        self.assertLessEqual(len(admission["observed"]["detail"]), 8192)
        self.assertTrue(admission["observed"]["detail"].endswith("\u2026 [detail truncated]"))
        self.assertEqual(admission["statusAction"]["tool"], "worktree_status")
        self.assertEqual(
            admission["statusAction"]["args"]["contract_path"],
            contract.contract_path.as_posix(),
        )
        self.assertIn("before retrying", payload["retryPrecondition"])
        self.assertEqual(payload["nextTool"], "worktree_status")
        self.assertEqual(payload["nextArgs"]["repo_id"], "repo-a")
        self.assertEqual(payload["nextArgs"]["contract_path"], contract.contract_path.as_posix())
        self.assertIn("invalid contract kind:", self._text(result))
        self.assertIn("worktree_status", self._text(result))
        self.assertEqual(contract.contract_path.read_bytes(), malformed_bytes)

    def _publish_locator(self, contract) -> None:
        publish_new_lifecycle_operation_location(
            contract,
            contract_text=contract_publication_text(contract.contract_path, contract),
        )

    def _contract(self, name: str):
        # The configured-authority route derives an internal memory edge when the configured
        # code repository carries its ``ar-memory`` root. Keep this fixture local and untracked
        # while exercising the same series activation source pair.
        (self.fixture.code / "ar-memory").mkdir(exist_ok=True)
        internal_coordination = self.fixture.code / "ar-coordination"
        if not internal_coordination.exists():
            internal_coordination.symlink_to(self.fixture.coord, target_is_directory=True)
        contract = self.fixture.contract(name)
        configured = replace(contract, memory_mode="internal")
        lifecycle_operation_locator_path(
            configured.coordination_root, configured.contract_path
        ).unlink(missing_ok=True)
        shutil.rmtree(configured.worktree_group, ignore_errors=True)
        write_contract(configured.contract_path, configured)
        return configured

    def test_registered_status_projects_activation_without_mutation(self) -> None:
        contract = self._contract("master-a")
        self._publish_locator(contract)
        selected = publish_atomic_series_selection(contract, "active")
        before = selected.activation_path.read_bytes()

        result = anyio.run(
            self._call,
            self._server(),
            "worktree_status",
            self._arguments(repo_id="repo-a", contract_path=contract.contract_path.as_posix()),
        )

        self.assertFalse(result.isError)
        assert result.structuredContent is not None
        payload = result.structuredContent
        self.assertEqual(payload["atomicSeriesActivation"]["state"], "active")
        self.assertEqual(
            payload["atomicSeriesActivation"]["address"], selected.activation_path.as_posix()
        )
        self.assertGreaterEqual(payload["atomicSeriesActivation"]["record"]["revision"], 1)
        self.assertEqual(selected.activation_path.read_bytes(), before)

    def test_registered_sync_refusal_names_foreign_holder_and_read_status(self) -> None:
        contract_a = self._contract("master-a")
        contract_b = self._contract("master-b")
        self._publish_locator(contract_a)
        publish_atomic_series_selection(contract_b, "active")
        pair_path = activation_path(
            self.fixture.coord,
            atomic_series_source_pair(contract_a),
        )
        before = pair_path.read_bytes()

        result = anyio.run(
            self._call,
            self._server(),
            "worktree_sync",
            self._arguments(
                contract_path=contract_a.contract_path.as_posix(),
                resolution_action="continue",
            ),
        )

        self.assertFalse(result.isError)
        assert result.structuredContent is not None
        payload = result.structuredContent
        self.assertFalse(payload["ok"])
        admission = payload["admission"]
        self.assertEqual(admission["classification"], "wait")
        self.assertEqual(admission["blocking"]["master"]["path"], "master-b/task.json")
        self.assertEqual(admission["blocking"]["state"], "active")
        self.assertEqual(admission["statusAction"]["tool"], "worktree_status")
        self.assertEqual(admission["statusAction"]["args"]["repo_id"], "repo-a")
        self.assertEqual(
            admission["statusAction"]["args"]["contract_path"],
            contract_a.contract_path.as_posix(),
        )
        self.assertIn("does not prove that a live process exists", admission["retryPrecondition"])
        self.assertIn("master-b/task.json", self._text(result))
        self.assertIn("worktree_status", self._text(result))
        self.assertEqual(pair_path.read_bytes(), before)

    def test_registered_sync_refusal_distinguishes_vacant_and_unreadable(self) -> None:
        contract = self._contract("master-a")
        self._publish_locator(contract)
        pair = atomic_series_source_pair(contract)
        path = activation_path(self.fixture.coord, pair)
        server = self._server()

        vacant = anyio.run(
            self._call,
            server,
            "worktree_sync",
            self._arguments(
                contract_path=contract.contract_path.as_posix(),
                resolution_action="continue",
            ),
        )
        self.assertFalse(vacant.isError)
        assert vacant.structuredContent is not None
        vacant_payload = vacant.structuredContent
        self.assertEqual(vacant_payload["admission"]["classification"], "corrective-action")
        self.assertEqual(vacant_payload["admission"]["activation"]["observedState"], "vacant")
        self.assertIn("not sufficient to continue", vacant_payload["retryPrecondition"])
        self.assertIn("not sufficient to continue", self._text(vacant))

        path.write_text("{broken", encoding="utf-8")
        unreadable = anyio.run(
            self._call,
            server,
            "worktree_sync",
            self._arguments(
                contract_path=contract.contract_path.as_posix(),
                resolution_action="continue",
            ),
        )
        self.assertFalse(unreadable.isError)
        assert unreadable.structuredContent is not None
        unreadable_payload = unreadable.structuredContent
        self.assertEqual(
            unreadable_payload["admission"]["activation"]["observedState"], "unreadable"
        )
        self.assertEqual(
            unreadable_payload["admission"]["activation"]["errorType"],
            "ValidationError",
        )
        self.assertEqual(path.read_text(encoding="utf-8"), "{broken")

    def test_registered_status_and_sync_bound_oversized_unreadable_detail(self) -> None:
        contract = self._contract("master-a")
        self._publish_locator(contract)
        pair = atomic_series_source_pair(contract)
        path = activation_path(self.fixture.coord, pair)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({f"unknown_{index}": index for index in range(100)}),
            encoding="utf-8",
        )
        before = path.read_bytes()
        server = self._server()

        status = anyio.run(
            self._call,
            server,
            "worktree_status",
            self._arguments(repo_id="repo-a", contract_path=contract.contract_path.as_posix()),
        )

        self.assertFalse(status.isError)
        assert status.structuredContent is not None
        status_activation = status.structuredContent["atomicSeriesActivation"]
        self.assertEqual(status_activation["state"], "unreadable")
        self.assertEqual(status_activation["errorType"], "ValidationError")
        self.assertLessEqual(len(status_activation["detail"]), 8192)
        self.assertTrue(status_activation["detail"].endswith("\u2026 [detail truncated]"))
        self.assertIn(
            "validation errors for AtomicSeriesActivationRecord", status_activation["detail"]
        )

        sync = anyio.run(
            self._call,
            server,
            "worktree_sync",
            self._arguments(
                contract_path=contract.contract_path.as_posix(),
                resolution_action="continue",
            ),
        )

        self.assertFalse(sync.isError)
        assert sync.structuredContent is not None
        sync_payload = sync.structuredContent
        self.assertFalse(sync_payload["ok"])
        admission = sync_payload["admission"]
        self.assertEqual(admission["activation"]["observedState"], "unreadable")
        self.assertEqual(admission["activation"]["errorType"], "ValidationError")
        self.assertLessEqual(len(admission["activation"]["detail"]), 8192)
        self.assertTrue(admission["activation"]["detail"].endswith("\u2026 [detail truncated]"))
        self.assertEqual(admission["statusAction"]["tool"], "worktree_status")
        self.assertIn("ValidationError", self._text(sync))
        self.assertIn("worktree_status", self._text(sync))
        self.assertEqual(path.read_bytes(), before)

    def test_registered_start_refusal_reports_contract_edges(self) -> None:
        contract = self._contract("master-a")
        master_doc = read_task_doc(contract.task_root / "task.json")
        write_task_doc(
            contract.task_root,
            master_doc.model_copy(
                update={
                    "subTasks": [
                        SubTaskRef(
                            number="L1",
                            name="Leaf A",
                            file="leaf-a.md",
                            status="inProgress",
                        )
                    ]
                }
            ),
        )
        mismatched = replace(contract, code_source_branch="main")
        write_contract(mismatched.contract_path, mismatched)

        result = anyio.run(
            self._call,
            self._server(),
            "worktree_start",
            self._arguments(
                repo_id="repo-a",
                task_name="master-a",
                worktree_name="leaf-a",
                leaf_id="L1",
                parent_task="sprint",
                dry_run=True,
            ),
        )

        self.assertFalse(result.isError)
        assert result.structuredContent is not None
        payload = result.structuredContent
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "atomic-series-contract-edge-mismatch")
        self.assertEqual(
            payload["admission"]["expected"]["branchEdge"]["codeSourceBranch"], "super"
        )
        self.assertEqual(payload["admission"]["observed"]["branchEdge"]["codeSourceBranch"], "main")
        self.assertEqual(payload["nextTool"], "worktree_status")
        self.assertEqual(payload["nextArgs"]["repo_id"], "repo-a")
        self.assertEqual(payload["nextArgs"]["contract_path"], contract.contract_path.as_posix())
        self.assertIn("declared edge", self._text(result))

    def test_registered_start_reread_refusal_preserves_edges_and_guidance(self) -> None:
        contract = self._contract("master-a")
        master_doc = read_task_doc(contract.task_root / "task.json")
        write_task_doc(
            contract.task_root,
            master_doc.model_copy(
                update={
                    "subTasks": [
                        SubTaskRef(
                            number="L1",
                            name="Leaf A",
                            file="leaf-a.md",
                            status="inProgress",
                        )
                    ]
                }
            ),
        )

        def drift_after_fetch(_contract):
            write_contract(contract.contract_path, replace(contract, code_source_branch="main"))
            return {}

        with patch(
            "agents_remember.worktrees.modules.startup.start_contract.fetch_source_upstreams",
            side_effect=drift_after_fetch,
        ):
            result = anyio.run(
                self._call,
                self._server(),
                "worktree_start",
                self._arguments(
                    repo_id="repo-a",
                    task_name="master-a",
                    worktree_name="leaf-a",
                    leaf_id="L1",
                    parent_task="sprint",
                ),
            )

        self.assertFalse(result.isError)
        assert result.structuredContent is not None
        payload = result.structuredContent
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "atomic-series-contract-edge-mismatch")
        self.assertEqual(
            payload["admission"]["expected"]["branchEdge"]["codeSourceBranch"], "super"
        )
        self.assertEqual(payload["admission"]["observed"]["branchEdge"]["codeSourceBranch"], "main")
        self.assertEqual(payload["nextTool"], "worktree_status")
        self.assertEqual(payload["nextArgs"]["repo_id"], "repo-a")
        self.assertEqual(payload["nextArgs"]["contract_path"], contract.contract_path.as_posix())
        self.assertIn("declared edge", self._text(result))

    def test_registered_start_unreadable_contract_keeps_parser_reason(self) -> None:
        contract = self._contract("master-a")
        master_doc = read_task_doc(contract.task_root / "task.json")
        write_task_doc(
            contract.task_root,
            master_doc.model_copy(
                update={
                    "subTasks": [
                        SubTaskRef(
                            number="L1",
                            name="Leaf A",
                            file="leaf-a.md",
                            status="inProgress",
                        )
                    ]
                }
            ),
        )
        contract.contract_path.write_text("{broken", encoding="utf-8")
        server = self._server()

        result = anyio.run(
            self._call,
            server,
            "worktree_start",
            self._arguments(
                repo_id="repo-a",
                task_name="master-a",
                worktree_name="leaf-a",
                leaf_id="L1",
                parent_task="sprint",
                dry_run=True,
            ),
        )

        self.assertFalse(result.isError)
        assert result.structuredContent is not None
        payload = result.structuredContent
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "atomic-series-contract-unreadable")
        self.assertIn("worktree contract must start with a front matter block", payload["detail"])
        self.assertIn(
            "worktree contract must start with a front matter block",
            payload["admission"]["observed"]["detail"],
        )
        self.assertEqual(payload["nextTool"], "worktree_status")
        self.assertIn("worktree_status", self._text(result))

        write_contract(contract.contract_path, contract)
        original = contract.contract_path.read_text(encoding="utf-8")
        malformed = original.replace("kind: series", "kind: " + "x" * 9000, 1)
        contract.contract_path.write_text(malformed, encoding="utf-8")
        malformed_bytes = contract.contract_path.read_bytes()
        with self.assertRaises(ContractError) as parser_error:
            load_contract(contract.contract_path)
        self.assertEqual(len(str(parser_error.exception)), 9099)
        self.assertTrue(str(parser_error.exception).startswith("invalid contract kind:"))

        oversized = anyio.run(
            self._call,
            server,
            "worktree_start",
            self._arguments(
                repo_id="repo-a",
                task_name="master-a",
                worktree_name="leaf-a",
                leaf_id="L1",
                parent_task="sprint",
                dry_run=True,
            ),
        )

        self._assert_bounded_parser_refusal(oversized, contract, malformed_bytes)


if __name__ == "__main__":
    unittest.main()
