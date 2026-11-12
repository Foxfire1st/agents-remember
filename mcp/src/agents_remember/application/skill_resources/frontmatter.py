"""Read a ``SKILL.md`` YAML frontmatter block into the JSON object the SEP requires.

SEP-2640 §Enumeration via ``skills/list`` requires an entry's ``frontmatter`` to be
"a verbatim copy of the skill's ``SKILL.md`` YAML frontmatter, rendered as JSON …
every field the author wrote, not a curated subset", with ``license``, ``metadata``
and fields added by future revisions of the Agent Skills specification passing
through unchanged. A reader that understood only ``name:`` and ``description:``
would silently drop the rest, so this module implements the YAML subset a
frontmatter block is written in: block mappings, block sequences, nested
indentation, flow sequences and mappings, quoted and plain scalars, comments, and
block scalars.

It is deliberately a reader, not a general YAML engine. It refuses the constructs it
does not implement — anchors, aliases, tags, explicit keys and merge keys — rather
than guessing, because a silently mistranslated frontmatter is exactly the
"curated subset" the specification forbids.

Reading is total over its input otherwise: a file without frontmatter, or with
frontmatter that does not declare a usable ``name``, is reported as an unreadable
skill by the caller instead of being served under a guessed identity. The skill name
is never inferred from the directory name, because the specification makes the
declared name, not the path, the skill's identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_FENCE = "---"

#: Value prefixes for YAML constructs this reader does not implement.
_UNSUPPORTED_PREFIXES = ("&", "*", "!", "<<:")


class SkillFrontmatterError(ValueError):
    """A skill root file does not carry the frontmatter the specification requires."""


@dataclass(frozen=True, slots=True)
class SkillFrontmatter:
    """The declared fields of one ``SKILL.md``: verbatim, plus the two required ones."""

    name: str
    description: str
    fields: dict[str, Any]

    def get(self, key: str) -> Any:
        return self.fields.get(key)


def parse_skill_frontmatter(text: str) -> SkillFrontmatter:
    """Parse the leading frontmatter block, refusing a skill that has no usable name.

    An empty ``description`` is refused too: the description is the one field a host
    surfaces before it loads a body, so a skill that cannot describe itself cannot be
    discovered without loading it, which is the property the discovery registry
    exists to keep.
    """

    block = _frontmatter_block(text)
    fields = _MappingReader(block.splitlines()).read()
    name = str(fields.get("name", "")).strip()
    if not name:
        raise SkillFrontmatterError("skill root file does not declare a 'name' in its frontmatter")
    description = str(fields.get("description", "")).strip()
    if not description:
        raise SkillFrontmatterError(
            f"skill {name!r} does not declare a 'description' in its frontmatter"
        )
    return SkillFrontmatter(name=name, description=description, fields=fields)


def _frontmatter_block(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        raise SkillFrontmatterError("skill root file does not open with a YAML frontmatter block")
    for index in range(1, len(lines)):
        if lines[index].strip() == _FENCE:
            return "\n".join(lines[1:index])
    raise SkillFrontmatterError("skill root file frontmatter block is never closed")


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _content(line: str) -> str:
    """The line without its indentation and without a trailing comment when plain."""

    stripped = line.strip()
    if stripped.startswith(('"', "'")):
        return stripped
    return stripped.split(" #", 1)[0].strip()


class _MappingReader:
    """One block mapping, plus the nested values its entries open."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self._index = 0

    def read(self, indent: int = 0) -> dict[str, Any]:
        mapping: dict[str, Any] = {}
        while self._index < len(self._lines):
            line = self._lines[self._index]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                self._index += 1
                continue
            current = _indent_of(line)
            if current < indent:
                break
            if current > indent:
                raise SkillFrontmatterError(f"frontmatter is indented unexpectedly: {line!r}")
            key, separator, value = stripped.partition(":")
            if not separator:
                raise SkillFrontmatterError(f"frontmatter line is not a mapping entry: {line!r}")
            key = _unquote(key.strip())
            _reject_unsupported(key, value)
            self._index += 1
            mapping[key] = self._value(indent, value.strip())
        return mapping

    def _value(self, indent: int, inline: str) -> Any:
        if inline.startswith(("|", ">")):
            return self._block_scalar(indent, folded=inline.startswith(">"))
        if inline:
            return _scalar(inline)
        nested = self._peek()
        if nested is None or _indent_of(nested) <= indent:
            return None
        if nested.strip().startswith("- "):
            return self._sequence(_indent_of(nested))
        return self.read(_indent_of(nested))

    def _sequence(self, indent: int) -> list[Any]:
        items: list[Any] = []
        while self._index < len(self._lines):
            line = self._lines[self._index]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                self._index += 1
                continue
            if _indent_of(line) != indent or not stripped.startswith("- "):
                break
            self._index += 1
            items.append(_scalar(stripped[2:].strip()))
        return items

    def _block_scalar(self, indent: int, *, folded: bool) -> str:
        collected: list[str] = []
        while self._index < len(self._lines):
            line = self._lines[self._index]
            if line.strip() and _indent_of(line) <= indent:
                break
            collected.append(line)
            self._index += 1
        body = [line[indent + 2 :] if len(line) > indent + 2 else "" for line in collected]
        parts = [part.strip() for part in body] if folded else body
        return (" " if folded else "\n").join(parts).strip("\n")

    def _peek(self) -> str | None:
        index = self._index
        while index < len(self._lines):
            stripped = self._lines[index].strip()
            if stripped and not stripped.startswith("#"):
                return self._lines[index]
            index += 1
        return None


def _reject_unsupported(key: str, value: str) -> None:
    if key.startswith("<<"):
        raise SkillFrontmatterError("frontmatter merge keys are not supported")
    stripped = value.strip()
    for prefix in _UNSUPPORTED_PREFIXES:
        if stripped.startswith(prefix):
            raise SkillFrontmatterError(
                f"frontmatter value {stripped!r} uses a YAML construct this reader does not "
                "implement; anchors, aliases, tags and merge keys are refused rather than "
                "guessed, because a mistranslated frontmatter is not a verbatim copy"
            )


def _scalar(raw: str) -> Any:
    value = _content(raw)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return _unquote(value)
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [] if not inner else [_scalar(part) for part in _split_flow(inner)]
    if value.startswith("{") and value.endswith("}"):
        return _flow_mapping(value[1:-1].strip())
    lowered = value.lower()
    if lowered in {"null", "~"}:
        return None
    if lowered in {"true", "false"}:
        return lowered == "true"
    return _unquote(value)


def _flow_mapping(inner: str) -> dict[str, Any]:
    if not inner:
        return {}
    pairs: dict[str, Any] = {}
    for part in _split_flow(inner):
        key, separator, item = part.partition(":")
        if not separator:
            raise SkillFrontmatterError(f"flow mapping entry is not a pair: {part!r}")
        pairs[_unquote(key.strip())] = _scalar(item.strip())
    return pairs


def _split_flow(inner: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current = ""
    quote: str | None = None
    for character in inner:
        if quote is not None:
            current += character
            if character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
            current += character
            continue
        if character in "[{":
            depth += 1
        elif character in "]}":
            depth -= 1
        if character == "," and depth == 0:
            parts.append(current.strip())
            current = ""
            continue
        current += character
    if current.strip():
        parts.append(current.strip())
    return parts


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


__all__ = ["SkillFrontmatter", "SkillFrontmatterError", "parse_skill_frontmatter"]
