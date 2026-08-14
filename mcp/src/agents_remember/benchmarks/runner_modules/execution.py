from __future__ import annotations

import concurrent.futures
import json
import shutil
import subprocess
import time
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

from agents_remember.benchmarks.runner_modules.analysis import analyze_run_root, write_summary
from agents_remember.benchmarks.runner_modules.constants import (
    BENCHMARK_ROOT_MARKER,
    CODEX_BENCHMARK_SANDBOX,
    CODEX_BENCHMARK_SANDBOX_MODES,
    CODEX_BENCHMARK_SCOPE,
    CODEX_EXECUTABLE_NAME,
    CODEX_EXECUTABLE_RESOLUTION,
    CODEX_HARNESS_DIR,
    CODEX_SANDBOX_DEFAULT,
)
from agents_remember.benchmarks.runner_modules.manifest import (
    case_prompt,
    manifest_relative_path,
    prompt_variant,
    selected_provider_ids,
)
from agents_remember.benchmarks.runner_modules.models import (
    BenchmarkCase,
    BenchmarkPreparation,
    BenchmarkRun,
    BenchmarkRunRequest,
    BenchmarkTask,
)
from agents_remember.benchmarks.runner_modules.workspace import prepare_case


def run_id() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H-%M-%S")


class CodexExecutableNotFound(RuntimeError):
    """Raised when the Codex benchmark runner cannot resolve Codex from PATH."""


def resolve_codex_executable() -> str:
    resolved = shutil.which(CODEX_EXECUTABLE_NAME)
    if resolved:
        return resolved
    raise CodexExecutableNotFound(
        "codex executable was not found on PATH; benchmark execution resolves "
        "Codex only from the MCP server process PATH"
    )


def validate_codex_sandbox(codex_sandbox: str) -> str:
    if codex_sandbox not in CODEX_BENCHMARK_SANDBOX_MODES:
        allowed = ", ".join(CODEX_BENCHMARK_SANDBOX_MODES)
        raise ValueError(
            f"unsupported Codex benchmark sandbox {codex_sandbox!r}; expected {allowed}"
        )
    return codex_sandbox


def codex_sandbox_argument(codex_sandbox: str) -> str | None:
    sandbox = validate_codex_sandbox(codex_sandbox)
    if sandbox == CODEX_SANDBOX_DEFAULT:
        return None
    return sandbox


def codex_config_literal(value: object, *, config_path: Path) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        items = (codex_config_literal(item, config_path=config_path) for item in value)
        return "[" + ",".join(items) + "]"
    raise RuntimeError(f"unsupported benchmark Codex config value in {config_path}: {value!r}")


def benchmark_mcp_config_overrides(cwd: Path) -> list[str]:
    config_path = cwd / CODEX_HARNESS_DIR / "config.toml"
    if not config_path.exists():
        return []

    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    servers = config.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise RuntimeError(f"benchmark Codex config has invalid mcp_servers table: {config_path}")

    overrides: list[str] = []
    scalar_keys = (
        "command",
        "args",
        "startup_timeout_sec",
        "cwd",
        "url",
        "bearer_token_env_var",
        "env_vars",
    )
    for server_name, server_config in servers.items():
        if not isinstance(server_config, dict):
            raise RuntimeError(
                f"benchmark Codex config has invalid MCP server {server_name!r}: {config_path}"
            )
        server_prefix = f"mcp_servers.{server_name}"
        for key in scalar_keys:
            if key in server_config:
                literal = codex_config_literal(server_config[key], config_path=config_path)
                overrides.extend(["-c", f"{server_prefix}.{key}={literal}"])
        env = server_config.get("env", {})
        if env:
            if not isinstance(env, dict):
                raise RuntimeError(
                    f"benchmark Codex config has invalid env table for {server_name!r}: {config_path}"
                )
            for env_name, env_value in env.items():
                literal = codex_config_literal(env_value, config_path=config_path)
                overrides.extend(["-c", f"{server_prefix}.env.{env_name}={literal}"])
    return overrides


def codex_execution_policy(
    resolved_executable: str | None = None,
    *,
    codex_sandbox: str = CODEX_BENCHMARK_SANDBOX,
) -> dict[str, Any]:
    sandbox = validate_codex_sandbox(codex_sandbox)
    sandbox_argument = codex_sandbox_argument(sandbox)
    policy: dict[str, Any] = {
        "scope": CODEX_BENCHMARK_SCOPE,
        "benchmarkOnly": True,
        "hostProcess": True,
        "executable": CODEX_EXECUTABLE_NAME,
        "resolution": CODEX_EXECUTABLE_RESOLUTION,
        "pathVariable": CODEX_EXECUTABLE_RESOLUTION,
        "sandbox": sandbox,
        "sandboxArgument": sandbox_argument or "omitted",
        "sandboxModes": list(CODEX_BENCHMARK_SANDBOX_MODES),
        "approvalPolicy": "never",
        "genericExecutableOverride": False,
        "arbitraryShell": False,
    }
    if resolved_executable:
        policy["resolvedExecutable"] = resolved_executable
    return policy


