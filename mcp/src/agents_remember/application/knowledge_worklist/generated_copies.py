"""Compare declared copies with canonical bytes in the candidate code tree.

An absent repository declaration changes nothing. A present declaration that cannot
be read makes the caller incomplete; it never consults installed package data.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Any

from agents_remember.application.knowledge_worklist.code import CodeReadError, CodeTrees
from agents_remember.kernel.generated_copies import DECLARATION_PATH, parse_generated_copies
from agents_remember.memory.conversion.code_objects import CodeObjectError


def generated_copy_facts(code: CodeTrees, paths: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Map declared payload paths to their canonical path and exact generator-byte equality."""
    candidate = code.candidate()
    symlinks = code.candidate_symlinks()
    try:
        mode = code.objects.tree_entry_mode(code.candidate_tree, DECLARATION_PATH)
    except CodeObjectError as error:
        raise CodeReadError(str(error)) from error
    if mode is not None and mode not in {"100644", "100755"}:
        raise CodeReadError(f"{DECLARATION_PATH}: a copy declaration must be a regular file")
    blob = candidate.get(DECLARATION_PATH)
    if blob is None:
        return {}
    try:
        projections = parse_generated_copies(code.objects.blob(blob))
    except (ValueError, OSError, CodeObjectError) as error:
        raise CodeReadError(f"{DECLARATION_PATH} cannot be read: {error}") from error
    facts: dict[str, dict[str, Any]] = {}
    for path in paths:
        pure = PurePosixPath(path)
        for projection in projections:
            for target in projection.targets:
                if not pure.is_relative_to(target.path):
                    continue
                relative = pure.relative_to(target.path).as_posix()
                if relative == "." or projection.ignores(relative):
                    continue
                canonical = (PurePosixPath(projection.source) / relative).as_posix()
                original, copy = candidate.get(canonical), candidate.get(path)
                facts[path] = {
                    "canonicalPath": canonical,
                    "matches": original is not None
                    and copy is not None
                    and original == copy
                    and canonical not in symlinks
                    and path not in symlinks,
                }
    return facts
