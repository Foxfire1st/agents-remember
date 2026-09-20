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
it three paths and receives one typed settlement.

Three properties are load-bearing.

**Binary safety.** The three datasets are Git *index stages*, and ``kernel.git_command.run_git``
returns ``CompletedProcess[str]`` -- text. Reading a stage with ``git show :1:<path>`` would decode a
SQLite file through a text layer and corrupt it before the adapter ever saw it, which would surface as
a row-count mismatch rather than as corruption. The stages are therefore materialised with
``git checkout-index --stage=<n>``, where Git writes the bytes itself.

**Refusal is preserved, not swallowed.** Only a genuine knowledge dataset settles. A path the adapter
will not decide -- a schema disagreement above all -- stays conflicted and remains the agent's to
resolve, so this narrows the agent's work rather than hiding any of it. What changed with CYCLE-02's
remainder is *what the caller receives*: the adapter's own explanation (the refusal, and the conflict
naming the table, the operation and the exact row) travels out of here with the path instead of being
reduced to a boolean, because the agent that has to reconcile the row is the one that needs it.

**No compatibility verdict.** A structurally merged dataset says nothing about whether the combined
knowledge is correct, and nothing here may treat it as approval. An *authored* reconciliation is the
caller's own decision about one conflict and stays exactly that: this module passes it through, names
it nowhere, and never invents one.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from agents_remember.application.knowledge_merge import ConflictCommits, merge_conflicted_stages
from agents_remember.kernel.git_command import run_git
from agents_remember.models.knowledge.merge import (
    AuthoredDecision,
    AuthoredReconciliation,
    MergeConflict,
    MergeInputRole,
    expressible_decisions,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal

__all__ = [
    "KnowledgeConflictSettlement",
    "RefusedKnowledgeStage",
    "settle_knowledge_conflict",
    "settle_knowledge_conflicts",
]

# The three positions a conflicted path occupies in the index, and the stage number Git gives each.
# ``base`` is the merge base, ``left`` is the side being merged into (ours), ``right`` is the side
# arriving (theirs) -- the same roles the adapter's request names, so nothing is translated twice.
_STAGE_ROLES: tuple[tuple[MergeInputRole, int], ...] = (("base", 1), ("left", 2), ("right", 3))


@dataclass(frozen=True)
class RefusedKnowledgeStage:
    """One conflicted path the adapter would not settle, with the engine's own reason.

    ``conflict`` is the engine's attribution -- table, operation and the exact row identity it
    refused -- and ``refusal`` is the typed explanation with the action it advertises. Both are
    carried verbatim: a caller that re-rendered them would be reimplementing the diagnosis this
    exists to preserve. ``detail`` is this layer's own reason for a path that never reached the
    adapter (no common base, a stage that would not materialise), and it is empty whenever the
    engine answered.
    """

    path: str
    conflict: MergeConflict | None = None
    refusal: KnowledgeRefusal | None = None
    detail: str = ""

    @property
    def decisions(self) -> tuple[AuthoredDecision, ...]:
        """The authored decisions this refusal admits; empty when nothing can be reconciled.

        Read from the conflict the engine attributed, so the agent is offered exactly the decisions
        that conflict admits and never one the engine would refuse to apply.
        """

        return expressible_decisions(self.conflict)


@dataclass(frozen=True)
class KnowledgeConflictSettlement:
    """What one automatic pass over the conflicted paths settled, and what it could not.

    The return value is the contract with the caller: whatever this cannot settle is exactly what
    the agent is still asked to resolve, so a refusal here narrows the agent's work rather than
    hiding it -- and ``guidance`` is the explanation to hand that agent.
    """

    remaining: tuple[str, ...]
    refused: tuple[RefusedKnowledgeStage, ...] = ()

    @property
    def guidance(self) -> RefusedKnowledgeStage | None:
        """The refusal whose explanation belongs in the public response, when there is one.

        A refusal that carries the engine's attribution or its typed explanation is preferred over
        one that carries only this layer's own reason: the first names a row an agent can reconcile,
        the second says the path never became a dataset. When several paths refused, the first of the
        preferred kind is the one reported; the remaining paths are still named in ``remaining``.
        """

        structured = next((item for item in self.refused if item.conflict or item.refusal), None)
        if structured is not None:
            return structured
        return self.refused[0] if self.refused else None


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


def _stage(worktree: Path, path: str, left: str, right: str) -> _Settlement | RefusedKnowledgeStage:
    """Everything the application layer needs for one conflicted path, or why there is none.

    Every return here is a different reason the agent keeps the conflict: the file is gone, Git
    cannot name one common base, or a stage will not materialise. The reason is reported rather than
    collapsed, because "nothing settled" is not something an agent can act on.
    """

    target = worktree / path
    if not target.is_file():
        return RefusedKnowledgeStage(
            path=path, detail="the conflicted path is not a file in the merge worktree"
        )
    base_commit = _common_base(worktree, left, right)
    if base_commit is None:
        return RefusedKnowledgeStage(
            path=path, detail="Git cannot name one common base commit for the retained merge"
        )
    stages = _materialise_stages(path, Path(tempfile.mkdtemp(prefix="ar-merge-stages-")), worktree)
    if stages is None:
        return RefusedKnowledgeStage(
            path=path, detail="the three index stages could not be materialised byte-for-byte"
        )
    return _Settlement(target=target, stages=stages, base_commit=base_commit)


def settle_knowledge_conflict(
    worktree: Path,
    path: str,
    left: str,
    right: str,
    reconciliation: AuthoredReconciliation | None = None,
) -> RefusedKnowledgeStage | None:
    """Route one conflicted knowledge dataset through the adapter, publishing into the worktree.

    Returns ``None`` when the path settled *and* was staged; anything else returns the reason, with
    the engine's own explanation whenever the engine was the one that answered. ``reconciliation``
    is the caller's authored decision for exactly one conflict, and it is the only way an authored
    resolution reaches the merge: without it this is the automatic pass, with it this is the retry
    the refusal advertised.
    """

    settlement = _stage(worktree, path, left, right)
    if isinstance(settlement, RefusedKnowledgeStage):
        return settlement
    outcome = merge_conflicted_stages(
        destination=settlement.target,
        stages=settlement.stages,
        repository_root=worktree,
        commits=ConflictCommits(base=settlement.base_commit, left=left, right=right),
        reconciliation=reconciliation,
    )
    if not outcome.settled:
        return RefusedKnowledgeStage(
            path=path,
            conflict=outcome.conflict,
            refusal=outcome.refusal,
            detail=outcome.detail,
        )
    if run_git(worktree, ["add", "--", path]).returncode != 0:
        return RefusedKnowledgeStage(
            path=path, detail="the settled dataset could not be staged for the merge commit"
        )
    return None


def settle_knowledge_conflicts(
    worktree: Path, conflicts: tuple[str, ...], left: str, right: str
) -> KnowledgeConflictSettlement:
    """Settle every conflicted path that is a knowledge dataset; report the ones still unresolved.

    The return value is the contract with the caller: whatever this cannot settle is exactly what the
    agent is still asked to resolve, together with the reason it could not be settled.
    """

    refused = tuple(
        item
        for path in conflicts
        if (item := settle_knowledge_conflict(worktree, path, left, right)) is not None
    )
    return KnowledgeConflictSettlement(
        remaining=tuple(item.path for item in refused), refused=refused
    )
