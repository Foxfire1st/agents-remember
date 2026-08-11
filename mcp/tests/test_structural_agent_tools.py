"""L19 structural agent operations: relationship routing without runtime-id cognition."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application.structural.agent_tools import (
    StructuralAgentRuntime,
    dispatch_agent_tool,
    message_child_tool,
    message_parent_tool,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.kernel.primitives.observer_paths import observer_root
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.structural.agent import (
    DispatchAgentRequest,
    StructuralMessageRequest,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.serving.inbox_delivery import target_session_for_entry
from agents_remember.serving.structural_seats import StructuralSeatError, StructuralSeatResolver
from agents_remember.serving.terminal_catalog import TerminalCatalog, terminal_catalog_path
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentTopology


class _Host:
    def has_session(self, _tmux_name: str) -> bool:
        return False


def _config(root: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root,
        transcript_root=root / "logs" / "mcp",
    )


def _task_doc(**values: object) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": values.pop("id"),
            "slug": values.pop("slug"),
            "title": values.pop("title"),
            "kind": values.pop("kind"),
            "repo": "repo",
            "createdAt": "2026-08-11T00:00",
            **values,
        }
    )


def _write_topology(root: Path) -> tuple[TaskDocumentRef, TaskDocumentRef, TaskDocumentRef]:
    task_root = root / "tasks" / "repo"
    write_task_doc(
        task_root / "sprint",
        _task_doc(
            id="SPRINT",
            slug="sprint",
            title="Sprint",
            kind="master",
            orchestrates=["master"],
        ),
    )
    write_task_doc(
        task_root / "master",
        _task_doc(
            id="MASTER",
            slug="master",
            title="Master",
            kind="master",
            subTasks=[
                {
                    "number": "leaf-1",
                    "name": "Leaf 1",
                    "file": "leaf-1.md",
                    "status": "inProgress",
                }
            ],
        ),
    )
    write_task_doc(
        task_root / "master",
        _task_doc(
            id="leaf-1",
            slug="leaf-1",
            title="Leaf 1",
            kind="subTask",
            master="task.md",
        ),
    )
    return (
        TaskDocumentRef(repository="repo", path="sprint/task.json"),
        TaskDocumentRef(repository="repo", path="master/task.json"),
        TaskDocumentRef(repository="repo", path="master/leaf-1.json"),
    )


def _seat(
    session_id: str,
    document: TaskDocumentRef,
    role: str,
    *,
    status: str = "running",
) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=session_id,
        label=session_id,
        kind="harness",
        harness="codex",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=("codex",),
        created_at="2026-08-11T00:00:00+00:00",
        last_attached_at="2026-08-11T00:00:00+00:00",
        status=status,  # type: ignore[arg-type]
        task_document_ref=document,
        seat_role=role,
    )


class StructuralAgentToolTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.sprint, self.master, self.leaf = _write_topology(self.root)
        self.config = _config(self.root)
        self.catalog = TerminalCatalog(terminal_catalog_path(self.root))

    def test_child_to_replacement_parent_is_resolved_by_task_containment(self) -> None:
        self.catalog.upsert(_seat("manager-old", self.master, "manager", status="terminated"))
        self.catalog.upsert(_seat("manager-new", self.master, "manager"))
        self.catalog.upsert(_seat("worker", self.leaf, "worker"))

        result = message_parent_tool(
            self.config,
            StructuralMessageRequest(
                ask="Review my turn report.",
                response="The report is durable.",
                message_kind="turn-report",
            ),
            StructuralAgentRuntime(
                host=_Host(),  # type: ignore[arg-type]
                environ={"AR_HOSTED_SESSION_ID": "worker", "AR_SPAWN_ROLE": "worker"},
            ),
        )

        self.assertEqual(result["taskDocumentRef"], self.master.model_dump())
        self.assertEqual(result["role"], "manager")
        self.assertTrue(
            {"session", "sessionId", "agentId", "lifecycleId", "entryId"}.isdisjoint(result)
        )
        row = next(iter(OperatorInboxStore(observer_root(self.config)).current().values()))
        self.assertEqual(row.agentId, "manager-new")
        self.assertEqual(row.taskDocumentRef, self.master)

    def test_parent_to_replacement_child_is_resolved_by_document_and_role(self) -> None:
        self.catalog.upsert(_seat("manager", self.master, "manager"))
        self.catalog.upsert(_seat("worker-old", self.leaf, "worker", status="terminated"))
        self.catalog.upsert(_seat("worker-new", self.leaf, "worker"))

        result = message_child_tool(
            self.config,
            StructuralMessageRequest(
                ask="Address the review finding.",
                response="Continue on the same leaf.",
                task_document_ref=self.leaf,
                role="worker",
            ),
            StructuralAgentRuntime(
                host=_Host(),  # type: ignore[arg-type]
                environ={"AR_HOSTED_SESSION_ID": "manager", "AR_SPAWN_ROLE": "manager"},
            ),
        )

        self.assertEqual(result["taskDocumentRef"], self.leaf.model_dump())
        self.assertEqual(result["role"], "worker")
        row = next(iter(OperatorInboxStore(observer_root(self.config)).current().values()))
        self.assertEqual(row.taskDocumentRef, self.leaf)
        self.assertEqual(row.recipientRole, "worker")
        self.assertEqual(target_session_for_entry(self.catalog, row).id, "worker-new")  # type: ignore[union-attr]

    def test_duplicate_current_occupants_fail_closed(self) -> None:
        self.catalog.upsert(_seat("manager-a", self.master, "manager"))
        self.catalog.upsert(_seat("manager-b", self.master, "manager"))
        resolver = StructuralSeatResolver(self.catalog, TaskDocumentTopology(self.root))

        with self.assertRaisesRegex(StructuralSeatError, "multiple running occupants"):
            resolver.current(self.master, "manager")

    def test_dispatch_persistence_failure_retires_the_unbriefed_child_privately(self) -> None:
        self.catalog.upsert(_seat("architect", self.sprint, "architect"))
        with (
            mock.patch(
                "agents_remember.application.structural.agent_tools.spawn_agent_session_tool",
                return_value={"status": "spawned-unbriefed", "session": "private-child-id"},
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools._post_structural_message",
                side_effect=ValueError("store refused"),
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools.session_retire_tool",
                return_value={"ok": True, "status": "retired"},
            ) as retire,
        ):
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.sprint,
                    role="orchestrator",
                    brief="Coordinate the sprint.",
                ),
                StructuralAgentRuntime(
                    environ={
                        "AR_HOSTED_SESSION_ID": "architect",
                        "AR_SPAWN_ROLE": "architect",
                    }
                ),
            )

        self.assertEqual(result["status"], "dispatch-persistence-refused")
        self.assertNotIn("private-child-id", str(result))
        self.assertEqual(retire.call_args.kwargs["session_id"], "private-child-id")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
