"""Read the definitions and extents that citation symbols name.

Language resolution runs through the active :class:`LanguageSet` ContextVar. Every
tree-sitter language executes declaratively through the same :mod:`tags_engine`
``tags.scm`` (and optional ``tests.scm``) pipeline; a declared grammar that cannot
load raises ``GrammarUnavailableError``. Lean plugs its built-in declaration scanner
behind the same :class:`LanguageProvider` protocol with ``.lean`` permanently
reserved. Unsupported suffixes use occurrence matching and cannot prove a pure move.

Tree-sitter false-positive boundaries:

1. A trailing comment inside a syntax node remains part of its extent.
2. Local bindings are definitions, so a common local name may resolve ambiguously.
3. Interface members are definitions and may resolve in several files.
4. Calls/generic spans are accepted only when parsing yields one direct identifier.
5. A string widens to a call only when it is a direct argument; identical callback,
   assignment, or same-line literals retain their own extents.
6. Imports, re-exports, object keys, JSX attributes, call arguments, comments, strings,
   and attribute/subscript assignment targets are not definitions.
"""

from __future__ import annotations

import importlib
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from functools import cache
from typing import Final

from tree_sitter import Language, Node, Parser, Tree

from agents_remember.errors import GrammarUnavailableError
from agents_remember.kernel.request_memo import request_owner
from agents_remember.memory_quality.style.citations import (
    language_set as _language_set,
)
from agents_remember.memory_quality.style.citations import (
    tags_engine,
    test_case_ranges,
    test_cases,
)
from agents_remember.memory_quality.style.citations.language_set import (
    JAVASCRIPT,
    LEAN,
    PYTHON,
    RESERVED_SUFFIXES,
    TSX,
    TYPESCRIPT,
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
    "GRAMMAR_PACKAGES",
    "JAVASCRIPT",
    "LEAN",
    "MEASURED_VERSIONS",
    "PYTHON",
    "RESERVED_SUFFIXES",
    "SUFFIX_GRAMMARS",
    "TSX",
    "TYPESCRIPT",
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
    "test_case_range",
    "test_case_refusal",
    "typescript_anchor_identifier",
]

SUFFIX_GRAMMARS = {
    ".lean": LEAN,
    ".py": PYTHON,
    ".ts": TYPESCRIPT,
    ".mts": TYPESCRIPT,
    ".cts": TYPESCRIPT,
    ".tsx": TSX,
    ".js": JAVASCRIPT,
    ".jsx": JAVASCRIPT,
    ".mjs": JAVASCRIPT,
    ".cjs": JAVASCRIPT,
}
GRAMMAR_PACKAGES = {
    PYTHON: ("tree_sitter_python", "language"),
    TYPESCRIPT: ("tree_sitter_typescript", "language_typescript"),
    TSX: ("tree_sitter_typescript", "language_tsx"),
    JAVASCRIPT: ("tree_sitter_javascript", "language"),
}
# Every claim this module makes about tree-sitter names one exact measured version. The
# project bounds in `mcp/pyproject.toml` are installation compatibility, not citation
# provenance: R30 requires dependency claims to carry the exact resolved version from a
# lock surface. `test_memory_citation_grammars.py::PinnedDependencyTests` proves that
# the declared compatibility bounds, `mcp/uv.lock`, and installed parser builds stay
# aligned with these measured versions.
#
#   tree-sitter 0.26.1             `Node.end_point` gives the position AFTER the node's
#                                  last byte, so a construct ending at the end of line N
#                                  reports row N-1 (zero-based) and one ending at a
#                                  newline reports (N, 0).
#   tree-sitter 0.26.1             `Node.has_error` is true for a node that CONTAINS a
#                                  parse error, which is what lets one broken construct be
#                                  skipped without blinding the rest of the file.
#   tree-sitter 0.26.1             `Language` accepts the PyCapsule a grammar package's
#                                  `language()` returns, and raises when the grammar's ABI
#                                  is out of range. Measured ABI: python 15,
#                                  javascript 15, typescript and tsx 14.
#   tree-sitter-python 0.25.0      `async def` is a `function_definition` carrying an
#                                  `async` child, not a distinct node type; a decorated
#                                  definition is WRAPPED in `decorated_definition`.
#   tree-sitter-typescript 0.23.2  a decorator on an EXPORTED class is a SIBLING of the
#                                  class under `export_statement`; on a non-exported class
#                                  it is a child of the class node. Widening to the
#                                  wrapper is what makes both include the decorator.
#   tree-sitter-javascript 0.25.0  `variable_declarator` carries the destructuring pattern
#                                  in its `name` field, so the bound names come from
#                                  unpacking it rather than from reading that field.
MEASURED_VERSIONS = {
    "tree-sitter": "0.26.1",
    "tree-sitter-python": "0.25.0",
    "tree-sitter-typescript": "0.23.2",
    "tree-sitter-javascript": "0.25.0",
}

