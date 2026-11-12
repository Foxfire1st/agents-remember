"""Fresh Paseo launch choices with per-part durable last-answer fallback."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Any

from agents_remember.application.user_settings_init import SettingsInitRefused
from agents_remember.cli.paseo_bridge import (
    BRIDGE_INVALID_REPLY,
    PaseoBridgeFailure,
    bridge_call,
    require_bridge_runtime,
)
from agents_remember.cli.paseo_catalog_features import selected_model_features
from agents_remember.cli.paseo_catalog_names import (
    canonical_defaults,
    canonical_fields,
    canonical_model_choices,
)
from agents_remember.cli.paseo_catalog_names import (
    part_known as _part_known,
)
from agents_remember.cli.paseo_catalog_store import (
    catalog_store_path,
    merge_catalog_parts,
    saved_catalog_parts,
)
from agents_remember.cli.role_instructions import launch_choice_metadata
from agents_remember.cli.role_launch_defaults import extra_args_plan
from agents_remember.cli.role_step_up import label_step_up_choices
from agents_remember.kernel._agentic_settings_core import NON_PERMISSION_FEATURES
from agents_remember.kernel.agentic_settings import load_agentic_settings
from agents_remember.kernel.primitives.named_choices import named as _named
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import RoleAgentOverride


@dataclass(frozen=True)
class LauncherCatalog:
    """One discovery of the runtime's catalog; ``origin`` changes with every discovery."""

    origin: str
    agents: tuple[dict[str, Any], ...]
    # The providers the runtime reports as not accepting tool servers from their host.
    without_tool_servers: frozenset[str] = frozenset()

    discovery: dict[str, Any] = field(default_factory=lambda: {"source": "fresh", "readAt": None})
    mode: str = "full"
    asked: bool = True
    scope: tuple[str, ...] = ()
    feature_answers: dict[tuple[str, str], dict[str, Any]] = field(
        default_factory=dict, compare=False
    )

    def agent(self, agent_id: str) -> dict[str, Any] | None:
        return _named(self.agents, agent_id)


