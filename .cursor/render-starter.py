#!/usr/bin/env python3
"""Render the Cursor Agents Remember starter package for one workspace."""

# Generated file -- do not edit.
# Source: scripts/harness/render_starter.py
# Regenerate: python3 scripts/sync-harness.py

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

HARNESS_LABEL = "Cursor"
PATH_PLACEHOLDER = "<PATH/TO/YOUR/PROJECTS_FOLDER>"
REPO_PLACEHOLDER = "<YOUR_REPOSITORY_FOLDER_NAME>"
HOOK_COMMAND_PLACEHOLDER = "<PYTHON_HOOK_COMMAND>"
PLACEHOLDERS = (
    PATH_PLACEHOLDER,
    REPO_PLACEHOLDER,
    HOOK_COMMAND_PLACEHOLDER,
)
TARGET_FILES = (
    "hooks.json",
    "mcp.json",
    "mcp/agents-remember-settings.json",
    "rules/agents-remember.mdc",
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


def render_cursor_hooks(path: Path, workspace_root: Path) -> None:
    """Cursor takes one command string under a lower-case ``sessionStart`` key."""
    data = json.loads(path.read_text(encoding="utf-8"))
    hook_command = command_string(
        [
            str(Path(sys.executable).resolve()),
            (workspace_root / ".cursor" / "hooks" / "agents-remember-session-start.py").as_posix(),
        ]
    )
    data["hooks"]["sessionStart"][0]["command"] = hook_command
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


def render_cursor(script_root: Path, workspace_root: Path, repos: list[str]) -> None:
    render_cursor_hooks(script_root / "hooks.json", workspace_root)
    replace_text(script_root / "mcp.json", {PATH_PLACEHOLDER: workspace_root.as_posix()})
    replace_text(
        script_root / "rules" / "agents-remember.mdc",
        {PATH_PLACEHOLDER: workspace_root.as_posix()},
    )
    render_settings(script_root / "mcp" / "agents-remember-settings.json", workspace_root, repos)
    validate((script_root, TARGET_FILES))


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
    main(render_cursor)
