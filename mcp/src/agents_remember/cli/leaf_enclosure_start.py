"""Start one leaf enclosure in a short-lived process of this build.

A worktree start writes stores whose declared writer is the tool-server role
(``controlplane/durable_store.py``); the dashboard backend is not one of their writers and its
start is refused. When a launch from the dashboard has to create a leaf's enclosure, the backend
therefore runs AR's existing worktree start in a child process, which ends when the start is done:

    <this interpreter> -P -m agents_remember.cli start-leaf-enclosure --config <settings file>

The settings file is the one the backend itself was started with (``config.config_path``). The
request is one JSON object on standard input: the identity of the start (``repoId``, ``taskName``,
``worktreeName``, ``leafId``, ``parentTask``) and the roots the backend holds
(``coordinationRoot``, ``codeRoot``, ``memoryRoot``). The child resolves its roots from the
settings file and starts nothing when they are not the backend's. The reply is one JSON object on
standard output: ``{"ok": true}`` or ``{"ok": false, "error": {"code", "message"}}``; the exit
status is 0 only for ``ok``. The command is internal: it is no sub-command of the public command
line and appears in no help.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from agents_remember.application.worktree_services import build_default_worktree_services
from agents_remember.application.worktree_tool_requests import StartExecution, TaskIdentity
from agents_remember.application.worktree_tools import worktree_start_tool
from agents_remember.controlplane.durable_store import declare_process_role
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.worktrees.services import bind_worktree_services

COMMAND = "start-leaf-enclosure"
# A start that takes longer is cut off, with everything it started, and counts as refused.
START_TIMEOUT_SECONDS = 120
ROOTS_DIFFER = "leaf_enclosure_roots_differ"
_TEXT_LIMIT = 800
_ERROR_OUTPUT_TAIL = 300
_IDENTITY_FIELDS = ("repoId", "taskName", "worktreeName", "leafId", "parentTask")


def start_leaf_enclosure_in_child(
    config: McpRuntimeConfig, identity: TaskIdentity
) -> dict[str, Any]:
    """Run the worktree start in a child process and return its outcome as the start tool does.

    ``{"ok": True}`` when the child created the enclosure; otherwise ``ok`` is false and
    ``summary`` names the reason: the child's own error code and message, or that it was cut off
    at the limit or answered unreadably.
    """

    roots = _held_roots(config, identity.repo_id)
    if roots is None:
        return _refused(
            "leaf_enclosure_repository_unknown",
            f"the settings of this backend name no repository {identity.repo_id!r}.",
        )
    request = {
        "repoId": identity.repo_id,
        "taskName": identity.task_name,
        "worktreeName": identity.worktree_name,
        "leafId": identity.leaf_id,
        "parentTask": identity.parent_task,
        **roots,
    }
    # -P: the child inherits the working directory, which can be a folder agents write into; a
    # module lying there must not be imported in place of the build's or the interpreter's own.
    argv = [sys.executable, "-P", "-m", "agents_remember.cli", COMMAND]
    try:
        completed = _run_child([*argv, "--config", config.config_path.as_posix()], request)
    except subprocess.TimeoutExpired as expired:
        return _refused(
            "leaf_enclosure_start_timeout",
            f"the worktree start was cut off after {START_TIMEOUT_SECONDS} seconds, together "
            "with its process group. It may have left a half-made enclosure: if the next Start "
            "on this leaf is refused, the enclosure has to be repaired or abandoned with AR's "
            "worktree tools.",
            expired.stderr,
        )
    except OSError as error:
        return _refused("leaf_enclosure_start_unavailable", str(error))
    try:
        reply = json.loads(completed.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        reply = None
    if isinstance(reply, dict) and reply.get("ok") is True and completed.returncode == 0:
        return {"ok": True}
    error = reply.get("error") if isinstance(reply, dict) else None
    if isinstance(error, dict):
        return _refused(
            str(error.get("code") or "leaf_enclosure_start_refused"),
            str(error.get("message") or "AR refused to create the leaf enclosure."),
            completed.stderr,
        )
    return _refused(
        "leaf_enclosure_start_unreadable",
        f"the worktree start ended with status {completed.returncode} and no readable reply.",
        completed.stderr,
    )


def _run_child(argv: list[str], request: dict[str, Any]) -> subprocess.CompletedProcess[bytes]:
    """Run the child in a process group of its own and end that group at the limit.

    The child inherits this process's environment and working directory and stays in this
    process's session, so whatever ends the backend's session ends the child as well. A start that
    is cut off leaves no process of its group behind that could go on changing a repository after
    the launch was refused. The reply is read when the child itself has ended: a process it left
    behind does not hold the answer back.
    """

    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        child = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=output, stderr=errors, process_group=0
        )
        try:
            with contextlib.suppress(BrokenPipeError):
                assert child.stdin is not None
                child.stdin.write(json.dumps(request).encode("utf-8"))
                child.stdin.close()
            child.wait(timeout=START_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as expired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
            child.wait()
            raise subprocess.TimeoutExpired(
                argv, START_TIMEOUT_SECONDS, stderr=_written(errors)
            ) from expired
        return subprocess.CompletedProcess(
            argv, child.returncode, _written(output), _written(errors)
        )


def _written(stream: Any) -> bytes:
    stream.seek(0)
    return stream.read()


def _held_roots(config: McpRuntimeConfig, repo_id: str) -> dict[str, str | None] | None:
    """The roots a configuration resolves for one repository, as the request carries them."""

    repository = config.repositories.get(repo_id)
    if repository is None:
        return None
    memory_root = repository.memory_root
    return {
        "coordinationRoot": config.coordination_root.resolve().as_posix(),
        "codeRoot": repository.path.resolve().as_posix(),
        "memoryRoot": memory_root.resolve().as_posix() if memory_root else None,
    }


def _refused(code: str, message: str, error_output: bytes | None = None) -> dict[str, Any]:
    """A refusal in the start tool's shape; the tail of the child's error output is kept."""

    tail = (error_output or b"").decode("utf-8", errors="replace").strip()[-_ERROR_OUTPUT_TAIL:]
    said = f" The child process said: {tail}" if tail else ""
    summary = f"AR could not create the leaf enclosure ({code}): {message}"
    return {"ok": False, "state": code, "summary": summary[: _TEXT_LIMIT - len(said)] + said}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog=f"agents-remember {COMMAND}")
    parser.add_argument(
        "--config",
        required=True,
        help="Absolute path of the settings file the launching backend was started with.",
    )
    return run(parser.parse_args(argv))


def run(args: argparse.Namespace) -> int:
    # The start is the tool server's operation, so this process takes the tool server's role for
    # the stores it writes. The role is declared before the settings are loaded: an undeclared
    # process that runs from a source checkout is given that checkout's development settings
    # instead of the file named here.
    declare_process_role("mcp")
    bind_worktree_services(build_default_worktree_services())
    try:
        request = json.load(sys.stdin)
        identity = _identity(request)
        config = load_config(args.config)
        differing = _differing_roots(request, _held_roots(config, identity.repo_id))
        if differing:
            return _reply(
                ROOTS_DIFFER,
                f"{args.config} now names other roots than the backend that asked for this "
                f"start holds ({differing}); nothing was started.",
            )
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
    values = [request.get(key) for key in _IDENTITY_FIELDS] if isinstance(request, dict) else []
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


def _differing_roots(request: dict[str, Any], loaded: dict[str, str | None] | None) -> str:
    """Which roots of the request are not the ones this process loaded; empty when all agree."""

    if loaded is None:
        return f"no repository {request.get('repoId')!r}"
    differing = []
    for name, here in loaded.items():
        asked = request.get(name)
        same = (
            asked is None and here is None
            if asked is None or here is None
            else isinstance(asked, str) and Path(asked).resolve() == Path(here).resolve()
        )
        if not same:
            differing.append(f"{name}: {here} instead of {asked}")
    return "; ".join(differing)


def _reply(code: str, message: str) -> int:
    print(json.dumps({"ok": False, "error": {"code": code, "message": message[:_TEXT_LIMIT]}}))
    return 1
