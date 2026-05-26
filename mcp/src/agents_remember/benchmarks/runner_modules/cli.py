from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from agents_remember.benchmarks.runner_modules.analysis import (
    analyze_run_root,
    summary_markdown,
    write_summary,
)
from agents_remember.benchmarks.runner_modules.constants import (
    CODEX_BENCHMARK_SANDBOX,
    CODEX_BENCHMARK_SANDBOX_MODES,
    SKILL_EXPOSURE_MODES,
)
from agents_remember.benchmarks.runner_modules.manifest import load_cases
from agents_remember.benchmarks.runner_modules.models import (
    BenchmarkPrepareRequest,
    BenchmarkRunRequest,
)
from agents_remember.benchmarks.runner_modules.roots import benchmark_root_context
from agents_remember.benchmarks.runner_modules.services import (
    prepare_benchmarks,
    run_codex_benchmark,
)


def command_list(args: argparse.Namespace) -> int:
    with benchmark_root_context(args.benchmarks_root) as selected_benchmarks_root:
        benchmarks_root = selected_benchmarks_root.resolve()
        for case in load_cases(benchmarks_root):
            repository = case.repository
            repo_name = repository.get("name")
            repo_commit = repository.get("commit")
            print(
                f"{case.case_id}\t{case.data.get('status', 'unknown')}\t"
                f"{case.data.get('sizeBand', 'unknown')}\t{repo_name}@{repo_commit}"
            )
    return 0


def command_prepare(args: argparse.Namespace) -> int:
    with benchmark_root_context(args.benchmarks_root) as benchmarks_root:
        payload = prepare_benchmarks(
            BenchmarkPrepareRequest(
                benchmarks_root=benchmarks_root,
                target=args.target,
                case_id=args.case_id,
                dry_run=args.dry_run,
                skill_exposure_mode=args.skill_exposure_mode,
                force_clone=args.force_clone,
                provider_timeout=args.provider_timeout,
            )
        )
    for message in payload["messages"]:
        print(message)
    return 0


def command_run(args: argparse.Namespace) -> int:
    with benchmark_root_context(args.benchmarks_root) as benchmarks_root:
        payload = run_codex_benchmark(
            BenchmarkRunRequest(
                benchmarks_root=benchmarks_root,
                target=args.target,
                case_id=args.case_id,
                prompt=args.prompt,
                variant=args.variant,
                repetitions=args.repetitions,
                jobs=args.jobs,
                dry_run=args.dry_run,
                skip_prepare=args.skip_prepare,
                skill_exposure_mode=args.skill_exposure_mode,
                force_clone=args.force_clone,
                provider_timeout=args.provider_timeout,
                codex_sandbox=args.codex_sandbox,
            )
        )
    for message in payload["messages"]:
        print(message)
    for output_root in payload["runOutputRoots"]:
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
        default=None,
        help="Benchmark root. Defaults to packaged benchmark cases.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List benchmark cases.")
    list_parser.set_defaults(func=command_list)

    prepare_parser = subparsers.add_parser(
        "prepare", help="Prepare resettable benchmark workspaces."
    )
    prepare_parser.add_argument("target", choices=("all", "case"))
    prepare_parser.add_argument("case_id", nargs="?")
    prepare_parser.add_argument(
        "--skill-exposure-mode", choices=SKILL_EXPOSURE_MODES, default="copy"
    )
    prepare_parser.add_argument(
        "--force-clone",
        action="store_true",
        help="Discard existing benchmark repository checkouts before cloning.",
    )
    prepare_parser.add_argument(
        "--provider-timeout",
        type=int,
        default=1800,
        help="Seconds allowed for each benchmark provider install/index command.",
    )
    prepare_parser.add_argument("--dry-run", action="store_true")
    prepare_parser.set_defaults(func=command_prepare)

    run_parser = subparsers.add_parser("run", help="Run benchmark cases with codex exec --json.")
    run_parser.add_argument("target", choices=("all", "case"))
    run_parser.add_argument("case_id", nargs="?")
    run_parser.add_argument("--prompt", help="Run only one prompt id.")
    run_parser.add_argument("--variant", help="Run only one variant id.")
    run_parser.add_argument(
        "--repetitions", type=int, help="Override repetitions per prompt variant."
    )
    run_parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help="Maximum concurrent Codex runs. Defaults to the number of selected variants.",
    )
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument(
        "--skip-prepare", action="store_true", help="Use the existing workspace fixture state."
    )
    run_parser.add_argument("--skill-exposure-mode", choices=SKILL_EXPOSURE_MODES, default="copy")
    run_parser.add_argument(
        "--force-clone",
        action="store_true",
        help="Discard existing benchmark repository checkouts during preparation.",
    )
    run_parser.add_argument(
        "--provider-timeout",
        type=int,
        default=1800,
        help="Seconds allowed for each benchmark provider install/index command during preparation.",
    )
    run_parser.add_argument(
        "--codex-sandbox",
        choices=CODEX_BENCHMARK_SANDBOX_MODES,
        default=CODEX_BENCHMARK_SANDBOX,
        help=(
            "Codex benchmark sandbox mode. Use 'default' to omit --sandbox and "
            "let the Codex CLI use its configured default."
        ),
    )
    run_parser.set_defaults(func=command_run)

    analyze_parser = subparsers.add_parser(
        "analyze", help="Analyze an existing user-runs directory."
    )
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
