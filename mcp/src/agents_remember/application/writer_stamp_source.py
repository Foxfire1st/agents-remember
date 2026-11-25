"""The writer's stamp of the agent calling this tool server.

The caller is the agent the tool server was launched for: its role and seat come from the launch
binding (:func:`read_agent_binding`), its harness, model and effort from that seat's launch receipt
(:mod:`agents_remember.tasks.writer_stamps`): beside the task document the binding names, or for a
taskless seat such as Bootstrap in its role's session folder. A tool's input never names any of
them. A server without a binding, a binding that cannot be read or a receipt that cannot be found
gives a stamp whose unresolved values read ``unknown`` with the reason; the write is not refused
for that.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

from agents_remember.application.agent_binding import AgentBinding, read_agent_binding
from agents_remember.models.knowledge_files.writer_stamp import WriterStamp
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.tasks.writer_stamps import (
    StampOwner,
    StampRequest,
    receipt_of_request,
    stamp,
    taskless_receipt,
)

Clock = Callable[[], str]


def clock_now() -> str:
    """The local time with its offset, read from the clock at the call."""

    return datetime.now().astimezone().isoformat(timespec="seconds")


def caller_stamp(
    owner: StampOwner,
    *,
    coordination_root: Path,
    environment: Mapping[str, str] | None = None,
    now: Clock = clock_now,
) -> WriterStamp:
    """The stamp of the agent calling this tool server, for a write that belongs to ``owner``."""

    try:
        binding = read_agent_binding(environment)
    except ValueError as error:
        return stamp(
            _request(None, owner, now(), f"the launch binding is unreadable: {error}"), None
        )
    if binding is None:
        reason = "this tool server was not launched for an agent"
        return stamp(_request(None, owner, now(), reason), None)
    receipt = _receipt(binding, coordination_root)
    return stamp(_request(binding, owner, now(), None), receipt)


def _request(
    binding: AgentBinding | None, owner: StampOwner, at: str, unresolved: str | None
) -> StampRequest:
    return StampRequest(
        by="agent",
        role=binding.role if binding else None,
        seat=binding.request_id if binding else None,
        owner=owner,
        at=at,
        unresolved=unresolved,
    )


def _receipt(binding: AgentBinding, coordination_root: Path) -> Mapping[str, object] | None:
    reference = binding.task_ref or binding.master_ref or binding.sprint_ref
    if reference is None:
        return taskless_receipt(coordination_root, binding.role, binding.request_id)
    try:
        folder = TaskDocumentTopology(coordination_root).path_for_ref(reference).parent
    except (TaskDocumentRefError, OSError, ValueError):
        return None
    return receipt_of_request(folder, binding.request_id)
