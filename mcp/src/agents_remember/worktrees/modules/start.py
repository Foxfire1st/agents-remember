from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

from agents_remember.kernel.git_freshness import freshness_to_packet, read_branch_freshness
from agents_remember.kernel.memory_ledger import (
    LedgerError,
    MemoryLedger,
    find_mapping,
    load_ledger,
    prepend_mapping,
    write_ledger,
)
from agents_remember.providers import provider_setup
from agents_remember.tasks import read_task_doc
from agents_remember.tasks.leaf_doc import restamp_leaf_doc_lifecycle
from agents_remember.worktrees.modules import provider_async
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
    run_git,
)
from agents_remember.worktrees.modules.guidance import (
    contract_next_args,
    contract_payload,
    next_guidance,
    status_payload,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.start_progress import clear_start_progress, write_start_progress
from agents_remember.worktrees.task_resolver import (
    resolve_active_task_root,
    resolve_leaf_enclosure_contract,
    series_contract_path,
    slugify,
)
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    default_contract,
    default_series_contract,
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
    contract_path = resolve_leaf_enclosure_contract(
        context.coordination_root,
        context.code_repository_name,
        args.task_name,
        leaf_id=args.leaf_id,
        parent_task=args.parent_task,
    )
    if contract_path is None:
        raise RuntimeError("--task-name resolved no leaf enclosure; pass --leaf-id")
    return load_contract(contract_path)


def status_result(args: WorktreeArgs) -> WorktreeCommandResult:
    contract = load_contract_from_args(args)
    return WorktreeCommandResult(0, status_payload(contract))


def attach_result(args: WorktreeArgs) -> WorktreeCommandResult:
    contract = load_contract_from_args(args)
    return WorktreeCommandResult(
        0, {"state": "attached", "attached": True, **status_payload(contract)}
    )


def _start_memory_repo(context, memory_mode: str):
    if memory_mode != "external":
        return None
    return context.coordination_root / "memory-repos" / f"ar-{context.code_repository_name}"


def _memory_base_commit(memory_repo) -> str:
    if memory_repo is None:
        return ""
    if not memory_repo.exists() or not (memory_repo / ".git").exists():
        return ""
    return head_commit(memory_repo)


def _memory_base_for_source(memory_repo, memory_source_branch: str) -> str:
    """The memory base = the tip of the memory source branch the worktree is created off.

    Mirrors the code-base derivation (``head_commit(repo, source_branch)``) instead of reading the
    memory repo's current HEAD, which may be checked out on an unrelated branch (e.g. another in-flight
    task) and would record a divergent base that breaks closeout's "memory source branch moved"
    preflight. Falls back to the repo HEAD when external memory is off or the source branch is not
    present yet (it is auto-created off the official tip during memory start).
    """
    if memory_repo is None or not memory_source_branch:
        return _memory_base_commit(memory_repo)
    if not memory_repo.exists() or not (memory_repo / ".git").exists():
        return ""
    if branch_exists(memory_repo, memory_source_branch):
        return head_commit(memory_repo, memory_source_branch)
    return _memory_base_commit(memory_repo)


def _external_memory_value(memory_mode: str, value: str) -> str:
    return value if memory_mode == "external" else ""


def _task_root_has_master_artifact(task_root: Path) -> bool:
    json_path = task_root / "task.json"
    if json_path.exists():
        try:
            return read_task_doc(json_path).kind == "master"
        except ValueError:
            return False
    markdown_path = task_root / "task.md"
    if not markdown_path.exists():
        return False
    try:
        head = markdown_path.read_text(encoding="utf-8")[:1000]
    except OSError:
        return False
    return "**Type:** Master" in head


def _ensure_branch(repo: Path, branch: str, source: str, *, dry_run: bool) -> None:
    if branch_exists(repo, branch) or dry_run:
        return
    require_git(repo, ["branch", branch, source])


def _parent_series_contract(
    context, args: WorktreeArgs, memory_mode: str
) -> WorktreeContract | None:
    if not args.task_name:
        return None
    task_root = resolve_active_task_root(
        context.coordination_root,
        context.code_repository_name,
        args.task_name,
        parent_task=args.parent_task,
    )
    path = series_contract_path(task_root)
    if not path.exists():
        if not _task_root_has_master_artifact(task_root):
            return None
        repo = context.code_repository_root
        protected_branch = args.source_branch or current_branch(repo)
        integration_branch = f"ar/{slugify(args.task_name)}"
        leaf_branch = args.work_branch or f"ar/{args.worktree_name}"
        if leaf_branch == integration_branch:
            raise RuntimeError(
                "master-series leaf work branch would equal the integration branch; "
                "choose a distinct worktree_name or work_branch"
            )
        _ensure_branch(repo, integration_branch, protected_branch, dry_run=args.dry_run)
        memory_repo = _start_memory_repo(context, memory_mode)
        memory_source_branch = _external_memory_value(memory_mode, protected_branch)
        memory_work_branch = _external_memory_value(memory_mode, integration_branch)
        if (
            memory_repo is not None
            and (memory_repo / ".git").exists()
            and memory_source_branch
            and memory_work_branch
        ):
            _ensure_branch(
                memory_repo,
                memory_work_branch,
                memory_source_branch,
                dry_run=args.dry_run,
            )
        contract = default_series_contract(
            task_name=args.task_name,
            repo_name=context.code_repository_name,
            workflow_kind=args.workflow_kind,
            memory_mode=memory_mode,
            coordination_root=context.coordination_root,
            code_repo_path=repo,
            protected_branch=protected_branch,
            integration_branch=integration_branch,
            code_base_commit=head_commit(repo, protected_branch),
            memory_repo_path=memory_repo,
            memory_source_branch=memory_source_branch,
            memory_work_branch=memory_work_branch,
            memory_base_commit=_memory_base_for_source(memory_repo, memory_source_branch),
            parent_task_name=args.parent_task or "",
            task_root=task_root,
        )
        if not args.dry_run:
            write_contract(contract.contract_path, contract)
        return contract
    try:
        contract = load_contract(path)
    except ContractError as exc:
        raise RuntimeError(f"parent task contract is not readable: {path}") from exc
    if contract.kind != "series":
        raise RuntimeError(f"parent task contract is not a series contract: {path}")
    return contract


def _build_start_contract(context, args: WorktreeArgs) -> WorktreeContract:
    assert args.task_name is not None
    assert args.worktree_name is not None
    repo = context.code_repository_root
    memory_mode = args.memory_mode or context.memory_mode
    parent_series = _parent_series_contract(context, args, memory_mode)
    source_branch = args.source_branch or (
        parent_series.code_work_branch if parent_series is not None else current_branch(repo)
    )
    work_branch = args.work_branch or f"ar/{args.worktree_name}"
    if args.dry_run and parent_series is not None and not branch_exists(repo, source_branch):
        base_commit = parent_series.code_base_commit
    else:
        base_commit = head_commit(repo, source_branch)
    memory_repo = _start_memory_repo(context, memory_mode)
    memory_source_branch = _external_memory_value(memory_mode, source_branch)
    # Memory base = the tip of the memory source branch this worktree is created off (mirroring the
    # code base), not the memory repo's current HEAD, which may be on an unrelated branch.
    if (
        args.dry_run
        and parent_series is not None
        and memory_repo is not None
        and memory_source_branch
        and not branch_exists(memory_repo, memory_source_branch)
    ):
        memory_base = parent_series.memory_base_commit
    else:
        memory_base = _memory_base_for_source(memory_repo, memory_source_branch)
    return default_contract(
        task_name=args.task_name,
        repo_name=context.code_repository_name,
        workflow_kind=args.workflow_kind,
        memory_mode=memory_mode,
        coordination_root=context.coordination_root,
        code_repo_path=repo,
        code_source_branch=source_branch,
        code_work_branch=work_branch,
        code_base_commit=base_commit,
        worktree_name=args.worktree_name,
        memory_repo_path=memory_repo,
        memory_source_branch=memory_source_branch,
        memory_work_branch=_external_memory_value(memory_mode, work_branch),
        memory_base_commit=memory_base,
        lifecycle_id=args.lifecycle_id,
        leaf_id=args.leaf_id or args.worktree_name,
        parent_task_name=parent_series.task_name if parent_series is not None else "",
        parent_contract_path=parent_series.contract_path if parent_series is not None else None,
    )


def _blocked_memory_start_result(
    context, args: WorktreeArgs, code_state: str, memory_state: dict[str, object]
) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        2,
        {
            "state": "blocked",
            "summary": "Code worktree is prepared, but external memory cannot be used until the developer selects a recovery path.",
            **next_guidance(
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
        return replace(
            contract,
            memory_mode="disabled",
            memory_repo_path=None,
            memory_source_branch="",
            memory_work_branch="",
            memory_base_commit="",
            memory_worktree=None,
            ledger_path=None,
            memory_state="disabled",
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
            **next_guidance(
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


def _started_result(
    contract: WorktreeContract,
    code_state: str,
    memory_state: dict[str, object],
    provider_state: dict[str, object],
) -> WorktreeCommandResult:
    summary = "Worktree task started; continue the wrapped workflow before closeout."
    if provider_state.get("state") == "starting":
        summary = (
            "Worktree task started; provider setup is running in the background — "
            "poll worktree_status until its providers block reaches a terminal state."
        )
    return WorktreeCommandResult(
        0,
        {
            "state": "started",
            "summary": summary,
            **next_guidance(
                "continue_work",
                tool="worktree_status",
                args=contract_next_args(contract),
            ),
            "code_worktree": code_state,
            "memory": memory_state,
            "providers": provider_state,
            "enclosure_path": contract.contract_path.as_posix(),
            "contract_path": contract.contract_path.as_posix(),
            "leaf_id": contract.leaf_id,
            "task_artifact": contract.task_artifact.as_posix(),
            "contract": contract_payload(contract),
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
        **next_guidance(
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


def _record_start_block(
    context,
    contract: WorktreeContract,
    args: WorktreeArgs,
    *,
    phase: str,
    reason: str,
    completed: tuple[str, ...],
    choices: tuple[str, ...],
) -> None:
    """Record a pre-contract start block (slice 5e §5.4) so the dashboard can see a start gated
    before its contract exists. Best-effort; skipped on dry runs."""
    if args.dry_run or args.worktree_name is None:
        return
    write_start_progress(
        context.coordination_root,
        repo_name=contract.repo_name,
        task_name=contract.task_name,
        worktree_name=args.worktree_name,
        worktree_group=contract.worktree_group.as_posix(),
        phase=phase,
        memory_mode=contract.memory_mode,
        code_source_branch=contract.code_source_branch,
        code_base_commit=contract.code_base_commit,
        code_repo_path=contract.code_repo_path.as_posix(),
        code_worktree=contract.code_worktree.as_posix(),
        blocked_reason=reason,
        completed_phases=completed,
        choices=choices,
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
    *,
    phase: str,
    completed: tuple[str, ...],
) -> None:
    """Record a happy-path pre-contract start beat (§9) so the Engine Room can observe the enclosure
    assembling rather than popping in at contract-write. Non-blocked (``blocked_reason`` stays None);
    best-effort; skipped on dry runs."""
    if args.dry_run or args.worktree_name is None:
        return
    write_start_progress(
        context.coordination_root,
        repo_name=contract.repo_name,
        task_name=contract.task_name,
        worktree_name=args.worktree_name,
        worktree_group=contract.worktree_group.as_posix(),
        phase=phase,
        memory_mode=contract.memory_mode,
        code_source_branch=contract.code_source_branch,
        code_base_commit=contract.code_base_commit,
        code_repo_path=contract.code_repo_path.as_posix(),
        code_worktree=contract.code_worktree.as_posix(),
        completed_phases=completed,
    )


def start_result(args: WorktreeArgs) -> WorktreeCommandResult:
    context = resolve_context(args)
    repo = context.code_repository_root
    contract = _build_start_contract(context, args)

    if contract.contract_path.exists():
        existing = load_contract(contract.contract_path)
        # An abandoned contract is a tombstone and a reopened one (L11) is a reset:
        # either way its worktrees/branches are gone, so start must recreate fresh
        # rather than attach to a dead binding.
        if existing.cleanup not in ("abandoned", "reopened"):
            if args.retry_provider_setup:
                return _retry_provider_setup_result(context, existing, args)
            return WorktreeCommandResult(
                0, {"state": "attached-existing-contract", **status_payload(existing)}
            )

    stale_base_block = _stale_base_preflight(context, contract, args)
    if stale_base_block is not None:
        _record_start_block(
            context,
            contract,
            args,
            phase="stale-base-blocked",
            reason=str(stale_base_block.get("summary", "")),
            completed=(),
            choices=(),
        )
        return WorktreeCommandResult(2, stale_base_block)
    if args.stale_base_choice == "fast-forward":
        # A fast-forward recovery may have moved the source branches; rebuild the
        # contract so the recorded base commits reflect the recovered tips.
        contract = _build_start_contract(context, args)

    long_path_block = _long_path_preflight(contract)
    if long_path_block is not None:
        return WorktreeCommandResult(2, long_path_block)
    _record_start_progress(context, contract, args, phase="preflight", completed=())

    code_state = ensure_worktree(
        repo,
        contract.code_worktree,
        contract.code_work_branch,
        contract.code_source_branch,
        args.dry_run,
    )
    _record_start_progress(context, contract, args, phase="code-worktree", completed=("preflight",))
    memory_state = prepare_memory_for_start(contract, args)
    if memory_state["state"] == "blocked":
        raw_choices = memory_state.get("choices")
        _record_start_block(
            context,
            contract,
            args,
            phase="memory-blocked",
            reason=str(memory_state.get("reason", "")),
            completed=("preflight", "code-worktree"),
            choices=tuple(str(choice) for choice in raw_choices)
            if isinstance(raw_choices, list)
            else (),
        )
        return _blocked_memory_start_result(context, args, code_state, memory_state)
    contract = _contract_after_memory_start(contract, memory_state)
    provider_plan = plan_providers_for_start(context, contract, args)
    if provider_plan["state"] == "blocked":
        _record_start_block(
            context,
            contract,
            args,
            phase="provider-blocked",
            reason=str(provider_plan.get("reason", "")),
            completed=("preflight", "code-worktree", "memory-compatible"),
            choices=(),
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
                contract.task_root, contract.leaf_id, contract.lifecycle_id
            )
    provider_state = run_or_launch_provider_setup(context, contract, args, provider_plan)
    if provider_state["state"] == "blocked":
        return _blocked_provider_start_result(
            context, args, code_state, memory_state, provider_state
        )
    return _started_result(contract, code_state, memory_state, provider_state)


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
    request = _provider_setup_request(context, args, paths)
    if args.dry_run:
        payload = provider_setup.run_provider_setup(request)
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
    return provider_async.launch_provider_setup(
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
    if provider_async.provider_setup_running(contract):
        return WorktreeCommandResult(
            2,
            {
                "state": "blocked",
                "summary": (
                    "Provider setup is still running for this worktree (fresh "
                    "heartbeat); poll worktree_status instead of retrying."
                ),
                "providers": provider_async.provider_setup_status(contract),
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
        settings = provider_setup.load_settings(provider_settings_path)
    except RuntimeError as error:
        return {
            "state": "blocked",
            "reason": str(error),
            "targetCoordinationRoot": target_coordination_root.as_posix(),
        }
    cgc_enabled = bool(settings) and provider_setup.provider_enabled(
        settings, "codegraphcontext-code"
    )
    grepai_enabled = bool(settings) and provider_setup.provider_enabled(settings, "grepai-memory")
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
        "settingsFile": provider_setup.settings_path(provider_settings_path).as_posix(),
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
) -> provider_setup.ProviderSetupRequest:
    return provider_setup.ProviderSetupRequest(
        action="prepare",
        coordination_root=paths.target_coordination_root,
        settings_path=provider_setup.settings_path(paths.provider_settings_path),
        timeout=getattr(args, "provider_timeout", 1800),
        dry_run=args.dry_run,
        skip_grepai=paths.target_memory_root is None,
        cgc_seed=provider_setup.CgcSeedOptions(
            source_coordination_root=paths.source_coordination_root,
            repo_id=context.code_repository_name,
            source_repo_root=paths.source_repo_root,
            target_repo_root=paths.target_repo_root,
        ),
        cgc_isolated=provider_setup.IsolatedCgcOptions(runtime_root=paths.provider_runtime_root),
        grepai_seed=provider_setup.GrepaiSeedOptions(
            source_coordination_root=paths.source_coordination_root,
            source_settings_path=paths.provider_settings_path,
            project_id=context.code_repository_name,
            target_memory_root=paths.target_memory_root,
        ),
        grepai_isolated=provider_setup.IsolatedGrepaiOptions(
            runtime_root=paths.provider_runtime_root,
            project_id=context.code_repository_name,
            target_memory_root=paths.target_memory_root,
            allow_missing_roots=args.dry_run,
        ),
    )


def prepare_memory_for_start(contract: WorktreeContract, args: WorktreeArgs) -> dict[str, object]:
    if contract.memory_mode == "internal":
        return {"state": "internal", "reason": "memory lives in the code worktree"}
    if contract.memory_mode == "disabled":
        return {"state": "disabled"}
    assert contract.memory_repo_path is not None
    if not contract.memory_repo_path.exists():
        return _missing_memory_repo_state(args)
    if (contract.memory_repo_path / ".git").exists() and has_changes(contract.memory_repo_path):
        return _dirty_memory_source_state(args)
    ledger = _load_memory_ledger(contract, args)
    if isinstance(ledger, dict):
        return ledger
    mapping = find_mapping(ledger, contract.code_base_commit)
    reconciled_base: str | None = None
    if mapping is None:
        disabled = _disabled_memory_choice(args)
        if disabled:
            return disabled
        reconciled = _reconcile_missing_mapping(contract, ledger, args)
        if reconciled is None:
            return _missing_mapping_state(contract, ledger)
        contract, ledger = reconciled
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
    """
    if dry_run:
        return {"state": "skipped", "reason": "dry-run"}
    if contract.memory_repo_path is None or contract.memory_worktree is None:
        return {"state": "skipped", "reason": "no external memory worktree"}
    source = contract.memory_repo_path
    target = contract.memory_worktree
    synced = 0
    missing = 0
    for path in target.rglob("*"):
        if ".git" in path.parts or not path.is_file():
            continue
        source_file = source / path.relative_to(target)
        try:
            stat = source_file.stat()
        except OSError:
            missing += 1
            continue
        os.utime(path, (stat.st_atime, stat.st_mtime))
        synced += 1
    return {"state": "synced", "filesSynced": synced, "filesMissingInSource": missing}


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
        memory_base_commit=_memory_base_for_source(
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
