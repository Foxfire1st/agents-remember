"""Bounded direct-test diagnostics with no acceptance authority."""

from agents_remember.models.test_evidence import (
    DiagnosticTestEvidence,
    EvidenceAltitude,
    EvidenceConsumer,
    EvidenceConsumerRefusal,
    EvidencePayloadError,
    evidence_payload,
    load_diagnostic_test_evidence,
    require_certifying_evidence,
)
from agents_remember.testing.eligibility import classify_direct_selection
from agents_remember.testing.selection_contract import (
    DirectSelectionDecision,
    EligibleDirectSelection,
    RefusedDirectSelection,
)

__all__ = [
    "DiagnosticTestEvidence",
    "DirectSelectionDecision",
    "EligibleDirectSelection",
    "EvidenceAltitude",
    "EvidenceConsumer",
    "EvidenceConsumerRefusal",
    "EvidencePayloadError",
    "RefusedDirectSelection",
    "classify_direct_selection",
    "evidence_payload",
    "load_diagnostic_test_evidence",
    "require_certifying_evidence",
]
