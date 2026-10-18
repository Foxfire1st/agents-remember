"""The code tree a read observes, and the working-tree capture it reads through (MIK-R73 rule 7).

A read answers over the bytes the caller is working with. When the checkout holds uncommitted
changes those bytes belong to no commit, so the read captures the working tree with Git's own
isolated mechanism -- a scratch index seeded from the repository's, ``git add --all``, ``git
write-tree`` -- and stages the new blobs in a scratch object store. The capture is **held for the
length of the read**: its store lives exactly as long as the reader needs it and is removed
afterwards, so a captured blob never enters the repository. Immutability is a property of the
mechanism rather than a promise: the repository's index, refs, object store and worktree are never
opened for writing, and a second capture of an unchanged worktree returns the same tree id.

A read states which of the two it observed. A clean checkout answers :data:`CODE_STATE_COMMIT`; a
checkout whose working tree differs answers :data:`CODE_STATE_WORKING_TREE`, and its tree is the
captured one. A repository that cannot be read at all answers nothing here, and its caller states
``unmeasured``.

A continuation of a working-tree walk captures the tree again, because no store outlives the page
that made it. An unchanged worktree yields the same tree id and the walk goes on; a changed one
refuses the page, so the rest of a stale anchor set is never served.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.memory.knowledge_index.tree import MemoryTreeError, capture_tree_into

__all__ = [
    "CODE_STATE_COMMIT",
    "CODE_STATE_WORKING_TREE",
    "WORKING_TREE_SELECTOR",
    "CodeState",
    "ObservedCodeTree",
    "WorktreeCapture",
    "WorktreeCaptureError",
    "capture_worktree",
    "head_tree_id",
    "hold_observed_code_tree",
    "observed_code_tree",
    "worktree_is_clean",
]

CodeState = Literal["commit", "working-tree"]

# The selector a caller names to read the checkout rather than a commit (MIK-R73 rule 7).
WORKING_TREE_SELECTOR: Final = "working-tree"
CODE_STATE_COMMIT: Final = "commit"
CODE_STATE_WORKING_TREE: Final = "working-tree"


class WorktreeCaptureError(ValueError):
    """The working tree could not be captured, or its state could not be read."""


@dataclass(frozen=True)
class WorktreeCapture:
    """A captured working tree, held open by the reader that captured it."""

    repository: Path
    tree_id: str
    object_directory: Path
    """The scratch store the captured blobs are staged in: read-only input to the observer."""

    def close(self) -> None:
        """Remove the scratch index and store: the capture is over."""

        shutil.rmtree(self.object_directory.parent, ignore_errors=True)


@dataclass(frozen=True)
class ObservedCodeTree:
    """The code tree one read observes, and the state it observed it in."""

    repository: Path
    tree_id: str
    code_state: CodeState
    object_directory: Path | None = None
    """The held capture's store, or ``None`` when the tree is the commit's own."""

    def to_document(self) -> dict[str, str]:
        return {
            "repositoryRoot": self.repository.as_posix(),
            "treeId": self.tree_id,
            "codeState": self.code_state,
        }


def head_tree_id(repository: Path) -> str | None:
    """The tree of ``HEAD``, or ``None`` for a repository with no commit yet."""

    try:
        result = run_git(
            repository,
            ["rev-parse", "--verify", "--quiet", "--end-of-options", "HEAD^{tree}"],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
    except (OSError, subprocess.SubprocessError):
        # A root that is not a readable checkout has no HEAD tree; the caller states "unmeasured".
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def worktree_is_clean(repository: Path) -> bool:
    """Whether the checkout differs from ``HEAD`` in any tracked or untracked path.

    Untracked files count: a read over the working tree answers at the captured tree, and a file the
    commit does not hold is still a byte the caller is working with. Submodule changes count too,
    because ``git status`` reports them and the capture records the gitlink.
    """

    result = run_git(
        repository,
        ["status", "--porcelain", "-z", "--untracked-files=normal", "--ignore-submodules=none"],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git exited {result.returncode}"
        raise WorktreeCaptureError(f"the checkout state of {repository} cannot be read: {detail}")
    return not result.stdout


def capture_worktree(repository: Path) -> WorktreeCapture:
    """Capture the working tree into scratch storage and keep it until the caller closes it."""

    scratch = Path(tempfile.mkdtemp(prefix="ar-worktree-capture-"))
    try:
        tree_id, _entries = capture_tree_into(repository, scratch)
    except (MemoryTreeError, OSError) as error:
        shutil.rmtree(scratch, ignore_errors=True)
        raise WorktreeCaptureError(
            f"the working tree of {repository} cannot be captured: {error}"
        ) from error
    return WorktreeCapture(repository.resolve(), tree_id, scratch / "objects")


def hold_observed_code_tree(
    repository: Path,
) -> tuple[ObservedCodeTree | None, WorktreeCapture | None]:
    """Observe the code tree a read must answer at, returning any capture for the caller to close.

    A caller that reads over several steps -- a reader that opens a selection and projects pages
    from it -- holds the capture until its last page is written; a caller that answers in one step
    uses :func:`observed_code_tree`, which closes it.
    """

    root = repository.resolve()
    head = head_tree_id(root)
    if head is None:
        return None, None
    if worktree_is_clean(root):
        return ObservedCodeTree(root, head, CODE_STATE_COMMIT), None
    capture = capture_worktree(root)
    state: CodeState = CODE_STATE_COMMIT if capture.tree_id == head else CODE_STATE_WORKING_TREE
    return (
        ObservedCodeTree(capture.repository, capture.tree_id, state, capture.object_directory),
        capture,
    )


@contextmanager
def observed_code_tree(repository: Path) -> Iterator[ObservedCodeTree | None]:
    """Observe the code tree a read must answer at, holding any capture until the read ends."""

    observed, capture = hold_observed_code_tree(repository)
    try:
        yield observed
    finally:
        if capture is not None:
            capture.close()
