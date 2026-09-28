from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from typing import cast
from unittest.mock import patch

from agents_remember.cli.orca_handover_artifacts import (
    MAX_HANDOVER_ARTIFACT_BYTES,
    read_role_handover_artifact,
    write_role_handover_artifact,
)
from agents_remember.cli.orca_runtime import OrcaRuntimeFailure
from agents_remember.cli.orca_task_routes import NativeRoleSessionPreparation
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, OrcaRuntimeSettings
from agents_remember.mcp.registration.orca_roles import register_orca_role_tools
from agents_remember.mcp.tools.orca_handover import orca_role_prepare_payload
from agents_remember.models.orca_launcher import OrcaDispatchRequest
from agents_remember.models.task_document_ref import TaskDocumentRef
from mcp.server.fastmcp import FastMCP


class OrcaRolePrepareTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = McpRuntimeConfig(
            config_path=Path("/test/ar/mcp-settings.json"),
            coordination_root=Path("/test/ar/coordination"),
            workspace_root=Path("/test/ar/projects"),
            transcript_root=Path("/test/ar/coordination/logs/mcp"),
            orca_runtime=OrcaRuntimeSettings(
                runtime_root=Path("/test/orca/source"),
                user_data_path=Path("/test/orca/profile"),
            ),
        )
        self.task_ref = TaskDocumentRef(
            repository="agents-remember",
            path="260922_orca-native-workspace-trial/10_flat-native-orchestration.json",
        )
        self.request = OrcaDispatchRequest.model_validate(
            {
                "role": "worker",
                "sprintDocumentRef": TaskDocumentRef(
                    repository="agents-remember",
                    path="260922_orca-native-workspace-trial/task.json",
                ),
                "masterDocumentRef": TaskDocumentRef(
                    repository="agents-remember",
                    path="260922_orca-native-workspace-trial/task.json",
                ),
                "taskDocumentRef": self.task_ref,
                "requestId": uuid.uuid4(),
            }
        )
        self.report_path = (
            "/coordination/tasks/agents-remember/example/notes/reports/orca-native/worker.md"
        )
        self.artifact = {
            "schema": "ar-orca-prepared-role-handover/v1",
            "requestId": str(self.request.request_id),
            "prompt": "full prompt kept in the artifact",
            "handover": {
                "role": "worker",
                "selection": {
                    "taskDocumentRef": self.task_ref.model_dump(mode="json"),
                },
                "taskDocumentDigest": "task-digest",
                "capsule": {"semanticDigest": "capsule-digest"},
                "taskReportPath": self.report_path,
                "workspace": {
                    "selector": "id:paired-leaf",
                    "path": "/work/paired-leaf",
                    "codeRoot": "/work/paired-leaf/code",
                    "memoryRoot": "/work/paired-leaf/memory",
                    "contractPath": "/coordination/tasks/agents-remember/example/series-contract.md",
                },
                "documents": [
                    {
                        "taskDocumentRef": self.task_ref.model_dump(mode="json"),
                        "taskDocReadArgs": {
                            "repo_id": "agents-remember",
                            "operation": "get",
                            "task_name": "260922_orca-native-workspace-trial",
                            "slug": "10_flat-native-orchestration",
                        },
                        "contentDigest": "task-doc-content-digest",
                    }
                ],
            },
        }
        self.handover_reference = {"path": "/reports/worker.handover.json", "sha256": "sha"}

    def test_tool_returns_compact_handover_and_exact_idle_native_next_action(self) -> None:
        execution = {
            "status": "running",
            "detail": "Orca accepted the session.",
            "execution": {
                "kind": "structured",
                "handle": "native-terminal-17",
                "sessionId": "native-session-17",
                "worktreeId": "native-worktree-4",
            },
        }
        prepared = NativeRoleSessionPreparation(
            execution=execution,
            request_id=self.request.request_id,
            handover_reference=self.handover_reference,
            handover_artifact=self.artifact,
        )
        with (
            patch(
                "agents_remember.mcp.tools.orca_handover.prepare_idle_native_role_session",
                return_value=prepared,
            ),
        ):
            result = orca_role_prepare_payload(self.config, self.request)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "idle-session-ready")
        self.assertFalse(result["workStarted"])
        self.assertEqual(result["nativeIdentity"]["handle"], "native-terminal-17")
        self.assertEqual(result["nativeIdentity"]["sessionId"], "native-session-17")
        action = result["nativeNextAction"]
        self.assertEqual(action["runCreateOperation"], "orca orchestration run-create")
        worker_start = action["workerStart"]
        self.assertEqual(worker_start["workspaceSelector"], "id:paired-leaf")
        self.assertEqual(worker_start["terminalHandle"], "native-terminal-17")
        spec = json.loads(worker_start["spec"])
        self.assertEqual(spec["candidate"]["class"], "working-tree-diff")
        self.assertEqual(
            spec["candidate"]["baselineContractPath"],
            self.artifact["handover"]["workspace"]["contractPath"],
        )
        self.assertEqual(
            spec["arAssignment"]["taskDocumentReadArgs"]["slug"],
            "10_flat-native-orchestration",
        )
        self.assertEqual(spec["handoverArtifact"], self.handover_reference)
        self.assertNotIn("full prompt kept in the artifact", json.dumps(result))

    def test_missing_shared_orca_settings_fail_before_native_start(self) -> None:
        config = McpRuntimeConfig(
            config_path=self.config.config_path,
            coordination_root=self.config.coordination_root,
            workspace_root=self.config.workspace_root,
            transcript_root=self.config.transcript_root,
        )
        with (
            patch(
                "agents_remember.mcp.tools.orca_handover.prepare_idle_native_role_session"
            ) as start,
            self.assertRaises(OrcaRuntimeFailure) as raised,
        ):
            orca_role_prepare_payload(config, self.request)
        start.assert_not_called()
        self.assertIn("orcaRuntime.runtimeRoot and orcaRuntime.userDataPath", str(raised.exception))
        self.assertIn("Data-only MCP tools remain available", str(raised.exception))

    def test_task_handover_artifact_is_bounded_immutable_and_report_confined(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report_path = str(Path(temporary) / "notes" / "reports" / "worker.md")
            small = {
                "schema": "ar-orca-prepared-role-handover/v1",
                "prompt": "x" * 128,
            }
            ref = write_role_handover_artifact(report_path, small)
            self.assertEqual(Path(ref["path"]), Path(report_path).with_suffix(".handover.json"))
            self.assertEqual(read_role_handover_artifact(report_path, ref), small)
            self.assertEqual(write_role_handover_artifact(report_path, small), ref)
            with self.assertRaisesRegex(ValueError, "different handover content"):
                write_role_handover_artifact(report_path, {"prompt": "different"})

            larger_path = str(Path(temporary) / "notes" / "reports" / "reviewer.md")
            larger = {
                "schema": "ar-orca-prepared-role-handover/v1",
                "prompt": "y" * 64_000,
            }
            larger_ref = write_role_handover_artifact(larger_path, larger)
            self.assertEqual(read_role_handover_artifact(larger_path, larger_ref), larger)
            oversized = {
                "schema": "ar-orca-prepared-role-handover/v1",
                "prompt": "z" * MAX_HANDOVER_ARTIFACT_BYTES,
            }
            with self.assertRaisesRegex(ValueError, "size limit"):
                write_role_handover_artifact(
                    str(Path(temporary) / "notes" / "reports" / "curator.md"), oversized
                )
            self.assertEqual(
                hashlib.sha256(Path(ref["path"]).read_bytes()).hexdigest(), ref["sha256"]
            )

    def test_registered_tool_offloads_native_preparation_from_mcp_event_loop(self) -> None:
        preparation_threads: list[int] = []

        def sync_preparation(
            _config: object, request: OrcaDispatchRequest
        ) -> NativeRoleSessionPreparation:
            preparation_threads.append(threading.get_ident())
            asyncio.run(asyncio.sleep(0))
            task_document_ref = request.task_document_ref
            assert task_document_ref is not None
            artifact = {
                **self.artifact,
                "requestId": str(request.request_id),
                "handover": {
                    **self.artifact["handover"],
                    "role": request.role,
                    "selection": {
                        **self.artifact["handover"]["selection"],
                        "taskDocumentRef": task_document_ref.model_dump(mode="json"),
                    },
                },
            }
            return NativeRoleSessionPreparation(
                execution={
                    "status": "running",
                    "detail": "Orca accepted the session.",
                    "execution": {
                        "kind": "structured",
                        "handle": "native-terminal-event-loop-test",
                        "sessionId": "native-session-event-loop-test",
                        "worktreeId": "native-worktree-event-loop-test",
                    },
                },
                request_id=request.request_id,
                handover_reference=self.handover_reference,
                handover_artifact=artifact,
            )

        async def call_registered_tool() -> tuple[int, dict[str, object]]:
            server = FastMCP("orca-role-preparation-event-loop-test")
            register_orca_role_tools(server, self.config)
            loop_thread = threading.get_ident()
            _content, structured = await server.call_tool(
                "orca_role_prepare",
                {
                    "role": "worker",
                    "sprint_document_ref": {
                        "repository": "agents-remember",
                        "path": "260922_orca-native-workspace-trial/task.json",
                    },
                    "master_document_ref": {
                        "repository": "agents-remember",
                        "path": "260922_orca-native-workspace-trial/task.json",
                    },
                    "task_document_ref": {
                        "repository": "agents-remember",
                        "path": "260922_orca-native-workspace-trial/10_flat-native-orchestration.json",
                    },
                },
            )
            return loop_thread, cast(dict[str, object], structured)

        with (
            patch(
                "agents_remember.mcp.tools.orca_handover.prepare_idle_native_role_session",
                side_effect=sync_preparation,
            ),
        ):
            loop_thread, structured = asyncio.run(call_registered_tool())

        self.assertTrue(structured["ok"], structured)
        native_identity = cast(dict[str, object], structured["nativeIdentity"])
        self.assertEqual(native_identity["handle"], "native-terminal-event-loop-test")
        self.assertEqual(len(preparation_threads), 1)
        self.assertNotEqual(preparation_threads[0], loop_thread)


if __name__ == "__main__":
    unittest.main()
