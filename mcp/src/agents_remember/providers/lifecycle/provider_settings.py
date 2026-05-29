"""Provider settings readers for lifecycle commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeGuard

from agents_remember.providers.context import ContextProviderError
from agents_remember.providers.lifecycle.state_files import read_json


def cgc_settings_from_file(
    coordination_root: Path, settings_path: Path | None
) -> tuple[Path, dict[str, Any]]:
    path = settings_path or coordination_root / "system" / "settings.json"
    data = read_json(path)
    provider = data.get("contextProviders", {}).get("providers", {}).get("codegraphcontext-code")
    if not isinstance(provider, dict):
        raise ContextProviderError(
            f"settings file does not define contextProviders.providers.codegraphcontext-code: {path}"
        )
    return path, provider


def context_provider_enabled(
    coordination_root: Path, settings_path: Path | None, provider_id: str
) -> tuple[Path, bool]:
    path = settings_path or coordination_root / "system" / "settings.json"
    data = read_json(path)
    return path, provider_enabled(data, provider_id)


def provider_enabled(data: dict[str, Any], provider_id: str) -> bool:
    context = data.get("contextProviders")
    if not context_providers_enabled(context):
        return False
    providers = context.get("providers")
    if not isinstance(providers, dict):
        return False
    provider = providers.get(provider_id)
    return isinstance(provider, dict) and provider.get("enabled") is True


def context_providers_enabled(context: Any) -> TypeGuard[dict[str, Any]]:
    return isinstance(context, dict) and context.get("enabled") is True


def grepai_settings_from_file(
    coordination_root: Path, settings_path: Path | None
) -> tuple[Path, dict[str, Any]]:
    path = settings_path or coordination_root / "system" / "settings.json"
    data = read_json(path)
    provider = data.get("contextProviders", {}).get("providers", {}).get("grepai-memory")
    return path, provider if isinstance(provider, dict) else {}
