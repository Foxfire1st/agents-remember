#!/usr/bin/env python3
"""Check Agents Remember file-level onboarding drift.

Requires Python 3.9+ and git. Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


RESOLVER_SCRIPT_DIR = Path(__file__).resolve().parents[2] / "C-08-ar-management-resolver" / "scripts"
RESOLVER_MODULE_PATH = RESOLVER_SCRIPT_DIR / "ar_management_resolver.py"
RESOLVER_SPEC = importlib.util.spec_from_file_location("ar_management_resolver", RESOLVER_MODULE_PATH)
if RESOLVER_SPEC is None or RESOLVER_SPEC.loader is None:
    raise ImportError(f"Unable to load C-08 resolver module from {RESOLVER_MODULE_PATH}")
ar_management_resolver = importlib.util.module_from_spec(RESOLVER_SPEC)
sys.modules[RESOLVER_SPEC.name] = ar_management_resolver
RESOLVER_SPEC.loader.exec_module(ar_management_resolver)

StorageSettings = ar_management_resolver.StorageSettings
clean_scalar = ar_management_resolver.clean_scalar
normalize_rel_path = ar_management_resolver.normalize_rel_path
resolve_management_context = ar_management_resolver.resolve_management_context
resolve_storage_for_source = ar_management_resolver.resolve_storage_for_source
sidecar_storage_label = ar_management_resolver.sidecar_storage_label


CLASSIFICATIONS = (
    "up to date",
    "drifted",
    "missing verification",
    "missing",
    "orphaned",
    "disabled",
    "unsupported",
)
ACTIONABLE_CLASSIFICATIONS = {"drifted", "missing verification", "missing", "orphaned", "unsupported"}
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


def run_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", f"safe.directory={repo_root.as_posix()}", *args],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def list_repo_sources(repo_root: Path) -> list[str]:
    result = run_git(repo_root, ["ls-files", "-z"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git ls-files failed")
    return [normalize_rel_path(value) for value in result.stdout.split("\0") if value]


def mirror_onboarding_path(onboarding_root: Path, source_file: str) -> Path:
    return onboarding_root / f"{normalize_rel_path(source_file)}.md"


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


def discover_inline_onboarding_sources(repo_root: Path, settings: StorageSettings) -> list[str]:
    inline_sources: list[str] = []
    for source_file in list_repo_sources(repo_root):
        if resolve_storage_for_source(source_file, settings, repo_root.name) != "inline":
            continue

        source_path = repo_root / source_file
        if not source_path.exists():
            continue

        try:
            source_text = source_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        if extract_inline_onboarding_block(source_text) is not None:
            inline_sources.append(source_file)

    return sorted(inline_sources)


def classify_external_onboarding(onboarding_file: Path, repo_root: Path) -> DriftRow:
    metadata = parse_table_metadata(onboarding_file)
    repository = metadata.get("repository", "")
    source_file = normalize_rel_path(metadata.get("path", ""))
    last_hash = metadata.get("lastVerifiedCommitHash", "")
    last_date = metadata.get("lastVerifiedCommitDate", "")

    if not source_file or not last_hash:
        return DriftRow(
            onboarding_file=onboarding_file.as_posix(),
            source_file=source_file,
            repository=repository,
            storage_mode="external",
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
            onboarding_file=onboarding_file.as_posix(),
            source_file=source_file,
            repository=repository,
            storage_mode="external",
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
            onboarding_file=onboarding_file.as_posix(),
            source_file=source_file,
            repository=repository,
            storage_mode="external",
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
            onboarding_file=onboarding_file.as_posix(),
            source_file=source_file,
            repository=repository,
            storage_mode="external",
            last_verified_hash=last_hash,
            last_verified_date=last_date,
            classification="up to date",
            trust="high",
            affected_sections="none",
            note="No source diff since recorded verification commit.",
        )
    if diff.returncode == 1:
        return DriftRow(
            onboarding_file=onboarding_file.as_posix(),
            source_file=source_file,
            repository=repository,
            storage_mode="external",
            last_verified_hash=last_hash,
            last_verified_date=last_date,
            classification="drifted",
            trust="medium",
            affected_sections="logic; invariants; conventions; docs references",
            note="Source changed since recorded verification commit.",
        )

    return DriftRow(
        onboarding_file=onboarding_file.as_posix(),
        source_file=source_file,
        repository=repository,
        storage_mode="external",
        last_verified_hash=last_hash,
        last_verified_date=last_date,
        classification="drifted",
        trust="medium",
        affected_sections="logic; invariants; metadata",
        note=f"git diff failed: {diff.stderr.strip() or 'unknown git error'}",
    )


def classify_external_source(source_file: str, repo_root: Path, onboarding_root: Path) -> DriftRow:
    onboarding_file = mirror_onboarding_path(onboarding_root, source_file)
    onboarding_ref = rel(onboarding_file, onboarding_root)
    if not onboarding_file.exists():
        return DriftRow(
            onboarding_file=onboarding_ref,
            source_file=normalize_rel_path(source_file),
            repository=repo_root.name,
            storage_mode="external",
            last_verified_hash="",
            last_verified_date="",
            classification="missing",
            trust="low",
            affected_sections="all; onboarding missing",
            note="Mirrored onboarding file is missing for this sidecar-managed source.",
        )
    row = classify_external_onboarding(onboarding_file, repo_root)
    row.onboarding_file = onboarding_ref
    return row


def classify_sidecar_onboarding(
    onboarding_file: Path,
    repo_root: Path,
    onboarding_root: Path,
    settings: StorageSettings,
) -> DriftRow:
    metadata = parse_table_metadata(onboarding_file)
    source_file = normalize_rel_path(metadata.get("path", ""))
    storage_mode = resolve_storage_for_source(source_file, settings, repo_root.name) if source_file else settings.mode
    onboarding_ref = rel(onboarding_file, onboarding_root)
    if storage_mode == "disabled":
        return DriftRow(
            onboarding_file=onboarding_ref,
            source_file=source_file,
            repository=metadata.get("repository", repo_root.name),
            storage_mode="disabled",
            last_verified_hash=metadata.get("lastVerifiedCommitHash", ""),
            last_verified_date=metadata.get("lastVerifiedCommitDate", ""),
            classification="disabled",
            trust="high",
            affected_sections="none",
            note="Source path is excluded by pathRules.",
        )
    if not sidecar_storage_label(storage_mode):
        return DriftRow(
            onboarding_file=onboarding_ref,
            source_file=source_file,
            repository=metadata.get("repository", repo_root.name),
            storage_mode=storage_mode,
            last_verified_hash=metadata.get("lastVerifiedCommitHash", ""),
            last_verified_date=metadata.get("lastVerifiedCommitDate", ""),
            classification="unsupported",
            trust="low",
            affected_sections="resolver; storage configuration",
            note=f"Sidecar onboarding exists but the source path resolves to '{storage_mode}'.",
        )
    row = classify_external_onboarding(onboarding_file, repo_root)
    row.onboarding_file = onboarding_ref
    row.storage_mode = storage_mode
    return row


@dataclass
class InlineBlock:
    raw_text: str
    metadata: dict[str, str]


def line_bounds(text: str, index: int) -> tuple[int, int]:
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    if end == -1:
        end = len(text)
    return start, end


def expand_inline_bounds(source_text: str, start_index: int, end_index: int) -> tuple[int, int]:
    start_line_start, _ = line_bounds(source_text, start_index)
    previous_line_end = start_line_start - 1
    previous_line_start = source_text.rfind("\n", 0, max(previous_line_end, 0)) + 1
    previous_line = source_text[previous_line_start:previous_line_end].strip() if start_line_start > 0 else ""
    block_start = start_line_start
    expected_end = COMMON_BLOCK_DELIMITERS.get(previous_line)
    if expected_end:
        block_start = previous_line_start

    _, end_line_end = line_bounds(source_text, end_index)
    block_end = end_line_end
    if expected_end and end_line_end < len(source_text):
        next_line_start = end_line_end + 1
        next_line_end = source_text.find("\n", next_line_start)
        if next_line_end == -1:
            next_line_end = len(source_text)
        next_line = source_text[next_line_start:next_line_end].strip()
        if next_line == expected_end:
            block_end = next_line_end
    if block_end < len(source_text) and source_text[block_end:block_end + 1] == "\n":
        block_end += 1
    return block_start, block_end


def extract_inline_onboarding_block(source_text: str) -> InlineBlock | None:
    start_index = source_text.find(INLINE_START_MARKER)
    end_index = source_text.find(INLINE_END_MARKER)
    if start_index == -1 or end_index == -1 or end_index < start_index:
        return None

    block_start, block_end = expand_inline_bounds(source_text, start_index, end_index)
    raw_text = source_text[block_start:block_end]
    metadata: dict[str, str] = {}
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped in {INLINE_START_MARKER, INLINE_END_MARKER}:
            continue
        if stripped.startswith(("/*", "*/", "<!--", "-->", '"""', "'''", "=begin", "=end", "{-", "-}", "(*", "*)")):
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        metadata[key.strip()] = clean_scalar(value)
    return InlineBlock(raw_text=raw_text, metadata=metadata)


