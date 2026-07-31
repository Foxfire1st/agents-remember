#!/usr/bin/env python3
"""Render the Hermes Agents Remember starter package for one workspace."""

# Generated file -- do not edit.
# Source: scripts/harness/render_starter.py
# Regenerate: python3 scripts/sync-harness.py

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

HARNESS_LABEL = "Hermes"
PATH_PLACEHOLDER = "<PATH/TO/YOUR/PROJECTS_FOLDER>"
REPO_PLACEHOLDER = "<YOUR_REPOSITORY_FOLDER_NAME>"
PLACEHOLDERS = (
    PATH_PLACEHOLDER,
    REPO_PLACEHOLDER,
)
TARGET_FILES = (
    "HERMES.md",
    "config.yaml",
    "mcp/agents-remember-settings.json",
)
# Mirrored to the workspace root, where Hermes reads it from.
WORKSPACE_TARGET_FILES = ("HERMES.md",)


Renderer = Callable[[Path, Path, list[str]], None]


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


def write_context_file(
    template_path: Path, target_path: Path, replacements: dict[str, str]
) -> None:
    """Render a context file in place and mirror it to the workspace root.

    Hermes and Antigravity read their context file from the workspace root rather than
    from the starter folder, so the rendered template is copied out. An existing file
    with different content is a merge the user has to make; overwriting it would
    silently discard their instructions.
    """
    text = template_path.read_text(encoding="utf-8")
    for old, new in replacements.items():
        text = text.replace(old, new)
    template_path.write_text(text, encoding="utf-8", newline="\n")
    if target_path.exists() and target_path.read_text(encoding="utf-8") != text:
        raise SystemExit(
            f"{target_path} already exists with different content; "
            "merge it manually before rerunning"
        )
    target_path.write_text(text, encoding="utf-8", newline="\n")


def render_settings(path: Path, workspace_root: Path, repos: list[str]) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    workspace = workspace_root.as_posix()
    data["coordinationRoot"] = f"{workspace}/ar-coordination"
    data["workspaceRoot"] = workspace
    data["transcriptRoot"] = f"{workspace}/ar-coordination/logs/mcp"
    data["repositories"] = {repo: {} for repo in repos}
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


def render_hermes(script_root: Path, workspace_root: Path, repos: list[str]) -> None:
    replacements = {PATH_PLACEHOLDER: workspace_root.as_posix()}
    write_context_file(script_root / "HERMES.md", workspace_root / "HERMES.md", replacements)
    replace_text(script_root / "config.yaml", replacements)
    render_settings(script_root / "mcp" / "agents-remember-settings.json", workspace_root, repos)
    validate((script_root, TARGET_FILES), (workspace_root, WORKSPACE_TARGET_FILES))


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
    main(render_hermes)
