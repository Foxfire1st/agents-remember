"""Data models and constants for onboarding drift detection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict

from agents_remember.models.drift import DriftStatus


class DriftSummaryPacket(TypedDict):
    """What `summary.py` hands to a wire model: a status, plus whatever that status carries."""

    status: DriftStatus
    count: NotRequired[int]
    actionableCount: NotRequired[int]
    reportPath: NotRequired[str]
    actionableSample: NotRequired[list[dict[str, Any]]]
    rows: NotRequired[list[dict[str, Any]]]
    error: NotRequired[str]
    # Converted-tree measurement (MIK-R44): the summary is built from
    # `converted_knowledge_check` and carries what was really measured.
    basis: NotRequired[str]
    memoryRoot: NotRequired[str]
    codeRoot: NotRequired[str]
    sidecarCount: NotRequired[int]
    states: NotRequired[dict[str, int]]
    violationCount: NotRequired[int]
    violationSample: NotRequired[list[dict[str, Any]]]
    reportOnlyCount: NotRequired[int]
    unreadableSidecars: NotRequired[list[str]]


SIDECAR_DOC_TYPES = {
    "file-level-onboarding",
    "repo-overview",
    "route-local-overview",
    "repo-entity-catalog",
}

INLINE_START_MARKER = "@ar-onboarding"
INLINE_END_MARKER = "@ar-onboarding-end"
COMMON_BLOCK_DELIMITERS = {
    "/*": "*/",
    "<!--": "-->",
    '"""': '"""',
    "'''": "'''",
    "=begin": "=end",
    "{-": "-}",
    "(*": "*)",
}


@dataclass
class EntityFingerprint:
    entity: str
    algorithm: str
    fingerprint: str
    evidence_paths: list[str]


@dataclass
class InlineBlock:
    raw_text: str
    metadata: dict[str, str]


def repo_root_placeholder() -> str:
    return "<repo-root>"
