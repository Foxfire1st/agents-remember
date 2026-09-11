"""Opaque candidate-bound Dagger certification for accepting consumers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

EVIDENCE_SCHEMA_VERSION = "python-test-evidence/v1"


class EvidenceAltitude(StrEnum):
    """Authority altitude of a test result."""

    DIAGNOSTIC = "diagnostic"
    CERTIFYING = "certifying"


class EvidenceConsumer(StrEnum):
    """Known result consumers whose accepting altitude must stay explicit."""

    LOCAL_FEEDBACK = "local-feedback"
    COVERAGE = "coverage"
    QUALITY = "quality"
    RETRY = "retry"
    ROUTE_REVIEW = "route-review"
    LIFECYCLE = "lifecycle"
    CLOSEOUT = "closeout"
    INTEGRATION = "integration"


class _DaggerAuthority:
    """Module-owned capability minted only after Dagger provenance validation."""


_DAGGER_AUTHORITY: Final[_DaggerAuthority] = _DaggerAuthority()


@dataclass(frozen=True, init=False)
class CertifyingTestEvidence:
    """Candidate-bound evidence produced by the governed Dagger publication route."""

    candidate_tree: str
    result_sha256: str
    _authority: _DaggerAuthority
    altitude: EvidenceAltitude = EvidenceAltitude.CERTIFYING

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise TypeError(
            "certifying evidence is minted only by the verified Dagger publication loader"
        )

    @classmethod
    def _mint(
        cls,
        *,
        candidate_tree: str,
        result_sha256: str,
        authority: _DaggerAuthority,
    ) -> CertifyingTestEvidence:
        instance = object.__new__(cls)
        object.__setattr__(instance, "candidate_tree", candidate_tree)
        object.__setattr__(instance, "result_sha256", result_sha256)
        object.__setattr__(instance, "_authority", authority)
        object.__setattr__(instance, "altitude", EvidenceAltitude.CERTIFYING)
        return instance


class EvidenceConsumerRefusal(RuntimeError):
    """A consumer was offered evidence below its required altitude."""


def _certifying_evidence_from_verified_dagger(
    *,
    candidate_tree: str,
    result_sha256: str,
) -> CertifyingTestEvidence:
    """Mint a capability after the Dagger manifest loader has verified provenance."""

    if re.fullmatch(r"[0-9a-f]{40,64}", candidate_tree) is None:
        raise ValueError("certifying evidence candidate tree is invalid")
    if re.fullmatch(r"[0-9a-f]{64}", result_sha256) is None:
        raise ValueError("certifying evidence result digest is invalid")

    return CertifyingTestEvidence._mint(
        candidate_tree=candidate_tree,
        result_sha256=result_sha256,
        authority=_DAGGER_AUTHORITY,
    )


def require_certifying_evidence(
    evidence: object | None,
    *,
    consumer: EvidenceConsumer,
) -> CertifyingTestEvidence:
    """Refuse diagnostic output at every coverage/quality/lifecycle accepting edge."""

    if consumer is EvidenceConsumer.LOCAL_FEEDBACK:
        raise ValueError("local feedback accepts either altitude and needs no certification")
    if (
        not isinstance(evidence, CertifyingTestEvidence)
        or evidence._authority is not _DAGGER_AUTHORITY
    ):
        raise EvidenceConsumerRefusal(
            f"{consumer.value} requires candidate-bound Dagger certification; "
            "non-certifying or diagnostic output is not acceptance evidence"
        )
    return evidence


def evidence_payload(evidence: CertifyingTestEvidence) -> dict[str, object]:
    """Serialize verified certification without granting authority to a caller."""

    require_certifying_evidence(evidence, consumer=EvidenceConsumer.QUALITY)
    return {
        "schemaVersion": EVIDENCE_SCHEMA_VERSION,
        "altitude": evidence.altitude.value,
        "candidateTree": evidence.candidate_tree,
        "resultSha256": evidence.result_sha256,
    }
