"""Bounded public result selection for the isolated legacy bridge."""

from __future__ import annotations

import hashlib
from pathlib import Path

from agents_remember.worktrees.integration.legacy.legacy_operation_schema import LegacyArchive

LEGACY_BRIDGE_REMOVAL_CONDITION = (
    "remove after one release window reports zero recognized schema-1 records and every "
    "canonical record carrying legacyMigration is terminal or archived"
)


def legacy_amendment_digests(
    memory_message: str,
    ledger_message: str,
    audit_reason: str,
) -> dict[str, object]:
    """Digest private accepted/replayed strings before lifecycle publication."""

    def digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    return {
        "memoryMessageSha256": digest(memory_message),
        "ledgerMessageSha256": digest(ledger_message),
        "auditReasonSha256": digest(audit_reason),
    }


def legacy_archive_response(
    archive: LegacyArchive,
    path: Path,
    *,
    dry_run: bool = False,
) -> dict[str, object]:
    """Select terminal archive facts without exposing retained schema-1 bytes."""

    return {
        "state": "would-archive" if dry_run else "archived",
        "contractPath": archive.contractPath,
        "operationKind": archive.operationKind,
        "legacyDigest": archive.originalSha256,
        "archivePath": path.as_posix(),
        "terminalEvidence": archive.terminalEvidence,
        "removalCondition": LEGACY_BRIDGE_REMOVAL_CONDITION,
        "dryRun": dry_run,
    }
