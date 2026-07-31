"""Child-agent identity and roster authority for active conversation projection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from agents_remember.serving.conversation.models import (
    ConversationAgentRef,
    ConversationAgentStatus,
    ConversationItem,
)
from agents_remember.serving.conversation.projectors.common import (
    MappedItem,
    MappedUnknownVendor,
    MapperOutput,
)
from agents_remember.serving.harness_control_models import AdapterSnapshot, EvidenceFrame

_REGISTRY_AGENT_STATUS: dict[str, ConversationAgentStatus] = {
    "registered": "registered",
    "running": "running",
    "started": "running",
    "interacted": "running",
    "active": "running",
    "completed": "completed",
    "failed": "failed",
    "interrupted": "interrupted",
}
_ROSTER_PREFIXES = ("codex-agent-", "claude-agent-")


def is_agent_roster_item(item: ConversationItem) -> bool:
    """Whether ``item`` is one of the backend's explicit roster identities."""

    return (
        item.kind == "notice"
        and item.role == "system"
        and item.agent is not None
        and item.item_id.startswith(_ROSTER_PREFIXES)
    )


class AgentAuthority:
    """Resolve thread identity and current roster state from adapter authority."""

    def __init__(self, parent_thread_id: str | None) -> None:
        self._parent_thread_id = parent_thread_id
        self._snapshot: AdapterSnapshot | None = None
        self.live_threads: set[str] = set()

    def set_snapshot(self, snapshot: AdapterSnapshot) -> None:
        self._snapshot = snapshot

    def reset(self) -> None:
        self.live_threads.clear()

    def frame_thread(self, frame: EvidenceFrame) -> str | None:
        thread_id = frame.thread_id
        if thread_id is None or thread_id == self._parent_thread_id:
            return None
        return thread_id

    def ref(self, thread_id: str) -> ConversationAgentRef:
        agent_path: str | None = None
        status: ConversationAgentStatus = "unknown"
        snapshot = self._snapshot
        registry = snapshot.raw.get("agentRegistry") if snapshot is not None else None
        if isinstance(registry, Mapping):
            entry = registry.get(thread_id)
            if isinstance(entry, Mapping):
                path = entry.get("agentPath")
                if isinstance(path, str) and path:
                    agent_path = path
                raw_status = entry.get("status")
                if isinstance(raw_status, str):
                    status = _REGISTRY_AGENT_STATUS.get(raw_status, "unknown")
        return ConversationAgentRef(agent_id=thread_id, agent_path=agent_path, status=status)

    def bind_thread(self, output: MapperOutput, thread_id: str) -> MapperOutput:
        """Attach one foreign-thread ref without inventing parent ownership."""

        self.live_threads.add(thread_id)
        if isinstance(output, MappedUnknownVendor):
            return (
                output if output.agent is not None else replace(output, agent=self.ref(thread_id))
            )
        if not isinstance(output, MappedItem):
            return output
        item = output.item
        if item.agent is None:
            item = item.model_copy(update={"agent": self.ref(thread_id)})
        return self.reconcile_roster(MappedItem(item=item))

    def reconcile_roster(self, output: MapperOutput) -> MapperOutput:
        """Overlay current registry state onto a historical roster observation."""

        if not isinstance(output, MappedItem) or not is_agent_roster_item(output.item):
            return output
        item = output.item
        assert item.agent is not None
        registry = self.ref(item.agent.agent_id)
        updates: dict[str, object] = {}
        if item.agent.agent_path is None and registry.agent_path is not None:
            updates["agent_path"] = registry.agent_path
        if (
            item.source == "native-history" or item.agent.status == "unknown"
        ) and registry.status != "unknown":
            updates["status"] = registry.status
        if not updates:
            return output
        # The roster lifecycle fields now come from the live adapter snapshot, not
        # merely the persisted item. Mark that authority so the store can distinguish
        # a genuine current lifecycle from a stale historical ``started`` replay.
        return MappedItem(
            item=item.model_copy(
                update={
                    "agent": item.agent.model_copy(update=updates),
                    "source": "harness-live",
                }
            )
        )

    @staticmethod
    def scope_native_item(output: MapperOutput, thread_id: str) -> MapperOutput:
        if not isinstance(output, MappedItem):
            return output
        item = output.item
        if item.item_id.startswith(f"{thread_id}:"):
            return output
        return MappedItem(item=item.model_copy(update={"item_id": f"{thread_id}:{item.item_id}"}))
