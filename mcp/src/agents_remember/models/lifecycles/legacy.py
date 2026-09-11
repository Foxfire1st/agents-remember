"""Bounded audit vocabulary for the one supported schema-1 closeout incident."""

from __future__ import annotations

import base64
import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LegacyCloseoutMigrationProof(BaseModel):
    """Exact legacy bytes and live code-output proof retained by a migrated record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    migrationVersion: Literal["schema-1-closeout-v1"] = "schema-1-closeout-v1"
    originalBytesBase64: str = Field(min_length=1)
    originalSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    legacyOperationKey: str = Field(pattern=r"^[0-9a-f]{64}$")
    legacyFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    legacyCandidateState: str = Field(pattern=r"^[0-9a-f]{64}$")
    legacyCandidateTree: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    legacyCodeCommitMessage: str = Field(min_length=1)
    legacyApprovalNote: str = Field(min_length=1)
    memoryCommitMessage: str = Field(min_length=1)
    ledgerCommitMessage: str = Field(min_length=1)
    auditReason: str = Field(min_length=1, max_length=8192)
    codeRepository: str = Field(min_length=1, max_length=4096)
    codeRef: str = Field(pattern=r"^refs/heads/.+$", max_length=4096)
    codeCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    codeTree: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    provenAt: str = Field(min_length=1)

    @model_validator(mode="after")
    def _require_exact_original_bytes(self) -> LegacyCloseoutMigrationProof:
        try:
            original = base64.b64decode(self.originalBytesBase64, validate=True)
        except ValueError as exc:
            raise ValueError("legacy migration proof contains invalid base64 bytes") from exc
        if hashlib.sha256(original).hexdigest() != self.originalSha256:
            raise ValueError("legacy migration proof digest does not match original bytes")
        if self.codeTree != self.legacyCandidateTree:
            raise ValueError("legacy migration code tree must equal the accepted candidate tree")
        for value in (
            self.legacyCodeCommitMessage,
            self.legacyApprovalNote,
            self.memoryCommitMessage,
            self.ledgerCommitMessage,
            self.auditReason,
        ):
            if value.strip() != value:
                raise ValueError("legacy migration audit strings must be stripped")
        return self
