"""Complete one tool result at the application boundary.

The MCP adapter supplies the tool name and raw use-case result.  This service
selects the wire model, attaches lifecycle-wide state, finalizes the token
count, and records the completed call.  Domain observation therefore remains
below the application boundary; the adapter receives a protocol-ready mapping.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from agents_remember.application.next_step import next_step_for
from agents_remember.kernel.agentic_settings import DEFAULT_AGENT_NOTIFIER_STALE_CUTOFF_SECONDS
from agents_remember.models.base import NextStep, ResponseEnvelope
from agents_remember.models.tools.tool_response import finalize_tool_response
from agents_remember.observer.ambient import AmbientLifecycle, ambient
from agents_remember.serving.agent_notifier_heartbeat import agent_notifier_staleness_banner

_RESPONSE_PATH_FIELDS = ("contractPath", "enclosurePath")
_ARGUMENT_PATH_FIELDS = (
    "contract_path",
    "enclosure_path",
    "contractPath",
    "enclosurePath",
)


def _names_the_same_place(observed: str, expected: str) -> bool:
    """Whether two path spellings name the same contract file, or its immediate directory.

    **What the product actually emits.** Both spellings are set to the contract FILE:
    ``worktrees/modules/guidance.py::contract_next_args`` (:171-176) and
    ``_status_payload_with_landing`` (:443-444), ``application/lifecycle/start_result.py:121`` and
    ``…/lifecycle/reopen.py:104`` all pass ``contract.contract_path.as_posix()`` for
    ``enclosure_path``, and a real ``worktree_status`` response carries both spellings equal and
    the camelCase pair ``None``. So equality is the shape in force today.

    **Why the directory tolerance stays, and how far it goes.** ``enclosure_path`` is the
    *enclosure* address by name, and the enclosure IS the directory that holds the contract
    (``tasks/<repo>/<master>/enclosures/<leaf>/``) -- a future producer that emits the directory
    instead of the file is emitting the same task's address, and refusing it would withhold every
    hint from that producer. The tolerance is therefore exactly the immediate container:
    ``<file>`` and ``dirname(<file>)``. It is NOT "any ancestor": an earlier form of this
    function accepted any directory above the contract (measured: ``/coord/tasks/repo`` → True,
    ``/coord/tasks/repo/own-master`` → True), which is wider than the rule it claimed to be, is
    emitted by no producer, and is `260918-TSIP-L6`'s `F3`. A different task's contract still
    fails both spellings, which is the whole point of the guard.
    """

    if observed.rstrip("/") == expected.rstrip("/"):
        return True
    return PurePosixPath(observed.rstrip("/")) == PurePosixPath(expected.rstrip("/")).parent


def bound_next_step(response: ResponseEnvelope, step: NextStep | None) -> NextStep | None:
    """Omit guidance whose task address contradicts the response's exact address.

    The guidance a response carries is derived from the *process-global* ambient lifecycle
    (``application/next_step.py::next_step_for`` reads ``LifecycleState.enclosure``), while the
    response has its own address. When they disagree the guidance is about a different task, and
    a seat following it is sent into another task's enclosure -- which is what ``260918-TSIP``
    `T54` recorded. So the address check is a *guard*, and its two rules are:

    * a response that declares no contract path of its own cannot be validated, and its guidance
      is withheld rather than emitted unchecked. The old form returned the step here
      (``if not response_paths: return step``), which is precisely the hole `T54` came through;
    * every path spelling the guidance carries must name the response's own place -- the same
      contract file, or the directory that immediately contains it -- so one stale spelling is
      enough to withhold the hint. The old form required the guidance's path set to be exactly
      ``{expected}``, which let a hint carrying both spellings through whenever only one of them
      was stale.
    """

    if step is None or step.nextArgs is None:
        return step
    response_paths = {
        value
        for field in _RESPONSE_PATH_FIELDS
        if isinstance((value := getattr(response, field, None)), str) and value
    }
    observed = {
        str(step.nextArgs[field]) for field in _ARGUMENT_PATH_FIELDS if field in step.nextArgs
    }
    if not observed:
        # Guidance that names no artifact cannot contradict anything.
        return step
    if not response_paths:
        return None
    if not all(
        any(_names_the_same_place(path, expected) for expected in response_paths)
        for path in observed
    ):
        return None
    return step


def _agent_notifier_banner(amb: AmbientLifecycle) -> str | None:
    """Return the stale-agent-notifier banner without blocking a tool response."""
    try:
        return agent_notifier_staleness_banner(
            amb.root,
            now=datetime.now(UTC),
            stale_cutoff_seconds=DEFAULT_AGENT_NOTIFIER_STALE_CUTOFF_SECONDS,
        )
    except Exception:
        return None


def _attach_lifecycle_tail(
    response: ResponseEnvelope, amb: AmbientLifecycle, tool_name: str
) -> None:
    if (
        amb.current is not None
        and amb.current.state == "awaiting-developer"
        and tool_name != "lifecycle_turn_end_notification"
    ):
        amb.resume_from_await()
    # A refusal/recovery producer may supply an explicit nextStep alongside its top-level
    # recovery keys. Preserve that one authority instead of overwriting it with ambient phase
    # guidance derived from a contract the operation intentionally refused or just rewrote.
    step = response.nextStep or next_step_for(amb, tool_name)
    response.nextStep = bound_next_step(response, step)
    banner = _agent_notifier_banner(amb)
    response.agentNotifierBanner = banner
    response.supervisorBanner = banner


def complete_tool_response(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate, enrich, count, and observe one application result."""
    amb = ambient()
    finalized = finalize_tool_response(
        tool_name,
        payload,
        enrich=(
            (lambda response: _attach_lifecycle_tail(response, amb, tool_name))
            if amb is not None
            else None
        ),
    )
    if amb is not None:
        amb.emit_tool(tool_name, finalized)
    return finalized
