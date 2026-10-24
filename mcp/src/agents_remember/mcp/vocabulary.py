"""The words registration needs before any implementation exists (MIK-R204 @ v1 rule 1).

A parameter default is evaluated when a tool is *declared*, not when it is called, so anything used as one
has to be importable without dragging an implementation package in behind it. These live here, outside
`agents_remember.mcp.tools`, so a registration family can declare its surface without loading every payload
builder -- and so the import guard can hold `agents_remember.mcp.tools` unloaded at discovery.
"""

from __future__ import annotations

from typing import Any

#: The sentinel a removed argument is defaulted to. A call that passes nothing gets this value, and the
#: tool refuses by name rather than pretending the argument still exists (MIK-R109's removed-argument rule).
REMOVED_ARGUMENT: Any = "removed"

#: The earlier, private spelling, kept as an alias so the two cannot drift while call sites move over.
_REMOVED_ARGUMENT = REMOVED_ARGUMENT

__all__ = ["REMOVED_ARGUMENT", "_REMOVED_ARGUMENT"]
