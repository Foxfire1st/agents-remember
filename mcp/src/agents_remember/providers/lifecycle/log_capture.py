"""Trim verbose command output before it rides up into a tool response.

Provider setup runs ``pg_dump | psql``, ``ollama pull``, and CGC ``bundle import``; their
combined stdout/stderr can be hundreds of KB (an ``ollama pull`` alone is one giant progress
bar) -- large enough that the client refuses to render the response. A tool response only
needs the *outcome*: drop the logs on success, keep a short tail on failure, and never echo
secrets embedded in the command line.
"""

from __future__ import annotations

from typing import Any

DEFAULT_FAILURE_TAIL = 30
_LOG_KEYS = ("stdout", "stderr")
_COMMAND_KEYS = ("command", "commands")
# Always redundant in a response: provider results carry a `json` mirror of `payload`
# (setup_common attaches `"json": result.get("payload")`), doubling the size for nothing.
_DROP_ALWAYS = ("json",)
# Verbose provider plumbing only needed to debug a failure; on a successful node the
# outcome (ok + key fields) is enough, and the full detail still lands in the
# provider-state file on disk. Dropping these is what keeps worktree_start renderable.
_DROP_ON_SUCCESS = ("command", "commands", "compose", "migration")


def tail_lines(text: str, max_lines: int) -> str:
    """Keep only the last ``max_lines`` lines, noting how many were dropped."""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    omitted = len(lines) - max_lines
    kept = "\n".join(lines[-max_lines:])
    return f"... [{omitted} earlier line(s) omitted] ...\n{kept}"


def summarize_command_logs(payload: Any, *, failure_tail: int = DEFAULT_FAILURE_TAIL) -> Any:
    """Recursively shrink stdout/stderr and redact command secrets in a result tree.

    A node is treated as successful when it carries ``ok is True`` or ``returncode == 0``.
    On a successful node logs are dropped (``stdout``/``stderr`` kept as ``""`` so the schema
    is preserved) and verbose plumbing (the duplicate ``json`` mirror, ``command``,
    ``compose``, ``migration``) is removed; on a failing node those are retained -- stdout/
    stderr capped to the last ``failure_tail`` lines, command secrets redacted -- so a
    failure is still debuggable. The duplicate ``json`` mirror is always dropped. Mutates in
    place (a terminal response object nothing reads afterwards) and returns it.
    """
    if isinstance(payload, dict):
        succeeded = payload.get("ok") is True or payload.get("returncode") == 0
        _shrink_logs(payload, succeeded=succeeded, failure_tail=failure_tail)
        _drop_verbose_plumbing(payload, succeeded=succeeded)
        for value in payload.values():
            summarize_command_logs(value, failure_tail=failure_tail)
    elif isinstance(payload, list):
        for item in payload:
            summarize_command_logs(item, failure_tail=failure_tail)
    return payload


def _shrink_logs(node: dict[str, Any], *, succeeded: bool, failure_tail: int) -> None:
    """Empty this node's captured output on success; cap it to the failure tail otherwise.

    The keys stay present (as ``""``) so the response keeps its shape either way.
    """
    for key in _LOG_KEYS:
        value = node.get(key)
        if isinstance(value, str) and value:
            node[key] = "" if succeeded else tail_lines(value, failure_tail)


def _drop_verbose_plumbing(node: dict[str, Any], *, succeeded: bool) -> None:
    """Drop the always-redundant mirror, then the debug-only keys or their secrets.

    A successful node loses the plumbing outright; a failing one keeps it -- redacted --
    because that detail is what makes the failure debuggable.
    """
    for key in _DROP_ALWAYS:
        node.pop(key, None)
    if succeeded:
        for key in _DROP_ON_SUCCESS:
            node.pop(key, None)
        return
    for key in _COMMAND_KEYS:
        if key in node:
            node[key] = _redact_commands(node[key])


def _redact_commands(value: Any) -> Any:
    if isinstance(value, list):
        return [_redact_token(token) for token in value]
    if isinstance(value, dict):
        return {key: _redact_commands(item) for key, item in value.items()}
    return value


def _redact_token(token: Any) -> Any:
    # Provider clone commands embed credentials inline (e.g. ``PGPASSWORD=...``); never
    # surface them in a tool response.
    if isinstance(token, str) and "PASSWORD=" in token:
        prefix, _, _ = token.partition("=")
        return f"{prefix}=***"
    return token
