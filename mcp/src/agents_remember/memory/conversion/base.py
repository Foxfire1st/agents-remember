"""The converted base of a comparison (MIK-R24 rule 7).

When a comparison's before side is unconverted and its after side is converted, the before side is
replaced by its conversion: run at the before side's paired code tree, with the after side's pinned
conversion-format version. So the mechanical conversion itself never counts as a change. The history
sides (MIK-R07 rule 0), the onboarding gate (MIK-R30), the worklist (MIK-R08), the reviewer (MIK-R25)
and the validator's commit route (MIK-R22 rule 6) all take their sides through
:func:`comparison_sides`.

Conversions are memoized per process by (memory repository, tree, version, paired code commit):
the result is a pure function of those inputs (rule 6).
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.kernel.memory_attribution import parse_code_commit_trailer
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory.conversion.convert import convert_memory
from agents_remember.memory.conversion.inputs import memory_from_git
from agents_remember.memory_quality.knowledge_validator.trees import KnowledgeTree
from agents_remember.models.knowledge_files.canonical import parse_json
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH

_CACHE_LIMIT: Final = 8
_cache: dict[tuple[str, str, str, str], KnowledgeTree] = {}


def pinned_version(tree: KnowledgeTree) -> str | None:
    """The conversion-format version a converted tree's layout marker names."""

    data = tree.files.get(LAYOUT_MARKER_PATH)
    if data is None:
        return None
    return str(parse_json(data.decode("utf-8"))["conversion"])


def _tree_id(repository: Path, treeish: str) -> str:
    result = run_git(
        repository,
        ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{treeish}^{{tree}}"],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    tree = result.stdout.strip()
    if result.returncode != 0 or not tree:
        raise ValueError(f"{treeish!r} does not name a tree in {repository}")
    return tree


def converted_base(
    memory_repository: Path,
    treeish: str,
    *,
    code_repository: Path,
    code_commit: str,
    version: str,
) -> KnowledgeTree:
    """The conversion of the memory tree ``treeish`` (rule 7), as the validator reads trees."""

    tree = _tree_id(memory_repository, treeish)
    key = (memory_repository.resolve().as_posix(), tree, version, code_commit)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    with tempfile.TemporaryDirectory(prefix="ar-converted-base-") as scratch:
        memory = memory_from_git(
            memory_repository, tree, Path(scratch), label=f"converted base {treeish}"
        )
        outcome = convert_memory(
            memory, CodeObjects(code_repository), paired_commit=code_commit, version=version
        )
    converted = KnowledgeTree(label=f"converted base {treeish}", files=outcome.files)
    if len(_cache) >= _CACHE_LIMIT:
        _cache.pop(next(iter(_cache)))
    _cache[key] = converted
    return converted


def own_paired_code_commit(memory_repository: Path, treeish: str) -> str | None:
    """The code commit a memory commit names in its ``Code-Commit`` trailer, or ``None``."""

    result = run_git(
        memory_repository,
        ["log", "-1", "--format=%B", "--end-of-options", treeish],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    if result.returncode != 0:
        return None
    return parse_code_commit_trailer(result.stdout)


@dataclass(frozen=True)
class GitBaseConverter:
    """The composition-bound converter the validator's commit route uses for unconverted bases.

    Rule 7 converts the before side at *its own* paired code tree: the commit its ``Code-Commit``
    trailer names. Only rule 2 fallback cards read that tree; when the base names none (or its
    commit is not in the code store) the route's paired commit stands in.

    **Known difference, fallback cards only.** A crossing sync (rule 8 step 2) converts *every*
    side at the own side's code tree, while this converter uses the base's own code commit. For a
    card whose ``lastVerifiedCommitHash`` is missing (a rule 2 fallback card; 0 in the real tree)
    the two conversions of the same base can therefore differ, and the commit-time validation could
    see such a card as changed. Every other card is anchored at its own verified commit and converts
    identically either way. Consumers wiring :func:`comparison_sides` (MIK-R07, R30, R08, R25)
    inherit the same rule.
    """

    def __call__(
        self,
        memory_repository: Path,
        base: str,
        *,
        after: KnowledgeTree,
        code_repository: Path,
        code_commit: str,
    ) -> KnowledgeTree:
        version = pinned_version(after)
        if version is None:
            raise ValueError("the after side is not converted, so there is no version to pin")
        own = own_paired_code_commit(memory_repository, base)
        if own is not None and CodeObjects(code_repository).commit(own) is not None:
            code_commit = own
        return converted_base(
            memory_repository,
            base,
            code_repository=code_repository,
            code_commit=code_commit,
            version=version,
        )


@dataclass(frozen=True)
class BeforeSide:
    """Where a comparison's before side lives: its memory commit and its paired code commit."""

    memory_repository: Path
    treeish: str
    code_repository: Path
    code_commit: str


def comparison_sides(
    before: KnowledgeTree, after: KnowledgeTree, where: BeforeSide
) -> tuple[KnowledgeTree, KnowledgeTree]:
    """Rule 7: an unconverted before side of a converted after side is replaced by its conversion."""

    version = pinned_version(after)
    if before.converted or version is None:
        return before, after
    converted = converted_base(
        where.memory_repository,
        where.treeish,
        code_repository=where.code_repository,
        code_commit=where.code_commit,
        version=version,
    )
    return converted, after
