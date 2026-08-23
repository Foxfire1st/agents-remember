"""Durable closeout Git-intent publication and crash reconciliation."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path
from typing import Literal, cast

from agents_remember.kernel.git_command import (
    IsolatedGitState,
    run_git,
    run_git_with_isolated_index_and_objects,
)
from agents_remember.models.closeout_input import EffectiveCloseoutInput
from agents_remember.models.lifecycles.mutation_evidence import (
    CloseoutMutationLeg,
    GitMutationEvidence,
    GitMutationSnapshot,
)
from agents_remember.models.lifecycles.operation import (
    CloseoutOperationInput,
    LifecycleOperationRecord,
)
from agents_remember.worktrees.modules.args import WorktreeArgs, report_operation_progress
from agents_remember.worktrees.modules.git import (
    head_commit,
    require_git,
    worktree_candidate_tree,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract

JOURNALED_CLOSEOUT_REQUIRED = (
    "worktree closeout mutation requires the journaled "
    "worktree_closeout_apply operation; synchronous apply is not authorized"
)


def require_closeout_mutation_authority(args: WorktreeArgs) -> None:
    """Refuse applying closeout when mutation evidence cannot be durable."""
    if args.operation_progress is None:
        raise RuntimeError(JOURNALED_CLOSEOUT_REQUIRED)


def initial_closeout_mutation_evidence(
    contract: WorktreeContract,
    effective_input: EffectiveCloseoutInput,
) -> dict[CloseoutMutationLeg, GitMutationEvidence]:
    """Create pre-mutation cells for exactly the enabled repository legs."""
    repositories = _contract_repositories(contract)
    evidence: dict[CloseoutMutationLeg, GitMutationEvidence] = {}
    for leg in ("code", "memory", "ledger"):
        if not effective_input.enabled(leg):
            continue
        repository = cast(Path, repositories[leg])
        accepted = git_mutation_snapshot(
            repository,
            contract.worktree_group / "reports" / f".{leg}-admission.index",
        )
        evidence[leg] = GitMutationEvidence(
            leg=leg,
            repository=repository.as_posix(),
            acceptedBefore=accepted,
        )
    return evidence


def begin_git_mutation(
    args: WorktreeArgs,
    *,
    leg: CloseoutMutationLeg,
    repository: Path,
    expected_output_tree: str | None,
    use_current_candidate: bool = False,
) -> GitMutationEvidence:
    """Persist exact pre-command facts before a commit command can launch."""
    require_closeout_mutation_authority(args)
    _require_mutation_leg_authority(args, leg, repository)
    before = git_mutation_snapshot(repository, _evidence_index_path(args, leg))
    return _publish_mutation_intent(
        args,
        leg=leg,
        repository=repository,
        before=before,
        expected_output_tree=(
            before.candidateTree if use_current_candidate else expected_output_tree
        ),
    )


def begin_exact_file_git_mutation(
    args: WorktreeArgs,
    *,
    leg: CloseoutMutationLeg,
    repository: Path,
    path: Path,
    intended_text: str,
) -> GitMutationEvidence:
    """Persist an exact file-output tree before touching the real worktree/index."""

    require_closeout_mutation_authority(args)
    _require_mutation_leg_authority(args, leg, repository)
    before = git_mutation_snapshot(repository, _evidence_index_path(args, leg))
    expected_output_tree = _isolated_file_candidate_tree(
        repository,
        before=before,
        path=path,
        intended_text=intended_text,
    )
    return _publish_mutation_intent(
        args,
        leg=leg,
        repository=repository,
        before=before,
        expected_output_tree=expected_output_tree,
    )


def _publish_mutation_intent(
    args: WorktreeArgs,
    *,
    leg: CloseoutMutationLeg,
    repository: Path,
    before: GitMutationSnapshot,
    expected_output_tree: str | None,
) -> GitMutationEvidence:
    evidence = GitMutationEvidence(
        leg=leg,
        repository=repository.as_posix(),
        state="mutation-intent",
        acceptedBefore=_accepted_prestate(args, leg),
        before=before,
        expectedOutputTree=expected_output_tree,
    )
    _report_evidence(args, evidence)
    return evidence


def _isolated_file_candidate_tree(
    repository: Path,
    *,
    before: GitMutationSnapshot,
    path: Path,
    intended_text: str,
) -> str:
    """Compute an exact candidate tree without touching the repository's mutable state."""

    try:
        relative = path.resolve().relative_to(repository.resolve()).as_posix()
    except ValueError as exc:
        raise RuntimeError("journaled mutation file is outside its accepted repository") from exc
    common_dir = Path(
        require_git(
            repository,
            ["rev-parse", "--path-format=absolute", "--git-common-dir"],
        )
    )
    alternate = common_dir / "objects"
    clean_status = hashlib.sha256(b"").hexdigest()
    if (
        before.indexTree != before.headTree
        or before.candidateTree != before.headTree
        or before.statusFingerprint != clean_status
    ):
        raise RuntimeError("exact file mutation requires a clean accepted HEAD tree before intent")
    tree_entry = require_git(repository, ["ls-tree", before.headTree, "--", relative])
    if not tree_entry:
        raise RuntimeError("journaled mutation file is absent from the accepted index tree")
    mode = tree_entry.split(maxsplit=1)[0]
    with tempfile.TemporaryDirectory(prefix="ar-mutation-tree-") as temp_dir:
        root = Path(temp_dir)
        execution = IsolatedGitState(root / "index", root / "objects", alternate)
        _require_isolated_git(
            repository,
            ["read-tree", before.headTree],
            state=execution,
        )
        blob = _require_isolated_git(
            repository,
            ["hash-object", "-w", "--stdin"],
            input_text=intended_text,
            state=execution,
        )
        _require_isolated_git(
            repository,
            ["update-index", "--add", "--cacheinfo", f"{mode},{blob},{relative}"],
            state=execution,
        )
        return _require_isolated_git(repository, ["write-tree"], state=execution)


