"""Track the lawful interpretations of Lean scope openings and exits."""

from dataclasses import dataclass, replace

from agents_remember.memory_quality.style.citations.lean_names import LeanName


@dataclass(frozen=True)
class _Frame:
    head: str
    indent: int
    parts: tuple[str, ...]
    member_indent: int | None = None

    @property
    def named(self) -> bool:
        return bool(self.parts and self.parts[-1] != "«»")

    def at_member_indent(self, indent: int) -> bool:
        return self.head == "mutual" and (
            indent == self.member_indent or (self.member_indent is None and indent > self.indent)
        )


@dataclass(frozen=True, eq=False)
class _Stack:
    frame: _Frame
    below: frozenset["_Stack"]
    namespaces: frozenset[tuple[str, ...]]


_ROOT = _Stack(_Frame("", 0, ()), frozenset(), frozenset({()}))


def _push(frame: _Frame, below: frozenset[_Stack]) -> _Stack:
    namespaces = frozenset(parts for stack in below for parts in stack.namespaces)
    if frame.head == "namespace":
        namespaces = frozenset((*parts, *frame.parts) for parts in namespaces)
    return _Stack(frame, below, namespaces)


def _pop(stack: _Stack, count: int) -> frozenset[_Stack]:
    parts = stack.frame.parts[:-count]
    if parts:
        return frozenset({_push(replace(stack.frame, parts=parts), stack.below)})
    return stack.below


def _same_part(active: str, given: str) -> bool:
    return active != "«»" and (
        active.removeprefix("«").removesuffix("»") == given.removeprefix("«").removesuffix("»")
    )


def _named_exits(stacks: frozenset[_Stack], name: LeanName) -> set[_Stack]:
    # Different tentative openings share their tails. Walk each tail at a
    # given name offset once, even when several interpretations reach it.
    pending = [(stack, len(name.parts)) for stack in stacks]
    checked: set[tuple[_Stack, int]] = set()
    exits: set[_Stack] = set()
    while pending:
        stack, remaining = pending.pop()
        state = (stack, remaining)
        if state in checked:
            continue
        checked.add(state)
        parts = stack.frame.parts
        count = min(remaining, len(parts))
        if not count or not all(
            _same_part(active, given)
            for active, given in zip(
                parts[-count:], name.parts[remaining - count : remaining], strict=True
            )
        ):
            continue
        remaining -= count
        if remaining:
            pending.extend((tail, remaining) for tail in stack.below)
        else:
            exits.update(_pop(stack, count))
    return exits


def _merge(stacks: set[_Stack]) -> frozenset[_Stack]:
    grouped: dict[_Frame, list[_Stack]] = {}
    for stack in stacks:
        grouped.setdefault(stack.frame, []).append(stack)
    return frozenset(
        group[0]
        if len(group) == 1
        else _push(frame, frozenset(tail for stack in group for tail in stack.below))
        for frame, group in grouped.items()
    )


class Scopes:
    """Retain alternative scope frames with shared lower stacks."""

    def __init__(self) -> None:
        self._stacks = frozenset({_ROOT})

    def open(self, head: str, indent: int, name: LeanName | None, *, tentative: bool) -> None:
        parts = name.parts if name is not None else ()
        opened = {_push(_Frame(head, indent, parts), self._stacks)}
        if parts and tentative:
            opened.add(_push(_Frame(head, indent, ()), self._stacks))
        self._stacks = frozenset(opened)

    def namespace_parts(self) -> frozenset[tuple[str, ...]]:
        return frozenset(parts for stack in self._stacks for parts in stack.namespaces)

    def at_member_indent(self, indent: int) -> bool:
        return any(stack.frame.at_member_indent(indent) for stack in self._stacks)

    def record_member_indent(self, indent: int) -> None:
        self._stacks = frozenset(
            replace(stack, frame=replace(stack.frame, member_indent=indent))
            if stack.frame.member_indent is None and stack.frame.at_member_indent(indent)
            else stack
            for stack in self._stacks
        )

    def may_have_end_label(self) -> bool:
        return any(stack.frame.named for stack in self._stacks)

    def close(self, name: LeanName | None, *, may_be_command: bool) -> bool:
        """Keep each stack reached by a lawful bare or matching named end."""

        exits = _named_exits(self._stacks, name) if name is not None else set()
        matched_name = bool(exits)
        if name is None or may_be_command:
            exits.update(
                tail
                for stack in self._stacks
                if stack.below and not stack.frame.named
                for tail in _pop(stack, 1)
            )
        if exits:
            self._stacks = _merge(exits)
        return matched_name
