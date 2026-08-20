"""Exact named-ref preparation and compare-and-swap for integration landings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_ledger import (
    LedgerError,
    LedgerRow,
    MemoryLedger,
    find_unique_mapping,
    parse_ledger_text,
)
from agents_remember.worktrees.integration.integration_branch_authority import (
    branch_worktree_owners,
    integration_targets,
)
from agents_remember.worktrees.integration.integration_operation_authority import (
    require_authorized_integration_commits,
    require_current_integration_sources,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.git import (
    branch_commit,
    head_commit,
    is_ancestor,
    require_clean,
    require_git,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


@dataclass(frozen=True)
class IntegrationSources:
    """One exact reading of both integration sources and their replay verdicts."""

    current_code_source: str
    current_memory_source: str
    code_replay_required: bool
    memory_replay_required: bool

    @property
    def replay_required(self) -> bool:
        return self.code_replay_required or self.memory_replay_required


class IntegrationRefRace(RuntimeError):
    """A named-ref compare-and-swap failed at the protected boundary."""

    def __init__(self, message: str, *, safe_to_replace: bool) -> None:
        super().__init__(message)
        self.safe_to_replace = safe_to_replace


_PREPARED_MOVE_AUTHORITY = object()


@dataclass(frozen=True)
class IntegratedCommits:
    """The code, memory-content, and ledger commits landed as one authority set."""

    code: str
    memory_content: str
    ledger: str


@dataclass(frozen=True)
class IntegrationRefSnapshot:
    """The last reversible read of every exact ref before CAS movement."""

    code_branch: str
    code_before: str
    memory_branch: str = ""
    memory_before: str = ""
    _authority: object | None = None


@dataclass(frozen=True)
class CheckoutRefresh:
    """One exact landed checkout transition recovered under immutable authority."""

    side: str
    old: str
    new: str


def prepare_integration_ref_move(
    contract: WorktreeContract,
    commits: IntegratedCommits,
    args: WorktreeArgs,
    sources: IntegrationSources,
    *,
    expected_series_ledger_prefix: tuple[LedgerRow, ...] = (),
) -> IntegrationRefSnapshot:
    """Perform every refusing read before the lifecycle marks the move irreversible."""

    require_authorized_integration_commits(
        contract,
        args,
        code_commit=commits.code,
        memory_content_commit=commits.memory_content,
        ledger_commit=commits.ledger,
    )
    targets = {target.side: target for target in integration_targets(contract)}
    code_target = targets["code"]
    external = contract.memory_mode == "external"
    code_head_before = branch_commit(contract.code_repo_path, code_target.branch)
    if code_head_before != sources.current_code_source:
        raise RuntimeError("code integration source moved at the protected-ref boundary")
    if not is_ancestor(contract.code_repo_path, code_head_before, commits.code):
        raise RuntimeError(
            "integrated code commit is not a fast-forward from the current code branch"
        )

    memory_head_before = ""
    memory_target = targets.get("memory")
    if external:
        assert contract.memory_repo_path is not None
        assert memory_target is not None
        memory_head_before = branch_commit(contract.memory_repo_path, memory_target.branch)
        if memory_head_before != sources.current_memory_source:
            raise RuntimeError("memory integration source moved at the protected-ref boundary")
        if not is_ancestor(contract.memory_repo_path, memory_head_before, commits.ledger):
            raise RuntimeError(
                "integrated memory ledger commit is not a fast-forward from the current "
                "memory branch"
            )
        require_integrated_ledger_mapping(
            contract,
            commits,
            memory_source_commit=memory_head_before,
            expected_series_prefix=expected_series_ledger_prefix,
        )

    require_current_integration_sources(
        contract,
        args,
        code_source_commit=code_head_before,
        memory_source_commit=memory_head_before,
    )
    _require_clean_branch_checkout(contract.code_repo_path, code_target.branch, code_head_before)
    if external:
        assert contract.memory_repo_path is not None
        assert memory_target is not None
        _require_clean_branch_checkout(
            contract.memory_repo_path,
            memory_target.branch,
            memory_head_before,
        )
    return IntegrationRefSnapshot(
        code_branch=code_target.branch,
        code_before=code_head_before,
        memory_branch=memory_target.branch if memory_target is not None else "",
        memory_before=memory_head_before,
        _authority=_PREPARED_MOVE_AUTHORITY,
    )


def merge_integrated_commits(
    contract: WorktreeContract,
    commits: IntegratedCommits,
    snapshot: IntegrationRefSnapshot,
) -> None:
    """CAS the already-validated named refs; no fresh refusing reads occur here."""

    if snapshot._authority is not _PREPARED_MOVE_AUTHORITY:
        raise RuntimeError(
            "protected-ref movement requires the plane-prepared integration capability"
        )
    if not _compare_and_swap_ref(
        contract.code_repo_path,
        snapshot.code_branch,
        snapshot.code_before,
        commits.code,
        authority=_PREPARED_MOVE_AUTHORITY,
    ):
        raise IntegrationRefRace(
            "code integration ref moved before its compare-and-swap",
            safe_to_replace=True,
        )
    if contract.memory_mode != "external":
        refresh_owned_checkout(
            contract.code_repo_path,
            snapshot.code_branch,
            snapshot.code_before,
            commits.code,
            authority=_PREPARED_MOVE_AUTHORITY,
        )
        return

    assert contract.memory_repo_path is not None
    if not _compare_and_swap_ref(
        contract.memory_repo_path,
        snapshot.memory_branch,
        snapshot.memory_before,
        commits.ledger,
        authority=_PREPARED_MOVE_AUTHORITY,
    ):
        rolled_back = _compare_and_swap_ref(
            contract.code_repo_path,
            snapshot.code_branch,
            commits.code,
            snapshot.code_before,
            authority=_PREPARED_MOVE_AUTHORITY,
        )
        raise IntegrationRefRace(
            "memory integration ref moved before its compare-and-swap; code rollback "
            + ("succeeded" if rolled_back else "was refused by a concurrent ref move"),
            safe_to_replace=rolled_back,
        )
    refresh_owned_checkout(
        contract.code_repo_path,
        snapshot.code_branch,
        snapshot.code_before,
        commits.code,
        authority=_PREPARED_MOVE_AUTHORITY,
    )
    refresh_owned_checkout(
        contract.memory_repo_path,
        snapshot.memory_branch,
        snapshot.memory_before,
        commits.ledger,
        authority=_PREPARED_MOVE_AUTHORITY,
    )


def require_integrated_ledger_mapping(
    contract: WorktreeContract,
    commits: IntegratedCommits,
    *,
    memory_source_commit: str,
    expected_series_prefix: tuple[LedgerRow, ...] = (),
) -> None:
    if contract.kind not in {"leaf", "series"}:
        raise RuntimeError("integrated memory ledger requires a leaf or series contract")
    assert contract.memory_repo_path is not None
    blob = run_git(contract.memory_repo_path, ["show", f"{commits.ledger}:memory.md"])
    if blob.returncode != 0:
        raise RuntimeError("integrated ledger commit has no readable memory.md")
    source_blob = run_git(
        contract.memory_repo_path,
        ["show", f"{memory_source_commit}:memory.md"],
    )
    if source_blob.returncode != 0:
        raise RuntimeError("exact memory source commit has no readable memory.md")
    try:
        ledger = parse_ledger_text(blob.stdout)
        source_ledger = parse_ledger_text(source_blob.stdout)
        mapping = find_unique_mapping(ledger, commits.code)
    except LedgerError as error:
        raise RuntimeError(
            "integrated memory ledger must contain exactly one landed code mapping"
        ) from error
    if mapping is None or mapping.memory_commit != commits.memory_content:
        raise RuntimeError(
            "integrated memory ledger does not map landed code commit to landed memory content"
        )
    if any(row.code_commit == commits.code for row in source_ledger.rows):
        # No-change leaf: the landed code commit is already mapped by the source ledger, so the
        # ledger and memory content are unchanged and there is nothing new to verify.
        return
    _require_preserved_ledger_history(
        contract,
        ledger,
        source_ledger,
        mapping,
        expected_series_prefix,
    )
    if not is_ancestor(contract.memory_repo_path, commits.memory_content, commits.ledger):
        raise RuntimeError(
            "integrated memory content commit is not reachable from the landed ledger commit"
        )
    if not is_ancestor(
        contract.memory_repo_path,
        memory_source_commit,
        commits.memory_content,
    ):
        raise RuntimeError(
            "integrated memory content commit is not based on the exact memory source"
        )


def _require_preserved_ledger_history(
    contract: WorktreeContract,
    ledger: MemoryLedger,
    source_ledger: MemoryLedger,
    mapping: LedgerRow,
    expected_series_prefix: tuple[LedgerRow, ...],
) -> None:
    if contract.kind == "series":
        if expected_series_prefix and ledger.rows == [
            *expected_series_prefix,
            *source_ledger.rows,
        ]:
            return
        raise RuntimeError(
            "integrated atomic series ledger does not preserve the exact ordered leaf "
            "landing prefix and complete source ledger history"
        )
    if ledger.rows != [mapping, *source_ledger.rows]:
        raise RuntimeError(
            "integrated memory ledger does not prepend exactly one mapping while preserving "
            "the complete source ledger history"
        )


def recover_integration_ref(
    contract: WorktreeContract,
    args: WorktreeArgs,
    commits: IntegratedCommits,
    *,
    side: str,
) -> bool:
    """CAS one torn side only under the immutable journaled integration authority."""

    record = require_authorized_integration_commits(
        contract,
        args,
        code_commit=commits.code,
        memory_content_commit=commits.memory_content,
        ledger_commit=commits.ledger,
    )
    authority = record.integrationAuthority
    assert authority is not None
    if side == "code":
        repository = contract.code_repo_path
        branch = authority.codeSourceBranch
        expected = authority.codeSourceCommit
        target = commits.code
    elif side == "memory" and contract.memory_repo_path is not None:
        repository = contract.memory_repo_path
        branch = authority.memorySourceBranch
        expected = authority.memorySourceCommit
        target = commits.ledger
    else:
        raise RuntimeError(f"invalid integration recovery side: {side!r}")
    return _compare_and_swap_ref(
        repository,
        branch,
        expected,
        target,
        authority=_PREPARED_MOVE_AUTHORITY,
    )


def _compare_and_swap_ref(
    repo: Path,
    branch: str,
    expected: str,
    target: str,
    *,
    authority: object | None = None,
) -> bool:
    if authority is not _PREPARED_MOVE_AUTHORITY:
        raise RuntimeError("protected-ref compare-and-swap requires journaled authority")
    result = run_git(repo, ["update-ref", f"refs/heads/{branch}", target, expected])
    return result.returncode == 0


def refresh_owned_checkout(
    repo: Path,
    branch: str,
    old: str,
    new: str,
    *,
    authority: object | None = None,
) -> None:
    if authority is not _PREPARED_MOVE_AUTHORITY:
        raise RuntimeError("protected checkout refresh requires journaled authority")
    if branch_commit(repo, branch) != new:
        raise RuntimeError("protected checkout refresh requires its named ref at the landed tip")
    for checkout in branch_worktree_owners(repo, branch):
        untracked = run_git(checkout, ["ls-files", "--others", "--exclude-standard"])
        if untracked.returncode != 0 or untracked.stdout.strip():
            raise RuntimeError(
                f"protected ref {branch!r} landed, but its checkout contains untracked files"
            )
        worktree_at_new = run_git(checkout, ["diff", "--quiet", new, "--"]).returncode == 0
        index_at_new = run_git(checkout, ["diff", "--cached", "--quiet", new, "--"]).returncode == 0
        if worktree_at_new and index_at_new:
            continue
        worktree_at_old = run_git(checkout, ["diff", "--quiet", old, "--"]).returncode == 0
        index_at_old = run_git(checkout, ["diff", "--cached", "--quiet", old, "--"]).returncode == 0
        if not worktree_at_old or not index_at_old:
            raise RuntimeError(
                f"protected ref {branch!r} landed, but its checkout contains unrelated changes"
            )
        require_git(checkout, ["read-tree", "--reset", "-u", new])


def refresh_recovered_checkout(
    contract: WorktreeContract,
    args: WorktreeArgs,
    commits: IntegratedCommits,
    refresh: CheckoutRefresh,
) -> None:
    """Refresh one landed checkout only after revalidating its immutable operation record."""

    record = require_authorized_integration_commits(
        contract,
        args,
        code_commit=commits.code,
        memory_content_commit=commits.memory_content,
        ledger_commit=commits.ledger,
    )
    authority = record.integrationAuthority
    assert authority is not None
    if refresh.side == "code":
        repository = contract.code_repo_path
        branch = authority.codeSourceBranch
    elif refresh.side == "memory" and contract.memory_repo_path is not None:
        repository = contract.memory_repo_path
        branch = authority.memorySourceBranch
    else:
        raise RuntimeError(f"invalid integration checkout recovery side: {refresh.side!r}")
    refresh_owned_checkout(
        repository,
        branch,
        refresh.old,
        refresh.new,
        authority=_PREPARED_MOVE_AUTHORITY,
    )


def _require_clean_branch_checkout(repo: Path, branch: str, expected: str) -> None:
    for checkout in branch_worktree_owners(repo, branch):
        require_clean(checkout, f"protected ref {branch!r} checkout")
        if head_commit(checkout) != expected:
            raise RuntimeError(
                f"protected ref {branch!r} checkout is not at its expected named-ref tip"
            )
