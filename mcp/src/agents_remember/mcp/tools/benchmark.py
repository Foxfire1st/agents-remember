"""Codex benchmark prepare/run payload builders."""

from __future__ import annotations

from typing import Any

from agents_remember.benchmarks.runner import CODEX_BENCHMARK_SANDBOX
from agents_remember.controllers.benchmark_tools import (
    codex_benchmark_prepare_tool,
    codex_benchmark_run_tool,
)

from ..config import McpRuntimeConfig
from .base import _tool_payload


def codex_benchmark_prepare_payload(
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
    return _tool_payload(
        "codex_benchmark_prepare",
        codex_benchmark_prepare_tool(
            config,
            target=target,
            case_id=case_id,
            benchmarks_root=benchmarks_root,
            dry_run=dry_run,
            force_clone=force_clone,
            skill_exposure_mode=skill_exposure_mode,
            provider_timeout=provider_timeout,
        ),
    )


def codex_benchmark_run_payload(
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
    codex_sandbox: str = CODEX_BENCHMARK_SANDBOX,
) -> dict[str, Any]:
    return _tool_payload(
        "codex_benchmark_run",
        codex_benchmark_run_tool(
            config,
            target=target,
            case_id=case_id,
            benchmarks_root=benchmarks_root,
            prompt=prompt,
            variant=variant,
            repetitions=repetitions,
            jobs=jobs,
            dry_run=dry_run,
            skip_prepare=skip_prepare,
            force_clone=force_clone,
            skill_exposure_mode=skill_exposure_mode,
            provider_timeout=provider_timeout,
            codex_sandbox=codex_sandbox,
        ),
    )
