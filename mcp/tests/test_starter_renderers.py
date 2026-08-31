from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
REPOSITORIES = ("repo-one", "repo_two")
PLACEHOLDERS = (
    "<PATH/TO/YOUR/PROJECTS_FOLDER>",
    "<YOUR_REPOSITORY_FOLDER_NAME>",
    "<PYTHON_HOOK_COMMAND>",
    "<PYTHON_EXECUTABLE>",
    "<CLAUDE_HOOK_SCRIPT>",
)

CASES: tuple[dict[str, Any], ...] = (
    {
        "name": "codex",
        "source": ".codex",
        "target": ".codex",
        "settings": ".codex/mcp/agents-remember-settings.json",
        "rendered_files": (
            ".codex/config.toml",
            ".codex/mcp/agents-remember-settings.json",
            ".codex/hooks/agents-remember-session-start.py",
        ),
        "hook_script": ".codex/hooks/agents-remember-session-start.py",
    },
    {
        "name": "claude",
        "source": ".claude",
        "target": ".claude",
        "settings": ".claude/mcp/agents-remember-settings.json",
        "rendered_files": (
            ".claude/settings.json",
            ".claude/mcp/mcp.json",
            ".claude/mcp/agents-remember-settings.json",
            ".claude/hooks/agents-remember-session-start.py",
        ),
        "hook_script": ".claude/hooks/agents-remember-session-start.py",
    },
    {
        "name": "cursor",
        "source": ".cursor",
        "target": ".cursor",
        "settings": ".cursor/mcp/agents-remember-settings.json",
        "rendered_files": (
            ".cursor/hooks.json",
            ".cursor/mcp.json",
            ".cursor/mcp/agents-remember-settings.json",
            ".cursor/rules/agents-remember.mdc",
            ".cursor/hooks/agents-remember-session-start.py",
        ),
        "hook_script": ".cursor/hooks/agents-remember-session-start.py",
    },
    {
        "name": "vscode-copilot",
        "source": ".github-vscode",
        "target": ".github",
        "extra_copies": ((".vscode", ".vscode"),),
        "settings": ".vscode/mcp/agents-remember-settings.json",
        "rendered_files": (
            ".github/copilot-instructions.md",
            ".github/hooks/agents-remember-session-start.json",
            ".github/hooks/agents-remember-session-start.py",
            ".vscode/mcp.json",
            ".vscode/mcp/agents-remember-settings.json",
        ),
        "hook_script": ".github/hooks/agents-remember-session-start.py",
    },
    {
        "name": "antigravity",
        "source": ".agents",
        "target": ".agents",
        "settings": ".agents/mcp/agents-remember-settings.json",
        "rendered_files": (
            ".agents/GEMINI.md",
            "GEMINI.md",
            ".agents/mcp_config.json",
            ".agents/mcp/agents-remember-settings.json",
        ),
    },
    {
        "name": "openclaw",
        "source": ".openclaw",
        "target": ".openclaw",
        "settings": ".openclaw/mcp/agents-remember-settings.json",
        "rendered_files": (
            ".openclaw/openclaw.merge.json",
            ".openclaw/workspace/AGENTS.md",
            ".openclaw/mcp/agents-remember-settings.json",
        ),
    },
    {
        "name": "hermes",
        "source": ".hermes",
        "target": ".hermes",
        "settings": ".hermes/mcp/agents-remember-settings.json",
        "rendered_files": (
            ".hermes/HERMES.md",
            "HERMES.md",
            ".hermes/config.yaml",
            ".hermes/mcp/agents-remember-settings.json",
        ),
    },
    {
        "name": "pi",
        "source": ".pi",
        "target": ".pi",
        "settings": ".pi/mcp/agents-remember-settings.json",
        "rendered_files": (
            ".pi/mcp.json",
            ".pi/mcp/agents-remember-settings.json",
            ".pi/extensions/agents-remember-start.ts",
        ),
    },
)


def copy_starter(source: str, target: Path) -> None:
    shutil.copytree(
        REPO_ROOT / source,
        target,
        ignore=shutil.ignore_patterns("skills", "__pycache__", "*.pyc"),
    )


