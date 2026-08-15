from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.git_freshness import freshness_to_packet, read_branch_freshness
from agents_remember.kernel.memory_ledger import (
    LedgerError,
    MemoryLedger,
    find_mapping,
    load_ledger,
    prepend_mapping,
    write_ledger,
)
from agents_remember.tasks.leaf_doc import restamp_leaf_doc_lifecycle
from agents_remember.tasks.store import write_task_docs
from agents_remember.worktrees.closeout_queue_lifecycle import publish_queue_bound_task_facts
from agents_remember.worktrees.leaf_refs import resolve_leaf_enclosure_contract_for_ref
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.context import resolve_context
from agents_remember.worktrees.modules.git import (
    branch_exists,
    commit_if_dirty,
    current_branch,
    ensure_worktree,
    has_changes,
    head_commit,
    longest_tracked_path_length,
    require_git,
)
from agents_remember.worktrees.modules.guidance import (
    contract_next_args,
    next_guidance,
    recovery_guidance,
    status_payload,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.modules.start_contract import (
    build_start_contract,
    memory_base_for_source,
)
from agents_remember.worktrees.modules.start_result import started_result
from agents_remember.worktrees.reopen import reopen_required_start_result
from agents_remember.worktrees.services import ProviderSetupRequestSpec, worktree_services
from agents_remember.worktrees.source_lineage import (
    lineage_block_payload,
    lineage_refusal,
    parent_source_lineage,
    source_lineage_for_contract,
)
from agents_remember.worktrees.start_progress import (
    StartBeat,
    StartingEnclosure,
    clear_start_progress,
    write_start_progress,
)
from agents_remember.worktrees.task_resolver import resolve_leaf_enclosure_contract
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    WorktreeContract,
    amend_contract,
    load_contract,
    write_contract,
)


@dataclass(frozen=True)
class ProviderStartPaths:
    target_coordination_root: Path
    source_coordination_root: Path
    source_repo_root: Path
    target_repo_root: Path
    source_memory_root: Path | None
    target_memory_root: Path | None
    provider_runtime_root: Path
    provider_settings_path: Path


def load_contract_from_args(args: WorktreeArgs) -> WorktreeContract:
    if args.contract_path is not None:
        return load_contract(args.contract_path)
    context = resolve_context(args)
    if not args.task_name:
        raise RuntimeError("--task-name or --contract-path is required")
    if args.leaf_id:
        contract_path = resolve_leaf_enclosure_contract_for_ref(
            context.coordination_root,
            context.code_repository_name,
            args.task_name,
            leaf_id=args.leaf_id,
            parent_task=args.parent_task,
        )
    else:
        contract_path = resolve_leaf_enclosure_contract(
            context.coordination_root,
            context.code_repository_name,
            args.task_name,
            parent_task=args.parent_task,
        )
    if contract_path is None:
        raise RuntimeError("--task-name resolved no leaf enclosure; pass --leaf-id")
    return load_contract(contract_path)


def status_result(args: WorktreeArgs) -> WorktreeCommandResult:
    contract = load_contract_from_args(args)
    return WorktreeCommandResult(0, dict(status_payload(contract)))


def attach_result(args: WorktreeArgs) -> WorktreeCommandResult:
    contract = load_contract_from_args(args)
    lineage = source_lineage_for_contract(contract)
    if lineage_refusal(lineage) is not None:
        assert lineage is not None
        return WorktreeCommandResult(
            2,
            {
                **status_payload(contract),
                **lineage_block_payload(lineage),
                "summary": "Attach refused before stale task context was resumed: "
                + lineage.summary,
            },
        )
    return WorktreeCommandResult(
        0, {"state": "attached", "attached": True, **status_payload(contract)}
    )


def _blocked_memory_start_result(
    context, args: WorktreeArgs, code_state: str, memory_state: dict[str, object]
) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        2,
        {
            "state": "blocked",
            "summary": "Code worktree is prepared, but external memory cannot be used until the developer selects a recovery path.",
            **recovery_guidance(
                "choose_memory_recovery",
                tool="worktree_start",
                args={
                    "repo_id": context.code_repository_name,
                    "task_name": args.task_name,
                    "worktree_name": args.worktree_name,
                    "workflow_kind": args.workflow_kind,
                },
                required_args=["memory_choice"],
            ),
            "code_worktree": code_state,
            "memory": memory_state,
        },
    )


def _contract_after_memory_start(
    contract: WorktreeContract, memory_state: dict[str, object]
) -> WorktreeContract:
    if contract.memory_mode == "external" and memory_state["state"] == "disabled":
        return amend_contract(
            replace(
                contract,
                memory_repo_path=None,
                memory_source_branch="",
                memory_work_branch="",
                memory_base_commit="",
                memory_worktree=None,
                ledger_path=None,
                memory_state="disabled",
            ),
            # Through the typed record, like every other vocabulary cell: `memory_state` above
            # is free text and `memory_mode` is not.
            ContractCells(memory_mode="disabled"),
        )
    reconciled_base = memory_state.get("reconciledMemoryBaseCommit")
    if isinstance(reconciled_base, str) and reconciled_base:
        # A reconciliation recovery (finding 7) advanced the official memory tip; persist the base the
        # mapping was recorded against so a freshly created memory branch carries the mapping.
        return replace(contract, memory_base_commit=reconciled_base)
    return contract


