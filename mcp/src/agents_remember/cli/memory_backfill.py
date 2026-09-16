"""CLI adapter: plan or apply the memory-history backfill that writes the code attribution.

    agents-remember memory-backfill --contract <leaf contract> [--apply]
        [--rescue-ref <ref>] [--ref <ref> ...] [--expected-digest <digest>]

``--contract`` is REQUIRED and is the write guard, exactly as ``memory-citations`` uses it: the
command reads the memory and code repositories the contract names, so there is no argument list
that can aim a history rewrite at the official memory repository by hand.

PLANNING IS THE DEFAULT, AND PLANNING IS ALSO THE DRY RUN. Without ``--apply`` this reads the
history, prints the census, and exits non-zero when anything is left to rewrite, which makes it
usable as a check. ``--apply`` writes the trailers and moves exactly the refs named by ``--ref``,
refusing to start unless ``--rescue-ref`` is free, because the rescue ref is the only undo a
message rewrite has. Pass the digest the plan printed as ``--expected-digest`` and the applied
run is provably the run that was rehearsed.

Exit status is the gate's: 0 when the history already carries every attribution the table
records, 1 when anything remains to write, and 2 when the invocation is refused.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agents_remember.kernel.memory_backfill import (
    MemoryBackfillRefusal,
    MemoryBackfillRequest,
    apply_memory_backfill,
    plan_memory_backfill,
)
from agents_remember.worktrees.worktree_contract import ContractError, load_contract

EXIT_NOTHING_TO_DO = 0
EXIT_WORK_REMAINS = 1
EXIT_REFUSED = 2

DEFAULT_RESCUE_REF = "refs/backup/memory-pre-migration"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--contract",
        required=True,
        help="Path to the leaf enclosure contract. Required: the backfill reads the memory and "
        "code repositories the contract names and refuses any other target.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the trailers and move the named refs. Without it this reports and writes "
        "nothing, which is the dry run.",
    )
    parser.add_argument(
        "--rescue-ref",
        default=DEFAULT_RESCUE_REF,
        help="Ref that pins every original commit before the first new object exists. It must "
        "not already exist: an existing one records an earlier rewrite nobody has read yet.",
    )
    parser.add_argument(
        "--ref",
        action="append",
        default=None,
        dest="refs",
        help="Branch to move onto its rewritten tip. Repeatable. Defaults to the contract's "
        "memory work branch.",
    )
    parser.add_argument(
        "--expected-digest",
        default=None,
        help="Refuse unless the plan's digest is exactly this, so the applied run is the run "
        "that was rehearsed.",
    )


def run(args: argparse.Namespace) -> int:
    """Resolve the request, plan, and apply only when the caller asked for it."""

    request = _request_from(args)
    if isinstance(request, str):
        print(request)
        return EXIT_REFUSED
    try:
        plan = plan_memory_backfill(request)
    except MemoryBackfillRefusal as error:
        print(f"the backfill could not be planned: {error}")
        return EXIT_REFUSED

    for line in plan.render():
        print(line)
    if plan.is_empty:
        print("nothing to rewrite: this history already carries every recorded attribution")
        return EXIT_NOTHING_TO_DO
    if not args.apply:
        print("re-run with --apply to write these trailers")
        return EXIT_WORK_REMAINS
    return _apply(request, args)


def _apply(request: MemoryBackfillRequest, args: argparse.Namespace) -> int:
    try:
        result = apply_memory_backfill(request, expected_digest=args.expected_digest)
    except MemoryBackfillRefusal as error:
        print(f"the backfill was refused: {error}")
        return EXIT_REFUSED
    print(f"rewrote {len(result.rewritten)} commits")
    print(f"memory tip {result.old_tip} -> {result.new_tip}")
    print(f"moved refs: {', '.join(result.updated_refs) or 'none'}")
    print(f"rescue refs: {', '.join(result.rescue_refs) or 'none'}")
    print(
        "next: reconcile the selected worktrees and contract bases with the rewritten refs, "
        "then rebuild the consumer ledger cache from commit trailers; do not commit the cache"
    )
    return EXIT_NOTHING_TO_DO


def _request_from(args: argparse.Namespace) -> MemoryBackfillRequest | str:
    """The request the arguments name, or the refusal line explaining why they name none."""

    try:
        contract = load_contract(Path(args.contract))
    except (ContractError, OSError) as error:
        return f"the contract could not be read: {error}"
    refusal = _refusal_for(contract)
    if refusal is not None:
        return refusal
    memory_repo = contract.memory_repo_path
    if memory_repo is None:  # proven by _refusal_for; kept total for the type, not for the run
        return "the contract names no memory repository"
    refs = tuple(args.refs) if args.refs else (_default_ref(contract),)
    return MemoryBackfillRequest(
        memory_repo=Path(memory_repo),
        tip=contract.memory_work_branch or contract.memory_base_commit,
        code_repo=Path(contract.code_repo_path),
        rescue_ref=args.rescue_ref,
        update_refs=tuple(ref for ref in refs if ref),
    )


def _refusal_for(contract) -> str | None:
    if contract.memory_mode != "external":
        return (
            f"this contract's memory mode is {contract.memory_mode!r}; the trailer backfill is "
            "an external-memory operation"
        )
    if contract.memory_repo_path is None or not Path(contract.memory_repo_path).is_dir():
        return f"the memory repository {contract.memory_repo_path!r} is not a directory"
    if not Path(contract.code_repo_path).is_dir():
        return f"the code repository {contract.code_repo_path!r} is not a directory"
    return None


def _default_ref(contract) -> str:
    return contract.memory_work_branch or contract.memory_source_branch


if __name__ == "__main__":
    import sys

    sys.exit(run(argparse.ArgumentParser().parse_args()))
