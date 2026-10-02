"""Publish external memory content and refresh its disposable ledger view."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory

from agents_remember.kernel.memory_cache import (
    ignore_memory_cache,
    prepare_memory_cache,
    refresh_memory_cache,
)
from agents_remember.models.closeout.input import EffectiveCloseoutInput
from agents_remember.models.memory_content_excludes import (
    MEMORY_CONTENT_EXCLUDES,
)
from agents_remember.worktrees.integration.mutation_evidence import (
    begin_git_mutation,
    ephemeral_git_mutation_snapshot,
    prove_git_commit,
)
from agents_remember.worktrees.knowledge_gate import (
    HistoryClosing,
    close_owner_history,
    closed_out_memory,
    leaf_cutover_refusal,
    leaf_gate_refusal,
    leaf_memory_converted,
    parent_memory_tip,
)
from agents_remember.worktrees.knowledge_validation import PairedCode, memory_commit_refusal
from agents_remember.worktrees.modules.args import WorktreeArgs, report_operation_progress
from agents_remember.worktrees.modules.context import contract_context
from agents_remember.worktrees.modules.git import (
    commit_verified_staged,
    head_commit,
    require_git,
    stage_tree,
    stage_worktree_content,
    worktree_candidate_tree,
    worktree_dirty,
)
from agents_remember.worktrees.modules.models import VerifiedChange
from agents_remember.worktrees.modules.onboarding import (
    contract_memory_verified_commit,
    refresh_entity_fingerprints_for_context,
    refresh_onboarding_metadata,
    refresh_route_indexes_for_context,
    refresh_route_overview_metadata_for_context,
)
from agents_remember.worktrees.queue.closeout_recovery import (
    MemoryCloseoutOutcome,
    resume_external_commits,
)
from agents_remember.worktrees.series_closeout import series_memory_closeout
from agents_remember.worktrees.services import LeafPublication


def external_closeout_commits(
    contract,
    args: WorktreeArgs,
    effective_input: EffectiveCloseoutInput,
    change: VerifiedChange,
) -> MemoryCloseoutOutcome:
    if contract.kind == "series":
        return series_memory_closeout(contract, change.commit)
    if contract.memory_worktree is None:
        raise RuntimeError("external-memory leaf closeout requires a memory worktree")
    recovered = args.recovery_commits
    if recovered is not None and recovered.memoryContentCommit:
        require_gated_recovery(contract, change.commit, recovered.memoryContentCommit)
        return resume_external_commits(
            contract,
            args,
            code_commit=change.commit,
            memory_commit=recovered.memoryContentCommit,
        )
    converted = leaf_memory_converted(contract)
    if not converted:
        # MIK-R09 rule 6: unconverted memory in a repository that holds converted memory is never
        # stamped or committed; it crosses through the crossing sync first.
        locked = leaf_cutover_refusal(contract, "the closeout")
        if locked is not None:
            raise RuntimeError(locked)
    refresh = _refresh_external_memory(contract, args, change)
    closing = None
    if converted:
        # MIK-R07 rule 7 / MIK-R09 rule 3: the memory commit that publishes the leaf closes its
        # history file, creating it with no rows when the leaf wrote none.
        closing = close_owner_history(contract.memory_worktree, _owner(contract))
    memory_commit, created = _commit_memory_content(
        contract,
        args,
        effective_input,
        code_commit=change.commit,
        closing=closing,
    )
    if not created:
        _report_memory_commit(args, change.commit, memory_commit)
    cache = refresh_memory_cache(
        contract.memory_worktree,
        memory_commit,
        path=contract.ledger_path,
        repo_name=contract.repo_name,
    )
    return MemoryCloseoutOutcome(
        memory_commit=memory_commit,
        refreshed_onboarding=refresh.onboarding,
        refreshed_entities=refresh.entities,
        refreshed_route_overviews=refresh.route_overviews,
        route_index_refresh=refresh.route_index,
        ledger_repair=cache,
    )


def _owner(contract) -> str:
    if not contract.leaf_id:
        raise RuntimeError(
            "the mandatory invariant gate (MIK-R09) refuses this closeout: a converted leaf names "
            "its leaf id, the owner of its history file"
        )
    return contract.leaf_id


def _exact_memory_tree(contract, repository: Path) -> str:
    """The exact tree the memory commit records: the working tree with the commit's exclusions."""

    with TemporaryDirectory(
        prefix=".closeout-memory-gate-", dir=contract.worktree_group
    ) as scratch:
        return worktree_candidate_tree(
            repository, Path(scratch) / "index", exclude_paths=MEMORY_CONTENT_EXCLUDES
        )


