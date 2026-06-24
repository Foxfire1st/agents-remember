from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents_remember.kernel.coordination_context.resolver import resolve_coordination_context
from agents_remember.kernel.coordination_context.serialize import context_to_dict, print_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve the active Agents Remember coordination context for one repository."
    )
    parser.add_argument(
        "--code-repository-name",
        help="Code repository name to resolve. This is the normal agent-facing input.",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=Path.cwd(),
        help="Workspace root used to find --code-repository-name.",
    )
    parser.add_argument(
        "--code-repository-root",
        type=Path,
        help="Root directory of the code repository to resolve.",
    )
    parser.add_argument(
        "--topology", choices=("internal", "external"), help="Optional topology override."
    )
    parser.add_argument(
        "--coordination-root", type=Path, help="Optional coordination root hint or override."
    )
    parser.add_argument(
        "--settings-path",
        type=Path,
        help=(
            "Optional active settings.md override. A sibling settings.json is preferred for "
            "machine-readable path settings when present."
        ),
    )
    parser.add_argument(
        "--onboarding-root",
        type=Path,
        help="Override for an already resolved code repository onboarding root.",
    )
    parser.add_argument(
        "--contract-path",
        type=Path,
        help="Optional worktree task series-contract.md path to resolve task/worktree context.",
    )
    parser.add_argument(
        "--task-name",
        help=(
            "Optional task name used to locate coordination tasks under "
            "ar-coordination/tasks/<code-repository-name>/, excluding 0_archive."
        ),
    )
    parser.add_argument(
        "--parent-task",
        help="Optional parent task name used only to disambiguate nested active task roots.",
    )
    parser.add_argument(
        "--leaf-id",
        help="Optional leaf enclosure id used to resolve a specific leaf series-contract.md.",
    )
    parser.add_argument(
        "--worktree-name",
        help="Optional worktree name used to compute the worktree group when no contract exists.",
    )
    parser.add_argument("--format", choices=("json", "text"), default="json", help="Output format.")
    args = parser.parse_args(argv)

    try:
        context = resolve_coordination_context(
            code_repository_name=args.code_repository_name,
            workspace_root=args.workspace_root,
            requested_topology=args.topology,
            coordination_root=args.coordination_root,
            settings_path=args.settings_path,
            onboarding_root=args.onboarding_root,
            code_repository_root=args.code_repository_root,
            contract_path=args.contract_path,
            task_name=args.task_name,
            parent_task=args.parent_task,
            leaf_id=args.leaf_id,
            worktree_name=args.worktree_name,
        )
    except ValueError as error:
        parser.error(str(error))

    if args.format == "json":
        print(json.dumps(context_to_dict(context), indent=2))
    else:
        print_text(context)
    return 0