def _blocked_provider_start_result(
    context,
    args: WorktreeArgs,
    code_state: str,
    memory_state: dict[str, object],
    provider_state: dict[str, object],
) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        2,
        {
            "state": "blocked",
            "summary": "Worktree provider setup could not be prepared safely.",
            **recovery_guidance(
                "choose_provider_setup_recovery",
                tool="worktree_start",
                args={
                    "repo_id": context.code_repository_name,
                    "task_name": args.task_name,
                    "worktree_name": args.worktree_name,
                    "workflow_kind": args.workflow_kind,
                    "skip_provider_setup": True,
                },
            ),
            "code_worktree": code_state,
            "memory": memory_state,
            "providers": provider_state,
        },
    )


# Margin under the legacy Windows 260-char MAX_PATH so separators, suffixes
# (e.g. memory sidecar ".md"), and tooling scratch names still fit.
WINDOWS_MAX_PATH_BUDGET = 250


def _windows_long_paths_enabled() -> bool:
    if os.name != "nt":
        return True
    import winreg  # noqa: PLC0415 — Windows-only stdlib module

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
        return bool(value)
    except OSError:
        return False


def long_path_block_payload(
    *,
    label: str,
    worktree_path: str,
    longest_tracked: int,
    budget: int = WINDOWS_MAX_PATH_BUDGET,
) -> dict[str, object] | None:
    """Pure decision: block payload when projected paths exceed the budget."""
    projected = len(worktree_path) + 1 + longest_tracked
    if projected <= budget:
        return None
    excess = projected - budget
    return {
        "state": "blocked",
        "summary": (
            f"Projected {label} worktree paths reach {projected} characters, but this "
            f"Windows host caps paths at 260 (LongPathsEnabled=0; budget {budget}). "
            "Files deeper in the tree would fail to check out or open mid-task."
        ),
        "projectedPathLength": projected,
        "pathBudget": budget,
        "longestTrackedPathLength": longest_tracked,
        "worktreePath": worktree_path,
        "remedies": [
            "Enable Windows long paths (admin): HKLM\\SYSTEM\\CurrentControlSet\\Control"
            "\\FileSystem\\LongPathsEnabled=1, then restart the MCP/harness processes.",
            f"Or choose a worktree name at least {excess} characters shorter.",
        ],
    }


def _long_path_preflight(contract: WorktreeContract) -> dict[str, object] | None:
    if _windows_long_paths_enabled():
        return None
    checks: list[tuple[str, Path, Path, str]] = [
        ("code", contract.code_repo_path, contract.code_worktree, contract.code_source_branch)
    ]
    if (
        contract.memory_mode == "external"
        and contract.memory_repo_path
        and contract.memory_worktree
    ):
        checks.append(
            (
                "memory",
                contract.memory_repo_path,
                contract.memory_worktree,
                contract.memory_source_branch or "HEAD",
            )
        )
    for label, repo, worktree, ref in checks:
        payload = long_path_block_payload(
            label=label,
            worktree_path=str(worktree),
            longest_tracked=longest_tracked_path_length(repo, ref),
        )
        if payload is not None:
            return payload
    return None


def _branch_freshness_findings(contract: WorktreeContract) -> list[dict[str, object]]:
    targets: list[tuple[str, Path, str]] = [
        ("code", contract.code_repo_path, contract.code_source_branch)
    ]
    if (
        contract.memory_mode == "external"
        and contract.memory_repo_path is not None
        and (contract.memory_repo_path / ".git").exists()
        and contract.memory_source_branch
    ):
        targets.append(("memory", contract.memory_repo_path, contract.memory_source_branch))
    return [
        {"side": side, **freshness_to_packet(read_branch_freshness(repo, branch))}
        for side, repo, branch in targets
    ]


def _stale_base_preflight(
    context, contract: WorktreeContract, args: WorktreeArgs
) -> dict[str, object] | None:
    """Refuse to base a new worktree on a source branch behind its upstream (issue #54).

    Only `behind`/`diverged` block; `unknown` (offline) and `no-upstream` are reported
    by callers via worktree_status freshness, never blocked on. `stale_base_choice`
    recoveries: `fast-forward` (ff the stale local branches, then proceed) or
    `proceed-stale` (explicit override).
    """
    if args.stale_base_choice == "proceed-stale":
        return None
    stale = [
        finding
        for finding in _branch_freshness_findings(contract)
        if finding["state"] in ("behind", "diverged")
    ]
    if not stale:
        return None
    if args.stale_base_choice == "fast-forward":
        failures = _fast_forward_stale_branches(contract, stale, args.dry_run)
        if not failures:
            return None
        stale = failures
    return {
        "state": "blocked",
        "summary": "Source branches are behind their upstream; a worktree started now "
        "would base on stale code/memory and silently defeat the provider seed "
        "fast-path. Choose fast-forward or proceed-stale.",
        **recovery_guidance(
            "choose_stale_base_recovery",
            tool="worktree_start",
            args={
                "repo_id": context.code_repository_name,
                "task_name": args.task_name,
                "worktree_name": args.worktree_name,
                "workflow_kind": args.workflow_kind,
            },
            required_args=["stale_base_choice"],
        ),
        "staleBases": stale,
    }


