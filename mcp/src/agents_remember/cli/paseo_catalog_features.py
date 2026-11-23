"""Ask Paseo for one model's features and retain its last answer."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from agents_remember.cli.paseo_bridge import PaseoBridgeFailure, bridge_call, require_bridge_runtime
from agents_remember.cli.paseo_catalog_store import catalog_store_path, merge_selected_parts
from agents_remember.kernel._agentic_settings_core import NON_PERMISSION_FEATURES

if TYPE_CHECKING:
    from agents_remember.cli.paseo_catalog import LauncherCatalog
    from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig


def public_model_features(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {**feature, "permissionBearing": feature["id"] not in NON_PERMISSION_FEATURES}
        for feature in features
    ]


def launcher_model_options(
    config: McpRuntimeConfig, provider: str, model: str, scope: tuple[str, ...]
) -> dict[str, Any]:
    answer = _question_features(config, (provider, model), scope, bridge_call)
    return {
        "agentId": provider,
        "modelId": model,
        **answer,
        "features": public_model_features(answer["features"]),
    }


def selected_model_features(
    config: McpRuntimeConfig,
    catalog: LauncherCatalog,
    selection: tuple[str, str | None],
    model: dict[str, Any] | None,
    question: Callable[..., dict[str, Any]],
) -> dict[str, Any] | None:
    if catalog.model_options == "metadata":
        return None
    target_model = model["id"] if model is not None else selection[1]
    if target_model is None or _fresh_features(model):
        return model
    target = (selection[0], target_model)
    if target not in catalog.feature_answers:
        catalog.feature_answers[target] = _question_features(
            config, target, catalog.scope, question
        )
    result = catalog.feature_answers[target]
    return {
        **(model or {"id": target_model}),
        **result,
        "discovery": {
            **({} if model is None else model.get("discovery", {})),
            **result["discovery"],
        },
    }


def _fresh_features(model: dict[str, Any] | None) -> bool:
    return (
        model is not None
        and model.get("discovery", {}).get("features", {}).get("source") == "fresh"
    )


def _question_features(
    config: McpRuntimeConfig,
    target: tuple[str, str],
    scope: tuple[str, ...],
    question: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    # Both this question and metadata use the same scope and part store; no second offer cache.
    error = None
    try:
        reply = question(
            config,
            "catalog",
            {
                "cwd": config.workspace_root.as_posix(),
                "mode": "selected",
                "selectedProvider": target[0],
                "selectedModel": target[1],
            },
        )
    except PaseoBridgeFailure as failure:
        reply, error = None, str(failure)
    return merge_selected_parts(
        catalog_store_path(require_bridge_runtime(config).home),
        scope,
        target,
        reply,
        error,
    )
