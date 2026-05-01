#!/usr/bin/env python3
"""Check Agents Remember file-level onboarding drift.

Requires Python 3.9+ and git. Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


CLASSIFICATIONS = ("up to date", "drifted", "missing verification", "orphaned")


@dataclass
class DriftRow:
    onboarding_file: Path
    source_file: str
    repository: str
    last_verified_hash: str
    last_verified_date: str
    classification: str
    trust: str
    affected_sections: str
    note: str


def run_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", f"safe.directory={repo_root.as_posix()}", *args],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def parse_table_metadata(path: Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        key, value = cells[0], cells[1]
        if key in {"Field", "---", "----------------------"}:
            continue
        if value.startswith("`") and value.endswith("`"):
            value = value[1:-1]
        metadata[key] = value
    return metadata


def is_file_level_onboarding(path: Path) -> bool:
    try:
        metadata = parse_table_metadata(path)
    except UnicodeDecodeError:
        return False
    return metadata.get("doc_type") == "file-level-onboarding"


def discover_onboarding_files(onboarding_root: Path) -> list[Path]:
    return sorted(
        path
        for path in onboarding_root.rglob("*.md")
        if path.is_file() and is_file_level_onboarding(path)
    )


def filter_by_sources(rows: list[Path], source_filters: set[str]) -> list[Path]:
    if not source_filters:
        return rows
    selected: list[Path] = []
    normalized_filters = {normalize_rel_path(value) for value in source_filters}
    for path in rows:
        metadata = parse_table_metadata(path)
        source = normalize_rel_path(metadata.get("path", ""))
        if source in normalized_filters:
            selected.append(path)
    return selected


def normalize_rel_path(value: str) -> str:
    return value.replace("\\", "/").strip().strip("/")


def classify(onboarding_file: Path, repo_root: Path) -> DriftRow:
    metadata = parse_table_metadata(onboarding_file)
    repository = metadata.get("repository", "")
    source_file = normalize_rel_path(metadata.get("path", ""))
    last_hash = metadata.get("lastVerifiedCommitHash", "")
    last_date = metadata.get("lastVerifiedCommitDate", "")

    if not source_file or not last_hash:
        return DriftRow(
            onboarding_file=onboarding_file,
            source_file=source_file,
            repository=repository,
            last_verified_hash=last_hash,
            last_verified_date=last_date,
            classification="missing verification",
            trust="medium",
            affected_sections="metadata; verification",
            note="Missing source path or lastVerifiedCommitHash.",
        )

    source_path = repo_root / source_file
    if not source_path.exists():
        return DriftRow(
            onboarding_file=onboarding_file,
            source_file=source_file,
            repository=repository,
            last_verified_hash=last_hash,
            last_verified_date=last_date,
            classification="orphaned",
            trust="low",
            affected_sections="all; source missing",
            note="Source file no longer exists.",
        )

    rev = f"{last_hash}^{{commit}}"
    exists = run_git(repo_root, ["cat-file", "-e", rev])
    if exists.returncode != 0:
        return DriftRow(
            onboarding_file=onboarding_file,
            source_file=source_file,
            repository=repository,
            last_verified_hash=last_hash,
            last_verified_date=last_date,
            classification="drifted",
            trust="medium",
            affected_sections="logic; invariants; metadata",
            note="Recorded verification commit is not available in git history.",
        )

    diff = run_git(repo_root, ["diff", "--quiet", last_hash, "HEAD", "--", source_file])
    if diff.returncode == 0:
        return DriftRow(
            onboarding_file=onboarding_file,
            source_file=source_file,
            repository=repository,
            last_verified_hash=last_hash,
            last_verified_date=last_date,
            classification="up to date",
            trust="high",
            affected_sections="none",
            note="No source diff since recorded verification commit.",
        )
    if diff.returncode == 1:
        return DriftRow(
            onboarding_file=onboarding_file,
            source_file=source_file,
            repository=repository,
            last_verified_hash=last_hash,
            last_verified_date=last_date,
            classification="drifted",
            trust="medium",
            affected_sections="logic; invariants; conventions; docs references",
            note="Source changed since recorded verification commit.",
        )

    return DriftRow(
        onboarding_file=onboarding_file,
        source_file=source_file,
        repository=repository,
        last_verified_hash=last_hash,
        last_verified_date=last_date,
        classification="drifted",
        trust="medium",
        affected_sections="logic; invariants; metadata",
        note=f"git diff failed: {diff.stderr.strip() or 'unknown git error'}",
    )


def counts(rows: list[DriftRow]) -> dict[str, int]:
    return {name: sum(1 for row in rows if row.classification == name) for name in CLASSIFICATIONS}


def rel(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def write_markdown_report(rows: list[DriftRow], report_path: Path, repo_root: Path, onboarding_root: Path) -> None:
    generated = dt.datetime.now().astimezone().replace(microsecond=0).isoformat()
    head = run_git(repo_root, ["rev-parse", "--short", "HEAD"])
    head_text = head.stdout.strip() if head.returncode == 0 else "unknown"
    summary = counts(rows)
    actionable = [row for row in rows if row.classification != "up to date"]
    clean = [row for row in rows if row.classification == "up to date"]

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
            "| Onboarding file | Source file | Classification | Trust | Likely affected sections | Note |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    if actionable:
        for row in actionable:
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{rel(row.onboarding_file, onboarding_root)}`",
                        f"`{row.source_file}`" if row.source_file else "",
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

    lines.extend(
        [
            "",
            "## Up To Date",
            "",
            "| Source file | Onboarding file |",
            "| --- | --- |",
        ]
    )
    if clean:
        for row in clean:
            lines.append(f"| `{row.source_file}` | `{rel(row.onboarding_file, onboarding_root)}` |")
    else:
        lines.append("| _None_ |  |")

    lines.append("")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")


def print_text(rows: list[DriftRow], onboarding_root: Path) -> None:
    for row in rows:
        print(
            f"{rel(row.onboarding_file, onboarding_root)}\t"
            f"{row.source_file}\t"
            f"{row.classification}\t"
            f"{row.trust}\t"
            f"{row.note}"
        )


def print_json(rows: list[DriftRow], onboarding_root: Path) -> None:
    payload = [
        {
            "onboarding_file": rel(row.onboarding_file, onboarding_root),
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path, help="Path to the source repository root.")
    parser.add_argument(
        "--onboarding-root",
        required=True,
        type=Path,
        help="Path to the repo onboarding root, e.g. ar-management/onboarding/my-repo.",
    )
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        help="Limit to a repo-relative source path. May be passed multiple times.",
    )
    parser.add_argument("--report", type=Path, help="Optional Markdown report output path.")
    parser.add_argument("--format", choices=("text", "json", "csv"), default="text", help="Stdout format.")
    parser.add_argument(
        "--fail-on-actionable",
        action="store_true",
        help="Exit with code 1 when drifted, missing-verification, or orphaned files are found.",
    )
    args = parser.parse_args(argv)

    repo_root = args.repo.resolve()
    onboarding_root = args.onboarding_root.resolve()
    if not repo_root.exists():
        parser.error(f"repo path does not exist: {repo_root}")
    if not onboarding_root.exists():
        parser.error(f"onboarding root does not exist: {onboarding_root}")

    git_check = run_git(repo_root, ["rev-parse", "--show-toplevel"])
    if git_check.returncode != 0:
        parser.error(f"repo path is not a git repository: {repo_root}\n{git_check.stderr.strip()}")

    onboarding_files = discover_onboarding_files(onboarding_root)
    onboarding_files = filter_by_sources(onboarding_files, set(args.source))
    rows = [classify(path, repo_root) for path in onboarding_files]

    if args.report:
        write_markdown_report(rows, args.report.resolve(), repo_root, onboarding_root)

    if args.format == "json":
        print_json(rows, onboarding_root)
    elif args.format == "csv":
        print_csv(rows, onboarding_root)
    else:
        print_text(rows, onboarding_root)

    if args.fail_on_actionable and any(row.classification != "up to date" for row in rows):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
