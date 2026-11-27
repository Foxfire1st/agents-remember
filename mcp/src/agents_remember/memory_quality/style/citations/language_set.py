"""Explicit ``LanguageProvider`` protocol and ``LanguageSet`` ContextVar seam.

Language resolution is a pure function of ``(LanguageSet, path)`` rather than a
process-global suffix table. Each ``LanguageSet`` carries its resolved providers,
pinned versions, query digests, and any explicit glob/suffix overrides so every
cache and observation key derives its extractor identity from the active set.

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

LEAN: Final = "lean"
RESERVED_SUFFIXES: Final[frozenset[str]] = frozenset({".lean"})
LEAN_SCANNER_VERSION: Final = "builtin-lean/1"

ParseTreeFn = Callable[[Language, Sequence[str]], Tree]


def matches_glob(path: str, pattern: str) -> bool:
    """Match a POSIX relative ``path`` against ``pattern`` (including ``**`` segments)."""
    normalized = path.lstrip("/")
    if not normalized:
        return False
    posix = PurePosixPath(normalized)
    return posix.full_match(pattern) or (
        pattern.startswith("**/") and posix.full_match(pattern[3:])
    )


class LanguageProvider(Protocol):
    """One language reader behind the uniform ``LanguageSet`` seam."""

    @property
    def key(self) -> str: ...

    @property
    def suffixes(self) -> frozenset[str]: ...

    @property
    def filenames(self) -> frozenset[str]: ...

    @property
    def globs(self) -> tuple[str, ...]: ...

    @property
    def shebangs(self) -> frozenset[str]: ...

    @property
    def version(self) -> str: ...

    @property
    def is_tree_sitter(self) -> bool: ...

    @property
    def uses_exact_bindings(self) -> bool: ...

    @property
    def has_test_cases(self) -> bool: ...

    @property
    def anchor_spelling(self) -> bool: ...

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
    filenames: frozenset[str] = frozenset()
    globs: tuple[str, ...] = ()
    shebangs: frozenset[str] = frozenset()
    anchor_spelling: bool = False
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
    filenames: frozenset[str] = frozenset()
    globs: tuple[str, ...] = ()
    shebangs: frozenset[str] = frozenset()
    version: str = LEAN_SCANNER_VERSION
    anchor_spelling: bool = False

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
    """Immutable, versioned mapping from paths to ``LanguageProvider`` instances."""

    providers: tuple[LanguageProvider, ...]
    tree_sitter_version: str
    glob_overrides: tuple[tuple[str, str], ...] = ()
    suffix_overrides: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    by_key: Mapping[str, LanguageProvider] = field(init=False)
    by_suffix: Mapping[str, LanguageProvider] = field(init=False)
    by_filename: Mapping[str, LanguageProvider] = field(init=False)
    extractor_version: str = field(init=False)

    def __post_init__(self) -> None:
        by_key, by_suffix, by_filename = _index_providers(self.providers)
        validated_suffix_overrides = _validate_overrides(
            self.glob_overrides, self.suffix_overrides, by_key
        )
        object.__setattr__(self, "suffix_overrides", MappingProxyType(validated_suffix_overrides))
        object.__setattr__(self, "by_key", MappingProxyType(by_key))
        object.__setattr__(self, "by_suffix", MappingProxyType(by_suffix))
        object.__setattr__(self, "by_filename", MappingProxyType(by_filename))
        object.__setattr__(
            self,
            "extractor_version",
            _build_extractor_version(
                self.tree_sitter_version,
                self.providers,
                glob_overrides=self.glob_overrides,
                suffix_overrides=validated_suffix_overrides,
            ),
        )

    def resolve(self, path: str) -> LanguageProvider | None:
        """Return the provider owning ``path`` in this set, or ``None``."""
        posix = PurePosixPath(path)
        suffix = posix.suffix.lower()
        if suffix in RESERVED_SUFFIXES:
            return self.by_suffix.get(suffix)
        for pattern, lang_id in self.glob_overrides:
            if matches_glob(path, pattern):
                return self.by_key.get(lang_id)
        if suffix in self.suffix_overrides:
            return self.by_key.get(self.suffix_overrides[suffix])
        for provider in self.providers:
            if provider.globs and any(matches_glob(path, pattern) for pattern in provider.globs):
                return provider
        by_name = self.by_filename.get(posix.name)
        if by_name is not None:
            return by_name
        return self.by_suffix.get(suffix)

    def grammar_of(self, path: str) -> str | None:
        """Return the language key owning ``path`` in this set, or ``None``."""
        provider = self.resolve(path)
        return None if provider is None else provider.key

    def provider_for(self, key: str) -> LanguageProvider | None:
        """Return the provider registered under ``key``, or ``None``."""
        return self.by_key.get(key)

    def anchor_spelling_provider(self) -> TreeSitterLanguageProvider | None:
        """Return the active provider marked for call/generic anchor spelling, if any."""
        for provider in self.providers:
            if isinstance(provider, TreeSitterLanguageProvider) and provider.anchor_spelling:
                return provider
        return None

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
            glob_overrides=tuple(
                (pattern, lang_id)
                for pattern, lang_id in self.glob_overrides
                if lang_id not in excluded
            ),
            suffix_overrides={
                suffix: lang_id
                for suffix, lang_id in self.suffix_overrides.items()
                if lang_id not in excluded
            },
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
            glob_overrides=self.glob_overrides,
            suffix_overrides=self.suffix_overrides,
        )


def _index_providers(
    providers: Sequence[LanguageProvider],
) -> tuple[
    dict[str, LanguageProvider],
    dict[str, LanguageProvider],
    dict[str, LanguageProvider],
]:
    by_key: dict[str, LanguageProvider] = {}
    by_suffix: dict[str, LanguageProvider] = {}
    by_filename: dict[str, LanguageProvider] = {}
    for provider in providers:
        if provider.key in by_key:
            raise AgentsRememberError(
                f"duplicate language provider key {provider.key!r} in LanguageSet"
            )
        by_key[provider.key] = provider
        for suffix in provider.suffixes:
            _register_suffix(suffix, provider, by_suffix)
        for filename in provider.filenames:
            if filename in by_filename:
                existing = by_filename[filename].key
                raise AgentsRememberError(
                    f"filename {filename!r} is claimed by both {existing!r} and {provider.key!r}"
                )
            by_filename[filename] = provider
        for pattern in provider.globs:
            _check_reserved_glob(pattern, provider.key)
    return by_key, by_suffix, by_filename


def _validate_overrides(
    glob_overrides: Sequence[tuple[str, str]],
    suffix_overrides: Mapping[str, str],
    by_key: Mapping[str, LanguageProvider],
) -> dict[str, str]:
    validated: dict[str, str] = {}
    for suffix, lang_id in suffix_overrides.items():
        normalized = suffix.lower()
        if not normalized.startswith(".") or normalized != suffix:
            raise AgentsRememberError(
                f"suffix override {suffix!r} must be lowercase and start with '.'"
            )
        if normalized in RESERVED_SUFFIXES:
            raise AgentsRememberError(
                f"suffix {normalized!r} is reserved for the built-in Lean scanner and cannot be overridden"
            )
        if lang_id not in by_key:
            raise AgentsRememberError(
                f"suffix override {suffix!r} references unknown provider {lang_id!r}"
            )
        validated[normalized] = lang_id
    for pattern, lang_id in glob_overrides:
        _check_reserved_glob(pattern, lang_id)
        if lang_id not in by_key:
            raise AgentsRememberError(
                f"glob override {pattern!r} references unknown provider {lang_id!r}"
            )
    return validated


def _check_reserved_glob(pattern: str, owner: str) -> None:
    lower = pattern.lower().strip()
    if any(lower.endswith(suffix) for suffix in RESERVED_SUFFIXES):
        raise AgentsRememberError(
            f"glob {pattern!r} for {owner!r} targets reserved Lean suffix and is refused"
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
    *,
    glob_overrides: Sequence[tuple[str, str]] = (),
    suffix_overrides: Mapping[str, str] = MappingProxyType({}),
) -> str:
    parts = [f"tree-sitter={tree_sitter_version}"]
    for provider in sorted(providers, key=lambda item: item.key):
        selectors = [
            *sorted(provider.suffixes),
            *(f"file:{name}" for name in sorted(provider.filenames)),
            *(f"glob:{pattern}" for pattern in provider.globs),
        ]
        selector_list = ",".join(selectors)
        parts.append(f"{provider.key}[{selector_list}]={provider.version}")
    if suffix_overrides:
        override_text = ",".join(
            f"{suffix}->{lang_id}" for suffix, lang_id in sorted(suffix_overrides.items())
        )
        parts.append(f"suffix-overrides[{override_text}]")
    if glob_overrides:
        glob_text = ",".join(f"{pattern}->{lang_id}" for pattern, lang_id in glob_overrides)
        parts.append(f"glob-overrides[{glob_text}]")
    return ";".join(parts)


_active_language_set: ContextVar[LanguageSet | None] = ContextVar(
    "active_language_set", default=None
)
_explicit_language_set: ContextVar[bool] = ContextVar("explicit_language_set", default=False)


def current_language_set(default: LanguageSet) -> LanguageSet:
    """Return the ``LanguageSet`` bound in the current context, or ``default``."""
    bound = _active_language_set.get()
    return default if bound is None else bound


def has_language_set_override(default: LanguageSet) -> bool:
    """Whether the current context binds a non-default ``LanguageSet``."""
    bound = _active_language_set.get()
    return bound is not None and bound != default


def is_explicit_language_set() -> bool:
    """Whether the current context entered an explicit caller-supplied ``language_set_scope``."""
    return _explicit_language_set.get()


@contextmanager
def language_set_scope(language_set: LanguageSet) -> Iterator[LanguageSet]:
    """Bind ``language_set`` explicitly for the current context and restore the prior set on exit."""
    set_token = _active_language_set.set(language_set)
    explicit_token = _explicit_language_set.set(True)
    try:
        yield language_set
    finally:
        _explicit_language_set.reset(explicit_token)
        _active_language_set.reset(set_token)


@contextmanager
def derived_language_set_scope(language_set: LanguageSet) -> Iterator[LanguageSet]:
    """Bind a repository-derived ``language_set`` unless an explicit override is already active."""
    if _explicit_language_set.get():
        bound = _active_language_set.get()
        yield language_set if bound is None else bound
        return
    token = _active_language_set.set(language_set)
    try:
        yield language_set
    finally:
        _active_language_set.reset(token)
