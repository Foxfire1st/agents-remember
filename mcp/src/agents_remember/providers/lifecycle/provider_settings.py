"""Provider settings readers for lifecycle commands.

The readers require an EXPLICIT settings path (the lifecycle CLI's
``--from-settings``). At runtime the MCP server generates that file from the
authority config (``providers/settings.py``); a manual invocation must pass the
flag itself. The historic implicit fallback to the coordinator's
``system/settings.json`` was removed with 260703-L13 (GQ3): that file is the
GLOBAL agentic settings home now (with ``contextProviders`` earmarked to return
there as a properly-parsed family in a follow-up), and the authority discipline
already refused it as a provider settings source (``setup_common.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeGuard

from agents_remember.providers.context import ContextProviderError
from agents_remember.providers.lifecycle.state_files import read_json


def require_lifecycle_settings_path(settings_path: Path | None) -> Path:
    if settings_path is None:
        raise ContextProviderError(
            "provider lifecycle commands require an explicit --from-settings path; "
            "coordinator system/settings.json is not an authority source (the "
            "implicit fallback was removed -- the MCP server generates lifecycle "
            "settings from the authority config)"
        )
    return settings_path


def cgc_settings_from_file(settings_path: Path | None) -> tuple[Path, dict[str, Any]]:
    path = require_lifecycle_settings_path(settings_path)
    data = read_json(path)
    provider = data.get("contextProviders", {}).get("providers", {}).get("codegraphcontext-code")
    if not isinstance(provider, dict):
        raise ContextProviderError(
            f"settings file does not define contextProviders.providers.codegraphcontext-code: {path}"
        )
    return path, provider


def context_provider_enabled(settings_path: Path | None, provider_id: str) -> tuple[Path, bool]:
    path = require_lifecycle_settings_path(settings_path)
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


def grepai_settings_from_file(settings_path: Path | None) -> tuple[Path, dict[str, Any]]:
    path = require_lifecycle_settings_path(settings_path)
    data = read_json(path)
    provider = data.get("contextProviders", {}).get("providers", {}).get("grepai-memory")
    return path, provider if isinstance(provider, dict) else {}
