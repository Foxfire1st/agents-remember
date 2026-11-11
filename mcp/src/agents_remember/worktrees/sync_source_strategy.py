"""Which line one sync pulls, chosen once from the request or the journal.

``sync_source`` is omitted (``""`` in the journal) for the contract's own source line, or
``"repository-default"`` for each repository's default branch. Each strategy answers every question
the transaction asks of that choice -- the branch per side, the bases a finalized sync records, the
sync-log keys, the pinned refs, the contract finalization and its undo -- so no caller compares the
mode itself.

A default-source sync keeps the contract's base pair, pins its resolved branch in a ref name and
appends one sync-log entry naming the sources, incoming tips and produced heads. Resume and cancel
read the journal and those pins only; the live default is read again only on continue.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from pathlib import Path
from typing import Literal, TypedDict, override

from agents_remember.models.worktree import SyncSide
from agents_remember.worktrees.integration.integration_branch_repository import (
    BranchAuthorityUnavailable,
    memory_repository_default_branch,
    repository_default_branch,
)
from agents_remember.worktrees.sync_transaction_git import SyncGitProofError
from agents_remember.worktrees.sync_transaction_state import (
    SyncOperationRecord,
    SyncSideRecord,
    sync_side_default_source_prefix,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

SyncSourceMode = Literal["", "repository-default"]

#: The values a caller may name in ``sync_source``; omitting it selects the contract's own line.
SELECTABLE_SYNC_SOURCES: frozenset[str] = frozenset({"repository-default"})

#: The sync-log keys that prove a contract finalization belongs to one sync generation.
FINALIZED_KEYS = frozenset(
    {"codeBaseFrom", "codeBaseTo", "memoryBaseFrom", "memoryBaseTo", "codeHeadTo", "memoryHeadTo"}
)


class SourceRecordFields(TypedDict):
    """The journal fields that record the source choice at admission."""

    syncSource: SyncSourceMode
    codeSourceFrom: str
    memorySourceFrom: str


def repository_default_branch_of(side: SyncSide, repository: Path) -> str:
    """The repository's default branch, read where the integration authority reads it.

    Code reads the repository's ``origin/HEAD``; memory reads its ``origin/HEAD`` or its
    ``memory_init`` record. An unrecorded default raises ``BranchAuthorityUnavailable``.
    """

    if side == "code":
        return repository_default_branch(repository)
    return memory_repository_default_branch(repository)


def _contract_source_branch(contract: WorktreeContract, side: SyncSide) -> str:
    return contract.code_source_branch if side == "code" else contract.memory_source_branch


def _side_repository(contract: WorktreeContract, side: SyncSide) -> Path:
    if side == "code":
        return contract.code_repo_path
    if contract.memory_repo_path is None:
        raise SyncGitProofError("external-memory sync has no memory repository")
    return contract.memory_repo_path


def owns_log_entry(written: dict[str, str], entry: dict[str, str]) -> bool:
    return all(written.get(key) == value for key, value in entry.items())


def _owns_last_entry(contract: WorktreeContract, entry: dict[str, str]) -> bool:
    return bool(contract.sync_log) and owns_log_entry(contract.sync_log[-1], entry)


class SyncSourceStrategy(ABC):
    """The answers of one source choice; one instance per mode."""

    mode: SyncSourceMode
    #: The summary of a completed pass whose source did not move again.
    synced_summary: str

    @abstractmethod
    def source_branch(self, contract: WorktreeContract, side: SyncSide) -> str:
        """The branch this sync pulls on one side."""

    @abstractmethod
    def bases_current(
        self, contract: WorktreeContract, code: SyncSideRecord, memory: SyncSideRecord | None
    ) -> bool:
        """Whether the recorded base pair already carries the admitted sources."""

    @abstractmethod
    def target_bases(self, record: SyncOperationRecord) -> tuple[str, str]:
        """The code and memory bases a finalized sync records."""

    @abstractmethod
    def log_keys(self, record: SyncOperationRecord) -> dict[str, str]:
        """The sync-log keys this choice adds to the finalized entry."""

    @abstractmethod
    def pinned_ref(self, record: SyncOperationRecord, side: SyncSideRecord) -> str | None:
        """The extra ref that pins this side's resolved branch, or None."""

    @abstractmethod
    def moved_default(self, record: SyncOperationRecord) -> str | None:
        """On continue: why the recorded default no longer holds, or None."""

    @abstractmethod
    def journal_source_branch(
        self, record: SyncOperationRecord, side: SyncSideRecord, contract_branch: str
    ) -> str:
        """The source branch the journal side must carry beside the contract's own line."""

    @abstractmethod
    def record_fields(self, contract: WorktreeContract) -> SourceRecordFields:
        """The journal fields recording this choice for a new or rebuilt generation."""

    @abstractmethod
    def finalized(self, contract: WorktreeContract, record: SyncOperationRecord) -> bool:
        """Whether the contract already holds this generation's finalization."""

    @abstractmethod
    def finalization_absent(self, contract: WorktreeContract, record: SyncOperationRecord) -> bool:
        """Whether a cancel can tell, without proof, that nothing was finalized."""

    @abstractmethod
    def without_finalization(
        self, contract: WorktreeContract, record: SyncOperationRecord
    ) -> WorktreeContract:
        """The contract with this generation's proven finalization removed."""

    @abstractmethod
    def request_args(self) -> dict[str, str]:
        """The ``worktree_sync`` arguments that select this choice again."""

    @abstractmethod
    def result_keys(self) -> dict[str, str]:
        """The keys a completed answer adds to name this choice."""