def _fast_forward_stale_branches(
    contract: WorktreeContract, stale: list[dict[str, object]], dry_run: bool
) -> list[dict[str, object]]:
    """Fast-forward `behind` branches to their upstream; return findings that could not be."""
    failures: list[dict[str, object]] = []
    for finding in stale:
        if finding["state"] != "behind":
            failures.append(
                {**finding, "recovery_error": "diverged branches cannot be fast-forwarded"}
            )
            continue
        if dry_run:
            continue
        repo = contract.code_repo_path if finding["side"] == "code" else contract.memory_repo_path
        assert repo is not None
        branch = str(finding["branch"])
        upstream = str(finding["upstream"])
        if current_branch(repo) == branch:
            result = run_git(repo, ["merge", "--ff-only", upstream])
        else:
            # state == "behind" proves the branch is an ancestor of its upstream,
            # so the forced update is a fast-forward; git still refuses branches
            # checked out in another worktree, which lands in failures.
            result = run_git(repo, ["branch", "-f", branch, upstream])
        if result.returncode != 0:
            failures.append({**finding, "recovery_error": (result.stderr or result.stdout).strip()})
    return failures


def _starting_enclosure(contract: WorktreeContract, worktree_name: str) -> StartingEnclosure:
    """The contract's own front-matter facts, for a start that has not written one yet."""
    return StartingEnclosure(
        repo_name=contract.repo_name,
        task_name=contract.task_name,
        worktree_name=worktree_name,
        worktree_group=contract.worktree_group.as_posix(),
        memory_mode=contract.memory_mode,
        code_source_branch=contract.code_source_branch,
        code_base_commit=contract.code_base_commit,
        code_repo_path=contract.code_repo_path.as_posix(),
        code_worktree=contract.code_worktree.as_posix(),
    )


def _record_start_block(
    context,
    contract: WorktreeContract,
    args: WorktreeArgs,
    beat: StartBeat,
) -> None:
    """Record a pre-contract start block (slice 5e §5.4) so the dashboard can see a start gated
    before its contract exists. Best-effort; skipped on dry runs."""
    if args.dry_run or args.worktree_name is None:
        return
    write_start_progress(
        context.coordination_root,
        _starting_enclosure(contract, args.worktree_name),
        beat,
    )


def _clear_start_block(context, contract: WorktreeContract, args: WorktreeArgs) -> None:
    """The contract now anchors the enclosure; drop the transient start-progress file."""
    if args.worktree_name is None:
        return
    clear_start_progress(context.coordination_root, contract.repo_name, args.worktree_name)


def _record_start_progress(
    context,
    contract: WorktreeContract,
    args: WorktreeArgs,
    beat: StartBeat,
) -> None:
    """Record a happy-path pre-contract start beat (§9) so the Engine Room can observe the enclosure
    assembling rather than popping in at contract-write. Non-blocked (``blocked_reason`` stays None);
    best-effort; skipped on dry runs."""
    if args.dry_run or args.worktree_name is None:
        return
    write_start_progress(
        context.coordination_root,
        _starting_enclosure(contract, args.worktree_name),
        beat,
    )


def start_result(args: WorktreeArgs) -> WorktreeCommandResult:
    context = resolve_context(args)
    contract = build_start_contract(context, args)
    if isinstance(contract, WorktreeCommandResult):
        return contract
    existing_result = _existing_contract_result(context, contract, args)
    if existing_result is not None:
        return existing_result
    preflighted = _preflighted_contract(context, contract, args)
    if isinstance(preflighted, WorktreeCommandResult):
        return preflighted
    return _create_start_enclosure(context, preflighted, args)


def _existing_contract_result(
    context, contract: WorktreeContract, args: WorktreeArgs
) -> WorktreeCommandResult | None:
    """Attach to a live contract at this path instead of recreating its worktrees.

    An abandoned contract is a tombstone and a reopened one (L11) is a reset: either
    way its worktrees/branches are gone, so start must recreate fresh rather than
    attach to a dead binding.
    """
    if not contract.contract_path.exists():
        return None
    existing = load_contract(contract.contract_path)
    if existing.cleanup in ("abandoned", "reopened"):
        return None
    if existing.cleanup == "completed":
        return reopen_required_start_result(existing)
    lineage = source_lineage_for_contract(existing)
    refusal = lineage_refusal(lineage)
    if refusal is not None:
        assert lineage is not None
        return WorktreeCommandResult(2, lineage_block_payload(lineage))
    if args.retry_provider_setup:
        return _retry_provider_setup_result(context, existing, args)
    return WorktreeCommandResult(
        0, {"state": "attached-existing-contract", **status_payload(existing)}
    )


