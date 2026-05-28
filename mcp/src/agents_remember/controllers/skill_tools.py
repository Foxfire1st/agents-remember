"""Controllers for skill-facing MCP parity tools."""

from __future__ import annotations

from typing import Any

from agents_remember.install.skills import install_skills
from agents_remember.mcp.config import McpRuntimeConfig


def skills_install_tool(
    config: McpRuntimeConfig,
    *,
    layout: str = "tree",
    dry_run: bool = True,
    overwrite: bool = False,
    archive_existing: bool = False,
) -> dict[str, Any]:
    if config.harness_skill_root is None:
        raise ValueError(
            "MCP settings must live under <registration-root>/mcp/ or define "
            "harnessSkillRoot before skills_install can run"
        )
    return install_skills(
        install_root=config.harness_skill_root,
        layout=layout,
        dry_run=dry_run,
        overwrite=overwrite,
        archive_existing=archive_existing,
    )
