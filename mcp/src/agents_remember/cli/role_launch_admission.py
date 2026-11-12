"""What a start settles before any stage writes, and what an earlier attempt already did.

MIK-R196 rules 1-3 (L188-B05). The dispatch route reaches three decisions in one order, and only
the last of them may write:

1. **The selection.** A document reference the role's parentless row does not admit is refused
   here. Nothing is installed, provisioned or written for a selection that was never valid --
   previously the first-run stages ran before this check, so an invalid call could create a
   scaffold and a host and then be rejected. The task-document resolution stays where it was,
   before a seat is compiled: a genuinely cold root has no documents to resolve yet, so it cannot
   move ahead of the scaffold it is meant to create.
2. **The request binding.** A request id that an archived execution, or a message-binding file of
   another selection, already owns is refused with the same reason the later write-once check
   gives, but before any stage runs. A request whose receipt carries it is an execution that
   already exists: this call reconciles it instead of preparing again, so a retry reaches its own
   seat even when preparation would fail today.
3. **The first-run stages**, for a request that has none yet: the caller takes the dispatch lock
   around this, so two starts cannot prepare the same cold root side by side.

The route body stays a delegation call: the decisions live here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException

from agents_remember.application.role_launch_context import (
    PARENTLESS_START_SELECTIONS,
    TASKLESS_ROLES,
    parentless_selection_violation,
    resolve_role_launch_context,
    selection_binding,
)
from agents_remember.cli import role_launch_first_run as first_run
from agents_remember.cli import role_launch_receipts as bindings
from agents_remember.cli import role_launch_routes as route
from agents_remember.errors import RolePreparationError
from agents_remember.models.role_identity import canonical_role

if TYPE_CHECKING:
    from agents_remember.cli.paseo_launch import StartingAgent
    from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
    from agents_remember.models.role_launcher import RoleDispatchRequest


@dataclass(frozen=True, slots=True)
class Admission:
    """What one start settled before its first stage: its receipt address and its state."""

    receipt_path: Path
    # This request already has its receipt: reconcile it, and prepare nothing.
    existing: bool


def admit(
    config: McpRuntimeConfig,
    request: RoleDispatchRequest,
    started_by: StartingAgent | None = None,
) -> Admission:
    """Validate the selection and the request binding; refuse before any durable effect."""

    if request.action != "start":
        # A revive resumes an execution that a validated start already created.
        return Admission(receipt_path=_receipt_address(config, request), existing=False)
    _refuse_parentless_violation(request)
    receipt_path = _receipt_address(config, request)
    if route._receipt_carries_request(receipt_path, request):
        route._refuse_another_starter(receipt_path, request, started_by)
        return Admission(receipt_path=receipt_path, existing=True)
    if _binding_holds_this_selection(config, request):
        # Its binding file was written when this request prepared, and a binding is written once.
        # A retry whose receipt never landed is therefore a stale queued request: it reconciles
        # the seat it already prepared and prepares nothing again (ruling 887182ab).
        route._refuse_another_starter(receipt_path, request, started_by)
        return Admission(receipt_path=receipt_path, existing=True)
    route._refuse_reused_request_id(config, receipt_path, request)
    return Admission(receipt_path=receipt_path, existing=False)


def _binding_holds_this_selection(config: McpRuntimeConfig, request: RoleDispatchRequest) -> bool:
    """True when this request id's message-binding file records this exact selection.

    The binding file is written once, at the moment preparation ran for the request, so its
    presence for *this* selection proves the request already prepared: a retry under the same
    request id reaches its own execution and prepares the root no further (MIK-R196 rule 3 as
    ruling 887182ab states it). A file recording another selection is not this case;
    ``_refuse_reused_request_id`` refuses it by name.
    """

    binding_file = bindings.message_binding_projection_path(config, request.request_id)
    try:
        bound = json.loads(binding_file.read_text("utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(bound, dict) and bound.get("selection") == selection_binding(request)


def _receipt_address(config: McpRuntimeConfig, request: RoleDispatchRequest) -> Path:
    taskless_id = request.request_id if request.role in TASKLESS_ROLES else None
    return route._receipt_path(config, request, taskless_id)


def _refuse_parentless_violation(request: RoleDispatchRequest) -> None:
    """Refuse a document reference this role's parentless row does not admit.

    The row is the whole selection of the one role that prepares a cold root: a Bootstrap seat
    takes no document at all, so any reference it carries is an invalid call and must be refused
    before the scaffold, the authority file or a host exists to undo. The deeper task-document
    resolution stays where it was -- before a seat is compiled -- and is not duplicated here.
    """

    role = canonical_role(request.role)
    if role not in PARENTLESS_START_SELECTIONS:
        return
    violated = parentless_selection_violation(
        role,
        request.sprint_document_ref,
        request.master_document_ref,
        request.task_document_ref,
    )
    if violated is not None:
        raise HTTPException(status_code=409, detail=violated)


def prepare_first_run_for(
    config: McpRuntimeConfig, request: RoleDispatchRequest, admission: Admission
) -> None:
    """Prepare a cold first run for this request, or leave an existing execution alone.

    Called with the dispatch lock held. A non-Bootstrap start keeps its older requirement -- a
    configured runtime -- and a Bootstrap start whose receipt exists is not prepared a second
    time: its own seat answers the retry. A failed stage is raised as the start path's refusal,
    carrying the stage and the effects that came before it (:class:`FirstRunPreparationError`).
    """

    if canonical_role(request.role) != "bootstrap" or request.action != "start":
        route._require_paseo_runtime(config)
        return
    if admission.existing:
        return
    try:
        first_run.prepare_first_run(config)
    except RolePreparationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


__all__ = [
    "Admission",
    "admit",
    "prepare_first_run_for",
    # The selection owner's second half (task-document resolution), re-exported so the launch
    # seams patch one name per module. The start itself runs it before a seat is compiled; this
    # module cannot run it earlier, because a genuinely cold root has no documents to resolve.
    "resolve_role_launch_context",
]
