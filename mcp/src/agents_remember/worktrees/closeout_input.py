"""Resolve and validate closeout mutation intent before authority is acquired."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, cast

from agents_remember.errors import AgentsRememberError
from agents_remember.models.closeout.input import (
    CloseoutCommitLegName,
    CloseoutCorrectedCall,
    CloseoutInputRoute,
    CloseoutInvalidField,
    CloseoutLegPlan,
    CloseoutMessageInput,
    CloseoutMessageObservation,
    CloseoutPublicMessageField,
    EffectiveCloseoutInput,
    EnabledCloseoutLeg,
    NotApplicableCloseoutLeg,
    ResolvedCloseoutPlan,
)
from agents_remember.worktrees.modules.git import branch_commit, head_commit, require_git
from agents_remember.worktrees.route_review import code_candidate_tree
from agents_remember.worktrees.worktree_contract import WorktreeContract

_PUBLIC_FIELDS: dict[CloseoutCommitLegName, CloseoutPublicMessageField] = {
    "code": "code_commit_message",
    "memory": "memory_commit_message",
    "ledger": "ledger_commit_message",
}


@dataclass(frozen=True)
class CloseoutCandidateSnapshot:
    """One bounded code candidate observation used to resolve closeout enabledness."""

    candidate_tree: str
    head_commit: str
    head_tree: str

    @property
    def code_would_commit(self) -> bool:
        return self.candidate_tree != self.head_tree


class CloseoutInputError(AgentsRememberError):
    """One or more enabled mutation legs lack explicit caller intent."""

    status = "closeout-input-invalid"

    def __init__(
        self,
        *,
        invalid_fields: list[CloseoutInvalidField],
        resolved_plan: ResolvedCloseoutPlan,
        corrected_call: CloseoutCorrectedCall,
    ) -> None:
        self.invalid_fields = invalid_fields
        self.resolved_plan = resolved_plan
        self.corrected_call = corrected_call
        detail = {
            "invalidFields": [item.model_dump(mode="json") for item in invalid_fields],
            "resolvedPlan": resolved_plan.model_dump(mode="json"),
            "correctedCall": corrected_call.model_dump(mode="json"),
        }
        super().__init__(f"{self.status}: {json.dumps(detail, sort_keys=True)}")

    def response_fields(self) -> dict[str, object]:
        return {
            "invalidFields": [item.model_dump(mode="json") for item in self.invalid_fields],
            "resolvedPlan": self.resolved_plan.model_dump(mode="json"),
            "correctedCall": self.corrected_call.model_dump(mode="json"),
        }


def resolve_closeout_plan(
    contract: WorktreeContract,
    *,
    route: CloseoutInputRoute,
    candidate: CloseoutCandidateSnapshot | None = None,
) -> ResolvedCloseoutPlan:
    """Resolve lifecycle-possible writes without consulting caller message values."""
    contract_kind = cast(Literal["leaf", "series"], contract.kind)
    if route == "direct-landing":
        code = _not_applicable("code commit is verified-existing")
    elif contract_kind == "series":
        code = _not_applicable("series closeout records an existing code commit")
    elif (candidate or capture_closeout_candidate(contract)).code_would_commit:
        code = _enabled("leaf closeout can create a code commit")
    else:
        code = _not_applicable("leaf code worktree has no commit to create")

    if route == "worktree" and contract_kind == "series":
        memory = _not_applicable("series closeout records existing memory content")
        ledger = _not_applicable("series closeout verifies an existing ledger mapping")
    elif contract.memory_mode == "external":
        if route == "direct-landing":
            memory = _enabled("direct landing can create external-memory content")
            ledger = _enabled("direct landing can create a ledger mapping")
        else:
            memory = _enabled("external-memory refresh can create content")
            ledger = _enabled("external-memory closeout can create a ledger mapping")
    else:
        reason = f"memory mode {contract.memory_mode} has no external-memory commit"
        memory = _not_applicable(reason)
        ledger = _not_applicable(reason)

    return ResolvedCloseoutPlan(
        route=route,
        contractKind=contract_kind,
        memoryMode=contract.memory_mode,
        code=code,
        memory=memory,
        ledger=ledger,
    )


def normalize_closeout_input(
    contract: WorktreeContract,
    messages: CloseoutMessageInput,
    *,
    route: CloseoutInputRoute,
    corrected_call: CloseoutCorrectedCall,
    resolved_plan: ResolvedCloseoutPlan | None = None,
) -> EffectiveCloseoutInput:
    """Return the one canonical input or refuse before any durable authority."""
    plan = resolved_plan or resolve_closeout_plan(contract, route=route)
    try:
        _require_plan_contract_identity(contract, plan, route=route)
    except RuntimeError:
        raise _plan_mismatch_error(plan, corrected_call=corrected_call) from None
    invalid: list[CloseoutInvalidField] = []
    normalized: dict[str, EnabledCloseoutLeg | NotApplicableCloseoutLeg] = {}
    for leg in ("code", "memory", "ledger"):
        leg_name = leg  # Preserve the Literal narrowing for the typed field map.
        leg_plan = getattr(plan, leg_name)
        supplied = getattr(messages, leg_name)
        if leg_plan.state == "not-applicable":
            normalized[leg_name] = NotApplicableCloseoutLeg(reason=leg_plan.reason)
            continue
        value = supplied.strip() if supplied is not None else ""
        if not value:
            invalid.append(
                CloseoutInvalidField(
                    field=_PUBLIC_FIELDS[leg_name],
                    leg=leg_name,
                    observation=_message_observation(supplied),
                    code=f"enabled-{leg_name}-message-required",
                )
            )
            continue
        normalized[leg_name] = EnabledCloseoutLeg(reason=leg_plan.reason, message=value)

    if invalid:
        arguments = dict(corrected_call.arguments)
        for leg in ("code", "memory", "ledger"):
            if getattr(plan, leg).state == "enabled":
                arguments[_PUBLIC_FIELDS[leg]] = f"<nonblank {leg} commit message>"
        raise CloseoutInputError(
            invalid_fields=invalid,
            resolved_plan=plan,
            corrected_call=corrected_call.model_copy(update={"arguments": arguments}),
        )

    return EffectiveCloseoutInput(
        route=plan.route,
        contractKind=plan.contractKind,
        memoryMode=plan.memoryMode,
        code=normalized["code"],
        memory=normalized["memory"],
        ledger=normalized["ledger"],
    )


def require_effective_closeout_plan(
    contract: WorktreeContract,
    effective_input: EffectiveCloseoutInput,
    *,
    route: CloseoutInputRoute,
) -> None:
    """Refuse contract identity drift without re-deriving an accepted generation's plan."""
    plan = resolved_plan_from_effective_input(effective_input)
    try:
        _require_plan_contract_identity(contract, plan, route=route)
    except RuntimeError:
        raise _plan_mismatch_error(
            plan,
            corrected_call=CloseoutCorrectedCall(
                tool="normalize_closeout_input",
                arguments={"contract_path": contract.contract_path.as_posix()},
            ),
        ) from None