class LaunchSelectionRefused(ValueError):
    """The requested agent, model or effort is not one the currently answered catalog offers."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_OPERATION_CATALOGS: ContextVar[dict[tuple[str, ...], LauncherCatalog] | None] = ContextVar(
    "paseo_operation_catalogs", default=None
)


@contextmanager
def launcher_catalog_question() -> Iterator[None]:
    """Share a start's two questions (metadata, selected features) or a view's one full read."""
    if _OPERATION_CATALOGS.get() is not None:
        yield
        return
    token = _OPERATION_CATALOGS.set({})
    try:
        yield
    finally:
        _OPERATION_CATALOGS.reset(token)


def launcher_catalog(
    config: McpRuntimeConfig, *, refresh: bool = False, mode: str = "metadata"
) -> LauncherCatalog:
    """Ask Paseo now; only unanswered parts use its durable last successful answer."""
    scope = paseo_catalog_scope(config)
    current = _OPERATION_CATALOGS.get()
    if not refresh and current is not None and scope in current:
        cached = current[scope]
        if mode != "full" or cached.mode == "full":
            return cached
    catalog = _question_catalog(config, scope, refresh=refresh, mode=mode)
    if current is not None:
        current[scope] = catalog
    return catalog


def _question_catalog(
    config: McpRuntimeConfig, scope: tuple[str, ...], *, refresh: bool, mode: str
) -> LauncherCatalog:
    runtime = require_bridge_runtime(config)
    error = None
    try:
        reply = bridge_call(
            config,
            "catalog",
            {
                "cwd": config.workspace_root.as_posix(),
                **({"refresh": True} if refresh else {}),
                **({"mode": mode} if mode != "full" else {}),
            },
        )
    except PaseoBridgeFailure as failure:
        reply, error = None, str(failure)
    merged = merge_catalog_parts(catalog_store_path(runtime.home), scope, reply, error)
    return LauncherCatalog(
        origin=f"paseo:{uuid.uuid4().hex}",
        agents=_agents_from_reply(merged),
        without_tool_servers=_providers_without_tool_servers(merged),
        discovery=merged["catalogDiscovery"],
        mode=mode,
        scope=scope,
    )


def saved_launcher_catalog(config: McpRuntimeConfig) -> LauncherCatalog:
    """Offer the saved snapshot while a launcher awaits its fresh question; never author it."""
    runtime = require_bridge_runtime(config)
    saved = saved_catalog_parts(catalog_store_path(runtime.home), paseo_catalog_scope(config))
    return LauncherCatalog(
        origin="paseo:saved",
        agents=_agents_from_reply(saved),
        without_tool_servers=_providers_without_tool_servers(saved),
        discovery=saved["catalogDiscovery"],
        asked=False,
    )


def runnable_harnesses(config: McpRuntimeConfig) -> list[dict[str, str]] | None:
    """The provider ids the configured host can run, as the catalog answers them now.

    The role-settings initializer needs this check; the MCP tool handler supplies this reader so
    the application layer never imports the CLI.
    """

    try:
        catalog = launcher_catalog(config)
    except PaseoBridgeFailure as error:
        raise SettingsInitRefused(
            "host-unreadable",
            f"The host's providers cannot be read: {error}",
            "Start the host (runtime_install, then retry) and call again.",
        ) from error
    if catalog.discovery.get("source") == "unknown":
        return None
    return [{"id": agent["id"], "label": agent["label"]} for agent in catalog.agents]


def paseo_catalog_scope(config: McpRuntimeConfig) -> tuple[str, str, str, str]:
    """The identity of one Paseo runtime: its install, daemon home, listen address and version."""

    settings = require_bridge_runtime(config)
    return (
        settings.install_prefix.as_posix(),
        settings.home.as_posix(),
        settings.listen,
        settings.version,
    )


def read_host_role(config: McpRuntimeConfig, role: str) -> dict[str, Any]:
    """Read matching host profiles; record an unavailable read and use role settings."""
    try:
        reply = bridge_call(config, "role-profiles", {})
        profiles = reply.get("profiles")
        if not isinstance(profiles, list):
            raise _unreadable_catalog()
        matches = [row for row in profiles if isinstance(row, dict) and row.get("name") == role]
        return {
            "profile": matches[0] if matches else {},
            "passedOver": [row.get("id") for row in matches[1:]],
            "readAt": reply.get("readAt"),
            "state": "read" if matches else "no-match",
            "reason": None if matches else "the host returned no profile with this exact role name",
            "profileCount": len(profiles),
        }
    except PaseoBridgeFailure as error:
        return {"profile": {}, "state": "unavailable", "reason": str(error)}


def launcher_options(
    config: McpRuntimeConfig,
    defaults: dict[str, Any],
    harness_order: tuple[str, ...],
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    catalog = launcher_catalog(config, refresh=refresh, mode="full")
    try:
        agent_id, options = resolve_agent_selection(
            config, defaults, harness_order, None, catalog=catalog
        )
        role_defaults = {
            "agent": agent_id,
            "stepUp": defaults.get("stepUp"),
            "model": options.get("model"),
            "effort": options.get("effort"),
            "serviceTier": defaults.get("serviceTier")
            if options.get("choices", {})
            .get("featureValues", {})
            .get("service_tier", {})
            .get("source")
            == "our settings"
            else options.get("serviceTier"),
            "mode": options.get("mode"),
            "featureValues": options.get("featureValues", {}),
            "choices": launch_choice_metadata(options["choices"]),
            "available": True,
        }
    except LaunchSelectionRefused as error:
        role_defaults = {**defaults, "available": False, "refusal": str(error)}
    return {
        "roleDefaults": role_defaults,
        "agents": [dict(agent) for agent in catalog.agents],
        "catalogOrigin": catalog.origin,
        "catalogDiscovery": catalog.discovery,
    }


def resolve_agent_selection(
    config: McpRuntimeConfig,
    defaults: dict[str, Any],
    harness_order: tuple[str, ...],
    override: RoleAgentOverride | None,
    *,
    catalog: LauncherCatalog | None = None,
) -> tuple[str, dict[str, Any]]:
    """Resolve fields by source, keeping lower choices bound to their provider/model."""
    catalog = launcher_catalog(config) if catalog is None or not catalog.asked else catalog
    defaults = canonical_defaults(catalog.agents, defaults)
    settings_agent = _default_agent(defaults["agent"], harness_order, catalog)
    profile_read = defaults.get("hostRole", {})
    profile = canonical_fields(
        catalog.agents, profile_read.get("profile", {}), ("provider", "model", "thinkingOptionId")
    )
    host_agent = profile.get("provider") or None
    call = canonical_fields(
        catalog.agents,
        override.model_dump(by_alias=True, exclude_none=True) if override else {},
        ("agentId", "modelId", "effortId"),
    )
    call_features = call.get("featureValues", {})
    agent_id = call.get("agentId") or host_agent or settings_agent
    agent_id = _canonical_agent_id(catalog, agent_id)
    if agent_id is None:
        raise LaunchSelectionRefused("agent_not_selected", "No agent is selected.")
    choices: dict[str, Any] = {}
    ignored_settings: list[dict[str, Any]] = []

    def pick(key: str, candidates: list[tuple[Any, str]]) -> Any:
        value, source = next(
            ((v, origin) for v, origin in candidates if v is not None and v != ""),
            (None, "host default"),
        )
        choices[key] = {
            "value": value,
            "source": source,
            **_choice_origin(source, key, defaults, profile),
        }
        return value

    pick(
        "agent",
        [
            (call.get("agentId"), "start call"),
            (host_agent, "host role"),
            (settings_agent, "our settings" if defaults["agent"] else "host default"),
        ],
    )
    model_id = pick(
        "model",
        [
            (call.get("modelId"), "start call"),
            (profile.get("model") if host_agent == agent_id else None, "host role"),
            (
                _setting_value(defaults, "model", (agent_id, None), ignored_settings)
                if settings_agent == agent_id
                else None,
                "our settings",
            ),
        ],
    )
    host_model = profile.get("model") or (
        _setting_value(defaults, "model", (agent_id, None), ignored_settings)
        if host_agent == settings_agent
        else None
    )
    effort_id = pick(
        "effort",
        [
            (call.get("effortId"), "start call"),
            (
                profile.get("thinkingOptionId")
                if host_agent == agent_id and model_id == host_model
                else None,
                "host role",
            ),
            (
                _setting_value(defaults, "effort", (agent_id, model_id), ignored_settings)
                if settings_agent == agent_id
                and (
                    defaults.get("fieldOrigins", {}).get("effort") is not None
                    or model_id == defaults["model"]
                )
                else None,
                "our settings",
            ),
        ],
    )
    model_id, effort_id = canonical_model_choices(catalog.agent(agent_id), model_id, effort_id)
    choices.update(
        {
            key: {**choices[key], "value": value}
            for key, value in (("agent", agent_id), ("model", model_id), ("effort", effort_id))
        }
    )
    refusal = _selection_refusal(catalog, agent_id, model_id, effort_id)
    if refusal:
        key = (
            "effort"
            if "effort" in refusal.code
            else "model"
            if "model" in refusal.code
            else "agent"
        )
        raise LaunchSelectionRefused(
            refusal.code,
            f"Role {defaults.get('role')}: {_choice_source(choices[key], key, defaults.get('role'))}: {refusal}",
        )
    agent = catalog.agent(agent_id) or _unknown_agent(agent_id)
    mode = pick(
        "mode",
        [
            (profile.get("modeId") if host_agent == agent_id else None, "host role"),
            (
                _setting_value(defaults, "mode", (agent_id, None), ignored_settings)
                if settings_agent == agent_id
                else None,
                "our settings",
            ),
        ],
    )
    mode, choices["mode"] = _resolve_mode(
        mode,
        choices["mode"],
        agent,
        defaults.get("role"),
        host_default=defaults.get("useHostDefaultMode", False),
    )
    model = _model_with_features(config, catalog, agent, (agent_id, model_id))
    call_features = _canonical_features(call_features, model)
    _check_permission_features(call_features, defaults.get("role"))
    settings_features = _settings_features(defaults, model)
    layers = [
        (
            settings_features,
            "our settings",
            settings_agent,
            defaults["model"],
            defaults.get("fieldOrigins", {}),
        ),
        (
            _canonical_features(profile.get("featureValues") or {}, model),
            "host role",
            host_agent,
            host_model,
            {
                key: {
                    "provider": host_agent,
                    "model": host_model,
                    "profileId": profile.get("id"),
                    "profileName": profile.get("name"),
                    "key": f"featureValues.{key}",
                }
                for key in profile.get("featureValues") or {}
            },
        ),
        (call_features, "start call", agent_id, model_id, {}),
    ]
    features, feature_sources, omitted = _resolve_features(
        layers,
        agent_id=agent_id,
        model_id=model_id,
        model=model,
        role=defaults.get("role"),
    )
    choices["notes"] = _resolve_notes(defaults, profile)
    choices["featureValues"] = feature_sources
    choices["settingsSources"] = defaults.get("settingsSources", [])
    choices["profileRead"] = profile_read
    omitted.extend(
        _omitted_lower_choices(
            [
                ("our settings", settings_agent, defaults["model"], defaults),
                (
                    "host role",
                    host_agent,
                    host_model,
                    {
                        "model": profile.get("model"),
                        "effort": profile.get("thinkingOptionId"),
                        "mode": profile.get("modeId"),
                    },
                ),
            ],
            (agent_id, model_id),
        )
    )
    choices["omitted"] = omitted + ignored_settings
    choices["catalogOrigin"] = catalog.origin
    options: dict[str, Any] = {"choices": choices, "featureValues": features}
    options.update(
        {
            key: value
            for key, value in (("model", model_id), ("effort", effort_id), ("mode", mode))
            if value
        }
    )
    if "service_tier" in features:
        options["serviceTier"] = features["service_tier"]
    # MIK-R192: the extra harness arguments this start takes, with where each came from, and the
    # role entries it does not take. They are handed to the host with the create request and
    # recorded in the start's receipt.
    _attach_extra_args(options, defaults, agent_id)
    label_step_up_choices(options, defaults)
    return agent_id, options


def _attach_extra_args(options: dict[str, Any], defaults: dict[str, Any], agent_id: str) -> None:
    """Add the extra harness arguments this start takes, if it takes any (MIK-R192)."""
    extra_args = extra_args_plan(defaults.get("extraArgs"), agent_id)
    if extra_args:
        options["extraArgs"] = extra_args


def _setting_value(
    defaults: dict[str, Any],
    key: str,
    selection: tuple[str, str | None],
    ignored: list[dict[str, Any]],
) -> Any:
    value = defaults.get(key)
    origin = defaults.get("fieldOrigins", {}).get(key)
    if value and origin and not _scope_matches(origin, selection, key):
        ignored.append(
            {
                "key": key,
                "value": value,
                "source": "our settings",
                "origin": origin,
                "reason": "authored provider/model binding differs",
            }
        )
        return None
    return value


def _scope_matches(origin: dict[str, Any], selection: tuple[str, str | None], field: str) -> bool:
    provider, model = selection
    model_scoped = field == "effort" or (
        field.startswith("featureValues.") and field != "featureValues.service_tier"
    )
    return origin.get("provider") in (None, provider) and (
        not model_scoped or origin.get("model") == model
    )


def _choice_origin(
    source: str, key: str, defaults: dict[str, Any], profile: dict[str, Any]
) -> dict[str, Any]:
    if source == "our settings":
        origin = defaults.get("fieldOrigins", {}).get("harness" if key == "agent" else key)
        return {"origin": origin} if origin else {}
    if source == "host role":
        return {
            "origin": {
                "profileId": profile.get("id"),
                "profileName": profile.get("name"),
                "key": {"agent": "provider", "effort": "thinkingOptionId", "mode": "modeId"}.get(
                    key, key
                ),
            }
        }
    return {}


def _choice_source(choice: dict[str, Any], key: str, role: str | None) -> str:
    origin = choice.get("origin", {})
    source = choice.get("source", "host default")
    if source == "our settings":
        key = origin.get(
            "key", f"orchestration.roles.{role}.{'harness' if key == 'agent' else key}"
        )
        return f"{key} in {origin.get('source', 'role settings')}"
    if source == "host role":
        return f"host role {origin.get('profileName')!r} (id {origin.get('profileId')!r}), {origin.get('key', key)}"
    return f"{key} from {source}"


def _resolve_mode(
    mode: str | None,
    choice: dict[str, Any],
    agent: dict[str, Any],
    role: str | None,
    *,
    host_default: bool = False,
) -> tuple[str | None, dict[str, Any]]:
    if agent.get("modesKnown") is False:
        return mode, choice
    modes = agent["modes"]
    if mode is None and host_default:
        mode = agent.get("defaultModeId")
        choice = {"value": mode, "source": "host default"}
    elif mode is None:
        safe = {"claude": "default", "codex": "auto"}.get(agent["id"])
        mode = safe if any(row["id"] == safe for row in modes) else None
        choice = {
            "value": mode,
            "source": "least permissive mode" if mode else "none",
            "reason": "least permissive supported mode for this agent kind"
            if mode
            else "no named least working mode offered for this agent kind",
        }
    matched = _named(modes, mode) if mode is not None else None
    if matched is not None:
        mode = matched["id"]
        choice = {**choice, "value": mode}
    if mode is not None and matched is None:
        raise LaunchSelectionRefused(
            "mode_not_offered",
            f"Role {role}: {_choice_source(choice, 'mode', role)}={mode!r} is not offered for {agent['id']}; offered: {[row['id'] for row in modes]}",
        )
    return mode, choice


def _omitted_lower_choices(
    layers: list[tuple[str, str | None, str | None, dict[str, Any]]],
    selection: tuple[str, str | None],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    agent_id, model_id = selection
    for source, provider, scoped_model, lower in layers:
        for key in ("model", "effort", "mode"):
            value = lower.get(key)
            if value and (provider != agent_id or (key == "effort" and scoped_model != model_id)):
                result.append(
                    {
                        "key": key,
                        "value": value,
                        "source": source,
                        "reason": "provider/model differs",
                    }
                )
    return result


def _resolve_features(
    layers: list[tuple[dict[str, Any], str, str | None, str | None, dict[str, Any]]],
    *,
    agent_id: str,
    model_id: str | None,
    model: dict[str, Any] | None,
    role: str | None,
) -> tuple[dict[str, str | bool], dict[str, Any], list[dict[str, Any]]]:
    offered = model.get("features", []) if model else []
    omitted: list[dict[str, Any]] = []
    features: dict[str, str | bool] = {}
    feature_sources: dict[str, Any] = {}
    for values, source, provider, scoped_model, origins in layers:
        for key, value in values.items():
            field_key = f"featureValues.{key}"
            origin = origins.get(field_key)
            if key == "service_tier" and "serviceTier" in origins:
                alias = origins["serviceTier"]
                if not origin or alias["precedence"] >= origin["precedence"]:
                    origin = alias
            binding = origin or {"provider": provider, "model": scoped_model}
            if not _scope_matches(binding, (agent_id, model_id), field_key):
                omitted.append(
                    {
                        "key": field_key,
                        "value": value,
                        "source": source,
                        "origin": origin,
                        "reason": "provider/model differs",
                    }
                )
                continue
            features[key] = value
            feature_sources[key] = {
                "value": value,
                "source": source,
                **({"origin": origin} if origin else {}),
            }
    _validate_features(features, feature_sources, (agent_id, model_id), model, role)
    for definition in offered:
        if definition["id"] not in feature_sources:
            feature_sources[definition["id"]] = {
                "value": definition.get("value"),
                "source": "host default",
            }
    return features, feature_sources, omitted


def _resolve_notes(defaults: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    notes = profile.get("notes")
    if not isinstance(notes, str) or not notes.strip():
        notes = defaults.get("notes")
        note_source = "our settings" if notes else "none"
    else:
        note_source = "host role"
    if notes:
        lines = notes.splitlines()
        first = next((i for i, line in enumerate(lines) if line.strip()), None)
        if (
            first is not None
            and lines[first].strip() == "<overwrite>"
            and not "\n".join(lines[first + 1 :]).strip()
        ):
            raise LaunchSelectionRefused(
                "empty_instruction_overwrite",
                f"Role {defaults.get('role')}: notes from {note_source} contains <overwrite> without instructions.",
            )
    return {
        "value": notes,
        "source": note_source,
        "key": defaults.get("notesKey", f"orchestration.roles.{defaults.get('role')}.notes")
        if note_source == "our settings"
        else "notes",
    }


def provider_accepts_tool_servers(config: McpRuntimeConfig, agent_id: str) -> bool:
    """Whether the runtime reports that this provider takes tool servers from its host.

    The answer follows the same fresh-question and per-part fallback as launch choices. Only an
    explicit report of the runtime withholds the tool server from a provider.
    """

    agent = launcher_catalog(config).agent(agent_id)
    return agent is None or agent.get("acceptsToolServers") is not False


def _default_agent(
    configured: str | None, harness_order: tuple[str, ...], catalog: LauncherCatalog
) -> str | None:
    """The role's configured agent; with none configured, the first harness the catalog offers."""

    if configured:
        return configured
    return next((harness for harness in harness_order if catalog.agent(harness)), None)


