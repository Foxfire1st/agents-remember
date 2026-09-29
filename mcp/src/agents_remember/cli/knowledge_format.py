"""CLI adapter: rewrite knowledge JSON files in the canonical formatting, or check them.

    agents-remember knowledge-format [--check] PATH [PATH ...]

Each PATH is a file or a directory. A directory contributes every ``*.json`` file below it except
the generated route-index cache (``*.index.json``) and hidden directories such as ``.ar-index``.

The formatter changes formatting only (:mod:`agents_remember.models.knowledge_files.canonical`);
it never validates or changes content, and a file it cannot parse is reported and left untouched.

Exit status: 0 when every file is canonical (after rewriting, without ``--check``); 1 when
``--check`` found a file that is not canonical; 2 when a file could not be parsed or read.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from pathlib import Path

from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.memory_quality.knowledge_validator.trees import is_excluded_from_knowledge
from agents_remember.models.knowledge_files.canonical import CanonicalFormatError, format_bytes


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("paths", nargs="+", type=Path, help="Files or directories to format.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report files that are not canonical and write nothing.",
    )


def iter_json_files(paths: list[Path]) -> Iterator[Path]:
    """Yield each named file, and each non-cache ``*.json`` below each named directory, sorted."""

    for path in paths:
        if not path.is_dir():
            yield path
            continue
        for candidate in sorted(path.rglob("*.json")):
            if is_excluded_from_knowledge(candidate.relative_to(path).as_posix()):
                continue
            yield candidate


def run(args: argparse.Namespace) -> int:
    status = 0
    for path in iter_json_files(list(args.paths)):
        try:
            original = path.read_bytes()
            formatted = format_bytes(original)
        except (OSError, CanonicalFormatError) as error:
            print(f"invalid {path}: {error}")
            status = 2
            continue
        if formatted == original:
            continue
        if args.check:
            print(f"not canonical {path}")
            status = max(status, 1)
            continue
        atomic_write_bytes(path, formatted)
        print(f"reformatted {path}")
    return status
