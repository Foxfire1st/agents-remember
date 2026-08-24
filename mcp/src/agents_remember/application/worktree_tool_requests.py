"""Typed request objects shared by worktree application entry points."""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.models.closeout_source import CandidateAdmissionFacts, SchedulingGradeInput
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.lifecycles.operation import LifecycleOperationKind
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_controls import (
    LifecycleControlAction,
)


@dataclass(frozen=True)
class TaskIdentity:
    """The identity a task is created under.

    ``worktree_name`` is the on-disk directory the code worktree gets;
    ``leaf_id``/``parent_task`` place the task in the task tree; ``workflow_kind``
    is the document format its contract follows ('light-task' or 'chat-task').
    """

    repo_id: str
    task_name: str
    worktree_name: str
    leaf_id: str | None = None
    parent_task: str | None = None
    workflow_kind: str = "light-task"


@dataclass(frozen=True)
class TaskBases:
    """What a started task is based on, and the answers that clear a refused base.

    A start cuts a code work branch from a source branch and opens memory alongside
    it under ``memory_mode``. When a preflight refuses -- a source branch behind or
    diverged from its remote, or an undecided memory setup -- the caller may explicitly
    choose ``proceed-stale`` or ``disabled-memory``. Protected source repair belongs to a
    separate landing/recovery operation.
    """

    source_branch: str | None = None
    work_branch: str | None = None
    memory_mode: str | None = None
    memory_choice: str | None = None
    stale_base_choice: str | None = None


@dataclass(frozen=True)
class StartExecution:
    """How a task start and its background provider setup execute."""

    dry_run: bool = False
    skip_provider_setup: bool = False
    retry_provider_setup: bool = False


DEFAULT_TASK_BASES = TaskBases()
"""Repo-default branches, repo-default memory topology, no recovery choice made."""

DEFAULT_START_EXECUTION = StartExecution()
"""A real start with background provider setup launched normally."""


@dataclass(frozen=True)
class OperationControlRequest:
    contract_path: str
    operation_kind: LifecycleOperationKind
    action: LifecycleControlAction
    expected_generation: int
    intent_note: str
    dry_run: bool = False
    code_commit_message: str | None = None
    memory_commit_message: str | None = None
    ledger_commit_message: str | None = None
    grade: SchedulingGradeInput | None = None
    admission: CandidateAdmissionFacts | None = None
    caller: DeclaredCaller | None = None

    def __post_init__(self) -> None:
        # Legal-control arguments are public JSON and must be directly
        # executable when fed back through this application request boundary.
        # Transport registration already supplies the typed model; direct
        # application callers reconstruct the same bounded model from JSON.
        if self.caller is not None and not isinstance(self.caller, DeclaredCaller):
            object.__setattr__(self, "caller", DeclaredCaller.model_validate(self.caller))
        if self.grade is not None and not isinstance(self.grade, SchedulingGradeInput):
            object.__setattr__(self, "grade", SchedulingGradeInput.model_validate(self.grade))
        if self.admission is not None and not isinstance(self.admission, CandidateAdmissionFacts):
            object.__setattr__(
                self,
                "admission",
                CandidateAdmissionFacts.model_validate(self.admission),
            )


@dataclass(frozen=True)
class CloseoutCommitMessages:
    """Raw public observations; contract resolution decides which legs are enabled."""

    code: str | None = None
    memory: str | None = None
    ledger: str | None = None


@dataclass(frozen=True)
class CloseoutApproval:
    """Whether a closeout actually commits, and the note recording why it may."""

    intent_note: str = ""
    dry_run: bool = False


PREVIEW_ONLY = CloseoutApproval(dry_run=True)
"""The preview form: nothing is committed and no approval is claimed."""


@dataclass(frozen=True)
class FinalizeTaskDocs:
    """Task-document addresses reconciled by lifecycle finalization."""

    task_doc_path: str | None = None
    master_doc_path: str | None = None
    subtask_number: str = ""


NO_TASK_DOCS = FinalizeTaskDocs()
"""Finalize a contract that carries no task documents to tick."""
