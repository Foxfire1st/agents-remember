#!/usr/bin/env python3
"""Adopt existing external-memory onboarding as the first ledgered baseline.

Requires Python 3.10+ and git. Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.kernel import coordination_context_resolver as resolver
from agents_remember.kernel.coordination_context.models import CoordinationRequest
from agents_remember.kernel.coordination_context_resolver import CoordinationHints
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_ledger import (
    LedgerError,
    create_initial_ledger,
    load_ledger,
    write_ledger,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check import drift
from agents_remember.worktrees import git_worktree_manager as worktree_manager
from agents_remember.worktrees.integration_branch_authority import (
    memory_repository_default_branch,
)
from agents_remember.worktrees.modules.contract_reader import WorktreeContractReader
from agents_remember.worktrees.modules.git import (
    branch_commit,
    commit_if_dirty,
    ensure_git_identity,
    local_branch_ref,
    require_git,
)


@dataclass(frozen=True)
class BaselineRequest:
    code_repository_name: str
    workspace_root: Path
    code_repository_root: Path | None = None
    coordination_root: Path | None = None
    topology: Literal["internal", "external"] | None = "external"
    report: Path | None = None


def _normalize_topology(value: str | None) -> Literal["internal", "external"] | None:
    if value in ("internal", "external"):
        return value
    return None


def request_from_args(args: argparse.Namespace) -> BaselineRequest:
    return BaselineRequest(
        code_repository_name=args.code_repository_name,
        workspace_root=args.workspace_root,
        code_repository_root=args.code_repository_root,
        coordination_root=args.coordination_root,
        topology=_normalize_topology(args.topology),
        report=args.report,
    )


def resolve_request_context(request: BaselineRequest):
    return resolver.resolve_coordination_context(
        code_repository_name=request.code_repository_name,
        workspace_root=request.workspace_root,
        code_repository_root=request.code_repository_root,
        request=CoordinationRequest(
            hints=CoordinationHints(
                topology=request.topology, coordination_root=request.coordination_root
            ),
            contract_reader=WorktreeContractReader(),
        ),
    )


def resolve_baseline_context(args: argparse.Namespace):
    return resolve_request_context(request_from_args(args))


def run_drift(context, report_path: Path | None) -> tuple[list[drift.DriftRow], Path]:
    if not context.onboarding_root.exists():
        raise RuntimeError(f"onboarding root does not exist: {context.onboarding_root}")
    rows = [
        row
        for path in drift.discover_onboarding_files(context.onboarding_root)
        for row in drift.classify_sidecar_onboarding_units(
            path, context.code_repository_root, context.onboarding_root, context.storage
        )
    ]
    rows.extend(
        drift.classify_inline_source(path, context.code_repository_root)
        for path in drift.discover_inline_onboarding_sources(
            context.code_repository_root, context.storage
        )
    )
    rows.sort(key=lambda row: (row.source_file, row.onboarding_file))
    report = drift.resolve_report_path(
        report_path,
        context.coordination_root,
        context.temp_root,
        context.code_repository_root,
        context.memory_root,
    )
    drift.write_markdown_report(rows, report, context.code_repository_root, context.onboarding_root)
    return rows, report


def drift_summary(rows: list[drift.DriftRow]) -> dict[str, int]:
    return drift.counts(rows)


def actionable_rows(rows: list[drift.DriftRow]) -> list[drift.DriftRow]:
    return [row for row in rows if row.classification in drift.ACTIONABLE_CLASSIFICATIONS]


def current_branch(repo: Path) -> str:
    return worktree_manager.current_branch(repo)


def head_commit(repo: Path, ref: str = "HEAD") -> str:
    return worktree_manager.head_commit(repo, ref)


def _baseline_default_branch(memory_root: Path) -> str:
    """Prove the existing default, or the exact unborn branch minted by memory_init."""

    head = run_git(memory_root, ["rev-parse", "--verify", "HEAD"])
    if head.returncode == 0:
        return memory_repository_default_branch(memory_root)
    configured = run_git(
        memory_root,
        ["config", "--get", "agents-remember.defaultBranch"],
    )
    branch = configured.stdout.strip().removeprefix("refs/heads/")
    if configured.returncode != 0 or branch != "main":
        raise RuntimeError(
            "memory baseline adoption requires explicit default-branch authority from memory_init"
        )
    ref = local_branch_ref(branch)
    existing = run_git(memory_root, ["show-ref", "--verify", "--quiet", ref])
    if existing.returncode == 0:
        return memory_repository_default_branch(memory_root)
    if existing.returncode != 1:
        raise RuntimeError("memory baseline adoption cannot verify its configured default branch")
    local_heads = run_git(
        memory_root,
        ["for-each-ref", "--format=%(refname)", "refs/heads"],
    )
    symbolic_head = run_git(memory_root, ["symbolic-ref", "--quiet", "HEAD"])
    if (
        local_heads.returncode != 0
        or local_heads.stdout.strip()
        or symbolic_head.returncode != 0
        or symbolic_head.stdout.strip() != ref
    ):
        raise RuntimeError(
            "memory baseline adoption requires the exact unborn default branch created by "
            "memory_init"
        )
    return branch


def adopt_initial_baseline(context, source_branch: str, memory_branch: str) -> dict[str, object]:
    if not context.memory_root.exists():
        raise RuntimeError(
            f"memory root does not exist: {context.memory_root}. "
            "Run c-00-initialize-memory-repo before adopting a memory baseline."
        )
    if not (context.memory_root / ".git").exists():
        raise RuntimeError(
            f"external memory root is not a Git repo: {context.memory_root}. "
            "Run c-00-initialize-memory-repo before adopting a memory baseline."
        )

    if context.ledger_path.exists():
        raise RuntimeError("memory baseline adoption is only valid before the first ledger exists")
    default_branch = _baseline_default_branch(context.memory_root)
    requested_branch = memory_branch.removeprefix("refs/heads/")
    if requested_branch != default_branch or current_branch(context.memory_root) != default_branch:
        raise RuntimeError(
            "memory baseline adoption is a bootstrap-only exception on the checked-out "
            "repository-default branch; it cannot create, switch, or commit an integration ref"
        )

    ensure_git_identity(context.memory_root)

    existing_paths = [
        path.name
        for path in (context.memory_root / name for name in ("onboarding", "docs", "system"))
        if path.exists()
    ]
    if not existing_paths:
        raise RuntimeError(
            f"memory root has no onboarding, docs, or system content: {context.memory_root}. "
            "Run c-00-initialize-memory-repo first, then add onboarding before adopting."
        )

    require_git(context.memory_root, ["add", *existing_paths])
    memory_content_commit = commit_if_dirty(
        context.memory_root,
        f"[adopt-{context.code_repository_name}-memory-baseline] Adopt external memory content",
    )
    ledger = create_initial_ledger(
        context.code_repository_name,
        branch_commit(context.code_repository_root, source_branch),
        memory_content_commit,
    )
    write_ledger(context.ledger_path, ledger)
    require_git(context.memory_root, ["add", "memory.md"])
    ledger_commit = commit_if_dirty(
        context.memory_root,
        f"[adopt-{context.code_repository_name}-memory-baseline] Bootstrap memory ledger",
    )
    return {
        "state": "adopted-baseline",
        "memoryContentCommit": memory_content_commit,
        "ledgerCommit": ledger_commit,
    }


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


def base_payload(context, rows: list[drift.DriftRow], report: Path) -> dict[str, object]:
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


def baseline_status(request: BaselineRequest) -> dict[str, object]:
    context = resolve_request_context(request)
    rows, report = run_drift(context, request.report)
    return base_payload(context, rows, report)


def baseline_adopt(
    request: BaselineRequest,
    *,
    accept_drift: bool = False,
    source_branch: str | None = None,
    work_branch: str | None = None,
    dry_run: bool = False,
) -> tuple[int, dict[str, object]]:
    context = resolve_request_context(request)
    if context.topology != "external":
        raise RuntimeError("adoption requires external topology")
    if context.ledger_path is None:
        raise RuntimeError("external memory context is missing a ledger path")
    rows, report = run_drift(context, request.report)
    payload = base_payload(context, rows, report)
    if ledger_status(context.ledger_path)["exists"]:
        return 0, payload
    if actionable_rows(rows) and not accept_drift:
        payload["message"] = (
            "actionable drift blocks adoption; refresh onboarding with the c-05-create-or-update-onboarding-files skill or rerun with --accept-drift"
        )
        return 2, payload
    if dry_run:
        payload["state"] = "would-adopt"
        payload["accepted_drift"] = bool(accept_drift)
        return 0, payload

    resolved_source_branch = source_branch or current_branch(context.code_repository_root)
    result = adopt_initial_baseline(
        context, resolved_source_branch, work_branch or resolved_source_branch
    )
    payload["state"] = "adopted"
    payload["accepted_drift"] = bool(accept_drift)
    payload["bootstrap"] = result
    payload["ledger"] = ledger_status(context.ledger_path)
    return 0, payload


def command_status(args: argparse.Namespace) -> int:
    print(json.dumps(baseline_status(request_from_args(args)), indent=2))
    return 0


def command_adopt(args: argparse.Namespace) -> int:
    returncode, payload = baseline_adopt(
        request_from_args(args),
        accept_drift=args.accept_drift,
        source_branch=args.source_branch,
        work_branch=args.work_branch,
        dry_run=args.dry_run,
    )
    print(json.dumps(payload, indent=2))
    return returncode


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--code-repository-name", help="Code repository name to resolve.")
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=Path.cwd(),
        help="Workspace root used to find --code-repository-name.",
    )
    parser.add_argument(
        "--code-repository-root",
        type=Path,
        help="Root directory of the code repository to resolve.",
    )
    parser.add_argument(
        "--topology", choices=("internal", "external"), help="Optional topology override."
    )
    parser.add_argument("--coordination-root", type=Path, help="Optional coordination root.")
    parser.add_argument(
        "--report", type=Path, help="Optional c-02-memory-quality-control drift report path."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status")
    add_common(status)
    status.set_defaults(func=command_status)

    adopt = subparsers.add_parser("adopt")
    add_common(adopt)
    adopt.add_argument(
        "--accept-drift",
        action="store_true",
        help="Accept current onboarding as factual enough to become the baseline.",
    )
    adopt.add_argument(
        "--source-branch", help="Code branch to map in memory.md. Defaults to current branch."
    )
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
