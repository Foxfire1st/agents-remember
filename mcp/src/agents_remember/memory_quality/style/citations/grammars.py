"""Read the definitions and extents that citation symbols name.

Language resolution runs through the active :class:`LanguageSet` ContextVar
built from the data-driven global catalog lock (:mod:`language_catalog`) and
lazy isolated grammar cache (:mod:`language_cache`). Every tree-sitter language
executes declaratively through :mod:`tags_engine` ``tags.scm`` (and optional
``tests.scm``); a declared grammar that cannot be downloaded or loaded raises
``GrammarUnavailableError``. Lean plugs its built-in declaration scanner behind
the same :class:`LanguageProvider` protocol with ``.lean`` permanently reserved.
Unsupported or ambiguous suffixes use occurrence matching and cannot prove a
pure move unless opted in through ``languages.json`` or user settings.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from functools import cache
from pathlib import Path
from typing import Final

from tree_sitter import Language, Node, Parser, Tree

from agents_remember.errors import GrammarUnavailableError
from agents_remember.kernel.request_memo import request_owner
from agents_remember.memory_quality.style.citations import (
    language_scanner,
    test_case_ranges,
    test_cases,
)
from agents_remember.memory_quality.style.citations import (
    language_set as _language_set,
)
from agents_remember.memory_quality.style.citations.language_set import (
    LEAN,
    RESERVED_SUFFIXES,
    LanguageProvider,
    LanguageSet,
    LeanLanguageProvider,
    TreeSitterLanguageProvider,
)
from agents_remember.memory_quality.style.citations.tags_engine import (
    Binding,
    DestructuringSpec,
)

__all__ = [
    "DEFAULT_LANGUAGE_SET",
    "LEAN",
    "RESERVED_SUFFIXES",
    "Binding",
    "DestructuringSpec",
    "LanguageProvider",
    "LanguageSet",
    "LeanLanguageProvider",
    "TreeSitterLanguageProvider",
    "active_language_set",
    "bindings",
    "default_language_set",
    "extractor_version",
    "grammar_of",
    "is_default_language_set",
    "language",
    "language_identity",
    "language_set_scope",
    "parsed",
    "parsing_scope",
    "repository_language_set",
    "repository_language_set_scope",
    "test_case_range",
    "test_case_refusal",
    "typescript_anchor_identifier",
]

DEFAULT_LANGUAGE_SET: Final[LanguageSet] = language_scanner.build_language_set()


def default_language_set() -> LanguageSet:
    """Return the process's deterministic default ``LanguageSet`` built from the catalog lock."""
    return DEFAULT_LANGUAGE_SET


def active_language_set() -> LanguageSet:
    """Return the ``LanguageSet`` bound in the current context, or the deterministic default."""
    return _language_set.current_language_set(DEFAULT_LANGUAGE_SET)


def is_default_language_set() -> bool:
    """Whether the current context has no non-default ``LanguageSet`` override."""
    return not _language_set.has_language_set_override(DEFAULT_LANGUAGE_SET)


def repository_language_set(repo_root: Path) -> LanguageSet:
    """Return the ``LanguageSet`` configured for ``repo_root``, defaulting to the catalog set."""
    return language_scanner.repository_language_set(repo_root, default=DEFAULT_LANGUAGE_SET)


@contextmanager
def language_set_scope(selected: LanguageSet) -> Iterator[LanguageSet]:
    """Bind ``selected`` explicitly as the active ``LanguageSet`` for the duration of this context."""
    with _language_set.language_set_scope(selected) as bound:
        yield bound


@contextmanager
def repository_language_set_scope(repo_root: Path | None) -> Iterator[LanguageSet]:
    """Bind ``repo_root``'s configured ``LanguageSet`` unless an explicit scope is active."""
    if repo_root is None or _language_set.is_explicit_language_set():
        yield active_language_set()
        return
    selected = repository_language_set(repo_root)
    with _language_set.derived_language_set_scope(selected) as bound:
        yield bound


def extractor_version(selected: LanguageSet | None = None) -> str:
    """Version identity string derived from ``selected`` or the active ``LanguageSet``."""
    return (active_language_set() if selected is None else selected).extractor_version


