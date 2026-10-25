"""Registry that maps MCP tool payload builders to declared response models.

Response-model convention (one place, deliberately two tiers):

- Tools whose response shape is fully owned by this package use a STRICT model
  (``StrictResponseModel`` / ``ResponseModel`` / ``ToolResponse``, ``extra="forbid"``)
  so the field set is a real, drift-proof contract. ``context_packet`` (the
  slim agent answer ``SlimContextPacket``; the whole ``ContextPacketV2`` stays
  behind the answer's log) and ``server_info`` are the
  exemplars; new AR-owned responses should follow them.
- Tools that surface provider-native / raw diagnostic detail (a worktree command's
  own output, skill installation, benchmark runs, memory-quality detail) use a FLEXIBLE model
  (``FlexibleResponseModel`` / ``FlexibleToolResponse``, ``extra="allow"``) ON
  PURPOSE: that payload is owned by the upstream producer and must not be
  rejected when the producer adds a field. This is a tolerated-drift surface,
  not an un-validated one -- the envelope (ok/operation/tokens) is still typed.
  The retired CGC/GrepAI search-provider tools are not part of this tier: they are
  gone, not merely flexible.

Application entry points return plain dicts; ``_tool_payload`` validates them against the
model registered here and finalizes token counts. Pick STRICT unless the
payload genuinely embeds provider-native detail.
"""

from __future__ import annotations

from agents_remember.models.base import ResponseEnvelope
from agents_remember.models.context_packet import SlimContextPacket
from agents_remember.models.core import ServerInfoResponse
from agents_remember.models.direct_execution_grant import DirectExecutionGrantResponse
from agents_remember.models.direct_landing import DirectLandingResponse
from agents_remember.models.lifecycles.curator_coherence import CuratorCoherenceResponse
from agents_remember.models.lifecycles.finalize import LifecycleFinalizeTaskResponse
from agents_remember.models.memory import (
    CitationFixResponse,
    CitationNoteRevisionResponse,
    MemoryBaselineAdoptResponse,
    MemoryBaselineStatusResponse,
    MemoryCarryoverApplyResponse,
    MemoryCarryoverPlanResponse,
    MemoryInitResponse,
    MemoryQualityCheckResponse,
    RouteIndexRefreshResponse,
)
from agents_remember.models.queue.closeout_queue import CloseoutQueueResponse
from agents_remember.models.read_files import ReadArFilesResponse
from agents_remember.models.role_agents import (
    RoleMessageResponse,
    RoleStartResponse,
    RoleStartSetResponse,
)
from agents_remember.models.runtime import RuntimeInstallResponse
from agents_remember.models.structural.agent import RetireChildResponse
from agents_remember.models.task_doc import TaskDocResponse, TaskReopenResponse
from agents_remember.models.tools.explainer_control import ExplainerControlResponse
from agents_remember.models.tools.knowledge_responses import (
    KnowledgeDiffResponse,
    KnowledgeIntegrityCheckResponse,
    KnowledgeReadResponse,
    KnowledgeWriteResponse,
)
from agents_remember.models.user_settings_init import UserSettingsInitResponse
from agents_remember.models.worktree import (
    WorktreeAbandonResponse,
    WorktreeAttachResponse,
    WorktreeCheckpointLandingResponse,
    WorktreeCleanupResponse,
    WorktreeCloseoutApplyResponse,
    WorktreeCloseoutPreviewResponse,
    WorktreeEnclosureAdoptResponse,
    WorktreeIntegrateResponse,
    WorktreeOperationControlResponse,
    WorktreePauseResponse,
    WorktreeRecordLandingResponse,
    WorktreeStartResponse,
    WorktreeStatusResponse,
    WorktreeSyncResponse,
)

# ``type[ResponseEnvelope]``, not ``type[BaseModel]``: every registered model is a
# ``ResponseModel`` or a ``FlexibleResponseEnvelope``, and saying so is what lets the choke
# point set ``nextStep`` on the validated response before the dump.
# ``BaseModel`` here made that field unreachable by type, which is how it ended up
# being written into the already-dumped dict instead.
TOOL_RESPONSE_MODELS: dict[str, type[ResponseEnvelope]] = {
    "server_info": ServerInfoResponse,
    "context_packet": SlimContextPacket,
    "read_ar_files": ReadArFilesResponse,
    "retire_child": RetireChildResponse,
    "runtime_install": RuntimeInstallResponse,
    "memory_quality_check": MemoryQualityCheckResponse,
    "citation_fix": CitationFixResponse,
    "citation_note_revision": CitationNoteRevisionResponse,
    "route_index_refresh": RouteIndexRefreshResponse,
    "memory_init": MemoryInitResponse,
    "worktree_start": WorktreeStartResponse,
    "worktree_enclosure_adopt": WorktreeEnclosureAdoptResponse,
    "worktree_attach": WorktreeAttachResponse,
    "worktree_status": WorktreeStatusResponse,
    "worktree_sync": WorktreeSyncResponse,
    "worktree_pause": WorktreePauseResponse,
    "worktree_closeout_preview": WorktreeCloseoutPreviewResponse,
    "worktree_closeout_apply": WorktreeCloseoutApplyResponse,
    "worktree_integrate": WorktreeIntegrateResponse,
    "worktree_checkpoint_landing": WorktreeCheckpointLandingResponse,
    "worktree_record_landing": WorktreeRecordLandingResponse,
    "worktree_operation_control": WorktreeOperationControlResponse,
    "worktree_cleanup": WorktreeCleanupResponse,
    "worktree_abandon": WorktreeAbandonResponse,
    "task_reopen": TaskReopenResponse,
    "memory_baseline_status": MemoryBaselineStatusResponse,
    "memory_baseline_adopt": MemoryBaselineAdoptResponse,
    "memory_carryover_plan": MemoryCarryoverPlanResponse,
    "memory_carryover_apply": MemoryCarryoverApplyResponse,
    "lifecycle_finalize_task": LifecycleFinalizeTaskResponse,
    "task_doc": TaskDocResponse,
    "curator_coherence": CuratorCoherenceResponse,
    "closeout_queue": CloseoutQueueResponse,
    "direct_landing": DirectLandingResponse,
    "direct_execution_grant": DirectExecutionGrantResponse,
    "role_start": RoleStartResponse,
    "role_start_set": RoleStartSetResponse,
    "role_message": RoleMessageResponse,
    "user_settings_init": UserSettingsInitResponse,
    "knowledge_read": KnowledgeReadResponse,
    "knowledge_diff": KnowledgeDiffResponse,
    "knowledge_integrity_check": KnowledgeIntegrityCheckResponse,
    "knowledge_write": KnowledgeWriteResponse,
    "explainer_control": ExplainerControlResponse,
}
