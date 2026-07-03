from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.mcp.tools.terminal import attach_terminal_session_to_leaf_payload
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    TerminalCatalogEntry,
    terminal_catalog_path,
)
from agents_remember.serving.terminal_leaf_assignment import assign_terminal_session_to_leaf


def _config(root: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root,
        transcript_root=root / "logs" / "mcp",
    )


def _entry(session_id: str, *, leaf_key: str | None = None) -> TerminalCatalogEntry:
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
                session_id="chat-1",
                leaf_key="repo/master/new",
            )

            self.assertEqual(result.status, "attached")
            self.assertEqual(result.previous_leaf_key, "repo/master/old")
            updated = _require_entry(catalog, "chat-1")
            self.assertEqual(updated.leaf_key, "repo/master/new")

    def test_assign_terminal_session_to_leaf_reports_leaf_taken_without_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "terminal-sessions.json")
            catalog.upsert(_entry("owner", leaf_key="repo/master/new"))
            catalog.upsert(_entry("seeker", leaf_key="repo/master/old"))

            result = assign_terminal_session_to_leaf(
                catalog,
                session_id="seeker",
                leaf_key="repo/master/new",
            )

            self.assertEqual(result.status, "leaf-taken")
            self.assertEqual(result.owner_session_id, "owner")
            seeker = _require_entry(catalog, "seeker")
            self.assertEqual(seeker.leaf_key, "repo/master/old")

    def test_attach_terminal_session_to_leaf_payload_uses_dashboard_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config(root)
            catalog = TerminalCatalog(terminal_catalog_path(root))
            catalog.upsert(_entry("chat-1", leaf_key="repo/master/old"))

            payload = attach_terminal_session_to_leaf_payload(
                config,
                session_id="chat-1",
                leaf_key="repo/master/new",
            )

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["operation"], "attach_terminal_session_to_leaf")
            self.assertEqual(payload["status"], "attached")
            self.assertEqual(payload["session"], "chat-1")
            self.assertEqual(payload["previousLeafKey"], "repo/master/old")
            self.assertEqual(payload["role"], "chat")
            updated = _require_entry(catalog, "chat-1")
            self.assertEqual(updated.leaf_key, "repo/master/new")


if __name__ == "__main__":
    unittest.main()
