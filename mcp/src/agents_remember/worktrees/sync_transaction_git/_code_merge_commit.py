"""Bounded continued code-side sync commit execution and mutation rollback (L29-R1).

Extracted from ``_merge.py`` so continued code-side sync commit ownership (session-isolated
``git commit --no-edit``, ``commitCheckTimeoutSeconds`` enforcement, stale-lock cleanup,
published-HEAD proof, and tracked-file/index mutation rollback) lives in its own focused
module alongside ``_merge.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.kernel.git_command import git_command_spec, run_git
from agents_remember.worktrees.modules.commit_check_process import (
    BoundedCommandSpec,
    CommitCheckRunResult,
    CommitCheckSettingsError,
    SyncCommitCheckStop,
    extract_commit_check_log_detail,
    format_timeout_outcome,
    resolve_commit_check_timeout_seconds,
    run_bounded_command,
)
from agents_remember.worktrees.modules.git import head_commit
from agents_remember.worktrees.modules.landing_commit_check import (
    append_commit_check_trailer,
    commit_check_hook_id,
    resolve_commit_check_hook,
)
from agents_remember.worktrees.sync_transaction_git._base import (
    SyncGitProofError,
    SyncMergeCommitError,
)
from agents_remember.worktrees.sync_transaction_git._read import (
    exact_created_head,
    merge_head,
    sample_paths,
    stashed_parked_paths,
)
from agents_remember.worktrees.sync_transaction_git._stash import (
    _require_sync_git,
    clear_stale_git_locks,
)
from agents_remember.worktrees.sync_transaction_state import SyncSideRecord

_MERGE_SEQUENCER_FILES = ("MERGE_HEAD", "MERGE_MSG", "MERGE_MODE")


class BoundedSyncCommitError(SyncMergeCommitError):
    """A bounded continued-sync commit failed, timed out, or mutated tracked files (L29-R1)."""

    def __init__(self, detail: str, *, stop: SyncCommitCheckStop) -> None:
        self.stop = stop
        super().__init__(detail)


@dataclass(frozen=True)
class _CodeMergeContext:
    """Validated pre-commit state captured before spawning a continued code-side sync commit."""

    side: SyncSideRecord
    worktree: Path
    git_dir: Path
    expected_tree: str
    staged: tuple[str, ...]
    task_changed: dict[str, bool]
    sequencer: dict[str, bytes]


def snapshot_merge_sequencer(git_dir: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for name in _MERGE_SEQUENCER_FILES:
        path = git_dir / name
        if path.is_file():
            snapshot[name] = path.read_bytes()
    return snapshot


def _clean_merge_message(raw: bytes | None, fallback: str) -> str:
    """Strip Git ``#`` comment lines from ``MERGE_MSG`` like ``git commit --no-edit``."""
    if raw is None:
        return fallback
    lines = [
        line
        for line in raw.decode("utf-8", errors="replace").splitlines()
        if not line.startswith("#")
    ]
    text = "\n".join(lines).strip()
    return text or fallback


def sync_merge_commit_args(
    worktree: Path,
    expected_tree: str,
    *,
    fallback_message: str,
    merge_msg_bytes: bytes | None = None,
) -> tuple[list[str], bool]:
    """Build ``git commit`` args, stamping ``Commit-Check-Pass`` iff a runnable hook will run.

    L29-R8 Condition 2: only stamp when ``resolve_commit_check_hook`` finds a runnable
    pre-commit hook that this commit runs (never bypassing hooks); when no hook exists or the
    hook is disabled, write no stamp and run ``git commit --no-edit``.
    """
    hook = resolve_commit_check_hook(worktree)
    if hook is None:
        return ["commit", "--no-edit"], False
    hook_id = commit_check_hook_id(worktree)
    base_message = _clean_merge_message(merge_msg_bytes, fallback_message)
    stamped = append_commit_check_trailer(base_message, expected_tree, hook_id)
    return ["commit", "-m", stamped], True


