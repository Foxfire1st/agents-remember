"""Task-document wire vocabulary shared with response models.

``StepStatus`` / ``DocStatus`` and the terminal-readiness blocker moved here
from the tasks package so response models can import them without reaching up.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.task_intent import TaskIntentState

# Step/substep status carries the dashboard's granularity; the markdown render
# only has a binary checkbox, so the richer state lives in the JSON.
StepStatus = Literal["pending", "inProgress", "blocked", "done"]
# Document status stays in the ``w-02-light-task-workflow`` template vocabulary so
# the rendered ``**Status:**`` line is always a valid template value.
# ``abandoned`` is a second *terminal* value, and it records a decision rather than a failure.
# On a master document it means nothing of that master integrated, so its work was deliberately
# not taken; on a master row it means that leaf's work was not taken while the master still
# completed. It carries no reason of its own: the reason belongs to the declaring operation's
# audit trail (``skip_step``, ``remove_subtask`` with a disposition, or the decision log).
DocStatus = Literal["planning", "inProgress", "Completed", "abandoned"]
# A commanded master's closed execution contract. This is shared by persisted task documents and
# the served projection so generated clients receive the same finite vocabulary.
MasterExecutionNature = Literal["organizational", "atomic"]

CompletionUnitStatus = StepStatus | DocStatus

# A master row is resolved once it reaches a terminal state by either route: its work landed and
# finished (``Completed``), or its work was deliberately not taken (``abandoned``). Both are
# decisions, so neither is outstanding work and neither may hold its master open. It lives here
# beside the vocabulary so the task, worktree, and observer planes share one definition instead of
# each spelling the set out and drifting.
RESOLVED_MASTER_ROW_STATUSES: frozenset[str] = frozenset({"Completed", "abandoned"})


class CompletionBlocker(BaseModel):
    """One exact declared work unit that prevents terminal completion."""

    model_config = ConfigDict(extra="forbid")

    id: str
    parentId: str | None = None
    title: str
    status: CompletionUnitStatus


Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class CanonicalTaskObservation(BaseModel):
    """Exact output from the task source, R01 topology, and R02 intent owners."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    taskRoot: str
    taskDocumentRef: TaskDocumentRef
    sourceDigest: Sha256
    sourceAuthorityNamespace: Literal[
        "agents-remember.closeout-door-generation",
        "agents-remember.task-document-source",
    ]
    sourceValidatorVersion: str = Field(min_length=1)
    semanticTopologySchema: Literal["semantic-topology/v2"] = "semantic-topology/v2"
    semanticTopologyDigest: Sha256 | None
    taskIntent: TaskIntentState | None
    owner: Literal["agents-remember.task-domain"] = "agents-remember.task-domain"
    ownerVersion: Literal["task-scope-observation/v1"] = "task-scope-observation/v1"

    @field_validator("taskRoot")
    @classmethod
    def _absolute_task_root(cls, value: str) -> str:
        path = Path(value)
        if not path.is_absolute() or path.as_posix() != value or ".." in path.parts:
            raise ValueError("task root must be one normalized absolute POSIX path")
        return value
