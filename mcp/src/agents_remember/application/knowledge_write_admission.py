"""The admission a taskless knowledge write is bound to.

The curator file writer needs to know which repository it writes for, which two roots it reads and
writes, and which exact commits it was admitted at. A leaf's write reads those facts from its
enclosure contract (``cli/knowledge_ingest.py``). A repository's first knowledge, and every taskless
curator run after it, has no enclosure, and fabricating one is refused outright by the bootstrap
handover. ``KnowledgeWriteAdmission`` is the value the taskless route is bound to instead:
:func:`~agents_remember.application.knowledge_bootstrap_admission.admit_bootstrap_context` derives
one from the repository entry an MCP settings document declares and from the real checkouts.

**Provenance is a value, never a boolean.** ``AdmissionProvenance`` names *what* admitted the write,
the exact document it was read from, and the fact that was read. There is deliberately no
``admitted=True`` parameter anywhere on this path: a caller cannot assert an admission, it can only
be handed one that a resolver built after its own checks.

**What this value is not.** It confers no authority by itself -- it is a frozen record of facts a
resolver already established -- and it decides nothing about a destination or an identity.

The database writer this module once fronted is retired with the canonical knowledge database
(MIK-R26): the adapter that derived an admission from an enclosure contract, and the refusal that
kept that writer off a converted tree, left with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

__all__ = [
    "BOOTSTRAP_ADMISSION_KIND",
    "AdmissionKind",
    "AdmissionProvenance",
    "KnowledgeWriteAdmission",
]

# The admission that exists. The spelling is the vocabulary of a report, so a reader branches on it
# rather than on prose.
AdmissionKind = Literal["repository-bootstrap"]

BOOTSTRAP_ADMISSION_KIND: AdmissionKind = "repository-bootstrap"


@dataclass(frozen=True)
class AdmissionProvenance:
    """What admitted one write, and the document that fact was read from.

    ``authority`` is the *kind* of authority (the repository entry a settings document declares),
    ``reference`` is the exact document it was read from, so a reader can re-open the source of the
    admission, and ``detail`` states the one fact that made this an admission: which allowed
    repository id.
    """

    kind: AdmissionKind
    authority: str
    reference: str
    detail: str


@dataclass(frozen=True)
class KnowledgeWriteAdmission:
    """The facts one admitted taskless knowledge write is bound to.

    * ``scope`` is the bootstrap operation's own name for the repository; the wave's records name
      it as their task.
    * ``code_worktree`` and ``memory_worktree`` are the two roots the write uses: the tree an anchor
      is resolved against and the memory tree the files are written into.
    * ``code_base_commit`` and ``memory_base_commit`` are the **exact source revisions** this write
      was admitted at, read from the real checkouts rather than asserted.
    * ``code_work_branch`` is the line the code side stood on when it could be resolved.
    * ``source_ref`` names the document this admission was read from and is used in messages only.

    ``memory_repo_path`` stays optional because a repository with no external memory layer has no
    memory repository to name; ``memory_worktree`` is the strictly stronger requirement the write
    itself refuses on.
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
