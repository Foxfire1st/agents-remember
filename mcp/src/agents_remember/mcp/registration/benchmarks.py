"""Codex benchmark tools: prepare the case workspaces, then run them."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.benchmarks.runner import CODEX_BENCHMARK_SANDBOX
from agents_remember.controllers.benchmark_tools import (
    BenchmarkPreparation,
    BenchmarkSelection,
    CodexBenchmarkRun,
)

from ..config import McpRuntimeConfig
from ..tools import codex_benchmark_prepare_payload, codex_benchmark_run_payload


def register_benchmark_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def codex_benchmark_prepare(
        target: str = "all",
        case_id: str | None = None,
        benchmarks_root: str | None = None,
        dry_run: bool = True,
        force_clone: bool = False,
        skill_exposure_mode: str = "copy",
        provider_timeout: int = 1800,
    ) -> dict[str, Any]:
        """Prepare resettable benchmark case workspaces (clones repos, materializes coordination).
        Defaults to dry_run=true because a real prepare clones repos and writes workspaces. Set
        dry_run=false to actually prepare."""
        return codex_benchmark_prepare_payload(
            config,
            selection=BenchmarkSelection(
                target=target, case_id=case_id, benchmarks_root=benchmarks_root
            ),
            preparation=BenchmarkPreparation(
                dry_run=dry_run,
                force_clone=force_clone,
                skill_exposure_mode=skill_exposure_mode,
                provider_timeout=provider_timeout,
            ),
        )

    @server.tool()
    def codex_benchmark_run(
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
        codex_sandbox: str = CODEX_BENCHMARK_SANDBOX,
    ) -> dict[str, Any]:
        """Run a Codex benchmark case (executes Codex agents in a sandbox). Refused unless the MCP
        settings enable benchmarks (benchmarksEnabled). Defaults to dry_run=true because a real run
        clones third-party repos and runs agents. codex_sandbox defaults to Codex's own 'default'
        sandbox; pass 'danger-full-access' to grant full host access (trusted local runs only)."""
        return codex_benchmark_run_payload(
            config,
            selection=BenchmarkSelection(
                target=target, case_id=case_id, benchmarks_root=benchmarks_root
            ),
            preparation=BenchmarkPreparation(
                dry_run=dry_run,
                force_clone=force_clone,
                skill_exposure_mode=skill_exposure_mode,
                provider_timeout=provider_timeout,
            ),
            run=CodexBenchmarkRun(
                prompt=prompt,
                variant=variant,
                repetitions=repetitions,
                jobs=jobs,
                skip_prepare=skip_prepare,
                codex_sandbox=codex_sandbox,
            ),
        )
