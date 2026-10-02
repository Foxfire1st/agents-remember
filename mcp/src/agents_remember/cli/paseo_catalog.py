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
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.orca_launcher import OrcaAgentOverride

_CATALOGS: dict[tuple[str, str, str, str], LauncherCatalog] = {}
_CATALOG_LOCK = threading.Lock()


@dataclass(frozen=True)
class LauncherCatalog:
    """One discovery of the runtime's catalog; ``origin`` changes with every discovery."""

    origin: str
    agents: tuple[dict[str, Any], ...]

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
        reply = bridge_call(config, "catalog", {"refresh": True} if refresh else {})
        catalog = LauncherCatalog(
            origin=f"paseo:{uuid.uuid4().hex}", agents=_agents_from_reply(reply)
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


def launcher_options(
    config: McpRuntimeConfig,
    defaults: dict[str, str | None],
    harness_order: tuple[str, ...],
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    """Assemble the catalog part of the launcher options response."""

    catalog = launcher_catalog(config, refresh=refresh)
    agent_id = _default_agent(defaults["agent"], harness_order, catalog)
    refusal = _selection_refusal(catalog, agent_id, defaults["model"], defaults["effort"])
    return {
        "roleDefaults": {
            "agent": agent_id,
            "model": defaults["model"],
            "effort": defaults["effort"],
            "available": refusal is None,
        },
        "agents": [dict(agent) for agent in catalog.agents],
        "catalogOrigin": catalog.origin,
    }


def resolve_agent_selection(
    config: McpRuntimeConfig,
    defaults: dict[str, str | None],
    harness_order: tuple[str, ...],
    override: OrcaAgentOverride | None,
) -> tuple[str, dict[str, str]]:
    """Validate a launch's agent, model and effort against the cached catalog.

    The role's model and effort defaults apply only while the agent is the role's default agent,
    and its effort default only while the model is the role's default model. Nothing is
    substituted: a value the catalog does not offer refuses the launch.
    """

    catalog = launcher_catalog(config)
    default_agent = _default_agent(defaults["agent"], harness_order, catalog)
    agent_id = override.agent_id if override else default_agent
    same_default_agent = agent_id == default_agent
    model_id = (override.model_id if override and override.model_id else None) or (
        defaults["model"] if same_default_agent else None
    )
    effort_id = (override.effort_id if override and override.effort_id else None) or (
        defaults["effort"] if same_default_agent and model_id == defaults["model"] else None
    )
    refusal = _selection_refusal(catalog, agent_id, model_id, effort_id)
    if refusal is not None:
        raise refusal
    assert agent_id is not None
    options = {key: value for key, value in (("model", model_id), ("effort", effort_id)) if value}
    return agent_id, options


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
        **({"listingError": listing_error} if _is_text(listing_error) else {}),
    }


def _model_row(model: Any) -> dict[str, Any]:
    if not isinstance(model, dict) or not _is_text(model.get("id")):
        raise _unreadable_catalog()
    efforts = model.get("efforts")
    if not isinstance(efforts, list):
        raise _unreadable_catalog()
    default_effort = model.get("defaultEffort")
    return {
        "id": model["id"],
        "label": model["label"] if _is_text(model.get("label")) else model["id"],
        **({"description": model["description"]} if _is_text(model.get("description")) else {}),
        **({"isDefault": True} if model.get("isDefault") is True else {}),
        "efforts": [_effort_row(effort) for effort in efforts],
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
