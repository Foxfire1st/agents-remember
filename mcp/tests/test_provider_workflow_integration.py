from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, cast

import pytest
from agents_remember.benchmarks import runner as benchmark_runner
from agents_remember.controllers.worktree_tools import worktree_start_tool
from agents_remember.kernel.memory_ledger import create_initial_ledger, ledger_to_text
from agents_remember.mcp.config import load_config
from agents_remember.providers import lifecycle, provider_setup
from agents_remember.providers.settings import lifecycle_settings_from_config
from agents_remember.providers.setup_progress import read_setup_progress

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENTS_REMEMBER_PROVIDER_INTEGRATION") != "1",
    reason="set AGENTS_REMEMBER_PROVIDER_INTEGRATION=1 to run Docker provider workflow tests",
)


def _run(
    command: list[str], *, cwd: Path | None = None, timeout: int = 60
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.strip() or result.stdout.strip() or command)
    return result


def _git(repo: Path, *args: str) -> str:
    return _run(["git", *args], cwd=repo).stdout.strip()


def _init_git_repo(repo: Path, files: dict[str, str]) -> str:
    repo.mkdir(parents=True)
    _run(["git", "init", "-b", "main"], cwd=repo)
    _git(repo, "config", "user.email", "providers@example.invalid")
    _git(repo, "config", "user.name", "Provider Integration")
    for relative, text in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "Initial fixture")
    return _git(repo, "rev-parse", "HEAD")


def _init_memory_repo(memory_repo: Path, repo_id: str, code_commit: str) -> str:
    memory_repo.mkdir(parents=True)
    _run(["git", "init", "-b", "main"], cwd=memory_repo)
    _git(memory_repo, "config", "user.email", "providers@example.invalid")
    _git(memory_repo, "config", "user.name", "Provider Integration")
    (memory_repo / "system").mkdir()
    (memory_repo / "system" / "settings.md").write_text("# Settings\n", encoding="utf-8")
    (memory_repo / "onboarding").mkdir()
    (memory_repo / "onboarding" / "overview.md").write_text(
        "# Repo A Overview\n\nProvider integration memory fixture.\n",
        encoding="utf-8",
    )
    _git(memory_repo, "add", ".")
    _git(memory_repo, "commit", "-m", "Initial memory")
    memory_commit = _git(memory_repo, "rev-parse", "HEAD")
    (memory_repo / "memory.md").write_text(
        ledger_to_text(create_initial_ledger(repo_id, code_commit, memory_commit)),
        encoding="utf-8",
    )
    _git(memory_repo, "add", "memory.md")
    _git(memory_repo, "commit", "-m", "Add memory ledger")
    return _git(memory_repo, "rev-parse", "HEAD")


def _write_mcp_settings(path: Path, *, root: Path, instance_id: str) -> None:
    payload = {
        "version": 1,
        "coordinationRoot": (root / "ar-coordination").as_posix(),
        "workspaceRoot": (root / "workspace").as_posix(),
        "repositories": {"repo-a": {}},
        "providers": {
            "grepai-memory": {"instanceId": instance_id},
            "codegraphcontext-code": {"instanceId": instance_id},
        },
        "timeoutCaps": {"providerSetupSeconds": _provider_timeout()},
    }
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _provider_timeout() -> int:
    return int(os.environ.get("AGENTS_REMEMBER_PROVIDER_INTEGRATION_TIMEOUT", "1800"))


