"""Publish external memory content and refresh its disposable ledger view."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
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
from agents_remember.worktrees.disabled_memory import (
    opt_out_detail_for_contract,
)
from agents_remember.worktrees.integration.mutation_evidence import (
    begin_git_mutation,
    ephemeral_git_mutation_snapshot,
    prove_git_commit,
)
from agents_remember.worktrees.knowledge_gate import (
    GateProbeError,
    HistoryClosing,
    close_owner_history,
    closed_out_memory,
    converted_memory,
    leaf_cutover_refusal,
    leaf_gate_refusal,
    leaf_memory_converted,
    parent_memory_tip,
)
from agents_remember.worktrees.knowledge_validation import PairedCode, memory_commit_refusal
from agents_remember.worktrees.modules.args import WorktreeArgs, report_operation_progress
from agents_remember.worktrees.modules.context import contract_context
from agents_remember.worktrees.modules.git import (
    changed_worktree_paths,
    committed_changed_paths,
    local_branch_ref,
    publish_tree_commit,
    require_ancestor,
    require_git,
    require_readable_commit,
    worktree_candidate_tree,
    worktree_dirty,
)
from agents_remember.worktrees.modules.models import VerifiedChange
from agents_remember.worktrees.modules.onboarding import (
    entities_catalog_path,
    entity_evidence_at_base,
    entity_fingerprint_plan_problems,
    entity_fingerprint_refresh_plan_for_context,
    refresh_entity_fingerprints_for_context,
    refresh_route_indexes_for_context,
    route_index_refresh_problems,
)
from agents_remember.worktrees.queue.closeout_recovery import (
    MemoryCloseoutOutcome,
    observe_admitted_tip,
    require_admitted_tip,
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
    try:
        if recovered is not None and recovered.memoryContentCommit:
            require_gated_recovery(contract, change.commit, recovered.memoryContentCommit)
            return resume_external_commits(
                contract,
                args,
                code_commit=change.commit,
                memory_commit=recovered.memoryContentCommit,
            )
        if not leaf_memory_converted(contract):
            locked = leaf_cutover_refusal(contract, "the closeout")
            if locked is not None:
                raise RuntimeError(locked)
        refresh = _refresh_external_memory(contract, args, change)
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
    except RuntimeError as error:
        # MIK-R52 rule 9: the code commit is already published when the memory phase runs, so
        # every refusal from it says what is true.
        raise RuntimeError(_published_code_commit_detail(change.commit, str(error))) from error


def _published_code_commit_detail(code_commit: str, detail: str) -> str:
    """Say what is true in a closeout refusal raised after the code commit (MIK-R52 rule 9).

    The code commit is published and nothing is integrated; rerunning the closeout completes the
    pair. A detail that already says so is left alone.
    """

    marker = "rerunning the closeout completes the pair"
    if marker in detail:
        return detail
    return (
        f"{detail}; code commit {code_commit} is published and nothing is integrated: "
        f"rerunning the closeout completes the pair"
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


def closeout_changed_paths(contract) -> dict[str, list[str]]:
    """Closeout worklist: working-tree changes plus the unverified committed range.

    ``committed`` covers commits the task transports (merges, pre-committed
    slices) that no previous closeout verified; ``working`` keeps the strict
    dirty-tree tier. A path in both tiers counts as working.
    """
    if contract.kind == "series":
        return {"all": [], "working": [], "committed": []}
    working = changed_worktree_paths(contract.code_worktree)
    committed = committed_changed_paths(
        contract.code_worktree, contract.code_base_commit, contract.code_commit
    )
    committed_only = sorted(set(committed) - set(working))
    return {
        "all": sorted({*working, *committed_only}),
        "working": working,
        "committed": committed_only,
    }


def candidate_memory_refresh_refusal(contract, code_tree: str) -> str | None:
    """Every memory-refresh refusal the candidate and the memory worktree alone decide.

    MIK-R178 rule 1: the closeout's candidate verdict, which the preview and the apply's
    preflight both read, asks the memory refresh's own refusals over the accepted code candidate
    tree and the memory worktree's tree. Both refresh legs are asked, because both can refuse on
    nothing but those two trees: the entity leg (every row's evidence, an unsupported algorithm, a
    missing fingerprint value -- rules 1-2) and the route-index leg (a cache or route overview it
    cannot read or render -- L176-R1-F02). Asking only one leg leaves the other to fail after the
    code commit, which is the half-landed leaf the requirement forbids. The checks read the
    candidate by its id, so they depend on nothing the closeout writes later. ``None`` when the
    refresh can run from this candidate; a refusal names its cause, row, path and step (rule 2).
    """

    context = replace(contract_context(contract), code_repository_root=contract.code_worktree)
    changed_paths = closeout_changed_paths(contract)["all"]
    try:
        base_evidence = entity_evidence_at_base(
            context.onboarding_root, contract.memory_base_commit
        )
        plan = entity_fingerprint_refresh_plan_for_context(
            context, changed_paths, base_evidence=base_evidence
        )
        lines = entity_fingerprint_plan_problems(context, plan, code_tree=code_tree)
        lines.extend(route_index_refresh_problems(context))
    except (RuntimeError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            "the closeout's candidate verdict cannot ask the memory refresh: step "
            f"candidate memory refresh preflight, {entities_catalog_path(context).as_posix()} "
            f"against code candidate tree {code_tree} ({type(error).__name__}: {error})"
        ) from error
    if not lines:
        return None
    header = (
        "external-memory closeout refuses this candidate before the code commit: the memory "
        f"refresh cannot run from the code candidate tree {code_tree}"
    )
    return "\n".join([header, *(f"- {line}" for line in lines)])


def _series_format_verdict(contract) -> str:
    """The format verdict for a series/master contract on external memory (MIK-R46 rule 1).

    The invariant gate does not govern series contracts, but an external series whose
    own line holds the layout marker on neither its work tip nor its source tip is
    refused with the one ``legacy-format`` message, never passed through. A probe Git
    cannot answer refuses by name. Callers translate a non-``None`` answer into their
    route's refusal before anything moves.
    """

    repository = contract.memory_repo_path
    assert repository is not None
    work = contract.memory_work_branch
    source = contract.memory_source_branch
    try:
        work_ref = local_branch_ref(work) if work else ""
        source_ref = local_branch_ref(source) if source else ""
        if converted_memory(repository, work_ref, source_ref):
            return GATE_NOT_APPLICABLE
    except (GateProbeError, RuntimeError) as error:
        return str(error)
    return leaf_cutover_refusal(contract, "the closeout validator") or GATE_NOT_APPLICABLE


def candidate_gate_verdict(contract, code_tree: str) -> str | None:
    """The mandatory gate's verdict over the closeout's candidate, before anything is committed.

    ``None`` when it passes, the refusal naming every finding when it does not, and
    :data:`GATE_NOT_APPLICABLE` for a contract the gate does not govern (not a leaf, no memory
    worktree, or no external memory). An external leaf's unconverted memory is refused by the
    cutover lock (MIK-R46 rule 1), never passed through: the gate is asked about the accepted
    code candidate tree and the memory worktree's current tree. Nothing is written to either
    worktree: the gate reads the leaf's own history files whatever their ``closed`` flag, so the
    closing need not be applied to ask. The apply's preflight (:func:`refuse_ungated_candidate`)
    and the preview read this one verdict, so a preview cannot promise a closeout the apply
    refuses; the gate's memo makes the second of the two free.

    MIK-R178 rule 1: the verdict also asks every refusal the closeout's memory refresh reaches
    from the code candidate tree and the memory worktree's tree alone
    (:func:`candidate_memory_refresh_refusal`), so a leaf that deletes a file an entity row cites
    is stopped here, before the code commit, with the catalog file, the row and the path named.
    """

    repository = contract.memory_worktree
    if contract.memory_mode != "external":
        # A contract that carries the removed mode never reaches the gate's
        # trees: the verdict itself refuses it by name (L50-CODE-R1-F02), so a
        # direct caller meets the opt-out refusal rather than not-applicable.
        # Series contracts keep GATE_NOT_APPLICABLE; an external unconverted leaf is refused
        # by the cutover lock above (MIK-R46 rule 1), never passed through.
        return opt_out_detail_for_contract(contract, "worktree_closeout")
    if contract.kind != "leaf" or repository is None:
        # The gate does not govern series contracts -- but an external series on
        # unconverted memory is refused with the one legacy-format message
        # (MIK-R46 rule 1), never passed through as not-applicable. A series
        # whose own line already holds the marker converts or is converted, so
        # the verdict stays not-applicable for it.
        if contract.memory_mode == "external" and contract.memory_repo_path is not None:
            return _series_format_verdict(contract)
        return GATE_NOT_APPLICABLE
    if not leaf_memory_converted(contract):
        return leaf_cutover_refusal(contract, "the closeout validator")
    gate = leaf_gate_refusal(
        contract, code_tree=code_tree, memory_tree=_exact_memory_tree(contract, repository)
    )
    if gate is not None:
        return gate
    return candidate_memory_refresh_refusal(contract, code_tree)


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


def _frozen_recorded_closeout(contract, repository: Path, memory_commit: str) -> tuple[str, ...]:
    """Commits whose closed history files stay frozen for one judged leaf pair (MIK-R52 rule 1).

    Only the contract's recorded closeout freezes anything: when the judged memory commit is
    that commit or descends from it, the files closed in its parents stay frozen and everything
    the judged commit adds is checked. Any other judged commit -- a hand-made commit elsewhere,
    a commit outside the leaf -- freezes nothing. A recorded commit Git cannot read refuses the
    judgment as an unreadable input.
    """

    recorded = closed_out_memory(contract)
    if not recorded:
        return ()
    try:
        require_git(repository, ["rev-parse", "--verify", recorded[0]])
    except (RuntimeError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            "the mandatory invariant gate (MIK-R52 rule 1) cannot read the leaf's recorded "
            f"closeout memory commit {recorded[0]} ({type(error).__name__}: {error}); "
            "the pair is not judged"
        ) from error
    try:
        require_readable_commit(repository, memory_commit, what="the judged memory commit")
        descends = memory_commit == recorded[0] or require_ancestor(
            repository,
            recorded[0],
            memory_commit,
            what=(
                "the ancestry of the leaf's recorded closeout memory commit "
                f"{recorded[0]} to the judged memory commit {memory_commit}"
            ),
        )
    except (RuntimeError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            "the mandatory invariant gate (MIK-R52 rule 1) cannot read the judged pair's "
            f"ancestry ({type(error).__name__}: {error}); the pair is not judged"
        ) from error
    if not descends:
        return ()
    try:
        parents = require_git(repository, ["rev-list", "--parents", "-n", "1", recorded[0]])
    except (RuntimeError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            "the mandatory invariant gate (MIK-R52 rule 1) cannot read the parents of the "
            f"leaf's recorded closeout memory commit {recorded[0]} "
            f"({type(error).__name__}: {error}); the pair is not judged"
        ) from error
    _, *made_on = parents.split()
    return tuple(made_on)


def leaf_pair_refusal(
    contract,
    *,
    code_commit: str,
    memory_commit: str,
    bases: Sequence[str],
    persist_worklist: bool = True,
) -> str | None:
    """Judge a leaf's code and memory commits exactly as the closeout judges its candidate.

       MIK-R52 rule 1: the knowledge validator as a leaf publication over the two commits with the
       caller's ``bases``, then the mandatory gate with the worklist recomputed over the two
    commits' trees and the contract's base pair. ``None`` when the pair passes; the refusal
    naming every finding when it does not. A side Git cannot read refuses the judgment as an
    unreadable input, and nothing is landed or recorded. Only the contract's recorded closeout
    freezes history files (:func:`_frozen_recorded_closeout`).

    The repositories are resolved as the landing routes resolve them: the memory and code repo
    paths when set, else the route's live worktrees.
    """

    repository = contract.memory_repo_path or contract.memory_worktree
    code_repository = contract.code_repo_path or contract.code_worktree
    try:
        code_tree = require_git(
            code_repository, ["rev-parse", "--verify", f"{code_commit}^{{tree}}"]
        )
        memory_tree = require_git(
            repository, ["rev-parse", "--verify", f"{memory_commit}^{{tree}}"]
        )
    except (RuntimeError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            "the mandatory invariant gate (MIK-R52 rule 1) cannot read the judged pair "
            f"({type(error).__name__}: {error}); nothing is landed or recorded"
        ) from error
    frozen = _frozen_recorded_closeout(contract, repository, memory_commit)
    return memory_commit_refusal(
        memory_repository=repository,
        candidate_tree=memory_commit,
        bases=tuple(bases),
        paired_code=PairedCode(repository=code_repository, commit=code_commit),
        leaf_publication=LeafPublication(memory_commit, tuple(bases), frozen=frozen),
    ) or leaf_gate_refusal(
        contract, code_tree=code_tree, memory_tree=memory_tree, persist_worklist=persist_worklist
    )


def require_gated_recovery(contract, code_commit: str, memory_commit: str) -> None:
    """A recovered closeout is no way around the gate (MIK-R09 rule 5).

    A closeout that resumes with a memory commit already made records that commit as the leaf's
    memory output. On converted memory the recovered pair is judged by MIK-R52 rule 1
    (:func:`leaf_pair_refusal`) with the parent line's memory tip as the validator's base, exactly
    as the tree of a fresh closeout is judged: the verdict is a function of the two commits, the
    contract and the parent line's tip, so a pair the gate passed when it was made passes again,
    and one that was made without the gate is refused by name here.
    """

    repository = contract.memory_worktree
    if contract.kind != "leaf" or repository is None or not leaf_memory_converted(contract):
        return
    try:
        tip = parent_memory_tip(contract)
    except (RuntimeError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            "the mandatory invariant gate (MIK-R09) cannot read the parent memory line "
            f"({type(error).__name__}: {error}); the closeout is not finalized"
        ) from error
    refusal = leaf_pair_refusal(
        contract,
        code_commit=code_commit,
        memory_commit=memory_commit,
        bases=() if tip is None else (tip,),
    )
    if refusal is not None:
        raise RuntimeError(refusal)


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
    (:func:`_refuse_ungated_memory`). A converted leaf with nothing left to commit is gated all
    the same: the commit this closeout records as the leaf's memory output is its ``HEAD``, and
    its tree is judged.

    Every write the closeout itself makes to the memory content is on disk before that tree is
    read: the metadata refresh and the history closing (the caller's), and the ledger cache's
    ignore rule (:func:`ignore_memory_cache`, which may add one line to ``.gitignore``). The tree
    the gate judges is therefore the tree the commit records. A working tree that
    changed while the gate ran is refused before any Git mutation (:func:`_require_judged_tree`);
    the commit is then staged from the judged tree object itself (:func:`stage_tree`) and published
    from that tree (:func:`publish_tree_commit`), so nothing written after that check can enter it
    and nothing written later refuses it; its proof expects the judged tree.

    **The publication is bound to what the closeout admitted**: the
    contract's memory work branch, and the commit that branch named before the tree to commit was
    read (:func:`observe_admitted_tip`). A ``HEAD`` switched to another branch, a detached
    ``HEAD`` or a branch moved by someone else's commit between that reading and the publication
    refuses; nothing is committed on any branch.

    **One restoring block** runs from the first preparation until publication returns:
    whatever refuses or fails in between (the gate, a changed tree, a ``HEAD`` that is not the
    admitted branch, a journal that cannot record the intent, a moved branch, an unfinished merge,
    a Git failure) leaves the leaf's history file open again and the ignore rule taken back, each
    only while the file still holds exactly what the closeout wrote; the publication itself puts
    the index back to the tree it held when it began (:func:`publish_tree_commit`). A partial
    publication can raise after the branch moves: if ``HEAD`` switches and the expected-old
    takeback fails, the published commit can remain reachable while these preparations restore.
    After publication returns successfully, its proof runs outside this restoring block; a
    failed proof leaves the preparations in place and is completed by the rerun, which finds
    nothing left to commit.
    """

    repository = contract.memory_worktree
    assert repository is not None
    ignore = repository / ".gitignore"
    preparations: list[HistoryClosing | None] = [closing]
    try:
        unignored = ignore.read_bytes() if ignore.is_file() else None
        admitted = observe_admitted_tip(repository, contract.memory_work_branch, side="memory")
        dirty = worktree_dirty(repository, exclude_paths=MEMORY_CONTENT_EXCLUDES)
        if dirty:
            try:
                ignore_memory_cache(repository)
            finally:
                written = ignore.read_bytes() if ignore.is_file() else None
                if written != unignored:
                    preparations.append(HistoryClosing(ignore, unignored, written))
        judged = _exact_memory_tree(contract, repository)
        _refuse_ungated_memory(contract, code_commit, judged)
        # The gate can take a minute: the checkout is compared with what was admitted as soon as
        # it has answered, before anything more is prepared or an intent is recorded, and again
        # below on the very snapshot the publication is bound to.
        require_admitted_tip(repository, admitted)
        if not dirty:
            # Nothing to commit: the memory output this closeout records is the admitted tip.
            assert admitted.head is not None
            return admitted.head, False
        prepare_memory_cache(repository)
        _require_judged_tree(repository, judged)
        report_operation_progress(
            args, "memory-commit", current_command="commit verified memory content"
        )
        intent = begin_git_mutation(
            args,
            leg="memory",
            repository=repository,
            expected_output_tree=judged,
            use_current_candidate=False,
        )
        assert intent.before is not None
        require_admitted_tip(repository, admitted, snapshot=intent.before)
        # With a judged tree the commit is that tree object, not the working tree again: a file
        # written after the check above is not committed, does not refuse the closeout and stays
        # an uncommitted change. The publication stages, publishes and, on any refusal,
        # gives the index back. Unconverted memory refuses before this publication.
        committed = publish_tree_commit(
            repository,
            effective_input.memory_content_message(code_commit),
            tree=judged,
            before=intent.before,
        )
    except BaseException as error:
        skipped = _restore_preparations(*preparations)
        if skipped and isinstance(error, RuntimeError):
            names = ", ".join(skipped)
            raise RuntimeError(
                f"{error}; the closeout left {names} as it found it: the file no longer holds "
                "the bytes the closeout wrote, so the takeback did not touch it"
            ) from error
        raise
    prove_git_commit(args, intent, repository=repository, commit=committed)
    return committed, True


