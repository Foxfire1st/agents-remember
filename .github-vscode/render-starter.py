#!/usr/bin/env python3
"""Render the VS Code + Copilot Agents Remember starter package for one workspace."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import subprocess
import sys
from pathlib import Path

PATH_PLACEHOLDER = "<PATH/TO/YOUR/PROJECTS_FOLDER>"
REPO_PLACEHOLDER = "<YOUR_REPOSITORY_FOLDER_NAME>"
TARGET_FILES = (
    "copilot-instructions.md",
    "hooks/agents-remember-session-start.json",
    "hooks/agents-remember-session-start.py",
)
VSCODE_TARGET_FILES = (
    "mcp.json",
    "mcp/agents-remember-settings.json",
)


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
    return workspace_root / ".github" / "hooks" / "agents-remember-session-start.py"


def render_hooks(path: Path, workspace_root: Path) -> None:
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


def vscode_root(workspace_root: Path) -> Path:
    root = workspace_root / ".vscode"
    if not root.is_dir():
        raise SystemExit(f"VS Code starter folder is missing: {root}")
    return root


def validate(script_root: Path, vscode_dir: Path) -> None:
    unresolved: list[str] = []
    for relative in TARGET_FILES:
        path = script_root / relative
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in (PATH_PLACEHOLDER, REPO_PLACEHOLDER)):
            unresolved.append(path.as_posix())
    for relative in VSCODE_TARGET_FILES:
        path = vscode_dir / relative
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in (PATH_PLACEHOLDER, REPO_PLACEHOLDER)):
            unresolved.append(path.as_posix())
    if unresolved:
        joined = "\n".join(unresolved)
        raise SystemExit(f"unresolved starter placeholders remain:\n{joined}")


def render(script_root: Path, workspace_root: Path, repos: list[str]) -> None:
    vscode_dir = vscode_root(workspace_root)
    replace_text(
        script_root / "copilot-instructions.md", {PATH_PLACEHOLDER: workspace_root.as_posix()}
    )
    render_hooks(script_root / "hooks" / "agents-remember-session-start.json", workspace_root)
    replace_text(vscode_dir / "mcp.json", {PATH_PLACEHOLDER: workspace_root.as_posix()})
    render_settings(vscode_dir / "mcp" / "agents-remember-settings.json", workspace_root, repos)
    validate(script_root, vscode_dir)


def main() -> None:
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
    print(f"Rendered VS Code + Copilot starter for {workspace_root.as_posix()}")


if __name__ == "__main__":
    main()