def _capture_code_merge_context(side: SyncSideRecord) -> _CodeMergeContext:
    """Capture staged paths, provenance, tree, and merge metadata before spawning git commit."""

    worktree = Path(side.worktree)
    staged_result = run_git(worktree, ["diff", "--cached", "--name-only", "-z", "--", "."])
    if staged_result.returncode != 0:
        raise SyncGitProofError(
            staged_result.stderr.strip() or "could not read staged code sync files"
        )
    staged = tuple(entry for entry in staged_result.stdout.split("\0") if entry)
    try:
        parked = set(stashed_parked_paths(side))
    except SyncGitProofError:
        parked = set(sample_paths(side))
    task_changed = {path: (path in parked) for path in staged}
    expected_tree = _require_sync_git(worktree, ["write-tree"])
    git_dir = Path(_require_sync_git(worktree, ["rev-parse", "--absolute-git-dir"]))
    return _CodeMergeContext(
        side=side,
        worktree=worktree,
        git_dir=git_dir,
        expected_tree=expected_tree,
        staged=staged,
        task_changed=task_changed,
        sequencer=snapshot_merge_sequencer(git_dir),
    )


def _mutated_tracked_paths(
    worktree: Path, expected_tree: str, current_tree: str | None
) -> tuple[str, ...]:
    """Tracked paths altered in the index or working tree during the commit hook."""

    changed: set[str] = set()
    if current_tree is None:
        changed.add("the index")
    elif current_tree != expected_tree:
        tree_diff = run_git(worktree, ["diff", "--name-only", "-z", expected_tree, current_tree])
        if tree_diff.returncode != 0:
            raise SyncGitProofError(
                tree_diff.stderr.strip() or "could not compare committed tree to staged tree"
            )
        names = [entry for entry in tree_diff.stdout.split("\0") if entry]
        changed.update(names or ["the index"])
    unstaged = run_git(worktree, ["diff", "--name-only", "-z"])
    if unstaged.returncode != 0:
        raise SyncGitProofError(
            unstaged.stderr.strip() or "could not inspect worktree after commit"
        )
    changed.update(entry for entry in unstaged.stdout.split("\0") if entry)
    return tuple(sorted(changed))


def _restore_merge_after_mutation(context: _CodeMergeContext, current_tree: str | None) -> None:
    """Restore HEAD, pinned index tree, and merge sequencer files after a hook mutation."""

    if head_commit(context.worktree) != context.side.preSyncHead:
        _require_sync_git(context.worktree, ["reset", "--soft", context.side.preSyncHead])
    if current_tree != context.expected_tree:
        _require_sync_git(context.worktree, ["read-tree", context.expected_tree])
    for name, payload in context.sequencer.items():
        atomic_write_bytes(context.git_dir / name, payload)
    if (
        head_commit(context.worktree) != context.side.preSyncHead
        or merge_head(context.worktree) != context.side.sourceCommit
        or _require_sync_git(context.worktree, ["write-tree"]) != context.expected_tree
    ):
        raise SyncGitProofError(
            f"{context.side.side} merge state could not be restored after hook file mutation"
        )


def _restore_nonpublishing_code_merge(context: _CodeMergeContext) -> tuple[str, ...]:
    """Detect tracked/index mutations on failed or timed-out commits and restore pinned state."""

    written = run_git(context.worktree, ["write-tree"])
    current_tree = written.stdout.strip() if written.returncode == 0 else None
    mutated = _mutated_tracked_paths(context.worktree, context.expected_tree, current_tree)
    if (
        current_tree != context.expected_tree
        or mutated
        or snapshot_merge_sequencer(context.git_dir) != context.sequencer
    ):
        _restore_merge_after_mutation(context, current_tree)
    return mutated


def _build_sync_stop(
    context: _CodeMergeContext,
    run_result: CommitCheckRunResult,
    *,
    timed_out: bool,
    changed_paths: tuple[str, ...],
) -> SyncCommitCheckStop:
    return SyncCommitCheckStop(
        side=context.side.side,
        staged=context.staged,
        task_changed=context.task_changed,
        timed_out=timed_out,
        timeout_seconds=run_result.timeout_seconds,
        log_path=run_result.log,
        changed_paths=changed_paths,
        escaped_survivors=run_result.outcome.escaped_survivors if timed_out else (),
    )


