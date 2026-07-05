from __future__ import annotations

from dataclasses import replace

from agents_remember.controlplane.enforcement import evaluate_gate
from agents_remember.controlplane.store import GateStore
from agents_remember.kernel.memory_ledger import (
    find_mapping,
    load_ledger,
    prepend_mapping,
    write_ledger,
)
from agents_remember.observer.paths import observer_logs_root
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.git import (
    branch_exists,
    commit_if_dirty,
    current_branch,
    head_commit,
    is_ancestor,
    require_clean,
    require_git,
    run_git,
)
from agents_remember.worktrees.modules.guidance import (
    contract_next_args,
    next_guidance,
    status_payload,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)


def integration_branch(contract: WorktreeContract) -> str:
    return f"{contract.memory_work_branch}-integration"


def blocked_integration_payload(
    contract: WorktreeContract, state: str, reason: str, persist: bool = True, **extra: object
) -> dict[str, object]:
    blocked = replace(contract, integration_status="blocked")
    if persist:
        write_contract(blocked.contract_path, blocked)
    return {
        "state": state,
        **status_payload(blocked),
        "reason": reason,
        "summary": reason,
        "developer_decision_required": True,
        **extra,
    }


def validate_integrate_contract(contract: WorktreeContract) -> None:
    if contract.closeout_status != "completed":
        raise RuntimeError("integration requires closeout.status completed")
    if not contract.approved_for_commit:
        raise RuntimeError("integration requires approved closeout")
    if not contract.code_commit:
        raise RuntimeError("integration requires closeout code_commit")
    if not contract.code_worktree.exists():
        raise RuntimeError(f"code worktree does not exist: {contract.code_worktree}")
    if current_branch(contract.code_repo_path) != contract.code_source_branch:
        raise RuntimeError(f"code source repo must have {contract.code_source_branch} checked out")
    if current_branch(contract.code_worktree) != contract.code_work_branch:
        raise RuntimeError(f"code worktree must have {contract.code_work_branch} checked out")
    require_clean(contract.code_repo_path, "code source repo")
    require_clean(contract.code_worktree, "code worktree")
    if head_commit(contract.code_worktree) != contract.code_commit:
        raise RuntimeError("code worktree HEAD does not match closeout code_commit")
    if contract.memory_mode == "external":
        validate_integrate_memory_contract(contract)


def validate_integrate_memory_contract(contract: WorktreeContract) -> None:
    if (
        contract.memory_repo_path is None
        or contract.memory_worktree is None
        or contract.ledger_path is None
    ):
        raise RuntimeError(
            "external-memory integration requires memory repo, worktree, and ledger path"
        )
    if not contract.memory_content_commit or not contract.ledger_commit:
        raise RuntimeError(
            "external-memory integration requires closeout memory_content_commit and ledger_commit"
        )
    if current_branch(contract.memory_repo_path) != contract.memory_source_branch:
        raise RuntimeError(
            f"memory source repo must have {contract.memory_source_branch} checked out"
        )
    if current_branch(contract.memory_worktree) != contract.memory_work_branch:
        raise RuntimeError(f"memory worktree must have {contract.memory_work_branch} checked out")
    require_clean(contract.memory_repo_path, "memory source repo")
    require_clean(contract.memory_worktree, "memory worktree")
    if head_commit(contract.memory_worktree) != contract.ledger_commit:
        raise RuntimeError("memory worktree HEAD does not match closeout ledger_commit")


def replay_code_if_needed(
    contract: WorktreeContract, current_code_source: str
) -> tuple[str, dict[str, object] | None]:
    if is_ancestor(contract.code_repo_path, current_code_source, contract.code_commit):
        return contract.code_commit, None
    result = run_git(contract.code_worktree, ["rebase", contract.code_source_branch])
    if result.returncode != 0:
        return "", blocked_integration_payload(
            contract,
            "blocked-code-conflict",
            "code replay conflicted; resolve with the developer before moving main",
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
            conflict_scope="code",
        )
    return head_commit(contract.code_worktree), None


