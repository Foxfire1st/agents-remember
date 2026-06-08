#!/usr/bin/env python3
"""Render the OpenClaw Agents Remember starter package for one workspace."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

PATH_PLACEHOLDER = "<PATH/TO/YOUR/PROJECTS_FOLDER>"
REPO_PLACEHOLDER = "<YOUR_REPOSITORY_FOLDER_NAME>"
TARGET_FILES = (
    "openclaw.merge.json",
    "workspace/AGENTS.md",
    "mcp/agents-remember-settings.json",
)


def infer_workspace_root(script_root: Path) -> Path:
    return script_root.parent.resolve()


def repository_ids(workspace_root: Path, repos: list[str]) -> list[str]:
    if not repos:
        raise SystemExit("pass at least one --repo <repository-folder-name>")
    seen: set[str] = set()
    ordered: list[str] = []
    for repo_id in repos:
        if not repo_id or any(separator in repo_id for separator in ("/", "\\")):
            raise SystemExit(f"repository id must be a folder name under workspace root: {repo_id!r}")
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


def validate(script_root: Path) -> None:
    unresolved: list[str] = []
    for relative in TARGET_FILES:
        path = script_root / relative
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in (PATH_PLACEHOLDER, REPO_PLACEHOLDER)):
            unresolved.append(path.as_posix())
    if unresolved:
        joined = "\n".join(unresolved)
        raise SystemExit(f"unresolved starter placeholders remain:\n{joined}")


def render(script_root: Path, workspace_root: Path, repos: list[str]) -> None:
    replacements = {PATH_PLACEHOLDER: workspace_root.as_posix()}
    replace_text(script_root / "openclaw.merge.json", replacements)
    replace_text(script_root / "workspace" / "AGENTS.md", replacements)
    render_settings(script_root / "mcp" / "agents-remember-settings.json", workspace_root, repos)
    validate(script_root)


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
    print(f"Rendered OpenClaw starter for {workspace_root.as_posix()}")


if __name__ == "__main__":
    main()