def compute_inline_source_digest(source_text: str, block: InlineBlock) -> str:
    source_without_block = source_text.replace(block.raw_text, "", 1)
    normalized_source = source_without_block.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized_source.encode("utf-8")).hexdigest()


def classify_inline_source(source_file: str, repo_root: Path) -> DriftRow:
    source_path = repo_root / normalize_rel_path(source_file)
    if not source_path.exists():
        return DriftRow(
            onboarding_file=f"inline:{normalize_rel_path(source_file)}",
            source_file=normalize_rel_path(source_file),
            repository=repo_root.name,
            storage_mode="inline",
            last_verified_hash="",
            last_verified_date="",
            classification="orphaned",
            trust="low",
            affected_sections="all; source missing",
            note="Inline onboarding source file no longer exists.",
        )

    try:
        source_text = source_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return DriftRow(
            onboarding_file=f"inline:{normalize_rel_path(source_file)}",
            source_file=normalize_rel_path(source_file),
            repository=repo_root.name,
            storage_mode="inline",
            last_verified_hash="",
            last_verified_date="",
            classification="unsupported",
            trust="low",
            affected_sections="verification; encoding",
            note="Source file is not UTF-8 text, so inline onboarding cannot be parsed safely.",
        )

    block = extract_inline_onboarding_block(source_text)
    if block is None:
        return DriftRow(
            onboarding_file=f"inline:{normalize_rel_path(source_file)}",
            source_file=normalize_rel_path(source_file),
            repository=repo_root.name,
            storage_mode="inline",
            last_verified_hash="",
            last_verified_date="",
            classification="missing",
            trust="low",
            affected_sections="all; onboarding missing",
            note="Inline onboarding block is missing.",
        )

    source_digest = block.metadata.get("sourceDigest", "")
    verified_at = block.metadata.get("verifiedAt", "")
    if source_digest.startswith("sha256:"):
        source_digest = source_digest.split(":", 1)[1]
    if not source_digest:
        return DriftRow(
            onboarding_file=f"inline:{normalize_rel_path(source_file)}",
            source_file=normalize_rel_path(source_file),
            repository=repo_root.name,
            storage_mode="inline",
            last_verified_hash="",
            last_verified_date=verified_at,
            classification="missing verification",
            trust="medium",
            affected_sections="metadata; verification",
            note="Inline onboarding block is missing sourceDigest metadata.",
        )

    computed_digest = compute_inline_source_digest(source_text, block)
    classification = "up to date" if computed_digest == source_digest else "drifted"
    return DriftRow(
        onboarding_file=f"inline:{normalize_rel_path(source_file)}",
        source_file=normalize_rel_path(source_file),
        repository=repo_root.name,
        storage_mode="inline",
        last_verified_hash=source_digest,
        last_verified_date=verified_at,
        classification=classification,
        trust="high" if classification == "up to date" else "medium",
        affected_sections="none" if classification == "up to date" else "logic; invariants; metadata",
        note=(
            "Inline source digest matches the current source body."
            if classification == "up to date"
            else "Source body changed since the recorded inline sourceDigest was computed."
        ),
    )


