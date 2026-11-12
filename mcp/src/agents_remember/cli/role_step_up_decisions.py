"""Reconcile one task-owned step-up decision from saved and observed launch facts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.cli.role_instructions import launch_choice_metadata
from agents_remember.cli.role_step_up import LAUNCH_FIELDS
from agents_remember.errors import AgentsRememberError
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument
from agents_remember.tasks.document_refs import TaskDocumentTopology


def recorded_step_up(document: TaskDocument | None, role: str) -> dict[str, Any] | None:
    if document is None:
        return None
    for decision in reversed(document.decisions):
        if decision.decision != f"Step-up {role}.":
            continue
        data = _decision_data(decision.rationale)
        if data is not None:
            return data
    return None


def _decision_data(rationale: str) -> dict[str, Any] | None:
    try:
        data = json.loads(rationale)
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("schema") == "ar-role-step-up/v1" else None


def record_step_up(config: McpRuntimeConfig, receipt: dict[str, Any]) -> None:
    """Reconcile before every execution; retries need no ephemeral preparation object."""
    step = receipt.get("stepUp")
    selection = receipt.get("selection") or {}
    ref_data = (
        selection.get("taskDocumentRef")
        or selection.get("masterDocumentRef")
        or selection.get("sprintDocumentRef")
    )
    if not step or not ref_data:
        return
    ref = TaskDocumentRef.model_validate(ref_data)
    topology = TaskDocumentTopology(config.coordination_root)
    # Use the task publisher's own repository lock: the re-read and any update
    # form one serialized mutation, including against ordinary task edits.
    with exclusive_file_lock(
        config.coordination_root / "tasks" / ref.repository, "task document publication"
    ):
        document = topology.resolve(ref).document
        prior = recorded_step_up(document, receipt["role"])
        path = Path(ref.path)
        target = TaskDocTarget(
            repo_id=ref.repository, task_name=path.parent.as_posix(), slug=path.stem
        )
        if prior:
            _record_observed(config, target, document, receipt, prior)
            return
        options = receipt.get("sessionOptions") or {}
        to_choices = {
            "agent": receipt["agent"]["id"],
            **{key: options.get(key) for key in LAUNCH_FIELDS},
            "notes": launch_choice_metadata(receipt.get("choices", {})).get("notes"),
        }
        data = {
            **step,
            "schema": "ar-role-step-up/v1",
            "requestId": receipt["requestId"],
            "to": to_choices,
            "requestedTo": to_choices,
            "choices": launch_choice_metadata(receipt.get("choices", {})),
        }
        answer = task_doc_tool(
            config,
            target,
            operation="append_decision",
            edit=TaskDocEdit(
                decision={
                    "at": receipt["createdAt"],
                    "decision": f"Step-up {receipt['role']}.",
                    "rationale": json.dumps(data, sort_keys=True),
                }
            ),
        )
        _require_recorded(answer)


def _record_observed(
    config: McpRuntimeConfig,
    target: TaskDocTarget,
    document: TaskDocument,
    receipt: dict[str, Any],
    prior: dict[str, Any],
) -> None:
    if prior.get("requestId") != receipt["requestId"] or not receipt.get("hostAgentExists"):
        return
    applied = receipt.get("agent") or {}
    data = {
        **prior,
        "model": receipt["stepUp"].get("model"),
        "requestedModel": prior.get("requestedModel", prior.get("model")),
        "to": {
            **prior["to"],
            **{key: applied[key] for key in LAUNCH_FIELDS if key != "notes" and key in applied},
        },
        "choices": launch_choice_metadata(receipt.get("choices", {})),
    }
    if data == prior:
        return
    fields = document.model_dump(mode="json")
    for decision in reversed(fields["decisions"]):
        if (
            decision["decision"] == f"Step-up {receipt['role']}."
            and _decision_data(decision["rationale"]) == prior
        ):
            decision["rationale"] = json.dumps(data, sort_keys=True)
            break
    _require_recorded(
        task_doc_tool(config, target, operation="replace", edit=TaskDocEdit(fields=fields))
    )


def _require_recorded(answer: dict[str, Any]) -> None:
    if not answer.get("ok"):
        raise AgentsRememberError(
            f"The step-up decision could not be recorded; retry this same request: {answer}"
        )
