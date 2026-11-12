"""The installer-owned, explicit move to the user's settings directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents_remember.install.runtime import upgrade_user_settings
from agents_remember.kernel.agentic_settings import AgenticSettingsError


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--coordination-root", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")


def run(args: argparse.Namespace) -> int:
    if not args.coordination_root.is_absolute():
        print(json.dumps({"error": "coordination root must be absolute"}))
        return 2
    try:
        result = upgrade_user_settings(args.coordination_root, dry_run=args.dry_run)
    except (AgenticSettingsError, OSError) as error:
        print(
            json.dumps(
                {
                    "error": str(error),
                    "oldPath": str(args.coordination_root / "system/settings.json"),
                }
            )
        )
        return 2
    print(json.dumps(result))
    return 0