def classify_source(source_file: str, repo_root: Path, onboarding_root: Path, settings: StorageSettings) -> DriftRow:
    storage_mode = resolve_storage_for_source(source_file, settings, repo_root.name)
    if storage_mode == "disabled":
        return DriftRow(
            onboarding_file=f"disabled:{normalize_rel_path(source_file)}",
            source_file=normalize_rel_path(source_file),
            repository=repo_root.name,
            storage_mode="disabled",
            last_verified_hash="",
            last_verified_date="",
            classification="disabled",
            trust="high",
            affected_sections="none",
            note="Source path is excluded by pathRules.",
        )
    if sidecar_storage_label(storage_mode):
        row = classify_external_source(source_file, repo_root, onboarding_root)
        row.storage_mode = storage_mode
        return row
    if storage_mode == "inline":
        return classify_inline_source(source_file, repo_root)
    return DriftRow(
        onboarding_file=f"unsupported:{normalize_rel_path(source_file)}",
        source_file=normalize_rel_path(source_file),
        repository=repo_root.name,
        storage_mode=storage_mode,
        last_verified_hash="",
        last_verified_date="",
        classification="unsupported",
        trust="low",
        affected_sections="resolver; storage configuration",
        note=f"Unsupported storage mode '{storage_mode}'.",
    )


