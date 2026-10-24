"""User role defaults and their provenance, independent of capsule compilation."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from agents_remember.application.role_launch_context import (
    RoleLaunchContext,
)
from agents_remember.cli.paseo_bridge import daemon_configuration
from agents_remember.errors import PaseoRuntimeFailure
from agents_remember.kernel._agentic_settings_core import EXTRA_ARGS_HARNESSES
from agents_remember.kernel.agentic_settings import (
    AgenticSettings,
    RoleKnobs,
    load_agentic_settings,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_identity import ROLE_LEVELS
from agents_remember.serving.paseo.paseo_daemon_config import (
    host_provider_extra_args,
    provider_extra_args,
)


def role_defaults(
    config: McpRuntimeConfig, context: RoleLaunchContext, host_role: dict[str, Any]
) -> tuple[dict[str, Any], tuple[str, ...]]:
    repository = context.effective_task.ref.repository if context.effective_task else None
    scope = config.repositories.get(repository) if repository else None
    settings = load_agentic_settings(
        config.coordination_root, repo_root=scope.path if scope else None
    )
    knobs = settings.resolved_role_knobs(context.role, ROLE_LEVELS[context.role])
    step = knobs.step_up
    return {
        "agent": knobs.harness or settings.spawn_harness,
        "model": knobs.model,
        "effort": knobs.effort,
        "serviceTier": knobs.service_tier,
        "mode": knobs.mode,
        "featureValues": dict(knobs.feature_values),
        "notes": knobs.notes,
        "extraArgs": extra_args_statement(settings, knobs, context.role, config=config),
        "role": context.role,
        "notesKey": knobs.origins["notes"].key
        if "notes" in knobs.origins
        else f"orchestration.roles.{context.role}.notes",
        "hostRole": host_role,
        "settingsSources": [path.as_posix() for path in settings.sources],
        "fieldOrigins": {key: asdict(origin) for key, origin in knobs.origins.items()},
        "stepUp": {
            **{
                key: value
                for key, value in {
                    "harness": step.choices.harness,
                    "model": step.choices.model,
                    "effort": step.choices.effort,
                    "serviceTier": step.choices.service_tier,
                    "mode": step.choices.mode,
                    "featureValues": step.choices.feature_values or None,
                    "notes": step.choices.notes,
                    "afterTries": step.after_tries,
                }.items()
                if value is not None
            },
        }
        if step
        else None,
    }, settings.harness_ids


def extra_args_statement(
    settings: AgenticSettings,
    knobs: RoleKnobs,
    role: str,
    *,
    config: McpRuntimeConfig | None = None,
) -> dict[str, Any]:
    """The extra harness arguments the role's settings state, as plain data for one launch.

    The harness entries belong to the harness that states them; the role entries belong to the
    role's settings harness -- the row's ``harness``, else ``orchestration.spawn.harness`` -- as a
    model does (MIK-R192 rule 4). A launch lays the role entry over the harness entry key by key.
    ``hostConfiguration`` names the arguments the host's own provider map gives a harness, which a
    start notes and never takes (rule 7).
    """
    role_entries: dict[str, dict[str, Any]] = {}
    for name, value in knobs.extra_args.items():
        origin = knobs.origins.get(f"extraArgs.{name}")
        role_entries[name] = {
            "name": name,
            "value": value,
            "key": origin.key
            if origin is not None
            else f"orchestration.roles.{role}.extraArgs.{name}",
            "source": origin.source if origin is not None else _settings_source(settings),
        }
    configured, unread = _host_configuration(config) if config is not None else ({}, [])
    return {
        "settingsHarness": knobs.harness or settings.spawn_harness,
        "harness": {
            harness: {name: asdict(entry) for name, entry in entries.items()}
            for harness, entries in settings.harness_extra_args.items()
        },
        "hostConfiguration": configured,
        **({"hostConfigurationUnread": unread} if unread else {}),
        "role": role_entries,
    }


def _host_configuration(config: McpRuntimeConfig) -> tuple[dict[str, list[str]], list[str]]:
    """The host's own arguments per harness, and the harnesses whose running map was not read.

    The file's map is read always; the running daemon's map is read through the composition root's
    port (``cli.paseo_bridge.daemon_configuration``), because the serving module that walks the
    document ranks below the bridge's caller and never imports it (MIK-R192 rule 19). A live read
    that fails leaves the file's map standing and names the harness, so the launch record says the
    map was not read instead of presenting the file as the clean whole (rule 7).
    """

    runtime = config.paseo_runtime
    configuration: dict[str, list[str]] = {}
    unread: list[str] = []
    for harness in sorted(EXTRA_ARGS_HARNESSES):
        try:
            configuration[harness] = list(
                host_provider_extra_args(config, harness, read_config=daemon_configuration)
            )
        except PaseoRuntimeFailure:
            configuration[harness] = (
                list(provider_extra_args(runtime.home, harness)) if runtime is not None else []
            )
            unread.append(harness)
    return configuration, unread


def extra_args_plan(
    statement: dict[str, Any] | None,
    provider: str | None,
) -> dict[str, Any] | None:
    """What one start takes from the statement, and what of it the start does not take.

    The harness entries follow the harness the start actually uses. The role entries are taken
    only while the start uses the role's settings harness, as its model is (MIK-R71 rule 17); a
    start on another harness, or one with no settings harness at all, takes none of them and says
    why. The host's own configured arguments are noted, never taken (MIK-R192 rule 7). ``None``
    means the start carries no extra arguments and nothing to record.
    """
    if not statement:
        return None
    entries: dict[str, str | None] = {}
    arguments: dict[str, dict[str, Any]] = {}
    removed: list[dict[str, Any]] = []
    not_taken: list[dict[str, Any]] = []
    reason: str | None = None
    host_configuration = [
        {"name": name, "source": "host configuration"}
        for name in (statement.get("hostConfiguration") or {}).get(provider) or ()
    ]
    supported = provider is not None and provider in EXTRA_ARGS_HARNESSES
    if supported:
        for entry in (statement.get("harness") or {}).get(provider, {}).values():
            entries[entry["name"]] = entry["value"]
            arguments[entry["name"]] = _argument(entry, "harness entry")
    role_entries = list((statement.get("role") or {}).values())
    settings_harness = statement.get("settingsHarness")
    if role_entries and supported and settings_harness == provider:
        for entry in role_entries:
            if entry["value"] is False:
                if entry["name"] in entries:
                    removed.append(_argument(entry, _entry_source(entry["key"])))
                    del entries[entry["name"]]
                    arguments.pop(entry["name"], None)
                continue
            entries[entry["name"]] = entry["value"]
            arguments[entry["name"]] = _argument(entry, _entry_source(entry["key"]))
    elif role_entries:
        not_taken = [_argument(entry, _entry_source(entry["key"])) for entry in role_entries]
        reason = (
            f"the start uses {provider or 'the host default'}; the role's extra arguments belong "
            f"to {settings_harness}"
            if settings_harness
            else "no settings harness is stated, so the role's extra arguments are not taken"
        )
    if (
        not entries
        and not not_taken
        and not removed
        and not host_configuration
        and not statement.get("hostConfigurationUnread")
    ):
        # Nothing to say about extra arguments — unless the daemon's map was not read, which the
        # record must still carry (F21: a clean disk is not a verified-clean host).
        return None
    plan = {
        "entries": entries,
        "arguments": list(arguments.values()),
        "removed": removed,
        "notTaken": not_taken,
        "notTakenReason": reason,
        "hostConfiguration": host_configuration,
    }
    return _with_unread(plan, statement)


def _with_unread(plan: dict[str, Any], statement: dict[str, Any]) -> dict[str, Any]:
    """Say in the record that the running daemon's map was not read (MIK-R192 rules 7 and 8)."""

    unread = list(statement.get("hostConfigurationUnread") or ())
    if unread:
        # The file's map above is what the start knows, not a verified-clean map.
        plan["hostConfigurationUnread"] = unread
    return plan


def _argument(entry: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "name": entry["name"],
        "value": entry["value"],
        "source": source,
        "key": entry["key"],
        "file": entry["source"],
    }


def _entry_source(key: str) -> str:
    return "per-level entry" if key.startswith("orchestration.rolesPerLevel.") else "role entry"


def _settings_source(settings: AgenticSettings) -> str:
    return settings.sources[-1].as_posix() if settings.sources else "agentic settings defaults"
