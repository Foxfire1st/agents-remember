"""Controllers for benchmark-facing MCP tools."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from agents_remember.benchmarks import runner as benchmark_runner
from agents_remember.controllers._guards import require_within_coordination
from agents_remember.install.assets import packaged_source_root
from agents_remember.mcp.config import McpRuntimeConfig


def codex_benchmark_prepare_tool(
    config: McpRuntimeConfig,
    *,
    target: str = "all",
    case_id: str | None = None,
    benchmarks_root: str | None = None,
    dry_run: bool = True,
    force_clone: bool = False,
    skill_exposure_mode: str = "copy",
    provider_timeout: int = 1800,
) -> dict[str, Any]:
    if not config.benchmarks_enabled:
        return _benchmarks_disabled("codex_benchmark_prepare")
    with _benchmark_root_context(config, benchmarks_root) as resolved_benchmarks_root:
        return benchmark_runner.prepare_benchmarks(
            benchmark_runner.BenchmarkPrepareRequest(
                benchmarks_root=resolved_benchmarks_root,
                target=target,
                case_id=case_id,
                dry_run=dry_run,
                skill_exposure_mode=skill_exposure_mode,
                force_clone=force_clone,
                provider_timeout=provider_timeout,
            )
        )


def codex_benchmark_run_tool(
    config: McpRuntimeConfig,
    *,
    target: str = "all",
    case_id: str | None = None,
    benchmarks_root: str | None = None,
    prompt: str | None = None,
    variant: str | None = None,
    repetitions: int | None = None,
    jobs: int | None = None,
    dry_run: bool = True,
    skip_prepare: bool = False,
    force_clone: bool = False,
    skill_exposure_mode: str = "copy",
    provider_timeout: int = 1800,
    codex_sandbox: str = benchmark_runner.CODEX_BENCHMARK_SANDBOX,
) -> dict[str, Any]:
    if not config.benchmarks_enabled:
        return _benchmarks_disabled("codex_benchmark_run")
    try:
        codex_executable = benchmark_runner.resolve_codex_executable()
    except benchmark_runner.CodexExecutableNotFound as error:
        return {
            "ok": False,
            "operation": "codex_benchmark_run",
            "error": str(error),
            "codexExecutionPolicy": benchmark_runner.codex_execution_policy(
                codex_sandbox=codex_sandbox,
            ),
            "executable": benchmark_runner.CODEX_EXECUTABLE_NAME,
            "resolution": benchmark_runner.CODEX_EXECUTABLE_RESOLUTION,
            "recoveryAction": (
                "Install the Codex CLI or ensure `codex` is on the MCP server process PATH."
            ),
        }

    with _benchmark_root_context(config, benchmarks_root) as resolved_benchmarks_root:
        result = benchmark_runner.run_codex_benchmark(
            benchmark_runner.BenchmarkRunRequest(
                benchmarks_root=resolved_benchmarks_root,
                target=target,
                case_id=case_id,
                prompt=prompt,
                variant=variant,
                repetitions=repetitions,
                jobs=jobs,
                dry_run=dry_run,
                skip_prepare=skip_prepare,
                skill_exposure_mode=skill_exposure_mode,
                force_clone=force_clone,
                provider_timeout=provider_timeout,
                codex_sandbox=codex_sandbox,
            )
        )
    result["codexExecutable"] = codex_executable
    result["codexResolution"] = "PATH"
    return result


@contextmanager
def _benchmark_root_context(config: McpRuntimeConfig, value: str | None) -> Iterator[Path]:
    if value:
        yield require_within_coordination(config, value, "benchmarks_root")
        return

    coordinator_benchmarks = config.coordination_root / "benchmarks"
    if (coordinator_benchmarks / "cases").is_dir():
        yield coordinator_benchmarks
        return

    with packaged_source_root() as source_root:
        yield source_root / "benchmarks"


def _benchmarks_disabled(operation: str) -> dict[str, Any]:
    return {
        "ok": False,
        "operation": operation,
        "error": (
            'benchmark tools are disabled; set "benchmarksEnabled": true in the MCP '
            "settings to enable them"
        ),
    }
