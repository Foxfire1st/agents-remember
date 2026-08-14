"""Codex benchmark prepare/run payload builders."""

from __future__ import annotations

from typing import Any

from agents_remember.application.benchmark_tools import (
    ALL_CASES,
    DEFAULT_PREPARATION,
    DEFAULT_RUN,
    BenchmarkPreparation,
    BenchmarkSelection,
    CodexBenchmarkRun,
    codex_benchmark_prepare_tool,
    codex_benchmark_run_tool,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

from .base import _tool_payload


def codex_benchmark_prepare_payload(
    config: McpRuntimeConfig,
    *,
    selection: BenchmarkSelection = ALL_CASES,
    preparation: BenchmarkPreparation = DEFAULT_PREPARATION,
) -> dict[str, Any]:
    return _tool_payload(
        "codex_benchmark_prepare",
        codex_benchmark_prepare_tool(config, selection=selection, preparation=preparation),
    )


def codex_benchmark_run_payload(
    config: McpRuntimeConfig,
    *,
    selection: BenchmarkSelection = ALL_CASES,
    preparation: BenchmarkPreparation = DEFAULT_PREPARATION,
    run: CodexBenchmarkRun = DEFAULT_RUN,
) -> dict[str, Any]:
    return _tool_payload(
        "codex_benchmark_run",
        codex_benchmark_run_tool(config, selection=selection, preparation=preparation, run=run),
    )
