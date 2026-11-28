"""CLI adapter for ``agents-remember languages scan`` and ``agents-remember languages warm``."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from agents_remember.errors import AgentsRememberError
from agents_remember.memory_quality.style.citations.language_scanner import (
    all_catalog_language_ids,
    scan_repository_languages,
    warm_repository_languages,
)


@dataclass(frozen=True)
class LanguagesCliArgs:
    action: str
    repo: Path
    cache_dir: Path | None
    languages: tuple[str, ...]
    all_languages: bool
    dry_run: bool
    as_json: bool


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="languages_action", required=True)
    scan = sub.add_parser("scan", help="Scan a repository's tracked files for catalog languages.")
    scan.add_argument(
        "--repo", default=".", help="Repository root to scan (default: current directory)."
    )
    scan.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON output.")

    warm = sub.add_parser(
        "warm",
        help="Scan a repository (or explicit language IDs) and pre-warm AR_LANGUAGE_CACHE.",
    )
    warm.add_argument(
        "--repo", default=".", help="Repository root to scan (default: current directory)."
    )
    warm.add_argument(
        "--cache-dir",
        default=None,
        help="Override AR_LANGUAGE_CACHE target directory.",
    )
    warm.add_argument(
        "--language",
        action="append",
        dest="languages",
        default=[],
        help="Explicit catalog language ID to warm (may be repeated; skips repo auto-scan filter).",
    )
    warm.add_argument(
        "--all",
        action="store_true",
        dest="all_languages",
        help="Warm every pinned language in the global catalog (for CI and offline caches).",
    )
    warm.add_argument(
        "--dry-run", action="store_true", help="Report what would be warmed without downloading."
    )
    warm.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON output.")


def _parse_args(args: argparse.Namespace) -> LanguagesCliArgs:
    return LanguagesCliArgs(
        action=str(args.languages_action),
        repo=Path(str(args.repo)).expanduser().resolve(),
        cache_dir=None if getattr(args, "cache_dir", None) is None else Path(str(args.cache_dir)),
        languages=tuple(getattr(args, "languages", ()) or ()),
        all_languages=bool(getattr(args, "all_languages", False)),
        dry_run=bool(getattr(args, "dry_run", False)),
        as_json=bool(getattr(args, "as_json", False)),
    )


def run(args: argparse.Namespace) -> int:
    parsed = _parse_args(args)
    try:
        if parsed.action == "scan":
            report = scan_repository_languages(parsed.repo)
            payload = report.to_payload()
            if parsed.as_json:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(
                    f"repo={report.repo_root} files={report.file_count} "
                    f"detected={','.join(report.detected) or 'none'} "
                    f"ambiguous={','.join(report.ambiguous_suffixes) or 'none'}"
                )
            return 0
        if parsed.action == "warm":
            explicit = (
                parsed.languages
                if parsed.languages
                else (all_catalog_language_ids() if parsed.all_languages else None)
            )
            warm = warm_repository_languages(
                parsed.repo,
                languages=explicit,
                cache_root=parsed.cache_dir,
                dry_run=parsed.dry_run,
            )
            payload = warm.to_payload()
            if parsed.as_json:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(
                    f"cache={warm.cache_root} warmed={','.join(warm.warmed) or 'none'} "
                    f"cached={','.join(warm.already_cached) or 'none'} "
                    f"failed={','.join(sorted(warm.failed)) or 'none'}"
                )
            return 0 if warm.ok else 1
    except AgentsRememberError as error:
        print(f"languages {parsed.action} error: {error}", file=sys.stderr)
        return 1
    return 2
