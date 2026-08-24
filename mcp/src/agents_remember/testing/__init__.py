"""Bounded direct-test diagnostics with no acceptance authority."""

from agents_remember.testing.eligibility import classify_direct_selection
from agents_remember.testing.evidence import (
    DiagnosticTestEvidence,
    EvidenceAltitude,
    EvidenceConsumer,
    EvidenceConsumerRefusal,
    EvidencePayloadError,
    load_diagnostic_test_evidence,
    require_certifying_evidence,
    test_evidence_payload,
)
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
    "load_diagnostic_test_evidence",
    "require_certifying_evidence",
    "test_evidence_payload",
]
