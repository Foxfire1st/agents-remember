#!/usr/bin/env python3
"""Manage Agents Remember worktree-backed task lifecycle.

Requires Python 3.10+ and git. Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = Path(__file__).resolve().parents[2]
SHARED_ROOT = CORE_ROOT / "_shared"
RESOLVER_PATH = CORE_ROOT / "C-08-ar-management-resolver" / "scripts" / "ar_management_resolver.py"
sys.path.insert(0, str(SHARED_ROOT))

from agents_remember.memory_ledger import (  # noqa: E402
    LedgerError,
    create_initial_ledger,
    find_mapping,
    load_ledger,
    prepend_mapping,
    write_ledger,
)
from agents_remember.worktree_contract import (  # noqa: E402
    ContractError,
    WorktreeContract,
    default_contract,
    load_contract,
    write_contract,
)


RESOLVER_SPEC = importlib.util.spec_from_file_location("ar_management_resolver", RESOLVER_PATH)
if RESOLVER_SPEC is None or RESOLVER_SPEC.loader is None:
    raise ImportError(f"Unable to load C-08 resolver module from {RESOLVER_PATH}")
resolver = importlib.util.module_from_spec(RESOLVER_SPEC)
sys.modules[RESOLVER_SPEC.name] = resolver
RESOLVER_SPEC.loader.exec_module(resolver)


def run_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", f"safe.directory={repo.as_posix()}", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def require_git(repo: Path, args: list[str]) -> str:
    result = run_git(repo, args)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def current_branch(repo: Path) -> str:
    return require_git(repo, ["branch", "--show-current"])


def head_commit(repo: Path, ref: str = "HEAD") -> str:
    return require_git(repo, ["rev-parse", ref])


def branch_exists(repo: Path, branch: str) -> bool:
    return run_git(repo, ["rev-parse", "--verify", "--quiet", branch]).returncode == 0


def has_changes(repo: Path) -> bool:
    return bool(require_git(repo, ["status", "--porcelain"]))


def require_clean(repo: Path, label: str) -> None:
    changes = require_git(repo, ["status", "--porcelain"])
    if changes:
        raise RuntimeError(f"{label} is not clean:\n{changes}")


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return run_git(repo, ["merge-base", "--is-ancestor", ancestor, descendant]).returncode == 0


def ensure_git_identity(repo: Path) -> None:
    if not run_git(repo, ["config", "--get", "user.email"]).stdout.strip():
        require_git(repo, ["config", "user.email", "agents-remember@example.invalid"])
    if not run_git(repo, ["config", "--get", "user.name"]).stdout.strip():
        require_git(repo, ["config", "user.name", "Agents Remember"])


def ensure_worktree(repo: Path, worktree: Path, branch: str, source_branch: str, dry_run: bool) -> str:
    if worktree.exists():
        return "existing"
    if dry_run:
        return "would-create"
    worktree.parent.mkdir(parents=True, exist_ok=True)
    if branch_exists(repo, branch):
        require_git(repo, ["worktree", "add", str(worktree), branch])
    else:
        require_git(repo, ["worktree", "add", "-b", branch, str(worktree), source_branch])
    return "created"


def resolve_context(args: argparse.Namespace):
    return resolver.resolve_management_context(
        repo_name=args.repo_name,
        workspace_root=args.workspace_root,
        requested_topology=args.topology,
        shared_root=args.shared_root,
        target_repo=args.repo,
        task_name=getattr(args, "task_name", None),
        worktree_name=getattr(args, "worktree_name", None),
        contract_path=getattr(args, "contract_path", None),
    )


def contract_payload(contract: WorktreeContract) -> dict[str, object]:
    data = asdict(contract)
    for key, value in list(data.items()):
        if isinstance(value, Path):
            data[key] = value.as_posix()
        elif value is None:
            data[key] = ""
    return data


def status_payload(contract: WorktreeContract) -> dict[str, object]:
    return {
        "task_id": contract.task_id,
        "task_name": contract.task_name,
        "repo_name": contract.repo_name,
        "workflow_kind": contract.workflow_kind,
        "memory_mode": contract.memory_mode,
        "contract_path": contract.contract_path.as_posix(),
        "worktree_group": contract.worktree_group.as_posix(),
        "code_worktree": contract.code_worktree.as_posix(),
        "code_worktree_exists": contract.code_worktree.exists(),
        "memory_worktree": contract.memory_worktree.as_posix() if contract.memory_worktree else "",
        "memory_worktree_exists": contract.memory_worktree.exists() if contract.memory_worktree else False,
        "ledger_path": contract.ledger_path.as_posix() if contract.ledger_path else "",
        "human_review_status": contract.human_review_status,
        "approved_for_commit": contract.approved_for_commit,
        "closeout_status": contract.closeout_status,
        "integration_status": contract.integration_status,
        "cleanup": contract.cleanup,
    }


def load_contract_from_args(args: argparse.Namespace) -> WorktreeContract:
    context = resolve_context(args)
    contract_path = args.contract_path
    if contract_path is None:
        if not args.task_name:
            raise RuntimeError("--task-name or --contract-path is required")
        contract_path = context.task_root / "contract.md" if context.task_root.name.endswith("-ar") else context.task_root / context.repo_name / f"{args.task_name}-ar" / "contract.md"
    return load_contract(contract_path)


def command_status(args: argparse.Namespace) -> int:
    contract = load_contract_from_args(args)
    print(json.dumps(status_payload(contract), indent=2))
    return 0


def command_attach(args: argparse.Namespace) -> int:
    contract = load_contract_from_args(args)
    print(json.dumps({"attached": True, **status_payload(contract)}, indent=2))
    return 0


def command_start(args: argparse.Namespace) -> int:
    context = resolve_context(args)
    repo = context.target_repo
    source_branch = args.source_branch or current_branch(repo)
    work_branch = args.work_branch or f"ar/{args.worktree_name}"
    base_commit = head_commit(repo, source_branch)
    memory_mode = args.memory_mode or context.memory_mode
    memory_repo = context.coordination_root / "memory-repos" / f"ar-{context.repo_name}" if memory_mode == "shared" else None
    memory_base = head_commit(memory_repo) if memory_repo is not None and memory_repo.exists() and (memory_repo / ".git").exists() else ""
    contract = default_contract(
        task_name=args.task_name,
        repo_name=context.repo_name,
        workflow_kind=args.workflow_kind,
        memory_mode=memory_mode,
        coordination_root=context.coordination_root,
        code_repo_path=repo,
        code_source_branch=source_branch,
        code_work_branch=work_branch,
        code_base_commit=base_commit,
        worktree_name=args.worktree_name,
        memory_repo_path=memory_repo,
        memory_source_branch=source_branch if memory_mode == "shared" else "",
        memory_work_branch=work_branch if memory_mode == "shared" else "",
        memory_base_commit=memory_base,
    )

    if contract.contract_path.exists():
        contract = load_contract(contract.contract_path)
        print(json.dumps({"state": "attached-existing-contract", **status_payload(contract)}, indent=2))
        return 0

    code_state = ensure_worktree(repo, contract.code_worktree, contract.code_work_branch, contract.code_source_branch, args.dry_run)
    memory_state = prepare_memory_for_start(contract, args)
    if memory_state["state"] == "blocked":
        print(json.dumps({"state": "blocked", "code_worktree": code_state, "memory": memory_state}, indent=2))
        return 2
    if contract.memory_mode == "shared" and memory_state["state"] == "disabled":
        contract = replace(
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
    if contract.memory_mode == "shared" and memory_state["state"] == "clean-start" and memory_state.get("ledgerCommit"):
        contract = replace(contract, memory_base_commit=str(memory_state["ledgerCommit"]))

    if not args.dry_run:
        write_contract(contract.contract_path, contract)
    print(json.dumps({"state": "started", "code_worktree": code_state, "memory": memory_state, "contract": contract_payload(contract)}, indent=2))
    return 0


def prepare_memory_for_start(contract: WorktreeContract, args: argparse.Namespace) -> dict[str, object]:
    if contract.memory_mode == "internal":
        return {"state": "internal", "reason": "memory lives in the code worktree"}
    if contract.memory_mode == "disabled":
        return {"state": "disabled"}
    assert contract.memory_repo_path is not None
    if not contract.memory_repo_path.exists():
        if args.memory_choice == "clean-start":
            result = bootstrap_memory_repo(contract, args.dry_run)
            if args.dry_run or result["state"] != "clean-start":
                return result
            assert contract.memory_worktree is not None
            result["worktree"] = ensure_worktree(
                contract.memory_repo_path,
                contract.memory_worktree,
                contract.memory_work_branch,
                contract.memory_source_branch,
                args.dry_run,
            )
            return result
        if args.memory_choice == "disabled-memory":
            return {"state": "disabled", "reason": "human selected disabled memory"}
        return {
            "state": "blocked",
            "reason": "shared memory repo is missing",
            "choices": ["reconciliation", "clean-start", "disabled-memory", "custom"],
        }
    ledger_path = contract.memory_repo_path / "memory.md"
    try:
        ledger = load_ledger(ledger_path)
    except LedgerError as error:
        if args.memory_choice == "disabled-memory":
            return {"state": "disabled", "reason": "human selected disabled memory"}
        return {
            "state": "blocked",
            "reason": str(error),
            "choices": ["reconciliation", "clean-start", "disabled-memory", "custom"],
        }
    if ledger.tracked_code_branch != contract.code_source_branch or ledger.memory_branch != contract.memory_source_branch:
        return {
            "state": "blocked",
            "reason": "memory ledger branch metadata does not match the selected code branch",
            "trackedCodeBranch": ledger.tracked_code_branch,
            "memoryBranch": ledger.memory_branch,
            "expectedBranch": contract.code_source_branch,
            "choices": ["reconciliation", "clean-start", "disabled-memory", "custom"],
        }
    mapping = find_mapping(ledger, contract.code_base_commit)
    if mapping is None:
        return {
            "state": "blocked",
            "reason": "no exact ledger mapping for selected code base commit",
            "codeBaseCommit": contract.code_base_commit,
            "lastVerifiedCodeCommit": ledger.last_verified_code_commit,
            "choices": ["reconciliation", "clean-start", "disabled-memory", "custom"],
        }
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


def bootstrap_memory_repo(contract: WorktreeContract, dry_run: bool) -> dict[str, object]:
    assert contract.memory_repo_path is not None
    assert contract.ledger_path is not None
    ledger_path = contract.memory_repo_path / "memory.md"
    if ledger_path.exists():
        ledger = load_ledger(ledger_path)
        return {
            "state": "already-ledgered",
            "lastVerifiedCodeCommit": ledger.last_verified_code_commit,
            "lastMemoryContentCommit": ledger.last_memory_content_commit,
        }
    if dry_run:
        return {"state": "would-bootstrap", "path": contract.memory_repo_path.as_posix()}
    contract.memory_repo_path.mkdir(parents=True, exist_ok=True)
    if not (contract.memory_repo_path / ".git").exists():
        require_git(contract.memory_repo_path, ["init"])
    ensure_git_identity(contract.memory_repo_path)
    if contract.memory_source_branch and current_branch(contract.memory_repo_path) != contract.memory_source_branch:
        if branch_exists(contract.memory_repo_path, contract.memory_source_branch):
            require_git(contract.memory_repo_path, ["checkout", contract.memory_source_branch])
        else:
            require_git(contract.memory_repo_path, ["checkout", "-b", contract.memory_source_branch])
    for directory in ("onboarding", "docs", "system"):
        (contract.memory_repo_path / directory).mkdir(parents=True, exist_ok=True)
    docs_keep = contract.memory_repo_path / "docs" / ".gitkeep"
    if not any((contract.memory_repo_path / "docs").iterdir()):
        docs_keep.write_text("", encoding="utf-8")
    overview = contract.memory_repo_path / "onboarding" / "overview.md"
    if not overview.exists():
        overview.write_text(f"# {contract.repo_name} Memory Overview\n\nShared memory repo bootstrap placeholder.\n", encoding="utf-8")
    settings = contract.memory_repo_path / "system" / "settings.json"
    if not settings.exists():
        settings.write_text(
            json.dumps(
                {
                    "version": 2,
                    "onboarding": {"storage": {"mode": "memory-repo"}, "pathRules": []},
                    "crossRepo": {"allow": []},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    for name in ("settings.md", "sources.md", "tools.md"):
        path = contract.memory_repo_path / "system" / name
        if not path.exists():
            path.write_text(f"# {name.removesuffix('.md').title()}\n\nNo entries configured yet.\n", encoding="utf-8")
    require_git(contract.memory_repo_path, ["add", "onboarding", "docs", "system"])
    memory_content_commit = commit_if_dirty(contract.memory_repo_path, f"[{contract.task_id}] Bootstrap shared memory content")
    ledger = create_initial_ledger(
        contract.repo_name,
        contract.code_source_branch,
        contract.memory_source_branch or contract.code_source_branch,
        contract.code_base_commit,
        memory_content_commit,
    )
    write_ledger(ledger_path, ledger)
    require_git(contract.memory_repo_path, ["add", "memory.md"])
    ledger_commit = commit_if_dirty(contract.memory_repo_path, f"[{contract.task_id}] Bootstrap memory ledger")
    return {
        "state": "clean-start",
        "memoryContentCommit": memory_content_commit,
        "ledgerCommit": ledger_commit,
    }


def commit_if_dirty(repo: Path, message: str) -> str:
    if not has_changes(repo):
        return head_commit(repo)
    require_git(repo, ["add", "-A"])
    require_git(repo, ["commit", "-m", message])
    return head_commit(repo)


def command_bootstrap_memory(args: argparse.Namespace) -> int:
    context = resolve_context(args)
    source_branch = args.source_branch or current_branch(context.target_repo)
    contract = default_contract(
        task_name=args.task_name or f"bootstrap-{context.repo_name}-memory",
        repo_name=context.repo_name,
        workflow_kind="bootstrap-memory",
        memory_mode="shared",
        coordination_root=context.coordination_root,
        code_repo_path=context.target_repo,
        code_source_branch=source_branch,
        code_work_branch=args.work_branch or source_branch,
        code_base_commit=head_commit(context.target_repo, source_branch),
        worktree_name=args.worktree_name or f"bootstrap-{context.repo_name}-memory",
        memory_repo_path=context.coordination_root / "memory-repos" / f"ar-{context.repo_name}",
        memory_source_branch=source_branch,
        memory_work_branch=args.work_branch or source_branch,
    )
    print(json.dumps(bootstrap_memory_repo(contract, args.dry_run), indent=2))
    return 0


def command_closeout(args: argparse.Namespace) -> int:
    if not args.approved:
        raise RuntimeError("closeout requires --approved after human review")
    contract = load_contract(args.contract_path)
    current_code_source = head_commit(contract.code_repo_path, contract.code_source_branch)
    if current_code_source != contract.code_base_commit:
        raise RuntimeError(
            "code source branch moved since task start: "
            f"{contract.code_source_branch} is {current_code_source}, expected {contract.code_base_commit}"
        )
    if contract.memory_mode == "shared" and contract.memory_repo_path is not None and contract.memory_base_commit:
        current_memory_source = head_commit(contract.memory_repo_path, contract.memory_source_branch)
        if current_memory_source != contract.memory_base_commit:
            raise RuntimeError(
                "memory source branch moved since task start: "
                f"{contract.memory_source_branch} is {current_memory_source}, expected {contract.memory_base_commit}"
            )
    if args.dry_run:
        print(json.dumps({"state": "would-closeout", **status_payload(contract)}, indent=2))
        return 0
    code_commit = commit_if_dirty(contract.code_worktree, args.code_commit_message)
    memory_commit = ""
    ledger_commit = ""
    if contract.memory_mode == "shared":
        if contract.memory_worktree is None or contract.ledger_path is None:
            raise RuntimeError("shared closeout requires memory worktree and ledger path")
        memory_commit = commit_if_dirty(contract.memory_worktree, args.memory_commit_message)
        ledger = load_ledger(contract.ledger_path)
        write_ledger(contract.ledger_path, prepend_mapping(ledger, code_commit, memory_commit))
        require_git(contract.memory_worktree, ["add", "memory.md"])
        ledger_commit = commit_if_dirty(contract.memory_worktree, args.ledger_commit_message or f"[{contract.task_id}] Ledger sync: {code_commit} -> {memory_commit}")
    updated = replace(
        contract,
        human_review_status="approved",
        approved_for_commit=True,
        closeout_status="completed",
        code_commit=code_commit,
        memory_content_commit=memory_commit,
        ledger_commit=ledger_commit,
    )
    write_contract(contract.contract_path, updated)
    print(json.dumps({"state": "closed", **status_payload(updated), "code_commit": code_commit, "memory_content_commit": memory_commit, "ledger_commit": ledger_commit}, indent=2))
    return 0


def integration_branch(contract: WorktreeContract) -> str:
    return f"{contract.memory_work_branch}-integration"


def blocked_integration_payload(contract: WorktreeContract, state: str, reason: str, **extra: object) -> dict[str, object]:
    return {
        "state": state,
        "reason": reason,
        "developer_decision_required": True,
        **status_payload(contract),
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
    if contract.memory_mode == "shared":
        if contract.memory_repo_path is None or contract.memory_worktree is None or contract.ledger_path is None:
            raise RuntimeError("shared integration requires memory repo, worktree, and ledger path")
        if not contract.memory_content_commit or not contract.ledger_commit:
            raise RuntimeError("shared integration requires closeout memory_content_commit and ledger_commit")
        if current_branch(contract.memory_repo_path) != contract.memory_source_branch:
            raise RuntimeError(f"memory source repo must have {contract.memory_source_branch} checked out")
        if current_branch(contract.memory_worktree) != contract.memory_work_branch:
            raise RuntimeError(f"memory worktree must have {contract.memory_work_branch} checked out")
        require_clean(contract.memory_repo_path, "memory source repo")
        require_clean(contract.memory_worktree, "memory worktree")
        if head_commit(contract.memory_worktree) != contract.ledger_commit:
            raise RuntimeError("memory worktree HEAD does not match closeout ledger_commit")


def replay_code_if_needed(contract: WorktreeContract, current_code_source: str) -> tuple[str, dict[str, object] | None]:
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
    current_memory_source: str,
    ledger_message: str,
) -> tuple[str, str, dict[str, object] | None]:
    assert contract.memory_repo_path is not None
    assert contract.memory_worktree is not None
    assert contract.ledger_path is not None
    scratch_branch = integration_branch(contract)
    if branch_exists(contract.memory_repo_path, scratch_branch):
        return "", "", blocked_integration_payload(
            contract,
            "blocked-existing-integration-branch",
            f"memory integration branch already exists: {scratch_branch}",
            conflict_scope="memory",
            branch=scratch_branch,
        )
    result = run_git(contract.memory_worktree, ["checkout", "-b", scratch_branch, contract.memory_content_commit])
    if result.returncode != 0:
        return "", "", blocked_integration_payload(
            contract,
            "blocked-memory-replay",
            "could not create memory integration branch",
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
            conflict_scope="memory",
        )
    result = run_git(contract.memory_worktree, ["rebase", "--onto", contract.memory_source_branch, contract.memory_base_commit])
    if result.returncode != 0:
        return "", "", blocked_integration_payload(
            contract,
            "blocked-memory-conflict",
            "memory replay conflicted; resolve with the developer before moving memory main",
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
            conflict_scope="memory",
            branch=scratch_branch,
        )
    integrated_memory_content_commit = head_commit(contract.memory_worktree)
    ledger = load_ledger(contract.ledger_path)
    write_ledger(contract.ledger_path, prepend_mapping(ledger, integrated_code_commit, integrated_memory_content_commit))
    require_git(contract.memory_worktree, ["add", "memory.md"])
    integrated_ledger_commit = commit_if_dirty(contract.memory_worktree, ledger_message)
    return integrated_memory_content_commit, integrated_ledger_commit, None


def command_integrate(args: argparse.Namespace) -> int:
    if not args.approved:
        raise RuntimeError("integration requires --approved after human review")
    contract = load_contract(args.contract_path)
    if contract.integration_status == "completed":
        print(json.dumps({"state": "already-integrated", **status_payload(contract)}, indent=2))
        return 0
    validate_integrate_contract(contract)

    current_code_source = head_commit(contract.code_repo_path, contract.code_source_branch)
    current_memory_source = ""
    code_replay_required = not is_ancestor(contract.code_repo_path, current_code_source, contract.code_commit)
    memory_replay_required = False
    if contract.memory_mode == "shared":
        assert contract.memory_repo_path is not None
        current_memory_source = head_commit(contract.memory_repo_path, contract.memory_source_branch)
        memory_replay_required = not is_ancestor(contract.memory_repo_path, current_memory_source, contract.ledger_commit)
    if args.strategy == "ff-only" and (code_replay_required or memory_replay_required):
        print(json.dumps(blocked_integration_payload(
            contract,
            "blocked-non-ff",
            "source branch moved; rerun with --strategy replay after reviewing parallel changes",
            code_replay_required=code_replay_required,
            memory_replay_required=memory_replay_required,
        ), indent=2))
        return 2

    if args.dry_run:
        print(json.dumps({
            "state": "would-integrate",
            **status_payload(contract),
            "strategy": args.strategy,
            "code_replay_required": code_replay_required,
            "memory_replay_required": memory_replay_required,
            "cleanup_question": "After successful integration, ask whether to remove the code and memory worktrees plus merged local task branches.",
        }, indent=2))
        return 0

    integrated_code_commit = contract.code_commit
    if args.strategy == "replay":
        integrated_code_commit, blocked = replay_code_if_needed(contract, current_code_source)
        if blocked is not None:
            print(json.dumps(blocked, indent=2))
            return 2
    if not is_ancestor(contract.code_repo_path, current_code_source, integrated_code_commit):
        raise RuntimeError("integrated code commit is not a fast-forward from the current code source branch")

    integrated_memory_content_commit = contract.memory_content_commit
    integrated_ledger_commit = contract.ledger_commit
    if contract.memory_mode == "shared":
        assert contract.memory_repo_path is not None
        needs_new_ledger = args.strategy == "replay" and (
            integrated_code_commit != contract.code_commit
            or not is_ancestor(contract.memory_repo_path, current_memory_source, contract.ledger_commit)
        )
        if needs_new_ledger:
            integrated_memory_content_commit, integrated_ledger_commit, blocked = replay_memory_content(
                contract,
                integrated_code_commit,
                current_memory_source,
                args.ledger_commit_message or f"[{contract.task_id}] Integration ledger sync: {integrated_code_commit} -> {contract.memory_content_commit}",
            )
            if blocked is not None:
                print(json.dumps(blocked, indent=2))
                return 2
        if not is_ancestor(contract.memory_repo_path, current_memory_source, integrated_ledger_commit):
            raise RuntimeError("integrated memory ledger commit is not a fast-forward from the current memory source branch")

    require_git(contract.code_repo_path, ["merge", "--ff-only", integrated_code_commit])
    if contract.memory_mode == "shared":
        assert contract.memory_repo_path is not None
        require_git(contract.memory_repo_path, ["merge", "--ff-only", integrated_ledger_commit])
        ledger = load_ledger(contract.memory_repo_path / "memory.md")
        mapping = find_mapping(ledger, integrated_code_commit)
        if mapping is None or mapping.memory_commit != integrated_memory_content_commit:
            raise RuntimeError("integrated memory ledger does not map landed code commit to landed memory content commit")

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
    print(json.dumps({
        "state": "integrated",
        **status_payload(updated),
        "strategy": args.strategy,
        "integrated_code_commit": integrated_code_commit,
        "integrated_memory_content_commit": integrated_memory_content_commit,
        "integrated_ledger_commit": integrated_ledger_commit,
        "cleanup_question": "Integration completed. Remove the code and memory worktrees plus merged local task branches now?",
    }, indent=2))
    return 0


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-name", help="Repository name to resolve.")
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd(), help="Workspace root used to find --repo-name.")
    parser.add_argument("--repo", type=Path, help="Compatibility input for callers that already have the repository root path.")
    parser.add_argument("--topology", choices=("internal", "shared"), help="Optional topology override.")
    parser.add_argument("--shared-root", type=Path, help="Optional shared ar-management root.")
    parser.add_argument("--contract-path", type=Path, help="Path to an existing contract.md.")
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
    start.add_argument("--memory-mode", choices=("internal", "shared", "disabled"))
    start.add_argument("--memory-choice", choices=("reconciliation", "clean-start", "disabled-memory", "custom"))
    start.add_argument("--custom-instruction")
    start.add_argument("--dry-run", action="store_true")
    start.set_defaults(func=command_start)

    attach = subparsers.add_parser("attach")
    add_common(attach)
    attach.set_defaults(func=command_attach)

    status = subparsers.add_parser("status")
    add_common(status)
    status.set_defaults(func=command_status)

    bootstrap = subparsers.add_parser("bootstrap-memory")
    add_common(bootstrap)
    bootstrap.add_argument("--worktree-name")
    bootstrap.add_argument("--source-branch")
    bootstrap.add_argument("--work-branch")
    bootstrap.add_argument("--dry-run", action="store_true")
    bootstrap.set_defaults(func=command_bootstrap_memory)

    closeout = subparsers.add_parser("closeout")
    closeout.add_argument("--contract-path", type=Path, required=True)
    closeout.add_argument("--approved", action="store_true")
    closeout.add_argument("--code-commit-message", required=True)
    closeout.add_argument("--memory-commit-message", default="")
    closeout.add_argument("--ledger-commit-message", default="")
    closeout.add_argument("--dry-run", action="store_true")
    closeout.set_defaults(func=command_closeout)

    integrate = subparsers.add_parser("integrate")
    integrate.add_argument("--contract-path", type=Path, required=True)
    integrate.add_argument("--approved", action="store_true")
    integrate.add_argument("--strategy", choices=("ff-only", "replay"), default="ff-only")
    integrate.add_argument("--ledger-commit-message", default="")
    integrate.add_argument("--dry-run", action="store_true")
    integrate.set_defaults(func=command_integrate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, ContractError, LedgerError, ValueError) as error:
        parser.error(str(error))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
