#!/usr/bin/env python3
"""Manage Agents Remember task Git lifecycle.

Requires Python 3.10+ and git. Uses only the Python standard library.
"""

from __future__ import annotations

from agents_remember.worktrees.modules.abandon import abandon_result
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.cleanup import (
    cleanup_result,
    delete_branch_force,
    delete_branch_if_merged,
    remove_empty_dir,
    remove_registered_worktree,
)
from agents_remember.worktrees.modules.cli import (
    add_common,
    build_parser,
    command_attach,
    command_cleanup,
    command_closeout,
    command_direct_closeout,
    command_integrate,
    command_start,
    command_status,
    main,
    parse_json_stdout,
)
from agents_remember.worktrees.modules.closeout import (
    closeout_preview_payload,
    closeout_result,
    direct_closeout_preview_payload,
    direct_closeout_result,
    validate_direct_external_context,
)
from agents_remember.worktrees.modules.context import contract_context, resolve_context
from agents_remember.worktrees.modules.git import (
    branch_exists,
    changed_worktree_paths,
    commit_date,
    commit_if_dirty,
    contract_has_worktree_changes,
    current_branch,
    ensure_git_identity,
    ensure_worktree,
    has_changes,
    head_commit,
    is_ancestor,
    require_clean,
    require_git,
    run_git,
    worktree_dirty,
)
from agents_remember.worktrees.modules.guidance import (
    contract_next_args,
    contract_payload,
    lifecycle_guidance,
    next_guidance,
    status_payload,
)
from agents_remember.worktrees.modules.integrate import (
    blocked_integration_payload,
    integrate_result,
    integration_branch,
    replay_code_if_needed,
    replay_memory_content,
    validate_integrate_contract,
)
from agents_remember.worktrees.modules.models import (
    WorktreeCommandResult,
    WorktreeProviderSetupConfig,
)
from agents_remember.worktrees.modules.onboarding import (
    ENTITY_FINGERPRINT_ALGORITHM,
    compute_git_blob_set_fingerprint,
    entity_fingerprint_refresh_plan,
    entity_fingerprint_refresh_plan_for_context,
    markdown_table_cells,
    normalized_table_cell,
    onboarding_metadata_row,
    onboarding_refresh_plan,
    onboarding_refresh_plan_for_context,
    parse_entity_fingerprint_rows,
    refresh_entity_fingerprints_for_context,
    refresh_onboarding_metadata,
    refresh_onboarding_metadata_for_context,
    sidecar_onboarding_path,
    validate_onboarding_refresh_plan,
    validate_onboarding_refresh_plan_for_context,
)
from agents_remember.worktrees.modules.provider_teardown import (
    teardown_worktree_providers,
)
from agents_remember.worktrees.modules.start import (
    attach_result,
    load_contract_from_args,
    prepare_memory_for_start,
    prepare_providers_for_start,
    start_result,
    status_result,
)
from agents_remember.worktrees.modules.sync import sync_result

__all__ = [
    "ENTITY_FINGERPRINT_ALGORITHM",
    "WorktreeArgs",
    "WorktreeCommandResult",
    "WorktreeProviderSetupConfig",
    "abandon_result",
    "add_common",
    "attach_result",
    "blocked_integration_payload",
    "branch_exists",
    "build_parser",
    "changed_worktree_paths",
    "cleanup_result",
    "closeout_preview_payload",
    "closeout_result",
    "command_attach",
    "command_cleanup",
    "command_closeout",
    "command_direct_closeout",
    "command_integrate",
    "command_start",
    "command_status",
    "commit_date",
    "commit_if_dirty",
    "compute_git_blob_set_fingerprint",
    "contract_context",
    "contract_has_worktree_changes",
    "contract_next_args",
    "contract_payload",
    "current_branch",
    "delete_branch_force",
    "delete_branch_if_merged",
    "direct_closeout_preview_payload",
    "direct_closeout_result",
    "ensure_git_identity",
    "ensure_worktree",
    "entity_fingerprint_refresh_plan",
    "entity_fingerprint_refresh_plan_for_context",
    "has_changes",
    "head_commit",
    "integrate_result",
    "integration_branch",
    "is_ancestor",
    "lifecycle_guidance",
    "load_contract_from_args",
    "main",
    "markdown_table_cells",
    "next_guidance",
    "normalized_table_cell",
    "onboarding_metadata_row",
    "onboarding_refresh_plan",
    "onboarding_refresh_plan_for_context",
    "parse_entity_fingerprint_rows",
    "parse_json_stdout",
    "prepare_memory_for_start",
    "prepare_providers_for_start",
    "refresh_entity_fingerprints_for_context",
    "refresh_onboarding_metadata",
    "refresh_onboarding_metadata_for_context",
    "remove_empty_dir",
    "remove_registered_worktree",
    "replay_code_if_needed",
    "replay_memory_content",
    "require_clean",
    "require_git",
    "resolve_context",
    "run_git",
    "sidecar_onboarding_path",
    "start_result",
    "status_payload",
    "status_result",
    "sync_result",
    "teardown_worktree_providers",
    "validate_direct_external_context",
    "validate_integrate_contract",
    "validate_onboarding_refresh_plan",
    "validate_onboarding_refresh_plan_for_context",
    "worktree_dirty",
]


if __name__ == "__main__":
    raise SystemExit(main())