def _preflighted_contract(
    context, contract: WorktreeContract, args: WorktreeArgs
) -> WorktreeContract | WorktreeCommandResult:
    """Run the pre-creation preflights, returning the blocked result or the usable contract.

    A fast-forward recovery may move the source branches mid-preflight, so the contract
    is rebuilt on that path and the caller works from the returned one.
    """
    # A dry-run that is about to create a master's first leaf also plans the parent
    # integration contract and branch without publishing either. That virtual parent was
    # built from the protected source's current tip, so asking the ordinary lineage reader
    # to load its deliberately absent contract would turn preview non-mutation into a false
    # unavailable refusal. Only this in-process planned-parent case bypasses the filesystem
    # projection; existing parent contracts still fail closed through the normal reader.
    parent_is_planned = (
        args.dry_run
        and bool(contract.parent_task_name)
        and contract.parent_contract_path is not None
        and not contract.parent_contract_path.exists()
    )
    lineage = None if parent_is_planned else parent_source_lineage(contract)
    refusal = lineage_refusal(lineage)
    if refusal is not None:
        assert lineage is not None
        block = lineage_block_payload(lineage)
        _record_start_block(
            context,
            contract,
            args,
            StartBeat(
                phase="source-lineage-blocked",
                blocked_reason=str(block.get("summary", "")),
            ),
        )
        return WorktreeCommandResult(2, block)
    stale_base_block = _stale_base_preflight(context, contract, args)
    if stale_base_block is not None:
        _record_start_block(
            context,
            contract,
            args,
            StartBeat(
                phase="stale-base-blocked",
                blocked_reason=str(stale_base_block.get("summary", "")),
            ),
        )
        return WorktreeCommandResult(2, stale_base_block)
    if args.stale_base_choice == "fast-forward":
        # A fast-forward recovery may have moved the source branches; rebuild the
        # contract so the recorded base commits reflect the recovered tips.
        rebuilt = build_start_contract(context, args)
        if isinstance(rebuilt, WorktreeCommandResult):
            return rebuilt
        contract = rebuilt
    long_path_block = _long_path_preflight(contract)
    if long_path_block is not None:
        return WorktreeCommandResult(2, long_path_block)
    return contract


def _create_start_enclosure(
    context, contract: WorktreeContract, args: WorktreeArgs
) -> WorktreeCommandResult:
    """Create the code worktree, prepare memory, write the contract, and set the providers up."""
    repo = context.code_repository_root
    _record_start_progress(context, contract, args, StartBeat(phase="preflight"))

    code_state = ensure_worktree(
        repo,
        contract.code_worktree,
        contract.code_work_branch,
        contract.code_source_branch,
        args.dry_run,
    )
    _record_start_progress(
        context, contract, args, StartBeat(phase="code-worktree", completed_phases=("preflight",))
    )
    memory_state = prepare_memory_for_start(contract, args)
    if memory_state["state"] == "blocked":
        raw_choices = memory_state.get("choices")
        _record_start_block(
            context,
            contract,
            args,
            StartBeat(
                phase="memory-blocked",
                completed_phases=("preflight", "code-worktree"),
                choices=tuple(str(choice) for choice in raw_choices)
                if isinstance(raw_choices, list)
                else (),
                blocked_reason=str(memory_state.get("reason", "")),
            ),
        )
        return _blocked_memory_start_result(context, args, code_state, memory_state)
    contract = _contract_after_memory_start(contract, memory_state)
    provider_plan = plan_providers_for_start(context, contract, args)
    if provider_plan["state"] == "blocked":
        _record_start_block(
            context,
            contract,
            args,
            StartBeat(
                phase="provider-blocked",
                completed_phases=("preflight", "code-worktree", "memory-compatible"),
                blocked_reason=str(provider_plan.get("reason", "")),
            ),
        )
        return _blocked_provider_start_result(
            context, args, code_state, memory_state, provider_plan
        )
    # The contract is written BEFORE provider setup launches: it is the durable
    # anchor worktree_status polls while the background thread runs (GitHub #53).
    if not args.dry_run:
        write_contract(contract.contract_path, contract)
        _clear_start_block(context, contract, args)
        # Explicit-linkage restamp (L11): a leaf whose doc already exists — a
        # reopened leaf, or one whose doc points at a finalized lifecycle — must
        # follow THIS enclosure's fresh lifecycle. First starts are a no-op (the
        # doc is authored afterwards, stamped by task_doc against the contract).
        if contract.kind == "leaf" and contract.leaf_id and contract.lifecycle_id:
            restamp_leaf_doc_lifecycle(
                contract.task_root,
                contract.leaf_id,
                contract.lifecycle_id,
                publish=lambda task_root, document: publish_queue_bound_task_facts(
                    contract,
                    lambda: write_task_docs(task_root, [document]),
                    topology_stable=True,
                ),
            )
    provider_state = run_or_launch_provider_setup(context, contract, args, provider_plan)
    if provider_state["state"] == "blocked":
        return _blocked_provider_start_result(
            context, args, code_state, memory_state, provider_state
        )
    return started_result(contract, args, code_state, memory_state, provider_state)


def prepare_providers_for_start(
    context, contract: WorktreeContract, args: WorktreeArgs
) -> dict[str, object]:
    """Preflight + execute/launch in one call (facade/CLI surface).

    `start_result` calls the two halves separately so the contract write lands
    between them; this wrapper preserves the established public contract.
    """
    plan = plan_providers_for_start(context, contract, args)
    if plan["state"] != "enabled":
        return plan
    return run_or_launch_provider_setup(context, contract, args, plan)


