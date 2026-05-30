from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
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
from agents_remember.benchmarks.runner_modules.models import BenchmarkCase
from agents_remember.mcp.config import (
    McpRuntimeConfig,
    ProviderScope,
    RepositoryScope,
    provider_runtime_name,
)
from agents_remember.providers import provider_setup
from agents_remember.providers.identity import provider_instance_id
from agents_remember.providers.settings import lifecycle_settings_from_config


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


def benchmark_mcp_settings_payload(
    *,
    repo_id: str,
    coordination_root: Path,
    workspace_root: Path,
    provider_ids: tuple[str, ...],
    provider_timeout: int,
    provider_instance: str | None = None,
) -> dict[str, Any]:
    provider_settings = {"scope": "benchmark"}
    if provider_instance is not None:
        provider_settings["instanceId"] = provider_instance
    return {
        "version": 1,
        "coordinationRoot": coordination_root.resolve().as_posix(),
        "workspaceRoot": workspace_root.resolve().as_posix(),
        "repositories": {repo_id: {}},
        "providers": {provider_id: dict(provider_settings) for provider_id in provider_ids},
        "timeoutCaps": {
            "toolSeconds": 30,
            "providerSetupSeconds": provider_timeout,
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
    *,
    case: BenchmarkCase,
    workspace_root: Path,
    coordination_root: Path,
    source_repo_root: Path,
    memory_repo: Path,
    provider_ids: tuple[str, ...],
    provider_timeout: int,
    dry_run: bool,
) -> tuple[Path, Path]:
    repo_id = manifest_path_component(case.repository["name"], f"{case.case_id}.repository.name")
    expected_memory_repo = coordination_root / "memory-repos" / f"ar-{repo_id}"
    if source_repo_root.name != repo_id:
        raise RuntimeError(
            "benchmark MCP registration requires the source repository directory "
            f"to match the repository id {repo_id!r}: {source_repo_root}"
        )
    if memory_repo.resolve() != expected_memory_repo.resolve():
        raise RuntimeError(
            "benchmark MCP registration requires memory repository "
            f"{expected_memory_repo}; got {memory_repo}"
        )

    mcp_settings_path = benchmark_mcp_settings_path(workspace_root)
    agents_config_path = benchmark_agents_config_path(workspace_root)
    settings_payload = benchmark_mcp_settings_payload(
        repo_id=repo_id,
        coordination_root=coordination_root,
        workspace_root=source_repo_root.parent,
        provider_ids=provider_ids,
        provider_timeout=provider_timeout,
        provider_instance=provider_instance_id("benchmark", workspace_root),
    )
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


def default_cgc_seed_source_coordination_root(
    benchmarks_root: Path, target_coordination_root: Path
) -> Path | None:
    candidates: list[Path] = [benchmarks_root.parent]

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen or resolved == target_coordination_root.resolve():
            continue
        seen.add(resolved)
        if (resolved / "providers" / "runners" / "codegraphcontext").is_dir():
            return resolved
    return None


def benchmark_mcp_config(
    *,
    case: BenchmarkCase,
    coordination_root: Path,
    source_repo_root: Path,
    memory_repo: Path,
    provider_ids: tuple[str, ...],
) -> McpRuntimeConfig:
    repo_id = manifest_path_component(case.repository["name"], f"{case.case_id}.repository.name")
    workspace_root = coordination_root.parent.resolve()
    instance_id = provider_instance_id("benchmark", workspace_root)
    providers = {
        provider_id: ProviderScope(
            provider_id=provider_id,
            runtime_root=coordination_root
            / "providers"
            / "runners"
            / provider_runtime_name(provider_id)
            / instance_id,
            log_root=coordination_root
            / "logs"
            / "providers"
            / provider_runtime_name(provider_id)
            / instance_id,
            instance_id=instance_id,
            scope="benchmark",
        )
        for provider_id in provider_ids
    }
    return McpRuntimeConfig(
        config_path=coordination_root / ".benchmark-mcp-settings.generated.json",
        coordination_root=coordination_root.resolve(),
        workspace_root=workspace_root,
        transcript_root=coordination_root / "logs" / "mcp",
        repositories={
            repo_id: RepositoryScope(
                repo_id=repo_id,
                path=source_repo_root.resolve(),
                memory_root=memory_repo.resolve(),
            )
        },
        providers=providers,
        timeout_caps={},
    )


def benchmark_lifecycle_settings(
    *,
    case: BenchmarkCase,
    coordination_root: Path,
    source_repo_root: Path,
    memory_repo: Path,
    provider_ids: tuple[str, ...],
) -> dict[str, Any]:
    config = benchmark_mcp_config(
        case=case,
        coordination_root=coordination_root,
        source_repo_root=source_repo_root,
        memory_repo=memory_repo,
        provider_ids=provider_ids,
    )
    return lifecycle_settings_from_config(config)


def write_temp_provider_settings(settings: dict[str, Any]) -> Path:
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        suffix="-agents-remember-benchmark-provider-settings.json",
    ) as handle:
        path = Path(handle.name)
        json.dump(settings, handle, indent=2)
        handle.write("\n")
    return path


def prepare_configured_providers(
    case: BenchmarkCase,
    coordination_root: Path,
    source_repo_root: Path,
    memory_repo: Path,
    dry_run: bool,
    provider_timeout: int,
    *,
    provider_ids: tuple[str, ...],
    cgc_seed_source_coordination_root: Path | None,
    cgc_seed_repo_id: str,
    provider_seed_source_settings_path: Path | None = None,
) -> None:
    if not provider_ids:
        if dry_run:
            print(
                f"Would skip benchmark provider setup for {case.case_id}; no variants request providers"
            )
        return

    settings = benchmark_lifecycle_settings(
        case=case,
        coordination_root=coordination_root,
        source_repo_root=source_repo_root,
        memory_repo=memory_repo,
        provider_ids=provider_ids,
    )
    settings_path = write_temp_provider_settings(settings)
    if dry_run:
        print(
            "Would run provider setup service for "
            f"{coordination_root} with generated settings {settings_path}"
        )
    try:
        cgc_seed = provider_setup.CgcSeedOptions()
        if (
            "codegraphcontext-code" in provider_ids
            and cgc_seed_source_coordination_root is not None
        ):
            cgc_seed = provider_setup.CgcSeedOptions(
                source_coordination_root=cgc_seed_source_coordination_root,
                source_settings_path=provider_seed_source_settings_path,
                repo_id=cgc_seed_repo_id,
            )
        grepai_seed = provider_setup.GrepaiSeedOptions()
        if "grepai-memory" in provider_ids and cgc_seed_source_coordination_root is not None:
            grepai_seed = provider_setup.GrepaiSeedOptions(
                source_coordination_root=cgc_seed_source_coordination_root,
                source_settings_path=provider_seed_source_settings_path,
                project_id=cgc_seed_repo_id,
                target_memory_root=memory_repo,
            )
        payload = provider_setup.run_provider_setup(
            provider_setup.ProviderSetupRequest(
                action="prepare",
                coordination_root=coordination_root,
                settings_path=settings_path,
                timeout=provider_timeout,
                dry_run=dry_run,
                cgc_seed=cgc_seed,
                grepai_seed=grepai_seed,
            )
        )
    finally:
        with contextlib.suppress(OSError):
            settings_path.unlink()
    if not payload.get("ok"):
        raise RuntimeError(json.dumps(payload, indent=2))