def _require_isolated_git(
    repository: Path,
    arguments: list[str],
    *,
    state: IsolatedGitState,
    input_text: str | None = None,
) -> str:
    result = run_git_with_isolated_index_and_objects(
        repository,
        arguments,
        state=state,
        input_text=input_text,
    )
    if result.returncode != 0:
        raise RuntimeError("isolated Git mutation evidence operation failed")
    return result.stdout.strip()


def _accepted_prestate(
    args: WorktreeArgs,
    leg: CloseoutMutationLeg,
) -> GitMutationSnapshot | None:
    if args.contract_path is None or not args.operation_key:
        return None
    contract = load_contract(args.contract_path)
    from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (  # noqa: PLC0415
        located_lifecycle_operation_store,
    )

    for kind in ("closeout", "direct-landing"):
        record = located_lifecycle_operation_store(contract, kind).read()
        if record is not None and record.operationKey == args.operation_key:
            evidence = record.mutationEvidence.get(leg)
            return evidence.acceptedBefore if evidence is not None else None
    return None


def bind_expected_output_tree(
    args: WorktreeArgs,
    evidence: GitMutationEvidence,
    *,
    repository: Path,
) -> GitMutationEvidence:
    """Bind a prepared output tree after intent, but still before commit launch."""
    require_closeout_mutation_authority(args)
    _require_mutation_leg_authority(args, evidence.leg, repository)
    _require_evidence_repository(evidence, repository)
    if evidence.state != "mutation-intent" or evidence.expectedOutputTree is not None:
        raise RuntimeError("closeout mutation output tree can only fill pending intent")
    updated = evidence.model_copy(
        update={
            "expectedOutputTree": worktree_candidate_tree(
                repository, _evidence_index_path(args, evidence.leg)
            )
        }
    )
    _report_evidence(args, updated)
    return updated


