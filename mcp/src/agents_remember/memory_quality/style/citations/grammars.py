"""Parse citation definitions and extents with tree-sitter.

Supported suffixes are Python, JavaScript, JSX, TypeScript, TSX, MJS, CJS, MTS, and CTS.
A declared grammar that cannot load raises ``GrammarUnavailableError``; unsupported
suffixes use occurrence matching and therefore cannot prove a pure move.

Definitions include Python functions, classes, and assignments plus JavaScript/TypeScript
functions, classes, methods, fields, variable bindings, interfaces, types, enums,
namespaces, ambient signatures, and interface members. Extents include attached
decorators/exports/ambient declarations.

False-positive boundaries:

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
from dataclasses import dataclass
from functools import cache
from pathlib import PurePosixPath

from tree_sitter import Language, Node, Parser

from agents_remember.errors import GrammarUnavailableError

PYTHON = "python"
TYPESCRIPT = "typescript"
TSX = "tsx"
JAVASCRIPT = "javascript"

SUFFIX_GRAMMARS = {
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
# lock surface. `test_memory_citation_grammars.py::PinnedDependencyTests` proves only that
# the declared compatibility bounds still admit the measured and installed parser builds.
#
#   tree-sitter 0.26.0             `Node.end_point` gives the position AFTER the node's
#                                  last byte, so a construct ending at the end of line N
#                                  reports row N-1 (zero-based) and one ending at a
#                                  newline reports (N, 0).
#   tree-sitter 0.26.0             `Node.has_error` is true for a node that CONTAINS a
#                                  parse error, which is what lets one broken construct be
#                                  skipped without blinding the rest of the file.
#   tree-sitter 0.26.0             `Language` accepts the PyCapsule a grammar package's
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
    "tree-sitter": "0.26.0",
    "tree-sitter-python": "0.25.0",
    "tree-sitter-typescript": "0.23.2",
    "tree-sitter-javascript": "0.25.0",
}

PYTHON_BINDINGS = frozenset({"function_definition", "class_definition"})
PYTHON_ASSIGNMENTS = frozenset({"assignment", "augmented_assignment"})
PYTHON_PATTERNS = frozenset({"pattern_list", "tuple_pattern", "list_pattern"})
PYTHON_WRAPPERS = frozenset({"decorated_definition"})

SCRIPT_BINDINGS = frozenset(
    {
        "abstract_class_declaration",
        "abstract_method_signature",
        "class_declaration",
        "enum_declaration",
        "function_declaration",
        "function_signature",
        "generator_function_declaration",
        "interface_declaration",
        "internal_module",
        "method_definition",
        "method_signature",
        "property_signature",
        "public_field_definition",
        "type_alias_declaration",
    }
)
SCRIPT_DECLARATOR = "variable_declarator"
SCRIPT_PATTERNS = frozenset({"array_pattern", "object_pattern", "rest_pattern"})
SCRIPT_LEAVES = frozenset({"identifier", "shorthand_property_identifier_pattern"})
SCRIPT_DEFAULTED = frozenset({"assignment_pattern", "object_assignment_pattern"})
SCRIPT_WRAPPERS = frozenset({"ambient_declaration", "export_statement"})

Spans = dict[str, list[tuple[int, int]]]


@dataclass(frozen=True)
class CallLiteral:
    """One direct quoted argument, its syntax identity, and its call's line extent."""

    text: str
    start: int
    end: int
    argument_start_byte: int
    argument_end_byte: int


def grammar_of(path: str) -> str | None:
    """The grammar that reads ``path``, or ``None`` when nothing does."""
    return SUFFIX_GRAMMARS.get(PurePosixPath(path).suffix.lower())


def parsed(path: str) -> bool:
    """Whether a definition in ``path`` is distinguishable from a mention of it."""
    return grammar_of(path) is not None


@cache
def typescript_anchor_identifier(text: str) -> str | None:
    """The direct identifier rooted by a complete TS call or generic type spelling."""
    signature = f"function {text} {{}}"
    signature_tree = Parser(language(TYPESCRIPT)).parse(signature.encode("utf-8"))
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
        tree = Parser(language(TYPESCRIPT)).parse(source.encode("utf-8"))
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