def _selection_refusal(
    catalog: LauncherCatalog,
    agent_id: str | None,
    model_id: str | None,
    effort_id: str | None,
) -> LaunchSelectionRefused | None:
    if not agent_id:
        return LaunchSelectionRefused(
            "agent_not_selected",
            "No agent is selected. Configure the AR role harness or choose an agent the Paseo "
            "runtime offers.",
        )
    agent = catalog.agent(agent_id)
    if agent is None:
        if catalog.discovery.get("source") == "unknown":
            return None
        return LaunchSelectionRefused(
            "agent_not_offered", f"The Paseo runtime does not offer agent {agent_id!r}."
        )
    if effort_id and not model_id:
        return LaunchSelectionRefused(
            "effort_requires_model", "An effort selection requires a model selection."
        )
    return _model_refusal(agent, model_id, effort_id) if model_id else None


def _model_refusal(
    agent: dict[str, Any], model_id: str, effort_id: str | None
) -> LaunchSelectionRefused | None:
    if agent.get("modelsKnown") is False:
        return None
    model = _named(agent["models"], model_id)
    if model is None:
        return LaunchSelectionRefused(
            "model_not_offered",
            f"The Paseo runtime does not offer model {model_id!r} for agent {agent['id']!r}.",
        )
    if effort_id and _part_known(model, "efforts") and _named(model["efforts"], effort_id) is None:
        return LaunchSelectionRefused(
            "effort_not_offered",
            f"The Paseo runtime does not offer effort {effort_id!r} for model {model_id!r}.",
        )
    return None


