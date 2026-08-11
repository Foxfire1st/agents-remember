"""Keep control-plane correlations out of executable agent doctrine."""

from __future__ import annotations

from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_SKILL = REPOSITORY_ROOT / ".codex/skills/l-01-agent-lifecycles"
PACKAGED_SKILL = (
    REPOSITORY_ROOT / "mcp/src/agents_remember/package_data/runtime/skills/l-01-agent-lifecycles"
)
FORBIDDEN_AGENT_INSTRUCTIONS = (
    "operator_inbox_post",
    "operator_inbox_poll",
    "operator_inbox_consume",
    "spawn_agent_session",
    "hosted_session_readiness",
    "session_retire",
    "session_rename",
    "attach_terminal_session",
    "gateId",
    "gate_id",
    "lifecycleId",
    "lifecycle_id",
    "agentId",
    "agent_id",
    "sessionId",
    "session_id",
    "leaf_key",
    "qualified leaf",
    "coordination leaf",
)


def _agent_instruction_files(root: Path) -> list[Path]:
    return sorted((*root.joinpath("roles").glob("*.md"), *root.joinpath("templates").glob("*.md")))


@pytest.mark.parametrize("skill_root", [CANONICAL_SKILL, PACKAGED_SKILL])
def test_agent_doctrine_contains_no_control_plane_address_instructions(skill_root: Path) -> None:
    findings: list[str] = []
    for path in _agent_instruction_files(skill_root):
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_AGENT_INSTRUCTIONS:
            if forbidden.casefold() in text.casefold():
                findings.append(f"{path.relative_to(skill_root)}: {forbidden}")

    assert findings == []


def test_packaged_agent_doctrine_is_the_canonical_skill_exactly() -> None:
    canonical = {
        path.relative_to(CANONICAL_SKILL): path.read_bytes()
        for path in CANONICAL_SKILL.rglob("*")
        if path.is_file()
    }
    packaged = {
        path.relative_to(PACKAGED_SKILL): path.read_bytes()
        for path in PACKAGED_SKILL.rglob("*")
        if path.is_file()
    }

    assert packaged == canonical
