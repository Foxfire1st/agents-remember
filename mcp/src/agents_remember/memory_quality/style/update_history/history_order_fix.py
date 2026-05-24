"""Fix Update History sections with timestamped bullets into newest-first order."""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path
from typing import Any

from agents_remember.memory_quality.style.update_history.history_order import (
    BULLET_PATTERN,
    datetime_value,
    parse_timestamp,
    update_history_sections,
)


def fix_onboarding_root(onboarding_root: Path, *, dry_run: bool = False) -> dict[str, Any]:
    changed_files: list[str] = []
    skipped_files: list[str] = []
    files_checked = 0
    for path in sorted(onboarding_root.rglob("*.md")):
        if not path.is_file():
            continue
        files_checked += 1
        result = fix_file(path, dry_run=dry_run)
        relative = relative_path(path, onboarding_root)
        if result["changed"]:
            changed_files.append(relative)
        if result["skipped"]:
            skipped_files.append(relative)
    return {
        "ok": not skipped_files,
        "operation": "history_order_fix",
        "onboardingRoot": onboarding_root.as_posix(),
        "dryRun": dry_run,
        "filesChecked": files_checked,
        "changedFiles": changed_files,
        "skippedFiles": skipped_files,
    }


def fix_file(path: Path, *, dry_run: bool = False) -> dict[str, bool]:
    text = path.read_text(encoding="utf-8")
    trailing_newline = text.endswith("\n")
    lines = text.splitlines()
    updated, changed, skipped = sort_update_history_sections(lines)
    if changed and not dry_run:
        path.write_text("\n".join(updated) + ("\n" if trailing_newline else ""), encoding="utf-8")
    return {"changed": changed, "skipped": skipped}


def sort_update_history_sections(lines: list[str]) -> tuple[list[str], bool, bool]:
    updated = list(lines)
    changed = False
    skipped = False
    for start, end in reversed(update_history_sections(lines)):
        section = updated[start:end]
        sorted_section, section_skipped = sort_section(section)
        skipped = skipped or section_skipped
        if sorted_section == section:
            continue
        updated[start:end] = sorted_section
        changed = True
    return updated, changed, skipped


def sort_section(section: list[str]) -> tuple[list[str], bool]:
    prefix, blocks = parse_bullet_blocks(section)
    if not blocks:
        return section, False
    if any(block_timestamp(block) is None for block in blocks):
        return section, True
    sorted_blocks = sorted(
        enumerate(blocks),
        key=lambda item: (block_timestamp(item[1]) or dt.datetime.min, -item[0]),
        reverse=True,
    )
    result = list(prefix)
    for _, block in sorted_blocks:
        result.extend(block)
    return result, False


def parse_bullet_blocks(section: list[str]) -> tuple[list[str], list[list[str]]]:
    prefix: list[str] = []
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in section:
        if BULLET_PATTERN.match(line):
            if current is not None:
                blocks.append(current)
            current = [line]
            continue
        if current is None:
            prefix.append(line)
        else:
            current.append(line)
    if current is not None:
        blocks.append(current)
    return prefix, blocks


def block_timestamp(block: list[str]) -> dt.datetime | None:
    if not block:
        return None
    match = BULLET_PATTERN.match(block[0])
    if match is None:
        return None
    timestamp = parse_timestamp(match.group("body"))
    if timestamp is None:
        return None
    try:
        return datetime_value(timestamp)
    except ValueError:
        return None


def relative_path(path: Path, onboarding_root: Path) -> str:
    try:
        return path.relative_to(onboarding_root).as_posix()
    except ValueError:
        return path.as_posix()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sort onboarding Update History bullets newest-first."
    )
    parser.add_argument("onboarding_root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    result = fix_onboarding_root(args.onboarding_root, dry_run=args.dry_run)
    print_result(result)
    return 0 if result["ok"] else 1


def print_result(result: dict[str, Any]) -> None:
    print(f"filesChecked={result['filesChecked']}")
    print(f"changedFiles={len(result['changedFiles'])}")
    for path in result["changedFiles"]:
        print(f"changed {path}")
    if result["skippedFiles"]:
        print(f"skippedFiles={len(result['skippedFiles'])}")
        for path in result["skippedFiles"]:
            print(f"skipped {path}")


if __name__ == "__main__":
    raise SystemExit(main())
