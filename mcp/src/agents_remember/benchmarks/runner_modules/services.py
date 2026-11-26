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
)
from agents_remember.benchmarks.runner_modules.models import (
    BenchmarkCase,
    BenchmarkPrepareRequest,
    BenchmarkRunOutcome,
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
    preparation = request.preparation
    benchmarks_root = preparation.benchmarks_root
    cases = select_cases(load_cases(benchmarks_root), request.target, request.case_id)

    def run_prepare() -> None:
        for case in cases:
            prepare_case(preparation, case)

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
        BenchmarkRunOutcome(
            cases=cases,
            output_roots=output_roots,
            messages=messages,
            codex_executable=resolved_codex_executable,
        ),
    )


def run_selected_cases(
    request: BenchmarkRunRequest,
    benchmarks_root: Path,
    cases: list[BenchmarkCase],
) -> list[Path]:
    del benchmarks_root
    output_roots: list[Path] = []
    for case in cases:
        output_roots.append(run_case(request, case))
    return output_roots


def benchmark_run_payload(
    request: BenchmarkRunRequest,
    benchmarks_root: Path,
    outcome: BenchmarkRunOutcome,
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
            outcome.codex_executable,
            codex_sandbox=request.codex_sandbox,
        ),
        "cases": [case.case_id for case in outcome.cases],
        "runOutputRoots": [path.as_posix() for path in outcome.output_roots],
        "messages": outcome.messages,
    }
