"""Application entry points for benchmark-facing MCP tools."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.benchmarks import runner as benchmark_runner
from agents_remember.install.assets import packaged_source_root
from agents_remember.kernel.authority import require_within_coordination
from agents_remember.mcp.config import McpRuntimeConfig, reload_provider_authority


@dataclass(frozen=True)
class BenchmarkSelection:
    """Which benchmark cases an operation acts on: the target group, one optional case id
    inside it, and the benchmarks root that holds them (defaulting to the coordination
    root's own benchmarks, then the packaged ones)."""

    target: str = "all"
    case_id: str | None = None
    benchmarks_root: str | None = None


@dataclass(frozen=True)
class BenchmarkPreparation:
    """How each selected case's worktree is built before it can run: whether the work is only
    planned, whether the case repo is re-cloned from scratch, how skills are exposed inside the
    case, and how long provider setup may take."""

    dry_run: bool = True
    force_clone: bool = False
    skill_exposure_mode: str = "copy"
    provider_timeout: int = 1800


@dataclass(frozen=True)
class CodexBenchmarkRun:
    """One Codex execution over the prepared cases: the prompt and variant under test, how many
    repetitions at what parallelism, whether preparation is reused rather than re-run, and the
    Codex sandbox policy the CLI runs under."""

    prompt: str | None = None
    variant: str | None = None
    repetitions: int | None = None
    jobs: int | None = None
    skip_prepare: bool = False
    codex_sandbox: str = benchmark_runner.CODEX_BENCHMARK_SANDBOX


ALL_CASES = BenchmarkSelection()
"""Every case in the default benchmarks root."""

DEFAULT_PREPARATION = BenchmarkPreparation()
"""Plan-only preparation with cached clones and copied skills."""

DEFAULT_RUN = CodexBenchmarkRun()
"""One repetition of every prepared case's default prompt under the default sandbox."""


def codex_benchmark_prepare_tool(
    config: McpRuntimeConfig,
    *,
    selection: BenchmarkSelection = ALL_CASES,
    preparation: BenchmarkPreparation = DEFAULT_PREPARATION,
) -> dict[str, Any]:
    if not config.benchmarks_enabled:
        return _benchmarks_disabled("codex_benchmark_prepare")
    with _benchmark_root_context(config, selection.benchmarks_root) as resolved_benchmarks_root:
        return benchmark_runner.prepare_benchmarks(
            benchmark_runner.BenchmarkPrepareRequest(
                benchmarks_root=resolved_benchmarks_root,
                target=selection.target,
                case_id=selection.case_id,
                dry_run=preparation.dry_run,
                skill_exposure_mode=preparation.skill_exposure_mode,
                force_clone=preparation.force_clone,
                provider_timeout=preparation.provider_timeout,
                allowed_provider_ids=_live_provider_ids(config),
            )
        )


def codex_benchmark_run_tool(
    config: McpRuntimeConfig,
    *,
    selection: BenchmarkSelection = ALL_CASES,
    preparation: BenchmarkPreparation = DEFAULT_PREPARATION,
    run: CodexBenchmarkRun = DEFAULT_RUN,
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
                codex_sandbox=run.codex_sandbox,
            ),
            "executable": benchmark_runner.CODEX_EXECUTABLE_NAME,
            "resolution": benchmark_runner.CODEX_EXECUTABLE_RESOLUTION,
            "recoveryAction": (
                "Install the Codex CLI or ensure `codex` is on the MCP server process PATH."
            ),
        }

    with _benchmark_root_context(config, selection.benchmarks_root) as resolved_benchmarks_root:
        result = benchmark_runner.run_codex_benchmark(
            benchmark_runner.BenchmarkRunRequest(
                benchmarks_root=resolved_benchmarks_root,
                target=selection.target,
                case_id=selection.case_id,
                prompt=run.prompt,
                variant=run.variant,
                repetitions=run.repetitions,
                jobs=run.jobs,
                dry_run=preparation.dry_run,
                skip_prepare=run.skip_prepare,
                skill_exposure_mode=preparation.skill_exposure_mode,
                force_clone=preparation.force_clone,
                provider_timeout=preparation.provider_timeout,
                codex_sandbox=run.codex_sandbox,
                allowed_provider_ids=_live_provider_ids(config),
            )
        )
    result["codexExecutable"] = codex_executable
    result["codexResolution"] = "PATH"
    return result


def _live_provider_ids(config: McpRuntimeConfig) -> tuple[str, ...]:
    """The live on-disk authority's provider ids (containment R1, 260707-HFX-L1).

    Benchmark provider synthesis is filtered to this set; a fail-closed read
    error yields an empty set, so no manifest can arm providers the developer
    has disabled on disk.
    """
    return tuple(sorted(reload_provider_authority(config).providers))


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
