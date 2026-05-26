"""GrepAI provider setup orchestration."""

from __future__ import annotations

from typing import Any

from agents_remember.providers.setup_common import run_lifecycle, selected_provider_enabled

GREPAI_PROVIDER_ID = "grepai-memory"


def install_enabled_provider(args: Any, settings: dict[str, Any]) -> list[dict[str, Any]]:
    if not selected_provider_enabled(args, settings, GREPAI_PROVIDER_ID):
        return []
    return [
        run_lifecycle(
            args.coordination_root,
            "grepai",
            "install",
            timeout=args.timeout,
            dry_run=args.dry_run,
        )
    ]


def refresh_enabled_provider(args: Any, settings: dict[str, Any]) -> list[dict[str, Any]]:
    if not selected_provider_enabled(args, settings, GREPAI_PROVIDER_ID):
        return []
    return [
        run_lifecycle(
            args.coordination_root,
            "grepai",
            "refresh",
            timeout=args.timeout,
            dry_run=args.dry_run,
        )
    ]
