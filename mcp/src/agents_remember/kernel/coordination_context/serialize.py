from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.kernel.coordination_context.models import (
    CoordinationContext,
    CrossRepoAllowEntry,
    CrossRepoSettings,
    StorageRule,
    StorageSettings,
)


def path_to_string(path: Path) -> str:
    return path.resolve().as_posix()


def path_rule_to_dict(rule: StorageRule) -> dict[str, object]:
    return {
        "path": str(rule.get("path", "")),
        "storage": str(rule.get("storage", "")) if rule.get("storage") else "",
        "include": {
            "paths": list(rule.get("includes", [])),
            "fileTypes": list(rule.get("include_file_types", [])),
        },
        "exclude": {
            "paths": list(rule.get("excludes", [])),
            "fileTypes": list(rule.get("exclude_file_types", [])),
        },
    }


def storage_to_dict(storage: StorageSettings) -> dict[str, object]:
    return {
        "mode": storage.mode,
        "default": storage.default,
        "pathRules": [path_rule_to_dict(rule) for rule in storage.path_rules],
    }


def cross_repo_entry_to_dict(entry: CrossRepoAllowEntry) -> dict[str, object]:
    payload: dict[str, object] = {
        "repo": entry.repo,
        "expectedBranch": entry.expected_branch,
        "includeCode": entry.include_code,
        "includeMemory": entry.include_memory,
    }
    if entry.state:
        payload["state"] = entry.state
    if entry.reason:
        payload["reason"] = entry.reason
    if entry.code:
        payload["code"] = entry.code
    if entry.memory:
        payload["memory"] = entry.memory
    return payload


def cross_repo_to_dict(cross_repo: CrossRepoSettings) -> dict[str, object]:
    payload: dict[str, object] = {
        "allow": [cross_repo_entry_to_dict(entry) for entry in cross_repo.allow]
    }
    if cross_repo.errors:
        payload["errors"] = cross_repo.errors
    return payload


def context_to_dict(context: CoordinationContext) -> dict[str, object]:
    return {
        "topology": context.topology,
        "code_repository_name": context.code_repository_name,
        "code_repository_root": path_to_string(context.code_repository_root),
        "coordination_root": path_to_string(context.coordination_root),
        "memory_root": path_to_string(context.memory_root),
        "memory_mode": context.memory_mode,
        "onboarding_root": path_to_string(context.onboarding_root),
        "settings_path": path_to_string(context.settings_path),
        "path_settings_path": path_to_string(context.path_settings_path)
        if context.path_settings_path
        else "",
        "task_root": path_to_string(context.task_root),
        "temp_root": path_to_string(context.temp_root),
        "docs_root": path_to_string(context.docs_root),
        "system_root": path_to_string(context.system_root),
        "sources_path": path_to_string(context.sources_path),
        "tools_path": path_to_string(context.tools_path),
        "contract_path": path_to_string(context.contract_path) if context.contract_path else "",
        "worktree_group": path_to_string(context.worktree_group) if context.worktree_group else "",
        "code_worktree": path_to_string(context.code_worktree) if context.code_worktree else "",
        "memory_worktree": path_to_string(context.memory_worktree)
        if context.memory_worktree
        else "",
        "ledger_path": path_to_string(context.ledger_path) if context.ledger_path else "",
        "storage": storage_to_dict(context.storage),
        "pathRules": [path_rule_to_dict(rule) for rule in context.path_rules],
        "crossRepo": cross_repo_to_dict(context.cross_repo),
    }


def print_text(context: CoordinationContext) -> None:
    for label, value in text_rows(context):
        print(f"{label}\t{value}")


def text_rows(context: CoordinationContext) -> list[tuple[str, str]]:
    rows = [
        ("topology", context.topology),
        ("code_repository_name", context.code_repository_name),
        ("code_repository_root", context.code_repository_root),
        ("coordination_root", context.coordination_root),
        ("memory_root", context.memory_root),
        ("memory_mode", context.memory_mode),
        ("onboarding_root", context.onboarding_root),
        ("settings_path", context.settings_path),
        ("task_root", context.task_root),
        ("temp_root", context.temp_root),
        ("docs_root", context.docs_root),
        ("storage_mode", context.storage.mode),
    ]
    return [(label, text_value(value)) for label, value in rows] + optional_text_rows(context)


def optional_text_rows(context: CoordinationContext) -> list[tuple[str, str]]:
    rows = [
        ("path_settings_path", context.path_settings_path),
        ("contract_path", context.contract_path),
        ("worktree_group", context.worktree_group),
        ("code_worktree", context.code_worktree),
        ("memory_worktree", context.memory_worktree),
        ("ledger_path", context.ledger_path),
    ]
    return [(label, path.as_posix()) for label, path in rows if path is not None]


def text_value(value: Any) -> str:
    if isinstance(value, Path):
        return value.as_posix()
    return str(value)
