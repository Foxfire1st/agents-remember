"""Payload builder for ``explainer_control`` (MIK-R186 rule 6, MIK-R189 rule 7).

The architect's one tool over a leaf's explainer. It answers three actions, and the caller check is
the whole point of two of them:

* ``update`` and ``retire`` are accepted only from the hosted architect seat the master-to-architect
  record names for the leaf's master (MIK-R186 rule 6). Any other caller, and every
  call when no record names a seat, is refused ``not-this-masters-architect``, and the refusal says
  how to take the master over.
* ``claim`` is open to any hosted architect seat (MIK-R189 rule 7 step 3). It writes the record with
  act ``claim``. It moves no reports: the explainer reports with ``role_message`` like every agent,
  so it holds no report of its own for a seat to take over (MIK-R211 v5 rule 3, D607).

The caller is this tool server's own agent binding -- an environment, never an argument -- so a
seat can no more name another agent than it can name itself a different role. What ``update`` and
``retire`` then do is the seat service's work; this module composes it exactly as
:mod:`agents_remember.serving.explainer` composes the page's routes and calls it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_remember.application.agent_binding import AgentBinding, read_agent_binding
from agents_remember.application.master_architect_record import (
    CLAIM,
    explainer_control_host,
    master_architect_path,
    read_record,
    write_record,
)
from agents_remember.application.review_answer_preparation import recorded_review_answers
from agents_remember.application.review_comparison_preparation import SharedComparisonPreparation
from agents_remember.application.review_planned_comparison import (
    explainer_application_ports,
)
from agents_remember.canvas.explainer_maintenance import retire_seat, update_seat
from agents_remember.canvas.explainer_paths import receipt_id_segment
from agents_remember.canvas.explainer_service import (
    ExplainerDeps,
    ExplainerHost,
    ExplainerRefused,
    LeafScope,
)
from agents_remember.canvas.paths import PathRefused
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_identity import canonical_role
from agents_remember.serving.explainer import (
    ExplainerComposition,
    ExplainerWiring,
    explainer_composition,
)

from .base import _tool_payload

__all__ = [
    "ACTION_CLAIM",
    "ACTION_RETIRE",
    "ACTION_UPDATE",
    "ExplainerControlCall",
    "explainer_control_payload",
]

ACTION_UPDATE = "update"
ACTION_RETIRE = "retire"
ACTION_CLAIM = CLAIM
ACTIONS = (ACTION_UPDATE, ACTION_RETIRE, ACTION_CLAIM)

#: The reasons this tool answers by name.
NOT_AN_ARCHITECT = "not-an-architect"
NOT_THIS_MASTERS_ARCHITECT = "not-this-masters-architect"
UNKNOWN_ACTION = "unknown-action"
LEAF_REQUIRED = "leaf-required"
RECEIPT_REQUIRED = "receipt-id-required"
EXPLAINER_UNAVAILABLE = "explainer-unavailable"
SELECTION_UNAVAILABLE = "selection-unavailable"

ARCHITECT_ROLE = "architect"


@dataclass(frozen=True)
class ExplainerControlCall:
    """One call: the action, and the leaf/master/repository selection it names."""

    action: str
    repo_id: str
    task_name: str
    leaf_id: str | None = None
    #: A Retire's receipt id, which the same retire repeats (MIK-R186 rule 8).
    receipt_id: str | None = None


@dataclass(frozen=True)
class _Collaborators:
    """What a call is answered through: the caller's environment and the clock."""

    environment: Mapping[str, str] | None = None
    now: Callable[[], str] | None = None


