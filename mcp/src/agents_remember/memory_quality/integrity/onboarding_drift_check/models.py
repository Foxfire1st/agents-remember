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


CLASSIFICATIONS = (
    "up to date",
    "drifted",
    "missing verification",
    "missing",
    "orphaned",
    "disabled",
    "unsupported",
)
ACTIONABLE_CLASSIFICATIONS = {
    "drifted",
    "missing verification",
    "missing",
    "orphaned",
    "unsupported",
}
INLINE_START_MARKER = "@ar-onboarding"
INLINE_END_MARKER = "@ar-onboarding-end"
GIT_BLOB_SET_ALGORITHM = "git-blob-set-v1"
SIDECAR_DOC_TYPES = {
    "file-level-onboarding",
    "repo-overview",
    "route-local-overview",
    "repo-entity-catalog",
}
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
class DriftRow:
    onboarding_file: str
    source_file: str
    repository: str
    storage_mode: str
    last_verified_hash: str
    last_verified_date: str
    classification: str
    trust: str
    affected_sections: str
    note: str


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
