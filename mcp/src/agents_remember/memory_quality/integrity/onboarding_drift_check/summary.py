"""Bounded drift summary helpers for context packets."""

from __future__ import annotations

import json
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.primitives.drift_snapshot import drift_snapshot_path
from agents_remember.kernel.primitives.observer_paths import DRIFT_SNAPSHOT_SCHEMA
from agents_remember.memory_quality.integrity.onboarding_drift_check import drift
from agents_remember.memory_quality.integrity.onboarding_drift_check.models import (
    ACTIONABLE_CLASSIFICATIONS,
    DriftSummaryPacket,
)


def not_checked() -> DriftSummaryPacket:
    return {"status": "notChecked"}


def run_drift_summary(
    *,
    code_repository_root: Path,
    context: Any,
    detail_limit: int = 10,
) -> DriftSummaryPacket:
    if not context.onboarding_root.exists():
        return {
            "status": "error",
            "error": f"onboarding root does not exist: {context.onboarding_root}",
        }

    rows = [
        row
        for path in drift.discover_onboarding_files(context.onboarding_root)
        for row in drift.classify_sidecar_onboarding_units(
            path,
            code_repository_root,
            context.onboarding_root,
            context.storage,
        )
    ]
    rows.extend(
        drift.classify_inline_source(path, code_repository_root)
        for path in drift.discover_inline_onboarding_sources(
            code_repository_root,
            context.storage,
        )
    )
    rows.sort(key=lambda row: (row.source_file, row.onboarding_file))
    report_path = drift.resolve_report_path(
        None,
        context.coordination_root,
        context.temp_root,
        code_repository_root,
        context.memory_root,
    )
    drift.write_markdown_report(
        rows,
        report_path,
        code_repository_root,
        context.onboarding_root,
    )
    _write_drift_snapshot(code_repository_root, context, rows, report_path=report_path)
    return summarize_rows(
        [_row_to_dict(row, context.onboarding_root) for row in rows],
        report_path=report_path.as_posix(),
        detail_limit=detail_limit,
    )


def summarize_rows(
    rows: list[dict[str, Any]],
    *,
    report_path: str = "",
    detail_limit: int = 10,
) -> DriftSummaryPacket:
    actionable = [
        row for row in rows if str(row.get("classification", "")) in ACTIONABLE_CLASSIFICATIONS
    ]
    return {
        "status": "checked",
        "count": len(rows),
        "actionableCount": len(actionable),
        "reportPath": report_path,
        "actionableSample": actionable[:detail_limit],
    }


def _row_to_dict(row: Any, onboarding_root: Path) -> dict[str, Any]:
    return {
        "onboarding_file": drift.rel(row.onboarding_file, onboarding_root),
        "storage_mode": row.storage_mode,
        "source_file": row.source_file,
        "repository": row.repository,
        "last_verified_hash": row.last_verified_hash,
        "last_verified_date": row.last_verified_date,
        "classification": row.classification,
        "trust": row.trust,
        "affected_sections": row.affected_sections,
        "note": row.note,
    }


def _write_drift_snapshot(
    code_repository_root: Path,
    context: Any,
    rows: list[Any],
    *,
    report_path: Path | None = None,
) -> None:
    """Persist the drift result as a durable JSON snapshot for the observer (slice 3b, b1).

    Best-effort: the dashboard snapshot must never fail the drift run that produced
    it, so write errors are swallowed. The reducer reads this snapshot with a
    staleness age instead of re-classifying drift (which is git-per-sidecar).
    """
    branch = drift.current_branch_name(code_repository_root)
    classification_counts = drift.counts(rows)
    payload = {
        "schema": DRIFT_SNAPSHOT_SCHEMA,
        "repository": code_repository_root.name,
        "branch": branch,
        "checkedAt": datetime.now(UTC).isoformat(),
        "sourceRoot": code_repository_root.resolve().as_posix(),
        "memoryRoot": Path(context.memory_root).resolve().as_posix(),
        **({"reportPath": report_path.as_posix()} if report_path is not None else {}),
        "counts": classification_counts,
        "actionableCount": sum(
            count
            for name, count in classification_counts.items()
            if name in ACTIONABLE_CLASSIFICATIONS
        ),
        "rows": [_row_to_dict(row, context.onboarding_root) for row in rows],
    }
    snapshot_path = drift_snapshot_path(
        context.coordination_root,
        repository=code_repository_root.name,
        branch=branch,
    )
    # The snapshot is a dashboard convenience: a write error must never fail the drift run
    # that produced it.
    with suppress(OSError):
        atomic_write_text(snapshot_path, json.dumps(payload, indent=2, default=str) + "\n")
