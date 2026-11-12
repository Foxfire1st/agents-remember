"""Rule-5 kept-line carryover: no recovery leaf where both tips sit on a kept line.

An integrated leaf whose ledger shows no carryover is still refused a recovery leaf
when the code tip and the parked memory tip are each reachable from the leaf's landing
line or the repository default line; the cleanup records both tips, the lines and the
commits it found them in, and goes on. A tip on neither line keeps the refusal.
"""

import subprocess
import sys
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.worktrees.modules import cleanup as cleanup_module
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
from test_worktree_support import git, init_repo

CODE_BRANCH = "ar/fixture-leaf"
MEMORY_BRANCH = "ar/fixture-memory-leaf"
SOURCE_BRANCH = "main"


def _commit(repo: Path, name: str) -> None:
    (repo / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
    git(repo, "add", f"{name}.txt")
    git(repo, "commit", "-m", name)


def _repo_with_leaf(repo: Path, branch: str, *, merged: bool) -> None:
    init_repo(repo, branch=SOURCE_BRANCH)
    git(repo, "checkout", "-b", branch)
    _commit(repo, "leaf-work")
    git(repo, "checkout", SOURCE_BRANCH)
    if merged:
        git(repo, "merge", "--no-ff", branch, "-m", "merge leaf")


def _contract(root: Path, code_repo: Path, memory_repo: Path) -> Path:
    contract_path = root / "tasks" / "repo-a" / "leaf" / "enclosures" / "a" / "series-contract.md"
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
        code_worktree=root / "worktrees" / "repo-a" / "leaf" / "code",
        memory_repo_path=memory_repo,
        memory_source_branch=SOURCE_BRANCH,
        memory_work_branch=MEMORY_BRANCH,
        memory_worktree=root / "worktrees" / "repo-a" / "leaf" / "memory",
        ledger_path=root / "worktrees" / "repo-a" / "leaf" / "memory-ledger.md",
        integration_status="completed",  # type: ignore[arg-type]
        kind="leaf",
        leaf_id="L99",
        lifecycle_id="01TESTKEPTLINE00000000000000",
    )
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    write_contract(contract_path, contract)
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


def test_kept_line_probe_records_both_tips_lines_and_commits(tmp_path: Path) -> None:
    """Both tips reachable from the landing line: the probe records everything."""
    code_repo = tmp_path / "code"
    memory_repo = tmp_path / "memory"
    _repo_with_leaf(code_repo, CODE_BRANCH, merged=True)
    _repo_with_leaf(memory_repo, MEMORY_BRANCH, merged=True)
    contract_path = _contract(tmp_path, code_repo, memory_repo)
    kept = cleanup_module._landed_tips_on_kept_line(load_contract(contract_path))
    assert kept is not None
    assert kept["codeLine"] == SOURCE_BRANCH
    assert kept["memoryLine"] == SOURCE_BRANCH
    code_tip = kept["codeTip"]
    memory_tip = kept["memoryTip"]
    code_found_in = kept["codeFoundIn"]
    memory_found_in = kept["memoryFoundIn"]
    assert isinstance(code_tip, str)
    assert isinstance(memory_tip, str)
    assert isinstance(code_found_in, str)
    assert isinstance(memory_found_in, str)
    assert git(code_repo, "rev-parse", code_tip)
    assert git(memory_repo, "rev-parse", memory_tip)
    assert git(code_repo, "rev-parse", code_found_in)
    assert git(memory_repo, "rev-parse", memory_found_in)


def test_kept_line_probe_refuses_a_tip_on_neither_line(tmp_path: Path) -> None:
    """Memory tip on neither the landing line nor a default: no kept line."""
    code_repo = tmp_path / "code"
    memory_repo = tmp_path / "memory"
    _repo_with_leaf(code_repo, CODE_BRANCH, merged=True)
    _repo_with_leaf(memory_repo, MEMORY_BRANCH, merged=False)
    contract_path = _contract(tmp_path, code_repo, memory_repo)
    assert cleanup_module._landed_tips_on_kept_line(load_contract(contract_path)) is None


def test_cleanup_dry_goes_on_where_both_tips_are_kept(tmp_path: Path) -> None:
    """End to end, dry: the gate passes (no recovery-leaf demand) and stops at the
    locator observation; the recorded tips ride the publish payload after adoption
    (proven on real-shaped data in the scratch server run)."""
    code_repo = tmp_path / "code"
    memory_repo = tmp_path / "memory"
    _repo_with_leaf(code_repo, CODE_BRANCH, merged=True)
    _repo_with_leaf(memory_repo, MEMORY_BRANCH, merged=True)
    contract_path = _contract(tmp_path, code_repo, memory_repo)
    result = cleanup_module.cleanup_result(WorktreeArgs(contract_path=contract_path, dry_run=True))
    assert "recovery leaf" not in str(result.payload.get("summary", ""))
    assert result.payload.get("state") == "operation-location-missing"
    assert _branch_present(code_repo, CODE_BRANCH)
    assert _branch_present(memory_repo, MEMORY_BRANCH)


def test_cleanup_dry_keeps_the_recovery_leaf_refusal_otherwise(tmp_path: Path) -> None:
    """End to end: a tip on neither line keeps the verbatim refusal, nothing changes."""
    code_repo = tmp_path / "code"
    memory_repo = tmp_path / "memory"
    _repo_with_leaf(code_repo, CODE_BRANCH, merged=True)
    _repo_with_leaf(memory_repo, MEMORY_BRANCH, merged=False)
    contract_path = _contract(tmp_path, code_repo, memory_repo)
    result = cleanup_module.cleanup_result(WorktreeArgs(contract_path=contract_path, dry_run=True))
    assert result.returncode == 2
    assert result.payload.get("code") == "carryover-required"
    assert "recovery leaf" in str(result.payload.get("error", ""))
    assert _branch_present(code_repo, CODE_BRANCH)
    assert _branch_present(memory_repo, MEMORY_BRANCH)
