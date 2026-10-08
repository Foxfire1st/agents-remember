#!/usr/bin/env python3
"""Render the Hermes Agents Remember starter package for one workspace."""

# Generated file -- do not edit.
# Source: scripts/harness/render_starter.py
# Regenerate: python3 scripts/sync-harness.py

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

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


class RenderOptions(NamedTuple):
    coordination_root: Path | None = None
    host_port: int = 8766
    dashboard_port: int = 8765


DEFAULT_RENDER_OPTIONS = RenderOptions()


Renderer = Callable[[Path, Path, list[str], RenderOptions], None]


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


def _xdg_home(name: str, default: Path) -> Path:
    path = Path(os.environ.get(name) or default).expanduser()
    if not path.is_absolute():
        raise SystemExit(f"{name} must be an absolute path: {path}")
    return path.resolve()


def settings_payload(
    data: dict, workspace_root: Path, repos: list[str], options: RenderOptions
) -> dict:
    """Only the recorded coordination root and two ports change the rendered settings."""
    coordination = options.coordination_root or workspace_root / "ar-coordination"
    if not coordination.is_absolute() or any(
        not 0 < port < 65536 for port in (options.host_port, options.dashboard_port)
    ):
        raise SystemExit("coordination root must be absolute and both ports must be in 1..65535")
    coordination = coordination.resolve()
    return {
        **data,
        "coordinationRoot": coordination.as_posix(),
        "workspaceRoot": workspace_root.as_posix(),
        "transcriptRoot": (coordination / "logs/mcp").as_posix(),
        "dashboard": {**data.get("dashboard", {}), "port": options.dashboard_port},
        "repositories": {repo: {} for repo in repos},
    }


def fresh_host_block(options: RenderOptions = DEFAULT_RENDER_OPTIONS) -> dict:
    data = _xdg_home("XDG_DATA_HOME", Path.home() / ".local/share")
    state = _xdg_home("XDG_STATE_HOME", Path.home() / ".local/state")
    return {
        "installPrefix": (data / "agents-remember/paseo/prefix").as_posix(),
        "home": (state / "agents-remember/paseo").as_posix(),
        "listen": f"127.0.0.1:{options.host_port}",
        "providers": {},
        "embed": [
            {
                "dashboardOrigin": f"http://{host}:{options.dashboard_port}",
                "frameBaseUrl": f"http://127.0.0.1:{options.host_port}",
            }
            for host in ("127.0.0.1", "localhost")
        ],
    }


def render_settings(
    path: Path,
    workspace_root: Path,
    repos: list[str],
    options: RenderOptions = DEFAULT_RENDER_OPTIONS,
) -> None:
    data = settings_payload(
        json.loads(path.read_text(encoding="utf-8")), workspace_root, repos, options
    )
    shared = Path(data["coordinationRoot"]) / "system/settings.json"
    if shared.exists():
        # Configured setup is an input; its folders/providers and every other family stay intact.
        document = json.loads(shared.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise SystemExit(f"Shared host settings must be an object: {shared}")
    else:
        document = {"version": 1, "paseoRuntime": fresh_host_block(options)}
        shared.parent.mkdir(parents=True, exist_ok=True)
        with shared.open("x", encoding="utf-8") as output:
            output.write(json.dumps(document, indent=2) + "\n")
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Shared host settings: {shared}; existing settings are never overwritten")


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


def render_hermes(
    script_root: Path, workspace_root: Path, repos: list[str], options: RenderOptions
) -> None:
    replacements = {PATH_PLACEHOLDER: workspace_root.as_posix()}
    write_context_file(script_root / "HERMES.md", workspace_root / "HERMES.md", replacements)
    replace_text(script_root / "config.yaml", replacements)
    render_settings(
        script_root / "mcp" / "agents-remember-settings.json", workspace_root, repos, options
    )
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
    parser.add_argument(
        "--coordination-root",
        type=Path,
        default=None,
        help="Absolute installation coordination root (default: workspace/ar-coordination).",
    )
    parser.add_argument(
        "--host-port", type=int, default=8766, help="Loopback host listen port (default: 8766)."
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=8765,
        help="Dashboard port used by the embed origins (default: 8765).",
    )
    args = parser.parse_args()
    options = RenderOptions(args.coordination_root, args.host_port, args.dashboard_port)

    script_root = Path(__file__).resolve().parent
    workspace_root = infer_workspace_root(script_root)
    repos = repository_ids(workspace_root, args.repo)
    render(script_root, workspace_root, repos, options)
    print(f"Rendered {HARNESS_LABEL} starter for {workspace_root.as_posix()}")


if __name__ == "__main__":
    main(render_hermes)
