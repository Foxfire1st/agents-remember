"""Payload builder for the ``read_ar_files`` tool."""

from __future__ import annotations

from typing import Any

from agents_remember.application.read_files import read_ar_files_tool
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

from .base import _tool_payload


def read_ar_files_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    files: list[dict[str, Any]],
    refresh: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "read_ar_files",
        read_ar_files_tool(config, repo_id=repo_id, files=files, refresh=refresh),
    )
