from __future__ import annotations

import contextlib
import io
from pathlib import Path
from typing import Any

from agents_remember.benchmarks.runner_modules.execution import (
    codex_execution_policy,
    resolve_codex_executable,
    run_case,
    validate_codex_sandbox,
)
from agents_remember.benchmarks.runner_modules.manifest import (
    load_cases,
    select_cases,
    selected_provider_ids,
)
from agents_remember.benchmarks.runner_modules.mcp_registration import (
    disarm_stale_benchmark_registrations,
)
from agents_remember.benchmarks.runner_modules.models import (
    BenchmarkPrepareRequest,
    BenchmarkRunRequest,
)
from agents_remember.benchmarks.runner_modules.workspace import prepare_case


def _capture_messages(run: Any) -> tuple[Any, list[str]]:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        value = run()
    messages = [line for line in stdout.getvalue().splitlines() if line]
    return value, messages


def prepare_benchmarks(request: BenchmarkPrepareRequest) -> dict[str, Any]:
    benchmarks_root = request.benchmarks_root.resolve()
    cases = select_cases(load_cases(benchmarks_root), request.target, request.case_id)

    def run_prepare() -> None:
        # Review B3: stale workspace registrations are the one authority file the
        # fleet kill-switch cannot reach — every touch of the benchmarks sweeps them.
        disarm_stale_benchmark_registrations(benchmarks_root, request.allowed_provider_ids)
        for case in cases:
            prepare_case(
                benchmarks_root,
                case,
                dry_run=request.dry_run,
                skill_exposure_mode=request.skill_exposure_mode,
                force_clone=request.force_clone,
                provider_timeout=request.provider_timeout,
                provider_ids=selected_provider_ids(case),
                allowed_provider_ids=request.allowed_provider_ids,
            )

    _, messages = _capture_messages(run_prepare)
    return {
        "ok": True,
        "operation": "codex_benchmark_prepare",
        "benchmarksRoot": benchmarks_root.as_posix(),
        "target": request.target,
        "caseId": request.case_id or "",
        "dryRun": request.dry_run,
        "cases": [case.case_id for case in cases],
        "messages": messages,
    }


def run_codex_benchmark(request: BenchmarkRunRequest) -> dict[str, Any]:
    benchmarks_root = request.benchmarks_root.resolve()
    cases = select_cases(load_cases(benchmarks_root), request.target, request.case_id)
    validate_codex_sandbox(request.codex_sandbox)
    resolved_codex_executable = resolve_codex_executable()
    output_roots, messages = _capture_messages(
        lambda: run_selected_cases(request, benchmarks_root, cases)
    )
    return benchmark_run_payload(
        request,
        benchmarks_root,
        cases,
        output_roots,
        messages,
        resolved_codex_executable,
    )


def run_selected_cases(
    request: BenchmarkRunRequest,
    benchmarks_root: Path,
    cases: list[Any],
) -> list[Path]:
    output_roots: list[Path] = []
    disarm_stale_benchmark_registrations(benchmarks_root, request.allowed_provider_ids)
    for case in cases:
        output_roots.append(
            run_case(
                benchmarks_root,
                case,
                prompt_id=request.prompt,
                variant_id=request.variant,
                repetitions=request.repetitions,
                jobs=request.jobs,
                dry_run=request.dry_run,
                skip_prepare=request.skip_prepare,
                skill_exposure_mode=request.skill_exposure_mode,
                force_clone=request.force_clone,
                provider_timeout=request.provider_timeout,
                codex_sandbox=request.codex_sandbox,
                allowed_provider_ids=request.allowed_provider_ids,
            )
        )
    return output_roots


def benchmark_run_payload(
    request: BenchmarkRunRequest,
    benchmarks_root: Path,
    cases: list[Any],
    output_roots: list[Path],
    messages: list[str],
    resolved_codex_executable: str,
) -> dict[str, Any]:
    return {
        "ok": True,
        "operation": "codex_benchmark_run",
        "benchmarksRoot": benchmarks_root.as_posix(),
        "target": request.target,
        "caseId": request.case_id or "",
        "prompt": request.prompt or "",
        "variant": request.variant or "",
        "dryRun": request.dry_run,
        "codexExecutionPolicy": codex_execution_policy(
            resolved_codex_executable,
            codex_sandbox=request.codex_sandbox,
        ),
        "cases": [case.case_id for case in cases],
        "runOutputRoots": [path.as_posix() for path in output_roots],
        "messages": messages,
    }
