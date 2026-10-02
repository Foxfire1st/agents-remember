from __future__ import annotations

import hashlib
import json
import stat
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agents_remember.application.orca_task_context import OrcaRoleContext, selection_binding
from agents_remember.application.skill_resources.provider import shipped_composition_corpus
from agents_remember.cli import (
    orca_task_liveness,
    orca_task_preparation,
    orca_task_receipts,
    orca_task_routes,
)
from agents_remember.cli.orca_task_preparation import (
    ROLE_START_OPERATIONS,
    _bind_task_report_access,
)
from agents_remember.kernel.primitives.paseo_runtime_settings import parse_paseo_runtime_settings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.orca_launcher import OrcaDispatchRequest
from agents_remember.models.role_capsules.manifest import parse_composition_manifest
from agents_remember.models.task_document_ref import TaskDocumentRef
from fastapi import HTTPException
from fastapi.responses import JSONResponse


def _runtime_config(root: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings" / "ar.json",
        coordination_root=root / "coordination",
        workspace_root=root / "projects",
        transcript_root=root / "coordination" / "logs" / "mcp",
        paseo_runtime=parse_paseo_runtime_settings(
            {
                "installPrefix": (root / "paseo" / "prefix").as_posix(),
                "home": (root / "paseo" / "home").as_posix(),
                "listen": "127.0.0.1:6835",
                "version": "0.11.0-beta.2",
                "providers": {},
                "embed": [],
            }
        ),
    )


