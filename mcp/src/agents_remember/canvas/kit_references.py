"""Reference extraction, kit asset resolution, and kit byte verification for canvas drafts.

Non-kit draft files (HTML, SVG, CSS, and JavaScript/ESM) may reference relative assets and pinned
kit bundles under ``kit/<library>/<version>/...``. This module parses those references without
regex heuristics over CSS or JavaScript source: HTML/SVG attributes and inline blocks use
``HTMLParser``, CSS ``@import`` and ``url()`` use a single-pass lexical scanner over comments,
strings, and brace depth, and JavaScript static/dynamic imports and re-exports use
``tree-sitter-javascript`` so imports inside comments or string literals never match.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from html.parser import HTMLParser
from pathlib import Path

import tree_sitter_javascript
from tree_sitter import Language, Node, Parser

from agents_remember.canvas.kit import (
    KIT_CONTRACT_PREFIX,
    KIT_ROOT,
    KitRefused,
    kit_assets,
    kit_files,
)

__all__ = [
    "KitCarryResult",
    "carry_kit_assets",
    "extract_css_references",
    "extract_html_references",
    "extract_js_references",
    "extract_references",
    "is_external_reference",
    "is_ignored_reference",
    "resolve_kit_references",
    "resolve_reference",
    "verify_kit_assets",
]

_HTML_SUFFIXES = (".html", ".htm", ".svg")
_CSS_SUFFIXES = (".css",)
_JS_SUFFIXES = (".js", ".mjs")

_IGNORED_PREFIXES = ("#", "?", "data:")
_ABSOLUTE_URL = re.compile(r"^(?:[a-z][a-z0-9+.\-]*:|//)", re.IGNORECASE)
_JS_SCRIPT_TYPES = frozenset(
    {
        "",
        "module",
        "text/javascript",
        "application/javascript",
        "text/ecmascript",
        "application/ecmascript",
    }
)
_URL_ATTRIBUTES = frozenset({"href", "xlink:href", "src", "poster"})


@dataclass(frozen=True)
class KitCarryResult:
    """Kit references discovered in a draft and the verified assets carried for them."""

    carried: dict[str, bytes]
    referenced_known: tuple[str, ...] = ()
    unknown_kit_paths: tuple[str, ...] = ()
    kit_problems: tuple[str, ...] = ()


@cache
def _js_language() -> Language:
    return Language(tree_sitter_javascript.language())


def is_ignored_reference(reference: str) -> bool:
    """Return True for empty, fragment, query, or inline ``data:`` references."""

    cleaned = reference.strip()
    return not cleaned or cleaned.lower().startswith(_IGNORED_PREFIXES)


def is_external_reference(reference: str) -> bool:
    """Return True for scheme-qualified or protocol-relative URLs (excluding ``data:``)."""

    cleaned = reference.strip()
    if is_ignored_reference(cleaned):
        return False
    return bool(_ABSOLUTE_URL.match(cleaned))


def resolve_reference(referrer: str, reference: str) -> str | None:
    """Resolve ``reference`` relative to ``referrer`` inside the revision, or return ``None``."""

    cleaned = reference.strip()
    if is_ignored_reference(cleaned) or is_external_reference(cleaned) or "\\" in cleaned:
        return None
    bare = cleaned.split("#", maxsplit=1)[0].split("?", maxsplit=1)[0]
    if not bare:
        return None
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(referrer), bare))
    if joined == ".." or joined.startswith("../") or joined.startswith("/"):
        return None
    return joined


def _is_css_ident_char(ch: str) -> bool:
    return ch.isalnum() or ch in {"_", "-"} or ord(ch) >= 0x80


def _skip_css_ws_and_comments(text: str, pos: int) -> int:
    n = len(text)
    while pos < n:
        if text[pos].isspace():
            pos += 1
        elif text.startswith("/*", pos):
            end = text.find("*/", pos + 2)
            pos = n if end == -1 else end + 2
        else:
            break
    return pos


def _scan_css_string(text: str, start: int) -> tuple[str, int]:
    """Scan a single- or double-quoted CSS string, respecting backslash escapes."""

    quote = text[start]
    n = len(text)
    pos = start + 1
    while pos < n:
        ch = text[pos]
        if ch == "\\":
            pos += 2
            continue
        if ch == quote:
            return text[start + 1 : pos], pos + 1
        pos += 1
    return text[start + 1 : n], n


def _scan_css_url(text: str, start: int) -> tuple[str | None, int]:
    """Scan a CSS ``url(...)`` function call starting at ``u``."""

    n = len(text)
    pos = _skip_css_ws_and_comments(text, start + 4)
    if pos >= n:
        return None, n
    if text[pos] in {'"', "'"}:
        value, pos = _scan_css_string(text, pos)
        pos = _skip_css_ws_and_comments(text, pos)
        if pos < n and text[pos] == ")":
            return value.strip() or None, pos + 1
        return None, pos
    token_start = pos
    while pos < n:
        ch = text[pos]
        if ch == "\\":
            pos += 2
            continue
        if ch.isspace() or ch in {")", "(", '"', "'"} or text.startswith("/*", pos):
            break
        pos += 1
    raw_value = text[token_start:pos]
    pos = _skip_css_ws_and_comments(text, pos)
    if pos < n and text[pos] == ")":
        return raw_value.strip() or None, pos + 1
    return None, pos


def _skip_css_statement(text: str, pos: int) -> int:
    """Skip an invalid nested ``@import`` directive up to ``;`` or before ``}``."""

    n = len(text)
    while pos < n:
        ch = text[pos]
        if ch == "\\":
            pos += 2
            continue
        if text.startswith("/*", pos):
            end = text.find("*/", pos + 2)
            pos = n if end == -1 else end + 2
            continue
        if ch in {'"', "'"}:
            _, pos = _scan_css_string(text, pos)
            continue
        if ch == ";":
            return pos + 1
        if ch == "}":
            return pos
        pos += 1
    return n


def _matches_css_import(text: str, pos: int) -> bool:
    return (
        text[pos] == "@"
        and text[pos + 1 : pos + 7].lower() == "import"
        and (pos + 7 >= len(text) or not _is_css_ident_char(text[pos + 7]))
    )


def _matches_css_url(text: str, pos: int) -> bool:
    return (
        text[pos] in {"u", "U"}
        and text[pos : pos + 4].lower() == "url("
        and (pos == 0 or not _is_css_ident_char(text[pos - 1]))
    )


def _scan_css_import(text: str, pos: int, depth: int) -> tuple[str | None, int]:
    """Scan a CSS ``@import`` directive; ignore nested directives inside ``{ ... }``."""

    if depth != 0:
        return None, _skip_css_statement(text, pos + 7)
    target_pos = _skip_css_ws_and_comments(text, pos + 7)
    if target_pos < len(text) and text[target_pos] in {'"', "'"}:
        value, next_pos = _scan_css_string(text, target_pos)
        return value.strip() or None, next_pos
    if text[target_pos : target_pos + 4].lower() == "url(":
        return _scan_css_url(text, target_pos)
    return None, target_pos


def _skip_css_non_code(text: str, pos: int) -> int | None:
    """Advance past a CSS escape, comment, or string literal, or return ``None``."""

    ch = text[pos]
    if ch == "\\":
        return pos + 2
    if text.startswith("/*", pos):
        end = text.find("*/", pos + 2)
        return len(text) if end == -1 else end + 2
    if ch in {'"', "'"}:
        _, next_pos = _scan_css_string(text, pos)
        return next_pos
    return None


def extract_css_references(text: str) -> tuple[str, ...]:
    """Extract top-level ``@import`` and ``url(...)`` targets from CSS via lexical scanning."""

    found: list[str] = []
    n = len(text)
    pos = 0
    depth = 0
    while pos < n:
        skipped = _skip_css_non_code(text, pos)
        if skipped is not None:
            pos = skipped
            continue
        ch = text[pos]
        if ch == "{":
            depth += 1
            pos += 1
        elif ch == "}":
            depth = max(0, depth - 1)
            pos += 1
        elif _matches_css_import(text, pos):
            value, pos = _scan_css_import(text, pos, depth)
            if value:
                found.append(value)
        elif _matches_css_url(text, pos):
            value, pos = _scan_css_url(text, pos)
            if value:
                found.append(value)
        else:
            pos += 1
    return tuple(found)


def _string_literal_value(node: Node, source: bytes) -> str | None:
    if node.type == "string":
        raw = source[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
            return raw[1:-1].strip() or None
        return None
    if node.type == "template_string":
        if any(child.type == "template_substitution" for child in node.children):
            return None
        raw = source[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")
        if len(raw) >= 2 and raw[0] == "`" and raw[-1] == "`":
            return raw[1:-1].strip() or None
    return None


def _import_specifier_node(node: Node) -> Node | None:
    if node.type in {"import_statement", "export_statement"}:
        return node.child_by_field_name("source")
    if node.type != "call_expression":
        return None
    function_node = node.child_by_field_name("function")
    if function_node is None or function_node.type != "import":
        return None
    arguments = node.child_by_field_name("arguments")
    if arguments is None:
        return None
    return next((child for child in arguments.named_children if child.type != "comment"), None)


def extract_js_references(text: str) -> tuple[str, ...]:
    """Extract static imports, re-exports, and literal dynamic imports via tree-sitter."""

    source = text.encode("utf-8")
    tree = Parser(_js_language()).parse(source)
    found: list[str] = []
    stack: list[Node] = [tree.root_node]
    while stack:
        node = stack.pop()
        specifier = _import_specifier_node(node)
        if specifier is not None:
            value = _string_literal_value(specifier, source)
            if value:
                found.append(value)
        for child in reversed(node.children):
            stack.append(child)
    return tuple(found)


class _HtmlReferenceParser(HTMLParser):
    """Collect attribute references plus inline ``<style>`` and ``<script>`` references."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[str] = []
        self._active_block: str | None = None
        self._block_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower = tag.lower()
        attr_map = self._record_attributes(attrs)
        if lower == "style":
            self._active_block = "style"
            self._block_chunks = []
        elif lower == "script":
            media = attr_map.get("type", "").strip().lower().split(";", 1)[0].strip()
            if media in _JS_SCRIPT_TYPES:
                self._active_block = "script"
                self._block_chunks = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del tag
        self._record_attributes(attrs)

    def handle_data(self, data: str) -> None:
        if self._active_block is not None:
            self._block_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._active_block == tag.lower():
            self.flush_block()

    def flush_block(self) -> None:
        if self._active_block is None:
            return
        body = "".join(self._block_chunks)
        block_kind = self._active_block
        self._active_block = None
        self._block_chunks = []
        if not body.strip():
            return
        if block_kind == "style":
            self.references.extend(extract_css_references(body))
        elif block_kind == "script":
            self.references.extend(extract_js_references(body))

    def _record_attributes(self, attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        attr_map: dict[str, str] = {}
        for raw_name, raw_value in attrs:
            name = raw_name.lower()
            value = "" if raw_value is None else raw_value
            attr_map[name] = value
            if name in _URL_ATTRIBUTES:
                stripped = value.strip()
                if stripped:
                    self.references.append(stripped)
            elif name == "srcset":
                for entry in value.split(","):
                    parts = entry.strip().split(maxsplit=1)
                    if parts and parts[0]:
                        self.references.append(parts[0])
            elif name == "style" and value.strip():
                self.references.extend(extract_css_references(value))
        return attr_map


def extract_html_references(text: str) -> tuple[str, ...]:
    """Extract subresource references from HTML or SVG markup and inline CSS/JS blocks."""

    parser = _HtmlReferenceParser()
    parser.feed(text)
    parser.close()
    parser.flush_block()
    return tuple(parser.references)


def extract_references(path: str, payload: bytes | str) -> tuple[str, ...]:
    """Extract raw subresource references from one non-kit draft asset."""

    if path.startswith(KIT_CONTRACT_PREFIX):
        return ()
    lower = path.lower()
    if not lower.endswith((*_HTML_SUFFIXES, *_CSS_SUFFIXES, *_JS_SUFFIXES)):
        return ()
    if isinstance(payload, bytes):
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return ()
    else:
        text = payload
    if lower.endswith(_HTML_SUFFIXES):
        return extract_html_references(text)
    if lower.endswith(_CSS_SUFFIXES):
        return extract_css_references(text)
    return extract_js_references(text)


def verify_kit_assets(assets: Mapping[str, bytes], *, root: Path | None = None) -> tuple[str, ...]:
    """Verify every asset under ``kit/`` matches the shipped kit bytes and manifest digest."""

    kit_entries = [
        (path, payload)
        for path, payload in sorted(assets.items())
        if path.startswith(KIT_CONTRACT_PREFIX)
    ]
    if not kit_entries:
        return ()
    base = root or KIT_ROOT
    try:
        shipped_index = kit_files(base)
    except KitRefused as refused:
        return (f"the shipped kit could not be verified ({refused.reason}): {refused.detail}",)
    problems: list[str] = []
    for path, payload in kit_entries:
        kit_rel = path.removeprefix(KIT_CONTRACT_PREFIX)
        expected = shipped_index.get(kit_rel)
        if expected is None:
            problems.append(
                f"{path!r} is not a shipped kit file; put a changed or custom copy outside 'kit/'"
            )
            continue
        try:
            shipped_bytes = (base / kit_rel).read_bytes()
        except OSError as error:
            problems.append(f"{path!r} could not be verified against the shipped kit: {error}")
            continue
        digest = hashlib.sha256(payload).hexdigest()
        if payload != shipped_bytes or len(payload) != expected.bytes or digest != expected.sha256:
            problems.append(
                f"{path!r} differs from the shipped kit bytes (expected sha256:{expected.sha256}); "
                "put a changed copy outside 'kit/' rather than modifying a kit file in place"
            )
    return tuple(problems)


def resolve_kit_references(
    draft_assets: Mapping[str, bytes], *, root: Path | None = None
) -> KitCarryResult:
    """Find kit references in non-kit draft files, carry shipped assets, and verify ``kit/``."""

    base = root or KIT_ROOT
    problems = list(verify_kit_assets(draft_assets, root=base))
    try:
        shipped_index = kit_files(base)
    except KitRefused as refused:
        problems.append(f"the shipped kit could not be loaded ({refused.reason}): {refused.detail}")
        return KitCarryResult(carried={}, kit_problems=tuple(dict.fromkeys(problems)))

    known: set[str] = set()
    unknown: set[str] = set()
    for path, payload in sorted(draft_assets.items()):
        if path.startswith(KIT_CONTRACT_PREFIX):
            continue
        for reference in extract_references(path, payload):
            target = resolve_reference(path, reference)
            if target is None or not target.startswith(KIT_CONTRACT_PREFIX):
                continue
            kit_rel = target.removeprefix(KIT_CONTRACT_PREFIX)
            if kit_rel in shipped_index:
                known.add(kit_rel)
            else:
                unknown.add(target)

    carried: dict[str, bytes] = {}
    if known:
        try:
            carried = kit_assets(sorted(known), root=base)
        except KitRefused as refused:
            problems.append(f"kit assets could not be carried ({refused.reason}): {refused.detail}")
    return KitCarryResult(
        carried=carried,
        referenced_known=tuple(sorted(known)),
        unknown_kit_paths=tuple(sorted(unknown)),
        kit_problems=tuple(dict.fromkeys(problems)),
    )


def carry_kit_assets(
    draft_assets: Mapping[str, bytes], *, root: Path | None = None
) -> tuple[dict[str, bytes], KitCarryResult]:
    """Return draft assets merged with carried kit files without masking tampered ``kit/`` files."""

    result = resolve_kit_references(draft_assets, root=root)
    merged = {**result.carried, **draft_assets}
    return merged, result