def counts(rows: list[DriftRow]) -> dict[str, int]:
    return {name: sum(1 for row in rows if row.classification == name) for name in CLASSIFICATIONS}


def rel(path: Path | str, base: Path) -> str:
    if isinstance(path, str):
        return path
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path, help="Path to the source repository root.")
    parser.add_argument(
        "--onboarding-root",
        type=Path,
        help="Compatibility override for the resolved repo onboarding root.",
    )
    parser.add_argument(
        "--topology",
        choices=("internal", "shared"),
        help="Management topology for this repo. Defaults to internal when no onboarding root is supplied.",
    )
    parser.add_argument(
        "--shared-root",
        type=Path,
        help="Shared ar-management root. Required for --topology shared unless --onboarding-root is supplied.",
    )
    parser.add_argument(
        "--settings-path",
        type=Path,
        help="Override the active settings.md path for this run.",
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
    if not repo_root.exists():
        parser.error(f"repo path does not exist: {repo_root}")
    try:
        context = resolve_management_context(
            repo_name=repo_root.name,
            workspace_root=repo_root.parent,
            requested_topology=args.topology,
            shared_root=args.shared_root,
            settings_path=args.settings_path,
            onboarding_root=args.onboarding_root,
            target_repo=repo_root,
        )
    except ValueError as error:
        parser.error(str(error))
    if not context.onboarding_root.exists():
        parser.error(f"onboarding root does not exist: {context.onboarding_root}")

    git_check = run_git(repo_root, ["rev-parse", "--show-toplevel"])
    if git_check.returncode != 0:
        parser.error(f"repo path is not a git repository: {repo_root}\n{git_check.stderr.strip()}")
    settings = context.storage
    rows = [
        classify_sidecar_onboarding(path, repo_root, context.onboarding_root, settings)
        for path in discover_onboarding_files(context.onboarding_root)
    ]
    rows.extend(classify_inline_source(path, repo_root) for path in discover_inline_onboarding_sources(repo_root, settings))
    rows.sort(key=lambda row: (row.source_file, row.onboarding_file))

    if args.report:
        write_markdown_report(rows, args.report.resolve(), repo_root, context.onboarding_root)

    if args.format == "json":
        print_json(rows, context.onboarding_root)
    elif args.format == "csv":
        print_csv(rows, context.onboarding_root)
    else:
        print_text(rows, context.onboarding_root)

    if args.fail_on_actionable and any(row.classification in ACTIONABLE_CLASSIFICATIONS for row in rows):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