class _Refused(Exception):
    """A refusal the answer carries by name, never a server error."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def explainer_control_payload(
    config: McpRuntimeConfig,
    call: ExplainerControlCall,
    *,
    environment: Mapping[str, str] | None = None,
    now: Callable[[], str] | None = None,
) -> dict[str, Any]:
    """Run one ``explainer_control`` call and answer it in the shared tool envelope."""

    collaborators = _Collaborators(environment=environment, now=now)
    return _tool_payload("explainer_control", _answer(config, call, collaborators))


def _answer(
    config: McpRuntimeConfig, call: ExplainerControlCall, collaborators: _Collaborators
) -> dict[str, Any]:
    try:
        _require_action(call.action)
        caller = _caller(collaborators.environment)
        if call.action == ACTION_CLAIM:
            return _claim(config, call, caller, collaborators)
        return _seat_action(config, call, caller)
    except _Refused as refused:
        return _refusal(call, refused)
    except ExplainerRefused as refused:
        return _refusal(call, _Refused(refused.reason, refused.detail))
    except ValueError as error:
        # A selection that cannot be resolved is a refusal, never a server error (MIK-R185 v2
        # rule 3): the seat service's own path and document readers raise this way.
        return _refusal(call, _Refused(SELECTION_UNAVAILABLE, str(error)))


def _require_action(action: object) -> str:
    """MIK-R186 rule 6 / MIK-R189 rule 7: refuse any unknown action before lookup or mutation."""

    if isinstance(action, str) and action in ACTIONS:
        return action
    raise _Refused(
        UNKNOWN_ACTION,
        f"unknown explainer_control action {action!r}; expected one of {', '.join(ACTIONS)}",
    )


def _caller(environment: Mapping[str, str] | None) -> AgentBinding | None:
    """The hosted seat this tool server was started for, or ``None`` for a process without one."""

    try:
        return read_agent_binding(environment)
    except ValueError:
        return None


# -- claim (MIK-R189 rule 7 step 3) --------------------------------------------------------


def _claim(
    config: McpRuntimeConfig,
    call: ExplainerControlCall,
    caller: AgentBinding | None,
    collaborators: _Collaborators,
) -> dict[str, Any]:
    """Take one master over: write the record that names the claiming seat."""

    if caller is None or canonical_role(caller.role) != ARCHITECT_ROLE:
        raise _Refused(NOT_AN_ARCHITECT, _not_an_architect(call, caller))
    root = config.coordination_root
    path = master_architect_path(root, call.repo_id, call.task_name)
    record = write_record(
        path,
        architect_agent_id=caller.agent_id,
        act=ACTION_CLAIM,
        at=_stamp(collaborators),
    )
    return {
        "ok": True,
        "operation": "explainer_control",
        "action": ACTION_CLAIM,
        "state": "claimed",
        "repository": call.repo_id,
        "master": call.task_name,
        "architectAgentId": caller.agent_id,
        "record": record,
    }


def _not_an_architect(call: ExplainerControlCall, caller: AgentBinding | None) -> str:
    if caller is None:
        return (
            f"this call reached the master's record with no agent binding, and `claim` on "
            f"{call.task_name} is open to a hosted architect seat alone; start the claim from an "
            "architect's own tool server"
        )
    return (
        f"agent {caller.agent_id} is a {caller.role}, not an architect: `claim` on "
        f"{call.task_name} is open to a hosted architect seat alone, and the record names a seat "
        "only for the seat that claims it"
    )


# -- update and retire (MIK-R186 rule 6) ---------------------------------------------------


def _seat_action(
    config: McpRuntimeConfig, call: ExplainerControlCall, caller: AgentBinding | None
) -> dict[str, Any]:
    """Update or retire one leaf's explainer, accepted only from the seat the record names."""

    root = config.coordination_root
    record = read_record(master_architect_path(root, call.repo_id, call.task_name))
    if record is None:
        raise _Refused(
            NOT_THIS_MASTERS_ARCHITECT,
            f"no master-to-architect record names a seat for {call.task_name}, so `{call.action}` "
            "is accepted from none; take the master over with explainer_control action `claim`",
        )
    architect = record["architectAgentId"]
    if caller is None or caller.agent_id != architect:
        raise _Refused(
            NOT_THIS_MASTERS_ARCHITECT,
            f"the master-to-architect record names agent {architect} for {call.task_name}, and "
            f"this call is from {_caller_name(caller)}: `{call.action}` is accepted from that seat "
            "alone; take the master over with explainer_control action `claim`",
        )
    if not call.leaf_id:
        raise _Refused(
            LEAF_REQUIRED, f"`{call.action}` names the leaf whose explainer it acts on (leaf_id)"
        )
    scope = LeafScope(repository_id=call.repo_id, task=call.task_name, leaf_id=call.leaf_id)
    answer: dict[str, Any] = {
        "ok": True,
        "operation": "explainer_control",
        "action": call.action,
        "repository": call.repo_id,
        "master": call.task_name,
        "leaf": call.leaf_id,
        "architectAgentId": architect,
        "record": record,
    }
    # Nothing is composed before the call is accepted: composing the seat service provisions
    # this process's runtime directories (the recorded review answers under runtime/), and a
    # refused call must leave no trace at all (S06). Validation therefore runs first, and each
    # accepted action composes the service it needs at the point it needs it.
    if call.action == ACTION_UPDATE:
        update = update_seat(_deps(config, call), scope)
        return {
            **answer,
            "state": "current" if update.current else "updated",
            "update": update.as_record(),
        }
    if call.action != ACTION_RETIRE:
        raise _Refused(
            UNKNOWN_ACTION,
            f"unknown explainer_control action {call.action!r}; expected one of {', '.join(ACTIONS)}",
        )
    if not call.receipt_id:
        raise _Refused(
            RECEIPT_REQUIRED,
            "a Retire carries its receipt id, so a repeat finishes the same retire (rule 8)",
        )
    # MIK-R186 rule 8: the id names a file beside the leaf's receipts, so it is one path segment and
    # never a path -- the same guard the HTTP route applies, applied again at the architect's tool.
    try:
        receipt_id = receipt_id_segment(call.receipt_id)
    except PathRefused as refused:
        raise _Refused(RECEIPT_REQUIRED, str(refused)) from refused
    retire = retire_seat(_deps(config, call), scope, receipt_id=receipt_id)
    return {**answer, "state": "retired", "retire": retire.as_record()}


