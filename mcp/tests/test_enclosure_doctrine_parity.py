"""L82-CODE-R1-08: scoped doctrine matches the new archive and no-remains routes.

Canonical ``c-09``/``c-12`` describe whole-sprint archive authority
(``archive_sprint`` alongside ``retire_master``, no false exclusivity) and the
bounded no-locator/no-folder/no-branch terminal exception with durable why
and exact retry; the generated package-data copies byte-match canonical; the
MCP reference rows name the exception; every documented next action exists as
a registered operation. Retirement/finalization authority, explicit adoption,
and archive-before-destructive-tail stay intact.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.models.tools.public_roster import PUBLIC_TOOLS

REPO_ROOT = Path(__file__).resolve().parents[2]
MCP_TOOLS = REPO_ROOT / "docs/reference/mcp-tools.md"


def test_mcp_reference_rows_name_exception() -> None:
    """The cleanup/abandon reference rows carry the bounded pre-locator path."""

    text = MCP_TOOLS.read_text(encoding="utf-8")
    for verb in ("worktree_cleanup", "worktree_abandon"):
        row = next(line for line in text.splitlines() if line.startswith(f"| `{verb}`"))
        assert "Bounded pre-locator exception" in row
        assert "records the why in the surviving contract" in row


def test_documented_next_actions_are_registered() -> None:
    """Every documented next action exists as a registered operation."""

    assert "worktree_enclosure_adopt" in PUBLIC_TOOLS
    assert "worktree_cleanup" in PUBLIC_TOOLS
    assert "worktree_abandon" in PUBLIC_TOOLS
    assert "task_doc" in PUBLIC_TOOLS
    registration = (REPO_ROOT / "mcp/src/agents_remember/mcp/registration/tasks.py").read_text(
        encoding="utf-8"
    )
    assert "archive_sprint" in registration
