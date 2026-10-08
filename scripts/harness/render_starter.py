#!/usr/bin/env python3
"""Fragment library for the per-harness ``render-starter.py`` programs.

``scripts/sync-harness.py`` slices named top-level definitions out of this module and
assembles one standalone ``render-starter.py`` per harness starter package. Nothing
imports this module at run time. It exists so that

* the body shared by every starter renderer has exactly one definition, and
* Ruff and Pyright check that body once, as a whole, instead of eight times.

A starter package is copied into a user's workspace and run from there, so each
generated program must stay a single self-contained file: sharing at run time is not
available, and sharing at generation time is what this module provides.

The module-level constants below are representative placeholders. The generator never
emits them; it emits a per-harness constants block built from ``sync-harness.py``'s
target table, then the requested fragments verbatim.
"""

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
from typing import NamedTuple

HARNESS_LABEL = "Claude Code"
PATH_PLACEHOLDER = "<PATH/TO/YOUR/PROJECTS_FOLDER>"
REPO_PLACEHOLDER = "<YOUR_REPOSITORY_FOLDER_NAME>"
PYTHON_PLACEHOLDER = "<PYTHON_EXECUTABLE>"
HOOK_SCRIPT_PLACEHOLDER = "<CLAUDE_HOOK_SCRIPT>"
HOOK_COMMAND_PLACEHOLDER = "<PYTHON_HOOK_COMMAND>"
PLACEHOLDERS: tuple[str, ...] = (PATH_PLACEHOLDER, REPO_PLACEHOLDER)
TARGET_FILES: tuple[str, ...] = ("mcp/agents-remember-settings.json",)
WORKSPACE_TARGET_FILES: tuple[str, ...] = ("HERMES.md",)
VSCODE_TARGET_FILES: tuple[str, ...] = ("mcp.json", "mcp/agents-remember-settings.json")


class RenderOptions(NamedTuple):
    coordination_root: Path | None = None
    host_port: int = 8766
    dashboard_port: int = 8765


DEFAULT_RENDER_OPTIONS = RenderOptions()


Renderer = Callable[[Path, Path, list[str], RenderOptions], None]


def command_string(argv: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def toml_basic_string_content(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


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


def render_claude_settings(path: Path, workspace_root: Path) -> None:
    """Claude Code takes the interpreter and the script as separate fields."""
    data = json.loads(path.read_text(encoding="utf-8"))
    hook = data["hooks"]["SessionStart"][0]["hooks"][0]
    hook["command"] = str(Path(sys.executable).resolve())
    hook["args"] = [
        (workspace_root / ".claude" / "hooks" / "agents-remember-session-start.py").as_posix()
    ]
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


def render_claude(
    script_root: Path, workspace_root: Path, repos: list[str], options: RenderOptions
) -> None:
    render_claude_settings(script_root / "settings.json", workspace_root)
    replace_text(script_root / "mcp" / "mcp.json", {PATH_PLACEHOLDER: workspace_root.as_posix()})
    render_settings(
        script_root / "mcp" / "agents-remember-settings.json", workspace_root, repos, options
    )
    validate((script_root, TARGET_FILES))


def render_codex(
    script_root: Path, workspace_root: Path, repos: list[str], options: RenderOptions
) -> None:
    hook_command = command_string(
        [
            str(Path(sys.executable).resolve()),
            (workspace_root / ".codex" / "hooks" / "agents-remember-session-start.py").as_posix(),
        ]
    )
    replace_text(
        script_root / "config.toml",
        {
            PATH_PLACEHOLDER: workspace_root.as_posix(),
            HOOK_COMMAND_PLACEHOLDER: toml_basic_string_content(hook_command),
        },
    )
    render_settings(
        script_root / "mcp" / "agents-remember-settings.json", workspace_root, repos, options
    )
    validate((script_root, TARGET_FILES))


def render_cursor(
    script_root: Path, workspace_root: Path, repos: list[str], options: RenderOptions
) -> None:
    render_cursor_hooks(script_root / "hooks.json", workspace_root)
    replace_text(script_root / "mcp.json", {PATH_PLACEHOLDER: workspace_root.as_posix()})
    replace_text(
        script_root / "rules" / "agents-remember.mdc",
        {PATH_PLACEHOLDER: workspace_root.as_posix()},
    )
    render_settings(
        script_root / "mcp" / "agents-remember-settings.json", workspace_root, repos, options
    )
    validate((script_root, TARGET_FILES))


def render_vscode(
    script_root: Path, workspace_root: Path, repos: list[str], options: RenderOptions
) -> None:
    vscode_dir = vscode_root(workspace_root)
    replace_text(
        script_root / "copilot-instructions.md", {PATH_PLACEHOLDER: workspace_root.as_posix()}
    )
    hooks_path = script_root / "hooks" / "agents-remember-session-start.json"
    render_vscode_hooks(hooks_path, workspace_root)
    replace_text(vscode_dir / "mcp.json", {PATH_PLACEHOLDER: workspace_root.as_posix()})
    render_settings(
        vscode_dir / "mcp" / "agents-remember-settings.json", workspace_root, repos, options
    )
    validate((script_root, TARGET_FILES), (vscode_dir, VSCODE_TARGET_FILES))


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


def render_openclaw(
    script_root: Path, workspace_root: Path, repos: list[str], options: RenderOptions
) -> None:
    replacements = {PATH_PLACEHOLDER: workspace_root.as_posix()}
    replace_text(script_root / "openclaw.merge.json", replacements)
    replace_text(script_root / "workspace" / "AGENTS.md", replacements)
    render_settings(
        script_root / "mcp" / "agents-remember-settings.json", workspace_root, repos, options
    )
    validate((script_root, TARGET_FILES))


def render_pi(
    script_root: Path, workspace_root: Path, repos: list[str], options: RenderOptions
) -> None:
    replacements = {PATH_PLACEHOLDER: workspace_root.as_posix()}
    replace_text(script_root / "mcp.json", replacements)
    replace_text(script_root / "extensions" / "agents-remember-start.ts", replacements)
    render_settings(
        script_root / "mcp" / "agents-remember-settings.json", workspace_root, repos, options
    )
    validate((script_root, TARGET_FILES))


def render_antigravity(
    script_root: Path, workspace_root: Path, repos: list[str], options: RenderOptions
) -> None:
    replacements = {PATH_PLACEHOLDER: workspace_root.as_posix()}
    write_context_file(script_root / "GEMINI.md", workspace_root / "GEMINI.md", replacements)
    replace_text(script_root / "mcp_config.json", replacements)
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
