#!/usr/bin/env python3
"""Native Curator evidence tooling. Cases and output paths are private, caller-owned data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_evals.accounting import Attempt, compare_reports, summarize_attempts, summarize_usage
from agent_evals.adapters.execution import ACTOR_PROFILES
from agent_evals.artifacts import read_object, write_object
from agent_evals.fixtures import CASE_IDS, CaseBundle, inspect_cases
from agent_evals.preflight import write_preflight
from agent_evals.regrade import RegradeRequest, run_regrades
from agent_evals.text import capture_curator_text
from agent_evals.trials import TrialBatchRequest, run_trials


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="operation", required=True)
    preflight = sub.add_parser(
        "preflight", help="validate every case tree before actors, without models"
    )
    preflight.add_argument("--cases", type=Path, required=True)
    preflight.add_argument("--output", type=Path, required=True)
    census = sub.add_parser(
        "census", help="freeze unknown or stale Codex/Hermes attempts without spending"
    )
    census.add_argument("--batch", type=Path, required=True)
    census.add_argument("--evidence", type=Path, required=True)
    census.add_argument("--attempt", action="append", default=[])
    census.add_argument("--judge-executable", type=Path, required=True)
    regrade = sub.add_parser("regrade", help="inspect or judge a frozen census without actors")
    regrade.add_argument("--manifest", type=Path, required=True)
    action = regrade.add_mutually_exclusive_group(required=True)
    action.add_argument("--execute", action="store_true")
    action.add_argument("--dry-run", action="store_true")
    regrade.add_argument("--tooling-check", action="store_true")
    regrade.add_argument("--timeout", type=float, default=600)
    trials = sub.add_parser("trials", help="run native Curator trials and cross-family grading")
    for name in ("case", "evidence", "sandbox-home"):
        trials.add_argument(f"--{name}", type=Path, required=True)
    trials.add_argument(
        "--model",
        choices=(
            "codex",
            "hermes",
            "hermes-kimi",
            "hermes-gpt",
            *(profile.model for profile in ACTOR_PROFILES),
        ),
        required=True,
    )
    trials.add_argument(
        "--text", required=True, help="old or a captured JSON/plain instruction file"
    )
    trials.add_argument("--trials", type=int, choices=(1, 2, 3), default=3)
    trials.add_argument(
        "--phase",
        choices=("measurement", "final-full", "tooling-check"),
        default="measurement",
    )
    trials.add_argument("--code", type=Path, default=Path(__file__).resolve().parents[2])
    trials.add_argument(
        "--approver-ready",
        action="store_true",
        help="Architect-owned approver is already attached; does not approve prompts",
    )
    trials.add_argument("--timeout", type=float, default=3600)
    trials.add_argument(
        "--codex-access", type=Path, help="explicit access-only input; no store fallback"
    )
    trials.add_argument(
        "--actor-only",
        action="store_true",
        help="Hermes tooling-check turn only; no case grading or judge",
    )
    inspect = sub.add_parser(
        "inspect-cases", help="show recovery gaps without pretending cases ran"
    )
    inspect.add_argument("--cases", type=Path, required=True)
    summarize = sub.add_parser("summarize", help="report graded attempts and unknown coverage")
    for name in ("attempts", "output"):
        summarize.add_argument(f"--{name}", type=Path, required=True)
    for name in ("model", "text-signature", "batch-id"):
        summarize.add_argument(f"--{name}", required=True)
    compare = sub.add_parser(
        "compare", help="compare fixed populations from actual graded attempts"
    )
    for name in ("old-attempts", "new-attempts", "output"):
        compare.add_argument(f"--{name}", type=Path, required=True)
    for name in ("model", "old-text", "new-text", "old-batch", "new-batch"):
        compare.add_argument(f"--{name}", required=True)
    text = sub.add_parser(
        "capture-text", help="capture actual compiled Curator instructions/details"
    )
    text.add_argument("--code", type=Path, required=True)
    text.add_argument("--output", type=Path, required=True)
    return result


def _attempts(path: Path) -> list[Attempt]:
    return [Attempt.from_dict(item) for item in read_object(path)["attempts"]]


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.operation == "preflight":
        ids = (
            (CaseBundle.read(args.cases).case_id,)
            if (args.cases / "case.json").is_file()
            else CASE_IDS
        )
        report = write_preflight(args.cases, args.output, ids)
        print(json.dumps(report, indent=2))
        return 0 if report["pass"] else 1
    elif args.operation == "census":
        run_regrades(
            RegradeRequest(
                evidence=args.evidence,
                batch=args.batch,
                attempt_ids=tuple(args.attempt),
                judge_executable=args.judge_executable,
            )
        )
    elif args.operation == "regrade":
        result = run_regrades(
            RegradeRequest(
                manifest=args.manifest,
                execute=args.execute,
                dry_run=args.dry_run,
                tooling_check=args.tooling_check,
                timeout_seconds=args.timeout,
            )
        )
        return int(read_object(result).get("exitCode", 0))
    elif args.operation == "trials":
        output = run_trials(
            TrialBatchRequest(
                cases=args.case,
                case_ids=(CaseBundle.read(args.case).case_id,)
                if (args.case / "case.json").is_file()
                else CASE_IDS,
                model=args.model,
                text=args.text,
                trials=args.trials,
                evidence=args.evidence,
                sandbox_home=args.sandbox_home,
                candidate_code_root=args.code,
                approver_ready=args.approver_ready,
                phase=args.phase.replace("-", "_"),
                timeout_seconds=args.timeout,
                actor_only=args.actor_only,
                codex_access=args.codex_access,
            )
        )
        print(output)
    elif args.operation == "inspect-cases":
        print(json.dumps(inspect_cases(args.cases), indent=2))
    elif args.operation == "summarize":
        attempts = _attempts(args.attempts)
        report = summarize_attempts(
            attempts, model=args.model, text_signature=args.text_signature, batch_id=args.batch_id
        )
        write_object(
            args.output, {"report": report.to_dict(), "usage": summarize_usage(attempts).to_dict()}
        )
    elif args.operation == "compare":
        old = summarize_attempts(
            _attempts(args.old_attempts),
            model=args.model,
            text_signature=args.old_text,
            batch_id=args.old_batch,
        )
        new = summarize_attempts(
            _attempts(args.new_attempts),
            model=args.model,
            text_signature=args.new_text,
            batch_id=args.new_batch,
        )
        write_object(args.output, compare_reports(old, new).to_dict())
    elif args.operation == "capture-text":
        write_object(args.output, capture_curator_text(args.code))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
