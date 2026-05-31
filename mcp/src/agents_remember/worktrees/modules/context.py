from __future__ import annotations

from dataclasses import replace

from agents_remember.kernel import coordination_context_resolver as resolver
from agents_remember.kernel import filesystem
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.worktree_contract import WorktreeContract


def resolve_context(args: WorktreeArgs):
    return resolver.resolve_coordination_context(
        code_repository_name=args.code_repository_name,
        workspace_root=args.workspace_root,
        requested_topology=args.topology,
        coordination_root=args.coordination_root,
        code_repository_root=args.code_repository_root,
        task_name=getattr(args, "task_name", None),
        worktree_name=getattr(args, "worktree_name", None),
        contract_path=getattr(args, "contract_path", None),
    )


def contract_context(contract: WorktreeContract):
    context = resolver.resolve_coordination_context(
        code_repository_name=contract.repo_name,
        workspace_root=contract.coordination_root.parent,
        code_repository_root=contract.code_repo_path,
        contract_path=contract.contract_path,
    )
    if contract.memory_mode != "external" or contract.memory_worktree is None:
        return context

    worktree_settings_path = contract.memory_worktree / "system" / "settings.md"
    worktree_path_settings_path = resolver.path_settings_path_for(worktree_settings_path)
    if not filesystem.exists(worktree_settings_path) and not filesystem.exists(
        worktree_path_settings_path
    ):
        return context

    storage, cross_repo = resolver.parse_coordination_settings(
        worktree_settings_path, context.topology
    )
    system_root = worktree_settings_path.parent
    resolved_cross_repo = resolver.resolve_cross_repo_settings(
        cross_repo,
        contract.code_repo_path.parent,
        context.coordination_root,
    )
    return replace(
        context,
        settings_path=worktree_settings_path,
        path_settings_path=(
            worktree_path_settings_path if filesystem.exists(worktree_path_settings_path) else None
        ),
        system_root=system_root,
        sources_path=system_root / "sources.md",
        tools_path=system_root / "tools.md",
        storage=storage,
        path_rules=storage.path_rules,
        cross_repo=resolved_cross_repo,
    )
