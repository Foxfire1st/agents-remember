#!/usr/bin/env python3
"""Adopt existing external-memory onboarding as an attributed Git baseline.

Requires Python 3.10+ and git. Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel import coordination_context_resolver as resolver
from agents_remember.kernel.coordination_context.models import CoordinationRequest
from agents_remember.kernel.coordination_context_resolver import CoordinationHints
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_attribution import (
    MemoryAttributionError,
    attributed_commits,
    render_memory_content_message,
)
from agents_remember.kernel.memory_cache import (
    derive_memory_ledger,
    prepare_memory_cache,
    refresh_memory_cache,
)
from agents_remember.kernel.memory_init import DEFAULT_BRANCH_CONFIG_KEY
from agents_remember.kernel.memory_mode import Topology, require_supported_topology
from agents_remember.memory_quality.integrity.onboarding_drift_check import drift
from agents_remember.models.memory_content_excludes import MEMORY_CONTENT_EXCLUDES
from agents_remember.worktrees import git_worktree_manager as worktree_manager
from agents_remember.worktrees.integration.integration_branch_authority import (
    memory_repository_default_branch,
)
from agents_remember.worktrees.modules.contract_reader import WorktreeContractReader
from agents_remember.worktrees.modules.git import (
    branch_commit,
    commit_if_dirty,
    ensure_git_identity,
    local_branch_ref,
)


@dataclass(frozen=True)
class BaselineRequest:
    code_repository_name: str
    workspace_root: Path
    code_repository_root: Path | None = None
    coordination_root: Path | None = None
    topology: Topology | None = "external"
    report: Path | None = None


def _normalize_topology(value: str | None) -> Topology | None:
    """Narrow ``--topology`` onto the supported set, or refuse.

    Through the shared vocabulary helper, for two reasons that used to be one: letting an
    unrecognized value fall through to ``None`` would turn an explicit request into ordinary
    detection, and answering *every* unrecognized value with "which was removed" tells a
    developer who mistyped the flag that a mode was removed when none was named.
    """
    if value is None:
        return None
    return require_supported_topology(value)


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


#: The exclusions below are the shared memory-content policy, declared in ``models`` so all
#: four producing seams read one set. They are carried on the call that actually stages:
#: ``commit_if_dirty`` re-stages the whole worktree, so an exclusion applied only to a bare
#: ``git add`` before it is inert and the file lands in the commit anyway. That was this
#: leaf's baseline defect; the case in ``test_memory_branch_authority.py`` fails if it
#: returns.


def _baseline_default_branch(memory_root: Path) -> str:
    """Prove the existing default, or the exact unborn branch minted by memory_init.

    The branch is whatever ``memory_init`` recorded, not a fixed name: the memory
    repository is founded on the code branch the developer chose, and hard-coding ``main``
    here would refuse every repository whose memory is founded on anything else.
    """

    head = run_git(memory_root, ["rev-parse", "--verify", "HEAD"])
    if head.returncode == 0:
        return memory_repository_default_branch(memory_root)
    configured = run_git(
        memory_root,
        ["config", "--get", DEFAULT_BRANCH_CONFIG_KEY],
    )
    branch = configured.stdout.strip().removeprefix("refs/heads/")
    if configured.returncode != 0 or not branch:
        raise RuntimeError(
            "memory baseline adoption requires explicit default-branch authority from "
            "memory_init; the memory repository records no "
            f"{DEFAULT_BRANCH_CONFIG_KEY} value"
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

    if has_adopted_baseline(context.memory_root):
        raise RuntimeError("memory baseline adoption is only valid before attributed memory exists")
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

    # Resolve attribution once; the consumer cache derives the same pair from this commit.
    code_source_commit = branch_commit(context.code_repository_root, source_branch)
    prepare_memory_cache(context.memory_root)
    memory_content_commit = commit_if_dirty(
        context.memory_root,
        render_memory_content_message(
            f"[adopt-{context.code_repository_name}-memory-baseline] Adopt external memory content",
            code_source_commit,
        ),
        exclude_paths=MEMORY_CONTENT_EXCLUDES,
    )
    return {
        "state": "adopted-baseline",
        "memoryContentCommit": memory_content_commit,
        "ledgerCache": refresh_memory_cache(
            context.memory_root,
            path=context.ledger_path,
            repo_name=context.code_repository_name,
        ),
    }


def has_adopted_baseline(repository: Path) -> bool:
    if run_git(repository, ["rev-parse", "--verify", "HEAD"]).returncode != 0:
        return False
    return any(commit.is_attributed for commit in attributed_commits(repository, tip="HEAD"))


def ledger_status(repository: Path) -> dict[str, object]:
    try:
        ledger = derive_memory_ledger(repository)
    except MemoryAttributionError as error:
        return {"state": "unavailable", "error": str(error)}
    return {
        "state": "derived",
        "lastVerifiedCodeCommit": ledger.last_verified_code_commit,
        "lastMemoryContentCommit": ledger.last_memory_content_commit,
    }


def base_payload(context, rows: list[drift.DriftRow], report: Path) -> dict[str, object]:
    ledger = ledger_status(context.memory_root)
    actionable = actionable_rows(rows)
    state = "ready"
    if ledger.get("state") == "derived" and ledger.get("lastMemoryContentCommit"):
        state = "already-adopted"
    elif (
        ledger.get("state") == "unavailable"
        and run_git(context.memory_root, ["rev-parse", "--verify", "HEAD"]).returncode == 0
    ):
        state = "unavailable"
    elif actionable:
        state = "blocked-drift"
    return {
        "state": state,
        "code_repository_name": context.code_repository_name,
        "topology": context.topology,
        "code_repository_root": context.code_repository_root.as_posix(),
        "memory_root": context.memory_root.as_posix(),
        "onboarding_root": context.onboarding_root.as_posix(),
        "ledger_path": context.ledger_path.as_posix() if context.ledger_path is not None else None,
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
    rows, report = run_drift(context, request.report)
    payload = base_payload(context, rows, report)
    if payload["state"] == "already-adopted":
        return 0, payload
    if payload["state"] == "unavailable":
        payload["message"] = (
            "memory Git history is unavailable; restore readable history before adoption"
        )
        return 2, payload
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
    payload["ledger"] = ledger_status(context.memory_root)
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
        "--topology",
        metavar="external",
        help="Optional topology override. `external` is the only supported topology.",
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
        "--source-branch", help="Code branch to attribute in Git. Defaults to current branch."
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
    except (RuntimeError, ValueError) as error:
        parser.error(str(error))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