def language_identity(path: str, selected: LanguageSet | None = None) -> str | None:
    """Resolved ``<key>@<version>`` identity for ``path`` in ``selected`` or the active set."""
    return (active_language_set() if selected is None else selected).language_identity(path)


def language(grammar: str) -> Language:
    """Load the pinned ``tree_sitter.Language`` for ``grammar`` from the default or active set."""
    provider = DEFAULT_LANGUAGE_SET.provider_for(grammar)
    if not isinstance(provider, TreeSitterLanguageProvider):
        provider = active_language_set().provider_for(grammar)
    if not isinstance(provider, TreeSitterLanguageProvider):
        raise GrammarUnavailableError(
            f"no tree-sitter language provider is registered for {grammar!r} in the catalog"
        )
    return provider.load_language()


@dataclass(frozen=True)
class _ParsedSource:
    tree: Tree
    test_calls: tuple[Node, ...] = ()


@dataclass
class _ParseCache:
    values: dict[tuple[str, str, str, tuple[str, ...]], _ParsedSource] = dataclass_field(
        default_factory=dict
    )
    lock: threading.Lock = dataclass_field(default_factory=threading.Lock)


_parsed_in_call: ContextVar[_ParseCache | None] = ContextVar("parsed_in_call", default=None)


@contextmanager
def parsing_scope(selected: LanguageSet | None = None) -> Iterator[None]:
    """Keep only trees that the test-case diagnostics can revisit during this call."""
    if selected is not None:
        with language_set_scope(selected), parsing_scope():
            yield
        return
    if _parsed_in_call.get() is not None:
        yield
        return
    cache_state = _ParseCache()
    token = _parsed_in_call.set(cache_state)
    try:
        yield
    finally:
        _parsed_in_call.reset(token)
        cache_state.values.clear()


def _parsed_source(
    path: str,
    provider: TreeSitterLanguageProvider,
    lines: Sequence[str],
    *,
    cache_tree: bool,
) -> _ParsedSource:
    cache_state = _parsed_in_call.get()
    if cache_state is None or not cache_tree:
        return _parse_source(provider, lines, capture_test_calls=cache_tree)
    key = (path, provider.key, provider.version, tuple(lines))
    with cache_state.lock:
        if key not in cache_state.values:
            cache_state.values[key] = _parse_source(provider, lines, capture_test_calls=True)
        return cache_state.values[key]


def _parse_source(
    provider: TreeSitterLanguageProvider,
    lines: Sequence[str],
    *,
    capture_test_calls: bool,
) -> _ParsedSource:
    # A Parser holds mutable traversal state. Only its completed immutable tree is reused.
    tree = Parser(provider.load_language()).parse("\n".join(lines).encode("utf-8"))
    calls = provider.extract_test_calls_from_tree(tree) if capture_test_calls else ()
    return _ParsedSource(tree=tree, test_calls=calls)


def grammar_of(path: str) -> str | None:
    """The active ``LanguageSet`` reader key for ``path``, or ``None`` for an unsupported path."""
    return active_language_set().grammar_of(path)


def parsed(path: str) -> bool:
    """Whether a definition in ``path`` is distinguishable from a mention of it."""
    return grammar_of(path) is not None


@dataclass
class _TypeScriptAnchorInputs:
    values: dict[tuple[str, str], str | None] = dataclass_field(default_factory=dict)
    lock: threading.Lock = dataclass_field(default_factory=threading.Lock)


def _anchor_spelling_provider() -> TreeSitterLanguageProvider:
    provider = active_language_set().anchor_spelling_provider()
    if provider is not None:
        return provider
    fallback = DEFAULT_LANGUAGE_SET.anchor_spelling_provider()
    if fallback is None:
        raise GrammarUnavailableError(
            "the active language catalog declares no provider marked anchorSpelling=true"
        )
    return fallback


