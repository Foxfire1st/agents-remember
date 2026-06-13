"""Bounded drift summary helpers for context packets."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_remember.memory_quality.integrity.onboarding_drift_check import drift
from agents_remember.memory_quality.integrity.onboarding_drift_check.models import (
    ACTIONABLE_CLASSIFICATIONS,
)
from agents_remember.observer.paths import DRIFT_SNAPSHOT_SCHEMA, drift_snapshot_dir


def not_checked() -> dict[str, Any]:
    return {"status": "notChecked"}


def run_drift_summary(
    *,
    code_repository_root: Path,
    context: Any,
    detail_limit: int = 10,
) -> dict[str, Any]:
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
    _write_drift_snapshot(code_repository_root, context, rows)
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
) -> dict[str, Any]:
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


def _write_drift_snapshot(code_repository_root: Path, context: Any, rows: list[Any]) -> None:
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
        "counts": classification_counts,
        "actionableCount": sum(
            count
            for name, count in classification_counts.items()
            if name in ACTIONABLE_CLASSIFICATIONS
        ),
        "rows": [_row_to_dict(row, context.onboarding_root) for row in rows],
    }
    repo_token = drift.sanitize_report_token(code_repository_root.name)
    branch_token = drift.sanitize_report_token(branch)
    snapshot_path = drift_snapshot_dir(context.coordination_root) / f"{repo_token}__{branch_token}.json"
    try:
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = snapshot_path.with_name(f"{snapshot_path.name}.tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
        os.replace(tmp, snapshot_path)
    except OSError:
        pass  # the snapshot is a dashboard convenience; never fail the drift run for it
