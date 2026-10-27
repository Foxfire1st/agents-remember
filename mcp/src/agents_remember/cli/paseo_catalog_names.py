"""Match launch choice ids or labels directly against Paseo's own rows."""

from __future__ import annotations

from typing import Any

from agents_remember.kernel.primitives.named_choices import named


def part_known(row: dict[str, Any] | None, part: str) -> bool:
    return row is not None and row.get("discovery", {}).get(part, {}).get("source") != "unknown"


def canonical_fields(
    agents: Any, values: dict[str, Any], keys: tuple[str, str, str]
) -> dict[str, Any]:
    result = dict(values)
    agent = named(agents, values[keys[0]]) if values.get(keys[0]) else None
    model = named(agent["models"], values[keys[1]]) if agent and values.get(keys[1]) else None
    effort = named(model["efforts"], values[keys[2]]) if model and values.get(keys[2]) else None
    for key, row in zip(keys, (agent, model, effort), strict=True):
        if row is not None:
            result[key] = row["id"]
    return result


def canonical_defaults(agents: Any, defaults: dict[str, Any]) -> dict[str, Any]:
    result = canonical_fields(agents, defaults, ("agent", "model", "effort"))
    result["fieldOrigins"] = {
        key: canonical_fields(agents, origin, ("provider", "model", "effort"))
        for key, origin in defaults.get("fieldOrigins", {}).items()
    }
    return result


def canonical_model_choices(
    agent: dict[str, Any] | None, model_id: str | None, effort_id: str | None
) -> tuple[str | None, str | None]:
    model = named(agent["models"], model_id) if agent and model_id else None
    if model is None:
        return model_id, effort_id
    effort = named(model["efforts"], effort_id) if effort_id else None
    return model["id"], effort["id"] if effort else effort_id
