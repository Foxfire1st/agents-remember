"""Separate successful catalog parts from unanswered ones before persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agents_remember.cli.paseo_catalog_store import CatalogParts


def merge_reply(parts: CatalogParts, reply: dict[str, Any], error: str | None) -> dict[str, Any]:
    raw = reply.get("providers")
    providers, state = parts.observe(
        ("providers",),
        _identities(raw),
        valid=_rows(raw) and error is None,
        error=error or (None if _rows(raw) else "Paseo did not answer its provider catalog"),
    )
    fresh = _by_id(raw) if state["source"] == "fresh" else {}
    if state["source"] == "fresh":
        parts.retain_children(("provider",), set(fresh), ("providers",))
    return {
        "providers": [_provider(parts, row, fresh.get(row["id"])) for row in providers or []],
        "catalogDiscovery": state,
    }


def _provider(
    parts: CatalogParts, identity: dict[str, Any], fresh: dict[str, Any] | None
) -> dict[str, Any]:
    row = fresh or {}
    key = ("provider", identity["id"])
    models, model_state = parts.observe(
        (*key, "models"),
        _identities(row.get("models")),
        valid=_rows(row.get("models")) and not row.get("listingError"),
        error=row.get("listingError"),
    )
    modes, mode_state = parts.observe(
        (*key, "modes"),
        {"rows": row.get("modes"), "defaultModeId": row.get("defaultModeId")},
        valid=_rows(row.get("modes")) and not row.get("modeError"),
        error=row.get("modeError"),
    )
    accepts, tool_state = parts.observe(
        (*key, "acceptsToolServers"),
        row.get("acceptsToolServers"),
        valid=isinstance(row.get("acceptsToolServers"), bool) and not row.get("toolServerError"),
        error=row.get("toolServerError"),
    )
    raw_models = _by_id(row.get("models")) if model_state["source"] == "fresh" else {}
    if model_state["source"] == "fresh":
        parts.retain_children((*key, "model"), set(raw_models), (*key, "models"))
    return {
        **identity,
        "models": [
            _model(parts, key, model, raw_models.get(model["id"])) for model in models or []
        ],
        "modelsKnown": model_state["source"] != "unknown",
        "modes": [] if modes is None else modes["rows"],
        "modesKnown": mode_state["source"] != "unknown",
        "defaultModeId": None if modes is None else modes.get("defaultModeId"),
        "acceptsToolServers": accepts,
        "discovery": {"models": model_state, "modes": mode_state, "acceptsToolServers": tool_state},
        **({"listingError": row["listingError"]} if row.get("listingError") else {}),
    }


def _model(
    parts: CatalogParts,
    provider: tuple[str, ...],
    identity: dict[str, Any],
    fresh: dict[str, Any] | None,
) -> dict[str, Any]:
    row = fresh or {}
    key = (*provider, "model", identity["id"])
    result = dict(identity)
    states: dict[str, Any] = {}
    for field, error_key in (
        ("efforts", "effortError"),
        ("features", "featureError"),
        ("serviceTiers", "serviceTierError"),
    ):
        value, states[field] = parts.observe(
            (*key, field),
            row.get(field),
            valid=_rows(row.get(field)) and not row.get(error_key),
            error=row.get(error_key),
        )
        result[field] = value or []
        if row.get(error_key):
            result[error_key] = row[error_key]
    result["discovery"] = states
    return result


def _rows(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"] for row in value
    )


def _identities(value: Any) -> list[dict[str, Any]]:
    if not _rows(value):
        return []
    return [
        {
            key: row[key]
            for key in ("id", "label", "description", "isDefault", "defaultEffort", "fetchedAt")
            if key in row
        }
        for row in value
    ]


def _by_id(value: Any) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in value} if _rows(value) else {}


def merge_selected_reply(
    parts: CatalogParts,
    target: tuple[str, str],
    reply: dict[str, Any],
    error: str | None,
) -> dict[str, Any]:
    provider, model = target
    selected = reply.get("selectedFeatures")
    valid = (
        isinstance(selected, dict)
        and selected.get("provider") == provider
        and selected.get("model") == model
    )
    row = selected if valid and error is None else {}
    if error is not None:
        row = {"featureError": error, "serviceTierError": error}
    resolved = _model(parts, ("provider", provider), {"id": model}, row)
    return {
        "features": resolved["features"],
        "serviceTiers": resolved["serviceTiers"],
        "discovery": {key: resolved["discovery"][key] for key in ("features", "serviceTiers")},
    }
