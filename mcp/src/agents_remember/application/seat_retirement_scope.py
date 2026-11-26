"""Decide which role agents may retire or replace a recorded seat."""

from __future__ import annotations

from typing import Protocol

from agents_remember.application.role_launch_context import selection_binding
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_identity import canonical_role
from agents_remember.models.role_launcher import RoleSelection
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology


class _RetirementCaller(Protocol):
    @property
    def agent_id(self) -> str: ...

    @property
    def role(self) -> str: ...

    @property
    def master_ref(self) -> TaskDocumentRef | None: ...

    @property
    def sprint_ref(self) -> TaskDocumentRef | None: ...

    @property
    def task_ref(self) -> TaskDocumentRef | None: ...


def retirement_scope_refusal(
    config: McpRuntimeConfig,
    caller: _RetirementCaller,
    agent_id: str,
    selection: RoleSelection,
) -> str | None:
    """Return a scope refusal for a retirement or replacement, if any."""

    role = canonical_role(caller.role)
    if caller.agent_id == agent_id:
        reason = "An agent cannot retire or replace itself."
    elif role == "architect":
        return None
    elif role in {"manager", "orchestrator"}:
        try:
            if _within_document_scope(config, caller, selection, role):
                return None
        except TaskDocumentRefError as error:
            reason = f"The current task documents do not establish scope: {error}."
        else:
            reason = "The seat is outside the caller's scope."
    else:
        reason = f"A {role} may retire or replace no seats."
    return (
        f"{reason} Caller {role} agent {caller.agent_id} has scope {_scope_of(caller, role)}. "
        f"Agent {agent_id} has recorded selection {selection_binding(selection)}. "
        "Nothing was archived."
    )


def _within_document_scope(
    config: McpRuntimeConfig,
    caller: _RetirementCaller,
    selection: RoleSelection,
    role: str,
) -> bool:
    """Use the current parent chain from the most specific recorded task reference."""

    topology = TaskDocumentTopology(config.coordination_root)
    master = selection.master_document_ref
    if selection.task_document_ref is not None:
        master = topology.parent(selection.task_document_ref)
    if role == "manager":
        return caller.master_ref is not None and caller.master_ref == master
    sprint = topology.parent(master) if master is not None else selection.sprint_document_ref
    return caller.sprint_ref is not None and caller.sprint_ref == sprint


def _scope_of(caller: _RetirementCaller, role: str) -> str:
    if role == "architect":
        return "all role seats"
    if role == "manager" and caller.master_ref is not None:
        return f"master {caller.master_ref.key} and its leaves"
    if role == "orchestrator" and caller.sprint_ref is not None:
        return f"sprint {caller.sprint_ref.key}, its masters and their leaves"
    reference = caller.task_ref or caller.master_ref or caller.sprint_ref
    return (
        f"no retirement authority for {reference.key}" if reference else "no retirement authority"
    )