def plan_providers_for_start(
    context, contract: WorktreeContract, args: WorktreeArgs
) -> dict[str, object]:
    """Synchronous preflight: skip/enablement/settings checks, no execution.

    Config-level failures still block the start fast; only the long-running
    setup execution moves to the background launch.
    """
    skipped = _provider_setup_skip_state(args)
    if skipped:
        return skipped
    paths = _provider_start_paths(context, contract, args)
    provider_state = _provider_enablement_state(
        paths.target_coordination_root,
        paths.provider_settings_path,
        target_memory_root=paths.target_memory_root,
    )
    if provider_state["state"] != "enabled":
        return provider_state
    return {**provider_state, "paths": paths}


def run_or_launch_provider_setup(
    context, contract: WorktreeContract, args: WorktreeArgs, plan: dict[str, object]
) -> dict[str, object]:
    """Dry runs stay synchronous; real setup launches on a background thread."""
    if plan["state"] != "enabled":
        return plan
    paths = plan["paths"]
    assert isinstance(paths, ProviderStartPaths)
    spec = _provider_setup_request(context, args, paths)
    request = worktree_services().provider_lifecycle.setup_request(spec=spec)
    if args.dry_run:
        payload = worktree_services().provider_lifecycle.run_setup(request)
        if not payload.get("ok"):
            return {
                "state": "blocked",
                "reason": "provider setup failed",
                "payload": payload,
            }
        state_file = _write_provider_state_file(contract, payload, dry_run=True)
        return {
            "state": "planned",
            "payload": payload,
            "provider_state_file": state_file.as_posix(),
        }
    setup_config = args.provider_setup_config
    cleanup = (
        paths.provider_settings_path
        if setup_config is not None and setup_config.unlink_settings_after_setup
        else None
    )
    return worktree_services().provider_lifecycle.launch_setup(
        request=request,
        contract=contract,
        write_state_file=lambda payload: _write_provider_state_file(
            contract, payload, dry_run=False
        ),
        settings_cleanup=cleanup,
    )


def _retry_provider_setup_result(
    context, contract: WorktreeContract, args: WorktreeArgs
) -> WorktreeCommandResult:
    if worktree_services().provider_lifecycle.setup_running(contract):
        return WorktreeCommandResult(
            2,
            {
                "state": "blocked",
                "summary": (
                    "Provider setup is still running for this worktree (fresh "
                    "heartbeat); poll worktree_status instead of retrying."
                ),
                "providers": worktree_services().provider_lifecycle.setup_status(contract),
                **next_guidance(
                    "continue_work",
                    tool="worktree_status",
                    args=contract_next_args(contract),
                ),
            },
        )
    provider_plan = plan_providers_for_start(context, contract, args)
    if provider_plan["state"] == "blocked":
        return WorktreeCommandResult(
            2, {**status_payload(contract), "state": "blocked", "providers": provider_plan}
        )
    provider_state = run_or_launch_provider_setup(context, contract, args, provider_plan)
    return WorktreeCommandResult(
        0,
        {
            **status_payload(contract),
            "state": "provider-setup-retried",
            "providers": provider_state,
        },
    )


def _write_provider_state_file(
    contract: WorktreeContract,
    payload: dict[str, object],
    dry_run: bool,
) -> Path:
    state_file = contract.worktree_group / "provider-runtime" / "provider-state.json"
    if dry_run:
        return state_file
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(_provider_state_payload(contract, payload), indent=2) + "\n",
        encoding="utf-8",
    )
    return state_file


def _provider_state_payload(
    contract: WorktreeContract,
    payload: dict[str, object],
) -> dict[str, object]:
    isolated = payload.get("isolatedProviderSettings")
    return {
        "schema": "ar-worktree-provider-state/v1",
        "taskName": contract.task_name,
        "repoName": contract.repo_name,
        "worktreeGroup": contract.worktree_group.as_posix(),
        "codeWorktree": contract.code_worktree.as_posix(),
        "memoryWorktree": contract.memory_worktree.as_posix()
        if contract.memory_worktree is not None
        else None,
        "isolatedProviderSettings": isolated,
    }


def _provider_setup_skip_state(args: WorktreeArgs) -> dict[str, object] | None:
    if args.skip_provider_setup:
        return {"state": "skipped", "reason": "provider setup was skipped"}
    if getattr(args, "provider_setup_config", None) is None:
        return {
            "state": "skipped",
            "reason": "provider setup requires MCP-derived provider settings",
        }
    return None


def _provider_start_paths(
    context, contract: WorktreeContract, args: WorktreeArgs
) -> ProviderStartPaths:
    setup_config = args.provider_setup_config
    assert setup_config is not None
    return ProviderStartPaths(
        target_coordination_root=setup_config.coordination_root.resolve(),
        source_coordination_root=(
            setup_config.seed_source_coordination_root or context.coordination_root
        ).resolve(),
        source_repo_root=context.code_repository_root.resolve(),
        target_repo_root=contract.code_worktree.resolve(),
        source_memory_root=context.memory_root.resolve()
        if getattr(context, "memory_root", None) is not None
        else None,
        target_memory_root=_grepai_target_memory_root(contract),
        provider_runtime_root=(contract.worktree_group / "provider-runtime").resolve(),
        provider_settings_path=setup_config.settings_path.resolve(),
    )


