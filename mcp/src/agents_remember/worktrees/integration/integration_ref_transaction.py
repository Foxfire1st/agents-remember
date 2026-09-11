"""Exact named-ref preparation and compare-and-swap for integration landings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_ledger import (
    LedgerError,
    LedgerRow,
    MemoryLedger,
    find_mapping,
    parse_ledger_text,
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
                    "memoryRef": commits.ledger,
                },
            },
            observed={},
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


def _integrated_ledger_pair(
    repository: Path,
    ledger_commit: str,
    source_commit: str,
) -> tuple[MemoryLedger, MemoryLedger]:
    blob = run_git(repository, ["show", f"{ledger_commit}:memory.md"])
    if blob.returncode != 0:
        raise RuntimeError("integrated ledger commit has no readable memory.md")
    source_blob = run_git(
        repository,
        ["show", f"{source_commit}:memory.md"],
    )
    if source_blob.returncode != 0:
        raise RuntimeError("exact memory source commit has no readable memory.md")
    try:
        return (
            parse_ledger_text(blob.stdout),
            parse_ledger_text(source_blob.stdout),
        )
    except LedgerError as error:
        raise RuntimeError("integrated memory ledger is invalid") from error


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
    ledger, source_ledger = _integrated_ledger_pair(
        contract.memory_repo_path,
        commits.ledger,
        memory_source_commit,
    )
    mapping = find_mapping(ledger, commits.code)
    if mapping is None or mapping.memory_commit != commits.memory_content:
        raise RuntimeError(
            "integrated memory ledger does not map landed code commit to landed memory content"
        )
    source_mapping = find_mapping(source_ledger, commits.code)
    if source_mapping is not None and source_mapping.memory_commit == commits.memory_content:
        # No-change leaf: the source ledger's current mapping already names the landed memory
        # content, so there is no new ledger row to verify.
        return
    _require_preserved_ledger_history(
        contract,
        ledger,
        source_ledger,
        expected_series_prefix,
        _LedgerLanding(commits.ledger, memory_source_commit),
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


@dataclass(frozen=True)
class _LedgerLanding:
    """The landed ledger commit and the exact memory source it must be based on."""

    ledger_commit: str
    memory_source_commit: str


def _require_preserved_ledger_history(
    contract: WorktreeContract,
    ledger: MemoryLedger,
    source_ledger: MemoryLedger,
    expected_series_prefix: tuple[LedgerRow, ...],
    landing: _LedgerLanding,
) -> None:
    """The source rows survive intact, and every row the leaf added ahead of them is true.

    The count of added rows was previously the safeguard, and it was wrong: one leaf that
    closes out, syncs because its parent moved, and closes out again accumulates two -- both
    closeouts really happened. Each added row is proven instead; see
    ``test_ledger_keeps_every_true_mapping_a_reclosed_leaf_accumulated``.
    """

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
    added = _added_leaf_mappings(ledger.rows, source_ledger.rows)
    if added is None:
        raise RuntimeError(
            _unpreserved_source_history_refusal(
                ledger.rows,
                source_ledger.rows,
                memory_source_commit=landing.memory_source_commit,
            )
        )
    _require_true_added_mappings(contract, added, landing)


def _added_leaf_mappings(
    ledger_rows: list[LedgerRow],
    source_rows: list[LedgerRow],
) -> list[LedgerRow] | None:
    """The mappings this leaf's own closeouts added ahead of its preserved source history.

    The distinction is suffix alignment, not a count: the last ``len(source_rows)`` rows of
    the integrated ledger must be the complete source ledger in order, so every row ahead of
    that tail is a mapping this leaf added. ``None`` means the source rows are not preserved
    that way, which is the refusal case -- the caller reports it instead of guessing an
    alignment.
    """

    preserved = len(source_rows)
    if len(ledger_rows) < preserved:
        return None
    if ledger_rows[len(ledger_rows) - preserved :] != source_rows:
        return None
    return ledger_rows[: len(ledger_rows) - preserved]


def _require_true_added_mappings(
    contract: WorktreeContract,
    added: list[LedgerRow],
    landing: _LedgerLanding,
) -> None:
    """Prove every mapping the leaf added ahead of the source rows is a real one.

    The truth of each row is what replaces the row count as the safeguard: its code commit
    must exist in the code repository, and its memory commit must be an ancestor of the
    landed ledger commit. The refusal names the offending row and the exact remedy; it never
    prints two equal-looking values and refuses anyway.
    """

    assert contract.memory_repo_path is not None
    for row in added:
        if not _code_commit_exists(contract.code_repo_path, row.code_commit):
            raise RuntimeError(
                f"integrated memory ledger adds the mapping {_ledger_row_text(row)}, but code "
                f"commit {row.code_commit} does not exist in the code repository "
                f"{contract.code_repo_path.as_posix()}: an added row must name a commit that "
                "repository really holds. "
                f"{_added_row_remedy(landing.memory_source_commit)}"
            )
        if not is_ancestor(contract.memory_repo_path, row.memory_commit, landing.ledger_commit):
            raise RuntimeError(
                f"integrated memory ledger adds the mapping {_ledger_row_text(row)}, but its "
                f"memory commit {row.memory_commit} is not an ancestor of the landed ledger "
                f"commit {landing.ledger_commit} in "
                f"{contract.memory_repo_path.as_posix()}: an added row must name memory "
                "content the landed ledger commit carries. "
                f"{_added_row_remedy(landing.memory_source_commit)}"
            )


def _added_row_remedy(memory_source_commit: str) -> str:
    return (
        "Remedy: remove that row from memory.md and re-run worktree_closeout_apply for this "
        "contract -- closeout writes only rows it can verify -- and if the file was "
        f"hand-edited, restore it from memory source commit {memory_source_commit} first."
    )


def _unpreserved_source_history_refusal(
    ledger_rows: list[LedgerRow],
    source_rows: list[LedgerRow],
    *,
    memory_source_commit: str,
) -> str:
    """Operator-legible evidence for a ledger that did not keep its source history."""

    return (
        "integrated memory ledger does not preserve the complete source ledger history: its "
        f"last {len(source_rows)} row(s) must be the source ledger, in order, and "
        f"{_source_history_divergence(ledger_rows, source_rows)}. Ledger rows newest-first: "
        f"{_ledger_row_list(ledger_rows)}. Source rows newest-first: "
        f"{_ledger_row_list(source_rows)}. A leaf may prepend any number of its own mappings "
        "ahead of the source rows; no source row may be dropped, reordered, or replaced. "
        "Remedy: run worktree_sync for this contract and re-run worktree_closeout_apply for "
        "it -- the closeout rebuilds the ledger from the exact source -- and if memory.md was "
        f"hand-edited, restore it from memory source commit {memory_source_commit} first."
    )


def _source_history_divergence(
    ledger_rows: list[LedgerRow],
    source_rows: list[LedgerRow],
) -> str:
    """Name the source rows the integrated ledger failed to keep, in row terms."""

    absent = [row for row in source_rows if row not in ledger_rows]
    if absent:
        return f"it is missing {len(absent)} source row(s): {_ledger_row_list(absent)}"
    return "the source rows are present but not as its trailing rows in source order"


def _ledger_row_list(rows: list[LedgerRow]) -> str:
    return "; ".join(_ledger_row_text(row) for row in rows)


def _ledger_row_text(row: LedgerRow) -> str:
    return f"{row.code_commit} -> {row.memory_commit}"


def _code_commit_exists(repository: Path, commit: str) -> bool:
    """Whether the code repository really holds ``commit`` as a commit object."""

    return run_git(repository, ["cat-file", "-e", f"{commit}^{{commit}}"]).returncode == 0


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


def _require_clean_branch_checkout(repo: Path, branch: str, expected: str) -> None:
    for checkout in branch_worktree_owners(repo, branch):
        require_clean(checkout, f"protected ref {branch!r} checkout")
        if head_commit(checkout) != expected:
            raise RuntimeError(
                f"protected ref {branch!r} checkout is not at its expected named-ref tip"
            )
