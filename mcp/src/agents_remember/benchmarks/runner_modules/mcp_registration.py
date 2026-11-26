from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from agents_remember.benchmarks.runner_modules.constants import (
    BENCHMARK_MCP_SERVER_NAME,
    BENCHMARK_MCP_SETTINGS_NAME,
    BENCHMARK_MCP_SOURCE_ENV,
    BENCHMARK_MCP_STARTUP_TIMEOUT_SECONDS,
    CODEX_HARNESS_DIR,
)
from agents_remember.benchmarks.runner_modules.manifest import manifest_path_component
from agents_remember.benchmarks.runner_modules.models import BenchmarkCase, BenchmarkWorkspace
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)


def benchmark_mcp_settings_path(workspace_root: Path) -> Path:
    return workspace_root / CODEX_HARNESS_DIR / "mcp" / BENCHMARK_MCP_SETTINGS_NAME


def benchmark_agents_config_path(workspace_root: Path) -> Path:
    return workspace_root / CODEX_HARNESS_DIR / "config.toml"


def benchmark_mcp_source_path() -> Path | None:
    value = os.environ.get(BENCHMARK_MCP_SOURCE_ENV)
    if not value:
        return None

    source_path = Path(value).expanduser().resolve()
    if not source_path.is_dir():
        raise RuntimeError(
            f"{BENCHMARK_MCP_SOURCE_ENV} must point to an existing mcp/src directory: {source_path}"
        )
    return source_path


def toml_string(value: object) -> str:
    return json.dumps(str(value))


def benchmark_repo_id(case: BenchmarkCase) -> str:
    return manifest_path_component(case.repository["name"], f"{case.case_id}.repository.name")


def benchmark_mcp_settings_payload(workspace: BenchmarkWorkspace) -> dict[str, Any]:
    return {
        "version": 1,
        "coordinationRoot": workspace.coordination_root.resolve().as_posix(),
        # The registration's workspace root is the directory that CONTAINS the
        # cloned repos, not the workspace root itself.
        "workspaceRoot": workspace.source_repo_root.parent.resolve().as_posix(),
        "repositories": {benchmark_repo_id(workspace.case): {}},
        "timeoutCaps": {
            "toolSeconds": 30,
        },
    }


def benchmark_agents_config_text(settings_path: Path) -> str:
    lines = [
        f"[mcp_servers.{BENCHMARK_MCP_SERVER_NAME}]",
        f"command = {toml_string(Path(sys.executable).resolve())}",
        "args = [",
        '  "-m",',
        '  "agents_remember.mcp",',
        '  "--config",',
        f"  {toml_string(settings_path.resolve())},",
        "]",
        f"startup_timeout_sec = {BENCHMARK_MCP_STARTUP_TIMEOUT_SECONDS}",
        "",
        f"[mcp_servers.{BENCHMARK_MCP_SERVER_NAME}.env]",
        'PYTHONIOENCODING = "utf-8"',
        'PYTHONUTF8 = "1"',
    ]
    source_path = benchmark_mcp_source_path()
    if source_path is not None:
        lines.append(f"PYTHONPATH = {toml_string(source_path)}")
    lines.append("")
    return "\n".join(lines)


def write_benchmark_mcp_registration(
    workspace: BenchmarkWorkspace,
    *,
    dry_run: bool,
) -> tuple[Path, Path]:
    repo_id = benchmark_repo_id(workspace.case)
    expected_memory_repo = workspace.coordination_root / "memory-repos" / f"ar-{repo_id}"
    if workspace.source_repo_root.name != repo_id:
        raise RuntimeError(
            "benchmark MCP registration requires the source repository directory "
            f"to match the repository id {repo_id!r}: {workspace.source_repo_root}"
        )
    if workspace.memory_repo.resolve() != expected_memory_repo.resolve():
        raise RuntimeError(
            "benchmark MCP registration requires memory repository "
            f"{expected_memory_repo}; got {workspace.memory_repo}"
        )

    mcp_settings_path = benchmark_mcp_settings_path(workspace.workspace_root)
    agents_config_path = benchmark_agents_config_path(workspace.workspace_root)
    settings_payload = benchmark_mcp_settings_payload(workspace)
    if dry_run:
        print(f"Would write benchmark MCP settings {mcp_settings_path}")
        print(f"Would write benchmark Codex MCP registration {agents_config_path}")
        return mcp_settings_path, agents_config_path

    mcp_settings_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_settings_path.write_text(
        json.dumps(settings_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    agents_config_path.parent.mkdir(parents=True, exist_ok=True)
    agents_config_path.write_text(
        benchmark_agents_config_text(mcp_settings_path),
        encoding="utf-8",
    )
    return mcp_settings_path, agents_config_path


def benchmark_mcp_config(workspace: BenchmarkWorkspace) -> McpRuntimeConfig:
    repo_id = benchmark_repo_id(workspace.case)
    coordination_root = workspace.coordination_root
    workspace_root = coordination_root.parent.resolve()
    return McpRuntimeConfig(
        config_path=coordination_root / ".benchmark-mcp-settings.generated.json",
        coordination_root=coordination_root.resolve(),
        workspace_root=workspace_root,
        transcript_root=coordination_root / "logs" / "mcp",
        repositories={
            repo_id: RepositoryScope(
                repo_id=repo_id,
                path=workspace.source_repo_root.resolve(),
                memory_root=workspace.memory_repo.resolve(),
            )
        },
        timeout_caps={},
    )
