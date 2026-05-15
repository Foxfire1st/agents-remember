#!/usr/bin/env python3
"""Run and analyze Agents Remember benchmark cases."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


AGENTS_MD_TARGETS = {
    Path("runtime/agents-md-files/coordinator/AGENTS.md"): Path("AGENTS.md"),
    Path("runtime/agents-md-files/system/AGENTS.md"): Path("system/AGENTS.md"),
    Path("runtime/agents-md-files/skills/AGENTS.md"): Path("skills/AGENTS.md"),
    Path("runtime/agents-md-files/tasks/AGENTS.md"): Path("tasks/AGENTS.md"),
}

TOKEN_KEYS = {
    "input_tokens": "input_tokens",
    "inputTokens": "input_tokens",
    "total_input_tokens": "input_tokens",
    "totalInputTokens": "input_tokens",
    "fresh_input_tokens": "fresh_input_tokens",
    "freshInputTokens": "fresh_input_tokens",
    "output_tokens": "output_tokens",
    "outputTokens": "output_tokens",
    "total_output_tokens": "output_tokens",
    "totalOutputTokens": "output_tokens",
    "reasoning_tokens": "reasoning_tokens",
    "reasoningTokens": "reasoning_tokens",
}

WORKSPACE_AGENTS_TEMPLATE = Path("templates/workspace-AGENTS.md")
COPYTREE_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")


def is_ignored_package_path(relative: Path) -> bool:
    return "__pycache__" in relative.parts or relative.suffix in {".pyc", ".pyo"}


@dataclass(frozen=True)
class BenchmarkCase:
    path: Path
    data: dict[str, Any]

    @property
    def case_id(self) -> str:
        return str(self.data["id"])

    @property
    def name(self) -> str:
        return str(self.data.get("name", self.case_id))

    @property
    def repository(self) -> dict[str, Any]:
        return dict(self.data["repository"])

    @property
    def memory_repository(self) -> dict[str, Any]:
        return dict(self.data.get("memoryRepository", {}))

    @property
    def workspace(self) -> dict[str, Any]:
        return dict(self.data["workspace"])

    @property
    def prompts(self) -> list[dict[str, Any]]:
        return list(self.data.get("prompts", []))


def default_benchmarks_root() -> Path:
    script_path = Path(__file__).resolve()
    for parent in script_path.parents:
        candidate = parent / "benchmarks"
        if (candidate / "cases").is_dir():
            return candidate
        if (parent / "runtime" / "scripts").is_dir() and (parent / "benchmarks" / "cases").is_dir():
            return parent / "benchmarks"
    return Path.cwd() / "benchmarks"


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object in {path}")
    return data


def load_cases(benchmarks_root: Path) -> list[BenchmarkCase]:
    cases_root = benchmarks_root / "cases"
    if not cases_root.is_dir():
        raise RuntimeError(f"benchmark cases directory not found: {cases_root}")

    cases: list[BenchmarkCase] = []
    for manifest in sorted(cases_root.glob("*/case.json")):
        case = BenchmarkCase(manifest, load_json(manifest))
        if case.data.get("schemaVersion") != 1:
            raise RuntimeError(f"unsupported benchmark schema in {manifest}")
        cases.append(case)
    return cases


def select_cases(cases: list[BenchmarkCase], target: str, case_id: str | None) -> list[BenchmarkCase]:
    if target == "all":
        return cases
    if not case_id:
        raise RuntimeError("case id is required when target is 'case'")
    for case in cases:
        if case.case_id == case_id:
            return [case]
    raise RuntimeError(f"benchmark case not found: {case_id}")


def copy_file(source: Path, destination: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"Would copy {source} -> {destination}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def replace_tree(source: Path, destination: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"Would replace {destination} from {source}")
        return
    if destination.exists() or destination.is_symlink():
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    shutil.copytree(source, destination, ignore=COPYTREE_IGNORE)


def copy_tree(source: Path, destination: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"Would copy tree {source} -> {destination}")
        return
    destination.mkdir(parents=True, exist_ok=True)
    for child in sorted(source.rglob("*")):
        relative = child.relative_to(source)
        if is_ignored_package_path(relative):
            continue
        target = destination / relative
        if child.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif child.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, target)


def render_template(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


def find_runtime_source() -> tuple[str, Path]:
    script_path = Path(__file__).resolve()
    for parent in script_path.parents:
        if (parent / "runtime" / "skills").is_dir() and (parent / "runtime" / "agents-md-files").is_dir():
            return "source", parent
        if (parent / "skills").is_dir() and (parent / "scripts").is_dir() and (parent / "AGENTS.md").is_file():
            return "installed", parent
    raise RuntimeError("could not locate Agents Remember runtime source")


def sync_runtime_assets(coordination_root: Path, dry_run: bool) -> None:
    mode, root = find_runtime_source()
    if dry_run:
        print(f"Would ensure directory {coordination_root}")
    else:
        coordination_root.mkdir(parents=True, exist_ok=True)
    if mode == "source":
        runtime_root = root / "runtime"
        replace_tree(runtime_root / "skills", coordination_root / "skills", dry_run)
        replace_tree(runtime_root / "scripts", coordination_root / "scripts", dry_run)
        for source_rel, target_rel in AGENTS_MD_TARGETS.items():
            copy_file(root / source_rel, coordination_root / target_rel, dry_run)
    else:
        replace_tree(root / "skills", coordination_root / "skills", dry_run)
        replace_tree(root / "scripts", coordination_root / "scripts", dry_run)
        for relative in (Path("AGENTS.md"), Path("system/AGENTS.md"), Path("tasks/AGENTS.md")):
            source = root / relative
            if source.exists():
                copy_file(source, coordination_root / relative, dry_run)

    for folder in ("memory-repos", "tasks", "worktrees", "notes", "temp"):
        path = coordination_root / folder
        if dry_run:
            print(f"Would ensure directory {path}")
        else:
            path.mkdir(parents=True, exist_ok=True)


def run_command(command: list[str], dry_run: bool, cwd: Path | None = None) -> None:
    printable = " ".join(command)
    if dry_run:
        location = f" in {cwd}" if cwd else ""
        print(f"Would run{location}: {printable}")
        return
    subprocess.run(command, cwd=cwd, check=True)


def prepare_repo(repository: dict[str, Any], repo_root: Path, dry_run: bool) -> None:
    url = str(repository["url"])
    commit = str(repository["commit"])
    if dry_run:
        print(f"Would ensure directory {repo_root.parent}")
    else:
        repo_root.parent.mkdir(parents=True, exist_ok=True)
    if not (repo_root / ".git").exists():
        run_command(["git", "clone", url, str(repo_root)], dry_run)
    else:
        run_command(["git", "-C", str(repo_root), "fetch", "--all", "--tags"], dry_run)
    run_command(["git", "-C", str(repo_root), "checkout", "--detach", commit], dry_run)
    run_command(["git", "-C", str(repo_root), "reset", "--hard", commit], dry_run)
    run_command(["git", "-C", str(repo_root), "clean", "-fdx"], dry_run)


def workspace_root(benchmarks_root: Path, case: BenchmarkCase) -> Path:
    return benchmarks_root / str(case.workspace["fixturePath"])


def repository_path(case: BenchmarkCase) -> Path:
    configured = case.workspace.get("repoRelativePath")
    if configured:
        return Path(str(configured))
    return Path("repos") / str(case.repository["name"])


def coordination_path(case: BenchmarkCase) -> Path:
    configured = case.workspace.get("coordinationRoot") or case.workspace.get("withOnboardingCoordinationRoot")
    if configured:
        return Path(str(configured))
    return Path("ar-coordination")


def memory_repo_name(case: BenchmarkCase) -> str:
    configured = case.memory_repository.get("name") or case.workspace.get("externalMemoryRepo")
    if not configured:
        raise RuntimeError(f"memory repository name is missing for case {case.case_id}")
    return str(configured)


def render_workspace_agents(benchmarks_root: Path, case: BenchmarkCase, dry_run: bool) -> Path:
    template_path = benchmarks_root / WORKSPACE_AGENTS_TEMPLATE
    if not template_path.is_file():
        raise RuntimeError(f"benchmark workspace template not found: {template_path}")

    repo_relative_path = repository_path(case).as_posix()
    coordination_root = coordination_path(case).as_posix()
    destination = workspace_root(benchmarks_root, case) / "AGENTS.md"
    rendered = render_template(
        template_path.read_text(encoding="utf-8"),
        {
            "case_id": case.case_id,
            "repository_name": str(case.repository["name"]),
            "repo_relative_path": repo_relative_path,
            "coordination_root": coordination_root,
            "memory_repository_name": memory_repo_name(case),
        },
    )
    if dry_run:
        print(f"Would render {template_path} -> {destination}")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(rendered, encoding="utf-8")
    return destination


def prepare_memory_repo(case: BenchmarkCase, coordination_root: Path, dry_run: bool) -> Path:
    memory_repo = coordination_root / "memory-repos" / memory_repo_name(case)
    memory_repository = case.memory_repository
    if memory_repository:
        prepare_repo(memory_repository, memory_repo, dry_run)
    elif not memory_repo.exists() and not dry_run:
        raise RuntimeError(f"workspace memory repo missing after preparation: {memory_repo}")
    return memory_repo


def prepare_case(benchmarks_root: Path, case: BenchmarkCase, dry_run: bool) -> None:
    repository = case.repository
    root = workspace_root(benchmarks_root, case)
    repo_root = root / repository_path(case)
    print(f"Preparing {case.case_id}")

    render_workspace_agents(benchmarks_root, case, dry_run)
    prepare_repo(repository, repo_root, dry_run)

    coordination_root = root / coordination_path(case)
    sync_runtime_assets(coordination_root, dry_run)
    memory_repo = prepare_memory_repo(case, coordination_root, dry_run)

    if dry_run:
        print(f"Would verify benchmark memory repo exists: {memory_repo}")


def case_prompt(case: BenchmarkCase, prompt_id: str | None) -> list[dict[str, Any]]:
    prompts = case.prompts
    if prompt_id is None:
        return prompts
    selected = [prompt for prompt in prompts if prompt.get("id") == prompt_id]
    if not selected:
        raise RuntimeError(f"prompt {prompt_id!r} not found in case {case.case_id}")
    return selected


def prompt_variant(prompt: dict[str, Any], variant_id: str | None) -> list[dict[str, Any]]:
    variants = list(prompt.get("variants", []))
    if variant_id is None:
        return variants
    selected = [variant for variant in variants if variant.get("id") == variant_id]
    if not selected:
        raise RuntimeError(f"variant {variant_id!r} not found in prompt {prompt.get('id')}")
    return selected


def run_id() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H-%M-%S")


def codex_command(codex_bin: str, cwd: Path, prompt_text: str) -> list[str]:
    return [
        codex_bin,
        "exec",
        "--json",
        "--cd",
        str(cwd),
        "--skip-git-repo-check",
        "--sandbox",
        "danger-full-access",
        "-c",
        'approval_policy="never"',
        prompt_text,
    ]


def write_metadata(path: Path, metadata: dict[str, Any], dry_run: bool) -> None:
    if dry_run:
        print(f"Would write metadata {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_one(
    *,
    benchmarks_root: Path,
    case: BenchmarkCase,
    prompt: dict[str, Any],
    variant: dict[str, Any],
    repetition: int,
    output_root: Path,
    codex_bin: str,
    dry_run: bool,
) -> None:
    prompt_path = case.path.parent / str(variant["promptPath"])
    prompt_text = prompt_path.read_text(encoding="utf-8")
    cwd = benchmarks_root / str(variant["cwd"])
    prompt_id = str(prompt["id"])
    variant_id = str(variant["id"])
    run_prefix = output_root / prompt_id / variant_id / f"run-{repetition:03d}"
    jsonl_path = run_prefix.with_suffix(".jsonl")
    stderr_path = run_prefix.with_suffix(".stderr")
    metadata_path = run_prefix.with_suffix(".metadata.json")
    command = codex_command(codex_bin, cwd, prompt_text)

    if dry_run:
        print(f"Would write JSONL to {jsonl_path}")
        print(f"Would write stderr to {stderr_path}")
        print("Would run: " + " ".join(command[:10]) + " <prompt>")
        write_metadata(
            metadata_path,
            {
                "case": case.case_id,
                "prompt": prompt_id,
                "variant": variant_id,
                "repetition": repetition,
                "cwd": str(cwd),
                "dryRun": True,
            },
            dry_run=True,
        )
        return

    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with jsonl_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        completed = subprocess.run(command, stdout=stdout, stderr=stderr, text=True, check=False)
    duration = time.monotonic() - started
    write_metadata(
        metadata_path,
        {
            "case": case.case_id,
            "prompt": prompt_id,
            "variant": variant_id,
            "repetition": repetition,
            "cwd": str(cwd),
            "command": command[:-1] + ["<prompt>"],
            "durationSeconds": round(duration, 3),
            "exitCode": completed.returncode,
            "jsonlPath": str(jsonl_path),
            "stderrPath": str(stderr_path),
        },
        dry_run=False,
    )


def run_case(
    benchmarks_root: Path,
    case: BenchmarkCase,
    *,
    prompt_id: str | None,
    variant_id: str | None,
    repetitions: int | None,
    codex_bin: str,
    dry_run: bool,
    skip_prepare: bool,
) -> Path:
    if not skip_prepare:
        prepare_case(benchmarks_root, case, dry_run=dry_run)

    current_run_id = run_id()
    output_root = benchmarks_root / "user-runs" / case.case_id / current_run_id
    if dry_run:
        print(f"Would create run output root {output_root}")
    else:
        output_root.mkdir(parents=True, exist_ok=False)

    for prompt in case_prompt(case, prompt_id):
        prompt_runs = int(repetitions or prompt.get("runs") or 3)
        for variant in prompt_variant(prompt, variant_id):
            for repetition in range(1, prompt_runs + 1):
                run_one(
                    benchmarks_root=benchmarks_root,
                    case=case,
                    prompt=prompt,
                    variant=variant,
                    repetition=repetition,
                    output_root=output_root,
                    codex_bin=codex_bin,
                    dry_run=dry_run,
                )

    if not dry_run:
        write_summary(output_root, analyze_run_root(output_root))
    return output_root


def walk_values(value: Any) -> list[Any]:
    values = [value]
    if isinstance(value, dict):
        for child in value.values():
            values.extend(walk_values(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(walk_values(child))
    return values


def collect_strings(value: Any, keys: set[str]) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in keys and isinstance(child, str):
                found.append(child)
            found.extend(collect_strings(child, keys))
    elif isinstance(value, list):
        for child in value:
            found.extend(collect_strings(child, keys))
    return found


def update_token_metrics(event: Any, metrics: dict[str, Any]) -> None:
    if isinstance(event, dict):
        for key, value in event.items():
            metric_key = TOKEN_KEYS.get(key)
            if metric_key and isinstance(value, int):
                metrics[metric_key] = max(int(metrics.get(metric_key, 0)), value)
            update_token_metrics(value, metrics)
    elif isinstance(event, list):
        for child in event:
            update_token_metrics(child, metrics)


def analyze_jsonl(path: Path) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "jsonl_size_bytes": path.stat().st_size,
        "event_count": 0,
        "command_event_count": 0,
        "errors": [],
        "final_answer": "",
    }
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            metrics["errors"].append(f"invalid jsonl line: {error}")
            continue
        metrics["event_count"] += 1
        raw = json.dumps(event, sort_keys=True)
        if any(marker in raw for marker in ("exec_command", "tool_call", '"cmd"', '"command"')):
            metrics["command_event_count"] += 1
        update_token_metrics(event, metrics)
        errors = collect_strings(event, {"error", "stderr"})
        metrics["errors"].extend(error for error in errors if error)
        text_candidates = collect_strings(event, {"content", "text", "message"})
        for candidate in text_candidates:
            if len(candidate) > len(str(metrics.get("final_answer", ""))):
                metrics["final_answer"] = candidate
    return metrics


def load_metadata(jsonl_path: Path) -> dict[str, Any]:
    metadata_path = jsonl_path.with_suffix(".metadata.json")
    if not metadata_path.exists():
        return {}
    try:
        return load_json(metadata_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def analyze_run_root(run_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for jsonl_path in sorted(run_root.rglob("*.jsonl")):
        metadata = load_metadata(jsonl_path)
        metrics = analyze_jsonl(jsonl_path)
        row = {
            "path": jsonl_path,
            "prompt": metadata.get("prompt", jsonl_path.parent.parent.name),
            "variant": metadata.get("variant", jsonl_path.parent.name),
            "repetition": metadata.get("repetition", jsonl_path.stem),
            "duration_seconds": metadata.get("durationSeconds"),
            "exit_code": metadata.get("exitCode"),
            **metrics,
        }
        rows.append(row)
    return rows


def range_text(values: list[Any]) -> str:
    clean = [value for value in values if isinstance(value, (int, float))]
    if not clean:
        return "n/a"
    low = min(clean)
    high = max(clean)
    if low == high:
        return str(low)
    return f"{low} - {high}"


def grouped(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    result: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row.get("prompt", "unknown")), str(row.get("variant", "unknown")))
        result.setdefault(key, []).append(row)
    return result


def summary_markdown(run_root: Path, rows: list[dict[str, Any]]) -> str:
    lines = [
        f"# Benchmark Summary: {run_root.name}",
        "",
        f"Run root: `{run_root}`",
        "",
    ]
    if not rows:
        lines.append("No JSONL files found.")
        lines.append("")
        return "\n".join(lines)

    numeric_keys = [
        "duration_seconds",
        "event_count",
        "command_event_count",
        "input_tokens",
        "fresh_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "jsonl_size_bytes",
    ]

    for (prompt, variant), group_rows in grouped(rows).items():
        lines.extend([f"## {prompt} / {variant}", "", "| Metric | Range |", "| --- | --- |"])
        for key in numeric_keys:
            lines.append(f"| {key} | {range_text([row.get(key) for row in group_rows])} |")
        exit_codes = sorted({str(row.get("exit_code")) for row in group_rows if row.get("exit_code") is not None})
        lines.append(f"| exit_code | {', '.join(exit_codes) if exit_codes else 'n/a'} |")
        lines.append("")
        lines.extend(["| Run | Duration | JSONL Size | Errors |", "| --- | ---: | ---: | --- |"])
        for row in group_rows:
            error_count = len(row.get("errors", []))
            lines.append(
                f"| {row.get('repetition')} | {row.get('duration_seconds', 'n/a')} | "
                f"{row.get('jsonl_size_bytes', 'n/a')} | {error_count} |"
            )
        lines.append("")

    return "\n".join(lines)


def write_summary(run_root: Path, rows: list[dict[str, Any]]) -> Path:
    summary_path = run_root / "summary.md"
    summary_path.write_text(summary_markdown(run_root, rows), encoding="utf-8")
    return summary_path


def command_list(args: argparse.Namespace) -> int:
    benchmarks_root = args.benchmarks_root.resolve()
    for case in load_cases(benchmarks_root):
        repository = case.repository
        print(
            f"{case.case_id}\t{case.data.get('status', 'unknown')}\t"
            f"{case.data.get('sizeBand', 'unknown')}\t{repository.get('name')}@{repository.get('commit')}"
        )
    return 0


def command_prepare(args: argparse.Namespace) -> int:
    benchmarks_root = args.benchmarks_root.resolve()
    cases = select_cases(load_cases(benchmarks_root), args.target, args.case_id)
    for case in cases:
        prepare_case(benchmarks_root, case, dry_run=args.dry_run)
    return 0


def command_run(args: argparse.Namespace) -> int:
    benchmarks_root = args.benchmarks_root.resolve()
    cases = select_cases(load_cases(benchmarks_root), args.target, args.case_id)
    for case in cases:
        output_root = run_case(
            benchmarks_root,
            case,
            prompt_id=args.prompt,
            variant_id=args.variant,
            repetitions=args.repetitions,
            codex_bin=args.codex_bin,
            dry_run=args.dry_run,
            skip_prepare=args.skip_prepare,
        )
        print(f"Run output: {output_root}")
    return 0


def command_analyze(args: argparse.Namespace) -> int:
    run_root = args.run_root.resolve()
    rows = analyze_run_root(run_root)
    markdown = summary_markdown(run_root, rows)
    if args.write_summary:
        path = write_summary(run_root, rows)
        print(f"Wrote {path}")
    else:
        print(markdown)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmarks-root",
        type=Path,
        default=default_benchmarks_root(),
        help="Benchmark root. Defaults to the installed or source benchmarks directory.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List benchmark cases.")
    list_parser.set_defaults(func=command_list)

    prepare_parser = subparsers.add_parser("prepare", help="Prepare resettable benchmark workspaces.")
    prepare_parser.add_argument("target", choices=("all", "case"))
    prepare_parser.add_argument("case_id", nargs="?")
    prepare_parser.add_argument("--dry-run", action="store_true")
    prepare_parser.set_defaults(func=command_prepare)

    run_parser = subparsers.add_parser("run", help="Run benchmark cases with codex exec --json.")
    run_parser.add_argument("target", choices=("all", "case"))
    run_parser.add_argument("case_id", nargs="?")
    run_parser.add_argument("--prompt", help="Run only one prompt id.")
    run_parser.add_argument("--variant", help="Run only one variant id.")
    run_parser.add_argument("--repetitions", type=int, help="Override repetitions per prompt variant.")
    run_parser.add_argument("--codex-bin", default="codex", help="Codex executable to run.")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--skip-prepare", action="store_true", help="Use the existing workspace fixture state.")
    run_parser.set_defaults(func=command_run)

    analyze_parser = subparsers.add_parser("analyze", help="Analyze an existing user-runs directory.")
    analyze_parser.add_argument("run_root", type=Path)
    analyze_parser.add_argument("--write-summary", action="store_true")
    analyze_parser.set_defaults(func=command_analyze)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    return 1


if __name__ == "__main__":
    sys.exit(main())