def typescript_anchor_identifier(text: str) -> str | None:
    """The direct identifier rooted by a complete call or generic type spelling."""
    provider = _anchor_spelling_provider()
    provider_version = provider.version
    memo = request_owner(_TypeScriptAnchorInputs, _TypeScriptAnchorInputs)
    if memo is None:
        if is_default_language_set():
            return _process_typescript_anchor_identifier(provider_version, text)
        return _parse_typescript_anchor_identifier(text, provider.load_language())
    key = (provider_version, text)
    with memo.lock:
        if key not in memo.values:
            memo.values[key] = _parse_typescript_anchor_identifier(text, provider.load_language())
        return memo.values[key]


def _parse_typescript_anchor_identifier(text: str, ts_language: Language) -> str | None:
    signature = f"function {text} {{}}"
    signature_tree = Parser(ts_language).parse(signature.encode("utf-8"))
    if not signature_tree.root_node.has_error:
        declaration = next(
            (
                node
                for node in _walk(signature_tree.root_node)
                if node.type == "function_declaration"
            ),
            None,
        )
        if declaration is not None:
            name = declaration.child_by_field_name("name")
            if name is not None and name.type == "identifier" and name.text is not None:
                return name.text.decode("utf-8")
    wrappers = (
        ("const __citation_anchor = ", ";", "call_expression", "function"),
        ("type __CitationAnchor = ", ";", "generic_type", "name"),
    )
    for prefix, suffix, kind, field in wrappers:
        source = f"{prefix}{text}{suffix}"
        tree = Parser(ts_language).parse(source.encode("utf-8"))
        start = len(prefix.encode("utf-8"))
        end = start + len(text.encode("utf-8"))
        for node in _walk(tree.root_node):
            if (
                node.has_error
                or node.type != kind
                or node.start_byte != start
                or node.end_byte != end
            ):
                continue
            root = node.child_by_field_name(field)
            if root is None:
                # These children are not field-named in every grammar build.
                root = next(iter(node.named_children), None)
            if root is not None and root.type in {"identifier", "type_identifier"}:
                return "" if root.text is None else root.text.decode("utf-8")
    return None


@cache
def _process_typescript_anchor_identifier(_version: str, text: str) -> str | None:
    return _parse_typescript_anchor_identifier(text, _anchor_spelling_provider().load_language())


def _resolve_provider(path: str) -> LanguageProvider | None:
    grammar = grammar_of(path)
    if grammar is None:
        return None
    return active_language_set().provider_for(grammar)


def bindings(path: str, lines: Sequence[str]) -> dict[str, list[Binding]]:
    """Every name ``path`` binds, with both its widened extent and its declaration line."""
    provider = _resolve_provider(path)
    if provider is None:
        return {}
    if isinstance(provider, LeanLanguageProvider):
        return provider.extract_bindings(lines)
    if isinstance(provider, TreeSitterLanguageProvider):
        test_file = provider.supports_test_cases(path)
        source = _parsed_source(path, provider, lines, cache_tree=test_file)
        return provider.extract_bindings_from_tree(
            source.tree, test_calls=source.test_calls if test_file else ()
        )
    return {}


def test_case_refusal(path: str, lines: Sequence[str], name: str) -> str | None:
    """Diagnostic for an unresolved test-case proof; other files keep symbol diagnostics."""
    provider = _resolve_provider(path)
    if not isinstance(provider, TreeSitterLanguageProvider) or not provider.supports_test_cases(
        path
    ):
        return None
    source = _parsed_source(path, provider, lines, cache_tree=True)
    return test_cases.refusal(source.test_calls, path, name)


def test_case_range(
    path: str,
    lines: Sequence[str],
    start: int,
    end: int,
    reaches: test_case_ranges.Reaches | None = None,
) -> test_case_ranges.CaseExtent | str:
    """The one test case a line-range proof names, or why these lines are none."""
    provider = _resolve_provider(path)
    if not isinstance(provider, TreeSitterLanguageProvider) or not provider.supports_test_cases(
        path
    ):
        return test_case_ranges.not_a_case_file(path, start, end)
    source = _parsed_source(path, provider, lines, cache_tree=True)
    return test_case_ranges.case_at(path, source.test_calls, start, end, reaches)


def _walk(root: Node) -> list[Node]:
    """Every node in the tree, at any depth, in document order."""
    found: list[Node] = []
    pending = [root]
    while pending:
        node = pending.pop()
        found.append(node)
        pending.extend(reversed(node.children))
    return found
