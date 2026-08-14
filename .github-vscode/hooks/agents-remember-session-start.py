#!/usr/bin/env python3
"""Emit Agents Remember workspace startup context for VS Code + Copilot SessionStart."""

# Generated file -- do not edit.
# Source: scripts/harness/session_start_hook.py
# Regenerate: python3 scripts/sync-harness.py

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

DIRECTIVE_PATH = Path(__file__).resolve().with_suffix(".md")


Payload = Callable[[str], dict[str, object]]


def hook_specific_output(directive: str) -> dict[str, object]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": directive,
        }
    }


def emit(build_payload: Payload) -> None:
    directive = DIRECTIVE_PATH.read_text(encoding="utf-8")
    print(json.dumps(build_payload(directive)))


if __name__ == "__main__":
    emit(hook_specific_output)
