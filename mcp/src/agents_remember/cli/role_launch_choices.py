"""Project host-observed launch choices without overwriting requested provenance."""

from __future__ import annotations

from typing import Any


def observe_launch_choices(receipt: dict[str, Any], applied: dict[str, Any] | None) -> None:
    if not applied:
        return
    step = receipt.get("stepUp")
    if isinstance(step, dict) and applied.get("model") is not None:
        receipt["stepUp"] = {
            **step,
            "requestedModel": step.get("requestedModel", step.get("model")),
            "model": applied["model"],
        }
    if isinstance(receipt.get("choices"), dict):
        _observe_choices(receipt, applied)


def _observe_choices(receipt: dict[str, Any], applied: dict[str, Any]) -> None:
    for key in ("model", "effort", "mode"):
        choice = receipt["choices"].get(key)
        if isinstance(choice, dict):
            choice["observedValue"] = applied.get(key)
            if choice.get("source") == "host default":
                choice["value"] = applied.get(key)
    for key, choice in receipt["choices"].get("featureValues", {}).items():
        choice["observedValue"] = applied.get("featureValues", {}).get(key)
    warnings = []
    for key, choice in [
        ("mode", receipt["choices"].get("mode", {})),
        *[
            (f"featureValues.{key}", value)
            for key, value in receipt["choices"].get("featureValues", {}).items()
        ],
    ]:
        if choice.get("source") not in ("host default", "none", None) and choice.get(
            "value"
        ) != choice.get("observedValue"):
            warnings.append(
                f"{key} stated {choice.get('value')!r}, but the host reported {choice.get('observedValue')!r}."
            )
    if warnings:
        receipt["warning"] = " ".join(filter(None, (receipt.get("warning"), *warnings)))