def _provider_enablement_state(
    target_coordination_root: Path,
    provider_settings_path: Path,
    *,
    target_memory_root: Path | None,
) -> dict[str, object]:
    try:
        settings = worktree_services().provider_lifecycle.load_settings(provider_settings_path)
    except RuntimeError as error:
        return {
            "state": "blocked",
            "reason": str(error),
            "targetCoordinationRoot": target_coordination_root.as_posix(),
        }
    cgc_enabled = bool(settings) and worktree_services().provider_lifecycle.provider_enabled(
        settings, "codegraphcontext-code"
    )
    grepai_enabled = bool(settings) and worktree_services().provider_lifecycle.provider_enabled(
        settings, "grepai-memory"
    )
    grepai_worktree_enabled = grepai_enabled and target_memory_root is not None
    if cgc_enabled or grepai_worktree_enabled:
        return _enabled_provider_state(cgc_enabled, grepai_worktree_enabled)
    return {
        "state": "skipped",
        "reason": _provider_enablement_skip_reason(
            cgc_enabled=cgc_enabled,
            grepai_enabled=grepai_enabled,
            target_memory_root=target_memory_root,
        ),
        "settingsFile": worktree_services()
        .provider_lifecycle.settings_path(provider_settings_path)
        .as_posix(),
    }


def _enabled_provider_state(
    cgc_enabled: bool,
    grepai_worktree_enabled: bool,
) -> dict[str, object]:
    return {
        "state": "enabled",
        "codegraphcontext-code": cgc_enabled,
        "grepai-memory": grepai_worktree_enabled,
    }


def _provider_enablement_skip_reason(
    *,
    cgc_enabled: bool,
    grepai_enabled: bool,
    target_memory_root: Path | None,
) -> str:
    reasons = []
    if not cgc_enabled:
        reasons.append("codegraphcontext-code is not enabled")
    if not grepai_enabled:
        reasons.append("grepai-memory is not enabled")
    elif target_memory_root is None:
        reasons.append("grepai-memory requires worktree memory")
    return "; ".join(reasons)


def _grepai_target_memory_root(contract: WorktreeContract) -> Path | None:
    if contract.memory_mode == "external":
        return contract.memory_worktree.resolve() if contract.memory_worktree is not None else None
    if contract.memory_mode == "internal":
        return (contract.code_worktree / "ar-memory").resolve()
    return None


def _provider_setup_request(
    context,
    args: WorktreeArgs,
    paths: ProviderStartPaths,
):
    lifecycle = worktree_services().provider_lifecycle
    return ProviderSetupRequestSpec(
        action="prepare",
        coordination_root=paths.target_coordination_root,
        settings_path=lifecycle.settings_path(paths.provider_settings_path),
        timeout=getattr(args, "provider_timeout", 1800),
        dry_run=args.dry_run,
        skip_grepai=paths.target_memory_root is None,
        cgc_seed=lifecycle.cgc_seed_options(
            source_coordination_root=paths.source_coordination_root,
            repo_id=context.code_repository_name,
            source_repo_root=paths.source_repo_root,
            target_repo_root=paths.target_repo_root,
        ),
        cgc_isolated=lifecycle.isolated_cgc_options(runtime_root=paths.provider_runtime_root),
        grepai_seed=lifecycle.grepai_seed_options(
            source_coordination_root=paths.source_coordination_root,
            source_settings_path=paths.provider_settings_path,
            project_id=context.code_repository_name,
            target_memory_root=paths.target_memory_root,
        ),
        grepai_isolated=lifecycle.isolated_grepai_options(
            runtime_root=paths.provider_runtime_root,
            project_id=context.code_repository_name,
            target_memory_root=paths.target_memory_root,
            allow_missing_roots=args.dry_run,
        ),
    )


def _memory_source_state(
    contract: WorktreeContract, args: WorktreeArgs
) -> dict[str, object] | None:
    """The state that settles the memory side before its ledger is ever read.

    Either there is no external memory repo to prepare, or the one configured cannot
    be started from (absent, or dirty in its official checkout).
    """
    if contract.memory_mode == "internal":
        return {"state": "internal", "reason": "memory lives in the code worktree"}
    if contract.memory_mode == "disabled":
        return {"state": "disabled"}
    assert contract.memory_repo_path is not None
    if not contract.memory_repo_path.exists():
        return _missing_memory_repo_state(args)
    if (contract.memory_repo_path / ".git").exists() and has_changes(contract.memory_repo_path):
        return _dirty_memory_source_state(args)
    return None


def _rebased_on_mapped_commit(
    contract: WorktreeContract, ledger: MemoryLedger, args: WorktreeArgs
) -> tuple[WorktreeContract, MemoryLedger] | dict[str, object]:
    """Rebind the contract onto a base the official ledger maps, or the state that blocks start."""
    disabled = _disabled_memory_choice(args)
    if disabled:
        return disabled
    reconciled = _reconcile_missing_mapping(contract, ledger, args)
    if reconciled is None:
        return _missing_mapping_state(contract, ledger)
    return reconciled