def prove_git_commit(
    args: WorktreeArgs,
    evidence: GitMutationEvidence,
    *,
    repository: Path,
    commit: str,
) -> None:
    """Publish a commit hash only after Git output matches the bound tree."""
    require_closeout_mutation_authority(args)
    _require_mutation_leg_authority(args, evidence.leg, repository)
    _require_evidence_repository(evidence, repository)
    if evidence.before is None:
        raise RuntimeError("closeout mutation proof requires pre-command Git evidence")
    actual = git_mutation_snapshot(repository, _evidence_index_path(args, evidence.leg))
    expected_tree = evidence.expectedOutputTree
    if expected_tree is None or not commit_matches_intent(
        repository,
        before=evidence.before,
        actual=actual,
        expected_tree=expected_tree,
        commit=commit,
    ):
        raise RuntimeError(
            f"closeout {evidence.leg} commit does not match its mutation-intent ref/tree"
        )
    _report_evidence(
        args,
        evidence.model_copy(
            update={"state": "commit-proven", "observed": actual, "commit": commit}
        ),
    )


def reconcile_closeout_mutations(
    record: LifecycleOperationRecord,
    *,
    temporary_indices: bool = False,
    purpose: Literal["recovery", "cancellation"] = "recovery",
) -> dict[CloseoutMutationLeg, GitMutationEvidence]:
    """Resolve launched-without-hash attempts without guessing from HEAD alone."""
    _require_record_repositories(record)
    reconciled = dict(record.mutationEvidence)
    for leg, evidence in record.mutationEvidence.items():
        if (
            evidence.state != "mutation-intent"
            or evidence.before is None
            or evidence.observed is not None
        ):
            continue
        try:
            repository = Path(evidence.repository)
            actual = _reconciliation_snapshot(
                record,
                leg,
                repository,
                temporary_index=temporary_indices,
            )
            if actual == evidence.before:
                if _preserve_pending_external_ledger_intent(
                    record,
                    leg,
                    evidence,
                    purpose=purpose,
                ):
                    # Code and memory are already durable output.  The exact
                    # journal-before-ledger-write cut therefore remains recovery
                    # authority until that same generation writes or commits the
                    # deterministic ledger output.  A status read must not turn it
                    # back into a cancellable pre-output attempt.
                    continue
                reconciled[leg] = evidence.model_copy(
                    update={"state": "reconciled-unchanged", "observed": actual}
                )
                continue
            expected_tree = evidence.expectedOutputTree
            if expected_tree is None or actual.head == evidence.before.head:
                # Prepared or dirty intermediate state is live recovery evidence,
                # not a durable mutation-cell transition.  Leaving the intent
                # untouched lets the same generation finish and publish the first
                # durable output proof after a crash.
                continue
            if commit_matches_intent(
                repository,
                before=evidence.before,
                actual=actual,
                expected_tree=expected_tree,
                commit=actual.head,
            ):
                reconciled[leg] = evidence.model_copy(
                    update={
                        "state": "commit-proven",
                        "observed": actual,
                        "commit": actual.head,
                    }
                )
            else:
                # Conflicting live state is reported by the typed recovery path;
                # it must not freeze an ambiguous observation into the intent.
                continue
        except (OSError, RuntimeError):
            # Missing or unreadable facts remain ambiguous and same-generation.
            continue
    return reconciled


