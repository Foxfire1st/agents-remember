"""Git owns the explainer's two ordinary detached worktrees (L201 F9, 04136569).

No per-file copying, mode enforcement or alternate snapshot implementation lives here.
Only provisioning/removal destinations are confined; Git checks out tracked symlinks normally.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.canvas.explainer_errors import ExplainerRefused
from agents_remember.canvas.paths import guard_path
from agents_remember.kernel.git_command import GitRunnerOptions, run_git, run_git_bytes


@dataclass(frozen=True)
class CheckoutTotals:
    """The checked-out commit and its tracked file/byte counts."""

    folder: str
    commit: str
    files: int
    bytes: int

    def as_record(self) -> dict[str, Any]:
        return {
            "folder": self.folder,
            "commit": self.commit,
            "files": self.files,
            "bytes": self.bytes,
        }


def _git(repository: Path, arguments: list[str], options: GitRunnerOptions | None = None) -> str:
    result = run_git(repository, arguments, options)
    if result.returncode:
        raise ExplainerRefused(
            "evidence-source-unavailable",
            f"Git could not prepare the explainer worktree: {result.stderr.strip()}",
        )
    return result.stdout.strip()


def _checkout_commit(repository: Path, tree: str) -> str:
    kind = _git(repository, ["cat-file", "-t", tree])
    if kind == "commit":
        return _git(repository, ["rev-parse", "--verify", tree + "^{commit}"])
    if kind != "tree":
        raise ExplainerRefused("evidence-source-unavailable", f"{tree} is not a commit or tree")
    # A tree-only comparison has no history commit. Make one unreferenced object, never a ref.
    identity = {
        "GIT_AUTHOR_NAME": "Agents Remember explainer",
        "GIT_AUTHOR_EMAIL": "explainer@agents-remember.invalid",
        "GIT_COMMITTER_NAME": "Agents Remember explainer",
        "GIT_COMMITTER_EMAIL": "explainer@agents-remember.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00 +0000",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00 +0000",
    }
    return _git(
        repository,
        ["commit-tree", tree],
        GitRunnerOptions(input_text="Bound explainer tree\n", identity=identity),
    )


def checkout_tree(repository: Path, tree: str, destination: Path, *, root: Path) -> CheckoutTotals:
    """Create or move this same detached worktree to the exact bound tree, using Git."""

    guard_path(root, destination, label="explainer worktree")
    commit = _checkout_commit(repository, tree)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Hooks are not part of a bound tree; provisioning cannot execute an ambient checkout hook.
    options = ["-c", "core.hooksPath=/dev/null", "-c", "submodule.recurse=false"]
    if destination.exists():
        guard_path(root, destination / ".git", label="worktree metadata")
        if not (destination / ".git").is_file():
            raise ExplainerRefused("workspace-invalid", f"{destination} is not a detached worktree")
        _git(destination, [*options, "checkout", "--detach", "--force", commit])
    else:
        _git(
            repository,
            [*options, "worktree", "add", "--detach", str(destination), commit],
        )
    listing = run_git_bytes(repository, ["ls-tree", "-r", "-l", "-z", commit])
    if listing.returncode:
        raise ExplainerRefused(
            "evidence-source-unavailable", listing.stderr.decode(errors="replace")
        )
    entries = [row.partition(b"\t")[0].split() for row in listing.stdout.split(b"\0") if row]
    blobs = [row for row in entries if len(row) == 4 and row[1] == b"blob"]
    return CheckoutTotals(destination.name, commit, len(blobs), sum(int(row[3]) for row in blobs))


def remove_checkout(destination: Path, *, root: Path) -> None:
    """Remove this linked worktree through Git, then prune its repository's registration."""

    guard_path(root, destination, label="explainer worktree")
    if not destination.exists():
        return
    guard_path(root, destination / ".git", label="worktree metadata")
    if not (destination / ".git").is_file():
        raise ExplainerRefused("workspace-invalid", f"{destination} is not a linked worktree")
    common = Path(_git(destination, ["rev-parse", "--path-format=absolute", "--git-common-dir"]))
    _git(common, ["worktree", "remove", "--force", str(destination)])
    _git(common, ["worktree", "prune"])


def prune_worktrees(repository: Path) -> None:
    """Prune the repository's registrations of worktrees whose folders are gone (Git's own rule).

    A workspace folder removed without Git -- by hand, or by an earlier removal that stopped part
    way -- leaves its registration in the repository; pruning drops it, and touches no worktree
    whose folder still exists (MIK-R211 v5 rule 12).
    """

    if not repository.exists():
        return
    _git(repository, ["worktree", "prune"])