def replay_memory_content(
    contract: WorktreeContract,
    integrated_code_commit: str,
    ledger_message: str,
) -> tuple[str, str, dict[str, object] | None]:
    assert contract.memory_repo_path is not None
    assert contract.memory_worktree is not None
    assert contract.ledger_path is not None
    scratch_branch = integration_branch(contract)
    if branch_exists(contract.memory_repo_path, scratch_branch):
        return (
            "",
            "",
            blocked_integration_payload(
                contract,
                "blocked-existing-integration-branch",
                f"memory integration branch already exists: {scratch_branch}",
                conflict_scope="memory",
                branch=scratch_branch,
            ),
        )
    result = run_git(
        contract.memory_worktree, ["checkout", "-b", scratch_branch, contract.memory_content_commit]
    )
    if result.returncode != 0:
        return (
            "",
            "",
            blocked_integration_payload(
                contract,
                "blocked-memory-replay",
                "could not create memory integration branch",
                stdout=result.stdout.strip(),
                stderr=result.stderr.strip(),
                conflict_scope="memory",
            ),
        )
    result = run_git(
        contract.memory_worktree,
        ["rebase", "--onto", contract.memory_source_branch, contract.memory_base_commit],
    )
    if result.returncode != 0:
        return (
            "",
            "",
            blocked_integration_payload(
                contract,
                "blocked-memory-conflict",
                "memory replay conflicted; resolve with the developer before moving memory main",
                stdout=result.stdout.strip(),
                stderr=result.stderr.strip(),
                conflict_scope="memory",
                branch=scratch_branch,
            ),
        )
    integrated_memory_content_commit = head_commit(contract.memory_worktree)
    ledger = load_ledger(contract.ledger_path)
    write_ledger(
        contract.ledger_path,
        prepend_mapping(ledger, integrated_code_commit, integrated_memory_content_commit),
    )
    require_git(contract.memory_worktree, ["add", "memory.md"])
    integrated_ledger_commit = commit_if_dirty(contract.memory_worktree, ledger_message)
    return integrated_memory_content_commit, integrated_ledger_commit, None


def _integration_replay_requirements(
    contract: WorktreeContract,
) -> tuple[str, str, bool, bool]:
    current_code_source = head_commit(contract.code_repo_path, contract.code_source_branch)
    current_memory_source = ""
    code_replay_required = not is_ancestor(
        contract.code_repo_path, current_code_source, contract.code_commit
    )
    memory_replay_required = False
    if contract.memory_mode == "external":
        assert contract.memory_repo_path is not None
        current_memory_source = head_commit(
            contract.memory_repo_path, contract.memory_source_branch
        )
        memory_replay_required = not is_ancestor(
            contract.memory_repo_path, current_memory_source, contract.ledger_commit
        )
    return current_code_source, current_memory_source, code_replay_required, memory_replay_required


def _blocked_non_ff_result(
    contract: WorktreeContract,
    args: WorktreeArgs,
    code_replay_required: bool,
    memory_replay_required: bool,
) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        2,
        blocked_integration_payload(
            contract,
            "blocked-non-ff",
            "source branch moved; rerun with --strategy replay after reviewing parallel changes",
            persist=not args.dry_run,
            code_replay_required=code_replay_required,
            memory_replay_required=memory_replay_required,
        ),
    )


def _dry_run_result(
    contract: WorktreeContract,
    args: WorktreeArgs,
    code_replay_required: bool,
    memory_replay_required: bool,
) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        0,
        {
            "state": "would-integrate",
            **status_payload(contract),
            "summary": "Dry run completed; integration preflight can proceed with the selected strategy.",
            **next_guidance(
                "request_integration_decision",
                tool="worktree_integrate",
                args=contract_next_args(
                    contract,
                    strategy=args.strategy,
                    ledger_commit_message=args.ledger_commit_message,
                    dry_run=False,
                ),
            ),
            "strategy": args.strategy,
            "code_replay_required": code_replay_required,
            "memory_replay_required": memory_replay_required,
            "cleanup_question": "After successful integration, ask whether to remove the code and memory worktrees plus merged local task branches.",
        },
    )