_PYTHON_WRAPPERS: Final = frozenset({"decorated_definition"})
_PYTHON_DESTRUCTURING: Final = DestructuringSpec(
    leaves=frozenset({"identifier"}),
    recurse_children=frozenset({"pattern_list", "tuple_pattern", "list_pattern"}),
)
_SCRIPT_WRAPPERS: Final = frozenset({"ambient_declaration", "export_statement"})
_SCRIPT_DESTRUCTURING: Final = DestructuringSpec(
    leaves=frozenset({"identifier", "shorthand_property_identifier_pattern"}),
    recurse_children=frozenset({"array_pattern", "object_pattern", "rest_pattern"}),
    recurse_fields={
        "pair_pattern": "value",
        "assignment_pattern": "left",
        "object_assignment_pattern": "left",
    },
)


@cache
def language(grammar: str) -> Language:
    """The loaded grammar, built once. A failure to load is fatal, never a fallback."""
    module_name, attribute = GRAMMAR_PACKAGES[grammar]
    try:
        return Language(getattr(importlib.import_module(module_name), attribute)())
    except (AttributeError, ImportError, TypeError, ValueError) as error:
        raise GrammarUnavailableError(
            f"the {grammar} grammar could not be loaded from {module_name}: {error}. "
            f"Install the pinned dependency ({module_name.replace('_', '-')}); the "
            f"citation check parses rather than guesses, and will not answer for a "
            f"language whose grammar is missing."
        ) from error


def _build_default_language_set() -> LanguageSet:
    python_tags = tags_engine.load_query_text("python", "tags.scm")
    ts_tags = tags_engine.load_query_text("typescript", "tags.scm")
    ts_tests = tags_engine.load_query_text("typescript", "tests.scm")
    js_tags = tags_engine.load_query_text("javascript", "tags.scm")
    js_tests = tags_engine.load_query_text("javascript", "tests.scm")
    return LanguageSet(
        providers=(
            TreeSitterLanguageProvider(
                key=PYTHON,
                suffixes=frozenset({".py"}),
                grammar_version=f"tree-sitter-python={MEASURED_VERSIONS['tree-sitter-python']}",
                load_language=lambda: language(PYTHON),
                tags_query_text=python_tags,
                wrappers=_PYTHON_WRAPPERS,
                destructuring=_PYTHON_DESTRUCTURING,
            ),
            TreeSitterLanguageProvider(
                key=TYPESCRIPT,
                suffixes=frozenset({".ts", ".mts", ".cts"}),
                grammar_version=(
                    f"tree-sitter-typescript={MEASURED_VERSIONS['tree-sitter-typescript']}"
                ),
                load_language=lambda: language(TYPESCRIPT),
                tags_query_text=ts_tags,
                wrappers=_SCRIPT_WRAPPERS,
                destructuring=_SCRIPT_DESTRUCTURING,
                tests_query_text=ts_tests,
            ),
            TreeSitterLanguageProvider(
                key=TSX,
                suffixes=frozenset({".tsx"}),
                grammar_version=(
                    f"tree-sitter-typescript={MEASURED_VERSIONS['tree-sitter-typescript']}"
                ),
                load_language=lambda: language(TSX),
                tags_query_text=ts_tags,
                wrappers=_SCRIPT_WRAPPERS,
                destructuring=_SCRIPT_DESTRUCTURING,
                tests_query_text=ts_tests,
            ),
            TreeSitterLanguageProvider(
                key=JAVASCRIPT,
                suffixes=frozenset({".js", ".jsx", ".mjs", ".cjs"}),
                grammar_version=(
                    f"tree-sitter-javascript={MEASURED_VERSIONS['tree-sitter-javascript']}"
                ),
                load_language=lambda: language(JAVASCRIPT),
                tags_query_text=js_tags,
                wrappers=_SCRIPT_WRAPPERS,
                destructuring=_SCRIPT_DESTRUCTURING,
                tests_query_text=js_tests,
            ),
            LeanLanguageProvider(),
        ),
        tree_sitter_version=MEASURED_VERSIONS["tree-sitter"],
    )


