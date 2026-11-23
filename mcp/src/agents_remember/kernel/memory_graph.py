"""Account for data reachable from a retained Python value."""

from __future__ import annotations

from collections.abc import Iterator
from gc import get_referents
from itertools import chain
from sys import getsizeof
from types import BuiltinFunctionType, FunctionType, ModuleType

_GLOBAL_REFERENTS = (type, ModuleType, FunctionType, BuiltinFunctionType)


def _children(value: object) -> Iterator[object]:
    if isinstance(value, dict):
        return chain.from_iterable(value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return iter(value)
    return iter(get_referents(value))


def retained_graph_bytes(value: object, *, ceiling: int) -> int:
    """Count reachable Python allocations once, stopping once they exceed the ceiling.

    Shared interpreter definitions do not belong to the retained data. Native buffers outside
    Python objects are outside this accounting. Keep only one iterator per active branch so a
    wide container does not add a second list of its references.
    """

    seen: set[int] = set()
    pending: list[Iterator[object]] = [iter((value,))]
    weight = 0
    while pending:
        try:
            child = next(pending[-1])
        except StopIteration:
            pending.pop()
            continue
        identity = id(child)
        if identity in seen or isinstance(child, _GLOBAL_REFERENTS):
            continue
        seen.add(identity)
        weight += getsizeof(child)
        if weight > ceiling:
            break
        pending.append(_children(child))
    return weight