def prepare_memory_for_start(contract: WorktreeContract, args: WorktreeArgs) -> dict[str, object]:
    source_state = _memory_source_state(contract, args)
    if source_state is not None:
        return source_state
    ledger = _load_memory_ledger(contract, args)
    if isinstance(ledger, dict):
        return ledger
    reconciled_base: str | None = None
    if find_mapping(ledger, contract.code_base_commit) is None:
        rebased = _rebased_on_mapped_commit(contract, ledger, args)
        if isinstance(rebased, dict):
            return rebased
        contract, ledger = rebased
        reconciled_base = contract.memory_base_commit
    # The reconciliation rebind re-widens the contract's optional memory fields; re-narrow both.
    assert contract.memory_repo_path is not None
    assert contract.memory_worktree is not None
    memory_source_branch = _ensure_memory_source_branch(contract, args.dry_run)
    memory_branch_state = ensure_worktree(
        contract.memory_repo_path,
        contract.memory_worktree,
        contract.memory_work_branch,
        contract.memory_source_branch,
        args.dry_run,
    )
    mtime_sync = _sync_worktree_memory_mtimes(contract, args.dry_run)
    result: dict[str, object] = {
        "state": "compatible",
        "worktree": memory_branch_state,
        "memorySourceBranch": memory_source_branch,
        "mtimeSync": mtime_sync,
        "lastVerifiedCodeCommit": ledger.last_verified_code_commit,
        "lastMemoryContentCommit": ledger.last_memory_content_commit,
    }
    if reconciled_base is not None:
        # A reconciliation just advanced the official memory tip; the caller re-bases the persisted
        # contract onto it so status/closeout see the base the mapping was recorded against.
        result["reconciledMemoryBaseCommit"] = reconciled_base
    return result


def _ensure_memory_source_branch(contract: WorktreeContract, dry_run: bool) -> dict[str, object]:
    """Auto-create a missing memory source branch off the official memory tip (issue #54).

    The code source branch name is the template; agents previously had to create the
    matching memory branch by hand before worktree_start would succeed. The branch
    bases on the validated official checkout HEAD (`memory_base_commit`), whose ledger
    was just proven to map `code_base_commit`.
    """
    assert contract.memory_repo_path is not None
    if branch_exists(contract.memory_repo_path, contract.memory_source_branch):
        return {"state": "existing", "branch": contract.memory_source_branch}
    if dry_run:
        return {
            "state": "would-create-from-official-tip",
            "branch": contract.memory_source_branch,
            "base": contract.memory_base_commit,
        }
    require_git(
        contract.memory_repo_path,
        ["branch", contract.memory_source_branch, contract.memory_base_commit],
    )
    return {
        "state": "created-from-official-tip",
        "branch": contract.memory_source_branch,
        "base": contract.memory_base_commit,
    }


def _sync_worktree_memory_mtimes(contract: WorktreeContract, dry_run: bool) -> dict[str, object]:
    """Mirror source memory-repo file mtimes onto the freshly checked-out worktree.

    `git checkout` stamps every file with the current time. grepai's watcher skips
    unchanged files by comparing ModTime against its index, so brand-new mtimes make
    every file look modified and force a full re-embed — defeating the DB clone. Copying
    each file's mtime from the source memory repo lets the watcher reuse the cloned index
    (files genuinely newer than the index still re-embed, exactly as on the source).

    260707-HFX-L2: files whose content diverges between the worktree HEAD and the
    source HEAD are deliberately left with their fresh checkout mtimes — stamping
    the source's old mtime onto different content would make the watcher skip exactly
    the delta and serve a silently wrong index. The fresh mtimes make the watcher's
    incremental scan re-embed precisely the divergence. The comparison is HEAD vs
    HEAD: uncommitted changes in the SOURCE checkout are outside this guard (the
    mtime copied from such a file is at least as new as its content, so the watcher
    still re-embeds it — over-embedding, never silent staleness).
    """
    if dry_run:
        return {"state": "skipped", "reason": "dry-run"}
    if contract.memory_repo_path is None or contract.memory_worktree is None:
        return {"state": "skipped", "reason": "no external memory worktree"}
    source = contract.memory_repo_path
    target = contract.memory_worktree
    divergent = _memory_divergence_paths(source, target)
    synced = 0
    missing = 0
    left_fresh = 0
    for path in target.rglob("*"):
        if ".git" in path.parts or not path.is_file():
            continue
        relative = path.relative_to(target).as_posix()
        if divergent is not None and relative in divergent:
            left_fresh += 1
            continue
        source_file = source / relative
        try:
            stat = source_file.stat()
        except OSError:
            missing += 1
            continue
        os.utime(path, (stat.st_atime, stat.st_mtime))
        synced += 1
    result: dict[str, object] = {
        "state": "synced",
        "filesSynced": synced,
        "filesMissingInSource": missing,
        "divergentLeftFresh": left_fresh,
    }
    if divergent is None:
        result["divergenceState"] = "uncomputable; synced all (pre-L2 behavior)"
    return result


