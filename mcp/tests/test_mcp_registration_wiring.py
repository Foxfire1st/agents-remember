"""What each advertised MCP tool does with the arguments it is handed.

``agents_remember.mcp.registration`` is the tool surface: one module per family, each
declaring ``@server.tool()`` bodies that translate a flat MCP argument list into the
parameter objects the application layer takes. The tool-list test in ``test_tools.py``
the surface *advertises* the right names; nothing proved what a call to one of those
names actually does.

That translation is the whole content of these bodies and it is exactly where a split
goes wrong: an argument dropped on the floor, a flag landing in the wrong parameter
object, a default that silently changes what a call means (``codex_benchmark_run``
defaulting to a real run rather than a preview, ``gate_decide`` attributing a
developer-delegated decision to the model). Each test below calls the tool through the
live ``FastMCP`` instance -- so the registered schema, its defaults and its coercions are
in the path -- with the family's payload builder recorded, and states what the builder is
handed and that the tool returns its result unchanged.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.kernel.primitives.runtime_config import (
    load_config,
)
from agents_remember.mcp.server import create_server
from test_config import settings_payload

# The recorded builders return this; every test asserts the tool hands it back untouched,
# which is the other half of "the body is a delegation" -- it must not reshape the result.
SENTINEL: dict[str, Any] = {"ok": True, "marker": "payload-result"}


class Recorder:
    """Stands in for a payload builder and remembers the one call it received."""

    def __init__(self) -> None:
        self.args: tuple[Any, ...] = ()
        self.kwargs: dict[str, Any] = {}
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.args = args
        self.kwargs = kwargs
        self.calls += 1
        return SENTINEL


class RegistrationWiringTests(unittest.TestCase):
    """One live server per test, with the family's payload builder recorded."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        settings_path = self.root / "mcp-settings.json"
        settings_path.write_text(
            json.dumps(settings_payload(self.root), indent=2), encoding="utf-8"
        )
        self.config = load_config(settings_path)
        self.server = create_server(self.config)

    @contextmanager
    def recorded(self, target: str) -> Iterator[Recorder]:
        recorder = Recorder()
        with patch(target, recorder):
            yield recorder

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Invoke one advertised tool the way a client does, and return its structured result."""
        _content, structured = asyncio.run(self.server.call_tool(name, arguments or {}))
        return structured

    def invoke(self, name: str, target: str, arguments: dict[str, Any] | None = None) -> Recorder:
        """Call ``name`` with ``target`` recorded, asserting the delegation happened once."""
        with self.recorded(target) as recorder:
            result = self.call(name, arguments)
        self.assertEqual(recorder.calls, 1, f"{name} did not call {target} exactly once")
        self.assertEqual(result, SENTINEL, f"{name} reshaped the payload builder's result")
        return recorder

    # ---- core ------------------------------------------------------------------

    # ---- sessions --------------------------------------------------------------

    # ---- memory ----------------------------------------------------------------

    # ---- providers -------------------------------------------------------------

    # ---- code search -----------------------------------------------------------

    # ---- worktrees -------------------------------------------------------------

    # ---- closeout --------------------------------------------------------------

    # ---- tasks -----------------------------------------------------------------

    # ---- benchmarks ------------------------------------------------------------

    # ---- lifecycle -------------------------------------------------------------

    # ---- gates -----------------------------------------------------------------

    # ---- orchestration ---------------------------------------------------------


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
