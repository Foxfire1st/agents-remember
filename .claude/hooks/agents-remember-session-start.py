#!/usr/bin/env python3
"""Emit Agents Remember workspace startup context for Claude Code SessionStart."""

from __future__ import annotations

import json
from pathlib import Path

DIRECTIVE_PATH = Path(__file__).resolve().with_suffix(".md")


def main() -> None:
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
