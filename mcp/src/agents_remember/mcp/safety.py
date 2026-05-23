"""Safety helpers for future Agents Remember MCP tools."""

from __future__ import annotations

from pathlib import Path

from .config import ConfigError, path_is_relative_to


def require_path_inside(path: Path, roots: tuple[Path, ...], *, label: str) -> Path:
    resolved = path.resolve()
    if not any(path_is_relative_to(resolved, root) for root in roots):
        allowed = ", ".join(root.as_posix() for root in roots)
        raise ConfigError(f"{label} must stay inside one of: {allowed}")
    return resolved
