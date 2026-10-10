"""A refused terminal branch retirement changes nothing (MIK-R82 Failure And Recovery Behavior).

``cleanup`` once deleted a fully merged local branch and only then refused at the
remote deletion (the origin had the branch checked out), and both ``cleanup`` and
``abandon`` retired the code branch before the memory branch's guard refused. A
refused ``cleanup`` or ``abandon`` must change nothing: every refusal guard runs
before the first deletion.
"""

import subprocess
import sys
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.worktrees.modules.abandon import _abandon_branches
from agents_remember.worktrees.modules.cleanup import (
    _deleted_branches,
)
from agents_remember.worktrees.modules.terminal_branch_retirement import (
    _TERMINAL_MUTATION_CAPABILITY,
    _repository_key,
    _TerminalMutationAuthority,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract
from test_worktree_support import git, init_repo

CODE_BRANCH = "ar/fixture-leaf"
MEMORY_BRANCH = "ar/fixture-memory-leaf"
SOURCE_BRANCH = "main"


def _commit(repo: Path, name: str) -> None:
    (repo / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
    git(repo, "add", f"{name}.txt")
    git(repo, "commit", "-m", name)


def _origin_with_checked_out_leaf(origin: Path) -> None:
    """Non-bare origin whose leaf branch is checked out, so ``push --delete`` refuses."""
    init_repo(origin, branch=SOURCE_BRANCH)
    git(origin, "checkout", "-b", CODE_BRANCH)
    _commit(origin, "leaf-work")
    git(origin, "checkout", SOURCE_BRANCH)
    git(origin, "merge", "--no-ff", CODE_BRANCH, "-m", "merge leaf")
    git(origin, "checkout", CODE_BRANCH)
    assert git(origin, "branch", "--show-current") == CODE_BRANCH


def _clone_leaf(clone: Path, origin: Path) -> None:
    git(clone.parent, "clone", origin.as_posix(), clone.as_posix())
    git(clone, "config", "user.email", "agents-remember-tests@example.invalid")
    git(clone, "config", "user.name", "Agents Remember Tests")
    git(clone, "checkout", "-b", SOURCE_BRANCH, f"origin/{SOURCE_BRANCH}")
    # The clone checks out the origin HEAD (the leaf branch), so the local leaf
    # branch already exists tracking the origin leaf.


def _repo_with_leaf(repo: Path, branch: str, *, merged: bool) -> None:
    init_repo(repo, branch=SOURCE_BRANCH)
    git(repo, "checkout", "-b", branch)
    _commit(repo, "leaf-work")
    git(repo, "checkout", SOURCE_BRANCH)
    if merged:
        git(repo, "merge", "--no-ff", branch, "-m", "merge leaf")


def _contract(root: Path, code_repo: Path, memory_repo: Path | None) -> WorktreeContract:
    return WorktreeContract(
        task_id="task-1",
        task_name="task-one",
        repo_name="repo-a",
        workflow_kind="light-task",
        memory_mode="external" if memory_repo is not None else "internal",  # type: ignore[arg-type]
        coordination_root=root,
        task_root=root / "tasks" / "repo-a" / "leaf",
        contract_path=root
        / "tasks"
        / "repo-a"
        / "leaf"
        / "enclosures"
        / "a"
        / "series-contract.md",
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
        kind="leaf",
    )


def _authority(
    operation: str, targets: list[tuple[Path, str, str]], remotes: list[tuple[Path, str]]
) -> _TerminalMutationAuthority:
    return _TerminalMutationAuthority(
        operation=operation,  # type: ignore[arg-type]
        worktrees=frozenset(),
        memory_worktree=None,
        branches=frozenset(
            (_repository_key(repo), branch, source) for repo, branch, source in targets
        ),
        remote_branches=frozenset((_repository_key(repo), branch) for repo, branch in remotes),
        _capability=_TERMINAL_MUTATION_CAPABILITY,
    )


def _branch_present(repo: Path, branch: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def test_cleanup_refused_remote_delete_keeps_the_local_branch(tmp_path: Path) -> None:
    """The origin refuses the remote delete (branch checked out there): the local
    branch, the checkout, and the entry must all survive the refusal."""
    origin = tmp_path / "origin"
    clone = tmp_path / "clone"
    _origin_with_checked_out_leaf(origin)
    _clone_leaf(clone, origin)
    git(clone, "checkout", CODE_BRANCH)
    assert git(clone, "branch", "--show-current") == CODE_BRANCH

    contract = _contract(tmp_path, clone, None)
    authority = _authority(
        "worktree_cleanup",
        [(clone, CODE_BRANCH, SOURCE_BRANCH)],
        [(clone, CODE_BRANCH)],
    )
    branches = _deleted_branches(contract, dry_run=False, authority=authority)

    entry = branches["code"]
    assert entry.get("deleted") is not True
    assert _branch_present(clone, CODE_BRANCH)
    assert git(clone, "branch", "--show-current") == CODE_BRANCH


def test_cleanup_checks_every_branch_before_deleting_any(tmp_path: Path) -> None:
    """The memory branch is unmerged: the merged code branch must survive alongside it."""
    code_repo = tmp_path / "code"
    memory_repo = tmp_path / "memory"
    _repo_with_leaf(code_repo, CODE_BRANCH, merged=True)
    _repo_with_leaf(memory_repo, MEMORY_BRANCH, merged=False)

    contract = _contract(tmp_path, code_repo, memory_repo)
    authority = _authority(
        "worktree_cleanup",
        [
            (code_repo, CODE_BRANCH, SOURCE_BRANCH),
            (memory_repo, MEMORY_BRANCH, SOURCE_BRANCH),
        ],
        [],
    )
    branches = _deleted_branches(contract, dry_run=False, authority=authority)

    assert branches["code"].get("deleted") is not True
    assert branches["memory"].get("deleted") is not True
    assert _branch_present(code_repo, CODE_BRANCH)
    assert _branch_present(memory_repo, MEMORY_BRANCH)


def test_abandon_checks_every_branch_before_deleting_any(tmp_path: Path) -> None:
    """The memory branch is unmerged and abandon is not forced: the merged code
    branch must survive alongside it."""
    code_repo = tmp_path / "code"
    memory_repo = tmp_path / "memory"
    _repo_with_leaf(code_repo, CODE_BRANCH, merged=True)
    _repo_with_leaf(memory_repo, MEMORY_BRANCH, merged=False)

    contract = _contract(tmp_path, code_repo, memory_repo)
    authority = _authority(
        "worktree_abandon",
        [
            (code_repo, CODE_BRANCH, SOURCE_BRANCH),
            (memory_repo, MEMORY_BRANCH, SOURCE_BRANCH),
        ],
        [],
    )
    branches = _abandon_branches(contract, dry_run=False, force=False, authority=authority)

    assert branches["code"].get("deleted") is not True
    assert branches["memory"].get("deleted") is not True
    assert _branch_present(code_repo, CODE_BRANCH)
    assert _branch_present(memory_repo, MEMORY_BRANCH)