def _agents_from_reply(reply: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    providers = reply.get("providers")
    if not isinstance(providers, list):
        raise _unreadable_catalog()
    return tuple(_agent_row(provider) for provider in providers)


def _providers_without_tool_servers(reply: dict[str, Any]) -> frozenset[str]:
    return frozenset(
        provider["id"]
        for provider in reply.get("providers") or []
        if isinstance(provider, dict)
        and _is_text(provider.get("id"))
        and provider.get("acceptsToolServers") is False
    )


def _agent_row(provider: Any) -> dict[str, Any]:
    if not isinstance(provider, dict) or not _is_text(provider.get("id")):
        raise _unreadable_catalog()
    models = provider.get("models")
    if not isinstance(models, list):
        raise _unreadable_catalog()
    listing_error = provider.get("listingError")
    return {
        "id": provider["id"],
        "label": provider["label"] if _is_text(provider.get("label")) else provider["id"],
        "models": [_model_row(model) for model in models],
        "modelsKnown": provider.get("modelsKnown", True),
        "discovery": provider.get("discovery", {}),
        "acceptsToolServers": provider.get("acceptsToolServers"),
        "modesKnown": provider.get("modesKnown", isinstance(provider.get("modes"), list)),
        "modes": [dict(mode) for mode in provider.get("modes", [])],
        "defaultModeId": provider.get("defaultModeId"),
        "fetchedAt": provider.get("fetchedAt"),
        **({"listingError": listing_error} if _is_text(listing_error) else {}),
    }


def _model_row(model: Any) -> dict[str, Any]:
    if not isinstance(model, dict) or not _is_text(model.get("id")):
        raise _unreadable_catalog()
    efforts = model.get("efforts")
    if not isinstance(efforts, list):
        raise _unreadable_catalog()
    default_effort = model.get("defaultEffort")
    tiers = model.get("serviceTiers", [])
    if not isinstance(tiers, list):
        raise _unreadable_catalog()
    return {
        "id": model["id"],
        "label": model["label"] if _is_text(model.get("label")) else model["id"],
        **({"description": model["description"]} if _is_text(model.get("description")) else {}),
        **({"isDefault": True} if model.get("isDefault") is True else {}),
        "efforts": [_effort_row(effort) for effort in efforts],
        "discovery": model.get("discovery", {}),
        "serviceTiers": [_effort_row(tier) for tier in tiers],
        "features": [
            {**feature, "permissionBearing": feature["id"] not in NON_PERMISSION_FEATURES}
            for feature in model.get("features", [])
        ],
        **({"featureError": model["featureError"]} if _is_text(model.get("featureError")) else {}),
        **(
            {"serviceTierError": model["serviceTierError"]}
            if _is_text(model.get("serviceTierError"))
            else {}
        ),
        **({"defaultEffort": default_effort} if _is_text(default_effort) else {}),
    }


def _effort_row(effort: Any) -> dict[str, str]:
    if not isinstance(effort, dict) or not _is_text(effort.get("id")):
        raise _unreadable_catalog()
    return {
        "id": effort["id"],
        "label": effort["label"] if _is_text(effort.get("label")) else effort["id"],
    }


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _unreadable_catalog() -> PaseoBridgeFailure:
    return PaseoBridgeFailure(
        BRIDGE_INVALID_REPLY, "The Paseo bridge returned an unreadable catalog."
    )


def settings_role_launch(
    config: McpRuntimeConfig, repository_id: str, role: str = "review-presenter"
) -> tuple[str, dict[str, Any]]:
    """One settings role's harness and launch options, resolved as the normal seat path resolves them.

    MIK-R105 @ v4: the explainer's harness, model and effort are the settings role's, and its launch is
    the normal one. This lives beside the host-role and selection readers it uses, so no capability keeps
    a launch resolution of its own.
    """

    repository = config.repositories[repository_id]
    settings = load_agentic_settings(config.coordination_root, repo_root=repository.path)
    knobs = settings.resolved_role_knobs(role)
    defaults = {
        "agent": knobs.harness or settings.spawn_harness,
        "model": knobs.model,
        "effort": knobs.effort,
        "serviceTier": knobs.service_tier,
        "mode": knobs.mode,
        "featureValues": dict(knobs.feature_values),
        "notes": knobs.notes,
        "role": role,
        "notesKey": f"orchestration.roles.{role}.notes",
        "hostRole": read_host_role(config, role),
        "settingsSources": [str(path) for path in settings.sources],
        "fieldOrigins": {key: asdict(value) for key, value in knobs.origins.items()},
        "useHostDefaultMode": True,
    }
    return resolve_agent_selection(config, defaults, settings.harness_ids, None)


def _unknown_agent(agent_id: str) -> dict[str, Any]:
    return {"id": agent_id, "models": [], "modelsKnown": False, "modes": [], "modesKnown": False}


def _validate_features(
    features: dict[str, str | bool],
    sources: dict[str, Any],
    selection: tuple[str, str | None],
    model: dict[str, Any] | None,
    role: str | None,
) -> None:
    offered = model.get("features", []) if model else []
    for key, value in features.items():
        if not _part_known(model, "serviceTiers" if key == "service_tier" else "features"):
            continue
        valid, resolved = _feature_choice(_named(offered, key), value)
        if not valid:
            agent_id, model_id = selection
            raise LaunchSelectionRefused(
                "service_tier_not_offered" if key == "service_tier" else "feature_not_offered",
                f"Role {role}: {_choice_source(sources[key], f'featureValues.{key}', role)} ({'service tier' if key == 'service_tier' else 'feature'})={value!r} is not offered for {agent_id}/{model_id or 'native default'}; offered: {[(row['id'], [option['id'] for option in row.get('options', [])]) for row in offered]}",
            )
        features[key] = resolved
        sources[key]["value"] = resolved


def _feature_choice(
    definition: dict[str, Any] | None, value: str | bool
) -> tuple[bool, str | bool]:
    if definition is None:
        return False, value
    if definition["type"] == "toggle":
        return isinstance(value, bool), value
    option = _named(definition.get("options", []), value) if isinstance(value, str) else None
    return option is not None, option["id"] if option else value


def _settings_features(defaults: dict[str, Any], model: dict[str, Any] | None) -> dict[str, Any]:
    features = _canonical_features(defaults.get("featureValues", {}), model)
    tier = defaults.get("serviceTier")
    other = features.get("service_tier")
    if tier and other and _part_known(model, "serviceTiers"):
        definition = _named(model.get("features", []), "service_tier") if model else None
        first = _feature_choice(definition, tier)
        second = _feature_choice(definition, other)
        if not first[0] or not second[0]:
            key = "serviceTier" if not first[0] else "featureValues.service_tier"
            raise LaunchSelectionRefused(
                "service_tier_not_offered",
                f"{_settings_choice_source(defaults, key)} is not offered by Paseo",
            )
        if first[1] != second[1]:
            raise LaunchSelectionRefused(
                "conflicting_service_tier",
                f"{_settings_choice_source(defaults, 'serviceTier')} and "
                f"{_settings_choice_source(defaults, 'featureValues.service_tier')} name different Paseo choices",
            )
    if tier:
        features["service_tier"] = tier
    return features


def _canonical_agent_id(catalog: LauncherCatalog, value: str | None) -> str | None:
    matched = catalog.agent(value) if value else None
    return matched["id"] if matched is not None else value


def _canonical_features(values: dict[str, Any], model: dict[str, Any] | None) -> dict[str, Any]:
    offered = model.get("features", []) if model else []
    return {
        (row["id"] if (row := _named(offered, key)) is not None else key): value
        for key, value in values.items()
    }


def _check_permission_features(features: dict[str, Any], role: str | None) -> None:
    for key in features:
        if key not in NON_PERMISSION_FEATURES:
            raise LaunchSelectionRefused(
                "permission_feature_in_start",
                f"Role {role}: featureValues.{key} bears on permission; configure it in the user's settings or host profile, not a start call.",
            )


def _settings_choice_source(defaults: dict[str, Any], key: str) -> str:
    choice = {"source": "our settings", **_choice_origin("our settings", key, defaults, {})}
    return _choice_source(choice, key, defaults.get("role"))


def _model_with_features(
    config: McpRuntimeConfig,
    catalog: LauncherCatalog,
    agent: dict[str, Any],
    target: tuple[str, str | None],
) -> dict[str, Any] | None:
    model_id = target[1]
    model = (
        next((row for row in agent["models"] if row["id"] == model_id), None)
        if model_id
        else next((row for row in agent["models"] if row.get("isDefault")), None)
    )
    return selected_model_features(config, catalog, target, model, bridge_call)
