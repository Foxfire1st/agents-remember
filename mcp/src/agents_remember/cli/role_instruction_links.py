"""Bind Markdown destinations at delivery, keeping compiler content immutable."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from agents_remember.errors import CapsuleDeliveryError

_REFERENCE = re.compile(r"(?m)^ {0,3}\[(?:\\.|[^\]\\\n])+\]:[ \t]*(?:\n[ \t]*)?")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_ESCAPE = re.compile(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\]\\^_`{|}~])")


def _code_mask(text: str) -> str:
    """Hide code without moving offsets into the original Markdown."""
    masked = list(text)
    fence: str | None = None
    offset = 0
    for line in text.splitlines(keepends=True):
        match = _FENCE.match(line)
        marker = match[1] if match else ""
        if fence or marker or line.startswith(("    ", "\t")):
            masked[offset : offset + len(line)] = " " * len(line)
            if fence:
                if (
                    match is not None
                    and marker.startswith(fence[0])
                    and len(marker) >= len(fence)
                    and not line[match.end() :].strip()
                ):
                    fence = None
            elif marker:
                fence = marker
        offset += len(line)
    return _mask_inline_code(masked)


def _mask_inline_code(masked: list[str]) -> str:
    result = "".join(masked)
    runs = list(re.finditer(r"`+", result))
    following: dict[int, int] = {}
    closing: dict[int, int] = {}
    for run in reversed(runs):
        length = run.end() - run.start()
        if length in following:
            closing[run.start()] = following[length]
        following[length] = run.end()
    cursor = 0
    for run in runs:
        if run.start() < cursor or _escaped(result, run.start()):
            continue
        if run.start() in closing:
            cursor = closing[run.start()]
            masked[run.start() : cursor] = " " * (cursor - run.start())
    return "".join(masked)


def _escaped(text: str, index: int) -> bool:
    start = index
    while start and text[start - 1] == "\\":
        start -= 1
    return (index - start) % 2 == 1


def _inline_starts(text: str) -> list[int]:
    labels: list[int] = []
    starts: list[int] = []
    for index, char in enumerate(text):
        if char not in "[]" or _escaped(text, index):
            continue
        if char == "[":
            labels.append(index)
        elif labels:
            labels.pop()
            if text[index + 1 : index + 2] == "(":
                starts.append(index + 2)
    return starts


def _destination(text: str, start: int) -> tuple[int, int] | None:
    while start < len(text) and text[start].isspace():
        start += 1
    if start == len(text):
        return None
    if text[start] == "<":
        return _angle_destination(text, start + 1)
    cursor, depth = start, 0
    while cursor < len(text):
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char.isspace() or (char == ")" and depth == 0):
            return start, cursor
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        cursor += 1
    return None


def _angle_destination(text: str, start: int) -> tuple[int, int] | None:
    cursor = start
    while cursor < len(text):
        if text[cursor] == "\\":
            cursor += 2
            continue
        if text[cursor] == ">":
            return start, cursor
        if text[cursor] == "\n":
            return None
        cursor += 1
    return None


def relative_destinations(text: str) -> list[tuple[int, int]]:
    """Find inline/image and reference-definition paths outside code."""
    masked = _code_mask(text)
    starts = [match.end() for match in _REFERENCE.finditer(masked)]
    starts.extend(_inline_starts(masked))
    result = []
    for start in starts:
        span = _destination(masked, start)
        if span is None:
            continue
        destination = _ESCAPE.sub(r"\1", text[slice(*span)])
        if destination and not (
            destination.startswith(("/", "{{detail:")) or _SCHEME.match(destination)
        ):
            result.append(span)
    return sorted(set(result))


def bind_markdown_links(text: str, source: Path, targets: Mapping[Path, Path] | None = None) -> str:
    """Resolve each local destination from its original source, not the launch file."""
    for start, end in reversed(relative_destinations(text)):
        destination = _ESCAPE.sub(r"\1", text[start:end])
        parts = urlsplit(destination)
        target = (source.parent / unquote(parts.path)).resolve() if parts.path else source.resolve()
        if targets is not None:
            target = targets.get(target, target)
        bound = quote(target.as_posix(), safe="/:@")
        if parts.query:
            bound += "?" + parts.query
        if parts.fragment:
            bound += "#" + parts.fragment
        text = text[:start] + bound + text[end:]
    return text


def require_no_unbound_links(text: str) -> None:
    if relative_destinations(text):
        raise CapsuleDeliveryError("relative instruction links require complete source origins")
