"""Explicit dependency surface for targeted ambient-role acceptance."""

from __future__ import annotations

import subprocess
from pathlib import Path

DEPENDENCY_PREFIXES = (
    ".codex/config.toml",
    ".dagger/src/agents_remember_quality/main.py",
    "scripts/e2e_harness/",
    "scripts/harness/",
    "skills/l-01-agent-lifecycles/",
    "mcp/src/agents_remember/application/structural/",
    "mcp/src/agents_remember/controlplane/",
    "mcp/src/agents_remember/kernel/primitives/checkout_coordination.py",
    "mcp/src/agents_remember/kernel/primitives/observer_paths.py",
    "mcp/src/agents_remember/kernel/primitives/runtime_config.py",
    "mcp/src/agents_remember/kernel/agentic_settings.py",
    "mcp/src/agents_remember/kernel/_agentic_settings_",
    "mcp/src/agents_remember/mcp/public_surface.py",
    "mcp/src/agents_remember/mcp/registration/",
    "mcp/src/agents_remember/mcp/tools/structural_agent.py",
    "mcp/src/agents_remember/models/structural/",
    "mcp/src/agents_remember/models/conversations/control_wire.py",
    "mcp/src/agents_remember/models/task_document_ref.py",
    "mcp/src/agents_remember/models/terminal_catalog.py",
    "mcp/src/agents_remember/models/tools/tool_registry.py",
    "mcp/src/agents_remember/serving/",
    "mcp/src/agents_remember/tasks/",
    "mcp/src/agents_remember/worktrees/source_lineage.py",
    "mcp/src/agents_remember/worktrees/modules/git.py",
    "mcp/src/agents_remember/worktrees/worktree_contract.py",
    "mcp/test_support/agents_remember_test_support/testing/dagger_admission.py",
)


def changed_paths(repository: Path, diff_base: str) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "diff", "--name-only", diff_base, "--"],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"cannot select ambient-role E2E from {diff_base!r}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--"],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    if untracked.returncode != 0:
        raise RuntimeError(
            "cannot enumerate untracked ambient-role E2E inputs: "
            f"{untracked.stderr.strip() or untracked.stdout.strip()}"
        )
    return tuple(
        sorted(
            {
                line
                for output in (result.stdout, untracked.stdout)
                for line in output.splitlines()
                if line
            }
        )
    )


def selected_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        path
        for path in paths
        if any(path == prefix or path.startswith(prefix) for prefix in DEPENDENCY_PREFIXES)
    )
