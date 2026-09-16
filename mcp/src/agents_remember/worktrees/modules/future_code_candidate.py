"""Exact pre-commit identity for a future-code closeout candidate."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agents_remember.errors import FutureCodeCandidateError
from agents_remember.worktrees.modules.git import head_commit, worktree_candidate_tree
from agents_remember.worktrees.worktree_contract import WorktreeContract


class FutureCodeCandidateIdentity(BaseModel):
    """The strict future-code route identity stored by acceptance issuance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observedCodeHead: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    codeBaseCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    codeCandidateTree: str = Field(pattern=r"^[0-9a-f]{40,64}$")


def capture_future_code_candidate(contract: WorktreeContract) -> FutureCodeCandidateIdentity:
    """Derive the full add-all leaf tree without mutating its real Git index."""

    if contract.kind != "leaf":
        raise FutureCodeCandidateError(
            "future-code-candidate-not-applicable",
            "future-code candidate identity belongs only to an ordinary leaf worktree",
        )
    try:
        observed_head = head_commit(contract.code_worktree)
        candidate_tree = _candidate_tree(contract)
        if head_commit(contract.code_worktree) != observed_head:
            raise FutureCodeCandidateError(
                "future-code-candidate-head-moved",
                "code HEAD moved while deriving the isolated add-all future-code candidate",
            )
        return FutureCodeCandidateIdentity(
            observedCodeHead=observed_head,
            codeBaseCommit=contract.code_base_commit,
            codeCandidateTree=candidate_tree,
        )
    except FutureCodeCandidateError:
        raise
    except (OSError, RuntimeError, ValidationError) as exc:
        raise FutureCodeCandidateError(
            "future-code-candidate-unavailable",
            f"could not derive the isolated add-all future-code candidate: {exc}",
        ) from exc


def require_current_future_code_candidate(
    contract: WorktreeContract,
    accepted: FutureCodeCandidateIdentity,
) -> FutureCodeCandidateIdentity:
    """Recompute and require the complete bound future-code route identity."""

    current = capture_future_code_candidate(contract)
    if current != accepted:
        raise FutureCodeCandidateError(
            "future-code-candidate-stale",
            "the future-code route identity changed after acceptance; "
            f"accepted {accepted.model_dump_json()}, current {current.model_dump_json()}",
        )
    return current


def _candidate_tree(contract: WorktreeContract) -> str:
    reports = contract.worktree_group / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".future-code-candidate-", dir=reports) as temporary_directory:
        return worktree_candidate_tree(
            contract.code_worktree,
            Path(temporary_directory) / "index",
        )
