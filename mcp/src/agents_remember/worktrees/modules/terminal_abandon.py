"""The admitted abandon transaction, with host archival before destructive outputs."""

from __future__ import annotations

from agents_remember.models.lifecycles.enclosure import TerminalWorktreeAbandonArguments
from agents_remember.worktrees.integration.atomic_series_terminal import (
    AtomicSeriesTerminalPermit,
    publish_atomic_series_terminal_under_authority,
)
from agents_remember.worktrees.integration.terminal_enclosure_archive import (
    terminal_archive_required_result,
    terminal_contract_authority_if_present,
)
from agents_remember.worktrees.modules import abandon
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.cleanup import _with_terminal_archive
from agents_remember.worktrees.modules.guidance import status_payload
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.modules.terminal_agents import archive_terminal_agents
from agents_remember.worktrees.modules.terminal_validation import TerminalPreflight
from agents_remember.worktrees.services import TerminalGuard
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract


def _abandon_with_guard(
    args: WorktreeArgs,
    contract: WorktreeContract,
    preflight: TerminalPreflight,
    guard: TerminalGuard,
) -> WorktreeCommandResult:
    try:
        terminal_archive = terminal_archive_required_result(
            contract,
            operation="worktree_abandon",
            arguments=TerminalWorktreeAbandonArguments(force=args.force),
            dry_run=args.dry_run,
        )
        if terminal_archive.returncode != 0:
            return terminal_archive
        terminal_authority = (
            None
            if args.dry_run
            else terminal_contract_authority_if_present(load_contract(contract.contract_path))
        )

        def publish(
            series_permit: AtomicSeriesTerminalPermit | None = None,
        ) -> WorktreeCommandResult:
            current = load_contract(contract.contract_path)
            if args.dry_run:
                if current != contract:
                    raise RuntimeError("abandon contract changed before preview")
            else:
                terminal = terminal_contract_authority_if_present(current)
                if terminal is None:
                    raise RuntimeError("abandon lost terminal archive authority before mutation")
                current = terminal.archived_contract
            agents = archive_terminal_agents(current, dry_run=args.dry_run)
            outputs = abandon._abandon_terminal_outputs(
                args,
                current,
                preflight,
                series_permit=series_permit,
            )
            result = abandon._abandon_outputs_result(args, current, preflight, guard, outputs)
            result = _with_terminal_archive(result, terminal_archive)
            return WorktreeCommandResult(
                result.returncode,
                {**result.payload, **({"agentArchive": agents} if agents else {})},
            )

        if contract.kind == "series":
            return publish_atomic_series_terminal_under_authority(
                contract,
                "worktree_abandon",
                publish,
                terminal_authority=terminal_authority,
            )
        return publish()
    except Exception as error:
        return WorktreeCommandResult(
            2,
            {
                "state": "abandon-blocked",
                **status_payload(contract),
                "summary": "Abandon terminal helper failed; cache and contract stayed live.",
                "citation_source_index": abandon._preserved_cache(
                    guard.preview(), "terminal-helper-failed"
                ),
                "blockers": [{"terminal": "helper", "reason": str(error)}],
            },
        )
