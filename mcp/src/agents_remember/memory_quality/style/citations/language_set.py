"""Explicit ``LanguageProvider`` protocol and ``LanguageSet`` ContextVar seam.

Language resolution is a pure function of ``(LanguageSet, path)`` rather than a
process-global suffix table. Each ``LanguageSet`` carries its resolved providers,
pinned versions, and query digests so every cache and observation key derives its
extractor identity from the active set.

``.lean`` is permanently reserved for the built-in Lean declaration scanner and
cannot be bound to a tree-sitter provider.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Final, Protocol

from tree_sitter import Language, Node, Query, Tree

from agents_remember.errors import AgentsRememberError
from agents_remember.memory_quality.style.citations import (
    lean_declarations,
    tags_engine,
    test_cases,
)
from agents_remember.memory_quality.style.citations.tags_engine import (
    Binding,
    DestructuringSpec,
)

PYTHON: Final = "python"
TYPESCRIPT: Final = "typescript"
TSX: Final = "tsx"
JAVASCRIPT: Final = "javascript"
LEAN: Final = "lean"

RESERVED_SUFFIXES: Final[frozenset[str]] = frozenset({".lean"})
LEAN_SCANNER_VERSION: Final = "builtin-lean/1"

ParseTreeFn = Callable[[Language, Sequence[str]], Tree]


class LanguageProvider(Protocol):
    """One language reader behind the uniform ``LanguageSet`` seam."""

    @property
    def key(self) -> str: ...

    @property
    def suffixes(self) -> frozenset[str]: ...

    @property
    def version(self) -> str: ...

    @property
    def is_tree_sitter(self) -> bool: ...

    @property
    def uses_exact_bindings(self) -> bool: ...

    @property
    def has_test_cases(self) -> bool: ...

    def supports_test_cases(self, path: str) -> bool: ...


@dataclass(frozen=True)
class TreeSitterLanguageProvider:
    """Declarative tree-sitter language backed by ``tags.scm`` and optional ``tests.scm``."""

    key: str
    suffixes: frozenset[str]
    grammar_version: str
    load_language: Callable[[], Language]
    tags_query_text: str
    wrappers: frozenset[str] = frozenset()
    destructuring: DestructuringSpec = field(default_factory=DestructuringSpec)
    tests_query_text: str | None = None
    _version: str = field(init=False)

    def __post_init__(self) -> None:
        parts = [self.grammar_version, f"tags:{tags_engine.query_digest(self.tags_query_text)}"]
        if self.tests_query_text is not None:
            parts.append(f"tests:{tags_engine.query_digest(self.tests_query_text)}")
        object.__setattr__(self, "_version", "+".join(parts))

    @property
    def version(self) -> str:
        return self._version

    @property
    def is_tree_sitter(self) -> bool:
        return True

    @property
    def uses_exact_bindings(self) -> bool:
        return False

    @property
    def has_test_cases(self) -> bool:
        return self.tests_query_text is not None

    def supports_test_cases(self, path: str) -> bool:
        return self.tests_query_text is not None and test_cases.is_test_file(path)

    def compiled_tags_query(self) -> Query:
        return tags_engine.compile_query(self.load_language(), self.tags_query_text)

    def compiled_tests_query(self) -> Query | None:
        if self.tests_query_text is None:
            return None
        return tags_engine.compile_query(self.load_language(), self.tests_query_text)

    def extract_test_calls_from_tree(self, tree: Tree) -> tuple[Node, ...]:
        query = self.compiled_tests_query()
        if query is None:
            return ()
        return tags_engine.extract_test_calls(tree, query)

    def extract_bindings_from_tree(
        self,
        tree: Tree,
        *,
        test_calls: Sequence[Node] = (),
    ) -> dict[str, list[Binding]]:
        tag_entries = tags_engine.extract_tag_entries(
            tree,
            self.compiled_tags_query(),
            wrappers=self.wrappers,
            destructuring=self.destructuring,
        )
        case_entries = (
            tags_engine.extract_test_case_entries(test_calls, wrappers=self.wrappers)
            if test_calls
            else ()
        )
        return tags_engine.merge_entries(tag_entries, case_entries)


@dataclass(frozen=True)
class LeanLanguageProvider:
    """Built-in Lean declaration scanner plugged behind ``LanguageProvider``."""

    key: str = LEAN
    suffixes: frozenset[str] = frozenset({".lean"})
    version: str = LEAN_SCANNER_VERSION

    @property
    def is_tree_sitter(self) -> bool:
        return False

    @property
    def uses_exact_bindings(self) -> bool:
        return True

    @property
    def has_test_cases(self) -> bool:
        return False

    def supports_test_cases(self, path: str) -> bool:
        del path
        return False

    def extract_bindings(self, lines: Sequence[str]) -> dict[str, list[Binding]]:
        full: dict[str, list[Binding]] = {}
        possible: dict[str, list[Binding]] = {}
        written: dict[str, list[Binding]] = {}
        for declaration in lean_declarations.declarations(lines):
            binding = Binding(
                span=(declaration.start, declaration.end),
                declaration=declaration.declaration,
                full_name=declaration.full_name,
                scope_uncertain=declaration.full_name is None,
            )
            if declaration.full_name is not None:
                full.setdefault(declaration.full_name, []).append(binding)
            else:
                for name in declaration.possible_full_names:
                    possible.setdefault(name, []).append(binding)
            written.setdefault(declaration.written_name, []).append(binding)
        # Use exact names before written aliases, and established names before possible ones.
        written.update(possible)
        written.update(full)
        return written


@dataclass(frozen=True)
class LanguageSet:
    """Immutable, versioned mapping from file suffixes to ``LanguageProvider`` instances."""

    providers: tuple[LanguageProvider, ...]
    tree_sitter_version: str
    by_key: Mapping[str, LanguageProvider] = field(init=False)
    by_suffix: Mapping[str, LanguageProvider] = field(init=False)
    extractor_version: str = field(init=False)

    def __post_init__(self) -> None:
        by_key: dict[str, LanguageProvider] = {}
        by_suffix: dict[str, LanguageProvider] = {}
        for provider in self.providers:
            if provider.key in by_key:
                raise AgentsRememberError(
                    f"duplicate language provider key {provider.key!r} in LanguageSet"
                )
            by_key[provider.key] = provider
            for suffix in provider.suffixes:
                _register_suffix(suffix, provider, by_suffix)
        object.__setattr__(self, "by_key", MappingProxyType(by_key))
        object.__setattr__(self, "by_suffix", MappingProxyType(by_suffix))
        object.__setattr__(
            self,
            "extractor_version",
            _build_extractor_version(self.tree_sitter_version, self.providers),
        )

    def resolve(self, path: str) -> LanguageProvider | None:
        """Return the provider owning ``path``'s suffix in this set, or ``None``."""
        return self.by_suffix.get(PurePosixPath(path).suffix.lower())

    def grammar_of(self, path: str) -> str | None:
        """Return the language key owning ``path`` in this set, or ``None``."""
        provider = self.resolve(path)
        return None if provider is None else provider.key

    def provider_for(self, key: str) -> LanguageProvider | None:
        """Return the provider registered under ``key``, or ``None``."""
        return self.by_key.get(key)

    def language_identity(self, path: str) -> str | None:
        """Return ``<key>@<version>`` for ``path`` in this set, or ``None``."""
        provider = self.resolve(path)
        return None if provider is None else f"{provider.key}@{provider.version}"

    def without(self, *keys: str) -> LanguageSet:
        """Return a derived ``LanguageSet`` omitting the named language keys."""
        excluded = set(keys)
        return LanguageSet(
            providers=tuple(p for p in self.providers if p.key not in excluded),
            tree_sitter_version=self.tree_sitter_version,
        )

    def with_providers(
        self,
        providers: Sequence[LanguageProvider],
        *,
        tree_sitter_version: str | None = None,
    ) -> LanguageSet:
        """Return a new ``LanguageSet`` replacing providers by key or appending new ones."""
        merged = dict(self.by_key)
        for provider in providers:
            merged[provider.key] = provider
        return LanguageSet(
            providers=tuple(merged.values()),
            tree_sitter_version=(
                self.tree_sitter_version if tree_sitter_version is None else tree_sitter_version
            ),
        )


