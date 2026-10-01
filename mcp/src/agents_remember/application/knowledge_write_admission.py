"""The admission one knowledge write is bound to, and the adapter that derives one from an enclosure.

The curator's ingest operation resolves every tree, identity, retry scope and root it uses from a
document it was handed, and until now that document could only be a **leaf enclosure contract**
(``WorktreeContract``). The consequence is the gap this module closes: the operation's own input
shape said a repository's knowledge can only be written by a task that already has a worktree, so a
repository's *first* knowledge -- and every taskless curator run after it -- had to either fabricate
an enclosure or not run at all. Fabricating one is refused outright by the bootstrap handover
(``BOOTSTRAP-HANDOVER.md`` line 11), and the remaining option was a second write path, which is the
parallel-store defect the preservation boundaries name.

So the operation is bound to this value instead of to a document. ``KnowledgeWriteAdmission`` is
exactly the facts the operation reads -- which repository, which two roots, which exact commits,
which line, which retry scope -- and nothing else, so a second *real* admission can be built for a
context that has no enclosure without the operation learning a second vocabulary or growing a
second branch.

**Two admissions, one implementation.** :func:`enclosure_admission` derives one from a contract and
:func:`~agents_remember.application.knowledge_bootstrap_admission.admit_bootstrap_context` derives
one from the repository and setup authority. Neither is a wrapper around the other and neither
duplicates a rule: the operation downstream of this value is identical for both, which is what makes
"reuse the namespace, identity allocation, candidate, batch, snapshot and publication owners" a fact
about the call graph rather than a claim in a report.

**Provenance is a value, never a boolean.** ``AdmissionProvenance`` names *what* admitted the write
(an enclosure contract that already exists, or the repository entry an MCP settings document
declares), the exact document it was read from, and the fact that was read. There is deliberately no
``admitted=True`` parameter anywhere on this path: a caller cannot assert an admission, it can only
be handed one that a resolver built after its own checks, and a reader of a stored record or a report
can tell which authority was behind a write by reading the value rather than by trusting the caller.

**What this value is not.** It confers no authority by itself -- it is a frozen record of facts a
resolver already established -- and it decides nothing about a destination, a namespace or an
identity: those stay with the operation and with the shipped owners it calls.

**The frozen database (MIK-R37 rule 3).** :func:`as_write_admission` is the database writer's front
door, so it refuses an admission whose memory worktree is converted (it holds the layout marker),
naming the curator file writer: from the cutover on, that tree's knowledge is text, and its
``knowledge.sqlite`` stays in place, unwritten, until MIK-R26 removes it. An unconverted tree in a
memory repository that holds converted memory is refused too, by the cutover lock (MIK-R09 rule 6),
which names the crossing sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.memory.knowledge.publication import FILE_WRITER_ROUTE
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH
from agents_remember.worktrees.cutover_lock import cutover_lock_refusal
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract

__all__ = [
    "BOOTSTRAP_ADMISSION_KIND",
    "ENCLOSURE_ADMISSION_KIND",
    "AdmissionKind",
    "AdmissionProvenance",
    "KnowledgeDatabaseFrozen",
    "KnowledgeWriteAdmission",
    "as_write_admission",
    "enclosure_admission",
]

# The two admissions that exist. The spelling is the stored vocabulary of a report or a retained
# progress record, so a reader branches on it rather than on prose.
AdmissionKind = Literal["leaf-enclosure", "repository-bootstrap"]

ENCLOSURE_ADMISSION_KIND: AdmissionKind = "leaf-enclosure"
BOOTSTRAP_ADMISSION_KIND: AdmissionKind = "repository-bootstrap"


@dataclass(frozen=True)
class AdmissionProvenance:
    """What admitted one write, and the document that fact was read from.

    ``authority`` is the *kind* of authority -- the enclosure document a task was created under, or
    the repository entry a settings document declares -- and ``reference`` is the exact document it
    was read from, so a reader can re-open the source of the admission. ``detail`` states the one
    fact that made this an admission: which enclosure, or which allowed repository id.
    """

    kind: AdmissionKind
    authority: str
    reference: str
    detail: str


@dataclass(frozen=True)
class KnowledgeWriteAdmission:
    """The facts one admitted knowledge write is bound to.

    The field set is exactly what the ingest operation reads, renamed to say what each one is in
    *any* admission rather than only inside a leaf enclosure:

    * ``scope`` is the **retry scope** -- the operation identity a repeat of this write is found by,
      joined with the entry's own id in the candidate's allocation journal. Inside an enclosure it is
      the leaf's own identity; for a repository bootstrap it is the bootstrap operation's own name
      for the repository. It is what makes "an exact retry does not duplicate knowledge" true without
      the retry key ever becoming an identity input.
    * ``code_worktree`` and ``memory_worktree`` are the two roots the citation machinery admits: the
      tree a citation is resolved against and the memory line a citation may name.
    * ``code_base_commit`` and ``memory_base_commit`` are the **exact source revisions** this write
      was admitted at, read from the real checkouts rather than asserted. ``code_base_commit`` is
      also what the snapshot and candidate references name, so a run that observes a different code
      revision is a different generation rather than a silent continuation.
    * ``code_work_branch`` is the line the code side is read at when it can be resolved; the
      operation falls back to the recorded commit and says so in its report when it cannot.
    * ``source_ref`` names the document this admission was read from and is used in messages only:
      one admission is a contract on disk and the other is a repository entry in a settings
      document, and a message that called both of them "the contract" would be false about one.

    ``memory_repo_path`` stays optional because a repository with no external memory layer has no
    memory repository to name; ``memory_worktree`` is the strictly stronger requirement the
    operation itself refuses on, exactly as it did when it read a contract.
    """

    provenance: AdmissionProvenance
    scope: str
    repository_name: str
    coordination_root: Path
    code_repo_path: Path
    code_worktree: Path
    memory_repo_path: Path | None
    memory_worktree: Path | None
    code_base_commit: str
    memory_base_commit: str
    code_work_branch: str
    source_ref: Path

    @property
    def kind(self) -> AdmissionKind:
        """Which authority admitted this write: an enclosure, or a repository bootstrap."""

        return self.provenance.kind

    @property
    def is_bootstrap(self) -> bool:
        """Whether this admission was derived without an enclosure in scope."""

        return self.provenance.kind == BOOTSTRAP_ADMISSION_KIND


def enclosure_admission(contract: WorktreeContract) -> KnowledgeWriteAdmission:
    """The admission one existing leaf enclosure contract confers.

    This is the thin adapter that keeps every enclosure caller working through the same value: it
    reads the cells the operation needs and states, in the provenance, that a real contract is what
    admitted the write. It derives nothing -- no commit, no tree, no identity is computed here; the
    contract already recorded them when the enclosure was cut.
    """

    scope = contract.leaf_id or contract.task_name
    return KnowledgeWriteAdmission(
        provenance=AdmissionProvenance(
            kind=ENCLOSURE_ADMISSION_KIND,
            authority="the leaf enclosure contract this run was handed",
            reference=str(contract.contract_path),
            detail=(
                f"enclosure {scope!r} of repository {contract.repo_name!r}, recorded at code base "
                f"{contract.code_base_commit}"
            ),
        ),
        scope=scope,
        repository_name=contract.repo_name,
        coordination_root=contract.coordination_root,
        code_repo_path=contract.code_repo_path,
        code_worktree=contract.code_worktree,
        memory_repo_path=contract.memory_repo_path,
        memory_worktree=contract.memory_worktree,
        code_base_commit=contract.code_base_commit,
        memory_base_commit=contract.memory_base_commit,
        code_work_branch=contract.code_work_branch,
        source_ref=contract.contract_path,
    )


def as_write_admission(
    target: KnowledgeWriteAdmission | WorktreeContract | str | Path,
) -> KnowledgeWriteAdmission:
    """The one coercion the operation's front door uses: an admission, or a contract to adapt.

    A caller that already holds an admission (the bootstrap resolver, or a caller that resolved one
    for itself) is passed through untouched -- its provenance is the fact being carried, and
    re-deriving it would replace a real authority with a guess. A path is loaded through the shipped
    contract loader, whose own refusals are the caller's; a contract object is adapted by the single
    factory above.
    """

    if isinstance(target, KnowledgeWriteAdmission):
        admission = target
    elif isinstance(target, WorktreeContract):
        admission = enclosure_admission(target)
    else:
        admission = enclosure_admission(load_contract(Path(target)))
    _require_unfrozen_database(admission)
    return admission


class KnowledgeDatabaseFrozen(ValueError):
    """The admitted memory tree is converted, so its database is frozen (MIK-R37 rule 3)."""


def _require_unfrozen_database(admission: KnowledgeWriteAdmission) -> None:
    """Refuse the database writer on a converted memory tree, naming the file writer."""

    memory = admission.memory_worktree
    if memory is None:
        return
    if (memory / LAYOUT_MARKER_PATH).is_file():
        raise KnowledgeDatabaseFrozen(
            f"the memory tree {memory} is converted (it holds {LAYOUT_MARKER_PATH}), so its "
            "knowledge database is frozen at the cutover (MIK-R37 rule 3) and the database writer "
            f"writes nothing for {admission.source_ref}; {FILE_WRITER_ROUTE}"
        )
    locked = cutover_lock_refusal(
        admission.memory_repo_path or memory,
        operation="the knowledge database writer",
        line=memory.as_posix(),
    )
    if locked is not None:
        raise KnowledgeDatabaseFrozen(locked)
