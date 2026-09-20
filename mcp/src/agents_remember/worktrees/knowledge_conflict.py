"""Settle a knowledge-dataset conflict inside the sync transaction, through the merge adapter.

CYCLE-02. A knowledge database is binary to Git: an ordinary merge can only declare the whole file
conflicted, and no amount of staging resolves it. Before this module the transaction handed that file
to the agent -- ``sync-resolution-required`` with ``resolutionOwner: agent`` -- and the union was only
obtainable by calling ``resolve_knowledge_merge_base`` and ``merge_resolved_knowledge_datasets`` by
hand, which is not a composition seam an agent should have to discover.
``application/knowledge_merge.py`` says of itself that it is "deliberately callable rather than wired"
and that a separately reviewed change turns it into a driver. This is the wiring half: the transaction
routes the three-way merge, and the adapter still decides it.

**This module owns exactly the Git half.** Materialising the three index stages and staging the
settled file are worktree facts; reading a dataset's identity, proving the common base and performing
the merge are application facts. The split is forced by the layer contract -- a module under
``worktrees`` may not import the memory domain at all
(``test_knowledge_store.py::test_lower_ranked_owners_do_not_import_the_memory_domain``), and the
adapter's own docstring says a lower owner "receives models values and never an import of this module
or of the store" -- so the dataset work lives behind ``merge_conflicted_stages`` and this module hands
it three paths and receives one boolean.

Three properties are load-bearing.

**Binary safety.** The three datasets are Git *index stages*, and ``kernel.git_command.run_git``
returns ``CompletedProcess[str]`` -- text. Reading a stage with ``git show :1:<path>`` would decode a
SQLite file through a text layer and corrupt it before the adapter ever saw it, which would surface as
a row-count mismatch rather than as corruption. The stages are therefore materialised with
``git checkout-index --stage=<n>``, where Git writes the bytes itself.

**Refusal is preserved, not swallowed.** Only a genuine knowledge dataset settles. A path the adapter
will not decide -- a schema disagreement above all -- stays conflicted and remains the agent's to
resolve, so this narrows the agent's work rather than hiding any of it.

**No compatibility verdict.** A structurally merged dataset says nothing about whether the combined
knowledge is correct, and nothing here may treat it as approval.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from agents_remember.application.knowledge_merge import ConflictCommits, merge_conflicted_stages
from agents_remember.kernel.git_command import run_git
from agents_remember.models.knowledge.merge import MergeInputRole

__all__ = ["settle_knowledge_conflicts"]

# The three positions a conflicted path occupies in the index, and the stage number Git gives each.
# ``base`` is the merge base, ``left`` is the side being merged into (ours), ``right`` is the side
# arriving (theirs) -- the same roles the adapter's request names, so nothing is translated twice.
_STAGE_ROLES: tuple[tuple[MergeInputRole, int], ...] = (("base", 1), ("left", 2), ("right", 3))


@dataclass(frozen=True)
class _Settlement:
    """One conflicted path's materialised stages, ready for the application layer."""

    target: Path
    stages: dict[MergeInputRole, Path]
    base_commit: str


def _materialise_stages(path: str, into: Path, worktree: Path) -> dict[MergeInputRole, Path] | None:
    """Write the three index stages for ``path`` under ``into``, byte-for-byte, or ``None``.

    ``git checkout-index --stage=<n> --prefix=<dir>/`` is used rather than ``git show`` precisely
    because Git writes the bytes directly: the file never passes through a text decode, so a SQLite
    database arrives intact. Each stage gets its own prefix directory so the three copies cannot
    overwrite one another.
    """

    staged: dict[MergeInputRole, Path] = {}
    for role, stage in _STAGE_ROLES:
        prefix = into / role
        prefix.mkdir(parents=True, exist_ok=True)
        result = run_git(
            worktree, ["checkout-index", f"--stage={stage}", f"--prefix={prefix}/", "--", path]
        )
        written = prefix / path
        if result.returncode != 0 or not written.is_file():
            return None
        staged[role] = written
    return staged


def _common_base(worktree: Path, left: str, right: str) -> str | None:
    """The unique common base commit, or ``None`` when Git cannot name exactly one.

    A base Git cannot name uniquely is the case the adapter refuses rather than choosing between
    candidates, so an unnameable base is reported as "not settled" here and never guessed at.
    """

    found = run_git(worktree, ["merge-base", left, right])
    commit = found.stdout.strip()
    if found.returncode != 0 or not commit:
        return None
    return commit


def _stage(worktree: Path, path: str, left: str, right: str) -> _Settlement | None:
    """Everything the application layer needs for one conflicted path, or ``None``.

    Every ``None`` here is a different reason the agent keeps the conflict: the file is gone, Git
    cannot name one common base, or a stage will not materialise.
    """

    target = worktree / path
    if not target.is_file():
        return None
    base_commit = _common_base(worktree, left, right)
    if base_commit is None:
        return None
    stages = _materialise_stages(path, Path(tempfile.mkdtemp(prefix="ar-merge-stages-")), worktree)
    if stages is None:
        return None
    return _Settlement(target=target, stages=stages, base_commit=base_commit)


def _settle_one(worktree: Path, path: str, left: str, right: str) -> bool:
    """Route one conflicted knowledge dataset through the adapter, publishing into the worktree.

    Returns whether the path was settled. Anything the adapter will not decide returns ``False``, so
    the caller leaves the path conflicted and the agent keeps ownership of it.
    """

    settlement = _stage(worktree, path, left, right)
    if settlement is None:
        return False
    settled = merge_conflicted_stages(
        destination=settlement.target,
        stages=settlement.stages,
        repository_root=worktree,
        commits=ConflictCommits(base=settlement.base_commit, left=left, right=right),
    )
    if not settled:
        return False
    return run_git(worktree, ["add", "--", path]).returncode == 0


def settle_knowledge_conflicts(
    worktree: Path, conflicts: tuple[str, ...], left: str, right: str
) -> tuple[str, ...]:
    """Settle every conflicted path that is a knowledge dataset; return the ones still unresolved.

    The return value is the contract with the caller: whatever this cannot settle is exactly what the
    agent is still asked to resolve, so a refusal here narrows the agent's work rather than hiding it.
    """

    remaining: list[str] = []
    for path in conflicts:
        if not _settle_one(worktree, path, left, right):
            remaining.append(path)
    return tuple(remaining)
