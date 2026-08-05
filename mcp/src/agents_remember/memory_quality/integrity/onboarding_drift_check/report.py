"""Drift report rendering (markdown/text/json/csv) and report path resolution."""

from __future__ import annotations

import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.memory_quality.integrity.onboarding_drift_check.discovery import (
    parse_table_metadata,
    rel,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.git_ops import (
    current_branch_name,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.models import (
    CLASSIFICATIONS,
    DriftRow,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.unclaimed_entities import (
    UnclaimedEntityReport,
    rank_unclaimed_entity_sources,
)


def sanitize_report_token(token: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", token.strip())
    normalized = normalized.strip(".-_")
    return normalized or "unknown"


def default_report_filename(repo_root: Path) -> str:
    repo_name = sanitize_report_token(repo_root.name)
    branch_name = sanitize_report_token(current_branch_name(repo_root))
    return f"{repo_name}_{branch_name}_drift-report.md"


def default_report_dir(temp_root: Path, repo_root: Path) -> Path:
    return temp_root / "drift-reports" / sanitize_report_token(repo_root.name)


def default_report_path(temp_root: Path, repo_root: Path) -> Path:
    return default_report_dir(temp_root, repo_root) / default_report_filename(repo_root)


def counts(rows: list[DriftRow]) -> dict[str, int]:
    return {name: sum(1 for row in rows if row.classification == name) for name in CLASSIFICATIONS}


def _entity_catalog_path(onboarding_root: Path) -> Path | None:
    for path in sorted(onboarding_root.rglob("*.md")):
        if parse_table_metadata(path).get("doc_type") == "repo-entity-catalog":
            return path
    return None


def _append_unclaimed_entity_report(lines: list[str], report: UnclaimedEntityReport) -> None:
    lines.extend(
        [
            "",
            "## Ranked Unclaimed Entity Sources (report only)",
            "",
            (
                f"The entity register claims {report.claimed_source_count} of "
                f"{report.source_count} tracked repository files; {report.unclaimed_source_count} "
                f"are unclaimed; ranked count: {len(report.ranked)}."
            ),
            "",
            (
                "All tracked repository files participate in the unclaimed count. Only Python "
                "declaration signals are currently ranked; non-Python artifacts remain counted "
                "but unranked, as do ordinary Python modules with no ranking declaration."
            ),
            "",
            (
                "Ranking signals, in order: versioned contract plus `StoreOwnership` authority; "
                "versioned contract; authority; schema. Counts and source path break ties."
            ),
            "",
            "| Priority | Source | Versioned contracts | Authority declarations | Schemas |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    if not report.ranked:
        lines.append("| _None_ |  |  |  |  |")
    for source in report.ranked:
        lines.append(
            "| "
            + " | ".join(
                [
                    source.priority,
                    f"`{source.path}`",
                    ", ".join(f"`{value}`" for value in source.versioned_contracts),
                    ", ".join(f"`{value}`" for value in source.authority_declarations),
                    ", ".join(f"`{value}`" for value in source.schema_declarations),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            (
                "Remediation: review the highest-ranked files. Add a file to an existing "
                "entity's `Evidence Paths` when it defines that entity, or define a new entity "
                "when the contract or authority is a distinct reusable concept; then recompute "
                "the affected `git-blob-set-v1` fingerprint. This section never changes drift "
                "classifications or the command's exit status."
            ),
        ]
    )


def write_markdown_report(
    rows: list[DriftRow], report_path: Path, repo_root: Path, onboarding_root: Path
) -> None:
    generated = dt.datetime.now().astimezone().replace(microsecond=0).isoformat()
    head = run_git(repo_root, ["rev-parse", "--short", "HEAD"])
    head_text = head.stdout.strip() if head.returncode == 0 else "unknown"
    summary = counts(rows)
    actionable = [row for row in rows if row.classification != "up to date"]

    lines: list[str] = [
        "# Onboarding Drift Report",
        "",
        f"**Scope checked:** `{onboarding_root.as_posix()}`",
        f"**Generated:** {generated}",
        f"**Repository HEAD:** `{head_text}`",
        "",
        "## Summary",
        "",
        "| Classification | Count |",
        "| --- | ---: |",
    ]
    for name in CLASSIFICATIONS:
        lines.append(f"| {name} | {summary[name]} |")

    lines.extend(
        [
            "",
            "## Actionable Findings",
            "",
            "| Onboarding unit | Source file | Storage | Classification | Trust | Likely affected sections | Note |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    if actionable:
        for row in actionable:
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{row.onboarding_file}`",
                        f"`{row.source_file}`" if row.source_file else "",
                        row.storage_mode,
                        row.classification,
                        row.trust,
                        row.affected_sections,
                        row.note.replace("|", "\\|"),
                    ]
                )
                + " |"
            )
    else:
        lines.append("| _None_ |  |  |  |  |  |")

    entity_catalog = _entity_catalog_path(onboarding_root)
    if entity_catalog is not None:
        _append_unclaimed_entity_report(
            lines,
            rank_unclaimed_entity_sources(repo_root, entity_catalog),
        )
    lines.append("")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")


def resolve_report_path(
    report_path: Path | None,
    coordination_root: Path,
    temp_root: Path,
    repo_root: Path,
    memory_root: Path | None = None,
) -> Path:
    if report_path is None:
        return default_report_path(temp_root, repo_root)
    if (
        memory_root is not None
        and report_path.is_absolute()
        and report_path.resolve().is_relative_to(memory_root.resolve())
    ):
        return default_report_dir(temp_root, repo_root) / report_path.name
    if report_path.is_absolute():
        if report_path.resolve().is_relative_to(coordination_root.resolve()):
            return report_path
        return default_report_dir(temp_root, repo_root) / report_path.name
    candidate = temp_root / report_path
    if candidate.resolve().is_relative_to(temp_root.resolve()):
        return candidate
    return default_report_dir(temp_root, repo_root) / report_path.name


def print_text(rows: list[DriftRow], _onboarding_root: Path) -> None:
    for row in rows:
        print(
            f"{row.onboarding_file}\t"
            f"{row.source_file}\t"
            f"{row.storage_mode}\t"
            f"{row.classification}\t"
            f"{row.trust}\t"
            f"{row.note}"
        )


def print_json(rows: list[DriftRow], onboarding_root: Path) -> None:
    payload = [
        {
            "onboarding_file": rel(row.onboarding_file, onboarding_root),
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
        for row in rows
    ]
    print(json.dumps(payload, indent=2))


def print_csv(rows: list[DriftRow], onboarding_root: Path) -> None:
    writer = csv.DictWriter(
        sys.stdout,
        fieldnames=[
            "onboarding_file",
            "storage_mode",
            "source_file",
            "repository",
            "last_verified_hash",
            "last_verified_date",
            "classification",
            "trust",
            "affected_sections",
            "note",
        ],
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "onboarding_file": rel(row.onboarding_file, onboarding_root),
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
        )
