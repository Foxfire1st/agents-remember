"""Focused proof for lifecycle operation mutation and worker invariants."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agents_remember.certification.certificate_store import (
    CertificateStorePolicy,
    ContentAddressedCertificateStore,
)
from agents_remember.models.certification.references import (
    CertificateObjectKind,
    CertificateObjectReference,
)
from agents_remember.models.lifecycles import operation
from agents_remember.models.lifecycles.preparation import build_prepared_closeout_output
from agents_remember.models.lifecycles.preparation_state import (
    OperationPreparationState,
    PreparedCodeRetention,
    SelectedPreparation,
    validate_prepared_code_retention,
)
from agents_remember.worktrees.integration.closeout.preparation_selection import (
    _rebound_code_output,
)


def _value(**fields: object) -> Any:
    return cast(Any, SimpleNamespace(**fields))


def test_mutation_history_and_irreversible_boundary_require_exact_proof() -> None:
    unchanged = _value(leg="code", state="reconciled-unchanged")
    operation._require_mutation_attempts("code", [unchanged])
    record = _value(mutationHistory={"code": [unchanged]})
    operation._require_mutation_history(record)
    with pytest.raises(ValueError, match="reconciled-unchanged"):
        operation._require_mutation_attempts("code", [_value(leg="memory", state="commit-proven")])

    reversible = _value(
        mutationEvidence={"code": _value(state="pre-mutation")},
        legacyMigration=None,
        irreversibleBoundaryEntered=False,
    )
    operation._require_irreversible_boundary(reversible)
    proven = _value(
        mutationEvidence={"code": _value(state="commit-proven")},
        legacyMigration=None,
        irreversibleBoundaryEntered=True,
    )
    operation._require_irreversible_boundary(proven)
    assert operation._commit_proven(proven)
    proven.irreversibleBoundaryEntered = False
    with pytest.raises(ValueError, match="irreversible boundary"):
        operation._require_irreversible_boundary(proven)


def test_recovery_commits_cannot_contradict_commit_proof() -> None:
    proof = _value(state="commit-proven", commit="a" * 40)
    pending = _value(state="pre-mutation", commit=None)
    commits = _value(codeCommit="a" * 40, memoryContentCommit="", ledgerCommit="")
    operation._require_recovered_leg(commits, "codeCommit", proof)
    operation._require_recovered_leg(commits, "memoryContentCommit", pending)

    record = _value(recoveryCommits=commits, mutationEvidence={"code": proof})
    operation._require_recovery_commit_evidence(record)
    operation._require_recovery_commit_evidence(_value(recoveryCommits=None))
    commits.codeCommit = "b" * 40
    with pytest.raises(ValueError, match="contradicts"):
        operation._require_recovered_leg(commits, "codeCommit", proof)


def test_worker_binding_and_termination_evidence_are_one_authority() -> None:
    none_binding = (None, None, None)
    full_binding = (123, "a" * 64, "b" * 64)
    operation._require_complete_worker_binding(none_binding)
    operation._require_complete_worker_binding(full_binding)
    assert not operation._binding_present(none_binding)
    assert operation._binding_present(full_binding)
    with pytest.raises(ValueError, match="one authority"):
        operation._require_complete_worker_binding((123, None, None))

    normal = _value(status="running", terminationReturnStatus=None, terminationReturnPhase=None)
    requested = _value(
        status="termination-required",
        terminationReturnStatus="running",
        terminationReturnPhase="source-merge",
    )
    operation._require_termination_return_identity(normal)
    operation._require_termination_return_identity(requested)
    with pytest.raises(ValueError, match="return status"):
        operation._require_termination_return_identity(
            _value(
                status="termination-required",
                terminationReturnStatus="running",
                terminationReturnPhase=None,
            )
        )

    operation._require_no_termination_return(normal)
    with pytest.raises(ValueError, match="durable termination"):
        operation._require_no_termination_return(requested)

    live_record = _value(workerPid=123, workerLease="a" * 64)
    live = _value(state="requested", pid=123, lease="a" * 64)
    exited = _value(state="exited", pid=123, lease="a" * 64)
    operation._require_live_termination_identity(live_record, live)
    operation._require_live_termination_identity(live_record, exited)
    with pytest.raises(ValueError, match="exact pid and lease"):
        operation._require_live_termination_identity(
            live_record, _value(state="requested", pid=456, lease="a" * 64)
        )

    operation._require_exited_worker_release(live, full_binding)
    operation._require_exited_worker_release(exited, none_binding)
    with pytest.raises(ValueError, match="release pid"):
        operation._require_exited_worker_release(exited, full_binding)


def _object_reference(kind: CertificateObjectKind, token: str) -> CertificateObjectReference:
    digest = token * 64
    return CertificateObjectReference(
        kind=kind,
        semanticDigest=digest,
        contentSha256=digest,
        sizeBytes=1,
    )


def test_prepared_code_retention_binds_successor_prefix_and_predecessor() -> None:
    source_intent = _object_reference("preparation-intent", "a")
    source_output = _object_reference("prepared-output", "b")
    successor_intent = _object_reference("preparation-intent", "c")
    successor_output = _object_reference("prepared-output", "d")
    selected = OperationPreparationState(
        operationKey="e" * 64,
        generation=2,
        legs=(SelectedPreparation(leg="code", intent=successor_intent, output=successor_output),),
    )
    retention = PreparedCodeRetention(
        predecessorOperationKey="f" * 64,
        predecessorGeneration=1,
        predecessorFingerprint="1" * 64,
        sourceIntent=source_intent,
        sourceOutput=source_output,
        successorIntent=successor_intent,
        successorOutput=successor_output,
        commit="2" * 40,
        tree="3" * 40,
        committerDate="2026-09-09T00:26:24+02:00",
        rawCommitSha256="4" * 64,
    )

    validate_prepared_code_retention(
        retention,
        selected,
        ("closeout", "e" * 64, 2, "1" * 64),
    )
    validate_prepared_code_retention(
        retention,
        selected.model_copy(
            update={
                "legs": (
                    *selected.legs,
                    SelectedPreparation(
                        leg="memory-content",
                        intent=_object_reference("preparation-intent", "e"),
                    ),
                )
            }
        ),
        ("closeout", "e" * 64, 2, "1" * 64),
    )

    with pytest.raises(ValueError, match="does not bind"):
        validate_prepared_code_retention(
            retention,
            selected.model_copy(
                update={
                    "legs": (
                        SelectedPreparation(
                            leg="code",
                            intent=successor_intent,
                            output=_object_reference("prepared-output", "e"),
                        ),
                    )
                }
            ),
            ("closeout", "e" * 64, 2, "1" * 64),
        )


def test_prepared_code_retention_rejects_non_immediate_predecessor() -> None:
    reference = _object_reference("preparation-intent", "a")
    output = _object_reference("prepared-output", "b")
    selected = OperationPreparationState(
        operationKey="c" * 64,
        generation=3,
        legs=(SelectedPreparation(leg="code", intent=reference, output=output),),
    )
    retention = PreparedCodeRetention(
        predecessorOperationKey="d" * 64,
        predecessorGeneration=1,
        predecessorFingerprint="e" * 64,
        sourceIntent=_object_reference("preparation-intent", "f"),
        sourceOutput=_object_reference("prepared-output", "1"),
        successorIntent=reference,
        successorOutput=output,
        commit="2" * 40,
        tree="3" * 40,
        committerDate="2026-09-09T00:26:24+02:00",
        rawCommitSha256="4" * 64,
    )

    with pytest.raises(ValueError, match="immediately prior"):
        validate_prepared_code_retention(
            retention,
            selected,
            ("closeout", "c" * 64, 3, "e" * 64),
        )


def test_rebound_prepared_output_retains_real_git_identity_and_date(tmp_path: Path) -> None:
    repository = tmp_path / "code"
    repository.mkdir()
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "fixture@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Fixture"],
        check=True,
    )
    (repository / "prepared.txt").write_text("prepared\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "prepared.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "commit", "--quiet", "-m", "prepared fixture"],
        check=True,
    )
    raw = subprocess.check_output(["git", "-C", str(repository), "cat-file", "commit", "HEAD"])
    source = build_prepared_closeout_output(
        raw,
        _object_reference("preparation-intent", "a"),
        disposition="created",
    )
    successor_intent = _object_reference("preparation-intent", "b")
    objects = ContentAddressedCertificateStore(
        tmp_path / "objects",
        CertificateStorePolicy(
            scopeId="retention-test",
            maxObjects=100,
            maxBytes=1_000_000,
            reclamationOwner="test",
        ),
    )

    rebound, reference = _rebound_code_output(objects, raw, source, successor_intent)

    assert rebound.intent == successor_intent
    assert (rebound.commit, rebound.tree, rebound.committerDate) == (
        source.commit,
        source.tree,
        source.committerDate,
    )
    assert rebound.rawCommitSha256 == source.rawCommitSha256
    assert objects.load_reference(reference) == rebound
