"""Plain-string JavaScript/TypeScript test cases as citation definitions (MIK-R54)."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from tree_sitter import Node

_ESCAPE = re.compile(r"\\(?:u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|\r\n|[\s\S])")


def _unescape(match: re.Match[str]) -> str:
    token = match.group()[1:]
    if token.startswith("u{"):
        return chr(int(token[2:-1], 16))
    if token.startswith(("u", "x")) and len(token) > 1:
        return chr(int(token[1:], 16))
    return {
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "b": "\b",
        "f": "\f",
        "v": "\v",
        "0": "\0",
        "\n": "",
        "\r": "",
        "\r\n": "",
    }.get(token, token)


def is_test_file(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return ".test." in name or ".spec." in name


def _callee(node: Node) -> str:
    function = node.child_by_field_name("function")
    return "" if function is None or function.text is None else function.text.decode("utf-8")


def _title(node: Node) -> str | None:
    arguments = node.child_by_field_name("arguments")
    if arguments is None or not arguments.named_children:
        return None
    first = next((child for child in arguments.named_children if child.type != "comment"), None)
    if first is None or first.type != "string" or first.has_error or first.text is None:
        return None
    title = _ESCAPE.sub(_unescape, first.text.decode("utf-8")[1:-1])
    # Combine valid UTF-16 pairs; preserve lone surrogate units as JavaScript does.
    return title.encode("utf-16-le", errors="surrogatepass").decode(
        "utf-16-le", errors="surrogatepass"
    )


def refusal(nodes: list[Node], name: str) -> str:
    """Why an unsupported test call cannot name a symbol proof."""
    for node in nodes:
        if node.type != "call_expression":
            continue
        callee = _callee(node)
        if not any(callee == root or callee.startswith(root + ".") for root in ("it", "test")):
            continue
        arguments = node.child_by_field_name("arguments")
        raw = "" if arguments is None or arguments.text is None else arguments.text.decode("utf-8")
        if name not in raw:
            continue
        for modifier in (".skip", ".todo", ".each"):
            if modifier in callee:
                return f"test case {name!r} uses {modifier}, which is not a definition; use a line_range"
        if _title(node) is None:
            return f"test case {name!r} has a computed title, not a plain string; use a line_range"
    return (
        f"test case {name!r} does not resolve uniquely: only plain-string it/test titles "
        "are definitions; .skip, .todo, .each and computed titles are not; use a line_range"
    )


def case_names(node: Node) -> tuple[str, ...]:
    """Qualified title and bare title for one plain ``it`` or ``test`` call."""
    if node.type != "call_expression" or node.has_error or _callee(node) not in {"it", "test"}:
        return ()
    title = _title(node)
    if title is None:
        return ()
    parents: list[str] = []
    parent = node.parent
    while parent is not None:
        if parent.type == "call_expression" and _callee(parent) == "describe":
            enclosing = _title(parent)
            if enclosing is None:
                return ()
            parents.append(enclosing)
        parent = parent.parent
    qualified = " > ".join((*reversed(parents), title))
    return tuple(dict.fromkeys((qualified, title)))