class TaskReportAccessTests(unittest.TestCase):
    def test_report_link_writes_to_canonical_task_reports(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "leaf-enclosure"
            workspace.mkdir()
            task_reports = root / "task" / "notes" / "reports"
            task_reports.mkdir(parents=True)

            access = _bind_task_report_access(workspace, task_reports)
            report = access / "orca-native" / "worker-report.md"
            report.parent.mkdir()
            report.write_text("native worker report\n", encoding="utf-8")

            self.assertTrue(access.is_symlink())
            self.assertEqual(report.resolve().parent, task_reports / "orca-native")
            self.assertEqual(
                (task_reports / "orca-native" / "worker-report.md").read_text(encoding="utf-8"),
                "native worker report\n",
            )
            self.assertEqual(_bind_task_report_access(workspace, task_reports), access)

            access.unlink()
            access.mkdir()
            with self.assertRaisesRegex(ValueError, "non-link"):
                _bind_task_report_access(workspace, task_reports)


class MessageBindingProjectionTests(unittest.TestCase):
    def test_projection_reuses_identical_request_content_and_refuses_replacement(self) -> None:
        with TemporaryDirectory() as temporary:
            config = _runtime_config(Path(temporary))
            request_id = uuid.uuid4()
            binding = {
                "requestId": str(request_id),
                "role": "worker",
                "operation": "implementation",
                "selection": {"taskDocumentRef": {"repository": "repo", "path": "task/leaf.json"}},
                "taskDocumentDigest": "task-digest",
                "taskReportPath": "/coordination/task/notes/reports/worker.md",
                "capsuleDigest": "capsule-digest",
            }
            reference = orca_task_receipts._message_binding_projection_reference(
                config, request_id, binding
            )
            written = orca_task_receipts._write_message_binding_projection(
                config, request_id, binding, reference
            )
            path = Path(reference["path"])
            exact_bytes = path.read_bytes()

            self.assertEqual(written, reference)
            self.assertEqual(json.loads(exact_bytes), binding)
            self.assertEqual(hashlib.sha256(exact_bytes).hexdigest(), reference["sha256"])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(
                orca_task_receipts._write_message_binding_projection(
                    config, request_id, binding, reference
                ),
                reference,
            )
            self.assertEqual(path.read_bytes(), exact_bytes)
            orca_task_receipts._verify_message_binding_projection(
                config, request_id, binding, reference
            )

            changed_binding = {**binding, "taskReportPath": "/other/request.md"}
            changed_reference = orca_task_receipts._message_binding_projection_reference(
                config, request_id, changed_binding
            )
            with self.assertRaisesRegex(ValueError, "different immutable message-binding"):
                orca_task_receipts._write_message_binding_projection(
                    config, request_id, changed_binding, changed_reference
                )
            with self.assertRaisesRegex(ValueError, "different message content"):
                orca_task_receipts._verify_message_binding_projection(
                    config, request_id, changed_binding, reference
                )
            self.assertEqual(path.read_bytes(), exact_bytes)

    def test_launch_receipt_references_projection_before_native_launch(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = _runtime_config(root)
            request_id = uuid.uuid4()
            request = OrcaDispatchRequest(role="architect", requestId=request_id)
            context = OrcaRoleContext(
                role="architect", sprint=None, master=None, task=None, effective_task=None
            )
            selection = selection_binding(request)
            binding = {
                "requestId": str(request_id),
                "role": "architect",
                "operation": "planning",
                "selection": selection,
                "taskDocumentDigest": "task-document-digest",
                "taskReportPath": (root / "report.md").as_posix(),
                "capsuleDigest": "capsule-digest",
            }
            reference = orca_task_receipts._message_binding_projection_reference(
                config, request_id, binding
            )
            prepared = {
                "prompt": "prepared prompt",
                "capsuleOperation": "planning",
                "capsuleDigest": "capsule-digest",
                "taskDocumentDigest": "task-document-digest",
                "taskReportPath": (root / "report.md").as_posix(),
                "canonicalTaskReportPath": (root / "report.md").as_posix(),
                "messageBindingProjection": {
                    "requestId": str(request_id),
                    "binding": binding,
                    **reference,
                },
            }
            receipt_path = orca_task_receipts._receipt_path(config, request, request_id)
            workspace = {"path": root.as_posix()}

            def execute(
                _config: McpRuntimeConfig,
                path: Path,
                receipt: dict[str, object],
            ) -> JSONResponse:
                projection_path = Path(reference["path"])
                self.assertEqual(json.loads(projection_path.read_text(encoding="utf-8")), binding)
                self.assertEqual(
                    receipt["messageBindingProjection"],
                    reference,
                )
                stored_receipt = orca_task_receipts._read_receipt(path)
                assert stored_receipt is not None
                self.assertEqual(stored_receipt["messageBindingProjection"], reference)
                return JSONResponse({"status": "running"})

            def dispatch() -> JSONResponse:
                with (
                    patch.object(
                        orca_task_routes, "resolve_orca_role_context", return_value=context
                    ),
                    patch.object(
                        orca_task_routes, "_request_digest", return_value="request-digest"
                    ),
                    patch.object(orca_task_routes, "_migrate_taskless_legacy_receipt"),
                    patch.object(
                        orca_task_preparation, "_resolve_workspace", return_value=workspace
                    ),
                    patch.object(
                        orca_task_preparation,
                        "_role_defaults",
                        return_value=(
                            {"agent": "codex", "model": None, "effort": None},
                            ("codex",),
                        ),
                    ),
                    patch.object(
                        orca_task_preparation,
                        "_resolve_agent_selection",
                        return_value=("codex", {"model": "gpt-5.6-luna"}, ()),
                    ),
                    patch.object(orca_task_preparation, "_compile_handover", return_value=prepared),
                    patch.object(
                        orca_task_routes, "provider_accepts_tool_servers", return_value=True
                    ),
                    patch.object(orca_task_routes, "_receipt_path", return_value=receipt_path),
                    patch.object(orca_task_routes, "_execute_prepared_launch", side_effect=execute),
                ):
                    return orca_task_routes._orca_dispatch_endpoint(config, request)

            response = dispatch()

            self.assertEqual(response.status_code, 200)
            exact_bytes = Path(reference["path"]).read_bytes()
            changed_binding = {**binding, "taskReportPath": "/other/request.md"}
            changed_reference = orca_task_receipts._message_binding_projection_reference(
                config, request_id, changed_binding
            )
            prepared["messageBindingProjection"] = {
                "requestId": str(request_id),
                "binding": changed_binding,
                **changed_reference,
            }
            # The write-once rule is reached by a request that has its binding file but no
            # receipt, as an attempt leaves it that ended before its receipt. A request with a
            # receipt is answered from that receipt and is not compiled again.
            receipt_path.unlink()
            with self.assertRaises(HTTPException) as raised:
                dispatch()
            self.assertEqual(raised.exception.status_code, 409)
            self.assertIn("different immutable message-binding", str(raised.exception.detail))
            self.assertEqual(Path(reference["path"]).read_bytes(), exact_bytes)
            self.assertFalse(receipt_path.exists())


class OrcaNativeResultTests(unittest.TestCase):
    def test_manual_role_operations_are_admitted_by_the_shipped_manifest(self) -> None:
        with shipped_composition_corpus() as (root, manifest):
            parsed = parse_composition_manifest((root / manifest).read_bytes())

        self.assertEqual(
            set(ROLE_START_OPERATIONS),
            {
                "architect",
                "system-specialist",
                "orchestrator",
                "manager",
                "worker",
                "reviewer",
                "curator",
            },
        )
        expected_altitudes = {
            "architect": "sprint",
            "system-specialist": "sprint",
            "orchestrator": "sprint",
            "manager": "master",
            "worker": "leaf",
            "reviewer": "leaf | master | sprint, by seam",
            "curator": "leaf",
        }
        self.assertEqual(
            {role: parsed.roles[role].altitude for role in ROLE_START_OPERATIONS},
            expected_altitudes,
        )
        self.assertTrue(
            {
                "worktree_start",
                "worktree_status",
                "worktree_sync",
                "worktree_closeout_apply",
                "worktree_closeout_preview",
            }
            <= set(parsed.roles["architect"].tools)
        )
        for role, operation in ROLE_START_OPERATIONS.items():
            with self.subTest(role=role, operation=operation):
                self.assertIn(operation, parsed.roles[role].operations)
                self.assertIn(role, parsed.operations[operation].applies_to_roles)

    def test_public_execution_retains_the_selected_capsule_operation(self) -> None:
        public = orca_task_receipts._public_execution(
            {
                "requestId": str(uuid.uuid4()),
                "role": "worker",
                "status": "starting",
                "capsuleOperation": "implementation",
            }
        )

        self.assertEqual(public["capsuleOperation"], "implementation")

    def test_taskless_can_start_means_fresh_request_while_bound_live_seat_remains_singleton(
        self,
    ) -> None:
        for status in ("running", "stopped", "completed", "failed", "rejected", "interrupted"):
            with self.subTest(role="architect", status=status):
                self.assertTrue(
                    orca_task_receipts._public_execution({"role": "architect", "status": status})[
                        "canStart"
                    ]
                )
        for status in ("starting", "unknown"):
            with self.subTest(role="system-specialist", status=status):
                self.assertFalse(
                    orca_task_receipts._public_execution(
                        {"role": "system-specialist", "status": status}
                    )["canStart"]
                )
        self.assertFalse(
            orca_task_receipts._public_execution({"role": "worker", "status": "running"})[
                "canStart"
            ]
        )

    def test_public_execution_whitelists_the_host_fields_and_keeps_the_saved_call_private(
        self,
    ) -> None:
        host = {
            "kind": "paseo-agent",
            "serverId": "srv_owned",
            "workspaceId": "wks_owned",
            "agentId": "8a6f0d3e-5f55-4c0b-9d53-6f0f1f6f2a10",
        }
        public = orca_task_receipts._public_execution(
            {
                "role": "worker",
                "status": "unknown",
                "agentId": host["agentId"],
                "hostAgentExists": True,
                "pendingArchiveAgentId": "an-older-agent",
                "execution": {**host, "internalHandle": "must-not-escape"},
                "replayRequest": {"agent": {"agentId": host["agentId"], "prompt": "first message"}},
            }
        )

        self.assertEqual(public["execution"], host)
        self.assertEqual(
            set(public), {"role", "status", "execution", "canStart", "canRetry", "canRevive"}
        )
        # Retry needs the saved call: without it there is nothing to repeat.
        self.assertEqual((public["canStart"], public["canRetry"]), (False, True))
        for status in ("starting", "unknown"):
            without_call = orca_task_receipts._public_execution(
                {"role": "worker", "status": status}
            )
            self.assertEqual((without_call["canStart"], without_call["canRetry"]), (False, False))

    def test_report_availability_is_projected_independently_of_session_status(self) -> None:
        with TemporaryDirectory() as temporary:
            report = Path(temporary) / "task-report.md"
            receipt = {
                "status": "running",
                "report": {"path": str(report), "canonicalPath": str(report)},
            }
            self.assertFalse(orca_task_receipts._public_execution(receipt)["report"]["available"])
            report.write_text("bounded report\n", encoding="utf-8")
            public = orca_task_receipts._public_execution(receipt)
            self.assertEqual(public["status"], "running")
            self.assertTrue(public["report"]["available"])


class OrcaProjectDispatchTests(unittest.TestCase):
    def test_projects_architect_dispatch_remains_available(self) -> None:
        with patch.object(
            orca_task_routes,
            "_start_execution",
            return_value=JSONResponse({"status": "running"}),
        ) as start:
            response = orca_task_routes._orca_dispatch_endpoint(
                _runtime_config(Path("/tmp/orca-projects-test")),
                OrcaDispatchRequest(role="architect", requestId=uuid.uuid4()),
            )
        self.assertEqual(response.status_code, 200)
        start.assert_called_once()


class TasklessExecutionIdentityTests(unittest.TestCase):
    def test_request_address_is_exact_for_taskless_revive_and_unchanged_for_bound_revive(
        self,
    ) -> None:
        taskless_id = uuid.uuid4()
        taskless = OrcaDispatchRequest(role="architect", requestId=taskless_id, action="revive")
        taskless_receipt = {
            "requestId": str(taskless_id),
            "selection": selection_binding(taskless),
        }
        self.assertTrue(orca_task_receipts._receipt_address_matches(taskless_receipt, taskless))
        self.assertFalse(
            orca_task_receipts._receipt_address_matches(
                taskless_receipt,
                OrcaDispatchRequest(role="architect", requestId=uuid.uuid4(), action="revive"),
            )
        )

        bound = OrcaDispatchRequest(
            role="worker",
            requestId=uuid.uuid4(),
            action="revive",
            sprintDocumentRef=TaskDocumentRef(repository="repo", path="sprint.json"),
            masterDocumentRef=TaskDocumentRef(repository="repo", path="master.json"),
            taskDocumentRef=TaskDocumentRef(repository="repo", path="master/leaf.json"),
        )
        bound_receipt = {
            "requestId": str(uuid.uuid4()),
            "selection": selection_binding(bound),
        }
        self.assertTrue(orca_task_receipts._receipt_address_matches(bound_receipt, bound))

    def test_same_request_replays_but_second_intent_gets_a_distinct_receipt_and_report(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = _runtime_config(root)
            config.coordination_root.mkdir()
            first_id = uuid.uuid4()
            second_id = uuid.uuid4()
            first = OrcaDispatchRequest(role="architect", requestId=first_id)
            second = OrcaDispatchRequest(role="architect", requestId=second_id)

            first_path = orca_task_receipts._receipt_path(config, first, first_id)
            self.assertEqual(first_path, orca_task_receipts._receipt_path(config, first, first_id))
            self.assertNotEqual(
                first_path, orca_task_receipts._receipt_path(config, second, second_id)
            )
            workspace = {"path": (root / "projects").as_posix()}
            context = OrcaRoleContext(
                role="architect", sprint=None, master=None, task=None, effective_task=None
            )
            first_report = orca_task_preparation._role_report_path(
                context, workspace, request_id=first_id
            )
            second_report = orca_task_preparation._role_report_path(
                context, workspace, request_id=second_id
            )
            self.assertNotEqual(first_report, second_report)

            receipt = {
                "schema": "ar-orca-native-execution/v1",
                "requestId": str(first_id),
                "role": "architect",
                "selection": selection_binding(first),
                "requestDigest": "same-payload",
                "status": "unknown",
                "agentId": "owned-agent",
                "replayRequest": {
                    "workspace": {"cwd": workspace["path"]},
                    "agent": {"agentId": "owned-agent", "prompt": "saved"},
                },
            }
            orca_task_receipts._write_receipt(first_path, receipt)
            with patch.object(
                orca_task_liveness,
                "_execute_prepared_launch",
                return_value=JSONResponse({"status": "running"}),
            ) as replay:
                same_request = orca_task_liveness._reconcile_prior_execution(
                    config, first_path, first, "same-payload"
                )
                second_intent = orca_task_liveness._reconcile_prior_execution(
                    config,
                    orca_task_receipts._receipt_path(config, second, second_id),
                    second,
                    "same-payload",
                )

            self.assertIsNotNone(same_request)
            self.assertEqual(replay.call_count, 1)
            self.assertEqual(
                replay.call_args.args[2]["replayRequest"]["agent"]["agentId"], "owned-agent"
            )
            self.assertIsNone(second_intent)

    def test_one_time_migration_moves_only_matching_taskless_history_ids(self) -> None:
        with TemporaryDirectory() as temporary:
            config = _runtime_config(Path(temporary))
            config.coordination_root.mkdir()
            current_id, archived_id, foreign_id = (uuid.uuid4() for _ in range(3))
            selection = OrcaDispatchRequest(role="architect", requestId=current_id)
            legacy = orca_task_receipts._legacy_receipt_path(config, selection)
            history = legacy.parent / "history"

            def receipt(request_id: uuid.UUID, role: str = "architect", selection_binding=None):
                return {
                    "schema": "ar-orca-native-execution/v1",
                    "requestId": str(request_id),
                    "role": role,
                    "selection": selection_binding
                    or {
                        "role": role,
                        "sprintDocumentRef": None,
                        "masterDocumentRef": None,
                        "taskDocumentRef": None,
                    },
                    "status": "running",
                    "createdAt": str(request_id),
                }

            orca_task_receipts._write_receipt(legacy, receipt(current_id))
            orca_task_receipts._write_receipt(history / f"{archived_id}.json", receipt(archived_id))
            unrelated = receipt(
                foreign_id,
                selection_binding={
                    "role": "architect",
                    "sprintDocumentRef": {"repository": "other", "path": "master.json"},
                    "masterDocumentRef": None,
                    "taskDocumentRef": None,
                },
            )
            orca_task_receipts._write_receipt(history / f"{foreign_id}.json", unrelated)
            system_id = uuid.uuid4()
            orca_task_receipts._write_receipt(
                history / f"{system_id}.json",
                receipt(system_id, role="system-specialist"),
            )

            orca_task_receipts._migrate_taskless_legacy_receipt(config, selection)
            rows = orca_task_receipts._taskless_execution_receipts(config, selection)

            self.assertEqual(
                {row[1]["requestId"] for row in rows}, {str(current_id), str(archived_id)}
            )
            self.assertFalse(legacy.exists())
            self.assertFalse((history / f"{archived_id}.json").exists())
            self.assertTrue((history / f"{foreign_id}.json").is_file())
            self.assertTrue((history / f"{system_id}.json").is_file())


if __name__ == "__main__":
    unittest.main()