def _preserve_pending_external_ledger_intent(
    record: LifecycleOperationRecord,
    leg: CloseoutMutationLeg,
    evidence: GitMutationEvidence,
    *,
    purpose: Literal["recovery", "cancellation"],
) -> bool:
    """Keep the one post-output ordinary-ledger intent visible to recovery."""

    commits = record.recoveryCommits
    operation_input = record.input
    code_evidence = record.mutationEvidence.get("code")
    memory_evidence = record.mutationEvidence.get("memory")
    return bool(
        purpose == "recovery"
        and record.operationKind == "closeout"
        and isinstance(operation_input, CloseoutOperationInput)
        and operation_input.effectiveInput.memoryMode == "external"
        and operation_input.effectiveInput.enabled("ledger")
        and leg == "ledger"
        and evidence.state == "mutation-intent"
        and evidence.before is not None
        and evidence.observed is None
        and evidence.expectedOutputTree
        and record.irreversibleBoundaryEntered
        and commits is not None
        and code_evidence is not None
        and code_evidence.state == "commit-proven"
        and code_evidence.commit == commits.codeCommit
        and memory_evidence is not None
        and memory_evidence.state == "commit-proven"
        and memory_evidence.commit == commits.memoryContentCommit
        and not commits.ledgerCommit
    )


def _reconciliation_snapshot(
    record: LifecycleOperationRecord,
    leg: CloseoutMutationLeg,
    repository: Path,
    *,
    temporary_index: bool,
) -> GitMutationSnapshot:
    if not temporary_index:
        return git_mutation_snapshot(repository, _record_index_path(record, leg))
    return ephemeral_git_mutation_snapshot(repository)


def closeout_requires_recovery(record: LifecycleOperationRecord) -> bool:
    """Whether closeout evidence forbids replacement or cancellation."""
    return any(
        evidence.state in {"mutation-intent", "commit-proven"}
        for evidence in record.mutationEvidence.values()
    )


def closeout_cancellable(record: LifecycleOperationRecord) -> bool:
    return not closeout_requires_recovery(record)


def git_mutation_snapshot(repository: Path, index_path: Path) -> GitMutationSnapshot:
    status_result = run_git(repository, ["status", "--porcelain=v1", "-z"])
    if status_result.returncode != 0:
        raise RuntimeError("Git status mutation evidence is unreadable")
    status = status_result.stdout
    head_ref = require_git(repository, ["symbolic-ref", "--quiet", "HEAD"])
    return GitMutationSnapshot(
        headRef=head_ref,
        head=head_commit(repository),
        headTree=require_git(repository, ["rev-parse", "HEAD^{tree}"]),
        refLogFingerprint=_ref_log_fingerprint(repository, head_ref),
        indexTree=require_git(repository, ["write-tree"]),
        candidateTree=worktree_candidate_tree(repository, index_path),
        statusFingerprint=hashlib.sha256(status.encode("utf-8")).hexdigest(),
    )


def ephemeral_git_mutation_snapshot(repository: Path) -> GitMutationSnapshot:
    """Read the exact snapshot through disposable index and object storage."""

    status_result = run_git(repository, ["status", "--porcelain=v1", "-z"])
    if status_result.returncode != 0:
        raise RuntimeError("Git status mutation evidence is unreadable")
    head_ref = require_git(repository, ["symbolic-ref", "--quiet", "HEAD"])
    head = head_commit(repository)
    head_tree = require_git(repository, ["rev-parse", "HEAD^{tree}"])
    common_dir = Path(
        require_git(
            repository,
            ["rev-parse", "--path-format=absolute", "--git-common-dir"],
        )
    )
    source_index = Path(
        require_git(
            repository,
            ["rev-parse", "--path-format=absolute", "--git-path", "index"],
        )
    )
    with tempfile.TemporaryDirectory(prefix="ar-mutation-snapshot-") as temp_dir:
        root = Path(temp_dir)
        index_path = root / "index"
        if source_index.exists():
            shutil.copyfile(source_index, index_path)
        execution = IsolatedGitState(index_path, root / "objects", common_dir / "objects")
        if not source_index.exists():
            _require_isolated_git(repository, ["read-tree", head_tree], state=execution)
        index_tree = _require_isolated_git(repository, ["write-tree"], state=execution)
        _require_isolated_git(repository, ["add", "-A"], state=execution)
        candidate_tree = _require_isolated_git(repository, ["write-tree"], state=execution)
    return GitMutationSnapshot(
        headRef=head_ref,
        head=head,
        headTree=head_tree,
        refLogFingerprint=_ref_log_fingerprint(repository, head_ref),
        indexTree=index_tree,
        candidateTree=candidate_tree,
        statusFingerprint=hashlib.sha256(status_result.stdout.encode("utf-8")).hexdigest(),
    )