class ContractLineSource(SyncSourceStrategy):
    """The contract's own source line: a finalized sync moves the base pair to the sources."""

    mode: SyncSourceMode = ""
    synced_summary = "The worktree base pair now matches the admitted official line."

    @override
    def source_branch(self, contract: WorktreeContract, side: SyncSide) -> str:
        return _contract_source_branch(contract, side)

    @override
    def bases_current(
        self, contract: WorktreeContract, code: SyncSideRecord, memory: SyncSideRecord | None
    ) -> bool:
        return code.sourceCommit == contract.code_base_commit and (
            memory is None or memory.sourceCommit == contract.memory_base_commit
        )

    @override
    def target_bases(self, record: SyncOperationRecord) -> tuple[str, str]:
        if record.memory is not None and record.memory.plan != "skip":
            return record.code.sourceCommit, record.memory.sourceCommit
        return record.code.sourceCommit, record.memoryBaseFrom

    @override
    def log_keys(self, record: SyncOperationRecord) -> dict[str, str]:
        return {}

    @override
    def pinned_ref(self, record: SyncOperationRecord, side: SyncSideRecord) -> str | None:
        return None

    @override
    def moved_default(self, record: SyncOperationRecord) -> str | None:
        return None

    @override
    def journal_source_branch(
        self, record: SyncOperationRecord, side: SyncSideRecord, contract_branch: str
    ) -> str:
        return contract_branch

    @override
    def record_fields(self, contract: WorktreeContract) -> SourceRecordFields:
        return {"syncSource": "", "codeSourceFrom": "", "memorySourceFrom": ""}

    @override
    def finalized(self, contract: WorktreeContract, record: SyncOperationRecord) -> bool:
        return (contract.code_base_commit, contract.memory_base_commit) == self.target_bases(record)

    @override
    def finalization_absent(self, contract: WorktreeContract, record: SyncOperationRecord) -> bool:
        return (contract.code_base_commit, contract.memory_base_commit) == (
            record.codeBaseFrom,
            record.memoryBaseFrom,
        )

    @override
    def without_finalization(
        self, contract: WorktreeContract, record: SyncOperationRecord
    ) -> WorktreeContract:
        """Prove the moved pair and the journaled heads are this generation's, then undo both."""

        expected = {
            key: value for key, value in sync_log_entry(record).items() if key in FINALIZED_KEYS
        }
        pair = (contract.code_base_commit, contract.memory_base_commit)
        if pair != self.target_bases(record) or not _owns_last_entry(contract, expected):
            raise SyncGitProofError("contract finalization is not owned by this sync generation")
        return replace(
            contract,
            code_base_commit=record.codeBaseFrom,
            memory_base_commit=record.memoryBaseFrom,
            sync_log=contract.sync_log[:-1],
        )

    @override
    def request_args(self) -> dict[str, str]:
        return {}

    @override
    def result_keys(self) -> dict[str, str]:
        return {}


