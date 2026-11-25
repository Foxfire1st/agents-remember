"""The Git hook reports missing dashboard prerequisites before slow checks."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_GATE = Path(__file__).resolve().parents[2] / ".githooks/_gate.sh"


def _executable(path: Path, script: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + script, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def hook_tree(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    commands = tmp_path / "commands"
    commands.mkdir()
    for command in ("git", "uname", "xargs"):
        executable = shutil.which(command)
        assert executable is not None
        (commands / command).symlink_to(executable)
    env = dict(os.environ, PATH=str(commands))
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "dashboard").mkdir()
    (tree / "dashboard/package.json").write_text("{}", encoding="utf-8")
    (tree / "tracked.py").touch()
    (tree / "scripts").mkdir()
    shutil.copyfile(
        _GATE.parents[1] / "scripts/python-runtime-contract.env",
        tree / "scripts/python-runtime-contract.env",
    )
    (tree / ".gitignore").write_text("mcp/.venv/\ndashboard/node_modules/\n", encoding="utf-8")
    _executable(
        tree / "mcp/.venv/bin/python",
        'printf "python %s\\n" "$*"; exit 0\n',
    )
    subprocess.run(["git", "init", "-q"], cwd=tree, env=env, check=True)
    subprocess.run(["git", "add", "."], cwd=tree, env=env, check=True)
    return tree, env


def _run_hook(
    hook_tree: tuple[Path, dict[str, str]], tier: str
) -> subprocess.CompletedProcess[str]:
    tree, env = hook_tree
    return subprocess.run(
        ["/bin/sh", str(_GATE), tier],
        cwd=tree,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


@pytest.mark.parametrize("tier", ["fast", "targeted"])
def test_missing_dependencies_fail_before_ruff(
    hook_tree: tuple[Path, dict[str, str]], tier: str
) -> None:
    result = _run_hook(hook_tree, tier)
    assert result.returncode == 1
    assert "dashboard/node_modules missing; run 'npm ci' in dashboard/ first." in result.stdout
    assert result.stdout.splitlines()[-1].endswith(f"result: {tier}-tier FAIL")
    assert "ruff (lint)" not in result.stdout, result.stdout


def test_missing_npm_fails_before_ruff(hook_tree: tuple[Path, dict[str, str]]) -> None:
    tree, _ = hook_tree
    (tree / "dashboard/node_modules").mkdir()
    result = _run_hook(hook_tree, "fast")
    assert result.returncode == 1
    assert "npm not found; cannot run the dashboard rail." in result.stdout
    assert result.stdout.splitlines()[-1].endswith("result: fast-tier FAIL")
    assert "ruff (lint)" not in result.stdout, result.stdout


def test_present_dependencies_keep_the_check_order(hook_tree: tuple[Path, dict[str, str]]) -> None:
    tree, env = hook_tree
    (tree / "dashboard/node_modules").mkdir()
    _executable(Path(env["PATH"]) / "npm", 'printf "npm %s\\n" "$*"; exit 0\n')

    result = _run_hook(hook_tree, "fast")
    assert result.returncode == 0, result.stdout
    assert [line for line in result.stdout.splitlines() if "result:" in line] == [
        "[pre-commit] result: generated-projection PASS",
        "[pre-commit] result: generated-skills PASS",
        "[pre-commit] result: generated-runtime PASS",
        "[pre-commit] result: ruff PASS",
        "[pre-commit] result: ruff-format PASS",
        "[pre-commit] result: pyright PASS",
        "[pre-commit] result: evidence-lifecycle PASS",
        "[pre-commit] result: dashboard-codegen PASS",
        "[pre-commit] result: dashboard-lint PASS",
        "[pre-commit] result: dashboard-typecheck PASS",
        "[pre-commit] result: fast-tier PASS; acceptance tests run only in Dagger.",
    ]


def test_missing_dashboard_keeps_the_skip(hook_tree: tuple[Path, dict[str, str]]) -> None:
    tree, env = hook_tree
    subprocess.run(["git", "rm", "-qf", "dashboard/package.json"], cwd=tree, env=env, check=True)
    result = _run_hook(hook_tree, "fast")
    assert result.returncode == 0, result.stdout
    assert "dashboard/package.json missing; skipping the frontend rail." in result.stdout
    assert "result: ruff PASS" in result.stdout
