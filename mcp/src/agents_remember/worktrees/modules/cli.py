from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from agents_remember.kernel.memory_ledger import LedgerError
from agents_remember.models.closeout_input import CloseoutCorrectedCall
from agents_remember.worktrees.closeout_input import (
    corrected_closeout_arguments,
    normalize_closeout_input,
    raw_closeout_messages,
)
from agents_remember.worktrees.integration.mutation_evidence import (
    JOURNALED_CLOSEOUT_REQUIRED,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.cleanup import cleanup_result
from agents_remember.worktrees.modules.closeout import closeout_result
from agents_remember.worktrees.modules.integrate import integrate_result
from agents_remember.worktrees.modules.start import attach_result, start_result, status_result
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    heal_contract_leaf_ids,
    load_contract,
)


def parse_json_stdout(stdout: str) -> object:
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def command_status(args: argparse.Namespace) -> int:
    result = status_result(WorktreeArgs.from_namespace(args))
    print(json.dumps(result.payload, indent=2))
    return result.returncode


def command_attach(args: argparse.Namespace) -> int:
    result = attach_result(WorktreeArgs.from_namespace(args))
    print(json.dumps(result.payload, indent=2))
    return result.returncode


def command_start(args: argparse.Namespace) -> int:
    result = start_result(WorktreeArgs.from_namespace(args))
    print(json.dumps(result.payload, indent=2))
    return result.returncode


def command_closeout(args: argparse.Namespace) -> int:
    worktree_args = WorktreeArgs.from_namespace(args)
    if not worktree_args.dry_run:
        raise RuntimeError(JOURNALED_CLOSEOUT_REQUIRED)
    if worktree_args.contract_path is None:
        raise RuntimeError("closeout requires a contract path")
    contract = load_contract(worktree_args.contract_path)
    effective = normalize_closeout_input(
        contract,
        raw_closeout_messages(
            code=getattr(args, "code_commit_message", None),
            memory=getattr(args, "memory_commit_message", None),
            ledger=getattr(args, "ledger_commit_message", None),
        ),
        route="worktree",
        corrected_call=CloseoutCorrectedCall(
            tool="worktree closeout",
            arguments=corrected_closeout_arguments(worktree_args.contract_path.as_posix()),
        ),
    )
    result = closeout_result(
        replace(worktree_args, closeout_input=effective, ledger_commit_message="")
    )
    print(json.dumps(result.payload, indent=2))
    return result.returncode


def command_integrate(args: argparse.Namespace) -> int:
    result = integrate_result(WorktreeArgs.from_namespace(args))
    print(json.dumps(result.payload, indent=2))
    return result.returncode


def command_cleanup(args: argparse.Namespace) -> int:
    result = cleanup_result(WorktreeArgs.from_namespace(args))
    print(json.dumps(result.payload, indent=2))
    return result.returncode


def command_heal_leaf_ids(args: argparse.Namespace) -> int:
    """The deliberate on-demand seam for :func:`heal_contract_leaf_ids` (260712-PTS-L1).

    Healing legacy stem-shaped leaf ids is a one-shot migration walk, never a per-read
    side effect — run this once against a coordination root (or at daemon startup)
    instead of relying on ``load_contract`` to normalize."""
    report = heal_contract_leaf_ids(args.coordination_root, dry_run=args.dry_run)
    print(json.dumps(report, indent=2))
    return 0


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
        "--contract-path", type=Path, help="Path to an existing series-contract.md."
    )
    parser.add_argument("--task-name", help="Task name used for task folder resolution.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start")
    add_common(start)
    start.add_argument("--worktree-name", required=True)
    start.add_argument("--workflow-kind", default="light-task")
    start.add_argument("--source-branch")
    start.add_argument("--work-branch")
    start.add_argument("--memory-mode", choices=("internal", "external", "disabled"))
    start.add_argument("--memory-choice", choices=("disabled-memory",))
    start.add_argument("--custom-instruction")
    start.add_argument("--skip-provider-setup", action="store_true")
    start.add_argument("--provider-timeout", type=int, default=1800)
    start.add_argument("--dry-run", action="store_true")
    start.set_defaults(func=command_start)

    attach = subparsers.add_parser("attach")
    add_common(attach)
    attach.set_defaults(func=command_attach)

    status = subparsers.add_parser("status")
    add_common(status)
    status.set_defaults(func=command_status)

    closeout = subparsers.add_parser("closeout")
    closeout.add_argument("--contract-path", type=Path, required=True)
    closeout.add_argument("--code-commit-message")
    closeout.add_argument("--memory-commit-message")
    closeout.add_argument("--ledger-commit-message")
    closeout.add_argument("--dry-run", action="store_true")
    closeout.set_defaults(func=command_closeout)

    integrate = subparsers.add_parser("integrate")
    integrate.add_argument("--contract-path", type=Path, required=True)
    integrate.add_argument("--approved", action="store_true")
    integrate.add_argument("--strategy", choices=("ff-only", "replay"), default="ff-only")
    integrate.add_argument("--ledger-commit-message", default="")
    integrate.add_argument("--dry-run", action="store_true")
    integrate.set_defaults(func=command_integrate)

    cleanup = subparsers.add_parser("cleanup")
    cleanup.add_argument("--contract-path", type=Path, required=True)
    cleanup.add_argument("--approved", action="store_true")
    cleanup.add_argument("--dry-run", action="store_true")
    cleanup.set_defaults(func=command_cleanup)

    heal = subparsers.add_parser(
        "heal-leaf-ids",
        help=(
            "Rewrite legacy stem-shaped leaf ids to canonical doc ids across the "
            "active leaf enclosures (one-shot, idempotent, loud)."
        ),
    )
    heal.add_argument("--coordination-root", type=Path, required=True)
    heal.add_argument("--dry-run", action="store_true")
    heal.set_defaults(func=command_heal_leaf_ids)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, ContractError, LedgerError, ValueError) as error:
        parser.error(str(error))
    return 1