class RepositoryDefaultSource(SyncSourceStrategy):
    """Each repository's default branch: the base pair stays, one sync-log entry names it."""

    mode: SyncSourceMode = "repository-default"
    synced_summary = (
        "The worktree carries the repository-default line; the contract's base "
        "pair is unchanged and the sync log names the sources, tips and heads."
    )

    @override
    def source_branch(self, contract: WorktreeContract, side: SyncSide) -> str:
        return repository_default_branch_of(side, _side_repository(contract, side))

    @override
    def bases_current(
        self, contract: WorktreeContract, code: SyncSideRecord, memory: SyncSideRecord | None
    ) -> bool:
        return True  # the pair is kept, so only the work branches decide

    @override
    def target_bases(self, record: SyncOperationRecord) -> tuple[str, str]:
        return record.codeBaseFrom, record.memoryBaseFrom

    @override
    def log_keys(self, record: SyncOperationRecord) -> dict[str, str]:
        # The incoming tips are official positions the line reconciled with: the landing chain
        # proof admits the default line's history through them.
        memory = record.memory
        return {
            "syncSource": self.mode,
            "codeSourceBranch": record.code.sourceBranch,
            "codeSourceCommit": record.code.sourceCommit,
            "memorySourceBranch": memory.sourceBranch if memory else "",
            "memorySourceCommit": (
                memory.sourceCommit if memory is not None and memory.plan != "skip" else ""
            ),
        }

    @override
    def pinned_ref(self, record: SyncOperationRecord, side: SyncSideRecord) -> str | None:
        prefix = sync_side_default_source_prefix(Path(record.contractPath), side.side)
        return f"{prefix}{side.sourceBranch}"

    @override
    def moved_default(self, record: SyncOperationRecord) -> str | None:
        """Is each recorded default still the repository's default branch?

        Resume and cancel run from the journal and its pins alone; this live read happens only
        where the sync would carry the recorded line forward.
        """

        for side in (record.code, record.memory):
            if side is None or side.plan in {"already-current", "skip"}:
                continue
            try:
                current = repository_default_branch_of(side.side, Path(side.repository))
            except (BranchAuthorityUnavailable, RuntimeError, OSError) as error:
                return (
                    f"The {side.side} repository default cannot be read ({error}); the sync "
                    f"recorded {side.sourceBranch}. End this sync with "
                    "resolution_action='cancel', or continue once the default reads."
                )
            if current != side.sourceBranch:
                return (
                    f"The {side.side} repository default is now {current}; this sync recorded "
                    f"{side.sourceBranch}. End this sync with worktree_sync and "
                    "resolution_action='cancel', then start a new repository-default sync."
                )
        return None

    @override
    def journal_source_branch(
        self, record: SyncOperationRecord, side: SyncSideRecord, contract_branch: str
    ) -> str:
        """The recorded default stays the source; the contract's own line must be unchanged.

        A retargeted contract refuses. Whether the default is still the repository's default is
        read live only on continue. The cancel route rebuilds the journal from the pinned refs
        beside the contract's current line, so it ends such a sync.
        """

        admitted = record.codeSourceFrom if side.side == "code" else record.memorySourceFrom
        if contract_branch != admitted:
            raise SyncGitProofError(
                f"sync journal {side.side} authority changed: this sync was admitted beside the "
                f"contract's source branch {admitted}, and the contract now names "
                f"{contract_branch}. End this sync with worktree_sync and "
                "resolution_action='cancel'."
            )
        return side.sourceBranch

    @override
    def record_fields(self, contract: WorktreeContract) -> SourceRecordFields:
        return {
            "syncSource": self.mode,
            "codeSourceFrom": contract.code_source_branch,
            "memorySourceFrom": contract.memory_source_branch,
        }

    @override
    def finalized(self, contract: WorktreeContract, record: SyncOperationRecord) -> bool:
        # A finalization that died after its log write finds its own entry.
        return _owns_last_entry(contract, sync_log_entry(record))

    @override
    def finalization_absent(self, contract: WorktreeContract, record: SyncOperationRecord) -> bool:
        return False  # the pair never moves, so only the proven log entry can tell

    @override
    def without_finalization(
        self, contract: WorktreeContract, record: SyncOperationRecord
    ) -> WorktreeContract:
        """Remove only this generation's own log entry; the pair never moved."""

        if _owns_last_entry(contract, sync_log_entry(record)):
            return replace(contract, sync_log=contract.sync_log[:-1])
        return contract

    @override
    def request_args(self) -> dict[str, str]:
        return {"sync_source": self.mode}

    @override
    def result_keys(self) -> dict[str, str]:
        return {"syncSource": self.mode}


CONTRACT_LINE = ContractLineSource()
REPOSITORY_DEFAULT = RepositoryDefaultSource()
_STRATEGIES: dict[str, SyncSourceStrategy] = {
    CONTRACT_LINE.mode: CONTRACT_LINE,
    REPOSITORY_DEFAULT.mode: REPOSITORY_DEFAULT,
}


def source_strategy(sync_source: str | None) -> SyncSourceStrategy:
    """The strategy a request (``None`` when omitted) or a journal (``""``) names."""

    strategy = _STRATEGIES.get(sync_source or "")
    if strategy is None:
        raise ValueError(
            f"sync_source must be omitted or exactly 'repository-default': {sync_source!r}"
        )
    return strategy


def target_code_base(record: SyncOperationRecord) -> str:
    return source_strategy(record.syncSource).target_bases(record)[0]


def target_memory_base(record: SyncOperationRecord) -> str:
    return source_strategy(record.syncSource).target_bases(record)[1]


def sync_log_entry(record: SyncOperationRecord) -> dict[str, str]:
    """The contract's sync-log entry for one finalized generation, without its time."""

    code_to, memory_to = source_strategy(record.syncSource).target_bases(record)
    return {
        "codeBaseFrom": record.codeBaseFrom,
        "codeBaseTo": code_to,
        "memoryBaseFrom": record.memoryBaseFrom,
        "memoryBaseTo": memory_to,
        "code": record.code.plan,
        "memory": record.memory.plan if record.memory is not None else "no-external-memory",
        # The heads this sync produced, so the landing chain proof admits exactly the merges the
        # sync made. An entry written before these keys existed admits its merge as before.
        "codeHeadTo": record.code.resultHead,
        "memoryHeadTo": record.memory.resultHead if record.memory is not None else "",
        **source_strategy(record.syncSource).log_keys(record),
    }
