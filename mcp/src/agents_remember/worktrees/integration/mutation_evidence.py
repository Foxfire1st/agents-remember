"""Durable closeout Git-intent publication and crash reconciliation."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import cast

from agents_remember.kernel.git_command import run_git
from agents_remember.models.closeout_input import EffectiveCloseoutInput
from agents_remember.models.lifecycles.mutation_evidence import (
    CloseoutMutationLeg,
    GitMutationEvidence,
    GitMutationSnapshot,
)
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
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
        evidence[leg] = GitMutationEvidence(leg=leg, repository=repository.as_posix())
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
    before = _snapshot(repository, _evidence_index_path(args, leg))
    evidence = GitMutationEvidence(
        leg=leg,
        repository=repository.as_posix(),
        state="mutation-intent",
        before=before,
        expectedOutputTree=before.candidateTree if use_current_candidate else expected_output_tree,
    )
    _report_evidence(args, evidence)
    return evidence


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
    actual = _snapshot(repository, _evidence_index_path(args, evidence.leg))
    expected_tree = evidence.expectedOutputTree
    if expected_tree is None or not _commit_matches_intent(
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
            actual = _snapshot(repository, _record_index_path(record, leg))
            if actual == evidence.before:
                reconciled[leg] = evidence.model_copy(
                    update={"state": "reconciled-unchanged", "observed": actual}
                )
                continue
            expected_tree = evidence.expectedOutputTree
            if expected_tree is None or actual.head == evidence.before.head:
                reconciled[leg] = evidence.model_copy(update={"observed": actual})
                continue
            if _commit_matches_intent(
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
                reconciled[leg] = evidence.model_copy(update={"observed": actual})
        except (OSError, RuntimeError):
            # Missing or unreadable facts remain ambiguous and same-generation.
            continue
    return reconciled


def closeout_requires_recovery(record: LifecycleOperationRecord) -> bool:
    """Whether closeout evidence forbids replacement or cancellation."""
    return any(
        evidence.state in {"mutation-intent", "commit-proven"}
        for evidence in record.mutationEvidence.values()
    )


def closeout_cancellable(record: LifecycleOperationRecord) -> bool:
    return not closeout_requires_recovery(record)


def _snapshot(repository: Path, index_path: Path) -> GitMutationSnapshot:
    status_result = run_git(repository, ["status", "--porcelain=v1", "-z"])
    if status_result.returncode != 0:
        raise RuntimeError(status_result.stderr.strip() or "could not read Git status evidence")
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


def _ref_log_fingerprint(repository: Path, head_ref: str) -> str:
    """Bind the latest entry and bounded append identity of the exact branch log."""
    reflog_result = run_git(
        repository,
        ["reflog", "show", "-1", "--format=%H%x00%gD%x00%gs", head_ref],
    )
    if reflog_result.returncode != 0 or not reflog_result.stdout:
        raise RuntimeError(reflog_result.stderr.strip() or "could not read Git ref-log evidence")
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


def _commit_matches_intent(
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
        "memory": contract.memory_worktree,
        "ledger": contract.memory_worktree,
    }