DEFAULT_LANGUAGE_SET: Final[LanguageSet] = _build_default_language_set()


def default_language_set() -> LanguageSet:
    """Return the process's deterministic default ``LanguageSet``."""
    return DEFAULT_LANGUAGE_SET


def active_language_set() -> LanguageSet:
    """Return the ``LanguageSet`` bound in the current context, or the deterministic default."""
    return _language_set.current_language_set(DEFAULT_LANGUAGE_SET)


def is_default_language_set() -> bool:
    """Whether the current context has no non-default ``LanguageSet`` override."""
    return not _language_set.has_language_set_override(DEFAULT_LANGUAGE_SET)


@contextmanager
def language_set_scope(selected: LanguageSet) -> Iterator[LanguageSet]:
    """Bind ``selected`` as the active ``LanguageSet`` for the duration of this context."""
    with _language_set.language_set_scope(selected) as bound:
        yield bound


def extractor_version(selected: LanguageSet | None = None) -> str:
    """Version identity string derived from ``selected`` or the active ``LanguageSet``."""
    return (active_language_set() if selected is None else selected).extractor_version


def language_identity(path: str, selected: LanguageSet | None = None) -> str | None:
    """Resolved ``<key>@<version>`` identity for ``path`` in ``selected`` or the active set."""
    return (active_language_set() if selected is None else selected).language_identity(path)


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
    cache = _ParseCache()
    token = _parsed_in_call.set(cache)
    try:
        yield
    finally:
        _parsed_in_call.reset(token)
        cache.values.clear()


def _parsed_source(
    path: str,
    provider: TreeSitterLanguageProvider,
    lines: Sequence[str],
    *,
    cache_tree: bool,
) -> _ParsedSource:
    cache = _parsed_in_call.get()
    if cache is None or not cache_tree:
        return _parse_source(provider, lines, capture_test_calls=cache_tree)
    key = (path, provider.key, provider.version, tuple(lines))
    with cache.lock:
        if key not in cache.values:
            cache.values[key] = _parse_source(provider, lines, capture_test_calls=True)
        return cache.values[key]


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


def _typescript_provider() -> TreeSitterLanguageProvider:
    provider = active_language_set().provider_for(TYPESCRIPT)
    if isinstance(provider, TreeSitterLanguageProvider):
        return provider
    fallback = DEFAULT_LANGUAGE_SET.provider_for(TYPESCRIPT)
    assert isinstance(fallback, TreeSitterLanguageProvider)
    return fallback


def typescript_anchor_identifier(text: str) -> str | None:
    """The direct identifier rooted by a complete TS call or generic type spelling."""
    provider = _typescript_provider()
    ts_version = provider.version
    memo = request_owner(_TypeScriptAnchorInputs, _TypeScriptAnchorInputs)
    if memo is None:
        if is_default_language_set():
            return _process_typescript_anchor_identifier(ts_version, text)
        return _parse_typescript_anchor_identifier(text, provider.load_language())
    key = (ts_version, text)
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
    return _parse_typescript_anchor_identifier(text, language(TYPESCRIPT))


def _resolve_provider(path: str) -> LanguageProvider | None:
    grammar = grammar_of(path)
    if grammar is None:
        return None
    return active_language_set().provider_for(grammar)


def bindings(path: str, lines: Sequence[str]) -> dict[str, list[Binding]]:
    """Every name ``path`` binds, with both its widened extent and its declaration line.

    One parse answers both, so a caller needing the declaration line does not re-parse the file.
    The declaration line starts the construct itself. It can follow a Python decorator
    or a Lean declaration prefix. For an exported TypeScript declaration, ``export``
    shares the declaration's first line.
    """
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
    """The one test case a line-range proof names, or why these lines are none (MIK-R194 rule 2).

    A range is admitted only in a JavaScript or TypeScript ``.test.``/``.spec.`` file, only when it
    is exactly one case's extent, and only when no ``path::title`` reaches that case. The extent is
    measured as every other extent in this module is, on a parse of the candidate bytes.
    """
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
