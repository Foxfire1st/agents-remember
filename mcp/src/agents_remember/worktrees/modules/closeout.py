from __future__ import annotations

import argparse
from dataclasses import replace

from agents_remember.kernel.memory_ledger import load_ledger, prepend_mapping, write_ledger
from agents_remember.worktrees.modules.context import contract_context, resolve_context
from agents_remember.worktrees.modules.git import (
    changed_worktree_paths,
    commit_date,
    commit_if_dirty,
    current_branch,
    head_commit,
    require_git,
    worktree_dirty,
)
from agents_remember.worktrees.modules.guidance import (
    contract_next_args,
    next_guidance,
    status_payload,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.modules.onboarding import (
    entity_fingerprint_refresh_plan,
    entity_fingerprint_refresh_plan_for_context,
    onboarding_refresh_plan,
    onboarding_refresh_plan_for_context,
    refresh_entity_fingerprints_for_context,
    refresh_onboarding_metadata,
    refresh_onboarding_metadata_for_context,
    validate_onboarding_refresh_plan,
    validate_onboarding_refresh_plan_for_context,
)
from agents_remember.worktrees.worktree_contract import load_contract, write_contract


def closeout_preview_payload(contract, args: argparse.Namespace) -> dict[str, object]:
    ledger_message = (
        args.ledger_commit_message
        or f"[{contract.task_id}] Ledger sync: <code_commit> -> <memory_commit>"
    )
    code_dirty = worktree_dirty(contract.code_worktree)
    memory_dirty = contract.memory_mode == "external" and worktree_dirty(contract.memory_worktree)
    changed_paths = changed_worktree_paths(contract.code_worktree)
    metadata_refresh = (
        onboarding_refresh_plan(contract, changed_paths)
        if contract.memory_mode == "external"
        else {
            "required": [],
            "missing": [],
            "unsupported": [],
        }
    )
    entity_refresh = (
        entity_fingerprint_refresh_plan(contract, changed_paths)
        if contract.memory_mode == "external"
        else {
            "required": [],
            "unsupported": [],
        }
    )
    return {
        "state": "would-closeout",
        **status_payload(contract),
        "phase": "commit-approval-pending",
        "summary": "Closeout preview only; no commits were created. External-memory closeout will commit code first, refresh onboarding verification metadata and affected entity fingerprints to that code commit, then commit memory and ledger.",
        **next_guidance(
            "request_commit_approval",
            tool="worktree_closeout_apply",
            args=contract_next_args(
                contract,
                code_commit_message=args.code_commit_message,
                memory_commit_message=args.memory_commit_message,
                ledger_commit_message=args.ledger_commit_message,
            ),
            required_args=["intent_note"],
        ),
        "commit_approval_required": True,
        "approval_question": "Approve creating the code, memory, and ledger commits with these messages?",
        "closeout_order": [
            "commit-code",
            "refresh-onboarding-metadata-and-entity-fingerprints",
            "commit-memory-content",
            "update-ledger",
            "commit-ledger",
            "update-contract",
        ],
        "changed_code_paths": changed_paths,
        "onboarding_metadata_refresh": metadata_refresh,
        "entity_fingerprint_refresh": entity_refresh,
        "proposed_commits": {
            "code": {
                "would_commit": code_dirty,
                "message": args.code_commit_message,
                "worktree": contract.code_worktree.as_posix(),
            },
            "memory": {
                "would_commit": memory_dirty,
                "message": args.memory_commit_message,
                "worktree": contract.memory_worktree.as_posix() if contract.memory_worktree else "",
                "metadata_refresh_after_code_commit": contract.memory_mode == "external",
                "entity_fingerprint_refresh_after_code_commit": contract.memory_mode == "external",
            },
            "ledger": {
                "would_update": contract.memory_mode == "external",
                "message": ledger_message,
                "path": contract.ledger_path.as_posix() if contract.ledger_path else "",
            },
        },
    }


def _validate_closeout_source_heads(contract) -> None:
    current_code_source = head_commit(contract.code_repo_path, contract.code_source_branch)
    if current_code_source != contract.code_base_commit:
        raise RuntimeError(
            "code source branch moved since task start: "
            f"{contract.code_source_branch} is {current_code_source}, expected {contract.code_base_commit}"
        )
    if (
        contract.memory_mode == "external"
        and contract.memory_repo_path is not None
        and contract.memory_base_commit
    ):
        current_memory_source = head_commit(
            contract.memory_repo_path, contract.memory_source_branch
        )
        if current_memory_source != contract.memory_base_commit:
            raise RuntimeError(
                "memory source branch moved since task start: "
                f"{contract.memory_source_branch} is {current_memory_source}, expected {contract.memory_base_commit}"
            )


def _closeout_approval_note(args: argparse.Namespace) -> str:
    if not args.approved:
        raise RuntimeError("closeout requires --approved after explicit commit approval")
    approval_note = args.approval_note.replace("\n", " ").strip()
    if not approval_note:
        raise RuntimeError(
            "closeout requires --approval-note describing the developer's explicit commit approval"
        )
    return approval_note


def _external_closeout_commits(
    contract,
    args: argparse.Namespace,
    changed_paths: list[str],
    code_commit: str,
    code_commit_date: str,
) -> tuple[str, str, list[dict[str, str]], list[dict[str, object]]]:
    if contract.memory_worktree is None or contract.ledger_path is None:
        raise RuntimeError("external-memory closeout requires memory worktree and ledger path")
    refreshed_onboarding = refresh_onboarding_metadata(
        contract, changed_paths, code_commit, code_commit_date
    )
    refreshed_entities = refresh_entity_fingerprints_for_context(
        contract_context(contract), changed_paths
    )
    memory_commit = commit_if_dirty(contract.memory_worktree, args.memory_commit_message)
    ledger = load_ledger(contract.ledger_path)
    write_ledger(contract.ledger_path, prepend_mapping(ledger, code_commit, memory_commit))
    require_git(contract.memory_worktree, ["add", "memory.md"])
    ledger_commit = commit_if_dirty(
        contract.memory_worktree,
        args.ledger_commit_message
        or f"[{contract.task_id}] Ledger sync: {code_commit} -> {memory_commit}",
    )
    return memory_commit, ledger_commit, refreshed_onboarding, refreshed_entities


def closeout_result(args: argparse.Namespace) -> WorktreeCommandResult:
    contract = load_contract(args.contract_path)
    _validate_closeout_source_heads(contract)
    if args.dry_run:
        return WorktreeCommandResult(0, closeout_preview_payload(contract, args))
    approval_note = _closeout_approval_note(args)

    changed_paths = changed_worktree_paths(contract.code_worktree)
    if contract.memory_mode == "external":
        validate_onboarding_refresh_plan(contract, changed_paths)
    code_commit = commit_if_dirty(contract.code_worktree, args.code_commit_message)
    code_commit_date = commit_date(contract.code_worktree, code_commit)
    memory_commit = ""
    ledger_commit = ""
    refreshed_onboarding: list[dict[str, str]] = []
    refreshed_entities: list[dict[str, object]] = []
    if contract.memory_mode == "external":
        memory_commit, ledger_commit, refreshed_onboarding, refreshed_entities = (
            _external_closeout_commits(contract, args, changed_paths, code_commit, code_commit_date)
        )
    updated = replace(
        contract,
        human_review_status="approved",
        approved_for_commit=True,
        commit_approval_note=approval_note,
        closeout_status="completed",
        code_commit=code_commit,
        memory_content_commit=memory_commit,
        ledger_commit=ledger_commit,
    )
    write_contract(contract.contract_path, updated)
    return WorktreeCommandResult(
        0,
        {
            "state": "closed",
            **status_payload(updated),
            "summary": "Closeout completed; integrate the task branches back into their source branches.",
            "code_commit": code_commit,
            "memory_content_commit": memory_commit,
            "ledger_commit": ledger_commit,
            "refreshed_onboarding": refreshed_onboarding,
            "refreshed_entities": refreshed_entities,
        },
    )


def validate_direct_external_context(context, source_branch: str) -> object:
    if context.memory_mode != "external":
        raise RuntimeError("direct closeout currently requires external memory mode")
    if not context.memory_root.exists() or not (context.memory_root / ".git").exists():
        raise RuntimeError(
            f"external memory repo is missing or is not a Git repo: {context.memory_root.as_posix()}"
        )
    if current_branch(context.code_repository_root) != source_branch:
        raise RuntimeError(
            f"code repository is on {current_branch(context.code_repository_root)}, expected {source_branch}"
        )
    memory_branch = current_branch(context.memory_root)
    if memory_branch != source_branch:
        raise RuntimeError(f"memory repo is on {memory_branch}, expected {source_branch}")
    return load_ledger(context.memory_root / "memory.md")


def direct_closeout_preview_payload(
    context, args: argparse.Namespace, source_branch: str
) -> dict[str, object]:
    validate_direct_external_context(context, source_branch)
    code_dirty = worktree_dirty(context.code_repository_root)
    memory_dirty = worktree_dirty(context.memory_root)
    changed_paths = changed_worktree_paths(context.code_repository_root)
    metadata_refresh = onboarding_refresh_plan_for_context(context, changed_paths)
    entity_refresh = entity_fingerprint_refresh_plan_for_context(context, changed_paths)
    ledger_message = (
        args.ledger_commit_message
        or "[direct-closeout] Ledger sync: <code_commit> -> <memory_commit>"
    )
    return {
        "state": "would-direct-closeout",
        "phase": "commit-approval-pending",
        "code_repository_name": context.code_repository_name,
        "code_repository_root": context.code_repository_root.as_posix(),
        "memory_repo": context.memory_root.as_posix(),
        "source_branch": source_branch,
        "summary": "Direct closeout preview only; no commits were created. The real command will commit code first, refresh onboarding verification metadata and affected entity fingerprints to that code commit, then commit memory and ledger.",
        **next_guidance(
            "request_commit_approval",
            tool="direct_closeout_apply",
            args={
                "repo_id": context.code_repository_name,
                "task_name": args.task_name,
                "source_branch": source_branch,
                "code_commit_message": args.code_commit_message,
                "memory_commit_message": args.memory_commit_message,
                "ledger_commit_message": args.ledger_commit_message,
            },
            required_args=["intent_note"],
        ),
        "commit_approval_required": True,
        "approval_question": "Approve creating the direct code, memory, and ledger commits with these messages?",
        "closeout_order": [
            "commit-code",
            "refresh-onboarding-metadata-and-entity-fingerprints",
            "commit-memory-content",
            "update-ledger",
            "commit-ledger",
        ],
        "changed_code_paths": changed_paths,
        "onboarding_metadata_refresh": metadata_refresh,
        "entity_fingerprint_refresh": entity_refresh,
        "proposed_commits": {
            "code": {
                "would_commit": code_dirty,
                "message": args.code_commit_message,
                "worktree": context.code_repository_root.as_posix(),
            },
            "memory": {
                "would_commit": memory_dirty
                or bool(metadata_refresh["required"])
                or bool(entity_refresh["required"]),
                "message": args.memory_commit_message,
                "worktree": context.memory_root.as_posix(),
                "metadata_refresh_after_code_commit": True,
                "entity_fingerprint_refresh_after_code_commit": True,
            },
            "ledger": {
                "would_update": code_dirty
                or memory_dirty
                or bool(metadata_refresh["required"])
                or bool(entity_refresh["required"]),
                "message": ledger_message,
                "path": (context.memory_root / "memory.md").as_posix(),
            },
        },
    }


def direct_closeout_result(args: argparse.Namespace) -> WorktreeCommandResult:
    context = resolve_context(args)
    source_branch = args.source_branch or current_branch(context.code_repository_root)
    validate_direct_external_context(context, source_branch)
    if args.dry_run:
        return WorktreeCommandResult(
            0, direct_closeout_preview_payload(context, args, source_branch)
        )
    if not args.approved:
        raise RuntimeError("direct closeout requires --approved after explicit commit approval")
    approval_note = args.approval_note.replace("\n", " ").strip()
    if not approval_note:
        raise RuntimeError(
            "direct closeout requires --approval-note describing the developer's explicit commit approval"
        )

    changed_paths = changed_worktree_paths(context.code_repository_root)
    memory_was_dirty = worktree_dirty(context.memory_root)
    if not changed_paths and not memory_was_dirty:
        raise RuntimeError("direct closeout found no code or memory changes to commit")
    validate_onboarding_refresh_plan_for_context(context, changed_paths)

    code_commit = commit_if_dirty(context.code_repository_root, args.code_commit_message)
    code_commit_date = commit_date(context.code_repository_root, code_commit)
    refreshed_onboarding = refresh_onboarding_metadata_for_context(
        context, changed_paths, code_commit, code_commit_date
    )
    refreshed_entities = refresh_entity_fingerprints_for_context(context, changed_paths)
    memory_commit = commit_if_dirty(context.memory_root, args.memory_commit_message)
    ledger_path = context.memory_root / "memory.md"
    ledger = load_ledger(ledger_path)
    write_ledger(ledger_path, prepend_mapping(ledger, code_commit, memory_commit))
    require_git(context.memory_root, ["add", "memory.md"])
    ledger_commit = commit_if_dirty(
        context.memory_root,
        args.ledger_commit_message
        or f"[direct-closeout] Ledger sync: {code_commit} -> {memory_commit}",
    )
    return WorktreeCommandResult(
        0,
        {
            "state": "direct-closed",
            "phase": "done",
            "code_repository_name": context.code_repository_name,
            "code_repository_root": context.code_repository_root.as_posix(),
            "memory_repo": context.memory_root.as_posix(),
            "source_branch": source_branch,
            "summary": "Direct closeout completed; code, memory content, and ledger commits were created on the current branches.",
            "approval_note": approval_note,
            "changed_code_paths": changed_paths,
            "code_commit": code_commit,
            "memory_content_commit": memory_commit,
            "ledger_commit": ledger_commit,
            "refreshed_onboarding": refreshed_onboarding,
            "refreshed_entities": refreshed_entities,
        },
    )
