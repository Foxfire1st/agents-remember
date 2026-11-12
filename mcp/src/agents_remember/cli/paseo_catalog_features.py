"""Ask only for the finally selected model's features during a start."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from agents_remember.cli.paseo_bridge import PaseoBridgeFailure, require_bridge_runtime
from agents_remember.cli.paseo_catalog_store import catalog_store_path, merge_selected_parts

if TYPE_CHECKING:
    from agents_remember.cli.paseo_catalog import LauncherCatalog
    from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig


def selected_model_features(
    config: McpRuntimeConfig,
    catalog: LauncherCatalog,
    selection: tuple[str, str | None],
    model: dict[str, Any] | None,
    question: Callable[..., dict[str, Any]],
) -> dict[str, Any] | None:
    target_model = model["id"] if model is not None else selection[1]
    if target_model is None or catalog.mode == "full" or _fresh_features(model):
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