def codex_command(
    cwd: Path,
    final_message_path: Path,
    *,
    codex_sandbox: str = CODEX_BENCHMARK_SANDBOX,
) -> list[str]:
    resolved_executable = resolve_codex_executable()
    command = [
        resolved_executable,
        "exec",
        "--json",
        "--ephemeral",
        "--cd",
        str(cwd),
        "--skip-git-repo-check",
        "--output-last-message",
        str(final_message_path),
        "-c",
        'approval_policy="never"',
        "-c",
        f"project_root_markers=['{BENCHMARK_ROOT_MARKER}']",
    ]
    command.extend(benchmark_mcp_config_overrides(cwd))
    sandbox_argument = codex_sandbox_argument(codex_sandbox)
    if sandbox_argument is not None:
        command[7:7] = ["--sandbox", sandbox_argument]
    return command


def write_metadata(path: Path, metadata: dict[str, Any], dry_run: bool) -> None:
    if dry_run:
        print(f"Would write metadata {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_one(run: BenchmarkRun, task: BenchmarkTask) -> None:
    case = run.case
    dry_run = run.dry_run
    prompt_id = str(task.prompt["id"])
    variant_id = str(task.variant["id"])
    repetition = task.repetition
    prompt_path = case.path.parent / manifest_relative_path(
        task.variant["promptPath"],
        f"{case.case_id}.{prompt_id}.{variant_id}.promptPath",
    )
    prompt_text = prompt_path.read_text(encoding="utf-8")
    cwd = run.benchmarks_root / manifest_relative_path(
        task.variant["cwd"],
        f"{case.case_id}.{prompt_id}.{variant_id}.cwd",
    )
    run_prefix = run.output_root / prompt_id / variant_id / f"run-{repetition:03d}"
    jsonl_path = run_prefix.with_suffix(".jsonl")
    stderr_path = run_prefix.with_suffix(".stderr")
    metadata_path = run_prefix.with_suffix(".metadata.json")
    final_message_path = run_prefix.with_suffix(".final.md")
    command = codex_command(cwd, final_message_path, codex_sandbox=run.codex_sandbox)
    execution_policy = codex_execution_policy(command[0], codex_sandbox=run.codex_sandbox)

    if dry_run:
        print(f"Would write JSONL to {jsonl_path}")
        print(f"Would write stderr to {stderr_path}")
        print(f"Would write final message to {final_message_path}")
        print(
            "Benchmark host execution: "
            f"{CODEX_BENCHMARK_SCOPE}; {CODEX_EXECUTABLE_NAME} resolved from "
            f"{CODEX_EXECUTABLE_RESOLUTION} to {command[0]}"
        )
        print(f"Benchmark sandbox: {execution_policy['sandbox']}")
        print("Would run: " + " ".join(command) + " <prompt via stdin>")
        write_metadata(
            metadata_path,
            {
                "case": case.case_id,
                "prompt": prompt_id,
                "variant": variant_id,
                "repetition": repetition,
                "cwd": str(cwd),
                "finalMessagePath": str(final_message_path),
                "dryRun": True,
                "codexExecutionPolicy": execution_policy,
            },
            dry_run=True,
        )
        return

    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with (
        jsonl_path.open("w", encoding="utf-8") as stdout,
        stderr_path.open("w", encoding="utf-8") as stderr,
    ):
        completed = subprocess.run(
            command,
            input=prompt_text,
            stdout=stdout,
            stderr=stderr,
            text=True,
            check=False,
        )
    duration = time.monotonic() - started
    write_metadata(
        metadata_path,
        {
            "case": case.case_id,
            "prompt": prompt_id,
            "variant": variant_id,
            "repetition": repetition,
            "cwd": str(cwd),
            "command": [*command, "<prompt via stdin>"],
            "codexExecutionPolicy": execution_policy,
            "durationSeconds": round(duration, 3),
            "exitCode": completed.returncode,
            "finalMessagePath": str(final_message_path),
            "jsonlPath": str(jsonl_path),
            "stderrPath": str(stderr_path),
        },
        dry_run=False,
    )


def maybe_prepare_case(
    preparation: BenchmarkPreparation,
    case: BenchmarkCase,
    *,
    prompt_id: str | None,
    variant_id: str | None,
    skip_prepare: bool,
) -> None:
    if skip_prepare:
        return
    prepare_case(
        preparation,
        case,
        provider_ids=selected_provider_ids(case, prompt_id=prompt_id, variant_id=variant_id),
    )


def create_output_root(benchmarks_root: Path, case: BenchmarkCase, dry_run: bool) -> Path:
    output_root = benchmarks_root / "user-runs" / case.case_id / run_id()
    if dry_run:
        print(f"Would create run output root {output_root}")
    else:
        output_root.mkdir(parents=True, exist_ok=False)
    return output_root


def benchmark_task_batches(
    case: BenchmarkCase,
    *,
    prompt_id: str | None,
    variant_id: str | None,
    repetitions: int | None,
) -> tuple[list[list[BenchmarkTask]], int]:
    task_batches: list[list[BenchmarkTask]] = []
    default_jobs = 1
    for prompt in case_prompt(case, prompt_id):
        variants = prompt_variant(prompt, variant_id)
        default_jobs = max(default_jobs, len(variants))
        task_batches.extend(task_batches_for_prompt(prompt, variants, repetitions))
    return task_batches, default_jobs


def task_batches_for_prompt(
    prompt: dict[str, Any],
    variants: list[dict[str, Any]],
    repetitions: int | None,
) -> list[list[BenchmarkTask]]:
    prompt_runs = int(repetitions or prompt.get("runs") or 3)
    return [
        [
            BenchmarkTask(prompt=prompt, variant=variant, repetition=repetition)
            for variant in variants
        ]
        for repetition in range(1, prompt_runs + 1)
    ]


def run_dry_batches(run: BenchmarkRun, task_batches: list[list[BenchmarkTask]]) -> None:
    for task_batch in task_batches:
        for task in task_batch:
            run_one(run, task)


def submit_task_batch(
    executor: concurrent.futures.Executor,
    run: BenchmarkRun,
    task_batch: list[BenchmarkTask],
) -> dict[concurrent.futures.Future[None], BenchmarkTask]:
    return {executor.submit(run_one, run, task): task for task in task_batch}


def completed_task_failures(
    future_to_task: dict[concurrent.futures.Future[None], BenchmarkTask],
) -> list[str]:
    failures: list[str] = []
    for future in concurrent.futures.as_completed(future_to_task):
        task = future_to_task[future]
        try:
            future.result()
        except Exception as error:
            failures.append(
                f"{task.prompt.get('id')}/{task.variant.get('id')}/"
                f"run-{task.repetition:03d}: {error}"
            )
    return failures


def run_task_batches(
    run: BenchmarkRun,
    task_batches: list[list[BenchmarkTask]],
    *,
    max_workers: int,
) -> list[str]:
    failures: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        for task_batch in task_batches:
            future_to_task = submit_task_batch(executor, run, task_batch)
            failures.extend(completed_task_failures(future_to_task))
    return failures


def raise_for_failures(failures: list[str]) -> None:
    if failures:
        joined = "\n".join(f"  - {failure}" for failure in failures)
        raise RuntimeError(f"benchmark run completed with failed subprocesses:\n{joined}")


def run_case(request: BenchmarkRunRequest, case: BenchmarkCase) -> Path:
    preparation = request.preparation
    benchmarks_root = preparation.benchmarks_root
    dry_run = preparation.dry_run
    maybe_prepare_case(
        preparation,
        case,
        prompt_id=request.prompt,
        variant_id=request.variant,
        skip_prepare=request.skip_prepare,
    )
    output_root = create_output_root(benchmarks_root, case, dry_run)
    task_batches, default_jobs = benchmark_task_batches(
        case,
        prompt_id=request.prompt,
        variant_id=request.variant,
        repetitions=request.repetitions,
    )
    run = BenchmarkRun(
        benchmarks_root=benchmarks_root,
        case=case,
        output_root=output_root,
        dry_run=dry_run,
        codex_sandbox=request.codex_sandbox,
    )
    if dry_run:
        run_dry_batches(run, task_batches)
        return output_root

    max_workers = request.jobs or default_jobs
    if max_workers < 1:
        raise RuntimeError("--jobs must be greater than zero")

    failures = run_task_batches(run, task_batches, max_workers=max_workers)
    write_summary(output_root, analyze_run_root(output_root))
    raise_for_failures(failures)
    return output_root
