#!/usr/bin/env python3
"""Emit Agents Remember workspace startup context for VS Code SessionStart."""

from __future__ import annotations

import json
from pathlib import Path


WORKSPACE_ROOT_TEMPLATE = "<PATH/TO/YOUR/PROJECTS_FOLDER>"
WORKSPACE_ROOT = Path(WORKSPACE_ROOT_TEMPLATE)
DIRECTIVE_PATH = WORKSPACE_ROOT / ".github" / "hooks" / "agents-remember-session-start.md"


def main() -> None:
    if WORKSPACE_ROOT_TEMPLATE.startswith("<"):
        raise SystemExit(
            "Replace <PATH/TO/YOUR/PROJECTS_FOLDER> in "
            ".github/hooks/agents-remember-session-start.py"
        )

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
