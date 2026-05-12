#!/usr/bin/env python3
"""Adopt existing shared-memory onboarding as the first ledgered baseline.

Requires Python 3.10+ and git. Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = Path(__file__).resolve().parents[2]
SHARED_ROOT = CORE_ROOT / "_shared"
RESOLVER_PATH = CORE_ROOT / "C-08-ar-coordination-context-resolver" / "scripts" / "ar_coordination_context_resolver.py"
DRIFT_PATH = CORE_ROOT / "C-02-onboarding-drift-detection" / "scripts" / "check_onboarding_drift.py"
WORKTREE_MANAGER_PATH = CORE_ROOT / "C-09-git-worktree-manager" / "scripts" / "git_worktree_manager.py"
sys.path.insert(0, str(SHARED_ROOT))

from agents_remember.memory_ledger import LedgerError, load_ledger  # noqa: E402
from agents_remember.worktree_contract import default_contract  # noqa: E402


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


resolver = load_module("coordination_resolver", RESOLVER_PATH)
drift = load_module("check_onboarding_drift", DRIFT_PATH)
worktree_manager = load_module("git_worktree_manager", WORKTREE_MANAGER_PATH)


def resolve_context(args: argparse.Namespace):
    return resolver.resolve_coordination_context(
        code_repository_name=args.code_repository_name,
        workspace_root=args.workspace_root,
        requested_topology=args.topology,
        shared_root=args.shared_root,
        code_repository_root=args.code_repository_root,
    )


def run_drift(context, report_path: Path | None):
    if not context.onboarding_root.exists():
        raise RuntimeError(f"onboarding root does not exist: {context.onboarding_root}")
    rows = [
        drift.classify_sidecar_onboarding(path, context.code_repository_root, context.onboarding_root, context.storage)
        for path in drift.discover_onboarding_files(context.onboarding_root)
    ]
    rows.extend(
        drift.classify_inline_source(path, context.code_repository_root)
        for path in drift.discover_inline_onboarding_sources(context.code_repository_root, context.storage)
    )
    rows.sort(key=lambda row: (row.source_file, row.onboarding_file))
    report = drift.resolve_report_path(report_path, context.coordination_root, context.temp_root, context.code_repository_root, context.memory_root)
    drift.write_markdown_report(rows, report, context.code_repository_root, context.onboarding_root)
    return rows, report


def drift_summary(rows: list[object]) -> dict[str, int]:
    return drift.counts(rows)


def actionable_rows(rows: list[object]) -> list[object]:
    return [row for row in rows if row.classification in drift.ACTIONABLE_CLASSIFICATIONS]


def current_branch(repo: Path) -> str:
    return worktree_manager.current_branch(repo)


def head_commit(repo: Path, ref: str = "HEAD") -> str:
    return worktree_manager.head_commit(repo, ref)


def ledger_status(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"exists": False}
    try:
        ledger = load_ledger(path)
    except LedgerError as error:
        return {"exists": True, "valid": False, "error": str(error)}
    return {
        "exists": True,
        "valid": True,
        "lastVerifiedCodeCommit": ledger.last_verified_code_commit,
        "lastMemoryContentCommit": ledger.last_memory_content_commit,
    }


def base_payload(context, rows: list[object], report: Path) -> dict[str, object]:
    ledger = ledger_status(context.ledger_path)
    actionable = actionable_rows(rows)
    state = "ready"
    if ledger["exists"]:
        state = "already-ledgered"
    elif actionable:
        state = "blocked-drift"
    return {
        "state": state,
        "code_repository_name": context.code_repository_name,
        "topology": context.topology,
        "code_repository_root": context.code_repository_root.as_posix(),
        "memory_root": context.memory_root.as_posix(),
        "onboarding_root": context.onboarding_root.as_posix(),
        "ledger_path": context.ledger_path.as_posix(),
        "drift_report": report.as_posix(),
        "drift": {
            "counts": drift_summary(rows),
            "actionable": len(actionable),
        },
        "ledger": ledger,
    }


def command_status(args: argparse.Namespace) -> int:
    context = resolve_context(args)
    rows, report = run_drift(context, args.report)
    print(json.dumps(base_payload(context, rows, report), indent=2))
    return 0


def command_adopt(args: argparse.Namespace) -> int:
    context = resolve_context(args)
    if context.topology != "shared":
        raise RuntimeError("adoption requires shared topology")
    rows, report = run_drift(context, args.report)
    payload = base_payload(context, rows, report)
    if payload["ledger"]["exists"]:
        print(json.dumps(payload, indent=2))
        return 0
    if payload["drift"]["actionable"] and not args.accept_drift:
        payload["message"] = "actionable drift blocks adoption; refresh onboarding with C-05 or rerun with --accept-drift"
        print(json.dumps(payload, indent=2))
        return 2
    if args.dry_run:
        payload["state"] = "would-adopt"
        payload["accepted_drift"] = bool(args.accept_drift)
        print(json.dumps(payload, indent=2))
        return 0

    source_branch = args.source_branch or current_branch(context.code_repository_root)
    contract = default_contract(
        task_name=f"adopt-{context.code_repository_name}-memory-baseline",
        repo_name=context.code_repository_name,
        workflow_kind="adopt-memory-baseline",
        memory_mode="shared",
        coordination_root=context.coordination_root,
        code_repo_path=context.code_repository_root,
        code_source_branch=source_branch,
        code_work_branch=args.work_branch or source_branch,
        code_base_commit=head_commit(context.code_repository_root, source_branch),
        worktree_name=f"adopt-{context.code_repository_name}-memory-baseline",
        memory_repo_path=context.memory_root,
        memory_source_branch=source_branch,
        memory_work_branch=args.work_branch or source_branch,
    )
    result = worktree_manager.bootstrap_memory_repo(contract, dry_run=False)
    payload["state"] = "adopted"
    payload["accepted_drift"] = bool(args.accept_drift)
    payload["bootstrap"] = result
    payload["ledger"] = ledger_status(context.ledger_path)
    print(json.dumps(payload, indent=2))
    return 0


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--code-repository-name", help="Code repository name to resolve.")
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd(), help="Workspace root used to find --code-repository-name.")
    parser.add_argument("--code-repository-root", type=Path, help="Root directory of the code repository to resolve.")
    parser.add_argument("--topology", choices=("internal", "shared"), help="Optional topology override.")
    parser.add_argument("--shared-root", type=Path, help="Optional shared ar-coordination root.")
    parser.add_argument("--report", type=Path, help="Optional C-02 drift report path.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status")
    add_common(status)
    status.set_defaults(func=command_status)

    adopt = subparsers.add_parser("adopt")
    add_common(adopt)
    adopt.add_argument("--accept-drift", action="store_true", help="Accept current onboarding as factual enough to become the baseline.")
    adopt.add_argument("--source-branch", help="Code branch to map in memory.md. Defaults to current branch.")
    adopt.add_argument("--work-branch", help="Memory branch to use. Defaults to source branch.")
    adopt.add_argument("--dry-run", action="store_true")
    adopt.set_defaults(func=command_adopt)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, ValueError, LedgerError) as error:
        parser.error(str(error))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