def call_argument_literals(path: str, lines: list[str]) -> tuple[CallLiteral, ...]:
    """Every direct string argument and the call it belongs to, in document order."""
    grammar = grammar_of(path)
    if grammar not in {TYPESCRIPT, TSX, JAVASCRIPT}:
        return ()
    tree = Parser(language(grammar)).parse("\n".join(lines).encode("utf-8"))
    found: list[CallLiteral] = []
    for node in _walk(tree.root_node):
        if node.type not in {"string", "template_string"} or node.parent is None:
            continue
        arguments = node.parent
        call = arguments.parent
        if arguments.type != "arguments" or call is None or call.type != "call_expression":
            continue
        text = "" if node.text is None else node.text.decode("utf-8", errors="replace")
        start, end = _span(call)
        found.append(
            CallLiteral(
                text=text,
                start=start,
                end=end,
                argument_start_byte=node.start_byte,
                argument_end_byte=node.end_byte,
            )
        )
    return tuple(found)


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


def definitions(path: str, lines: list[str]) -> Spans:
    """Every name ``path`` binds, and the line span of the construct that binds it.

    A language with no grammar binds nothing, which drops the file to occurrence matching
    -- the stated ceiling, not a parse failure. A construct CONTAINING a syntax error is
    skipped while its file's sound constructs are kept, so a file written for another
    dialect degrades to occurrence matching for the broken part alone.
    """
    grammar = grammar_of(path)
    if grammar is None:
        return {}
    # A Parser is built per call rather than cached beside its Language: the Language is
    # immutable and shared safely, a Parser holds the cursor for one parse and two
    # concurrent gate runs sharing one would interleave.
    tree = Parser(language(grammar)).parse("\n".join(lines).encode("utf-8"))
    reader = _python_names if grammar == PYTHON else _script_names
    wrappers = PYTHON_WRAPPERS if grammar == PYTHON else SCRIPT_WRAPPERS
    found: Spans = {}
    for node in _walk(tree.root_node):
        names = [] if node.has_error else reader(node)
        if not names:
            continue
        span = _span(_widened(node, wrappers))
        for name in names:
            found.setdefault(name, []).append(span)
    return found


def _walk(root: Node) -> list[Node]:
    """Every node in the tree, at any depth, in DOCUMENT ORDER.

    Order is contract, not incident: a name bound twice in one file yields its extents in
    the order a reader meets them, and a caller comparing two runs would otherwise see the
    same file produce two different answers.
    """
    found: list[Node] = []
    pending = [root]
    while pending:
        node = pending.pop()
        found.append(node)
        pending.extend(reversed(node.children))
    return found


def _widened(node: Node, wrappers: frozenset[str]) -> Node:
    """``node`` grown outwards through the syntax that decorates or exports it."""
    while node.parent is not None and node.parent.type in wrappers:
        node = node.parent
    return node


def _span(node: Node) -> tuple[int, int]:
    """The one-based line range ``node`` occupies."""
    start = node.start_point[0] + 1
    row, column = node.end_point
    return start, max(start, row if column == 0 else row + 1)


def _text(node: Node | None) -> list[str]:
    return [] if node is None or node.text is None else [node.text.decode("utf-8")]


def _python_names(node: Node) -> list[str]:
    """The names one Python construct binds."""
    if node.type in PYTHON_BINDINGS:
        return _text(node.child_by_field_name("name"))
    if node.type in PYTHON_ASSIGNMENTS:
        return _python_targets(node.child_by_field_name("left"))
    return []


def _python_targets(node: Node | None) -> list[str]:
    """The plain names an assignment target binds, unpacking nested tuples and lists.

    An attribute or subscript target (``holder.field = 1``) binds no name this check can
    resolve to a file, so it yields nothing rather than yielding ``holder``.
    """
    if node is None:
        return []
    if node.type == "identifier":
        return _text(node)
    if node.type in PYTHON_PATTERNS:
        return [name for child in node.named_children for name in _python_targets(child)]
    return []


def _script_names(node: Node) -> list[str]:
    """The names one JavaScript or TypeScript construct binds."""
    if node.type in SCRIPT_BINDINGS:
        return _text(node.child_by_field_name("name"))
    if node.type == SCRIPT_DECLARATOR:
        return _script_targets(node.child_by_field_name("name"))
    return []


def _script_targets(node: Node | None) -> list[str]:
    """The names a declarator binds, unpacking destructuring one alternative at a time.

    Only the BOUND side is read. ``{ p: renamed }`` binds ``renamed`` and not ``p``, and
    ``{ a = fallback }`` binds ``a`` and not ``fallback`` -- a descendant sweep would
    collect both halves and turn every default value into a declaration.
    """
    if node is None:
        return []
    if node.type in SCRIPT_LEAVES:
        return _text(node)
    if node.type == "pair_pattern":
        return _script_targets(node.child_by_field_name("value"))
    if node.type in SCRIPT_DEFAULTED:
        return _script_targets(node.child_by_field_name("left"))
    if node.type in SCRIPT_PATTERNS:
        return [name for child in node.named_children for name in _script_targets(child)]
    return []
