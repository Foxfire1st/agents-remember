"""Materialize a CGC runtime layout onto disk (directories and config files)."""

from __future__ import annotations

from pathlib import Path

from agents_remember.providers.cgc.context.constants import (
    CGC_ENV_FILE_EXCLUDED_KEYS,
    CGC_REQUIREMENTS,
    DEFAULT_CGCIGNORE,
    read_gitignore_patterns,
)
from agents_remember.providers.cgc.context.core import CgcRuntimeLayout


def ensure_cgc_runtime_layout(layout: CgcRuntimeLayout) -> None:
    """Create runtime directories and default CGC config files."""

    for path in _cgc_runtime_directories(layout):
        path.mkdir(parents=True, exist_ok=True)

    if not layout.requirements_file.exists():
        layout.requirements_file.write_text("\n".join(CGC_REQUIREMENTS) + "\n", encoding="utf-8")
    layout.cgcignore_path.write_text(_cgcignore_text(layout), encoding="utf-8")
    layout.config_file.write_text("database: falkordb-remote\n", encoding="utf-8")
    layout.env_file.write_text(_cgc_env_text(layout), encoding="utf-8")


def _cgc_runtime_directories(layout: CgcRuntimeLayout) -> list[Path]:
    return [
        layout.requirements_file.parent,
        layout.patches_root,
        layout.image_build_root,
        layout.image_lock_file.parent,
        layout.cgc_root,
        layout.backend_data_root,
        layout.backend_state_file.parent,
        layout.run_root,
        layout.run_root / "home",
        layout.run_root / "appdata",
        layout.run_root / "localappdata",
        layout.logs_root,
    ]


def _cgcignore_text(layout: CgcRuntimeLayout) -> str:
    cgcignore_lines: list[str] = []
    cgcignore_lines.extend(DEFAULT_CGCIGNORE.rstrip("\n").splitlines())
    gitignore_patterns = read_gitignore_patterns(layout.code_repo_root)
    if gitignore_patterns:
        cgcignore_lines.extend(["", "# Inherited from source .gitignore"])
        cgcignore_lines.extend(gitignore_patterns)
    if layout.cgcignore_patterns:
        cgcignore_lines.extend(["", "# Repo-specific managed exclusions"])
        cgcignore_lines.extend(layout.cgcignore_patterns)
    return "\n".join(cgcignore_lines) + "\n"


def _cgc_env_text(layout: CgcRuntimeLayout) -> str:
    lines = ["# Managed by Agents Remember for CodeGraphContext"]
    lines.extend(
        f"{key}={value}"
        for key, value in layout.env().items()
        if key not in CGC_ENV_FILE_EXCLUDED_KEYS
    )
    return "\n".join(lines) + "\n"
