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
from agents_remember.application.worktree_tools import TaskIdentity, worktree_start_tool
from agents_remember.benchmarks import runner as benchmark_runner
from agents_remember.kernel.memory_ledger import create_initial_ledger, ledger_to_text
from agents_remember.mcp.config import load_config
from agents_remember.providers import lifecycle, provider_setup
from agents_remember.providers.settings import lifecycle_settings_from_config
from agents_remember.providers.setup_progress import read_setup_progress

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENTS_REMEMBER_PROVIDER_INTEGRATION") != "1",
    reason="set AGENTS_REMEMBER_PROVIDER_INTEGRATION=1 to run Docker provider workflow tests",
)


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_provider_workflow_integration.py:28).
def _run(  # pragma: no cover
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


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_provider_workflow_integration.py:45).
def _git(repo: Path, *args: str) -> str:  # pragma: no cover
    return _run(["git", *args], cwd=repo).stdout.strip()


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_provider_workflow_integration.py:49).
def _init_git_repo(repo: Path, files: dict[str, str]) -> str:  # pragma: no cover
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


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_provider_workflow_integration.py:63).
def _init_memory_repo(memory_repo: Path, repo_id: str, code_commit: str) -> str:  # pragma: no cover
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


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_provider_workflow_integration.py:87).
def _write_mcp_settings(path: Path, *, root: Path, instance_id: str) -> None:  # pragma: no cover
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


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_provider_workflow_integration.py:103).
def _provider_timeout() -> int:  # pragma: no cover
    return int(os.environ.get("AGENTS_REMEMBER_PROVIDER_INTEGRATION_TIMEOUT", "1800"))


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_provider_workflow_integration.py:107).
def _docker_available() -> bool:  # pragma: no cover
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


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_provider_workflow_integration.py:121).
def _dict_value(value: object) -> dict[str, Any]:  # pragma: no cover
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    return {}


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_provider_workflow_integration.py:127).
def _list_value(value: object) -> list[Any]:  # pragma: no cover
    if isinstance(value, list):
        return cast(list[Any], value)
    return []


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_provider_workflow_integration.py:133).
def _settings_provider_containers(settings: dict[str, object]) -> set[str]:  # pragma: no cover
    providers = _dict_value(_dict_value(settings.get("contextProviders")).get("providers"))
    names: set[str] = set()
    grepai = _dict_value(providers.get("grepai-memory"))
    if grepai:
        names.update(_grepai_containers(grepai))
    cgc = _dict_value(providers.get("codegraphcontext-code"))
    if cgc:
        names.update(_cgc_containers(cgc))
    return names


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_provider_workflow_integration.py:145).
def _grepai_containers(provider: dict[str, Any]) -> set[str]:  # pragma: no cover
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


# 260731-EFA-L7 R10: live-provider-gated helper; needs installed provider runtimes.
def _cgc_containers(provider: dict[str, Any]) -> set[str]:  # pragma: no cover
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


# 260731-EFA-L7 R10: live-provider-gated helper; needs installed provider runtimes.
def _settings_networks(settings: dict[str, object]) -> set[str]:  # pragma: no cover
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


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_provider_workflow_integration.py:197).
def _cleanup_provider_settings(settings: dict[str, object]) -> None:  # pragma: no cover
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


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_provider_workflow_integration.py:216).
def _watchers_status(
    coordination_root: Path, settings_path: Path
) -> dict[str, object]:  # pragma: no cover
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