def _register_suffix(
    suffix: str,
    provider: LanguageProvider,
    by_suffix: dict[str, LanguageProvider],
) -> None:
    normalized = suffix.lower()
    if not normalized.startswith(".") or normalized != suffix:
        raise AgentsRememberError(
            f"language suffix {suffix!r} for {provider.key!r} must be lowercase and start with '.'"
        )
    if normalized in RESERVED_SUFFIXES and (provider.is_tree_sitter or provider.key != LEAN):
        raise AgentsRememberError(
            f"suffix {normalized!r} is reserved for the built-in Lean scanner and "
            f"cannot be bound to provider {provider.key!r}"
        )
    if normalized in by_suffix:
        existing = by_suffix[normalized].key
        raise AgentsRememberError(
            f"suffix {normalized!r} is claimed by both {existing!r} and {provider.key!r}"
        )
    by_suffix[normalized] = provider


def _build_extractor_version(
    tree_sitter_version: str,
    providers: Sequence[LanguageProvider],
) -> str:
    parts = [f"tree-sitter={tree_sitter_version}"]
    for provider in sorted(providers, key=lambda item: item.key):
        suffix_list = ",".join(sorted(provider.suffixes))
        parts.append(f"{provider.key}[{suffix_list}]={provider.version}")
    return ";".join(parts)


_active_language_set: ContextVar[LanguageSet | None] = ContextVar(
    "active_language_set", default=None
)


def current_language_set(default: LanguageSet) -> LanguageSet:
    """Return the ``LanguageSet`` bound in the current context, or ``default``."""
    bound = _active_language_set.get()
    return default if bound is None else bound


def has_language_set_override(default: LanguageSet) -> bool:
    """Whether the current context binds a non-default ``LanguageSet``."""
    bound = _active_language_set.get()
    return bound is not None and bound != default


@contextmanager
def language_set_scope(language_set: LanguageSet) -> Iterator[LanguageSet]:
    """Bind ``language_set`` for the current context and restore the prior set on exit."""
    token = _active_language_set.set(language_set)
    try:
        yield language_set
    finally:
        _active_language_set.reset(token)
