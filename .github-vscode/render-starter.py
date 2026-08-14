#!/usr/bin/env python3
"""Render the VS Code + Copilot Agents Remember starter package for one workspace."""

# Generated file -- do not edit.
# Source: scripts/harness/render_starter.py
# Regenerate: python3 scripts/sync-harness.py

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

HARNESS_LABEL = "VS Code + Copilot"
PATH_PLACEHOLDER = "<PATH/TO/YOUR/PROJECTS_FOLDER>"
REPO_PLACEHOLDER = "<YOUR_REPOSITORY_FOLDER_NAME>"
PLACEHOLDERS = (
    PATH_PLACEHOLDER,
    REPO_PLACEHOLDER,
)
TARGET_FILES = (
    "copilot-instructions.md",
    "hooks/agents-remember-session-start.json",
    "hooks/agents-remember-session-start.py",
)
# Rendered into the sibling .vscode/ folder, not into this one.
VSCODE_TARGET_FILES = (
    "mcp.json",
    "mcp/agents-remember-settings.json",
)


Renderer = Callable[[Path, Path, list[str]], None]


def command_string(argv: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def infer_workspace_root(script_root: Path) -> Path:
    return script_root.parent.resolve()


def repository_ids(workspace_root: Path, repos: list[str]) -> list[str]:
    if not repos:
        raise SystemExit("pass at least one --repo <repository-folder-name>")
    seen: set[str] = set()
    ordered: list[str] = []
    for repo_id in repos:
        if not repo_id or any(separator in repo_id for separator in ("/", "\\")):
            raise SystemExit(
                f"repository id must be a folder name under workspace root: {repo_id!r}"
            )
        repo_root = workspace_root / repo_id
        if not repo_root.is_dir():
            raise SystemExit(f"repository root does not exist for {repo_id!r}: {repo_root}")
        if repo_id not in seen:
            seen.add(repo_id)
            ordered.append(repo_id)
    return ordered


def replace_text(path: Path, replacements: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8")
    for old, new in replacements.items():
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8", newline="\n")


def render_settings(path: Path, workspace_root: Path, repos: list[str]) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    workspace = workspace_root.as_posix()
    data["coordinationRoot"] = f"{workspace}/ar-coordination"
    data["workspaceRoot"] = workspace
    data["transcriptRoot"] = f"{workspace}/ar-coordination/logs/mcp"
    data["repositories"] = {repo: {} for repo in repos}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")


def hook_script_path(workspace_root: Path) -> Path:
    """Where the VS Code starter installs its session-start hook.

    The starter folder ships as ``.github-vscode/`` so it does not collide with a
    repository's own ``.github/``, but VS Code reads the hook from ``.github/``. The
    installed path is therefore not the folder this program runs from.
    """
    return workspace_root / ".github" / "hooks" / "agents-remember-session-start.py"


def vscode_root(workspace_root: Path) -> Path:
    root = workspace_root / ".vscode"
    if not root.is_dir():
        raise SystemExit(f"VS Code starter folder is missing: {root}")
    return root


def render_vscode_hooks(path: Path, workspace_root: Path) -> None:
    """VS Code takes a default command plus a per-platform override key."""
    data = json.loads(path.read_text(encoding="utf-8"))
    rendered = command_string(
        [str(Path(sys.executable).resolve()), hook_script_path(workspace_root).as_posix()]
    )
    hook = data["hooks"]["SessionStart"][0]
    hook["command"] = rendered
    system = platform.system().lower()
    if system == "windows":
        hook["windows"] = rendered
    elif system == "darwin":
        hook["osx"] = rendered
    else:
        hook["linux"] = rendered
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")


def validate(*groups: tuple[Path, tuple[str, ...]]) -> None:
    unresolved: list[str] = []
    for root, relatives in groups:
        for relative in relatives:
            path = root / relative
            text = path.read_text(encoding="utf-8")
            if any(marker in text for marker in PLACEHOLDERS):
                unresolved.append(path.as_posix())
    if unresolved:
        joined = "\n".join(unresolved)
        raise SystemExit(f"unresolved starter placeholders remain:\n{joined}")


def render_vscode(script_root: Path, workspace_root: Path, repos: list[str]) -> None:
    vscode_dir = vscode_root(workspace_root)
    replace_text(
        script_root / "copilot-instructions.md", {PATH_PLACEHOLDER: workspace_root.as_posix()}
    )
    hooks_path = script_root / "hooks" / "agents-remember-session-start.json"
    render_vscode_hooks(hooks_path, workspace_root)
    replace_text(vscode_dir / "mcp.json", {PATH_PLACEHOLDER: workspace_root.as_posix()})
    render_settings(vscode_dir / "mcp" / "agents-remember-settings.json", workspace_root, repos)
    validate((script_root, TARGET_FILES), (vscode_dir, VSCODE_TARGET_FILES))


def main(render: Renderer) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        nargs="+",
        required=True,
        metavar="REPO",
        help="Repository folder name(s) under the workspace root.",
    )
    args = parser.parse_args()

    script_root = Path(__file__).resolve().parent
    workspace_root = infer_workspace_root(script_root)
    repos = repository_ids(workspace_root, args.repo)
    render(script_root, workspace_root, repos)
    print(f"Rendered {HARNESS_LABEL} starter for {workspace_root.as_posix()}")


if __name__ == "__main__":
    main(render_vscode)
