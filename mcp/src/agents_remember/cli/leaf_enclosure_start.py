"""Start one leaf enclosure in a short-lived process of this build.

A worktree start writes stores whose declared writer is the tool-server role
(``controlplane/durable_store.py``); the dashboard backend is not one of their writers and its
start is refused. When a launch from the dashboard has to create a leaf's enclosure, the backend
therefore runs AR's existing worktree start in a child process, which ends when the start is done:

    <this interpreter> -m agents_remember.cli start-leaf-enclosure --config <settings file>

The settings file is the one the backend itself was started with (``config.config_path``); the
child resolves every root from it and is given none. The request is one JSON object on standard
input (``repoId``, ``taskName``, ``worktreeName``, ``leafId``, ``parentTask``) and the reply one
JSON object on standard output: ``{"ok": true}`` or ``{"ok": false, "error": {"code", "message"}}``.
The exit status is 0 only for ``ok``. The command is internal: it is not listed in the help.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import subprocess
import sys
from typing import Any

from agents_remember.application.worktree_services import build_default_worktree_services
from agents_remember.application.worktree_tool_requests import StartExecution, TaskIdentity
from agents_remember.application.worktree_tools import worktree_start_tool
from agents_remember.controlplane.durable_store import declare_process_role
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.worktrees.services import bind_worktree_services

COMMAND = "start-leaf-enclosure"
# A start that takes longer is stopped and counts as refused.
START_TIMEOUT_SECONDS = 120
_TEXT_LIMIT = 800
_IDENTITY_FIELDS = {
    "repoId": "repo_id",
    "taskName": "task_name",
    "worktreeName": "worktree_name",
    "leafId": "leaf_id",
    "parentTask": "parent_task",
}


def start_leaf_enclosure_in_child(
    config: McpRuntimeConfig, identity: TaskIdentity
) -> dict[str, Any]:
    """Run the worktree start in a child process and return its outcome as the start tool does.

    ``{"ok": True}`` when the child created the enclosure; otherwise ``ok`` is false and
    ``summary`` names the reason: the child's own error code and message, or that it did not end
    in time or answered unreadably.
    """

    request = {key: getattr(identity, field) for key, field in _IDENTITY_FIELDS.items()}
    argv = [sys.executable, "-m", "agents_remember.cli", COMMAND]
    try:
        completed = subprocess.run(
            [*argv, "--config", config.config_path.as_posix()],
            input=json.dumps(request),
            encoding="utf-8",
            capture_output=True,
            check=False,
            timeout=START_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return _refused(
            "leaf_enclosure_start_timeout",
            f"the worktree start did not end within {START_TIMEOUT_SECONDS} seconds and was "
            "stopped.",
        )
    except OSError as error:
        return _refused("leaf_enclosure_start_unavailable", str(error))
    try:
        reply = json.loads(completed.stdout)
    except json.JSONDecodeError:
        reply = None
    if isinstance(reply, dict) and reply.get("ok") is True and completed.returncode == 0:
        return {"ok": True}
    error = reply.get("error") if isinstance(reply, dict) else None
    if isinstance(error, dict):
        return _refused(
            str(error.get("code") or "leaf_enclosure_start_refused"),
            str(error.get("message") or "AR refused to create the leaf enclosure."),
        )
    return _refused(
        "leaf_enclosure_start_unreadable",
        f"the worktree start ended with status {completed.returncode} and no readable reply.",
    )


def _refused(code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "state": code,
        "summary": f"AR could not create the leaf enclosure ({code}): {message}"[:_TEXT_LIMIT],
    }


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        required=True,
        help="Absolute path of the settings file the launching backend was started with.",
    )


def run(args: argparse.Namespace) -> int:
    # The start is the tool server's operation, so this process takes the tool server's role for
    # the stores it writes. The role is declared before the settings are loaded: an undeclared
    # process that runs from a source checkout is given that checkout's development settings
    # instead of the file named here.
    declare_process_role("mcp")
    bind_worktree_services(build_default_worktree_services())
    try:
        identity = _identity(json.load(sys.stdin))
        config = load_config(args.config)
        # Nothing the start prints may reach standard output: it carries the one reply.
        with contextlib.redirect_stdout(sys.stderr):
            created = worktree_start_tool(
                config, identity, execution=StartExecution(skip_provider_setup=True)
            )
    except Exception as error:  # every failure is this command's one reply
        return _reply(str(getattr(error, "code", "") or type(error).__name__), str(error))
    if created.get("ok") is True:
        print(json.dumps({"ok": True}))
        return 0
    return _reply(
        str(created.get("state") or "worktree_start_refused"),
        str(created.get("summary") or created.get("detail") or "AR refused the worktree start."),
    )


def _identity(request: Any) -> TaskIdentity:
    values = (
        [request.get(key) for key in ("repoId", "taskName", "worktreeName", "leafId", "parentTask")]
        if isinstance(request, dict)
        else []
    )
    texts = [value for value in values if isinstance(value, str) and value]
    if len(texts) != len(_IDENTITY_FIELDS):
        raise ValueError(
            "the request must be a JSON object with " + ", ".join(_IDENTITY_FIELDS) + " as text."
        )
    repo_id, task_name, worktree_name, leaf_id, parent_task = texts
    return TaskIdentity(
        repo_id=repo_id,
        task_name=task_name,
        worktree_name=worktree_name,
        leaf_id=leaf_id,
        parent_task=parent_task,
    )


def _reply(code: str, message: str) -> int:
    print(json.dumps({"ok": False, "error": {"code": code, "message": message[:_TEXT_LIMIT]}}))
    return 1
