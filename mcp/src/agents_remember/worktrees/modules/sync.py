"""Public entry point for resumable mid-task source synchronization."""

from __future__ import annotations

from agents_remember.controlplane.integration_authority_lock import integration_authority_lock
from agents_remember.worktrees.activation.atomic_series_activation_transaction import (
    sync_selected_atomic_series_under_authority,
)
from agents_remember.worktrees.integration.integration_branch_authority import require_sync_worktree
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.modules.start import load_contract_from_args
from agents_remember.worktrees.sync_source_refresh import fetch_source_upstreams
from agents_remember.worktrees.sync_transaction import (
    sync_contract_under_authority,
    sync_input_refusal,
)
from agents_remember.worktrees.worktree_contract import ContractError, WorktreeContract

_EXPECTED_SYNC_FAILURES = (
    ContractError,
    OSError,
    RuntimeError,
    UnicodeError,
    ValueError,
)


def sync_result(args: WorktreeArgs) -> WorktreeCommandResult:
    fetch: dict[str, object] = {}
    try:
        contract = load_contract_from_args(args)
        require_sync_worktree(contract)
        if contract.kind == "leaf" and not contract.code_worktree.exists():
            return WorktreeCommandResult(
                2,
                {
                    "state": "blocked",
                    "summary": "The code worktree does not exist; sync needs a live worktree.",
                },
            )
        invalid = sync_input_refusal(args, fetch)
        if invalid is not None:
            return invalid
        # Preview observes exact current state but creates no integration/store lock and
        # cannot fetch refs, publish atomic-series selection, or mutate journal residue.
        if args.dry_run:
            fetch = {
                "code": {"state": "skipped-preview"},
                **(
                    {"memory": {"state": "skipped-preview"}}
                    if contract.memory_mode == "external"
                    else {}
                ),
            }
            return sync_contract_under_authority(contract, args, fetch=fetch)
        fetch = fetch_source_upstreams(contract)
        return _sync_live(contract, args, fetch)
    except _EXPECTED_SYNC_FAILURES as error:
        return WorktreeCommandResult(
            2,
            {
                "state": "sync-operation-refused",
                "summary": "Sync could not establish its exact contract/source authority.",
                "detail": f"{type(error).__name__}: {error}",
                "fetch": fetch,
            },
        )


def _sync_live(
    contract: WorktreeContract,
    args: WorktreeArgs,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    with integration_authority_lock(contract.coordination_root, contract.repo_name):
        current = load_contract_from_args(args)
        if current != contract:
            return WorktreeCommandResult(
                2,
                {
                    "state": "sync-contract-changed-retry",
                    "summary": "The contract changed while source evidence was refreshed; "
                    "retry the same contract-addressed sync.",
                    "nextTool": "worktree_sync",
                    "nextArgs": {
                        "contract_path": current.contract_path.as_posix(),
                        "dry_run": False,
                    },
                    "fetch": fetch,
                },
            )
        require_sync_worktree(current)
        if current.kind == "series":
            return sync_selected_atomic_series_under_authority(
                current,
                activation_args=args,
                fetch=fetch,
            )
        return sync_contract_under_authority(current, args, fetch=fetch)
