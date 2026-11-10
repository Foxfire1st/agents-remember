"""A memory-init/baseline leaf with no hand-authored onboarding directory or cards."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from agents_remember.application.memory_tools import (
    CitationOperationScope,
    citation_fix_tool,
    memory_baseline_adopt_tool,
    memory_init_tool,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    contract_publication_text,
    default_contract,
    default_series_contract,
    write_contract,
)
from test_worktree_support import git, init_repo


@dataclass(frozen=True)
class FreshRepository:
    contract: WorktreeContract
    config: McpRuntimeConfig


def author_first_card(world: FreshRepository) -> None:
    """Author actual card content after the empty-memory MQC, following c-05."""

    contract = world.contract
    assert contract.memory_worktree is not None
    onboarding = contract.memory_worktree / "onboarding"
    assert not list(onboarding.rglob("*.md"))
    # Route indexes are derived, uncommitted caches (the c-05 card workflow).
    excludes = Path(
        git(
            contract.memory_worktree,
            "rev-parse",
            "--path-format=absolute",
            "--git-path",
            "info/exclude",
        )
    )
    with excludes.open("a", encoding="utf-8") as stream:
        stream.write("\noverview.index.json\n")
    onboarding.mkdir(exist_ok=True)
    (onboarding / "overview.md").write_text(
        "# Fresh repository\n\n"
        "## What This Repo Is\n\nA minimal identity-function library.\n\n"
        "## How It Is Built\n\nThe app module exposes one function.\n\n"
        "## Invariants And Boundaries\n\nIdentity returns the caller's value unchanged.\n\n"
        "## Evidence\n\n| Finding | Anchor | Source |\n| --- | --- | --- |\n"
        "| The identity module is the public surface. | | app.py |\n",
        encoding="utf-8",
    )
    (onboarding / "app.py.md").write_text(
        "# app.py\n\n## Governing Overview\n\n[Repository overview](overview.md)\n\n"
        "## Purpose\n\nExpose an identity operation that preserves its input.\n\n"
        "## Code Commentary\n\nThe function returns the argument without transforming it.\n\n"
        "## Evidence\n\n| Finding | Anchor | Source |\n| --- | --- | --- |\n"
        "| Identity returns its input unchanged. | `identity` | app.py:1-2 |\n",
        encoding="utf-8",
    )
    for dry_run in (True, False):
        citations = citation_fix_tool(
            world.config,
            repo_id=contract.repo_name,
            contract_path=str(contract.contract_path),
            dry_run=dry_run,
            operation_scope=CitationOperationScope(documents=("overview.md", "app.py.md")),
        )
        assert citations["ok"], citations


def fresh_repository(root: Path) -> FreshRepository:
    """Use the first-user APIs, then open their committed baseline as a leaf pair."""

    code = root / "fresh"
    init_repo(code)
    (code / "app.py").write_text("def identity(value):\n    return value\n", encoding="utf-8")
    git(code, "add", "app.py")
    git(code, "commit", "-m", "Add identity function")
    code_base = git(code, "rev-parse", "HEAD")
    coordination = root / "ar-coordination"
    coordination.mkdir()
    memory = coordination / "memory-repos" / "ar-fresh"
    config = McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=coordination,
        workspace_root=root,
        transcript_root=coordination / "logs" / "mcp",
        repositories={"fresh": RepositoryScope(repo_id="fresh", path=code, memory_root=memory)},
    )
    initialized = memory_init_tool(config, repo_id="fresh", initial_branch="main")
    assert initialized["ok"], initialized
    assert list((memory / "onboarding").iterdir()) == []
    adopted = memory_baseline_adopt_tool(config, repo_id="fresh")
    assert adopted["ok"] and adopted["state"] == "adopted", adopted
    assert adopted["bootstrap"]["state"] == "adopted-baseline", adopted
    assert not git(memory, "ls-tree", "HEAD", "onboarding")
    memory_base = git(memory, "rev-parse", "HEAD")
    git(code, "branch", "super", code_base)
    git(memory, "branch", "super", memory_base)
    parent = default_series_contract(
        ContractTask(
            name="Foundation Master",
            repo_name="fresh",
            coordination_root=coordination,
            workflow_kind="chat-task",
            memory_mode="external",
        ),
        code=RepoBranchPlan(
            repo_path=code,
            source_branch="main",
            work_branch="super",
            base_commit=code_base,
        ),
        memory=RepoBranchPlan(
            repo_path=memory,
            source_branch="main",
            work_branch="super",
            base_commit=memory_base,
        ),
    )
    leaf = default_contract(
        ContractTask(
            name="Foundation",
            repo_name="fresh",
            coordination_root=coordination,
            workflow_kind="chat-task",
            memory_mode="external",
        ),
        leaf=LeafIdentity(worktree_name="foundation", leaf_id="260928-FRESH-L1"),
        code=RepoBranchPlan(
            repo_path=code,
            source_branch="super",
            work_branch="ar/foundation",
            base_commit=code_base,
        ),
        memory=RepoBranchPlan(
            repo_path=memory,
            source_branch="super",
            work_branch="ar/foundation",
            base_commit=memory_base,
        ),
    )
    leaf = replace(leaf, parent_contract_path=parent.contract_path)
    for contract in (parent, leaf):
        write_contract(contract.contract_path, contract)
        publish_new_lifecycle_operation_location(
            contract,
            contract_text=contract_publication_text(contract.contract_path, contract),
        )
    assert leaf.memory_worktree is not None
    git(code, "worktree", "add", "-b", leaf.code_work_branch, str(leaf.code_worktree), "super")
    git(
        memory, "worktree", "add", "-b", leaf.memory_work_branch, str(leaf.memory_worktree), "super"
    )
    assert not (leaf.memory_worktree / "onboarding").exists()
    return FreshRepository(leaf, config)
