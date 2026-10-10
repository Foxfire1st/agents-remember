"""The launcher's agent, model and effort catalog, taken from the Paseo runtime.

The catalog is whatever the runtime reports through the bridge's ``catalog`` command: its ready and
enabled providers, each with its models and their thinking options. Agents Remember keeps no model
table of its own. One catalog is cached per Paseo runtime in this process; it is loaded when the
cache is empty and rediscovered only on an explicit refresh. Role defaults and launch requests are
checked against that cached catalog and are never replaced by another value.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Any

from agents_remember.cli.paseo_bridge import (
    BRIDGE_INVALID_REPLY,
    PaseoBridgeFailure,
    bridge_call,
    require_bridge_runtime,
)
from agents_remember.cli.role_instructions import launch_choice_metadata
from agents_remember.kernel._agentic_settings_core import (
    NON_PERMISSION_FEATURES,
    native_service_tier,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import RoleAgentOverride

_CATALOGS: dict[tuple[str, str, str, str], LauncherCatalog] = {}
_CATALOG_LOCK = threading.Lock()


@dataclass(frozen=True)
class LauncherCatalog:
    """One discovery of the runtime's catalog; ``origin`` changes with every discovery."""

    origin: str
    agents: tuple[dict[str, Any], ...]
    # The providers the runtime reports as not accepting tool servers from their host.
    without_tool_servers: frozenset[str] = frozenset()

    def agent(self, agent_id: str) -> dict[str, Any] | None:
        return next((agent for agent in self.agents if agent["id"] == agent_id), None)


class LaunchSelectionRefused(ValueError):
    """The requested agent, model or effort is not one the cached catalog offers."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def launcher_catalog(config: McpRuntimeConfig, *, refresh: bool = False) -> LauncherCatalog:
    """Return the cached catalog of the configured runtime, discovering it when needed.

    A failed discovery raises :class:`PaseoBridgeFailure` and caches nothing; a catalog cached
    earlier stays in place.
    """

    scope = paseo_catalog_scope(config)
    with _CATALOG_LOCK:
        cached = _CATALOGS.get(scope)
        if cached is not None and not refresh:
            return cached
        reply = bridge_call(
            config,
            "catalog",
            {"cwd": config.workspace_root.as_posix(), **({"refresh": True} if refresh else {})},
        )
        catalog = LauncherCatalog(
            origin=f"paseo:{uuid.uuid4().hex}",
            agents=_agents_from_reply(reply),
            without_tool_servers=_providers_without_tool_servers(reply),
        )
        _CATALOGS[scope] = catalog
        return catalog


def paseo_catalog_scope(config: McpRuntimeConfig) -> tuple[str, str, str, str]:
    """The cache key of one Paseo runtime: its install, daemon home, listen address and version."""

    settings = require_bridge_runtime(config)
    return (
        settings.install_prefix.as_posix(),
        settings.home.as_posix(),
        settings.listen,
        settings.version,
    )


def forget_launcher_catalogs() -> None:
    """Empty the process cache; the next options call or launch discovers the catalog again."""

    with _CATALOG_LOCK:
        _CATALOGS.clear()


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
    catalog = launcher_catalog(config, refresh=refresh)
    try:
        agent_id, options = resolve_agent_selection(config, defaults, harness_order, None)
        role_defaults = {
            "agent": agent_id,
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
    }


def resolve_agent_selection(
    config: McpRuntimeConfig,
    defaults: dict[str, Any],
    harness_order: tuple[str, ...],
    override: RoleAgentOverride | None,
) -> tuple[str, dict[str, Any]]:
    """Resolve fields by source, keeping lower choices bound to their provider/model."""
    catalog = launcher_catalog(config)
    settings_agent = _default_agent(defaults["agent"], harness_order, catalog)
    profile_read = defaults.get("hostRole", {})
    profile = profile_read.get("profile", {})
    host_agent = profile.get("provider") or None
    call = override.model_dump(by_alias=True, exclude_none=True) if override else {}
    call_features = call.get("featureValues", {})
    for key in call_features:
        if key not in NON_PERMISSION_FEATURES:
            raise LaunchSelectionRefused(
                "permission_feature_in_start",
                f"Role {defaults.get('role')}: featureValues.{key} bears on permission; configure it in the user's settings or host profile, not a start call.",
            )
    agent_id = call.get("agentId") or host_agent or settings_agent
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
    agent = catalog.agent(agent_id)
    assert agent is not None
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
    mode, choices["mode"] = _resolve_mode(mode, choices["mode"], agent, defaults.get("role"))
    model = (
        next((row for row in agent["models"] if row["id"] == model_id), None)
        if model_id
        else next((row for row in agent["models"] if row.get("isDefault")), None)
    )
    settings_features = dict(defaults.get("featureValues", {}))
    if defaults.get("serviceTier"):
        settings_features["service_tier"] = native_service_tier(defaults["serviceTier"])
    layers = [
        (
            settings_features,
            "our settings",
            settings_agent,
            defaults["model"],
            defaults.get("fieldOrigins", {}),
        ),
        (
            profile.get("featureValues") or {},
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
        offered=model.get("features", []) if model else [],
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
    return agent_id, options


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
    mode: str | None, choice: dict[str, Any], agent: dict[str, Any], role: str | None
) -> tuple[str | None, dict[str, Any]]:
    if agent.get("listingError") or agent.get("modesKnown") is False:
        raise LaunchSelectionRefused(
            "modes_unknown",
            f"Role {role}: modes for {agent['id']} are not known: {agent.get('listingError') or 'the host did not return its mode catalog'}; refresh the host catalog before starting.",
        )
    modes = agent["modes"]
    if mode is None:
        safe = {"claude": "default", "codex": "auto"}.get(agent["id"])
        mode = safe if any(row["id"] == safe for row in modes) else None
        choice = {
            "value": mode,
            "source": "least permissive mode" if mode else "none",
            "reason": "least permissive supported mode for this agent kind"
            if mode
            else "no named least working mode offered for this agent kind",
        }
    elif not any(row["id"] == mode for row in modes):
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
    offered: list[dict[str, Any]],
    role: str | None,
) -> tuple[dict[str, str | bool], dict[str, Any], list[dict[str, Any]]]:
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
    for key, value in features.items():
        definition = next((row for row in offered if row["id"] == key), None)
        valid = definition is not None and (
            isinstance(value, bool)
            if definition["type"] == "toggle"
            else isinstance(value, str) and any(row["id"] == value for row in definition["options"])
        )
        if not valid:
            raise LaunchSelectionRefused(
                "service_tier_not_offered" if key == "service_tier" else "feature_not_offered",
                f"Role {role}: {_choice_source(feature_sources[key], f'featureValues.{key}', role)} ({'service tier' if key == 'service_tier' else 'feature'})={value!r} is not offered for {agent_id}/{model_id or 'native default'}; offered: {[(row['id'], [option['id'] for option in row.get('options', [])]) for row in offered]}",
            )
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

    The answer is the cached catalog's, the one the launch was validated against. Only an
    explicit report of the runtime withholds the tool server from a provider.
    """

    return agent_id not in launcher_catalog(config).without_tool_servers


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
    model = next((item for item in agent["models"] if item["id"] == model_id), None)
    if model is None:
        return LaunchSelectionRefused(
            "model_not_offered",
            f"The Paseo runtime does not offer model {model_id!r} for agent {agent['id']!r}.",
        )
    if effort_id and not any(effort["id"] == effort_id for effort in model["efforts"]):
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
        "modesKnown": isinstance(provider.get("modes"), list),
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
