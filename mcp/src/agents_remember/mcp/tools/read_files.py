"""Payload builder for the ``read_ar_files`` tool (MIK-R109 rule 1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.application.read_files import (
    ARGUMENT_REMOVED_SENTENCE,
    read_ar_files_tool,
)
from agents_remember.application.task_scoped_mcp import (
    task_scoped_mcp_config_for_reader,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.task_document_ref import TaskScopedReaderContext

from .base import _tool_payload

_REMOVED_ARGUMENT: Any = "removed"
"""Sentinel for the removed ``refresh`` argument: only an omitted argument
carries it, so any supplied value -- true, false or an explicit null --
is distinguishable and refused (MIK-R109 rule 1)."""


def read_ar_files_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    files: list[Any],
    refresh: Any = _REMOVED_ARGUMENT,
    *,
    task_context: TaskScopedReaderContext | None = None,
) -> dict[str, Any]:
    scoped_config = task_scoped_mcp_config_for_reader(
        config,
        repository_id=repo_id,
        task_context=task_context,
    )
    if refresh is not _REMOVED_ARGUMENT:
        # The parameter is removed (MIK-R109 rule 1): any supplied value --
        # true, an explicit false, or an explicit null, which omission alone
        # must not mimic -- is refused with `argument-removed`, like an
        # object-form file entry.
        body = {
            "ok": True,
            "operation": "read_ar_files",
            "repoId": repo_id,
            "state": "refused",
            "refusalCode": "argument-removed",
            "refusalDetail": ARGUMENT_REMOVED_SENTENCE,
        }
    else:
        working_folder = _working_folder(scoped_config, task_context)
        body = read_ar_files_tool(
            scoped_config, repo_id=repo_id, files=files, working_folder=working_folder
        )
    return _tool_payload("read_ar_files", body)


def _working_folder(scoped_config: McpRuntimeConfig, task_context: Any) -> Path | None:
    """The caller's working folder when the server knows it, else ``None``.

    A leaf's working folder is its worktree group: the parent of the
    enclosure's code worktree, which the scoped config points at. Locations
    inside it are answered relative (MIK-R108 rule 1); otherwise absolute.
    """

    if task_context is None:
        return None
    try:
        repo = scoped_config.repositories[task_context.task_document_ref.repository]
    except (KeyError, AttributeError):
        return None
    return Path(repo.path).parent
