"""CLI adapter: rewrite knowledge JSON files in the canonical formatting, or check them.

    agents-remember knowledge-format [--check] PATH [PATH ...]

Each PATH is a file or a directory. A directory contributes its ``*.json`` files, excluding
generated route-index caches (``*.index.json``). The walk includes dot directories inside
``knowledge/`` and ``onboarding/`` of converted memory roots and skips them elsewhere.

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
from agents_remember.memory_quality.reference_state import is_converted_memory
from agents_remember.models.knowledge_files.canonical import CanonicalFormatError, format_bytes
from agents_remember.models.knowledge_files.documents import KNOWLEDGE_ROOT, ONBOARDING_ROOT

_KNOWLEDGE_ROOT_NAMES = frozenset({KNOWLEDGE_ROOT, ONBOARDING_ROOT})


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("paths", nargs="+", type=Path, help="Files or directories to format.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report files that are not canonical and write nothing.",
    )


def _directory_json_files(root: Path) -> Iterator[Path]:
    resolved_root = root.resolve()
    knowledge_roots = {
        directory / name
        for directory in resolved_root.parents
        if is_converted_memory(directory)
        for name in _KNOWLEDGE_ROOT_NAMES
    }
    for directory, subdirectories, filenames in root.walk():
        relative = directory.relative_to(root)
        resolved_directory = resolved_root / relative
        if resolved_directory not in knowledge_roots and knowledge_roots.isdisjoint(
            resolved_directory.parents
        ):
            if is_converted_memory(resolved_directory):
                knowledge_roots.update(resolved_directory / name for name in _KNOWLEDGE_ROOT_NAMES)
            subdirectories[:] = [name for name in subdirectories if not name.startswith(".")]
        for name in filenames:
            path = directory / name
            if path.match("*.json") and not is_excluded_from_knowledge(
                (relative / name).as_posix()
            ):
                yield path


def iter_json_files(paths: list[Path]) -> Iterator[Path]:
    """Yield each named file, and each non-cache ``*.json`` below each named directory, sorted."""

    for path in paths:
        if not path.is_dir():
            yield path
            continue
        yield from sorted(_directory_json_files(path))


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