def render_case(tmp_path: Path, case: dict[str, Any]) -> Path:
    workspace = tmp_path / f"workspace-{case['name']}"
    workspace.mkdir()
    for repo in REPOSITORIES:
        (workspace / repo).mkdir()

    copy_starter(case["source"], workspace / case["target"])
    for source, target in case.get("extra_copies", ()):
        copy_starter(source, workspace / target)

    command = [
        sys.executable,
        str(workspace / case["target"] / "render-starter.py"),
        "--repo",
    ]
    command.extend([*REPOSITORIES, REPOSITORIES[0]])

    result = subprocess.run(command, cwd=workspace, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return workspace


def assert_settings(settings_path: Path, workspace: Path) -> None:
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    workspace_posix = workspace.resolve().as_posix()

    assert data["coordinationRoot"] == f"{workspace_posix}/ar-coordination"
    assert data["workspaceRoot"] == workspace_posix
    assert data["transcriptRoot"] == f"{workspace_posix}/ar-coordination/logs/mcp"
    assert list(data["repositories"]) == list(REPOSITORIES)


def assert_no_placeholders(workspace: Path, rendered_files: tuple[str, ...]) -> None:
    for relative in rendered_files:
        text = (workspace / relative).read_text(encoding="utf-8")
        for placeholder in PLACEHOLDERS:
            assert placeholder not in text, f"{placeholder} remains in {relative}"


def assert_hook_smoke(workspace: Path, hook_script: str | None) -> None:
    if hook_script is None:
        return

    env = {**os.environ, "PWD": str(workspace)}
    result = subprocess.run(
        [sys.executable, str(workspace / hook_script)],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    if "additional_context" in payload:
        assert "ar-coordination/AGENTS.md" in payload["additional_context"]
    else:
        output = payload["hookSpecificOutput"]
        assert output["hookEventName"] == "SessionStart"
        assert "ar-coordination/AGENTS.md" in output["additionalContext"]


def assert_command_rendering(case: dict[str, Any], workspace: Path) -> None:
    python_executable = str(Path(sys.executable).resolve())
    workspace_posix = workspace.resolve().as_posix()

    if case["name"] == "codex":
        data = tomllib.loads((workspace / ".codex/config.toml").read_text(encoding="utf-8"))
        hook = data["hooks"]["SessionStart"][0]["hooks"][0]
        assert python_executable in hook["command"]
        assert f"{workspace_posix}/.codex/hooks/agents-remember-session-start.py" in hook["command"]
        assert data["mcp_servers"]["agents-remember"]["args"][-1] == (
            f"{workspace_posix}/.codex/mcp/agents-remember-settings.json"
        )
        assert data["mcp_servers"]["agents-remember"]["env_vars"] == [
            "AR_HOSTED_SESSION_ID",
            "AR_SPAWN_ROLE",
        ]
    elif case["name"] == "claude":
        data = json.loads((workspace / ".claude/settings.json").read_text(encoding="utf-8"))
        hook = data["hooks"]["SessionStart"][0]["hooks"][0]
        assert hook["command"] == python_executable
        assert hook["args"] == [f"{workspace_posix}/.claude/hooks/agents-remember-session-start.py"]
    elif case["name"] == "cursor":
        data = json.loads((workspace / ".cursor/hooks.json").read_text(encoding="utf-8"))
        command = data["hooks"]["sessionStart"][0]["command"]
        assert python_executable in command
        assert f"{workspace_posix}/.cursor/hooks/agents-remember-session-start.py" in command
    elif case["name"] == "vscode-copilot":
        data = json.loads(
            (workspace / ".github/hooks/agents-remember-session-start.json").read_text(
                encoding="utf-8"
            )
        )
        hook = data["hooks"]["SessionStart"][0]
        expected_key = {"windows": "windows", "darwin": "osx"}.get(
            platform.system().lower(), "linux"
        )
        assert hook["command"] == hook[expected_key]
        assert python_executable in hook["command"]
        assert (
            f"{workspace_posix}/.github/hooks/agents-remember-session-start.py" in hook["command"]
        )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_starter_renderers_fill_placeholders_and_settings(
    tmp_path: Path, case: dict[str, Any]
) -> None:
    workspace = render_case(tmp_path, case)

    assert_settings(workspace / case["settings"], workspace)
    assert_no_placeholders(workspace, case["rendered_files"])
    assert_command_rendering(case, workspace)
    assert_hook_smoke(workspace, case.get("hook_script"))


def test_starter_renderer_rejects_missing_repository(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    copy_starter(".codex", workspace / ".codex")

    result = subprocess.run(
        [
            sys.executable,
            str(workspace / ".codex" / "render-starter.py"),
            "--repo",
            "missing-repo",
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "repository root does not exist" in result.stderr
