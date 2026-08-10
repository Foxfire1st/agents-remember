from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.mcp.tools.terminal import attach_terminal_session_to_leaf_payload
from agents_remember.serving import _app_terminal_routes as terminal_routes
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    TerminalCatalogEntry,
    terminal_catalog_path,
)
from agents_remember.serving.terminal_leaf_assignment import assign_terminal_session_to_leaf
from agents_remember.serving.terminal_opener import OpenTerminalResult
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


def _write_leaf(root: Path) -> str:
    task_root = root / "tasks" / "repo" / "master"
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": "MASTER",
                "slug": "task",
                "title": "Master",
                "kind": "master",
                "repo": "repo",
                "createdAt": "2026-07-07T10:00",
                "subTasks": [
                    {
                        "number": "leaf-1",
                        "name": "Leaf 1",
                        "file": "legacy-leaf.md",
                        "status": "inProgress",
                    }
                ],
            }
        ),
    )
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": "leaf-1",
                "slug": "legacy-leaf",
                "title": "Leaf 1",
                "kind": "subTask",
                "repo": "repo",
                "createdAt": "2026-07-07T10:01",
                "master": "task.md",
            }
        ),
    )
    return "repo/master/leaf-1"


def _entry(
    session_id: str,
    *,
    leaf_key: str | None = None,
    spawn_role: str | None = None,
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
        leaf_key=leaf_key,
        spawn_role=spawn_role,
    )


def _require_entry(catalog: TerminalCatalog, session_id: str) -> TerminalCatalogEntry:
    entry = catalog.get(session_id)
    if entry is None:
        raise AssertionError(f"missing catalog entry {session_id}")
    return entry


