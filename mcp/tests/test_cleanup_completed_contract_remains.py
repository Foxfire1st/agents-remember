"""Rule-5 completed-contract-with-remains: a terminal completed contract whose
folder/branches still exist is finished by the repeated cleanup.

Missing same-lifecycle cache state is no refusal where remains exist: the dry
run lists them and the guard's genuine completion records the terminal state.
An active state of another lifecycle keeps the refusal and changes nothing.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import CitationCacheError
from agents_remember.memory_quality.style.citations import (
    source_index_cache as cache,
)
from agents_remember.worktrees.modules import cleanup as cleanup_module
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
from lifecycle_enclosure_test_support import publish_test_enclosure
from test_worktree_support import git, init_repo

CODE_BRANCH = "ar/fixture-remains"
MEMORY_BRANCH = "ar/fixture-remains-memory"
SOURCE_BRANCH = "main"
OTHER_LIFECYCLE = "01OTHERLIFECYCLE0000000000000"


def _commit(repo: Path, name: str) -> None:
    (repo / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
    git(repo, "add", f"{name}.txt")
    git(repo, "commit", "-m", name)


def _repo_with_branch(repo: Path, branch: str) -> None:
    init_repo(repo, branch=SOURCE_BRANCH)
    git(repo, "checkout", "-b", branch)
    _commit(repo, "leaf-work")
    git(repo, "checkout", SOURCE_BRANCH)
    git(repo, "merge", "--no-ff", branch, "-m", "merge leaf")


def _contract(root: Path, code_repo: Path, memory_repo: Path) -> Path:
    contract_path = root / "tasks" / "repo-a" / "leaf" / "enclosures" / "a" / "series-contract.md"
    code_worktree = root / "worktrees" / "repo-a" / "leaf" / "code"
    memory_worktree = root / "worktrees" / "repo-a" / "leaf" / "memory"
    code_worktree.parent.mkdir(parents=True, exist_ok=True)
    memory_worktree.parent.mkdir(parents=True, exist_ok=True)
    git(code_repo, "worktree", "add", str(code_worktree), CODE_BRANCH)
    git(memory_repo, "worktree", "add", str(memory_worktree), MEMORY_BRANCH)
    contract = WorktreeContract(
        task_id="task-1",
        task_name="task-one",
        repo_name="repo-a",
        workflow_kind="light-task",
        memory_mode="external",  # type: ignore[arg-type]
        coordination_root=root,
        task_root=root / "tasks" / "repo-a" / "leaf",
        contract_path=contract_path,
        task_artifact=root / "tasks" / "repo-a" / "leaf" / "task.md",
        worktree_group=root / "worktrees" / "repo-a" / "leaf",
        code_repo_path=code_repo,
        code_source_branch=SOURCE_BRANCH,
        code_work_branch=CODE_BRANCH,
        code_base_commit="a" * 40,
        code_worktree=code_worktree,
        memory_repo_path=memory_repo,
        memory_source_branch=SOURCE_BRANCH,
        memory_work_branch=MEMORY_BRANCH,
        memory_worktree=memory_worktree,
        ledger_path=root / "worktrees" / "repo-a" / "leaf" / "memory-ledger.md",
        integration_status="completed",  # type: ignore[arg-type]
        cleanup="completed",  # type: ignore[arg-type]
        kind="leaf",
        leaf_id="L98",
        lifecycle_id="01TESTREMAINS00000000000000",
    )
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    write_contract(contract_path, contract)
    publish_test_enclosure(contract, contract_path.read_text(encoding="utf-8"))
    return contract_path


def _branch_present(repo: Path, branch: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _cache_state_path(root: Path, contract_path: Path) -> Path:
    authority = cache.contract_cache_authority(load_contract(contract_path))
    assert authority is not None
    return authority.control_state


def test_completed_contract_missing_state_lists_remains_and_records_terminal(
    tmp_path: Path,
) -> None:
    """Missing same-lifecycle state + remains: dry lists them, genuine completion records."""
    code_repo = tmp_path / "code"
    memory_repo = tmp_path / "memory"
    _repo_with_branch(code_repo, CODE_BRANCH)
    _repo_with_branch(memory_repo, MEMORY_BRANCH)
    contract_path = _contract(tmp_path, code_repo, memory_repo)
    assert not _cache_state_path(tmp_path, contract_path).exists()

    dry = cleanup_module.cleanup_result(WorktreeArgs(contract_path=contract_path, dry_run=True))
    assert "no matching persistent citation cache fence state" not in str(
        dry.payload.get("citation_source_index", "")
    )
    assert "could not reserve exact terminal citation cache authority" not in str(
        dry.payload.get("summary", "")
    )
    listed = json.dumps(dry.payload)
    assert CODE_BRANCH in listed
    assert "code" in listed

    contract = load_contract(contract_path)
    with cache.terminal_namespace_guard(
        contract,
        requested_contract_path=contract_path,
        completed_contract_with_remains=True,
    ) as guard:
        guard.complete(outcome="completed", publish=lambda: None, rollback_publish=lambda: None)
    state_path = _cache_state_path(tmp_path, contract_path)
    assert state_path.exists()
    recorded = json.loads(state_path.read_text(encoding="utf-8"))
    assert recorded["phase"] == "terminal"
    assert recorded["lifecycleId"] == "01TESTREMAINS00000000000000"


def test_completed_contract_active_other_lifecycle_keeps_refusal(
    tmp_path: Path,
) -> None:
    """Active state of another lifecycle: the refusal stays, nothing changes."""
    code_repo = tmp_path / "code"
    memory_repo = tmp_path / "memory"
    _repo_with_branch(code_repo, CODE_BRANCH)
    _repo_with_branch(memory_repo, MEMORY_BRANCH)
    contract_path = _contract(tmp_path, code_repo, memory_repo)

    authority = cache.contract_cache_authority(load_contract(contract_path))
    assert authority is not None
    authority.control_dir.mkdir(parents=True, exist_ok=True)
    state_path = _cache_state_path(tmp_path, contract_path)
    state_path.write_text(
        cache.CacheControlState(OTHER_LIFECYCLE, "active", "").to_json(authority),
        encoding="utf-8",
    )

    with pytest.raises(
        CitationCacheError, match="no matching persistent citation cache fence state"
    ):
        cache.terminal_namespace_guard(
            load_contract(contract_path),
            requested_contract_path=contract_path,
        ).__enter__()
    assert _branch_present(code_repo, CODE_BRANCH)
    assert _branch_present(memory_repo, MEMORY_BRANCH)
