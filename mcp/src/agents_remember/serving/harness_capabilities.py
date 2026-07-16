"""Normalized model and effort capabilities shared by native harness adapters.

The config-option projection deliberately adopts ACP's category-keyed shape without
introducing an ACP transport dependency.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, cast

CapabilityCategory = Literal["model", "thought_level"]
SetAcceptance = Literal[
    "echo-verified",
    "immediate",
    "queued",
    "unknown",
    "unsupported",
]
SET_ACCEPTANCE_VALUES = frozenset(
    {"echo-verified", "immediate", "queued", "unknown", "unsupported"}
)


@dataclass(frozen=True)
class EffortOption:
    """One vendor token accepted by one specific model."""

    key: str
    display_name: str
    description: str | None = None
    launch_settable: bool = True
    session_settable: bool = True


@dataclass(frozen=True)
class ModelCapability:
    """One dynamically advertised model with its model-gated effort menu."""

    key: str
    display_name: str
    effort_options: tuple[EffortOption, ...]
    default_effort: str | None
    resolved_model: str | None = None
    description: str | None = None
    supports_effort: bool = False
    is_default: bool = False
    hidden: bool = False
    selectable: bool = True
    provider: str | None = None


@dataclass(frozen=True)
class ConfigSelectOption:
    """The value/name/description select-option shape used by SessionConfigOption."""

    value: str
    name: str
    description: str | None = None


@dataclass(frozen=True)
class SessionConfigOption:
    """ACP Sense 1 select config shape, independent of ACP transport."""

    config_id: str
    category: CapabilityCategory
    name: str
    options: tuple[ConfigSelectOption, ...]
    current_value: str
    description: str | None = None


@dataclass(frozen=True)
class CapabilitySnapshot:
    """Full dynamic catalog plus the current model and effort, when known."""

    models: tuple[ModelCapability, ...]
    selected_model_key: str | None
    selected_effort: str | None

    @property
    def config_options(self) -> tuple[SessionConfigOption, ...]:
        """Project the catalog into category-keyed options without duplicating state."""

        if self.selected_model_key is None:
            return ()
        visible_models = tuple(
            model
            for model in self.models
            if model.key == self.selected_model_key or (model.selectable and not model.hidden)
        )
        options = [
            SessionConfigOption(
                config_id="model",
                category="model",
                name="Model",
                description="Model used by this harness session",
                current_value=self.selected_model_key,
                options=tuple(
                    ConfigSelectOption(
                        value=model.key,
                        name=model.display_name,
                        description=model.description,
                    )
                    for model in visible_models
                ),
            )
        ]
        selected = next(
            (model for model in self.models if model.key == self.selected_model_key),
            None,
        )
        if selected is not None and self.selected_effort is not None and selected.effort_options:
            options.append(
                SessionConfigOption(
                    config_id="effort",
                    category="thought_level",
                    name="Effort",
                    description="Reasoning or thinking effort for the selected model",
                    current_value=self.selected_effort,
                    options=tuple(
                        ConfigSelectOption(
                            value=effort.key,
                            name=effort.display_name,
                            description=effort.description,
                        )
                        for effort in selected.effort_options
                    ),
                )
            )
        return tuple(options)


@dataclass(frozen=True)
class LaunchKnobs:
    """Additive native launch material plus selectors the adapter exclusively owns.

    ``owned_*`` makes free-form launch arguments fail on conflicts instead of silently overriding
    or being overridden by the normalized selection.
    """

    argv: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    session_config: Mapping[str, object] = field(default_factory=dict)
    owned_argv_options: tuple[str, ...] = ()
    owned_config_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class SetResult:
    """Honest model/effort mutation evidence consumed by L3 and serving clients."""

    ok: bool
    acceptance: SetAcceptance
    requested_value: str
    effective_value: str | None = None
    detail: str | None = None


def capability_snapshot_json(value: CapabilitySnapshot) -> dict[str, object]:
    return {
        "models": [model_capability_json(model) for model in value.models],
        "selectedModelKey": value.selected_model_key,
        "selectedEffort": value.selected_effort,
        "configOptions": [config_option_json(option) for option in value.config_options],
    }


def model_capability_json(value: ModelCapability) -> dict[str, object]:
    return {
        "key": value.key,
        "displayName": value.display_name,
        "resolvedModel": value.resolved_model,
        "description": value.description,
        "supportsEffort": value.supports_effort,
        "effortOptions": [effort_option_json(option) for option in value.effort_options],
        "defaultEffort": value.default_effort,
        "isDefault": value.is_default,
        "hidden": value.hidden,
        "selectable": value.selectable,
        "provider": value.provider,
    }


def effort_option_json(value: EffortOption) -> dict[str, object]:
    return {
        "key": value.key,
        "displayName": value.display_name,
        "description": value.description,
        "launchSettable": value.launch_settable,
        "sessionSettable": value.session_settable,
    }


def config_option_json(value: SessionConfigOption) -> dict[str, object]:
    return {
        "type": "select",
        "id": value.config_id,
        "category": value.category,
        "name": value.name,
        "description": value.description,
        "currentValue": value.current_value,
        "options": [
            {
                "value": option.value,
                "name": option.name,
                "description": option.description,
            }
            for option in value.options
        ],
    }


def set_result_json(value: SetResult) -> dict[str, object]:
    if value.acceptance not in SET_ACCEPTANCE_VALUES:
        raise ValueError(
            f"adapter set result has unsupported acceptance {value.acceptance!r}"
        )
    return {
        "ok": value.ok,
        "acceptance": value.acceptance,
        "requestedValue": value.requested_value,
        "effectiveValue": value.effective_value,
        "detail": value.detail,
    }


def capability_snapshot_from_json(raw: object) -> CapabilitySnapshot:
    """Strictly parse the normalized snapshot returned by an exact-session IPC peer."""

    payload = _object(raw, "capability snapshot")
    models_raw = payload.get("models")
    if not isinstance(models_raw, list):
        raise ValueError("capability snapshot models must be a list")
    models = tuple(_model_capability_from_json(item) for item in models_raw)
    snapshot = CapabilitySnapshot(
        models=models,
        selected_model_key=_optional_text(payload, "selectedModelKey"),
        selected_effort=_optional_text(payload, "selectedEffort"),
    )
    config_options = payload.get("configOptions")
    if config_options is not None and config_options != [
        config_option_json(option) for option in snapshot.config_options
    ]:
        raise ValueError("capability snapshot configOptions do not match the model-gated catalog")
    return snapshot


def set_result_from_json(raw: object) -> SetResult:
    """Strictly parse honest setter acceptance returned by the exact-session IPC peer."""

    payload = _object(raw, "set result")
    ok = payload.get("ok")
    if not isinstance(ok, bool):
        raise ValueError("set result ok must be a boolean")
    acceptance = payload.get("acceptance")
    if acceptance not in SET_ACCEPTANCE_VALUES:
        raise ValueError("set result acceptance is invalid")
    return SetResult(
        ok=ok,
        acceptance=cast(SetAcceptance, acceptance),
        requested_value=_required_text(payload, "requestedValue"),
        effective_value=_optional_text(payload, "effectiveValue"),
        detail=_optional_text(payload, "detail"),
    )


def _model_capability_from_json(raw: object) -> ModelCapability:
    payload = _object(raw, "model capability")
    efforts_raw = payload.get("effortOptions")
    if not isinstance(efforts_raw, list):
        raise ValueError("model capability effortOptions must be a list")
    return ModelCapability(
        key=_required_text(payload, "key"),
        display_name=_required_text(payload, "displayName"),
        resolved_model=_optional_text(payload, "resolvedModel"),
        description=_optional_text(payload, "description"),
        supports_effort=_required_bool(payload, "supportsEffort"),
        effort_options=tuple(_effort_option_from_json(item) for item in efforts_raw),
        default_effort=_optional_text(payload, "defaultEffort"),
        is_default=_required_bool(payload, "isDefault"),
        hidden=_required_bool(payload, "hidden"),
        selectable=_required_bool(payload, "selectable"),
        provider=_optional_text(payload, "provider"),
    )


def _effort_option_from_json(raw: object) -> EffortOption:
    payload = _object(raw, "effort option")
    return EffortOption(
        key=_required_text(payload, "key"),
        display_name=_required_text(payload, "displayName"),
        description=_optional_text(payload, "description"),
        launch_settable=_required_bool(payload, "launchSettable"),
        session_settable=_required_bool(payload, "sessionSettable"),
    )


def _object(raw: object, label: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping) or not all(isinstance(key, str) for key in raw):
        raise ValueError(f"{label} must be an object")
    return cast(Mapping[str, object], raw)


def _required_text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"capability payload requires non-empty {key}")
    return value


def _optional_text(raw: Mapping[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"capability payload {key} must be a string or null")
    return value


def _required_bool(raw: Mapping[str, object], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"capability payload {key} must be a boolean")
    return value