def _verify_published_code_merge(
    context: _CodeMergeContext, result_head: str, run_result: CommitCheckRunResult
) -> str:
    """Accept a published merge commit only if the hook left tracked files and tree intact."""

    committed_tree = _require_sync_git(
        context.worktree, ["rev-parse", "--verify", f"{result_head}^{{tree}}"]
    )
    mutated = _mutated_tracked_paths(context.worktree, context.expected_tree, committed_tree)
    if not mutated:
        return result_head
    _restore_merge_after_mutation(context, committed_tree)
    shown = ", ".join(mutated[:10])
    detail = (
        f"the commit hook modified tracked file(s) during the commit ({shown}); "
        f"nothing was published; hook log: {run_result.log.as_posix()}"
    )
    stop = _build_sync_stop(context, run_result, timed_out=False, changed_paths=mutated)
    raise BoundedSyncCommitError(detail, stop=stop)


def _resolve_timed_out_code_commit(
    context: _CodeMergeContext, run_result: CommitCheckRunResult
) -> str:
    """Clear any stale lock, prove published HEAD if landed, else emit typed timeout refusal."""

    clear_stale_git_locks([context.worktree])
    if merge_head(context.worktree) is None:
        current = head_commit(context.worktree)
        if exact_created_head(context.side, current):
            return _verify_published_code_merge(context, current, run_result)
        raise SyncGitProofError(
            f"{context.side.side} merge state disappeared after commit-check timeout "
            f"({run_result.timeout_seconds}s); hook log: {run_result.log.as_posix()}"
        )
    if (
        merge_head(context.worktree) != context.side.sourceCommit
        or head_commit(context.worktree) != context.side.preSyncHead
    ):
        raise SyncGitProofError(
            f"{context.side.side} active merge is no longer the pinned sync merge after "
            f"commit-check timeout; hook log: {run_result.log.as_posix()}"
        )
    mutated = _restore_nonpublishing_code_merge(context)
    detail = format_timeout_outcome(
        run_result.timeout_seconds,
        log_path=run_result.log,
        escaped_survivors=run_result.outcome.escaped_survivors,
    )
    stop = _build_sync_stop(context, run_result, timed_out=True, changed_paths=mutated)
    raise BoundedSyncCommitError(detail, stop=stop)


def commit_bounded_code_merge(side: SyncSideRecord) -> str:
    """Commit a validated code-side sync merge under the shared commit-check bound (L29-R1)."""

    worktree = Path(side.worktree)
    try:
        timeout_seconds = resolve_commit_check_timeout_seconds(worktree)
    except CommitCheckSettingsError as error:
        raise SyncGitProofError(str(error)) from error
    context = _capture_code_merge_context(side)
    commit_args, _stamped = sync_merge_commit_args(
        worktree,
        context.expected_tree,
        fallback_message=f"Merge commit '{side.sourceCommit}'",
        merge_msg_bytes=context.sequencer.get("MERGE_MSG"),
    )
    spec = git_command_spec(worktree, commit_args)
    try:
        run_result = run_bounded_command(
            BoundedCommandSpec(
                repo=worktree,
                argv=spec.argv,
                cwd=spec.work_dir,
                env=spec.environment,
                header=f"sync commit check: side={side.side} bound={timeout_seconds}s\n",
                timeout_s=timeout_seconds,
            )
        )
    except OSError as error:
        raise SyncGitProofError(f"could not start code merge commit: {error}") from error
    if run_result.outcome.timed_out:
        return _resolve_timed_out_code_commit(context, run_result)
    if run_result.outcome.returncode != 0:
        if merge_head(worktree) != side.sourceCommit or head_commit(worktree) != side.preSyncHead:
            raise SyncGitProofError(
                f"{side.side} active merge is no longer the pinned sync merge after failed "
                f"commit; hook log: {run_result.log.as_posix()}"
            )
        mutated = _restore_nonpublishing_code_merge(context)
        stop = _build_sync_stop(context, run_result, timed_out=False, changed_paths=mutated)
        raise BoundedSyncCommitError(extract_commit_check_log_detail(run_result.log), stop=stop)
    result_head = head_commit(worktree)
    if not exact_created_head(side, result_head):
        raise SyncGitProofError(f"{side.side} merge commit does not have the pinned parents")
    return _verify_published_code_merge(context, result_head, run_result)
