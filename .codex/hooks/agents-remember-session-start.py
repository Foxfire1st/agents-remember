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


def emit(build_payload: Payload) -> None:
    directive = DIRECTIVE_PATH.read_text(encoding="utf-8")
    print(json.dumps(build_payload(directive)))


if __name__ == "__main__":
    if started_inside_workspace():
        emit(hook_specific_output)
