"""Claude list_models parsing into the normalized harness capability contract."""

from __future__ import annotations

from collections.abc import Mapping

from agents_remember.errors import HarnessControlError
from agents_remember.serving.harness_capabilities import (
    CapabilitySnapshot,
    EffortOption,
    ModelCapability,
)


def parse_list_models_response(
    frame: Mapping[str, object],
    request_id: str,
    *,
    current_model: str,
    requested_key: str | None = None,
) -> CapabilitySnapshot | None:
    raw_models = _list_models_payload(frame, request_id)
    if raw_models is None:
        return None
    models = tuple(_parse_model(value, index=index) for index, value in enumerate(raw_models))
    _require_unique_model_keys(models)
    selected = _select_current_model(models, current_model, requested_key=requested_key)
    return CapabilitySnapshot(
        models=models,
        selected_model_key=selected.key,
        selected_effort=None,
    )


def _list_models_payload(frame: Mapping[str, object], request_id: str) -> list[object] | None:
    if frame.get("type") != "control_response":
        return None
    response = _mapping(frame.get("response"), "Claude list_models response")
    if response.get("request_id") != request_id:
        return None
    if response.get("subtype") != "success":
        raise HarnessControlError("Claude list_models control request was rejected")
    payload = _mapping(response.get("response"), "Claude list_models payload")
    raw_models = payload.get("models")
    if not isinstance(raw_models, list):
        raise HarnessControlError("Claude list_models response lacks a models array")
    return raw_models


def _parse_model(value: object, *, index: int) -> ModelCapability:
    model = _mapping(value, f"Claude list_models models[{index}]")
    key = _required_text(model, "value", "Claude model")
    supports_effort = _optional_bool(model, "supportsEffort", default=False)
    raw_levels = model.get("supportedEffortLevels", [])
    if not isinstance(raw_levels, list) or not all(
        isinstance(level, str) and level for level in raw_levels
    ):
        raise HarnessControlError(
            "Claude model supportedEffortLevels must be non-empty text values"
        )
    if supports_effort != bool(raw_levels):
        raise HarnessControlError("Claude model supportsEffort contradicts supportedEffortLevels")
    levels = tuple(raw_levels) if supports_effort else ()
    disabled = _optional_bool(model, "disabled", default=False)
    return ModelCapability(
        key=key,
        display_name=_required_text(model, "displayName", "Claude model"),
        resolved_model=_optional_text_field(model, "resolvedModel", "Claude model"),
        description=_required_text(model, "description", "Claude model"),
        supports_effort=supports_effort,
        effort_options=tuple(EffortOption(key=level, display_name=level) for level in levels),
        default_effort=None,
        is_default=key == "default",
        selectable=not disabled,
    )


def _require_unique_model_keys(models: tuple[ModelCapability, ...]) -> None:
    seen: set[str] = set()
    for model in models:
        if model.key in seen:
            raise HarnessControlError(f"Claude list_models repeated model value {model.key!r}")
        seen.add(model.key)


def _select_current_model(
    models: tuple[ModelCapability, ...],
    current_model: str,
    *,
    requested_key: str | None = None,
) -> ModelCapability:
    exact = next((model for model in models if model.key == current_model), None)
    resolved = [model for model in models if model.resolved_model == current_model]
    # Claude's catalog aliases several keys onto one resolved model (e.g. ``default`` and
    # ``opus[1m]`` both resolve to ``claude-opus-4-8[1m]``). The running harness echoes only the
    # resolved id, so when a launch explicitly requested one of those alias keys the requested key
    # wins over the default collapse — otherwise a natively-succeeding ``opus[1m]`` launch would map
    # back to ``default`` and be refused. See notes/260721-halftime-codex-open-diagnosis.md item 2.
    requested = next(
        (model for model in resolved if requested_key is not None and model.key == requested_key),
        None,
    )
    selected = (
        exact or requested or next((model for model in resolved if model.is_default), None)
    )
    if selected is None and len(resolved) == 1:
        selected = resolved[0]
    if selected is None:
        raise HarnessControlError(
            f"Claude current model {current_model!r} is absent from list_models"
        )
    return selected


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise HarnessControlError(f"{label} must be an object")
    return value


def _required_text(raw: Mapping[str, object], key: str, label: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise HarnessControlError(f"{label} requires non-empty {key}")
    return value


def _optional_text_field(raw: Mapping[str, object], key: str, label: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise HarnessControlError(f"{label} {key} must be non-empty text when present")
    return value


def _optional_bool(raw: Mapping[str, object], key: str, *, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise HarnessControlError(f"Claude model {key} must be boolean when present")
    return value
