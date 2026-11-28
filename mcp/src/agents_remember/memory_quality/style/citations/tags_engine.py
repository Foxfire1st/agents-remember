"""Language-agnostic declarative ``tags.scm`` and ``tests.scm`` anchor extraction engine.

This module executes standard upstream-compatible tree-sitter ``tags.scm`` queries
(``@definition.*``, ``@name``, ``@doc``, ``@local.*``) and optional ``tests.scm``
call-site queries (``@test.*``) without per-language AST walker code. Language-specific
structural rules are passed as declarative data:

* ``wrappers`` -- parent syntax node types widened over the definition extent while
  preserving ``declaration`` on the captured construct's own first line;
* ``destructuring`` -- bound-side pattern unpacking rules so multi-name bindings share
  their declarator extent without capturing keys or default values.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from types import MappingProxyType

from tree_sitter import Language, Node, Query, QueryCursor, Tree

from agents_remember.memory_quality.style.citations import test_cases

QUERIES_ROOT = Path(__file__).resolve().parents[3] / "package_data" / "citations" / "queries"


@dataclass(frozen=True)
class QualifierSpec:
    """Declarative per-language container-qualifier policy.

    ``separators`` lists language-native container separators (an empty tuple honestly refuses
    all qualified lookups). ``separator_kinds`` optionally constrains which definition capture
    kinds a final separator admits (for example ``#`` vs ``.`` in Ruby).
    """

    separators: tuple[str, ...] = (".",)
    separator_kinds: Mapping[str, frozenset[str]] = field(
        default_factory=lambda: MappingProxyType({})
    )

    @property
    def tracked_kinds(self) -> frozenset[str]:
        if not self.separator_kinds:
            return frozenset()
        return frozenset().union(*self.separator_kinds.values())


@dataclass(frozen=True)
class Binding:
    """One name a construct binds: its extent, and the line the declaration itself begins on.

    The two differ for a decorated Python definition. ``widened`` grows the construct outwards
    through ``decorated_definition`` so its extent covers the decorator, which is what makes a
    range projected from that extent quote the whole construct. The declaration the name belongs
    to, however, begins on the ``def``/``class`` line BELOW the decorator -- so a citation that
    starts at exactly the declaration's own lines must be read as current for it rather than as
    one line short of it.
    """

    span: tuple[int, int]
    declaration: int
    test_case: bool = False
    full_name: str | None = None
    scope_uncertain: bool = False
    containers: tuple[str, ...] = ()
    qualifier_kind: str = ""


@dataclass(frozen=True)
class DestructuringSpec:
    """Declarative bound-side unpacking rules for destructuring assignment targets."""

    leaves: frozenset[str] = frozenset()
    recurse_children: frozenset[str] = frozenset()
    recurse_fields: Mapping[str, str] = field(default_factory=dict)

    def is_container(self, node_type: str) -> bool:
        """Whether ``node_type`` is a destructuring pattern rather than a direct name leaf."""
        return node_type in self.recurse_children or node_type in self.recurse_fields

    def unpack(self, node: Node | None) -> list[str]:
        """Extract only the bound-side leaf names from a destructuring target in source order."""
        if node is None:
            return []
        if node.type in self.leaves:
            return [] if node.text is None else [node.text.decode("utf-8")]
        field_name = self.recurse_fields.get(node.type)
        if field_name is not None:
            return self.unpack(node.child_by_field_name(field_name))
        if node.type in self.recurse_children:
            return [name for child in node.named_children for name in self.unpack(child)]
        return []


TagEntry = tuple[int, int, int, str, Binding]


def load_query_text(language_dir: str, filename: str) -> str:
    """Read a packaged ``.scm`` query file for one language."""
    return (QUERIES_ROOT / language_dir / filename).read_text(encoding="utf-8")


def query_digest(query_text: str) -> str:
    """Stable short content digest of a ``.scm`` query for extractor-version identity."""
    return hashlib.sha256(query_text.encode("utf-8")).hexdigest()[:16]


@cache
def compile_query(language: Language, query_text: str) -> Query:
    """Compile a tree-sitter query once per ``(language, query_text)`` pair."""
    return Query(language, query_text)


def widened(node: Node, wrappers: frozenset[str]) -> Node:
    """``node`` grown outwards through the syntax that decorates, exports, or wraps it."""
    while node.parent is not None and node.parent.type in wrappers:
        node = node.parent
    return node


def widened_span(
    node: Node,
    wrappers: frozenset[str],
    leading_wrappers: frozenset[str] = frozenset(),
) -> tuple[int, int]:
    """Span of ``node`` grown through parent ``wrappers`` and preceding ``leading_wrappers``."""
    outer = widened(node, wrappers)
    start, end = test_cases.span_of(outer)
    if leading_wrappers:
        prev = outer.prev_named_sibling
        while prev is not None and prev.type in leading_wrappers:
            start = prev.start_point[0] + 1
            prev = prev.prev_named_sibling
    return (start, end)


@dataclass
class _TagCollector:
    wrappers: frozenset[str]
    destructuring: DestructuringSpec
    leading_wrappers: frozenset[str] = frozenset()
    tracked_kinds: frozenset[str] = frozenset()
    seen: set[tuple[int, int]] = field(default_factory=set)
    entries: list[TagEntry] = field(default_factory=list)

    def collect_match(self, captures: Mapping[str, list[Node]]) -> None:
        definition_nodes = [
            (capture_name.removeprefix("definition."), node)
            for capture_name, nodes in captures.items()
            if capture_name.startswith("definition.")
            for node in nodes
            if not node.has_error
        ]
        name_nodes = captures.get("name", ())
        if not definition_nodes or not name_nodes:
            return
        containers = _extract_containers(captures.get("container", ()))
        for capture_kind, definition_node in definition_nodes:
            binding = Binding(
                span=widened_span(definition_node, self.wrappers, self.leading_wrappers),
                declaration=test_cases.span_of(definition_node)[0],
                containers=containers,
                qualifier_kind=capture_kind if capture_kind in self.tracked_kinds else "",
            )
            self._append_names(definition_node, binding, name_nodes)

    def _append_names(
        self,
        definition_node: Node,
        binding: Binding,
        name_nodes: Sequence[Node],
    ) -> None:
        for name_node in name_nodes:
            pair = (definition_node.id, name_node.id)
            if pair in self.seen or name_node.has_error:
                continue
            self.seen.add(pair)
            for name in _extract_names(name_node, self.destructuring):
                self.entries.append(
                    (
                        definition_node.start_byte,
                        -definition_node.end_byte,
                        len(self.entries),
                        name,
                        binding,
                    )
                )


def _extract_containers(nodes: Sequence[Node]) -> tuple[str, ...]:
    if not nodes:
        return ()
    ordered = sorted(nodes, key=lambda node: node.start_byte)
    return tuple(
        node.text.decode("utf-8")
        for node in ordered
        if not node.has_error and node.text is not None and node.text
    )


@dataclass(frozen=True)
class TagExtractionOptions:
    """Optional language policy modifiers for ``extract_tag_entries``."""

    leading_wrappers: frozenset[str] = frozenset()
    tracked_kinds: frozenset[str] = frozenset()


def extract_tag_entries(
    tree: Tree,
    query: Query,
    *,
    wrappers: frozenset[str],
    destructuring: DestructuringSpec,
    options: TagExtractionOptions | None = None,
) -> list[TagEntry]:
    """Run a standard ``tags.scm`` query and return document-ordered binding entries."""
    opts = TagExtractionOptions() if options is None else options
    cursor = QueryCursor(query)
    collector = _TagCollector(
        wrappers=wrappers,
        destructuring=destructuring,
        leading_wrappers=opts.leading_wrappers,
        tracked_kinds=opts.tracked_kinds,
    )
    for _pattern_index, captures in cursor.matches(tree.root_node):
        collector.collect_match(captures)
    return collector.entries


def extract_test_calls(tree: Tree, query: Query) -> tuple[Node, ...]:
    """Run a ``tests.scm`` call-site query and return candidate calls in document order."""
    cursor = QueryCursor(query)
    captured = cursor.captures(tree.root_node)
    nodes: list[Node] = []
    seen: set[int] = set()
    for capture_name, matched in captured.items():
        if not capture_name.startswith("test."):
            continue
        for node in matched:
            if node.id not in seen:
                seen.add(node.id)
                nodes.append(node)
    nodes.sort(key=lambda node: (node.start_byte, -node.end_byte))
    return tuple(nodes)


def extract_test_case_entries(
    test_calls: Sequence[Node],
    *,
    wrappers: frozenset[str],
) -> list[TagEntry]:
    """Evaluate captured test call sites through the JS/TS test-case policy layer."""
    entries: list[TagEntry] = []
    order = 0
    for node in test_calls:
        cases = test_cases.case_names(node)
        if not cases:
            continue
        binding = Binding(
            span=test_cases.span_of(widened(node, wrappers)),
            declaration=test_cases.span_of(node)[0],
            test_case=True,
        )
        for name in cases:
            entries.append((node.start_byte, -node.end_byte, order, name, binding))
            order += 1
    return entries


def merge_entries(
    tag_entries: Sequence[TagEntry],
    case_entries: Sequence[TagEntry] = (),
) -> dict[str, list[Binding]]:
    """Order tag and test-case entries in pre-order document order and group by name."""
    combined = [*tag_entries, *case_entries]
    combined.sort(key=lambda item: (item[0], item[1], item[2]))
    found: dict[str, list[Binding]] = {}
    for _start, _neg_end, _order, name, binding in combined:
        found.setdefault(name, []).append(binding)
    return found


def _extract_names(name_node: Node, destructuring: DestructuringSpec) -> list[str]:
    if destructuring.is_container(name_node.type):
        return destructuring.unpack(name_node)
    return [] if name_node.text is None else [name_node.text.decode("utf-8")]