def _ref_log_fingerprint(repository: Path, head_ref: str) -> str:
    """Bind the latest entry and bounded append identity of the exact branch log."""
    reflog_result = run_git(
        repository,
        ["reflog", "show", "-1", "--format=%H%x00%gD%x00%gs", head_ref],
    )
    if reflog_result.returncode != 0 or not reflog_result.stdout:
        raise RuntimeError("Git ref-log mutation evidence is unreadable")
    log_path = Path(
        require_git(
            repository,
            ["rev-parse", "--path-format=absolute", "--git-path", f"logs/{head_ref}"],
        )
    )
    facts = log_path.stat()
    identity = (
        f"{facts.st_dev}\0{facts.st_ino}\0{facts.st_size}\0{facts.st_mtime_ns}\0"
        f"{reflog_result.stdout}"
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def commit_matches_intent(
    repository: Path,
    *,
    before: GitMutationSnapshot,
    actual: GitMutationSnapshot,
    expected_tree: str,
    commit: str,
) -> bool:
    if (
        actual.headRef != before.headRef
        or actual.head != commit
        or actual.headTree != expected_tree
    ):
        return False
    return require_git(repository, ["rev-parse", f"{commit}^"]) == before.head


def _report_evidence(args: WorktreeArgs, evidence: GitMutationEvidence) -> None:
    report_operation_progress(
        args,
        f"{evidence.leg}-commit",
        current_command=f"{evidence.leg} Git mutation {evidence.state}",
        mutation_evidence=evidence.model_dump(mode="json"),
    )


def _evidence_index_path(args: WorktreeArgs, leg: CloseoutMutationLeg) -> Path:
    contract_path = cast(Path, args.contract_path)
    return contract_path.parent / "reports" / f".{leg}-mutation-evidence.index"


def _record_index_path(record: LifecycleOperationRecord, leg: CloseoutMutationLeg) -> Path:
    return Path(record.contractPath).parent / "reports" / f".{leg}-reconcile.index"


def _require_record_repositories(record: LifecycleOperationRecord) -> None:
    contract = load_contract(Path(record.contractPath))
    expected = _contract_repositories(contract)
    for leg, evidence in record.mutationEvidence.items():
        repository = expected[leg]
        if repository is None or Path(evidence.repository).resolve() != repository.resolve():
            raise RuntimeError(
                f"closeout {leg} mutation evidence repository is outside contract authority"
            )


def _require_mutation_leg_authority(
    args: WorktreeArgs,
    leg: CloseoutMutationLeg,
    repository: Path,
) -> None:
    effective = args.closeout_input
    if effective is None or not effective.enabled(leg):
        raise RuntimeError(f"closeout {leg} mutation leg is not enabled")
    if args.contract_path is None:
        raise RuntimeError("closeout mutation evidence requires a contract path")
    expected = _contract_repositories(load_contract(args.contract_path))[leg]
    if expected is None or expected.resolve() != repository.resolve():
        raise RuntimeError(f"closeout {leg} mutation repository is outside contract authority")


def _require_evidence_repository(
    evidence: GitMutationEvidence,
    repository: Path,
) -> None:
    if Path(evidence.repository).resolve() != repository.resolve():
        raise RuntimeError(f"closeout {evidence.leg} mutation repository changed after intent")


def _contract_repositories(
    contract: WorktreeContract,
) -> dict[CloseoutMutationLeg, Path | None]:
    return {
        "code": contract.code_worktree if contract.kind == "leaf" else None,
        "memory": contract.memory_worktree or contract.memory_repo_path,
        "ledger": contract.memory_worktree or contract.memory_repo_path,
    }
