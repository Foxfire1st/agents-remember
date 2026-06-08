#!/usr/bin/env python3
"""Emit Agents Remember workspace startup context for Cursor sessionStart."""

from __future__ import annotations

import json
from pathlib import Path

DIRECTIVE_PATH = Path(__file__).resolve().with_suffix(".md")


def main() -> None:
    directive = DIRECTIVE_PATH.read_text(encoding="utf-8")
    payload = {"additional_context": directive}
    print(json.dumps(payload))


if __name__ == "__main__":
    main()