def _docker_available() -> bool:
    return (
        shutil.which("docker") is not None
        and subprocess.run(
            ["docker", "info"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


def _dict_value(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    return {}


def _list_value(value: object) -> list[Any]:
    if isinstance(value, list):
        return cast(list[Any], value)
    return []


def _settings_provider_containers(settings: dict[str, object]) -> set[str]:
    providers = _dict_value(_dict_value(settings.get("contextProviders")).get("providers"))
    names: set[str] = set()
    grepai = _dict_value(providers.get("grepai-memory"))
    if grepai:
        names.update(_grepai_containers(grepai))
    cgc = _dict_value(providers.get("codegraphcontext-code"))
    if cgc:
        names.update(_cgc_containers(cgc))
    return names


def _grepai_containers(provider: dict[str, Any]) -> set[str]:
    runtime = _dict_value(provider.get("runtime"))
    runner = _dict_value(runtime.get("runner"))
    backend = _dict_value(provider.get("backend"))
    embedder = _dict_value(provider.get("embedder"))
    embedder_backend = _dict_value(embedder.get("backend"))
    return {
        str(value)
        for value in (
            runner.get("containerName"),
            backend.get("containerName"),
            embedder_backend.get("containerName"),
        )
        if value
    }


def _cgc_containers(provider: dict[str, Any]) -> set[str]:
    backend = _dict_value(provider.get("backend"))
    runtime = _dict_value(provider.get("runtime"))
    runner = _dict_value(runtime.get("runner"))
    template = runner.get("containerNameTemplate")
    roots = _list_value(provider.get("roots"))
    names = {str(backend.get("containerName"))} if backend.get("containerName") else set()
    if isinstance(template, str):
        for root in roots:
            repo_id = _dict_value(root).get("repoId")
            if repo_id:
                names.add(template.replace("<repoId>", str(repo_id)))
    return names


def _settings_networks(settings: dict[str, object]) -> set[str]:
    providers = _dict_value(_dict_value(settings.get("contextProviders")).get("providers"))
    names: set[str] = set()
    grepai = _dict_value(providers.get("grepai-memory"))
    if grepai:
        runtime = _dict_value(grepai.get("runtime"))
        network = _dict_value(runtime.get("network"))
        if network.get("name"):
            names.add(str(network["name"]))
    cgc = _dict_value(providers.get("codegraphcontext-code"))
    if cgc:
        backend = _dict_value(cgc.get("backend"))
        network = _dict_value(backend.get("network"))
        if network.get("name"):
            names.add(str(network["name"]))
    return names


def _cleanup_provider_settings(settings: dict[str, object]) -> None:
    for container in sorted(_settings_provider_containers(settings)):
        subprocess.run(
            ["docker", "rm", "-f", container],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    for network in sorted(_settings_networks(settings)):
        subprocess.run(
            ["docker", "network", "rm", network],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def _watchers_status(coordination_root: Path, settings_path: Path) -> dict[str, object]:
    return lifecycle.watchers_run(
        argparse.Namespace(
            coordination_root=coordination_root,
            from_settings=settings_path,
            dry_run=False,
            timeout=_provider_timeout(),
            json=True,
        ),
        "status",
    )


def test_worktree_and_benchmark_providers_run_end_to_end(tmp_path: Path) -> None:
    if not _docker_available():
        pytest.skip("docker is not available")

    repo_id = "repo-a"
    instance = f"itest-{uuid.uuid4().hex[:10]}"
    root = tmp_path / "source"
    code_root = root / "workspace" / repo_id
    coordination_root = root / "ar-coordination"
    memory_root = coordination_root / "memory-repos" / f"ar-{repo_id}"
    code_commit = _init_git_repo(
        code_root, {"README.md": "# Repo A\n", "pkg/app.py": "VALUE = 1\n"}
    )
    _init_memory_repo(memory_root, repo_id, code_commit)
    settings_path = root / ".codex" / "mcp" / "settings.json"
    _write_mcp_settings(settings_path, root=root, instance_id=instance)
    config = load_config(settings_path)
    source_settings = lifecycle_settings_from_config(config)
    source_settings_path = tmp_path / "source-provider-settings.json"
    source_settings_path.write_text(json.dumps(source_settings, indent=2) + "\n", encoding="utf-8")
    cleanup_settings = [source_settings]

    try:
        source_payload = provider_setup.run_provider_setup(
            provider_setup.ProviderSetupRequest(
                action="prepare",
                coordination_root=coordination_root,
                settings_path=source_settings_path,
                timeout=_provider_timeout(),
                dry_run=False,
            )
        )
        assert source_payload["ok"], json.dumps(source_payload, indent=2)

        worktree_payload = worktree_start_tool(
            config,
            repo_id=repo_id,
            task_name="Provider Integration Worktree",
            worktree_name="provider-integration",
            workflow_kind="light-task",
            dry_run=False,
        )
        assert worktree_payload["state"] == "started", json.dumps(worktree_payload, indent=2)
        # Provider setup now runs on a background thread (GitHub #53): the start
        # returns `starting` plus a progress file, and readiness is polled.
        providers_block = worktree_payload["providers"]
        assert providers_block["state"] == "starting", json.dumps(worktree_payload, indent=2)
        progress_path = Path(providers_block["progressFile"])
        deadline = time.monotonic() + _provider_timeout()
        progress: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            progress = read_setup_progress(progress_path)
            if progress is not None and progress["state"] != "running":
                break
            time.sleep(2)
        assert progress is not None and progress["state"] == "ok", json.dumps(
            progress or {"error": "background provider setup never finished"}, indent=2
        )
        worktree_provider_state = Path(progress["summary"]["providerStateFile"])
        worktree_state = json.loads(worktree_provider_state.read_text(encoding="utf-8"))
        worktree_settings_info = worktree_state["isolatedProviderSettings"]
        worktree_settings_path = Path(worktree_settings_info["path"])
        worktree_settings = json.loads(worktree_settings_path.read_text(encoding="utf-8"))
        cleanup_settings.append(worktree_settings)
        assert set(worktree_settings_info["providers"]) == {
            "codegraphcontext-code",
            "grepai-memory",
        }
        worktree_status = _watchers_status(coordination_root, worktree_settings_path)
        assert worktree_status["enabled"] == {
            "grepai-memory": True,
            "codegraphcontext-code": True,
        }
        assert worktree_status["ok"], json.dumps(worktree_status, indent=2)

        benchmark_coordination = tmp_path / "benchmark" / "ar-coordination"
        benchmark_repo = tmp_path / "benchmark" / "workspace" / repo_id
        benchmark_memory = benchmark_coordination / "memory-repos" / f"ar-{repo_id}"
        shutil.copytree(code_root, benchmark_repo, ignore=shutil.ignore_patterns(".git"))
        shutil.copytree(memory_root, benchmark_memory, ignore=shutil.ignore_patterns(".git"))
        benchmark_case = benchmark_runner.BenchmarkCase(
            Path("case.json"),
            {
                "id": "provider-integration",
                "repository": {"name": repo_id},
                "memoryRepository": {"name": f"ar-{repo_id}"},
                "workspace": {"fixturePath": "provider-integration"},
            },
        )
        benchmark_settings = benchmark_runner.benchmark_lifecycle_settings(
            case=benchmark_case,
            coordination_root=benchmark_coordination,
            source_repo_root=benchmark_repo,
            memory_repo=benchmark_memory,
            provider_ids=("grepai-memory", "codegraphcontext-code"),
        )
        cleanup_settings.append(benchmark_settings)
        benchmark_settings_path = tmp_path / "benchmark-provider-settings.json"
        benchmark_settings_path.write_text(
            json.dumps(benchmark_settings, indent=2) + "\n",
            encoding="utf-8",
        )
        benchmark_runner.prepare_configured_providers(
            benchmark_case,
            benchmark_coordination,
            benchmark_repo,
            benchmark_memory,
            dry_run=False,
            provider_timeout=_provider_timeout(),
            provider_ids=("grepai-memory", "codegraphcontext-code"),
            cgc_seed_source_coordination_root=coordination_root,
            cgc_seed_repo_id=repo_id,
            provider_seed_source_settings_path=source_settings_path,
        )
        benchmark_status = _watchers_status(benchmark_coordination, benchmark_settings_path)
        assert benchmark_status["enabled"] == {
            "grepai-memory": True,
            "codegraphcontext-code": True,
        }
        assert benchmark_status["ok"], json.dumps(benchmark_status, indent=2)
    finally:
        for settings in reversed(cleanup_settings):
            _cleanup_provider_settings(settings)