def _integrated_code_commit(
    contract: WorktreeContract, args: WorktreeArgs, current_code_source: str
) -> tuple[str, dict[str, object] | None]:
    integrated_code_commit = contract.code_commit
    if args.strategy == "replay":
        integrated_code_commit, blocked = replay_code_if_needed(contract, current_code_source)
        if blocked is not None:
            return "", blocked
    if not is_ancestor(contract.code_repo_path, current_code_source, integrated_code_commit):
        raise RuntimeError(
            "integrated code commit is not a fast-forward from the current code source branch"
        )
    return integrated_code_commit, None


def _memory_needs_new_ledger(
    contract: WorktreeContract,
    args: WorktreeArgs,
    current_memory_source: str,
    integrated_code_commit: str,
) -> bool:
    assert contract.memory_repo_path is not None
    return args.strategy == "replay" and (
        integrated_code_commit != contract.code_commit
        or not is_ancestor(contract.memory_repo_path, current_memory_source, contract.ledger_commit)
    )


def _integrated_memory_commits(
    contract: WorktreeContract,
    args: WorktreeArgs,
    current_memory_source: str,
    integrated_code_commit: str,
) -> tuple[str, str, dict[str, object] | None]:
    integrated_memory_content_commit = contract.memory_content_commit
    integrated_ledger_commit = contract.ledger_commit
    if contract.memory_mode == "external":
        assert contract.memory_repo_path is not None
        if _memory_needs_new_ledger(contract, args, current_memory_source, integrated_code_commit):
            integrated_memory_content_commit, integrated_ledger_commit, blocked = (
                replay_memory_content(
                    contract,
                    integrated_code_commit,
                    args.ledger_commit_message
                    or f"[{contract.task_id}] Integration ledger sync: {integrated_code_commit} -> {contract.memory_content_commit}",
                )
            )
            if blocked is not None:
                return "", "", blocked
        if not is_ancestor(
            contract.memory_repo_path, current_memory_source, integrated_ledger_commit
        ):
            raise RuntimeError(
                "integrated memory ledger commit is not a fast-forward from the current memory source branch"
            )
    return integrated_memory_content_commit, integrated_ledger_commit, None


def _merge_integrated_commits(
    contract: WorktreeContract,
    integrated_code_commit: str,
    integrated_ledger_commit: str,
    integrated_memory_content_commit: str,
) -> None:
    external = contract.memory_mode == "external"
    # Pre-validate that BOTH fast-forwards are possible before mutating either
    # branch, so a memory-side problem cannot leave the code branch advanced
    # while memory stays behind (a half-integrated state).
    code_head_before = head_commit(contract.code_repo_path)
    if not is_ancestor(contract.code_repo_path, code_head_before, integrated_code_commit):
        raise RuntimeError(
            "integrated code commit is not a fast-forward from the current code branch"
        )
    memory_head_before = ""
    if external:
        assert contract.memory_repo_path is not None
        memory_head_before = head_commit(contract.memory_repo_path)
        if not is_ancestor(
            contract.memory_repo_path, memory_head_before, integrated_ledger_commit
        ):
            raise RuntimeError(
                "integrated memory ledger commit is not a fast-forward from the current memory branch"
            )

    require_git(contract.code_repo_path, ["merge", "--ff-only", integrated_code_commit])
    if not external:
        return
    assert contract.memory_repo_path is not None
    try:
        require_git(contract.memory_repo_path, ["merge", "--ff-only", integrated_ledger_commit])
        ledger = load_ledger(contract.memory_repo_path / "memory.md")
        mapping = find_mapping(ledger, integrated_code_commit)
        if mapping is None or mapping.memory_commit != integrated_memory_content_commit:
            raise RuntimeError(
                "integrated memory ledger does not map landed code commit to landed memory content commit"
            )
    except Exception:
        # The memory side failed after the code branch advanced. Roll both
        # branches back to their pre-merge heads (best-effort) so integration is
        # all-or-nothing, then re-raise the original failure.
        run_git(contract.code_repo_path, ["reset", "--hard", code_head_before])
        run_git(contract.memory_repo_path, ["reset", "--hard", memory_head_before])
        raise


