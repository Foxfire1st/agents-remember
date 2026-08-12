"""Canonical task-document seat assignment tests (EFA-L19; historical filename)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.mcp.tools.terminal import attach_terminal_session_to_task_payload
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.serving import _app_terminal_routes as terminal_routes
from agents_remember.serving._app_common import TerminalAttachTaskRequest
from agents_remember.serving.seat_binding import role_suffixed_leaf_base
from agents_remember.serving.terminal_catalog import TerminalCatalog, terminal_catalog_path
from agents_remember.serving.terminal_task_assignment import (
    TaskAssignmentRuntime,
    assign_terminal_session_to_task,
)
from agents_remember.tasks import TaskDocument, write_task_doc


class _Host:
    def __init__(self, *tmux_names: str) -> None:
        self.known = set(tmux_names)

    def has_session(self, tmux_name: str) -> bool:
        return tmux_name in self.known


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
            "createdAt": "2026-07-07T10:00",
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


def _entry(
    session_id: str,
    *,
    task_document_ref: TaskDocumentRef | None = None,
    spawn_role: str | None = None,
    seat_role: str | None = None,
) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=session_id,
        label=f"Claude Code {session_id}",
        kind="harness",
        harness="claude",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=("claude",),
        created_at="2026-07-02T00:00:00Z",
        last_attached_at="2026-07-02T00:00:00Z",
        status="running",
        task_document_ref=task_document_ref,
        spawn_role=spawn_role,
        seat_role=seat_role,
    )


class TerminalTaskAssignmentTests(unittest.TestCase):
    def test_assignment_moves_one_session_between_valid_leaf_role_seats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _sprint, _master, leaf = _write_topology(root)
            catalog = TerminalCatalog(terminal_catalog_path(root))
            catalog.upsert(_entry("worker", spawn_role="worker"))

            result = assign_terminal_session_to_task(
                TaskAssignmentRuntime(
                    catalog,
                    _Host("ar-worker"),
                    terminal_routes.TaskDocumentTopology(root),
                ),
                session_id="worker",
                task_document_ref=leaf,
            )

            self.assertEqual(result.status, "attached")
            self.assertEqual(catalog.get("worker").task_document_ref, leaf)  # type: ignore[union-attr]
            self.assertEqual(catalog.get("worker").binding_role, "worker")  # type: ignore[union-attr]

    def test_same_document_and_role_is_seat_taken_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _sprint, _master, leaf = _write_topology(root)
            catalog = TerminalCatalog(terminal_catalog_path(root))
            catalog.upsert(_entry("owner", task_document_ref=leaf, spawn_role="worker"))
            catalog.upsert(_entry("seeker", spawn_role="worker"))

            result = assign_terminal_session_to_task(
                TaskAssignmentRuntime(
                    catalog,
                    _Host("ar-owner", "ar-seeker"),
                    terminal_routes.TaskDocumentTopology(root),
                ),
                session_id="seeker",
                task_document_ref=leaf,
            )

            self.assertEqual(result.status, "seat-taken")
            self.assertEqual(result.owner_session_id, "owner")
            self.assertIsNone(catalog.get("seeker").task_document_ref)  # type: ignore[union-attr]

    def test_different_roles_can_share_one_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _sprint, _master, leaf = _write_topology(root)
            catalog = TerminalCatalog(terminal_catalog_path(root))
            catalog.upsert(_entry("worker", task_document_ref=leaf, spawn_role="worker"))
            catalog.upsert(_entry("reviewer", spawn_role="reviewer"))

            result = assign_terminal_session_to_task(
                TaskAssignmentRuntime(
                    catalog,
                    _Host("ar-worker", "ar-reviewer"),
                    terminal_routes.TaskDocumentTopology(root),
                ),
                session_id="reviewer",
                task_document_ref=leaf,
            )

            self.assertEqual(result.status, "attached")
            self.assertEqual(catalog.get("reviewer").binding_role, "reviewer")  # type: ignore[union-attr]

    def test_role_altitude_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sprint, master, leaf = _write_topology(root)
            catalog = TerminalCatalog(terminal_catalog_path(root))
            catalog.upsert(_entry("architect", spawn_role="architect"))
            catalog.upsert(_entry("manager", spawn_role="manager"))
            host = _Host("ar-architect", "ar-manager")
            topology = terminal_routes.TaskDocumentTopology(root)
            runtime = TaskAssignmentRuntime(catalog, host, topology)

            wrong_leaf = assign_terminal_session_to_task(
                runtime,
                session_id="architect",
                task_document_ref=leaf,
            )
            wrong_sprint = assign_terminal_session_to_task(
                runtime,
                session_id="manager",
                task_document_ref=sprint,
            )
            right_master = assign_terminal_session_to_task(
                runtime,
                session_id="manager",
                task_document_ref=master,
            )

            self.assertEqual(wrong_leaf.status, "task-binding-invalid")
            self.assertEqual(wrong_sprint.status, "task-binding-invalid")
            self.assertEqual(right_master.status, "attached")

    def test_payload_uses_canonical_task_reference_and_spawn_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _sprint, _master, leaf = _write_topology(root)
            catalog = TerminalCatalog(terminal_catalog_path(root))
            catalog.upsert(_entry("worker", spawn_role="worker"))

            payload = attach_terminal_session_to_task_payload(
                _config(root),
                session_id="worker",
                task_document_ref=leaf,
                host=_Host("ar-worker"),
            )

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["operation"], "attach_terminal_session_to_task")
            self.assertEqual(payload["status"], "attached")
            self.assertEqual(payload["taskDocumentRef"], leaf.model_dump())
            self.assertEqual(payload["seatRole"], "worker")

    def test_hand_opened_harness_requires_an_explicit_structural_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _sprint, _master, leaf = _write_topology(root)
            catalog = TerminalCatalog(terminal_catalog_path(root))
            catalog.upsert(_entry("free"))

            payload = attach_terminal_session_to_task_payload(
                _config(root),
                session_id="free",
                task_document_ref=leaf,
                host=_Host("ar-free"),
            )

            self.assertEqual(payload["status"], "role-required")
            self.assertIsNone(catalog.get("free").task_document_ref)  # type: ignore[union-attr]

    def test_http_attach_returns_structural_seat_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _sprint, _master, leaf = _write_topology(root)
            catalog = TerminalCatalog(terminal_catalog_path(root))
            catalog.upsert(_entry("owner", task_document_ref=leaf, spawn_role="worker"))
            catalog.upsert(_entry("seeker", spawn_role="worker"))
            runtime = cast(
                Any,
                SimpleNamespace(
                    config=SimpleNamespace(coordination_root=root),
                    catalog=catalog,
                    host=_Host("ar-owner", "ar-seeker"),
                ),
            )

            response = terminal_routes._attach_task_response(
                runtime,
                "seeker",
                TerminalAttachTaskRequest(taskDocumentRef=leaf, role="worker"),
            )

            self.assertEqual(response.status_code, 409)
            self.assertIn(b"seat-taken", response.body)
            self.assertIn(b"taskDocumentRef", response.body)

    def test_legacy_role_suffix_parser_covers_supported_separators_and_refusals(self) -> None:
        self.assertEqual(role_suffixed_leaf_base("leaf-WORKER"), ("leaf", "worker"))
        self.assertEqual(role_suffixed_leaf_base("leaf/reviewer"), ("leaf", "reviewer"))
        self.assertEqual(role_suffixed_leaf_base("leaf:curator"), ("leaf", "curator"))
        self.assertIsNone(role_suffixed_leaf_base("worker"))
        self.assertIsNone(role_suffixed_leaf_base("leaf-unknown"))


if __name__ == "__main__":
    unittest.main()
