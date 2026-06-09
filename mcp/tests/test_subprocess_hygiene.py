"""Package-wide guard: no subprocess may inherit the MCP stdio protocol pipes.

Under the stdio transport the server's stdin/stdout ARE the JSON-RPC stream.
A child process that inherits stdin can wedge a tool call (GitHub #49 —
proven by test_mcp_stdio_transport.py); a child that inherits stdout writes
its output into the protocol stream. Every subprocess call site must
therefore say what happens to stdin — `stdin=...`, `input=...`, or an
explicit `**kwargs` spread that carries one of them.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "agents_remember"

SPAWN_ATTRS = {"run", "Popen", "check_output", "check_call", "call"}


def _is_subprocess_spawn(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr in SPAWN_ATTRS
        and isinstance(func.value, ast.Name)
        and func.value.id == "subprocess"
    )


def _handles_stdin(node: ast.Call) -> bool:
    for keyword in node.keywords:
        if keyword.arg in {"stdin", "input"}:
            return True
        if keyword.arg is None:
            # **kwargs spread: accepted as a deliberate choice; the call site
            # owns building stdin/input into the dict (e.g. command_runner).
            return True
    return False


class SubprocessHygieneTests(unittest.TestCase):
    def test_every_subprocess_call_site_handles_stdin(self) -> None:
        offenders: list[str] = []
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            if "package_data" in path.parts:
                continue  # Docker/runtime assets run outside the MCP process.
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and _is_subprocess_spawn(node)
                    and not _handles_stdin(node)
                ):
                    rel = path.relative_to(PACKAGE_ROOT).as_posix()
                    offenders.append(f"{rel}:{node.lineno}")
        self.assertEqual(
            offenders,
            [],
            msg=(
                "subprocess call sites inherit the parent's stdin — under the "
                "stdio MCP transport that is the protocol pipe (see GitHub #49). "
                "Pass stdin=subprocess.DEVNULL or input=...: "
                + ", ".join(offenders)
            ),
        )


if __name__ == "__main__":
    unittest.main()