def _integrated_result(
    contract: WorktreeContract,
    args: WorktreeArgs,
    integrated_code_commit: str,
    integrated_memory_content_commit: str,
    integrated_ledger_commit: str,
) -> WorktreeCommandResult:
    updated = replace(
        contract,
        integration_status="completed",
        integration_strategy=args.strategy,
        integrated_code_commit=integrated_code_commit,
        integrated_memory_content_commit=integrated_memory_content_commit,
        integrated_ledger_commit=integrated_ledger_commit,
        cleanup="pending",
    )
    write_contract(contract.contract_path, updated)
    return WorktreeCommandResult(
        0,
        {
            "state": "integrated",
            **status_payload(updated),
            "summary": "Integration completed; ask the developer whether to clean up worktrees and merged local branches.",
            "strategy": args.strategy,
            "integrated_code_commit": integrated_code_commit,
            "integrated_memory_content_commit": integrated_memory_content_commit,
            "integrated_ledger_commit": integrated_ledger_commit,
            "cleanup_question": "Integration completed. Remove the code and memory worktrees plus merged local task branches now?",
        },
    )


def integrate_result(args: WorktreeArgs) -> WorktreeCommandResult:
    if not args.approved and not args.dry_run:
        raise RuntimeError("integration requires --approved after human review")
    assert args.contract_path is not None
    contract = load_contract(args.contract_path)
    if contract.integration_status == "completed":
        return WorktreeCommandResult(0, {"state": "already-integrated", **status_payload(contract)})
    validate_integrate_contract(contract)
    if not args.dry_run:
        # The master-exit seam consumer (mirror of the closeout gate): when a
        # master-handover-approval gate exists on this contract's lifecycle, only a
        # policy-valid approval lets the integration proceed. Gateless stays additive.
        gate_store = GateStore(observer_logs_root(contract.coordination_root))
        guard = evaluate_gate(
            gate_store.current(contract.lifecycle_id),
            kind="master-handover-approval",
            policy=args.gate_policy,
        )
        if not guard.permitted:
            return WorktreeCommandResult(
                2,
                {
                    "state": "handover-gate-blocked",
                    "gateId": guard.gate_id,
                    "reason": guard.reason,
                    **status_payload(contract),
                },
            )

    current_code_source, current_memory_source, code_replay_required, memory_replay_required = (
        _integration_replay_requirements(contract)
    )
    if args.strategy == "ff-only" and (code_replay_required or memory_replay_required):
        return _blocked_non_ff_result(contract, args, code_replay_required, memory_replay_required)
    if args.dry_run:
        return _dry_run_result(contract, args, code_replay_required, memory_replay_required)

    integrated_code_commit, blocked = _integrated_code_commit(contract, args, current_code_source)
    if blocked is not None:
        return WorktreeCommandResult(2, blocked)
    integrated_memory_content_commit, integrated_ledger_commit, blocked = (
        _integrated_memory_commits(contract, args, current_memory_source, integrated_code_commit)
    )
    if blocked is not None:
        return WorktreeCommandResult(2, blocked)
    _merge_integrated_commits(
        contract,
        integrated_code_commit,
        integrated_ledger_commit,
        integrated_memory_content_commit,
    )
    return _integrated_result(
        contract,
        args,
        integrated_code_commit,
        integrated_memory_content_commit,
        integrated_ledger_commit,
    )
