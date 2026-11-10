"""Preserve inherited harness storage locations before a provision stops its host."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents_remember.errors import PaseoRuntimeFailure
from agents_remember.kernel.primitives.paseo_runtime_settings import PaseoRuntimeSettings

if TYPE_CHECKING:
    from agents_remember.serving.paseo.paseo_process_record import ProcessFacts

HOME_CATALOG = json.loads(
    (Path(__file__).resolve().parents[2] / "package_data/host-harness-homes.json").read_text(
        encoding="utf-8"
    )
)


def home_variable_names() -> tuple[str, ...]:
    return tuple(sorted({name for entry in HOME_CATALOG.values() for name in entry["variables"]}))


def _provider_environments(
    settings: PaseoRuntimeSettings, variable: str
) -> list[Mapping[str, str]]:
    """Use each provider's inherited storage contract, including configured derived providers."""
    providers = {name for name, entry in HOME_CATALOG.items() if variable in entry["variables"]}
    selected = []
    for provider, entry in settings.providers.items():
        if provider in providers or entry.get("extends") in providers:
            env = entry.get("env")
            selected.append(env if isinstance(env, dict) else {})
    # Built-in providers not overridden in settings still inherit the supervisor environment.
    selected.extend({} for provider in providers if provider not in settings.providers)
    return selected


def harness_home_status(
    settings: PaseoRuntimeSettings, facts: ProcessFacts | None
) -> dict[str, Any]:
    if facts is None or facts.harness_homes is None:
        return {"inheritedHarnessHomes": "unknown", "harnessHomeRemedy": None}
    inherited = [
        name
        for name, _value in facts.harness_homes
        if any(name not in env for env in _provider_environments(settings, name))
    ]
    return {
        "inheritedHarnessHomes": sorted(inherited),
        "harnessHomeRemedy": (
            "Move each inherited variable into paseoRuntime.providers.<id>.env in the active "
            "settings file so a restart keeps the harness home from any shell."
            if inherited
            else None
        ),
    }


def require_preserved_harness_homes(
    settings: PaseoRuntimeSettings,
    facts: ProcessFacts | None,
    *,
    accept_change: bool = False,
    environment: Mapping[str, str] | None = None,
) -> None:
    """Compare only observed inherited values; unreadable environments add no new refusal."""
    if accept_change or facts is None or facts.harness_homes is None:
        return
    current = os.environ if environment is None else environment
    changed = []
    for name, value in facts.harness_homes:
        for env in _provider_environments(settings, name):
            replacement = env.get(name, current.get(name))
            if replacement != value:
                changed.append({"name": name, "runningValue": value, "newValue": replacement})
                break
    if changed:
        raise PaseoRuntimeFailure(
            "harness-home-would-change",
            "daemon",
            "The running host's harness homes would change; nothing was stopped. Run with "
            "the same variable values, put them in paseoRuntime.providers.<id>.env, or repeat "
            "paseo provision with --accept-harness-home-change.",
            json.dumps(changed, ensure_ascii=False),
        )
