#!/usr/bin/env python3
"""Compatibility facade for Agents Remember coordination context resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from agents_remember.kernel.coordination_context import paths as _paths
from agents_remember.kernel.coordination_context import resolver as _resolver
from agents_remember.kernel.coordination_context.cli import main
from agents_remember.kernel.coordination_context.contracts import (
    find_task_contract,
    find_worktree_contract,
    resolve_contract,
)
from agents_remember.kernel.coordination_context.cross_repo import (
    _entry_with_state,
    code_repo_exclusion,
    code_repository_info,
    git_branch,
    git_head_or_empty,
    invalid_cross_repo_entry,
    memory_repository_info,
    resolve_cross_repo_entry,
    resolve_cross_repo_memory,
    resolve_cross_repo_settings,
    run_git,
    with_memory_ledger_state,
)
from agents_remember.kernel.coordination_context.models import (
    CoordinationContext,
    CoordinationSelection,
    CrossRepoAllowEntry,
    CrossRepoSettings,
    MissingMemoryError,
    StorageRule,
    StorageSettings,
)
from agents_remember.kernel.coordination_context.paths import (
    DEFAULT_AR_COORDINATION_ROOT,
    agents_repo_from_script,
    clean_scalar,
    default_storage_mode,
    external_memory_root,
    extract_yaml_blocks,
    find_code_repository_root,
    infer_settings_path,
    infer_topology_from_onboarding_root,
    internal_coordination_root,
    internal_memory_root,
    looks_like_installed_coordination_root,
    memory_roots_from_settings,
    normalize_rel_path,
    path_settings_path_for,
    settings_path_for_roots,
)
from agents_remember.kernel.coordination_context.resolver import build_coordination_context
from agents_remember.kernel.coordination_context.serialize import (
    context_to_dict,
    cross_repo_entry_to_dict,
    cross_repo_to_dict,
    path_rule_to_dict,
    path_to_string,
    print_text,
    storage_to_dict,
)
from agents_remember.kernel.coordination_context.settings import (
    optional_bool,
    optional_mapping,
    parse_coordination_settings,
    parse_cross_repo_allow,
    parse_cross_repo_allow_entry,
    parse_json_cross_repo_settings,
    parse_json_path_rule,
    parse_json_path_rules,
    parse_json_settings,
    parse_json_storage_settings,
    parse_settings_block,
    require_mapping,
    required_clean_string,
    string_list,
)
from agents_remember.kernel.coordination_context.storage import (
    default_rule_storage,
    default_unmatched_storage,
    excludes_file_type,
    expand_pattern_variants,
    is_sidecar_storage,
    matches_any,
    matches_file_type,
    normalize_file_type,
    normalize_rule_base,
    relative_to_rule_base,
    resolve_storage_for_source,
    resolve_storage_from_rule,
    rule_excludes_source,
    rule_file_types,
    rule_includes_source,
    rule_patterns,
    source_file_type,
)


def resolve_coordination_root_hint(coordination_root: Path | None) -> Path:
    return _with_facade_agents_repo(_paths.resolve_coordination_root_hint, coordination_root)


def detect_coordination_selection(
    code_repository_name: str,
    code_repository_root: Path,
    requested_topology: Literal["internal", "external"] | None = None,
    coordination_root_hint: Path | None = None,
    settings_path: Path | None = None,
) -> CoordinationSelection:
    return _with_facade_agents_repo(
        _resolver.detect_coordination_selection,
        code_repository_name,
        code_repository_root,
        requested_topology,
        coordination_root_hint,
        settings_path,
    )


def resolve_coordination_context(
    code_repository_name: str | None = None,
    workspace_root: Path | None = None,
    requested_topology: Literal["internal", "external"] | None = None,
    coordination_root: Path | None = None,
    settings_path: Path | None = None,
    onboarding_root: Path | None = None,
    code_repository_root: Path | None = None,
    contract_path: Path | None = None,
    task_name: str | None = None,
    worktree_name: str | None = None,
) -> CoordinationContext:
    return _with_facade_agents_repo(
        _resolver.resolve_coordination_context,
        code_repository_name,
        workspace_root,
        requested_topology,
        coordination_root,
        settings_path,
        onboarding_root,
        code_repository_root,
        contract_path,
        task_name,
        worktree_name,
    )


def _with_facade_agents_repo(function, *args, **kwargs):
    # The facade re-exports `agents_repo_from_script` from `_paths`, so under
    # normal use this swap is an identity rebind (a no-op against the source
    # object). It is load-bearing as a test seam: callers/tests patch
    # `coordination_context_resolver.agents_repo_from_script` (the facade-level
    # name), and this propagates that binding into `_paths`, where
    # `resolve_coordination_root_hint` actually invokes it. Removing the swap
    # makes those patches ineffective. See test_worktree_support.py
    # test_resolver_* coordination-root tests.
    original = _paths.agents_repo_from_script
    _paths.agents_repo_from_script = agents_repo_from_script
    try:
        return function(*args, **kwargs)
    finally:
        _paths.agents_repo_from_script = original

__all__ = [
    "DEFAULT_AR_COORDINATION_ROOT",
    "CoordinationContext",
    "CoordinationSelection",
    "CrossRepoAllowEntry",
    "CrossRepoSettings",
    "MissingMemoryError",
    "StorageRule",
    "StorageSettings",
    "_entry_with_state",
    "agents_repo_from_script",
    "build_coordination_context",
    "clean_scalar",
    "code_repo_exclusion",
    "code_repository_info",
    "context_to_dict",
    "cross_repo_entry_to_dict",
    "cross_repo_to_dict",
    "default_rule_storage",
    "default_storage_mode",
    "default_unmatched_storage",
    "detect_coordination_selection",
    "excludes_file_type",
    "expand_pattern_variants",
    "external_memory_root",
    "extract_yaml_blocks",
    "find_code_repository_root",
    "find_task_contract",
    "find_worktree_contract",
    "git_branch",
    "git_head_or_empty",
    "infer_settings_path",
    "infer_topology_from_onboarding_root",
    "internal_coordination_root",
    "internal_memory_root",
    "invalid_cross_repo_entry",
    "is_sidecar_storage",
    "looks_like_installed_coordination_root",
    "main",
    "matches_any",
    "matches_file_type",
    "memory_repository_info",
    "memory_roots_from_settings",
    "normalize_file_type",
    "normalize_rel_path",
    "normalize_rule_base",
    "optional_bool",
    "optional_mapping",
    "parse_coordination_settings",
    "parse_cross_repo_allow",
    "parse_cross_repo_allow_entry",
    "parse_json_cross_repo_settings",
    "parse_json_path_rule",
    "parse_json_path_rules",
    "parse_json_settings",
    "parse_json_storage_settings",
    "parse_settings_block",
    "path_rule_to_dict",
    "path_settings_path_for",
    "path_to_string",
    "print_text",
    "relative_to_rule_base",
    "require_mapping",
    "required_clean_string",
    "resolve_contract",
    "resolve_coordination_context",
    "resolve_coordination_root_hint",
    "resolve_cross_repo_entry",
    "resolve_cross_repo_memory",
    "resolve_cross_repo_settings",
    "resolve_storage_for_source",
    "resolve_storage_from_rule",
    "rule_excludes_source",
    "rule_file_types",
    "rule_includes_source",
    "rule_patterns",
    "run_git",
    "settings_path_for_roots",
    "source_file_type",
    "storage_to_dict",
    "string_list",
    "with_memory_ledger_state",
]


if __name__ == "__main__":
    raise SystemExit(main())
