#!/usr/bin/env python3
"""Emit Agents Remember workspace startup context for Codex SessionStart."""

from __future__ import annotations

import json
import os
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DIRECTIVE_PATH = Path(__file__).resolve().with_suffix(".md")


def main() -> None:
    cwd = Path(os.environ.get("PWD", Path.cwd())).resolve()
    try:
        cwd.relative_to(WORKSPACE_ROOT)
    except ValueError:
        return

    directive = DIRECTIVE_PATH.read_text(encoding="utf-8")
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": directive,
        }
    }
    print(json.dumps(payload))


if __name__ == "__main__":
    main()