def _refuse_ungated_memory(contract, code_commit: str, memory_tree: str) -> None:
    """The validator and the mandatory gate over the leaf's exact memory output (MIK-R09 rule 3).

    ``memory_tree`` is the exact tree this closeout commits (or, when nothing is left to commit or a
    closeout is recovered, the tree of the memory commit it records), with the history closing
    applied; ``code_commit`` is the closeout's verified code commit. Both run before any Git
    mutation of the memory side:

    * the knowledge validator (MIK-R22 rule 8) against the parent line's memory tip, as a leaf
      publication, so the file this closeout closes is read whatever its flag;
    * the mandatory invariant gate (MIK-R09): the leaf's worklist recomputed over exactly these two
      trees, every item decided, the run complete. An open item, an incomplete run or a validator
      failure refuses, and the refusal names every finding. Nothing here takes a flag that skips it.

    A history file closed by an earlier, recorded closeout of this leaf that was not integrated
    (:func:`closed_out_memory`) is frozen for both (L37 ruling of 2026-10-01T17:17:07, B).
    """

    repository = contract.memory_worktree
    try:
        tip = parent_memory_tip(contract)
        code_tree = require_git(
            contract.code_worktree, ["rev-parse", "--verify", f"{code_commit}^{{tree}}"]
        )
    except (RuntimeError, subprocess.SubprocessError) as error:  # failed or timed out: named
        raise RuntimeError(
            "the mandatory invariant gate (MIK-R09) and the knowledge validator (MIK-R22) cannot "
            f"read the closeout's sides ({type(error).__name__}: {error}); nothing is committed"
        ) from error
    bases = () if tip is None else (tip,)
    refusal = memory_commit_refusal(
        memory_repository=repository,
        candidate_tree=memory_tree,
        bases=bases,
        paired_code=PairedCode(repository=contract.code_worktree, commit=code_commit),
        leaf_publication=LeafPublication(memory_tree, bases, frozen=closed_out_memory(contract)),
    ) or leaf_gate_refusal(contract, code_tree=code_tree, memory_tree=memory_tree)
    if refusal is not None:
        raise RuntimeError(refusal)


GATE_NOT_APPLICABLE = "not-applicable"
"""What :func:`candidate_gate_verdict` answers for a contract the gate does not govern."""


def candidate_gate_verdict(contract, code_tree: str) -> str | None:
    """The mandatory gate's verdict over the closeout's candidate, before anything is committed.

    ``None`` when it passes, the refusal naming every finding when it does not, and
    :data:`GATE_NOT_APPLICABLE` for a contract the gate does not govern (not a leaf, no memory
    worktree, or unconverted memory). The gate is asked about the accepted code candidate tree and
    the memory worktree's current tree. Nothing is written to either worktree: the gate reads the
    leaf's own history files whatever their ``closed`` flag, so the closing need not be applied
    to ask. The apply's preflight (:func:`refuse_ungated_candidate`) and the preview read this one
    verdict, so a preview cannot promise a closeout the apply refuses; the gate's memo makes the
    second of the two free.
    """

    repository = contract.memory_worktree
    if contract.kind != "leaf" or repository is None or not leaf_memory_converted(contract):
        return GATE_NOT_APPLICABLE
    return leaf_gate_refusal(
        contract, code_tree=code_tree, memory_tree=_exact_memory_tree(contract, repository)
    )


