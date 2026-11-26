"""First-message and compiled-capsule wire-token ceilings with offline tiktoken helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Final
from urllib.parse import SplitResult, quote, unquote, urlsplit

import tiktoken

from agents_remember.application.role_capsules.shared_block import filter_shared_block
from agents_remember.models.role_capsules.types import CapsuleCompilationResult, CapsuleRoleSeat
from agents_remember.models.tokens import VENDORED_ENCODING_NAME, vendored_vocabulary_cache

WIRE_TOKEN_CEILINGS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "investigator/orientation": 2_500,
        "bootstrap/bootstrap": 2_500,
        "worker/implementation": 2_500,
        "reviewer/review": 3_000,
        "curator/curation": 3_000,
        "orchestrator/coordination": 3_800,
        "manager/coordination": 3_800,
        "architect/planning": 3_800,
    }
)

COMPILED_CAPSULE_TOKEN_CEILINGS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "architect/orientation": 2_600,
        "architect/planning": 2_600,
        "architect/review": 2_600,
        "architect/coordination": 2_600,
        "architect/authorized-closeout": 2_600,
        "architect/recovery": 2_600,
        "orchestrator/orientation": 2_600,
        "orchestrator/planning": 2_600,
        "orchestrator/review": 2_600,
        "orchestrator/coordination": 2_600,
        "orchestrator/authorized-closeout": 2_600,
        "orchestrator/recovery": 2_600,
        "manager/orientation": 2_600,
        "manager/review": 2_600,
        "manager/curation": 2_600,
        "manager/coordination": 2_600,
        "manager/authorized-closeout": 2_600,
        "manager/recovery": 2_600,
        "worker/orientation": 1_500,
        "worker/implementation": 1_500,
        "worker/recovery": 1_500,
        "curator/orientation": 2_000,
        "curator/curation": 2_000,
        "curator/recovery": 2_000,
        "reviewer/orientation": 2_000,
        "reviewer/review": 2_000,
        "reviewer/recovery": 2_000,
        "investigator/orientation": 1_500,
        "investigator/recovery": 1_500,
        "bootstrap/orientation": 1_500,
        "bootstrap/recovery": 1_500,
        "bootstrap/bootstrap": 1_500,
        "launcher/orientation": 1_500,
        "launcher/coordination": 1_500,
    }
)

_SHIPPED_SKILL_URI_PREFIX: Final[str] = "skill://agents-remember/skills/l-01-agent-lifecycles/"
_REFERENCE: Final[re.Pattern[str]] = re.compile(
    r"(?m)^ {0,3}\[(?:\\.|[^\]\\\n])+\]:[ \t]*(?:\n[ \t]*)?"
)
_FENCE: Final[re.Pattern[str]] = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_SCHEME: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_ESCAPE: Final[re.Pattern[str]] = re.compile(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\]\\^_`{|}~])")


@lru_cache(maxsize=1)
def _wire_encoding() -> tiktoken.Encoding:
    with vendored_vocabulary_cache(VENDORED_ENCODING_NAME):
        return tiktoken.get_encoding(VENDORED_ENCODING_NAME)


def measure_wire_tokens(
    message: str,
    *,
    replacements: Sequence[tuple[str, str]] = (),
) -> int:
    """Count plain-text wire tokens after applying ordered ``(old, new)`` normalizations."""

    normalized = message
    for old, new in replacements:
        if old:
            normalized = normalized.replace(old, new)
    return len(_wire_encoding().encode(normalized, disallowed_special=()))


def evaluate_wire_token_ceilings(
    measurements: Mapping[str, int],
    ceilings: Mapping[str, int] = WIRE_TOKEN_CEILINGS,
) -> list[str]:
    """Return failure descriptions for any missing ceiling or over-budget measurement."""

    failures: list[str] = []
    for key, tokens in measurements.items():
        ceiling = ceilings.get(key)
        if ceiling is None:
            failures.append(f"{key}: no wire token ceiling configured (measured {tokens} tokens)")
        elif tokens > ceiling:
            failures.append(f"{key}: measured {tokens} tokens exceeds ceiling {ceiling}")
    return failures


def _escaped(text: str, index: int) -> bool:
    start = index
    while start and text[start - 1] == "\\":
        start -= 1
    return (index - start) % 2 == 1


def _is_closing_fence(line: str, fence: str, match: re.Match[str] | None) -> bool:
    if match is None:
        return False
    marker = match[1]
    return (
        marker.startswith(fence[0])
        and len(marker) >= len(fence)
        and not line[match.end() :].strip()
    )


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


def _mask_code(text: str) -> str:
    masked = list(text)
    fence: str | None = None
    offset = 0
    for line in text.splitlines(keepends=True):
        match = _FENCE.match(line)
        marker = match[1] if match else ""
        if fence or marker or line.startswith(("    ", "\t")):
            masked[offset : offset + len(line)] = " " * len(line)
            if fence and _is_closing_fence(line, fence, match):
                fence = None
            elif not fence and marker:
                fence = marker
        offset += len(line)
    return _mask_inline_code(masked)


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
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        cursor += 1
    return None


def _link_destinations(text: str) -> list[tuple[int, int]]:
    masked = _mask_code(text)
    starts = [match.end() for match in _REFERENCE.finditer(masked)]
    starts.extend(_inline_starts(masked))
    result = [
        span
        for start in starts
        if (span := _destination(masked, start)) is not None and span[0] < span[1]
    ]
    return sorted(set(result))


def _reattach_query_fragment(bound: str, parts: SplitResult) -> str:
    if parts.query:
        bound += "?" + parts.query
    if parts.fragment:
        bound += "#" + parts.fragment
    return bound


def _compact_skill_uri_links(text: str) -> str:
    for start, end in reversed(_link_destinations(text)):
        destination = _ESCAPE.sub(r"\1", text[start:end])
        if not destination.startswith(_SHIPPED_SKILL_URI_PREFIX):
            continue
        parts = urlsplit(destination[len(_SHIPPED_SKILL_URI_PREFIX) :])
        quoted_rel = quote(unquote(parts.path).lstrip("/"), safe="/:@")
        bound = _reattach_query_fragment(f"skill/{quoted_rel}", parts)
        text = text[:start] + bound + text[end:]
    return text


def _format_compact_target(
    target: Path, resolved_corpus: Path, detail_targets: Mapping[Path, str]
) -> str:
    if target in detail_targets:
        return "detail/" + quote(detail_targets[target], safe="/:@")
    if target.is_relative_to(resolved_corpus):
        return "skill/" + quote(target.relative_to(resolved_corpus).as_posix(), safe="/:@")
    return quote(target.as_posix(), safe="/:@")


def _compact_block_links(
    text: str,
    source: Path,
    resolved_corpus: Path,
    detail_targets: Mapping[Path, str],
) -> str:
    for start, end in reversed(_link_destinations(text)):
        destination = _ESCAPE.sub(r"\1", text[start:end])
        if (
            not destination
            or destination.startswith(("/", "{{detail:"))
            or _SCHEME.match(destination)
        ):
            continue
        parts = urlsplit(destination)
        target = (source.parent / unquote(parts.path)).resolve() if parts.path else source.resolve()
        bound = _format_compact_target(target, resolved_corpus, detail_targets)
        text = text[:start] + _reattach_query_fragment(bound, parts) + text[end:]
    return _compact_skill_uri_links(text)


def _shared_block_source(corpus_root: Path, every_role: str | None) -> str:
    if every_role is not None:
        return every_role
    shared_path = corpus_root / "core" / "every-role.md"
    return shared_path.read_text(encoding="utf-8") if shared_path.is_file() else ""


def measure_compiled_capsule_tokens(
    result: CapsuleCompilationResult,
    *,
    corpus_root: Path,
    every_role: str | None = None,
) -> int:
    """Count o200k_base tokens of the compact-bound shared block plus compiled instructions."""

    resolved_corpus = corpus_root.resolve()
    seat = result.capsule.binding.admitted.seat
    role = seat.role if isinstance(seat, CapsuleRoleSeat) else None
    bound_shared = ""
    if role is not None:
        shared_source = _shared_block_source(resolved_corpus, every_role)
        if shared_source:
            bound_shared = _compact_skill_uri_links(filter_shared_block(shared_source, role))
    detail_targets = {
        (resolved_corpus / ref.source_path).resolve(): ref.source_path
        for ref in result.capsule.detail_references
    }
    bound_instructions = "".join(
        _compact_block_links(
            block.content, resolved_corpus / block.source_path, resolved_corpus, detail_targets
        )
        for block in result.capsule.instructions
    )
    for ref in result.capsule.detail_references:
        replacement = "detail/" + quote(ref.source_path, safe="/:@")
        bound_instructions = bound_instructions.replace(
            "{{detail:" + ref.source_path + "}}", replacement
        )
    combined = f"{bound_shared}\n\n{bound_instructions}" if bound_shared else bound_instructions
    return measure_wire_tokens(combined)


__all__ = [
    "COMPILED_CAPSULE_TOKEN_CEILINGS",
    "WIRE_TOKEN_CEILINGS",
    "evaluate_wire_token_ceilings",
    "measure_compiled_capsule_tokens",
    "measure_wire_tokens",
]
