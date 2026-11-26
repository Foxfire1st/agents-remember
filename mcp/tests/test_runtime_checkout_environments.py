"""Verify the checkout migration list through the runtime install entry point."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest
from agents_remember.application.runtime.install import RuntimeInstallRequest, run_runtime_install
from agents_remember.application.tool_answer_log import tool_call_context
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.mcp.tools.core import compact_runtime_install_payload, runtime_install_payload
from agents_remember.serving import daemon
from agents_remember_test_support.testing.review_git_trees import git


def _new_repository(path: Path) -> Path:
    contract = path / "scripts/python-runtime-contract.env"
    contract.parent.mkdir(parents=True)
    contract.write_text("AR_PYTHON_MINOR=3.14\n", encoding="utf-8")
    git(path, "init", "-q")
    git(path, "add", "scripts/python-runtime-contract.env")
    git(
        path,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "runtime inventory fixture",
    )
    return path


def _linked_checkout(repository: Path, folder: str) -> Path:
    path = repository.parent.parent / folder
    git(repository, "worktree", "add", "--detach", "-q", str(path))
    return path


def _write_venv_home(checkout: Path, base: Path) -> Path:
    cfg = checkout / "mcp/.venv/pyvenv.cfg"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(f"home = {base / 'bin'}\n", encoding="utf-8")
    return cfg


@pytest.fixture
def install_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> McpRuntimeConfig:
    projects = tmp_path / "projects"
    _new_repository(projects / "agents-remember")
    path = tmp_path / "fixture-settings.json"
    path.write_text(
        json.dumps(
            {
                "coordinationRoot": str(tmp_path / "coordination"),
                "workspaceRoot": str(projects),
                "repositories": {"agents-remember": {}, "other": {}},
            }
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    monkeypatch.delenv("AR_EXPERIMENT", raising=False)
    monkeypatch.setattr(daemon, "observe_dashboard", lambda *_endpoint: None)
    return config


def test_runtime_install_lists_registered_stale_checkouts_without_rebuilding(
    install_config: McpRuntimeConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = install_config.repositories["agents-remember"].path
    uv_directory = tmp_path / "isolated uv"
    patch = uv_directory / "cpython-3.14.7-linux-x86_64-gnu"
    minor = uv_directory / "cpython-3.14-linux-x86_64-gnu"
    patch.mkdir(parents=True)
    minor.symlink_to(patch.name, target_is_directory=True)
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(uv_directory))

    patch_pinned = _linked_checkout(repository, "patch checkout's copy")
    carriage_return = _linked_checkout(repository, "stale checkout\rwith a carriage return")
    current = _linked_checkout(repository, "current linked checkout")
    incomplete = _linked_checkout(repository, "incomplete environment")
    (incomplete / "mcp/.venv").mkdir(parents=True)
    _linked_checkout(repository, "checkout without an environment")
    decoy = _new_repository(install_config.repositories["other"].path)
    monkeypatch.chdir(decoy)
    legacy_home = tmp_path / "legacy python"
    cfgs = [
        _write_venv_home(repository, legacy_home),
        _write_venv_home(patch_pinned, patch),
        _write_venv_home(carriage_return, legacy_home),
        _write_venv_home(current, minor),
        _write_venv_home(decoy, legacy_home),
    ]
    before = [(cfg.read_bytes(), cfg.stat().st_mtime_ns) for cfg in cfgs]

    result = run_runtime_install(install_config, RuntimeInstallRequest(dry_run=True))

    assert result["ok"] and not result["host"]["configured"]
    inventory = result["checkoutEnvironments"]
    assert set(inventory) == {"staleCheckouts", "instruction"}
    stale = inventory["staleCheckouts"]
    assert sorted(row["checkout"] for row in stale) == sorted(
        str(checkout) for checkout in (repository, patch_pinned, carriage_return, incomplete)
    )
    for row in stale:
        assert shlex.split(row["bootstrapCommand"]) == [
            "cd",
            row["checkout"],
            "&&",
            "scripts/bootstrap-mcp-venv.sh",
        ]
    assert compact_runtime_install_payload(result, None)["checkoutEnvironments"] == inventory
    assert [(cfg.read_bytes(), cfg.stat().st_mtime_ns) for cfg in cfgs] == before
    assert not install_config.coordination_root.exists()


def test_runtime_install_overflow_retains_every_stale_checkout(
    install_config: McpRuntimeConfig, tmp_path: Path
) -> None:
    repository = install_config.repositories["agents-remember"].path
    checkouts = [
        _linked_checkout(repository, f"candidate checkout requiring runtime migration {i:02}")
        for i in range(24)
    ]
    for checkout in checkouts:
        _write_venv_home(checkout, tmp_path / "legacy python")

    with tool_call_context("runtime_install", {"dry_run": True}, config=install_config):
        answer = runtime_install_payload(install_config, RuntimeInstallRequest(dry_run=True))

    assert answer["overflow"] is True
    detail = json.loads(Path(answer["log"]).read_text(encoding="utf-8"))
    stale = detail["checkoutEnvironments"]["staleCheckouts"]
    assert sorted(row["checkout"] for row in stale) == sorted(
        str(checkout) for checkout in checkouts
    )
