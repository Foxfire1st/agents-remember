"""Resolve citation sources against code and memory roots.

Sources are repo-relative ``path:start-end`` tokens. Resolution checks the code root first,
then the memory root (the onboarding root's parent). A path existing in both therefore
means code; the format cannot explicitly select the colliding memory path.

A source link to a sidecar resolves to the source file it documents. Memory documents are
excluded from the tree-wide symbol index because a card contains its own anchor text and
would become a false second location. Memory-target citations still receive bounds and
containment checks, but moves within memory are not searched.

Dependency paths in neither tree are counted as unresolved rather than gated on the local
installation environment.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import cached_property
from pathlib import Path

from agents_remember.errors import CitationCacheError
from agents_remember.memory_quality.style.citations.candidate.git_source import GitSourceCandidate
from agents_remember.memory_quality.style.citations.exclusion_register import (
    resolve_exclusion_register,
)
from agents_remember.memory_quality.style.citations.source_index_cache import (
    ManagedCacheAuthority,
)
from agents_remember.memory_quality.style.citations.source_index_state import (
    CitationIndexCaps,
    ExclusionRegister,
    candidate_tree,
)


@dataclass(frozen=True)
class Trees:
    """The two roots a source may name, the register they are read under, and the caps.

    ``exclusions`` and ``caps`` are cached properties rather than constructor arguments because
    every caller in the product already supplies the two roots, and a second construction site
    that had to remember to pass the register is a second place it can be forgotten. A caller
    that wants to add its own excludes for one operation passes ``caller_excludes``; the
    settings-derived register and the caps are then read from the memory layer's own
    ``system/settings.json``.

    Each root may be bound to the exact Git tree it stood on for the operation
    (``candidate_tree`` for ``code_root``, ``memory_candidate_tree`` for ``memory_root``), and
    :meth:`resolve` is the one place both bindings are read: when either is bound, an answer comes
    from a bound tree or not at all, so "resolved" means "a member of a tree this resolver was
    given" rather than "a file that happens to exist under a root" (D-43).
    """

    code_root: Path
    memory_root: Path
    cache_authority: ManagedCacheAuthority | None = None
    candidate_tree: str | None = None
    memory_candidate_tree: str | None = None
    caller_excludes: tuple[str, ...] = ()
    _exclusions: ExclusionRegister | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        candidate_tree(self.candidate_tree)
        candidate_tree(self.memory_candidate_tree)

    @property
    def exclusions(self) -> ExclusionRegister:
        """The exclusion register in force: pathRules, the ignore file, and the caller's own."""
        if self._exclusions is not None:
            return self._exclusions
        return resolve_exclusion_register(
            code_root=self.code_root,
            memory_root=self.memory_root,
            caller=self.caller_excludes,
        )

    @property
    def caps(self) -> CitationIndexCaps:
        """The bounds in force, from ``onboarding.citationIndex`` or the module constants."""
        return self.exclusions.caps

    def with_exclusions(self, exclusions: ExclusionRegister) -> Trees:
        """The same trees with an already-resolved register, so it is read exactly once."""
        return replace(self, _exclusions=exclusions)

    @cached_property
    def source_candidate(self) -> GitSourceCandidate | None:
        if self.candidate_tree is None:
            return None
        return GitSourceCandidate(self.code_root.resolve(), self.candidate_tree)

    @cached_property
    def memory_source_candidate(self) -> GitSourceCandidate | None:
        if self.memory_candidate_tree is None:
            return None
        return GitSourceCandidate(self.memory_root.resolve(), self.memory_candidate_tree)

    def resolve(self, path: str) -> Path | None:
        """The file this source names, as a member of a bound tree whenever one is bound.

        Two answers are admissible, and which one is available is a property of the binding rather
        than of the filesystem. With no tree bound at all, the two roots are searched as trees of
        convenience, which is what an unmanaged operation has: it never recorded a candidate, so
        there is no tree for an answer to disagree with. With a tree bound on either side, an
        answer must be a member of the tree bound to the root that carries it, and a path neither
        bound tree contains resolves to nothing.

        That last half is the repair. The old fallback answered from ``memory_root / path`` with
        ``is_file()`` alone, so a caller that bound the code tree to a candidate could receive a
        MEMORY path -- no member of the tree it bound -- and read an identity out of the wrong
        tree. ``system/tools.md`` is the measured case: it is gitignored in the code repository
        (.gitignore), so it is no member of the code tree, while the file exists beside a real
        ``system/`` directory (D-43).
        """

        bound = self.source_candidate is not None or self.memory_source_candidate is not None
        for root, candidate in (
            (self.code_root, self.source_candidate),
            (self.memory_root, self.memory_source_candidate),
        ):
            if candidate is not None:
                target = candidate.resolve(path)
                if target is not None:
                    return target
                continue
            if bound:
                # A tree is bound for the other side, which is the caller saying that membership
                # is what "resolved" must mean here; this root has no such proof to offer.
                continue
            target = root / path
            if target.is_file():
                return target
        return None

    def ours(self, path: str) -> bool:
        """Whether a path that did NOT resolve still names a location in these trees.

        This is what separates the two reasons a source resolves to nothing, and they need
        opposite treatment. ``uvicorn/main.py`` is a dependency's source: correct, useful,
        and absent by construction. A source naming a real top-level directory of this
        repository and a file under it that is gone is the other one, and it is the exact
        damage a package move does to a memory tree. The test is the first segment being a real top-level entry of one of
        the roots, read from the tree rather than listed here, so a new top-level directory
        is covered the day it is created.
        """
        first = path.split("/", maxsplit=1)[0]
        return any((root / first).exists() for root in (self.code_root, self.memory_root))


def operation_trees(onboarding_root: Path, code: Path | Trees) -> Trees:
    """Bind a core operation to either standalone roots or managed application authority."""
    if isinstance(code, Path):
        return Trees(code_root=code, memory_root=onboarding_root.parent)
    expected_memory = onboarding_root.parent.resolve()
    if code.memory_root.resolve() != expected_memory:
        raise CitationCacheError(
            "citation operation Trees belongs to a different onboarding memory root: "
            f"trees={code.memory_root.resolve()}, onboarding={expected_memory}"
        )
    if code.cache_authority is not None:
        code.cache_authority.validate_roots(code.code_root, code.memory_root)
    return code
