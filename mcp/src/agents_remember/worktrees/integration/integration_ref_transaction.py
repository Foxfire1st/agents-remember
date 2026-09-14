"""Exact named-ref preparation and compare-and-swap for integration landings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_attribution import (
    code_commit_exists,
)
from agents_remember.kernel.memory_ledger import (
    LedgerError,
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
        if not is_ancestor(contract.memory_repo_path, memory_head_before, commits.ledger):
            raise RuntimeError(
                "integrated memory ledger commit is not a fast-forward from the current "
                "memory branch"
            )
        require_integrated_ledger_mapping(
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


def _integrated_ledger(
    repository: Path,
    ledger_commit: str,
) -> MemoryLedger:
    """The landed ledger at its exact commit, header included, or a legible refusal.

    This is a *validated* read, and it is deliberately the validated one: the header's promise
    that it names the first row is a real protection and it is not about the tracked file, so it
    outlives the file-preservation rule that used to reach it through the projection check. The
    header is not compared against a second table here -- there is no longer a second table in
    this function's world -- it is checked against the row beneath it, which is the one claim the
    format makes about itself.
    """

    blob = run_git(repository, ["show", f"{ledger_commit}:memory.md"])
    if blob.returncode != 0:
        raise RuntimeError("integrated ledger commit has no readable memory.md")
    try:
        return parse_ledger_text(blob.stdout)
    except LedgerError as error:
        raise RuntimeError(
            "the ledger header disagrees with its own first row: "
            f"{error}. Remedy: re-run "
            "worktree_closeout_apply for this contract -- closeout recomputes memory.md from "
            "its source ledger plus the branch's own true mappings, so a malformed or "
            "partially-merged table needs no hand edit."
        ) from error


def require_integrated_ledger_mapping(
    contract: WorktreeContract,
    commits: IntegratedCommits,
    *,
    memory_source_commit: str,
) -> None:
    """Prove the exact landed commits are a landing this route is entitled to publish.

    FIVE promises are checked, and every one of them is about the *commits* rather than about the
    tracked ``memory.md`` table. Each is stated with the clause that enforces it, so the list can
    be read against the body:

    1. the landed ledger maps the landed code commit to the landed memory content, so the pair a
       reader resolves for the landing is the pair this landing created (``find_mapping``);
    2. every row of the landed table is true -- its code commit is one the code repository holds
       and its memory commit is content the landed ledger commit carries -- so a fabricated or
       stale row cannot ride along on a landing whose own pair happens to be correct
       (``_require_true_rows``, which is stricter than promise 1: it judges every row, not the
       landing's own);
    3. the landed memory content is itself reachable from the landed ledger commit, so the ledger
       commit really contains the content it maps;
    4. the landed memory content descends from the exact memory source, so the branch built on the
       source it says it built on;
    5. the ledger's header names its own first row, which the validated read of the landed table
       is what enforces.

    WHAT THIS DELIBERATELY DOES NOT ENFORCE, and the exposure, named rather than implied. The
    landed table is not compared against the tracked source's table -- not its rows, not its
    count, and not its ORDER. That rule protected ``memory.md``, and ``memory.md`` is derived
    state: the projection recomputes it from the memory commits' own ``Code-Commit:``
    attribution, so a table that differs from the file it replaced is the *normal* result of a
    rebuild rather than damage. Holding a landing to the file's row list and row order refused a
    real leaf's ledger repair (13 rows dropped, 455 reordered) that was correct on its own terms,
    and the asymmetry settled it: the checkpoint route already tolerated merge-produced
    interleaving while the leaf route did not, so one table was accepted on one road and refused
    on the other.

    So a KNOWN, DELIBERATE GAP is recorded here instead of a guarantee this function does not
    provide. ``find_mapping`` returns the FIRST row naming a code commit, and two rows for one
    code commit are normal -- a later closeout supersedes an earlier mapping without deleting it.
    A landing that moves the OLDER of such a pair above the newer one therefore changes what that
    code commit resolves to, and nothing here refuses it. Promise 1 still protects the pair this
    landing is about, because that row must resolve to the landed memory content; every OTHER
    code commit the table names is exposed.

    Two facts bound the gap, and both are measured rather than hoped for. The rebuild cannot
    produce such a table: ``_newest_first`` orders every row the projection computes --
    ``test_the_projection_orders_a_superseding_pair_newest_first`` -- so a reversal can only
    arrive from outside it. And where the source's own table already carries one, the projection
    preserves it with the rest of the source's order, because ordering the source's rows is the
    one thing that would make the rebuild, rather than the memory commits, the authority the
    ruling removed.

    The source-ancestry promise is *conditional on the landing not having happened yet*, and that
    condition is what the file rule used to carry without saying so. Once the refs have moved, the
    memory source branch IS the landed ledger commit, so asking whether the memory content descends
    from it asks whether the content descends from the ledger that already contains it -- true, but
    only by the ancestry of promise 4, and unprovable in the other direction when a checked-out
    retry compares the two. A retry that lands the very same pair is the one shape that must
    converge rather than refuse, so the promise is asked exactly while the source is still behind
    the landing it is about to publish.
    """

    if contract.kind not in {"leaf", "series"}:
        raise RuntimeError("integrated memory ledger requires a leaf or series contract")
    assert contract.memory_repo_path is not None
    repository = contract.memory_repo_path
    ledger = _integrated_ledger(repository, commits.ledger)
    mapping = find_mapping(ledger, commits.code)
    if mapping is None or mapping.memory_commit != commits.memory_content:
        raise RuntimeError(
            "integrated memory ledger does not map landed code commit to landed memory content"
        )
    _require_true_rows(contract, ledger, commits.ledger)
    if not is_ancestor(repository, commits.memory_content, commits.ledger):
        raise RuntimeError(
            "integrated memory content commit is not reachable from the landed ledger commit"
        )
    if not is_ancestor(repository, commits.ledger, memory_source_commit) and not is_ancestor(
        repository,
        memory_source_commit,
        commits.memory_content,
    ):
        raise RuntimeError(
            "integrated memory content commit is not based on the exact memory source"
        )


def _require_true_rows(
    contract: WorktreeContract,
    ledger: MemoryLedger,
    ledger_commit: str,
) -> None:
    """Refuse a landed table carrying a row the world contradicts.

    This is not a comparison against the source file: the table is never read for what it
    *should* have said. Each row is checked against the two repositories, which is the same truth
    test the projection applies to every row it keeps. A row naming a code commit the code
    repository does not hold, or memory content the landed ledger commit does not carry, is a
    false entry whether or not a reader ever resolves it -- and the offending row is named, so the
    operator does not have to diff the table to find it. The source row the old file rule would
    have *kept* is refused here only when the world contradicts it, which is exactly the class the
    ruling left standing.

    A memory cell written as an abbreviated object name is read by ancestry rather than compared
    as a string, so a short cell is a fact about the world rather than a malformed row.
    """

    assert contract.memory_repo_path is not None
    for row in ledger.rows:
        if not code_commit_exists(contract.code_repo_path, row.code_commit):
            raise RuntimeError(
                f"the integrated memory ledger row {row.code_commit} -> {row.memory_commit} "
                f"names code commit {row.code_commit}, which the code repository does not hold: "
                "every row of a landed table must be a mapping the repositories really hold"
            )
        if not is_ancestor(contract.memory_repo_path, row.memory_commit, ledger_commit):
            raise RuntimeError(
                f"the integrated memory ledger row {row.code_commit} -> {row.memory_commit} "
                "does not name memory content the landed ledger commit carries: every row of a "
                "landed table must be a mapping the repositories really hold"
            )


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
            ledger_commit=commits.ledger,
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