# 260731-EFA-L7 R10: AR_RUN_*_WORKFLOW-gated test body; needs live provider stack.
def test_worktree_and_benchmark_providers_run_end_to_end(
    tmp_path: Path,
) -> None:  # pragma: no cover
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
            TaskIdentity(
                repo_id=repo_id,
                task_name="Provider Integration Worktree",
                worktree_name="provider-integration",
                workflow_kind="light-task",
            ),
        )
        assert worktree_payload["state"] == "started", json.dumps(worktree_payload, indent=2)
        progress = _await_background_provider_setup(worktree_payload)
        worktree_settings_path = _isolated_worktree_settings(progress, cleanup_settings)
        worktree_status = _watchers_status(coordination_root, worktree_settings_path)
        assert worktree_status["enabled"] == {
            "grepai-memory": True,
            "codegraphcontext-code": True,
        }
        assert worktree_status["ok"], json.dumps(worktree_status, indent=2)

        benchmark_status = _run_benchmark_provider_stack(
            tmp_path,
            repo_id=repo_id,
            code_root=code_root,
            memory_root=memory_root,
            cleanup_settings=cleanup_settings,
        )
        assert benchmark_status["enabled"] == {
            "grepai-memory": True,
            "codegraphcontext-code": True,
        }
        assert benchmark_status["ok"], json.dumps(benchmark_status, indent=2)
    finally:
        for settings in reversed(cleanup_settings):
            _cleanup_provider_settings(settings)


# 260731-EFA-L7 R10: live-provider-gated helper; needs installed provider runtimes.
def _await_background_provider_setup(
    worktree_payload: dict[str, Any],
) -> dict[str, Any]:  # pragma: no cover
    """Poll the progress file a started worktree hands back until setup stops running.

    Provider setup runs on a background thread (GitHub #53), so `worktree_start` returns
    `starting` plus a progress file rather than a finished stack; readiness is polled.
    """
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
    return progress


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_provider_workflow_integration.py:327).
def _isolated_worktree_settings(  # pragma: no cover
    progress: dict[str, Any], cleanup_settings: list[dict[str, Any]]
) -> Path:
    """The provider settings the worktree wrote for itself, with both providers isolated.

    Registered for teardown before the isolation is asserted, so a stack that came up wrong
    is still a stack that gets reclaimed.
    """
    worktree_state = json.loads(
        Path(progress["summary"]["providerStateFile"]).read_text(encoding="utf-8")
    )
    settings_info = worktree_state["isolatedProviderSettings"]
    settings_path = Path(settings_info["path"])
    cleanup_settings.append(json.loads(settings_path.read_text(encoding="utf-8")))
    assert set(settings_info["providers"]) == {"codegraphcontext-code", "grepai-memory"}
    return settings_path


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_provider_workflow_integration.py:345).
def _run_benchmark_provider_stack(  # pragma: no cover
    tmp_path: Path,
    *,
    repo_id: str,
    code_root: Path,
    memory_root: Path,
    cleanup_settings: list[dict[str, Any]],
) -> dict[str, object]:
    """Stand the same two providers up through the benchmark runner's own entry point.

    The benchmark copies the repo and memory pair into its own coordination root, so this
    proves the runner configures a stack of its own rather than reusing the worktree's.

    The settings are registered for teardown the moment they exist and before anything is
    started from them, so a failure part-way through still leaves containers to be reclaimed.
    """
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
    benchmark_workspace = benchmark_runner.BenchmarkWorkspace(
        case=benchmark_case,
        workspace_root=benchmark_coordination.parent,
        coordination_root=benchmark_coordination,
        source_repo_root=benchmark_repo,
        memory_repo=benchmark_memory,
        provider_ids=("grepai-memory", "codegraphcontext-code"),
    )
    benchmark_settings = benchmark_runner.benchmark_lifecycle_settings(benchmark_workspace)
    cleanup_settings.append(benchmark_settings)
    benchmark_settings_path = tmp_path / "benchmark-provider-settings.json"
    benchmark_settings_path.write_text(
        json.dumps(benchmark_settings, indent=2) + "\n",
        encoding="utf-8",
    )
    benchmark_runner.prepare_configured_providers(
        benchmark_workspace,
        dry_run=False,
        provider_timeout=_provider_timeout(),
    )
    return _watchers_status(benchmark_coordination, benchmark_settings_path)
