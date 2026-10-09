"""Who a tool server's Event River rows name as the caller (MIK-R114 rule 2).

The binding the launch started the server with is read once at start, with the
one task document it names -- the leaf for a leaf role, the master for a
Manager, the sprint for an Orchestrator -- so a row is whole without another
read. A document that cannot be read names the document by its path instead; a
server without a binding is a chat without a role, named with the harness its
settings file tells.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.application.agent_binding import AgentBinding
from agents_remember.observer.tool_events import ToolCaller
from agents_remember.tasks.document_refs import TaskDocumentTopology


def snapshot_tool_caller(
    binding: AgentBinding | None,
    *,
    coordination_root: Path,
    config_path: Path | str | None,
) -> ToolCaller:
    """Snapshot the caller once, from the launch binding; never raises."""
    harness = derive_harness(config_path)
    if binding is None:
        return ToolCaller(harness=harness)
    reference = binding.task_ref or binding.master_ref or binding.sprint_ref
    if reference is None:
        return ToolCaller(role=binding.role, agent_id=binding.agent_id, harness=harness)
    try:
        document = TaskDocumentTopology(coordination_root).resolve(reference).document
        task_id: str | None = document.id
        task_title: str | None = document.title
        task_path: str | None = None
    except Exception:
        task_id = None
        task_title = None
        task_path = reference.key
    return ToolCaller(
        role=binding.role,
        agent_id=binding.agent_id,
        task_id=task_id,
        task_title=task_title,
        task_path=task_path,
        task_repository=reference.repository,
        harness=harness,
    )


def derive_harness(config_path: Path | str | None) -> str | None:
    """Name the harness from the settings file the server was started with.

    A harness starter package keeps its authority settings at
    ``<root>/mcp/settings.json`` (see ``infer_harness_skill_root``), so the
    harness is the directory that owns that ``mcp`` folder. Anything else falls
    back to the file's own stem; an unreadable value names nothing.
    """
    try:
        path = Path(config_path) if config_path is not None else None
    except Exception:
        return None
    if path is None or not path.name:
        return None
    if path.parent.name == "mcp" and path.parent.parent.name:
        return path.parent.parent.name
    return path.stem or None
