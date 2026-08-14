"""Projection of parent and multiplexed child pending interactions."""

from __future__ import annotations

from collections.abc import Callable

from agents_remember.models.conversations.content import (
    ChoiceOption,
    ChoicesBlock,
    ConversationAgentRef,
    ConversationCorrelation,
    ConversationItem,
    TextBlock,
)
from agents_remember.models.conversations.control_wire import (
    AdapterSnapshot,
    PendingInteraction,
)
from agents_remember.models.conversations.identity import (
    ProvenanceEvidence,
)
from agents_remember.serving.conversation.projectors.common import MappedItem

from .mutation_stream import ProjectionMutationStream

AgentRefReader = Callable[[str], ConversationAgentRef]


class InteractionProjection:
    """Own interaction item lifecycle, including parent-slot rotation."""

    def __init__(
        self,
        *,
        stream: ProjectionMutationStream,
        parent_thread_id: str | None,
        agent_ref: AgentRefReader,
    ) -> None:
        self._stream = stream
        self._parent_thread_id = parent_thread_id
        self._agent_ref = agent_ref
        self._parent_pending_id: str | None = None
        self._multiplexed_ids: set[str] = set()

    def reset(self) -> None:
        self._parent_pending_id = None
        self._multiplexed_ids.clear()

    def apply(self, snapshot: AdapterSnapshot) -> None:
        pending = snapshot.pending_interaction
        pending_id = pending.interaction_id if pending is not None else None
        if pending_id != self._parent_pending_id:
            previous_id = self._parent_pending_id
            self._parent_pending_id = pending_id
            if pending is not None:
                self._upsert(pending)
            if previous_id is not None:
                self._resolve(previous_id)
        self._apply_multiplexed(snapshot, parent_pending_id=pending_id)

    def _apply_multiplexed(
        self, snapshot: AdapterSnapshot, *, parent_pending_id: str | None
    ) -> None:
        current: dict[str, tuple[PendingInteraction, str]] = {}
        for entry in snapshot.pending_interactions:
            if entry.interaction_id == parent_pending_id:
                continue
            thread_id = entry.raw.get("threadId")
            if isinstance(thread_id, str) and thread_id:
                current[entry.interaction_id] = (entry, thread_id)
        for interaction_id, (entry, thread_id) in current.items():
            if interaction_id in self._multiplexed_ids:
                continue
            self._multiplexed_ids.add(interaction_id)
            agent = (
                self._interaction_agent_ref(entry, thread_id)
                if thread_id != self._parent_thread_id
                else None
            )
            self._upsert(entry, agent=agent)
        for interaction_id in sorted(self._multiplexed_ids - current.keys()):
            self._multiplexed_ids.discard(interaction_id)
            if interaction_id != parent_pending_id:
                self._resolve(interaction_id)

    def _interaction_agent_ref(
        self, entry: PendingInteraction, thread_id: str
    ) -> ConversationAgentRef:
        registry_ref = self._agent_ref(thread_id)
        label = entry.raw.get("agentLabel")
        return ConversationAgentRef(
            agent_id=thread_id,
            agent_path=registry_ref.agent_path,
            nickname=label if isinstance(label, str) and label else None,
            status=registry_ref.status,
        )

    def _upsert(
        self, pending: PendingInteraction, *, agent: ConversationAgentRef | None = None
    ) -> None:
        blocks: list[TextBlock | ChoicesBlock] = [TextBlock(block_id="prompt", text=pending.prompt)]
        if pending.choices:
            blocks.append(
                ChoicesBlock(
                    block_id="choices",
                    interaction_id=pending.interaction_id,
                    options=tuple(
                        ChoiceOption(option_id=choice, label=choice) for choice in pending.choices
                    ),
                )
            )
        item = ConversationItem(
            item_id=pending.interaction_id,
            revision=1,
            global_ordinal=1,
            lane="interaction",
            source="harness-live",
            provenance=ProvenanceEvidence(
                strength="exact",
                origin="adapter pending-interaction authority",
                producer="harness",
                observed_at=pending.created_at,
            ),
            role="system",
            kind="interaction",
            phase="waiting",
            blocks=tuple(blocks),
            correlation=ConversationCorrelation(interaction_id=pending.interaction_id),
            agent=agent,
            created_at=pending.created_at,
        )
        for mutation in self._stream.store.apply_item(MappedItem(item=item)):
            self._stream.emit(mutation)

    def _resolve(self, interaction_id: str) -> None:
        current = next(
            (item for item in self._stream.store.items() if item.item_id == interaction_id),
            None,
        )
        if current is None:
            return
        resolved = current.model_copy(
            update={
                "phase": "unknown",
                "provenance": current.provenance.model_copy(
                    update={"reason": "interaction cleared without observable outcome"}
                ),
            }
        )
        for mutation in self._stream.store.apply_item(MappedItem(item=resolved)):
            self._stream.emit(mutation)