def refuse_ungated_candidate(contract, code_tree: str) -> None:
    """The mandatory gate before the closeout claims its approval or commits anything (MIK-R09).

    The closeout commits code first and memory second. The gate over the exact memory tree runs at
    the memory commit (:func:`_refuse_ungated_memory`) and is the enforcement. This preflight asks
    the same gate (:func:`candidate_gate_verdict`) before either side is committed, so a closeout
    the gate refuses spends no approval and leaves the code uncommitted too. It can only deny;
    what it permits is judged again, on the exact tree, at the memory commit.
    """

    refusal = candidate_gate_verdict(contract, code_tree)
    if refusal is not None and refusal != GATE_NOT_APPLICABLE:
        raise RuntimeError(refusal)


def require_gated_recovery(contract, code_commit: str, memory_commit: str) -> None:
    """A recovered closeout is no way around the gate (MIK-R09 rule 5).

    A closeout that resumes with a memory commit already made records that commit as the leaf's
    memory output. On converted memory the commit's own tree is judged first, exactly as the tree
    of a fresh closeout is: the verdict is a function of the two trees, the contract and the parent
    line's tip, so a commit the gate passed when it was made passes again, and one that was made
    without the gate is refused by name here.
    """

    repository = contract.memory_worktree
    if contract.kind != "leaf" or repository is None or not leaf_memory_converted(contract):
        return
    try:
        memory_tree = require_git(
            repository, ["rev-parse", "--verify", f"{memory_commit}^{{tree}}"]
        )
    except (RuntimeError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            "the mandatory invariant gate (MIK-R09) cannot read the recovered memory commit "
            f"{memory_commit} ({type(error).__name__}: {error}); the closeout is not finalized"
        ) from error
    _refuse_ungated_memory(contract, code_commit, memory_tree)


def _commit_memory_content(
    contract,
    args: WorktreeArgs,
    effective_input: EffectiveCloseoutInput,
    *,
    code_commit: str,
    closing: HistoryClosing | None = None,
) -> tuple[str, bool]:
    """Commit only actual memory changes, binding code attribution inside the commit.

    ``closing`` (converted memory) is the closeout's own closing of the leaf's history file: the
    exact tree is validated and gated before anything is committed
    (:func:`_refuse_ungated_memory`), and a refusal -- or any failure before the commit begins --
    restores the file, so a refused closeout leaves it as the leaf wrote it (L09 review R1,
    finding 2). A converted leaf with nothing left to commit is gated all the same: the commit
    this closeout records as the leaf's memory output is its ``HEAD``, and its tree is judged.

    Every write the closeout itself makes to the memory content is on disk before that tree is
    read: the metadata refresh and the history closing (the caller's), and the ledger cache's
    ignore rule (:func:`ignore_memory_cache`, which may add one line to ``.gitignore``). The tree
    the gate judges is therefore the tree the commit records (L37 P1c, C9). A working tree that
    changed while the gate ran is refused before any Git mutation (:func:`_require_judged_tree`);
    the commit is then staged from the judged tree object itself (:func:`stage_tree`), so nothing
    written after that check can enter it, and its proof expects the judged tree. A refusal
    restores ``.gitignore`` with the closing; the index is not touched until the gate has passed.
    """

    repository = contract.memory_worktree
    assert repository is not None
    ignore = repository / ".gitignore"
    unignored = ignore.read_bytes() if ignore.is_file() else None
    judged = None
    try:
        dirty = worktree_dirty(repository, exclude_paths=MEMORY_CONTENT_EXCLUDES)
        if dirty:
            ignore_memory_cache(repository)
        if closing is not None:
            judged = _exact_memory_tree(contract, repository)
            _refuse_ungated_memory(contract, code_commit, judged)
        if not dirty:
            return head_commit(repository), False
        prepare_memory_cache(repository)
        if judged is not None:
            _require_judged_tree(repository, judged)
    except BaseException:
        if closing is not None:
            closing.restore()
        _restore_ignore_file(ignore, unignored)
        raise
    report_operation_progress(
        args, "memory-commit", current_command="commit verified memory content"
    )
    intent = begin_git_mutation(
        args,
        leg="memory",
        repository=repository,
        expected_output_tree=judged,
        use_current_candidate=judged is None,
    )
    if judged is None:
        stage_worktree_content(repository, exclude_paths=MEMORY_CONTENT_EXCLUDES)
    else:
        # The commit is built from the judged tree object, not from the working tree again: a file
        # written after the check above is not committed, and stays an uncommitted change (R5-3).
        stage_tree(repository, judged)
    committed = commit_verified_staged(
        repository,
        effective_input.memory_content_message(code_commit),
        exclude_paths=MEMORY_CONTENT_EXCLUDES,
    )
    prove_git_commit(args, intent, repository=repository, commit=committed)
    return committed, True