def capture_closeout_candidate(contract: WorktreeContract) -> CloseoutCandidateSnapshot:
    """Capture the exact candidate tree and the ref/tree it would advance."""
    repository = contract.code_repo_path if contract.kind == "series" else contract.code_worktree
    current_head = (
        branch_commit(repository, contract.code_work_branch)
        if contract.kind == "series"
        else head_commit(repository)
    )
    return CloseoutCandidateSnapshot(
        candidate_tree=code_candidate_tree(contract),
        head_commit=current_head,
        head_tree=require_git(repository, ["rev-parse", f"{current_head}^{{tree}}"]),
    )


def resolved_plan_from_effective_input(
    effective_input: EffectiveCloseoutInput,
) -> ResolvedCloseoutPlan:
    """Recover the immutable accepted plan without consulting post-mutation Git state."""
    return ResolvedCloseoutPlan(
        route=effective_input.route,
        contractKind=effective_input.contractKind,
        memoryMode=effective_input.memoryMode,
        code=CloseoutLegPlan(
            state=effective_input.code.state,
            reason=effective_input.code.reason,
        ),
        memory=CloseoutLegPlan(
            state=effective_input.memory.state,
            reason=effective_input.memory.reason,
        ),
        ledger=CloseoutLegPlan(
            state=effective_input.ledger.state,
            reason=effective_input.ledger.reason,
        ),
    )


def candidate_drift_error(
    plan: ResolvedCloseoutPlan,
    *,
    corrected_call: CloseoutCorrectedCall,
) -> CloseoutInputError:
    """Return the canonical typed refusal for an unstable admission candidate."""
    arguments = dict(corrected_call.arguments)
    for leg in ("code", "memory", "ledger"):
        if getattr(plan, leg).state == "enabled":
            arguments[_PUBLIC_FIELDS[leg]] = f"<nonblank {leg} commit message>"
    return CloseoutInputError(
        invalid_fields=[
            CloseoutInvalidField(
                field="effectiveInput",
                leg="plan",
                observation="stale-or-forged",
                code="closeout-candidate-changed-during-admission",
            )
        ],
        resolved_plan=plan,
        corrected_call=corrected_call.model_copy(update={"arguments": arguments}),
    )


def _plan_mismatch_error(
    plan: ResolvedCloseoutPlan,
    *,
    corrected_call: CloseoutCorrectedCall,
) -> CloseoutInputError:
    return CloseoutInputError(
        invalid_fields=[
            CloseoutInvalidField(
                field="effectiveInput",
                leg="plan",
                observation="stale-or-forged",
                code="effective-closeout-plan-mismatch",
            )
        ],
        resolved_plan=plan,
        corrected_call=corrected_call,
    )


def _require_plan_contract_identity(
    contract: WorktreeContract,
    plan: ResolvedCloseoutPlan,
    *,
    route: CloseoutInputRoute,
) -> None:
    if (
        plan.route != route
        or plan.contractKind != contract.kind
        or plan.memoryMode != contract.memory_mode
    ):
        raise RuntimeError("closeout plan does not match the current contract identity")


def _enabled(reason: str) -> CloseoutLegPlan:
    return CloseoutLegPlan(state="enabled", reason=reason)


def _not_applicable(reason: str) -> CloseoutLegPlan:
    return CloseoutLegPlan(state="not-applicable", reason=reason)


def _message_observation(value: str | None) -> CloseoutMessageObservation:
    if value is None:
        return "omitted"
    if value == "":
        return "empty"
    return "whitespace-only"


def raw_closeout_messages(
    *,
    code: str | None,
    memory: str | None,
    ledger: str | None,
) -> CloseoutMessageInput:
    """Name the raw boundary explicitly at callers that still receive flat fields."""
    return CloseoutMessageInput(code=code, memory=memory, ledger=ledger)


def corrected_closeout_arguments(contract_path: str, **values: Any) -> dict[str, object]:
    """Build a bounded corrected-call base without echoing supplied message text."""
    return {"contract_path": contract_path, **values}


def effective_message_arguments(effective_input: EffectiveCloseoutInput) -> dict[str, str]:
    """Render only enabled messages for public next-call guidance."""
    arguments: dict[str, str] = {}
    for leg in ("code", "memory", "ledger"):
        if effective_input.enabled(leg):
            arguments[_PUBLIC_FIELDS[leg]] = effective_input.message_for(leg)
    return arguments