def _memory_divergence_paths(source: Path, target: Path) -> set[str] | None:
    """Paths whose content differs between the worktree HEAD and the source HEAD.

    Computed in the source repo (shared object database for worktrees); ``None``
    when git cannot relate the heads, in which case the caller falls back to
    syncing everything (the pre-L2 behavior) rather than guessing.
    """
    try:
        source_head = head_commit(source)
        target_head = head_commit(target)
    except Exception:
        return None
    if source_head == target_head:
        return set()
    diff = run_git(source, ["diff", "--name-only", source_head, target_head])
    if diff.returncode != 0:
        return None
    return {line.strip() for line in diff.stdout.splitlines() if line.strip()}


def _disabled_memory_choice(args: WorktreeArgs) -> dict[str, object] | None:
    if args.memory_choice == "disabled-memory":
        return {"state": "disabled", "reason": "human selected disabled memory"}
    return None


def _missing_memory_repo_state(args: WorktreeArgs) -> dict[str, object]:
    disabled = _disabled_memory_choice(args)
    if disabled:
        return disabled
    return {
        "state": "blocked",
        "reason": "external memory repo is missing; run c-00-initialize-memory-repo before starting an external-memory worktree",
        "choices": ["initialize-memory-repo", "disabled-memory", "custom"],
    }


def _dirty_memory_source_state(args: WorktreeArgs) -> dict[str, object]:
    disabled = _disabled_memory_choice(args)
    if disabled:
        return disabled
    return {
        "state": "blocked",
        "reason": "external memory source repo has uncommitted changes; commit refreshed onboarding and ledger before starting worktrees",
        "choices": ["commit-memory-and-ledger-first", "disabled-memory", "custom"],
    }


def _load_memory_ledger(
    contract: WorktreeContract, args: WorktreeArgs
) -> MemoryLedger | dict[str, object]:
    assert contract.memory_repo_path is not None
    try:
        return load_ledger(contract.memory_repo_path / "memory.md")
    except LedgerError as error:
        disabled = _disabled_memory_choice(args)
        if disabled:
            return disabled
        return {
            "state": "blocked",
            "reason": str(error),
            # Only consumable choices (260703-L18 review L18R-3): "reconciliation" needs a
            # parseable ledger to map against, which a LedgerError path cannot supply, and
            # "custom" has no handler — advertising either here would be an F-R dead-end.
            "choices": ["initialize-memory-repo", "disabled-memory"],
        }


def _reconcile_missing_mapping(
    contract: WorktreeContract, ledger: MemoryLedger, args: WorktreeArgs
) -> tuple[WorktreeContract, MemoryLedger] | None:
    """``memory_choice="reconciliation"`` (260703-L18 finding 7 / friction F-R): record the unmapped
    code base -> the ledger's current memory content tip, exactly the way closeout ledger syncs do,
    then let the start proceed on the now-present mapping.

    Mirrors the owner's hand precedent (memory commit ``af50a05``): the header ``lastVerifiedCodeCommit``
    advances to the code base commit, a newest-first mapping row is prepended, and a ``Ledger sync``
    commit lands in the memory SOURCE repo -- the memory CONTENT tip is unchanged (this is a code-only
    catch-up, no onboarding changed). Returns the advanced ``(contract, ledger)`` so the caller bases
    the memory branch off the recorded commit; ``None`` when reconciliation was not the chosen recovery."""
    if args.memory_choice != "reconciliation":
        return None
    assert contract.memory_repo_path is not None
    # PR #100 review (Codex P1): the mapping commit must land on the memory SOURCE branch —
    # the worktree is created FROM that branch, so committing to whatever happens to be
    # checked out would leave the source branch unmapped while start reports compatible.
    # Refuse loudly instead of writing to the wrong branch.
    current_branch = require_git(contract.memory_repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    if current_branch != contract.memory_source_branch:
        raise LedgerError(
            "reconciliation writes the ledger mapping to the memory source branch "
            f"'{contract.memory_source_branch}', but the official memory repo is checked out "
            f"on '{current_branch}'; checkout the source branch and re-run worktree_start"
        )
    code_commit = contract.code_base_commit
    memory_commit = ledger.last_memory_content_commit
    updated = prepend_mapping(ledger, code_commit, memory_commit)
    if args.dry_run:
        return contract, updated
    write_ledger(contract.memory_repo_path / "memory.md", updated)
    require_git(contract.memory_repo_path, ["add", "memory.md"])
    commit_if_dirty(
        contract.memory_repo_path,
        args.ledger_commit_message
        or f"[{contract.task_id}] Ledger sync: {code_commit} -> {memory_commit}",
    )
    advanced = replace(
        contract,
        memory_base_commit=memory_base_for_source(
            contract.memory_repo_path, contract.memory_source_branch
        ),
    )
    return advanced, updated


def _missing_mapping_state(contract: WorktreeContract, ledger) -> dict[str, object]:
    # Advertise ONLY executable choices (260703-L18 finding 7): both are consumed in
    # prepare_memory_for_start's missing-mapping path (reconciliation records the mapping and proceeds;
    # disabled-memory drops external memory). 'custom' was advertised but wired nowhere -- removed.
    return {
        "state": "blocked",
        "reason": "no exact ledger mapping for selected code base commit",
        "codeBaseCommit": contract.code_base_commit,
        "lastVerifiedCodeCommit": ledger.last_verified_code_commit,
        "choices": ["reconciliation", "disabled-memory"],
    }
