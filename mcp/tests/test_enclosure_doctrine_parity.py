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
C09 = REPO_ROOT / "skills/c-09-git-worktree-manager/SKILL.md"
C12 = REPO_ROOT / "skills/c-12-closeout/SKILL.md"
C09_GENERATED = (
    REPO_ROOT
    / "mcp/src/agents_remember/package_data/runtime/skills/c-09-git-worktree-manager/SKILL.md"
)
C12_GENERATED = (
    REPO_ROOT / "mcp/src/agents_remember/package_data/runtime/skills/c-12-closeout/SKILL.md"
)
MCP_TOOLS = REPO_ROOT / "docs/reference/mcp-tools.md"


def test_c12_no_retire_master_exclusivity() -> None:
    """c-12 no longer claims retire_master is the only master archive operation."""

    text = C12.read_text(encoding="utf-8")
    assert "is the one operation that archives a master" not in text
    assert "archived only by" not in text
    assert "archive_sprint" in text
    assert "retire_master" in text


def test_c12_keeps_retirement_finalization_authority() -> None:
    """Individual retirement/finalization authority survives the repair."""

    text = C12.read_text(encoding="utf-8")
    assert "Finalizing a master never archives it" in text
    assert "archives an individual master" in text
    assert "archive_sprint" in text
    assert "tasks/<repository>/0_archive/" in text


def test_c09_states_bounded_no_remains_exception() -> None:
    """c-09 names the no-locator/no-folder/no-branch exception with why and retry."""

    text = C09.read_text(encoding="utf-8")
    assert "Bounded no-remains exception" in text
    assert "no locator" in text
    assert "worktree folder" in text
    assert "work branches" in text
    assert "durable why" in text
    assert "nothingToArchive" in text
    assert "nothingToDelete" in text
    assert "nextArgs" in text
    assert "worktree_cleanup" in text
    assert "worktree_abandon" in text
    assert "worktree_enclosure_adopt" in text


def test_c09_keeps_adoption_and_archive_tail() -> None:
    """Explicit adoption and archive-before-destructive-tail stay intact."""

    text = C09.read_text(encoding="utf-8")
    assert "validates the exact contract/root pair" in text
    assert "reads the archive back and verifies its exact bytes" in text
    assert "terminal-archived" in text
    assert "only then removes worktrees" in text


def test_generated_skill_copies_match_canonical() -> None:
    """Generated package-data siblings byte-match canonical after sync."""

    assert C09_GENERATED.read_bytes() == C09.read_bytes()
    assert C12_GENERATED.read_bytes() == C12.read_bytes()


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
