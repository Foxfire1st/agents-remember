"""Typed lifecycle-control refusals with executable public next actions."""

from __future__ import annotations

from collections.abc import Mapping

from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.lifecycles.operation import LifecycleOperationKind
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_lifecycle_evidence_pair,
)


class LifecycleControlError(RuntimeError):
    """Bounded refusal with an executable task-addressed next action."""

    def __init__(
        self,
        status: str,
        detail: str,
        *,
        expected: Mapping[str, object] | None = None,
        observed: Mapping[str, object] | None = None,
        **next_fields: object,
    ) -> None:
        unexpected = set(next_fields) - {"next_action", "next_tool", "next_args"}
        if unexpected:
            raise TypeError(f"unsupported lifecycle control fields: {sorted(unexpected)}")
        next_action = next_fields.get("next_action", "recover")
        next_tool = next_fields.get("next_tool")
        next_args = next_fields.get("next_args")
        if not isinstance(next_action, str):
            raise TypeError("next_action must be a string")
        if next_tool is not None and not isinstance(next_tool, str):
            raise TypeError("next_tool must be a string or None")
        if next_args is not None and not isinstance(next_args, Mapping):
            raise TypeError("next_args must be a mapping or None")
        self.status = status
        self.detail = detail
        public = public_lifecycle_evidence_pair(expected or {}, observed or {})
        self.expected = public.expected
        self.observed = public.observed
        self.next_action = next_action
        self.next_tool = next_tool
        self.next_args = dict(next_args or {})
        super().__init__(f"{status}: {detail}")

    def response_fields(
        self,
        *,
        contract_path: str,
        kind: LifecycleOperationKind,
        generation: int,
        caller: DeclaredCaller | None = None,
    ) -> dict[str, object]:
        fields: dict[str, object] = {
            "expected": self.expected,
            "observed": self.observed,
            "nextAction": self.next_action,
        }
        if self.next_action == "developer-decision":
            fields["developerDecisionRequired"] = True
            fields["decisionSurface"] = self.detail
            return fields
        if self.next_tool is not None:
            next_args = dict(self.next_args)
            if caller is not None and self.next_tool == "worktree_operation_control":
                next_args["caller"] = caller.model_dump(mode="json")
            fields.update(
                {
                    "nextTool": self.next_tool,
                    "nextArgs": next_args,
                }
            )
            return fields
        resolved_generation = generation
        observed_generation = self.observed.get("generation")
        if self.status == "lifecycle-generation-changed" and isinstance(observed_generation, int):
            resolved_generation = observed_generation
        arguments: dict[str, object] = {
            "contract_path": contract_path,
            "operation_kind": kind,
            "action": self.next_action,
            "expected_generation": resolved_generation,
            "intent_note": "<developer intent>",
            "dry_run": False,
        }
        if caller is not None:
            arguments["caller"] = caller.model_dump(mode="json")
        if self.next_action == "revise":
            arguments.update(
                {
                    "code_commit_message": "<fresh message when enabled>",
                    "memory_commit_message": "<fresh message when enabled>",
                    "ledger_commit_message": "<fresh message when enabled>",
                }
            )
        fields.update({"nextTool": "worktree_operation_control", "nextArgs": arguments})
        return fields