def _require_judged_tree(repository: Path, judged: str) -> None:
    """Refuse a commit whose tree is not the one the gate judged (MIK-R09 rule 3; L37 P1c, C11).

    The gate reads ``judged`` from the working tree through a fresh index, and its evaluation can
    take a minute; the commit then stages through the repository's own index. A file written
    meanwhile, or content staged in that index which the working tree does not hold, would be
    committed without having been judged. The tree the commit is about to record is read here as
    the commit stages it, before any Git mutation, and must be ``judged``.
    """

    staging = ephemeral_git_mutation_snapshot(repository, memory_cache=True).candidateTree
    if staging != judged:
        raise RuntimeError(
            f"the mandatory invariant gate (MIK-R09) judged the memory tree {judged}, but the "
            f"commit would record {staging}: the memory worktree or its index changed while the "
            "gate ran. Nothing is committed; rerun the closeout and the gate judges the tree as "
            "it is now"
        )


def _restore_ignore_file(ignore: Path, previous: bytes | None) -> None:
    """Take back the ignore rule a refused closeout recorded (``None``: there was no file)."""

    if previous is None:
        ignore.unlink(missing_ok=True)
    elif ignore.read_bytes() != previous:
        ignore.write_bytes(previous)


@dataclass(frozen=True)
class _ExternalMemoryRefresh:
    onboarding: list[dict[str, str]]
    entities: list[dict[str, object]]
    route_overviews: list[dict[str, str]]
    route_index: dict[str, object]


def _refresh_external_memory(
    contract,
    args: WorktreeArgs,
    change: VerifiedChange,
) -> _ExternalMemoryRefresh:
    context = replace(contract_context(contract), code_repository_root=contract.code_worktree)
    report_operation_progress(
        args, "memory-refresh", current_command="refresh onboarding and route metadata"
    )
    refreshed_onboarding = refresh_onboarding_metadata(
        contract,
        change,
    )
    refreshed_route_overviews = refresh_route_overview_metadata_for_context(
        context,
        change,
        memory_tree=contract.memory_worktree,
        memory_verified_commit=contract_memory_verified_commit(contract),
    )
    refreshed_entities = refresh_entity_fingerprints_for_context(context, change.changed_paths)
    route_index_refresh = refresh_route_indexes_for_context(context)
    return _ExternalMemoryRefresh(
        refreshed_onboarding,
        refreshed_entities,
        refreshed_route_overviews,
        route_index_refresh,
    )


def _report_memory_commit(args: WorktreeArgs, code_commit: str, memory_commit: str) -> None:
    report_operation_progress(
        args,
        "memory-commit",
        current_command="external memory commit recorded for recovery",
        recovery_commits={
            "codeCommit": code_commit,
            "memoryContentCommit": memory_commit,
        },
    )
