"""Response model for the ``task_doc`` authoring tool.

An AR-owned, operation-bearing response (a strict ``ToolResponse``): it echoes
the document's identity and progress after the operation. The task document
itself (``tasks.TaskDocument``) is the persisted contract and is deliberately
**not** returned here.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_remember.models.base import ToolResponse
from agents_remember.models.closeout_projection import TaskDocProjectionEffect
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.worktree import WorktreeCommandResponse


class TaskDocMasterSync(BaseModel):
    """Optional master-row sync result for a leaf task_doc write."""

    model_config = ConfigDict(extra="forbid")

    status: str
    masterDocPath: str
    renderedPath: str
    subtaskNumber: str | None = None
    rendered: str | None = None
    diff: str | None = None
    wouldLose: bool = False


class TaskDocDiscardSourceProof(BaseModel):
    """Exact accepted child-source state echoed from the discard audit."""

    model_config = ConfigDict(extra="forbid")

    state: Literal["missing", "present"]
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    size: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _proof_matches_state(self) -> Self:
        carries_any_bytes = self.sha256 is not None or self.size is not None
        carries_complete_bytes = self.sha256 is not None and self.size is not None
        if (self.state == "present" and not carries_complete_bytes) or (
            self.state == "missing" and carries_any_bytes
        ):
            raise ValueError("discard source proof state must match complete digest and size")
        return self


class TaskDocDiscardProof(BaseModel):
    """Compact persisted absence proof echoed from the parent discard audit."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["task-unstarted-evidence/v1"]
    taskDocumentRef: TaskDocumentRef
    taskState: Literal["planning-unstarted"]
    enclosureState: Literal["absent"]
    locatorState: Literal["absent"]
    doorState: Literal["absent"]
    operationState: Literal["absent"]
    seatState: Literal["absent"]
    reviewState: Literal["absent"]
    commitState: Literal["absent"]
    childJson: TaskDocDiscardSourceProof
    childMarkdown: TaskDocDiscardSourceProof
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class TaskDocDiscardAudit(BaseModel):
    """Bounded typed parent-owned audit returned by discard-unstarted."""

    model_config = ConfigDict(extra="forbid")

    number: str = Field(min_length=1, max_length=512)
    name: str = Field(min_length=1, max_length=1024)
    file: str = Field(min_length=1, max_length=4096)
    scope: str = Field(default="", max_length=4096)
    disposition: Literal["discard-unstarted"]
    reason: str = Field(min_length=1, max_length=4096)
    discardedAt: str = Field(min_length=1, max_length=128)
    proof: TaskDocDiscardProof


class TaskDocDiscardFact(BaseModel):
    """One canonical census observation behind a discard decision."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=128)
    state: str = Field(min_length=1, max_length=128)
    address: str | None = Field(default=None, max_length=4096)
    detail: str | None = Field(default=None, max_length=4096)


class TaskDocDiscardEvidence(BaseModel):
    """Bounded public census and routed next action for discard-unstarted."""

    model_config = ConfigDict(extra="forbid")

    state: Literal["unstarted", "started", "ambiguous"]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    taskDocumentRef: TaskDocumentRef
    facts: list[TaskDocDiscardFact] = Field(max_length=32)
    nextAction: str | None = Field(default=None, max_length=256)
    nextTool: str | None = Field(default=None, max_length=256)
    nextArgs: dict[str, Any] | None = Field(default=None, max_length=32)


class TaskDocResponse(ToolResponse):
    """``task_doc``: the document's identity, status, and progress after the op."""

    taskId: str
    slug: str
    kind: str
    status: str
    lifecycleId: str | None = None
    docPath: str
    renderedPath: str
    stepsDone: int = 0
    stepsTotal: int = 0
    # dry-run / preview (R5): set only when dry_run=True; a real op leaves these at their defaults.
    dryRun: bool = False
    rendered: str | None = None
    diff: str | None = None
    wouldLose: bool = False
    masterSync: TaskDocMasterSync | None = None
    # remove_subtask outcome (260703-L18 finding 1, closes friction F-N): the op removes the master
    # row and (unless keep_file) deletes the leaf doc, then echoes what it did. Without these fields
    # the extra=forbid envelope REJECTED the real payload, surfacing a tool error after a destructive
    # success -- a caller who believes the error could retry the (already-done) removal. Present only
    # on remove_subtask (real op: removedSubtask + deletedFiles; dry-run: removedSubtask +
    # wouldDeleteFiles); every other operation leaves them None (excluded by exclude_none).
    removedSubtask: str | None = None
    deletedFiles: list[str] | None = Field(default=None, max_length=16)
    wouldDeleteFiles: list[str] | None = Field(default=None, max_length=16)
    # Explicit planning discard is distinct from terminal remove. These fields make preview,
    # refusal, applied success, and lost-response convergence machine-readable without treating
    # the queue as lifecycle history.
    discardState: (
        Literal[
            "would-discard",
            "discarded",
            "already-discarded",
            "refused-started",
            "refused-ambiguous",
        ]
        | None
    ) = None
    alreadyDiscarded: bool | None = None
    discardAudit: TaskDocDiscardAudit | None = None
    discardEvidence: TaskDocDiscardEvidence | None = None
    detail: str | None = None
    nextAction: str | None = None
    nextTool: str | None = None
    nextArgs: dict[str, Any] | None = Field(default=None, max_length=32)
    # Sprint-linkage and execution-graph authoring surfaces (260815-DAG L14/L13). The special
    # ops (attach_master, detach_master, linkage_report, author_execution_graph) return raw
    # operation payloads that carry these keys; without declaration the extra=forbid envelope
    # REJECTED the real payloads after their writes, exactly the remove_subtask bug class
    # above. Present only on those ops; every other operation leaves them None (excluded by
    # exclude_none).
    # attach_master: the sprint row and graph node the master gained.
    subtaskNumber: str | None = None
    state: str | None = None
    sprintTaskDocumentRef: dict[str, Any] | None = None
    masterRef: dict[str, Any] | None = None
    graphNode: str | None = None
    executionNatureAsserted: bool | None = None
    documents: list[dict[str, Any]] | None = None
    # detach_master: what the detachment removed and whether the master doc still resolved.
    removedOrchestrates: list[str] | None = None
    removedGraphNodes: int | None = None
    masterResolved: bool | None = None
    # linkage_report + get on a sprint: the read-only drift facts.
    linkageFacts: list[dict[str, Any]] | None = None
    # author_execution_graph: what the batch applied and the derived scheduling view.
    bootstrapped: bool | None = None
    appliedMutations: list[dict[str, Any]] | None = None
    executionWaves: list[list[dict[str, Any]]] | None = None
    leafPlacementFacts: list[dict[str, Any]] | None = None
    numberingHints: list[dict[str, Any]] | None = None
    projectionEffects: list[TaskDocProjectionEffect] = Field(default_factory=list, max_length=2048)


class TaskReopenResponse(WorktreeCommandResponse):
    """task_reopen resets a leaf's contract + doc; the payload keeps the contract-state shape."""

    operation: Literal["task_reopen"] = "task_reopen"
