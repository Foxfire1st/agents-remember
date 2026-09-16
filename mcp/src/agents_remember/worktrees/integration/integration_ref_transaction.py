"""Exact named-ref preparation and compare-and-swap for integration landings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_attribution import (
    code_commit_exists,
)
from agents_remember.worktrees.integration.integration_branch_authority import (
    branch_worktree_owners,
    integration_targets,
)
from agents_remember.worktrees.integration.integration_operation_authority import (
    require_authorized_integration_commits,
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

    def __init__(
        self,
        message: str,
        *,
        expected: dict[str, dict[str, str]],
        observed: dict[str, str],
    ) -> None:
        super().__init__(message)
        self.expected = expected
        self.observed = observed


_PREPARED_MOVE_AUTHORITY = object()


@dataclass(frozen=True)
class IntegratedCommits:
    """The accepted code and memory commits landed as one authority set."""

    code: str
    memory_content: str


@dataclass(frozen=True)
class IntegrationRefSnapshot:
    """The last reversible read of every exact ref before CAS movement."""

    code_branch: str
    code_before: str
    memory_branch: str = ""
    memory_before: str = ""
    _authority: object | None = None


@dataclass(frozen=True)
class LandingAdmission:
    """The route-specific facts one landing admits before it moves a protected ref.

    The final routes land the closeout candidate the contract records. The checkpoint route admits
    an *unfinished* master, which has no closeout cell: its output must equal the candidate its own
    live capture proved. Every other refusing read on this path is identical for both routes, so
    the difference lives here as data rather than as a second copy of the transaction.
    """

    checkpoint_candidate: IntegratedCommits | None = None


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
    admission: LandingAdmission | None = None,
) -> IntegrationRefSnapshot:
    """Perform every refusing read before the lifecycle marks the move irreversible."""

    admitted = admission or LandingAdmission()
    _require_landing_output_authority(contract, args, commits, admitted)
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
        if not is_ancestor(contract.memory_repo_path, memory_head_before, commits.memory_content):
            raise RuntimeError(
                "integrated memory commit is not a fast-forward from the current memory branch"
            )
        require_integrated_memory_ancestry(
            contract,
            commits,
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
            exclude_paths=("memory.md",),
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
            expected={
                "before": {"codeRef": snapshot.code_before},
                "intended": {"codeRef": commits.code},
            },
            observed={},
        )
    if contract.memory_mode != "external":
        refresh_owned_checkout(
            contract.code_repo_path,
            snapshot.code_branch,
            CheckoutRefresh("code", snapshot.code_before, commits.code),
            authority=_PREPARED_MOVE_AUTHORITY,
        )
        return

    assert contract.memory_repo_path is not None
    if not _compare_and_swap_ref(
        contract.memory_repo_path,
        snapshot.memory_branch,
        snapshot.memory_before,
        commits.memory_content,
        authority=_PREPARED_MOVE_AUTHORITY,
    ):
        raise IntegrationRefRace(
            "memory integration ref moved before its compare-and-swap; retain the landed "
            "code ref as same-generation recovery evidence",
            expected={
                "before": {
                    "codeRef": snapshot.code_before,
                    "memoryRef": snapshot.memory_before,
                },
                "intended": {
                    "codeRef": commits.code,
                    "memoryRef": commits.memory_content,
                },
            },
            observed={},
        )
    refresh_owned_checkout(
        contract.code_repo_path,
        snapshot.code_branch,
        CheckoutRefresh("code", snapshot.code_before, commits.code),
        authority=_PREPARED_MOVE_AUTHORITY,
    )
    refresh_owned_checkout(
        contract.memory_repo_path,
        snapshot.memory_branch,
        CheckoutRefresh("memory", snapshot.memory_before, commits.memory_content),
        authority=_PREPARED_MOVE_AUTHORITY,
    )


def require_integrated_memory_ancestry(
    contract: WorktreeContract,
    commits: IntegratedCommits,
    *,
    memory_source_commit: str,
) -> None:
    """Prove the accepted objects and memory source ancestry without consulting a cache."""

    if contract.kind not in {"leaf", "series"}:
        raise RuntimeError("integrated memory requires a leaf or series contract")
    if contract.memory_repo_path is None:
        raise RuntimeError("integrated memory requires its repository")
    if not code_commit_exists(contract.code_repo_path, commits.code):
        raise RuntimeError("integrated code commit does not exist in its repository")
    if not is_ancestor(contract.memory_repo_path, memory_source_commit, commits.memory_content):
        raise RuntimeError("integrated memory commit is not based on the exact memory source")


def _require_landing_output_authority(
    contract: WorktreeContract,
    args: WorktreeArgs,
    commits: IntegratedCommits,
    admission: LandingAdmission,
) -> None:
    """Prove the commits about to land are the ones this route is entitled to land.

    The final routes land the closeout candidate recorded on the contract: that cell is a
    completion fact, and :func:`require_authorized_integration_commits` refuses a replay whose
    output is not it.

    The checkpoint route has no such cell -- an unfinished master was never closed out -- so its
    output is authorized by its own capture instead, which is re-proved against the live refs by
    :func:`~agents_remember.worktrees.series_closeout.publish_series_checkpoint_under_authority`
    immediately before this call. This function only refuses a landing whose output is not the
    candidate that was admitted, so the two halves cannot drift apart.
    """

    if admission.checkpoint_candidate is None:
        require_authorized_integration_commits(
            contract,
            args,
            code_commit=commits.code,
            memory_content_commit=commits.memory_content,
        )
        return
    if commits != admission.checkpoint_candidate:
        raise RuntimeError(
            "checkpoint landing output is not the exact candidate its live capture proved"
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
    refresh: CheckoutRefresh,
    *,
    authority: object | None = None,
) -> None:
    if authority is not _PREPARED_MOVE_AUTHORITY:
        raise RuntimeError("protected checkout refresh requires journaled authority")
    old, new = refresh.old, refresh.new
    exclude_paths = ("memory.md",) if refresh.side == "memory" else ()
    if branch_commit(repo, branch) != new:
        raise RuntimeError("protected checkout refresh requires its named ref at the landed tip")
    paths = ["--", ".", *(f":(top,exclude){path}" for path in exclude_paths)]
    for checkout in branch_worktree_owners(repo, branch):
        untracked = run_git(checkout, ["ls-files", "--others", "--exclude-standard", *paths])
        if untracked.returncode != 0 or untracked.stdout.strip():
            raise RuntimeError(
                f"protected ref {branch!r} landed, but its checkout contains untracked files"
            )
        worktree_at_new = run_git(checkout, ["diff", "--quiet", new, *paths]).returncode == 0
        index_at_new = (
            run_git(checkout, ["diff", "--cached", "--quiet", new, *paths]).returncode == 0
        )
        if worktree_at_new and index_at_new:
            continue
        worktree_at_old = run_git(checkout, ["diff", "--quiet", old, *paths]).returncode == 0
        index_at_old = (
            run_git(checkout, ["diff", "--cached", "--quiet", old, *paths]).returncode == 0
        )
        if not worktree_at_old or not index_at_old:
            raise RuntimeError(
                f"protected ref {branch!r} landed, but its checkout contains unrelated changes"
            )
        for path in exclude_paths:
            _reset_derived_path(checkout, old, path)
        require_git(checkout, ["read-tree", "--reset", "-u", new])


def _reset_derived_path(checkout: Path, old: str, path: str) -> None:
    """Discard only an excluded cache's local changes before Git refreshes content."""

    if require_git(checkout, ["ls-tree", "--name-only", old, "--", path]):
        require_git(checkout, ["restore", f"--source={old}", "--staged", "--worktree", "--", path])
        return
    require_git(checkout, ["rm", "--cached", "--force", "--ignore-unmatch", "--", path])
    cache = checkout / path
    if cache.is_file() or cache.is_symlink():
        cache.unlink()


def _require_clean_branch_checkout(
    repo: Path, branch: str, expected: str, *, exclude_paths: tuple[str, ...] = ()
) -> None:
    for checkout in branch_worktree_owners(repo, branch):
        require_clean(checkout, f"protected ref {branch!r} checkout", exclude_paths=exclude_paths)
        if head_commit(checkout) != expected:
            raise RuntimeError(
                f"protected ref {branch!r} checkout is not at its expected named-ref tip"
            )
