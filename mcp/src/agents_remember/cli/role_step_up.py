"""Select one user-owned step-up from task review facts, without mutating a seat."""

from __future__ import annotations

import re
from typing import Any

from agents_remember.cli.role_instructions import launch_choice_metadata
from agents_remember.errors import AgentsRememberError
from agents_remember.tasks import TaskDocument
from agents_remember.worktrees.review_history import failed_role_tries

LAUNCH_FIELDS = ("model", "effort", "serviceTier", "mode", "featureValues", "notes")


def step_up_note(receipt: dict[str, Any]) -> str:
    step = receipt.get("stepUp")
    if not step:
        return ""
    return f" Stepped-up · {step.get('model') or 'host default'}; {step['source']}."


def require_step_up_reason(reason: str) -> str:
    text = reason.strip()
    if not text or len(text) > 1000 or "\n" in text or re.search(r"[.!?]\s+\S", text):
        raise AgentsRememberError(
            "step_up must give a reason in one sentence (at most 1000 characters)."
        )
    return text


def select_step_up(
    defaults: dict[str, Any],
    document: TaskDocument | None,
    *,
    reason: str | None = None,
    prior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A step-up inherits nothing from a different provider/model's launch choices."""
    role = defaults["role"]
    row = defaults.get("stepUp")
    if reason is not None:
        reason = require_step_up_reason(reason)
        if row is None:
            raise AgentsRememberError(
                f"No step-up is configured; write orchestration.roles.{role}.stepUp in the user's settings."
            )
    if row is None:
        return {
            **defaults,
            **(
                {
                    "stepUpWarning": "The settings no longer name a step-up; this start takes the role's own choices."
                }
                if prior
                else {}
            ),
        }
    tries = failed_role_tries(document, role) if document is not None else 0
    threshold = row.get("afterTries", 3 if role in {"worker", "curator"} else None)
    if reason is None and not prior and (threshold is None or tries is None or tries < threshold):
        return defaults
    source = (
        f"step-up requested: {reason}"
        if reason
        else prior.get("source")
        if prior
        else f"step-up after {tries} tries"
    )
    provider = row.get("harness") or defaults.get("agent")
    same_scope = provider == defaults.get("agent") and row.get(
        "model", defaults.get("model")
    ) == defaults.get("model")
    selected = dict(defaults)
    selected.update(
        {key: row.get(key, defaults.get(key) if same_scope else None) for key in LAUNCH_FIELDS}
    )
    selected["agent"] = provider
    selected["featureValues"] = dict(selected.get("featureValues") or {})
    if "service_tier" in (row.get("featureValues") or {}) and not row.get("serviceTier"):
        selected["serviceTier"] = None
    selected["hostRole"] = {}
    selected["useHostDefaultMode"] = not same_scope and row.get("mode") is None
    selected["fieldOrigins"] = _step_up_origins(defaults, row, selected, same_scope)
    selected["notesKey"] = (
        selected["fieldOrigins"]
        .get("notes", {})
        .get("key", f"orchestration.roles.{role}.stepUp.notes")
    )
    selected["stepUpSelection"] = {
        "source": source,
        "tries": tries,
        "reason": reason,
        "role": role,
        "from": {
            "agent": defaults.get("agent"),
            **{key: defaults.get(key) for key in LAUNCH_FIELDS},
            "notes": launch_choice_metadata(
                {
                    "notes": {
                        "value": defaults["notes"],
                        "source": "our settings",
                        "key": defaults.get("notesKey", f"orchestration.roles.{role}.notes"),
                    }
                }
            )["notes"]
            if defaults.get("notes") is not None
            else None,
        },
    }
    return selected


def _step_up_origins(
    defaults: dict[str, Any], row: dict[str, Any], selected: dict[str, Any], same_scope: bool
) -> dict[str, Any]:
    origins = dict(defaults.get("fieldOrigins", {})) if same_scope else {}
    if "service_tier" in (row.get("featureValues") or {}) and not row.get("serviceTier"):
        origins.pop("serviceTier", None)
    fields = [key for key in ("harness", *LAUNCH_FIELDS) if row.get(key) is not None]
    fields.extend(f"featureValues.{key}" for key in row.get("featureValues") or {})
    for key in fields:
        origins[key] = defaults.get("fieldOrigins", {}).get(
            f"stepUp.{key}",
            {
                "key": f"orchestration.roles.{defaults['role']}.stepUp.{key}",
                "provider": selected["agent"],
                "model": selected.get("model"),
                "precedence": 0,
            },
        )
    return origins


def label_step_up_choices(options: dict[str, Any], defaults: dict[str, Any]) -> None:
    """Keep host defaults explicit; stamp each selected authored choice with its source."""
    if defaults.get("stepUpWarning"):
        options["stepUpWarning"] = defaults["stepUpWarning"]
    selection = defaults.get("stepUpSelection")
    if not selection:
        return
    options["stepUp"] = {**selection, "model": options.get("model")}
    choices = options.get("choices", {})
    for key in ("agent", "model", "effort", "mode", "notes"):
        choice = choices.get(key, {})
        if choice.get("source") == "our settings":
            choice.update(
                source=selection["source"],
                origin={
                    "key": choice.get("origin", {}).get(
                        "key",
                        f"orchestration.roles.{defaults['role']}.stepUp.{'harness' if key == 'agent' else key}",
                    )
                },
            )
    for key, choice in choices.get("featureValues", {}).items():
        if choice.get("source") == "our settings":
            choice.update(
                source=selection["source"],
                origin={
                    "key": choice.get("origin", {}).get(
                        "key", f"orchestration.roles.{defaults['role']}.stepUp.featureValues.{key}"
                    )
                },
            )
