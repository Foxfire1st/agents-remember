"""Role-scoped filtering of the shared ``core/every-role.md`` instruction block.

Parses the shared Markdown document once into a heading tree of universal and
role-prefixed entries while keeping fenced code blocks intact, filters entries
for one canonical target role, prunes empty headings, and re-renders the
surviving sections with their original tight or blank-line entry separation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

from agents_remember.models.role_identity import canonical_role

_RECOGNIZED_ROLE_LABELS: Final[frozenset[str]] = frozenset(
    {
        "architect",
        "orchestrator",
        "manager",
        "worker",
        "reviewer",
        "curator",
        "investigator",
        "bootstrap",
        "system-specialist",
    }
)
_HEADING_RE: Final[re.Pattern[str]] = re.compile(r"^(#{1,6})[ \t]+")
_FENCE_OPEN_RE: Final[re.Pattern[str]] = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_FENCE_CLOSE_RE: Final[re.Pattern[str]] = re.compile(r"^ {0,3}(`+|~+)[ \t]*$")


@dataclass(slots=True)
class _Entry:
    lines: list[str]
    target_roles: frozenset[str] | None
    group_id: int


@dataclass(slots=True)
class _Section:
    level: int
    heading: str | None
    entries: list[_Entry] = field(default_factory=list)
    children: list[_Section] = field(default_factory=list)


@dataclass(slots=True)
class _ParseState:
    stack: list[_Section]
    current_entry: _Entry | None = None
    active_fence: tuple[str, int] | None = None
    group_id: int = 0


def _opening_fence(line: str) -> tuple[str, int] | None:
    match = _FENCE_OPEN_RE.match(line)
    if match is None:
        return None
    marker = match.group(1)
    fence_char = marker[0]
    if fence_char == "`" and "`" in line[match.end() :]:
        return None
    return fence_char, len(marker)


def _is_closing_fence(line: str, fence: tuple[str, int]) -> bool:
    match = _FENCE_CLOSE_RE.match(line)
    if match is None:
        return False
    marker = match.group(1)
    fence_char, fence_len = fence
    return marker[0] == fence_char and len(marker) >= fence_len


def _parse_role_prefix(line: str) -> frozenset[str] | None:
    prefix, separator, _rest = line.partition(":")
    if not separator:
        return None
    tokens = [token.strip().lower() for token in prefix.split(",")]
    if not tokens or any(token not in _RECOGNIZED_ROLE_LABELS for token in tokens):
        return None
    return frozenset(canonical_role(token) for token in tokens)


def _push_heading(state: _ParseState, line: str, level: int) -> None:
    state.current_entry = None
    state.group_id += 1
    while state.stack[-1].level >= level:
        state.stack.pop()
    section = _Section(level=level, heading=line)
    state.stack[-1].children.append(section)
    state.stack.append(section)


def _append_body_line(state: _ParseState, line: str) -> None:
    fence = _opening_fence(line)
    if fence is not None:
        state.active_fence = fence
        target_roles = None
    else:
        target_roles = _parse_role_prefix(line)

    if target_roles is not None or state.current_entry is None:
        entry = _Entry(lines=[line], target_roles=target_roles, group_id=state.group_id)
        state.stack[-1].entries.append(entry)
        state.current_entry = entry
        return
    state.current_entry.lines.append(line)


def _consume_line(state: _ParseState, line: str) -> None:
    if state.active_fence is not None and state.current_entry is not None:
        state.current_entry.lines.append(line)
        if _is_closing_fence(line, state.active_fence):
            state.active_fence = None
        return

    if not line.strip():
        state.current_entry = None
        state.group_id += 1
        return

    heading = _HEADING_RE.match(line)
    if heading is not None:
        _push_heading(state, line, len(heading.group(1)))
        return

    _append_body_line(state, line)


def _parse_sections(lines: list[str]) -> _Section:
    root = _Section(level=0, heading=None)
    state = _ParseState(stack=[root])
    for line in lines:
        _consume_line(state, line)
    return root


def _filter_section(section: _Section, target_role: str) -> _Section | None:
    kept_entries = [
        entry
        for entry in section.entries
        if entry.target_roles is None or target_role in entry.target_roles
    ]
    kept_children = [
        filtered
        for child in section.children
        if (filtered := _filter_section(child, target_role)) is not None
    ]
    if section.level >= 1 and not kept_entries and not kept_children:
        return None
    return _Section(
        level=section.level,
        heading=section.heading,
        entries=kept_entries,
        children=kept_children,
    )


def _render_entries(entries: list[_Entry]) -> list[str]:
    groups: list[list[str]] = []
    previous_group_id: int | None = None
    for entry in entries:
        text = "\n".join(entry.lines)
        if previous_group_id == entry.group_id and groups:
            groups[-1].append(text)
        else:
            groups.append([text])
            previous_group_id = entry.group_id
    return ["\n".join(group) for group in groups]


def _render_section(section: _Section) -> str:
    parts: list[str] = []
    if section.heading is not None:
        parts.append(section.heading)
    parts.extend(_render_entries(section.entries))
    for child in section.children:
        rendered_child = _render_section(child)
        if rendered_child:
            parts.append(rendered_child)
    return "\n\n".join(parts)


def filter_shared_block(text: str, role: str | None) -> str:
    """Filter a Markdown shared-instructions block down to ``role``'s audience."""

    if role is None:
        return text
    if not text:
        return ""
    target_role = canonical_role(role.strip().lower())
    root = _parse_sections(text.splitlines())
    filtered = _filter_section(root, target_role)
    if filtered is None:
        return ""
    rendered = _render_section(filtered)
    if not rendered:
        return ""
    return f"{rendered}\n" if text.endswith("\n") else rendered


__all__ = ["filter_shared_block"]
