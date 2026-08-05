"""Structural identities for the construct an anchored range denotes.

Line numbers and source bytes are deliberately absent from an identity. Formatting can move
or reflow every line in a repository without changing a construct; tree-sitter leaf tokens
and node kinds retain operators, literals, names, and shape while omitting whitespace and
comments. Unparsed prose uses whitespace-normalised section text, which gives Markdown reflow
the same non-event semantics.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field

from tree_sitter import Node, Parser, Tree

from agents_remember.memory_quality.style.citations import extents, grammars, model

STRUCTURAL_DELIMITERS = frozenset({"(", ")", "[", "]", "{", "}", ",", ";", ":", '"', "'", "`"})


@dataclass
class StructuralView:
    """One parsed source revision, reused for every anchor resolved inside it."""

    path: str
    lines: list[str]
    _tree: Tree | None = None
    _bindings: dict[tuple[str, int, int], Node] | None = None
    _calls: dict[tuple[int, int], Node] | None = None
    _fingerprints: dict[tuple[model.Anchor, extents.Extent], str] = field(default_factory=dict)

    def fingerprint(self, anchor: model.Anchor, extent: extents.Extent) -> str:
        key = (anchor, extent)
        if key not in self._fingerprints:
            self._fingerprints[key] = self._derive(anchor, extent)
        return self._fingerprints[key]

    def _derive(self, anchor: model.Anchor, extent: extents.Extent) -> str:
        grammar = grammars.grammar_of(self.path)
        body = self.lines[max(0, extent.start - 1) : extent.end]
        if grammar is None:
            return _digest(("text", model.normalised("\n".join(body))))

        node = self.binding_node(anchor, extent, grammar)
        if node is None and extent.kind == extents.CALL:
            self.index(grammar)
            assert self._calls is not None
            node = self._calls.get((extent.start, extent.end))
        if node is None:
            fragment = Parser(grammars.language(grammar)).parse("\n".join(body).encode("utf-8"))
            node = fragment.root_node
        return _digest(_tokens(node))

    def tree(self, grammar: str) -> Tree:
        if self._tree is None:
            self._tree = Parser(grammars.language(grammar)).parse(
                "\n".join(self.lines).encode("utf-8")
            )
        return self._tree

    def binding_node(
        self,
        anchor: model.Anchor,
        extent: extents.Extent,
        grammar: str,
    ) -> Node | None:
        if anchor.kind != model.SYMBOL or extent.kind != extents.DEFINITION:
            return None
        self.index(grammar)
        assert self._bindings is not None
        return self._bindings.get((anchor.text, extent.start, extent.end))

    def index(self, grammar: str) -> None:
        """Index binding and call nodes once for every fingerprint in this revision."""
        if self._bindings is not None:
            return
        reader = grammars._python_names if grammar == grammars.PYTHON else grammars._script_names
        wrappers = (
            grammars.PYTHON_WRAPPERS if grammar == grammars.PYTHON else grammars.SCRIPT_WRAPPERS
        )
        bindings: dict[tuple[str, int, int], Node] = {}
        calls: dict[tuple[int, int], Node] = {}
        for node in grammars._walk(self.tree(grammar).root_node):
            if node.type == "call_expression":
                calls[_span(node)] = node
            if node.has_error:
                continue
            names = reader(node)
            if not names:
                continue
            widened = grammars._widened(node, wrappers)
            start, end = _span(widened)
            for name in names:
                bindings[(name, start, end)] = widened
        self._bindings = bindings
        self._calls = calls


def fingerprint(
    anchor: model.Anchor,
    path: str,
    lines: list[str],
    extent: extents.Extent,
) -> str:
    """Uncached convenience entry point for callers resolving one construct."""
    return StructuralView(path, lines).fingerprint(anchor, extent)


def _span(node: Node) -> tuple[int, int]:
    start = node.start_point[0] + 1
    row, column = node.end_point
    return start, max(start, row if column == 0 else row + 1)


def _tokens(node: Node) -> Iterable[str]:
    """A syntax token stream with comments and layout absent but operators retained."""
    if node.type == "comment":
        return ()
    if not node.children:
        if node.type in STRUCTURAL_DELIMITERS:
            return ()
        text = "" if node.text is None else node.text.decode("utf-8", errors="replace")
        return (node.type, text)
    return (node.type, *(token for child in node.children for token in _tokens(child)))


def _digest(tokens: Iterable[str]) -> str:
    payload = "\0".join(tokens).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
