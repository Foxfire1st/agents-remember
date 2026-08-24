"""Separate diagnostic feedback from candidate-bound Dagger certification."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, TypeAlias

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


@dataclass(frozen=True)
class CandidateBinding:
    """Content binding that invalidates evidence when code or config moves."""

    digest: str
    policy_version: str
    configuration_paths: tuple[str, ...]


@dataclass(frozen=True)
class DiagnosticTestEvidence:
    """Local feedback that cannot satisfy an accepting consumer."""

    binding: CandidateBinding
    nodes: tuple[str, ...]
    exit_code: int
    altitude: EvidenceAltitude = EvidenceAltitude.DIAGNOSTIC


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


TestEvidence: TypeAlias = DiagnosticTestEvidence | CertifyingTestEvidence


class EvidenceConsumerRefusal(RuntimeError):
    """A consumer was offered evidence below its required altitude."""


class EvidencePayloadError(ValueError):
    """Serialized test evidence is malformed or requests unavailable authority."""


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
    evidence: TestEvidence | None,
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
            "direct diagnostic output is not acceptance evidence"
        )
    return evidence


def test_evidence_payload(evidence: TestEvidence) -> dict[str, object]:
    """Serialize an altitude-discriminated result without granting new authority."""

    if isinstance(evidence, DiagnosticTestEvidence):
        return {
            "schemaVersion": EVIDENCE_SCHEMA_VERSION,
            "altitude": evidence.altitude.value,
            "candidate": {
                "digest": evidence.binding.digest,
                "policyVersion": evidence.binding.policy_version,
                "configurationPaths": list(evidence.binding.configuration_paths),
            },
            "nodes": list(evidence.nodes),
            "exitCode": evidence.exit_code,
        }
    require_certifying_evidence(evidence, consumer=EvidenceConsumer.QUALITY)
    return {
        "schemaVersion": EVIDENCE_SCHEMA_VERSION,
        "altitude": evidence.altitude.value,
        "candidateTree": evidence.candidate_tree,
        "resultSha256": evidence.result_sha256,
    }


def load_diagnostic_test_evidence(raw: Mapping[str, object]) -> DiagnosticTestEvidence:
    """Load local feedback; certifying payloads require the Dagger manifest loader."""

    if raw.get("schemaVersion") != EVIDENCE_SCHEMA_VERSION:
        raise EvidencePayloadError("test evidence schema version is unsupported")
    if raw.get("altitude") != EvidenceAltitude.DIAGNOSTIC.value:
        raise EvidencePayloadError(
            "serialized certifying evidence is not caller-loadable; verify its Dagger publication"
        )
    if set(raw) != {"schemaVersion", "altitude", "candidate", "nodes", "exitCode"}:
        raise EvidencePayloadError("diagnostic evidence fields are invalid")
    binding = _load_candidate_binding(raw.get("candidate"))
    raw_nodes = raw.get("nodes")
    exit_code = raw.get("exitCode")
    if (
        not isinstance(raw_nodes, list)
        or not raw_nodes
        or any(not isinstance(node, str) or not node for node in raw_nodes)
    ):
        raise EvidencePayloadError("diagnostic evidence nodes are invalid")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code < 0:
        raise EvidencePayloadError("diagnostic evidence exit code is invalid")
    return DiagnosticTestEvidence(binding, tuple(raw_nodes), exit_code)


def _load_candidate_binding(raw: object) -> CandidateBinding:
    if not isinstance(raw, dict) or set(raw) != {
        "digest",
        "policyVersion",
        "configurationPaths",
    }:
        raise EvidencePayloadError("diagnostic candidate binding is invalid")
    digest = raw.get("digest")
    policy_version = raw.get("policyVersion")
    paths = raw.get("configurationPaths")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise EvidencePayloadError("diagnostic candidate digest is invalid")
    if not isinstance(policy_version, str) or not policy_version:
        raise EvidencePayloadError("diagnostic policy version is invalid")
    if not isinstance(paths, list) or any(not isinstance(path, str) or not path for path in paths):
        raise EvidencePayloadError("diagnostic configuration paths are invalid")
    return CandidateBinding(digest, policy_version, tuple(paths))
