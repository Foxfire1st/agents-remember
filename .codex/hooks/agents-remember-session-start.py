#!/usr/bin/env python3
"""Emit Agents Remember workspace startup context for Codex SessionStart."""

# Generated file -- do not edit.
# Source: scripts/harness/session_start_hook.py
# Regenerate: python3 scripts/sync-harness.py

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DIRECTIVE_PATH = Path(__file__).resolve().with_suffix(".md")


Payload = Callable[[str], dict[str, object]]


def hook_specific_output(directive: str) -> dict[str, object]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": directive,
        }
    }


def started_inside_workspace() -> bool:
    """Whether the session that fired this hook started inside the workspace.

    Codex registers session-start hooks globally, so the hook also fires for sessions
    that have nothing to do with this workspace; emitting the directive there would
    inject unrelated instructions. Every other harness scopes its hooks to the folder
    the configuration lives in and needs no guard.
    """
    cwd = Path(os.environ.get("PWD", Path.cwd())).resolve()
    try:
        cwd.relative_to(WORKSPACE_ROOT)
    except ValueError:
        return False
    return True


HOST_RECORD_FOLDER = "agents"


ROLE_LABEL = "ar.role"


def host_agent_id(value: str | None) -> str | None:
    """``value`` when it has the form of the host's agent ids, else nothing.

    The host's ids are GUIDs: five groups of hexadecimal digits, 8-4-4-4-12. The check runs
    before any path is built from the value.
    """
    if not value:
        return None
    groups = value.split("-")
    if [len(group) for group in groups] != [8, 4, 4, 4, 12]:
        return None
    if any(character not in "0123456789abcdefABCDEF" for group in groups for character in group):
        return None
    return value


def has_binding() -> bool:
    """Whether the host's record of this session's agent carries the product's role label.

    A role agent AR launched is an agent of the host, and the launch labels it ``ar.role``
    (MIK-R113 rule 6). The host gives the harness process its home and the agent's id
    (``PASEO_HOME``, ``PASEO_AGENT_ID``) at every start, reload and resume, and keeps one record
    per agent at ``<home>/agents/<folder per working directory>/<agent id>.json``. That layout
    is the host's own and is named here alone (``HOST_RECORD_FOLDER`` and the pattern below):
    read in host 0.11.0-beta.2, where ``config.js`` names the folder and ``agent-storage.js``
    the file. This reads that one record and of it the labels alone.

    Whenever it cannot tell -- no host names, an id of another form, no record, a record it
    cannot read, no label -- the answer is no and the hook speaks. It never fails a session
    start and prints nothing of a record.
    """

    home = os.environ.get("PASEO_HOME")
    agent_id = host_agent_id(os.environ.get("PASEO_AGENT_ID"))
    if not home or agent_id is None:
        return False
    try:
        records = sorted(Path(home, HOST_RECORD_FOLDER).glob(f"*/{agent_id}.json"))
    except OSError:
        return False
    for path in records:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        labels = record.get("labels") if isinstance(record, dict) else None
        if isinstance(labels, dict) and labels.get(ROLE_LABEL):
            return True
    return False


def emit(build_payload: Payload) -> None:
    if has_binding():
        return
    directive = DIRECTIVE_PATH.read_text(encoding="utf-8")
    print(json.dumps(build_payload(directive)))


if __name__ == "__main__":
    if started_inside_workspace():
        emit(hook_specific_output)
