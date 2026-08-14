#!/usr/bin/env python3
"""Fragment library for the per-harness session-start hook scripts.

``scripts/sync-harness.py`` assembles one standalone
``hooks/agents-remember-session-start.py`` per harness that supports a session-start
hook. As with ``render_starter.py``, the hook scripts ship inside a user's workspace
and must stay self-contained, so the sharing happens at generation time.

Two things vary and both are protocol requirements, not preference:

* the payload envelope the harness reads back from the hook's stdout, and
* whether the harness scopes the hook to the workspace itself.
"""

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


def additional_context(directive: str) -> dict[str, object]:
    return {"additional_context": directive}


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
