"""Bounded inventory of every current Python-test evidence consumer."""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.models.test_evidence import EvidenceConsumer


@dataclass(frozen=True)
class EvidenceConsumerContract:
    """One acceptance edge and the authority it is permitted to consume."""

    consumer: EvidenceConsumer
    owner: str
    current_evidence_shape: str
    candidate_proof: str
    direct_route_reachable: bool
    enforcement: str


ACCEPTING_CONSUMER_INVENTORY = (
    EvidenceConsumerContract(
        EvidenceConsumer.COVERAGE,
        "agents_remember_test_support.code_quality.quality_plan._pytest_step",
        "Coverage.py data and JSON created by the quality wrapper",
        "exact candidate sandbox plus selected measurement scope",
        False,
        "only CertifyingTestEvidence may reach coverage-derived acceptance",
    ),
    EvidenceConsumerContract(
        EvidenceConsumer.QUALITY,
        "agents_remember.worktrees.modules.quality.clean_executor.run_clean_quality",
        "atomic Dagger quality generation and authoritative result JSON",
        "published generation hashes plus lifecycle-supplied attestation where applicable",
        False,
        "the Dagger publication loader is the sole certifying capability minter",
    ),
    EvidenceConsumerContract(
        EvidenceConsumer.RETRY,
        "agents_remember_test_support.code_quality.retry_proof.prepare",
        "content-addressed pytest and coverage proof",
        "repository/configuration/selection/runtime compatibility key inside Dagger",
        False,
        "diagnostics cannot publish or restore retry proof",
    ),
    EvidenceConsumerContract(
        EvidenceConsumer.ROUTE_REVIEW,
        "agents_remember.worktrees.route_review.require_current_route_review",
        "independent route-review verdict",
        "plane-stamped exact candidate tree and durable route artifacts",
        False,
        "route review is independent evidence; diagnostic output cannot substitute for it",
    ),
    EvidenceConsumerContract(
        EvidenceConsumer.LIFECYCLE,
        "agents_remember.worktrees.modules.quality.gate.run_strict_code_quality_gate",
        "lifecycle quality-gate result payload",
        "Dagger-only executor and enclosure-owned report generation",
        False,
        "lifecycle acceptance requires CertifyingTestEvidence",
    ),
    EvidenceConsumerContract(
        EvidenceConsumer.CLOSEOUT,
        "agents_remember.worktrees.queue.closeout_staged_quality.gate_staged_code",
        "targeted pre-commit Dagger gate result",
        "reviewed staged candidate tree before and after the gate",
        False,
        "diagnostic exit codes and files are rejected as closeout evidence",
    ),
    EvidenceConsumerContract(
        EvidenceConsumer.INTEGRATION,
        "agents_remember.worktrees.integration.integration_quality.run_integration_quality_gate",
        "full Dagger result or exact reusable organizational certification",
        "completion fingerprint, commit, candidate tree, plan, and result digest",
        False,
        "integration accepts only candidate-bound Dagger certification",
    ),
)
