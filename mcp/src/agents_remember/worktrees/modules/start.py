from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from agents_remember.kernel.memory_ledger import (
    LedgerError,
    MemoryLedger,
    find_mapping,
    load_ledger,
)
from agents_remember.providers import provider_setup
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.context import resolve_context
from agents_remember.worktrees.modules.git import (
    current_branch,
    ensure_worktree,
    has_changes,
    head_commit,
)
from agents_remember.worktrees.modules.guidance import (
    contract_next_args,
    contract_payload,
    next_guidance,
    status_payload,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    default_contract,
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
    contract_path = context.task_root / "contract.md"
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


def _external_memory_value(memory_mode: str, value: str) -> str:
    return value if memory_mode == "external" else ""


def _build_start_contract(context, args: WorktreeArgs) -> WorktreeContract:
    assert args.task_name is not None
    assert args.worktree_name is not None
    repo = context.code_repository_root
    source_branch = args.source_branch or current_branch(repo)
    work_branch = args.work_branch or f"ar/{args.worktree_name}"
    base_commit = head_commit(repo, source_branch)
    memory_mode = args.memory_mode or context.memory_mode
    memory_repo = _start_memory_repo(context, memory_mode)
    memory_base = _memory_base_commit(memory_repo)
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
        memory_source_branch=_external_memory_value(memory_mode, source_branch),
        memory_work_branch=_external_memory_value(memory_mode, work_branch),
        memory_base_commit=memory_base,
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
    if contract.memory_mode != "external" or memory_state["state"] != "disabled":
        return contract
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
    return WorktreeCommandResult(
        0,
        {
            "state": "started",
            "summary": "Worktree task started; continue the wrapped workflow before closeout.",
            **next_guidance(
                "continue_work",
                tool="worktree_status",
                args=contract_next_args(contract),
            ),
            "code_worktree": code_state,
            "memory": memory_state,
            "providers": provider_state,
            "contract_path": contract.contract_path.as_posix(),
            "task_artifact": contract.task_artifact.as_posix(),
            "contract": contract_payload(contract),
        },
    )


def start_result(args: WorktreeArgs) -> WorktreeCommandResult:
    context = resolve_context(args)
    repo = context.code_repository_root
    contract = _build_start_contract(context, args)

    if contract.contract_path.exists():
        contract = load_contract(contract.contract_path)
        return WorktreeCommandResult(
            0, {"state": "attached-existing-contract", **status_payload(contract)}
        )

    code_state = ensure_worktree(
        repo,
        contract.code_worktree,
        contract.code_work_branch,
        contract.code_source_branch,
        args.dry_run,
    )
    memory_state = prepare_memory_for_start(contract, args)
    if memory_state["state"] == "blocked":
        return _blocked_memory_start_result(context, args, code_state, memory_state)
    contract = _contract_after_memory_start(contract, memory_state)
    provider_state = prepare_providers_for_start(context, contract, args)
    if provider_state["state"] == "blocked":
        return _blocked_provider_start_result(
            context, args, code_state, memory_state, provider_state
        )
    if not args.dry_run:
        write_contract(contract.contract_path, contract)
    return _started_result(contract, code_state, memory_state, provider_state)


def prepare_providers_for_start(
    context, contract: WorktreeContract, args: WorktreeArgs
) -> dict[str, object]:
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

    payload = _run_provider_setup(context, args, paths)
    if not payload.get("ok"):
        return {
            "state": "blocked",
            "reason": "provider setup failed",
            "payload": payload,
        }
    state_file = _write_provider_state_file(contract, payload, args.dry_run)
    return {
        "state": "planned" if args.dry_run else "prepared",
        "payload": payload,
        "provider_state_file": state_file.as_posix(),
    }


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
    grepai_enabled = bool(settings) and provider_setup.provider_enabled(
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


def _run_provider_setup(
    context,
    args: WorktreeArgs,
    paths: ProviderStartPaths,
) -> dict[str, object]:
    request = provider_setup.ProviderSetupRequest(
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
    return provider_setup.run_provider_setup(request)


def prepare_memory_for_start(
    contract: WorktreeContract, args: WorktreeArgs
) -> dict[str, object]:
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
    if mapping is None:
        return _missing_mapping_state(contract, ledger)
    assert contract.memory_worktree is not None
    memory_branch_state = ensure_worktree(
        contract.memory_repo_path,
        contract.memory_worktree,
        contract.memory_work_branch,
        contract.memory_source_branch,
        args.dry_run,
    )
    return {
        "state": "compatible",
        "worktree": memory_branch_state,
        "lastVerifiedCodeCommit": ledger.last_verified_code_commit,
        "lastMemoryContentCommit": ledger.last_memory_content_commit,
    }


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
        "reason": "external memory repo is missing; run C-00-initialize-memory-repo before starting an external-memory worktree",
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
            "choices": ["initialize-memory-repo", "reconciliation", "disabled-memory", "custom"],
        }


def _missing_mapping_state(contract: WorktreeContract, ledger) -> dict[str, object]:
    return {
        "state": "blocked",
        "reason": "no exact ledger mapping for selected code base commit",
        "codeBaseCommit": contract.code_base_commit,
        "lastVerifiedCodeCommit": ledger.last_verified_code_commit,
        "choices": ["reconciliation", "disabled-memory", "custom"],
    }