def _caller_name(caller: AgentBinding | None) -> str:
    return "a process with no agent binding" if caller is None else f"agent {caller.agent_id}"


def _deps(config: McpRuntimeConfig, call: ExplainerControlCall) -> ExplainerDeps:
    """The seat service, composed exactly as the dashboard's review routes compose it."""

    host = _explainer_host(config)
    comparisons = SharedComparisonPreparation(config, recorded_review_answers(config))
    composition: ExplainerComposition = explainer_composition(
        config,
        comparisons.render,
        wiring=None
        if host is None
        else ExplainerWiring(
            host=host,
            application=explainer_application_ports(),
            comparison_prepare=comparisons.prepare,
            contract_for=lambda _scope: _contract(config, call),
        ),
    )
    if composition.deps is None:
        raise _Refused(
            EXPLAINER_UNAVAILABLE,
            "no explainer service can be composed in this process: it needs the comparison owner "
            "and the host, and one of them is missing",
        )
    return composition.deps


def _explainer_host(config: McpRuntimeConfig) -> ExplainerHost | None:
    """The host adapter the dashboard composes for the same seat service."""

    return explainer_control_host(config)


def _contract(config: McpRuntimeConfig, call: ExplainerControlCall) -> Path | None:
    """This master's series contract, read for a planning leaf's planned comparison (rule 4).

    The master document names its own contract (`seriesContractPath`); a master that predates that field
    falls back to the file at its folder's root. Nothing here borrows another line's contract.
    """

    folder = config.coordination_root / "tasks" / call.repo_id / call.task_name
    named = folder / "task.json"
    if named.is_file():
        try:
            document = json.loads(named.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            document = {}
        declared = document.get("seriesContractPath") if isinstance(document, dict) else None
        if isinstance(declared, str) and declared and Path(declared).is_file():
            return Path(declared)
    fallback = folder / "series-contract.md"
    return fallback if fallback.is_file() else None


# -- shared: the answer, the clock, the receipt lookup --------------------------------------


def _refusal(call: ExplainerControlCall, refused: _Refused) -> dict[str, Any]:
    """One refusal: its own name in ``reason`` and ``code``, and how to take the master over."""

    return {
        "ok": False,
        "operation": "explainer_control",
        "action": call.action,
        "state": "refused",
        "repository": call.repo_id,
        "master": call.task_name,
        "leaf": call.leaf_id,
        "reason": refused.reason,
        "detail": refused.detail,
        "code": refused.reason,
    }


def _stamp(collaborators: _Collaborators) -> str:
    if collaborators.now is None:
        return datetime.now(UTC).isoformat()
    return collaborators.now()
