"""Compact the JSON text content block emitted by FastMCP tool results.

FastMCP serializes the unstructured ``TextContent`` mirror of a tool result with
a hardcoded ``indent=2`` (see ``pydantic_core.to_json(result, fallback=str,
indent=2)`` inside ``mcp.server.fastmcp.utilities.func_metadata._convert_to_content``).
There is no public configuration hook for that indent in the supported ``mcp``
range, so this module monkeypatches the internal converter to re-serialize JSON
text blocks without the pretty-printing whitespace.

IMPORTANT: this patches a FastMCP internal. It must be re-validated on any ``mcp``
upgrade, which is why ``pyproject.toml`` keeps the ``mcp<2`` upper bound. The shim
only rewrites blocks whose text round-trips through ``json.loads``; anything else
(plain strings, already-minified payloads) passes through untouched, so the patch
is safe and idempotent. The ``structuredContent`` half of the result is produced
elsewhere and is never touched here.
"""

from __future__ import annotations

import contextlib
import json

import mcp.server.fastmcp.utilities.func_metadata as _fm
from mcp.types import TextContent

_installed = False


def _compact_text_block(block: TextContent) -> TextContent:
    """Return ``block`` re-serialized without indentation, or unchanged.

    Non-JSON text (or anything that fails to re-serialize) passes through.
    """
    with contextlib.suppress(Exception):
        return block.model_copy(
            update={
                "text": json.dumps(
                    json.loads(block.text),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            }
        )
    return block


def install_compact_content() -> None:
    """Install the compact-content shim once.

    Subsequent calls are no-ops, so it is safe to call from every
    ``create_server()`` invocation.
    """
    global _installed  # noqa: PLW0603 - module-level guard for one-time monkeypatch
    if _installed:
        return
    _orig = _fm._convert_to_content

    def _compact(result, **kwargs):
        return [
            _compact_text_block(block) if isinstance(block, TextContent) else block
            for block in _orig(result, **kwargs)
        ]

    _fm._convert_to_content = _compact
    _installed = True
