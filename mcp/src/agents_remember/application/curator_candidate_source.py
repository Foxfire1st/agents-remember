"""The ordinary leaf curator's exact pre-closeout source capture and currentness check."""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.application.knowledge_write_admission import KnowledgeWriteAdmission
from agents_remember.errors import FutureCodeCandidateError
from agents_remember.memory.knowledge.refusals import RefusalFacts, refusal
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.worktrees.modules.future_code_candidate import (
    FutureCodeCandidateIdentity,
    capture_future_code_candidate,
    require_current_future_code_candidate,
)
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)


@dataclass(frozen=True)
class CuratorCodeCapture:
    contract: WorktreeContract
    identity: FutureCodeCandidateIdentity

    def currentness_refusal(self) -> KnowledgeRefusal | None:
        """Re-prove the same capture before writes and publication; never substitute moved inputs."""

        try:
            current = load_contract(self.contract.contract_path)
            if any(
                getattr(current, field) != getattr(self.contract, field)
                for field in (
                    "kind",
                    "leaf_id",
                    "repo_name",
                    "task_root",
                    "code_worktree",
                    "code_repo_path",
                    "memory_repo_path",
                    "memory_worktree",
                    "memory_base_commit",
                )
            ):
                raise FutureCodeCandidateError(
                    "future-code-candidate-stale", "the curator's admitted enclosure inputs moved"
                )
            require_current_future_code_candidate(current, self.identity)
        except (FutureCodeCandidateError, ContractError, OSError) as error:
            return refusal(
                "stale_precondition",
                "change_candidate",
                str(error),
                facts=RefusalFacts(expected=self.identity.model_dump_json()),
                next_action="Re-read the changed source and rerun curation against its new captured candidate.",
            )
        return None


def capture_curator_code(admission: KnowledgeWriteAdmission) -> CuratorCodeCapture:
    """Use the shared future-code owner for a real leaf; taskless bootstrap does not call this."""

    contract = load_contract(admission.source_ref)
    captured = capture_future_code_candidate(contract)
    if captured.codeBaseCommit != admission.code_base_commit:
        raise FutureCodeCandidateError(
            "future-code-candidate-stale", "the admitted code base moved before curator capture"
        )
    return CuratorCodeCapture(contract, captured)


@dataclass(frozen=True)
class CuratorSourceTrees:
    """The exact source trees, admitted task base and optional live leaf currentness proof."""

    code: str
    memory: str
    base: str
    code_source: str
    capture: CuratorCodeCapture | None = None

    def currentness_refusal(self) -> KnowledgeRefusal | None:
        return None if self.capture is None else self.capture.currentness_refusal()
