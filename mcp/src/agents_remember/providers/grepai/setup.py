"""GrepAI provider setup orchestration."""

from __future__ import annotations

from typing import Any

from agents_remember.providers.grepai.isolated import (
    GREPAI_PROVIDER_ID,
    IsolatedGrepaiOptions,
    isolated_grepai_settings,
)
from agents_remember.providers.grepai.seed import (
    GrepaiSeedOptions,
    grepai_clone_bundle,
    grepai_extra_args,
)
from agents_remember.providers.setup_common import (
    load_settings,
    run_lifecycle,
    selected_provider_enabled,
)

__all__ = [
    "GREPAI_PROVIDER_ID",
    "GrepaiSeedOptions",
    "IsolatedGrepaiOptions",
    "grepai_extra_args",
    "install_enabled_provider",
    "isolated_grepai_settings",
    "prepare_enabled_provider",
    "refresh_enabled_provider",
]


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
            extra_args=grepai_extra_args(args),
        )
    ]


def prepare_enabled_provider(args: Any, settings: dict[str, Any]) -> list[dict[str, Any]]:
    if not selected_provider_enabled(args, settings, GREPAI_PROVIDER_ID):
        return []
    if getattr(args, "grepai_seed_source_coordination_root", None) is None:
        return []
    target_settings = getattr(args, "provider_isolated_settings_data", None)
    if target_settings is None:
        target_settings = load_settings(
            getattr(args, "grepai_from_settings", None) or args.from_settings,
        )
    clone = grepai_clone_bundle(args, target_settings or settings)
    return [{"provider": "grepai", "action": "clone-db", **clone}]


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
            extra_args=grepai_extra_args(args),
        )
    ]