class TerminalLeafAssignmentTests(unittest.TestCase):
    def test_assign_terminal_session_to_leaf_moves_existing_catalog_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "terminal-sessions.json")
            catalog.upsert(_entry("chat-1", leaf_key="repo/master/old"))

            result = assign_terminal_session_to_leaf(
                catalog,
                _Host("ar-chat-1"),
                session_id="chat-1",
                leaf_key="repo/master/new",
                role="worker",
            )

            self.assertEqual(result.status, "attached")
            self.assertEqual(result.previous_leaf_key, "repo/master/old")
            updated = _require_entry(catalog, "chat-1")
            self.assertEqual(updated.leaf_key, "repo/master/new")

    def test_assign_terminal_session_to_leaf_reports_leaf_taken_without_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "terminal-sessions.json")
            catalog.upsert(_entry("owner", leaf_key="repo/master/new", spawn_role="worker"))
            catalog.upsert(_entry("seeker", leaf_key="repo/master/old", spawn_role="worker"))

            result = assign_terminal_session_to_leaf(
                catalog,
                _Host("ar-owner", "ar-seeker"),
                session_id="seeker",
                leaf_key="repo/master/new",
                role="worker",
            )

            self.assertEqual(result.status, "leaf-taken")
            self.assertEqual(result.owner_session_id, "owner")
            seeker = _require_entry(catalog, "seeker")
            self.assertEqual(seeker.leaf_key, "repo/master/old")

    def test_attach_terminal_session_to_leaf_payload_uses_dashboard_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config(root)
            canonical = _write_leaf(root)
            catalog = TerminalCatalog(terminal_catalog_path(root))
            catalog.upsert(_entry("chat-1", leaf_key="repo/master/old"))

            payload = attach_terminal_session_to_leaf_payload(
                config,
                session_id="chat-1",
                leaf_key="legacy-leaf",
                role="worker",
                host=_Host("ar-chat-1"),
            )

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["operation"], "attach_terminal_session_to_leaf")
            self.assertEqual(payload["status"], "attached")
            self.assertEqual(payload["session"], "chat-1")
            self.assertEqual(payload["previousLeafKey"], "repo/master/old")
            self.assertEqual(payload["role"], "chat")
            self.assertEqual(payload["seatRole"], "worker")
            updated = _require_entry(catalog, "chat-1")
            self.assertEqual(payload["leafKey"], canonical)
            self.assertEqual(updated.leaf_key, canonical)

    def test_attach_defaults_to_spawn_role_and_requires_role_for_hand_opened_chat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config(root)
            canonical = _write_leaf(root)
            catalog = TerminalCatalog(terminal_catalog_path(root))
            catalog.upsert(_entry("worker", spawn_role="worker"))
            catalog.upsert(_entry("hand-opened"))
            host = _Host("ar-worker", "ar-hand-opened")

            worker = attach_terminal_session_to_leaf_payload(
                config, session_id="worker", leaf_key=canonical, host=host
            )
            hand_opened = attach_terminal_session_to_leaf_payload(
                config, session_id="hand-opened", leaf_key=canonical, host=host
            )

            self.assertEqual(worker["status"], "attached")
            self.assertEqual(worker["seatRole"], "worker")
            self.assertEqual(hand_opened["status"], "role-required")
            self.assertIsNone(_require_entry(catalog, "hand-opened").leaf_key)

    def test_hand_opened_architect_attaches_without_impersonating_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "terminal-sessions.json")
            catalog.upsert(_entry("worker", leaf_key="repo/master/leaf", spawn_role="worker"))
            catalog.upsert(_entry("architect"))
            host = _Host("ar-worker", "ar-architect")

            result = assign_terminal_session_to_leaf(
                catalog,
                host,
                session_id="architect",
                leaf_key="repo/master/leaf",
                role="architect",
            )

            self.assertEqual(result.status, "attached")
            self.assertEqual(_require_entry(catalog, "worker").binding_role, "worker")
            self.assertEqual(_require_entry(catalog, "architect").binding_role, "architect")

    def test_root_architect_attachment_binds_once_and_rejects_cross_sprint_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "terminal-sessions.json")
            catalog.upsert(_entry("architect"))
            host = _Host("ar-architect")

            first = assign_terminal_session_to_leaf(
                catalog,
                host,
                session_id="architect",
                leaf_key="repo-a/sprint-a/leaf-1",
                role="architect",
            )
            second = assign_terminal_session_to_leaf(
                catalog,
                host,
                session_id="architect",
                leaf_key="repo-b/sprint-b/leaf-1",
                role="architect",
            )

            row = _require_entry(catalog, "architect")
            self.assertEqual(first.status, "attached")
            self.assertEqual(second.status, "sprint-binding-conflict")
            self.assertEqual(
                (row.leaf_key, row.sprint_key), ("repo-a/sprint-a/leaf-1", "repo-a/sprint-a")
            )

    def test_unbound_non_root_named_attachment_refuses_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "terminal-sessions.json")
            catalog.upsert(_entry("manager"))

            result = assign_terminal_session_to_leaf(
                catalog,
                _Host("ar-manager"),
                session_id="manager",
                leaf_key="repo-a/sprint-a/leaf-1",
                role="manager",
            )

            self.assertEqual(result.status, "sprint-binding-required")
            self.assertIsNone(_require_entry(catalog, "manager").leaf_key)

    def test_http_routes_expose_named_scope_refusals(self) -> None:
        runtime = cast(
            Any,
            SimpleNamespace(
                config=SimpleNamespace(
                    workspace_root=Path("/workspace"), coordination_root=Path("/tmp")
                ),
                catalog=TerminalCatalog(Path(tempfile.gettempdir()) / "scope-route-catalog.json"),
                host=_Host(),
            ),
        )
        request = terminal_routes.TerminalOpenRequest(kind="terminal")
        with (
            mock.patch.object(
                terminal_routes, "resolve_terminal_open_selection", return_value=None
            ),
            mock.patch.object(
                terminal_routes,
                "open_terminal_session",
                return_value=OpenTerminalResult(status="sprint-binding-required"),
            ),
        ):
            response = terminal_routes._open_terminal_response(runtime, "architect", request)
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"sprint-binding-required", response.body)

        with (
            mock.patch.object(
                terminal_routes, "_resolve_request_leaf_key", return_value="repo/sprint/leaf"
            ),
            mock.patch.object(
                terminal_routes,
                "assign_terminal_session_to_leaf",
                return_value=SimpleNamespace(status="sprint-binding-conflict"),
            ),
        ):
            attached = terminal_routes._attach_leaf_response(
                runtime,
                "architect",
                terminal_routes.TerminalAttachLeafRequest(
                    leafKey="repo/sprint/leaf", role="architect"
                ),
            )
        self.assertEqual(attached.status_code, 409)
        self.assertIn(b"sprint-binding-conflict", attached.body)

        taken = terminal_routes._open_terminal_refusal_response(
            OpenTerminalResult(status="leaf-taken", owner_session_id="other"),
            "repo/sprint/leaf",
        )
        assert taken is not None
        self.assertEqual(taken.status_code, 409)
        self.assertIn(b"leaf-taken", taken.body)

    def test_role_suffixed_leaf_ref_is_rejected_with_pair_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config(root)
            _write_leaf(root)

            payload = attach_terminal_session_to_leaf_payload(
                config,
                session_id="unused",
                leaf_key="legacy-leaf-curator",
                role="curator",
                host=_Host(),
            )

            self.assertEqual(payload["status"], "leaf-ref-not-found")
            self.assertIn("role-suffixed leaf refs are unsupported", payload["detail"])
            self.assertIn("role='curator'", payload["detail"])

    def test_attach_payload_rejects_unmatchable_leaf_ref_without_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config(root)
            _write_leaf(root)
            catalog = TerminalCatalog(terminal_catalog_path(root))
            catalog.upsert(_entry("chat-1", leaf_key="repo/master/old"))

            payload = attach_terminal_session_to_leaf_payload(
                config,
                session_id="chat-1",
                leaf_key="missing-leaf",
            )

            self.assertFalse(payload["ok"])
            self.assertEqual(payload["status"], "leaf-ref-not-found")
            self.assertIn("<repo>/<master-folder>/<doc-id>", payload["detail"])
            self.assertEqual(_require_entry(catalog, "chat-1").leaf_key, "repo/master/old")


if __name__ == "__main__":
    unittest.main()
