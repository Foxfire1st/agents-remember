"""Measured quality identity, separate from the complete report's byte integrity."""

from __future__ import annotations

import hashlib

from agents_remember.memory_quality.knowledge_review import KNOWLEDGE_REVIEW_HEADING
from agents_remember.models.lifecycles.curator_coherence import CuratorQualityAttestation
from agents_remember.models.lifecycles.evidence_dependencies import canonical_sha256


def measured_report_sha256(report: str) -> str:
    """Hash every report byte except the authority's own assessment projection.

    The renderer emits top-level Markdown sections; only this named section is derived
    from the authority being validated. All following sections remain measured.
    """
    heading = f"## {KNOWLEDGE_REVIEW_HEADING}\n"
    measured: list[str] = []
    in_review = False
    for line in report.splitlines(keepends=True):
        if line == heading:
            in_review = True
        elif in_review and line.startswith("## "):
            in_review = False
        if not in_review:
            measured.append(line)
    return hashlib.sha256("".join(measured).encode("utf-8")).hexdigest()


def measured_quality_attestation_sha256(attestation: CuratorQualityAttestation) -> str:
    """Bind measured report content and all facts outside its assessment projection.

    Full report bytes and their dependency envelope are checked independently against
    the current report and trees. Their changing integrity hashes are not quality facts.
    """
    return canonical_sha256(
        attestation.model_dump(mode="json", by_alias=True, exclude={"reportSha256", "dependencies"})
    )
