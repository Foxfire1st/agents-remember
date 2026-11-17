"""CLI adapter: report the browser the explainer publishes with, or fetch it.

    agents-remember explainer-browser            # what is present, what is missing
    agents-remember explainer-browser --fetch    # fetch the missing part into the product's folder

The explainer fetches Playwright and its Chromium by itself at the first explanation, so this
command is not a step of an installation: it is the way to see what that fetch put where, and the
way to run it again, or ahead of time, when a render could not. One JSON document per run.

Exit status: 0 when the browser is there (or was fetched under ``--fetch``); 1 when it is missing
or the fetch failed, with the document naming the cause and what to do about it.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from agents_remember.canvas.explainer_browser import (
    BROWSER_NOTICE,
    PLAYWRIGHT_VERSION,
    browser_missing,
    ensure,
    home,
)
from agents_remember.canvas.explainer_errors import ExplainerRefused


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Fetch Playwright and its Chromium into the product's own folder when either is missing.",
    )


def run(args: argparse.Namespace) -> int:
    if args.fetch:
        return _fetched()
    return _reported()


def _fetched() -> int:
    try:
        record = ensure().record()
    except ExplainerRefused as refused:
        print(json.dumps(_failure(refused), indent=2))
        return 1
    print(json.dumps({"ok": True, "browser": record}, indent=2))
    return 0


def _reported() -> int:
    missing = browser_missing()
    folder = home()
    document: dict[str, Any] = {
        "ok": not missing,
        "browser": {
            "playwright": PLAYWRIGHT_VERSION,
            "folder": folder.root.as_posix(),
            "module": folder.module.as_posix(),
            "browsers": folder.browsers.as_posix(),
        },
    }
    if missing:
        document["missing"] = list(missing)
        document["tellTheUser"] = BROWSER_NOTICE
        document["remedy"] = "agents-remember explainer-browser --fetch"
    print(json.dumps(document, indent=2))
    return 0 if not missing else 1


def _failure(refused: ExplainerRefused) -> dict[str, Any]:
    return {"ok": False, "error": {"code": refused.reason, "message": refused.detail}}
