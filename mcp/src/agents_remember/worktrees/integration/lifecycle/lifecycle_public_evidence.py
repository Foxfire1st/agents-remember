"""Bound private lifecycle identity before developer-facing serialization."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from agents_remember.models.lifecycles.operation import LifecycleOperationRecord

_PRIVATE_OPERATION_KEYS = frozenset({"operationKey", "claimedOperationKey", "legacyOperationKey"})


@dataclass(frozen=True)
class PublicEvidencePair:
    expected: dict[str, object]
    observed: dict[str, object]


MigratedLifecycleState = Literal["not-applicable", "recovery-required", "terminal"]


@dataclass(frozen=True)
class MigratedLifecycleClassification:
    """Current lifecycle meaning of one internally retained migration proof."""

    state: MigratedLifecycleState

    def recovery_result(
        self,
        record: LifecycleOperationRecord,
    ) -> dict[str, object] | None:
        if self.state != "recovery-required":
            return None
        proof = record.legacyMigration
        assert proof is not None
        recovery = record.recoveryCommits
        return {
            "state": "legacy-closeout-recovery-required",
            "summary": (
                "The explicit schema-1 migration is accepted for same-generation recovery."
            ),
            "migration": {
                "version": proof.migrationVersion,
                "originalSha256": proof.originalSha256,
            },
            "generation": record.generation,
            "recoveryState": {
                "status": record.status,
                "phase": record.phase,
                "codeCommit": recovery.codeCommit if recovery is not None else "",
                "codeTree": proof.codeTree,
                "memoryCommitProven": bool(recovery and recovery.memoryContentCommit),
                "ledgerCommitProven": bool(recovery and recovery.ledgerCommit),
            },
            "nextAction": "recover",
        }


def public_lifecycle_evidence_pair(
    expected: Mapping[str, object],
    observed: Mapping[str, object],
) -> PublicEvidencePair:
    """Replace private identifiers with digests plus an exact comparison fact."""

    expected_private: list[str] = []
    observed_private: list[str] = []
    public_expected = _public_evidence(expected, expected_private)
    public_observed = _public_evidence(observed, observed_private)
    assert isinstance(public_expected, dict)
    assert isinstance(public_observed, dict)
    if expected_private or observed_private:
        public_observed["operationIdentityComparison"] = {
            "expectedCount": len(expected_private),
            "observedCount": len(observed_private),
            "matches": expected_private == observed_private,
        }
    return PublicEvidencePair(public_expected, public_observed)


def public_lifecycle_evidence(value: object) -> object:
    """Recursively sanitize one unpaired public result or evidence payload."""

    private_values: list[str] = []
    return _public_evidence(value, private_values)


def public_failure_evidence(
    *,
    stage: str,
    side: str,
    name: str,
    error_type: str,
    **facts: Mapping[str, object] | None,
) -> dict[str, object]:
    """Return the stable public vocabulary for one unreadable/publication failure."""

    unexpected = set(facts) - {"expected", "observed"}
    if unexpected:
        raise TypeError(f"unsupported public failure evidence facts: {sorted(unexpected)}")

    evidence: dict[str, object] = {
        "stage": stage,
        "side": side,
        "name": name,
        "errorType": error_type,
    }
    expected = facts.get("expected")
    observed = facts.get("observed")
    if expected is not None:
        evidence["expected"] = dict(expected)
    if observed is not None:
        evidence["observed"] = dict(observed)
    return evidence


def classify_migrated_lifecycle(
    record: LifecycleOperationRecord,
) -> MigratedLifecycleClassification:
    """Let terminal worker truth outrank the temporary migration handoff."""

    if record.legacyMigration is None:
        return MigratedLifecycleClassification("not-applicable")
    if record.status in {"completed", "failed", "cancelled"}:
        return MigratedLifecycleClassification("terminal")
    return MigratedLifecycleClassification("recovery-required")


def _public_evidence(value: object, private_values: list[str]) -> object:
    if isinstance(value, Mapping):
        public: dict[str, object] = {}
        identity_digests: list[str] = []
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if key in _PRIVATE_OPERATION_KEYS:
                identity = str(raw_value)
                private_values.append(identity)
                identity_digests.append(hashlib.sha256(identity.encode("utf-8")).hexdigest())
                continue
            public[key] = _public_evidence(raw_value, private_values)
        if identity_digests:
            public["operationIdentityDigests"] = identity_digests
        return public
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_public_evidence(item, private_values) for item in value]
    return value