def _restore_preparations(*preparations: HistoryClosing | None) -> list[str]:
    """Take back each preparation, naming the files an edit left in place (MIK-R52 rule 9)."""

    skipped: list[str] = []
    for preparation in preparations:
        if preparation is not None and not preparation.restore():
            skipped.append(preparation.path.as_posix())
    return skipped


def _require_judged_tree(repository: Path, judged: str) -> None:
    """Refuse a commit whose tree is not the one the gate judged (MIK-R09 rule 3).

    The gate reads ``judged`` from the working tree through a copy of the worktree's own index (a
    capture that starts from that index and rehashes what it must), and its evaluation can take a
    minute; the commit then stages from the repository's own index. A file written meanwhile, or
    content staged in that index which the working tree does not hold, would be committed without
    having been judged. The tree the commit is about to record is read here as the commit stages
    it, before any Git mutation, and must be ``judged``. What is written after this check does not
    enter the commit and does not refuse it (:func:`publish_tree_commit`).
    """

    staging = ephemeral_git_mutation_snapshot(repository, memory_cache=True).candidateTree
    if staging != judged:
        raise RuntimeError(
            f"the mandatory invariant gate (MIK-R09) judged the memory tree {judged}, but the "
            f"commit would record {staging}: the memory worktree or its index changed while the "
            "gate ran. Nothing is committed; rerun the closeout and the gate judges the tree as "
            "it is now"
        )


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
    refreshed_entities = refresh_entity_fingerprints_for_context(
        context,
        change.changed_paths,
        code_commit=change.commit,
        memory_base_commit=contract.memory_base_commit,
    )
    route_index_refresh = refresh_route_indexes_for_context(context)
    return _ExternalMemoryRefresh(
        [],
        refreshed_entities,
        [],
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
