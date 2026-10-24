"""File-surface readers for the projection (data surfaces 1-3, 5, 6, 8, 10, 12).

3a reads the surfaces the *named* state tree needs: worktree enclosures (the
contract, surface 6, plus the group layout, surface 5). 3b adds the analytical
surfaces -- converted drift counts read from the persisted JSON snapshot (never
re-classified here), route-index coverage (10), the tool-report feed (12), and
memory-ledger currency (8). Every reader reuses the producing subsystem's own
parser rather than re-parsing.

The runtime process readers (enclosures, engine facts), the analytical readers,
and the task-document readers live in responsibility-split sibling modules and
are re-exported here so the projection's public import surface is unchanged.
These functions do the file I/O at the projection's call edge; the fold itself
(:mod:`agents_remember.observer.reducer`) stays pure.
"""

from __future__ import annotations

from agents_remember.serving.projections.snapshots_impl._analytics import (
    _commit_meta_for,
    _enrich_ledger_rows,
    _git_commit_meta,
    _ledger_window,
    read_drift_snapshots,
    read_ledger,
    read_route_coverage,
    read_start_progress_entries,
    read_tool_reports,
)
from agents_remember.serving.projections.snapshots_impl._common import (
    STATUS_PAYLOAD_TTL_SECONDS,
    _as_float,
    _as_int,
    _current_phase_text,
    _iter_task_document_payloads,
    _iter_task_json,
    _report_label,
    _status_payload_cache,
    _task_doc_cache,
)
from agents_remember.serving.projections.snapshots_impl._runtime import (
    _cached_local_status,
    _enclosure_from_contract,
    _safe_status_payload,
    logger,
    read_enclosures,
    read_engine_process_facts,
    refresh_engine_process_landing,
)
from agents_remember.serving.projections.snapshots_impl._task_documents import (
    _task_doc_body_revision,
    _task_doc_node,
    _task_step_nodes,
    read_task_document_body,
    read_task_documents,
)
from agents_remember.tasks import TASK_DOCUMENT_SCHEMA

__all__ = [
    "STATUS_PAYLOAD_TTL_SECONDS",
    "TASK_DOCUMENT_SCHEMA",
    "_as_float",
    "_as_int",
    "_cached_local_status",
    "_commit_meta_for",
    "_current_phase_text",
    "_enclosure_from_contract",
    "_enrich_ledger_rows",
    "_git_commit_meta",
    "_iter_task_document_payloads",
    "_iter_task_json",
    "_ledger_window",
    "_report_label",
    "_safe_status_payload",
    "_status_payload_cache",
    "_task_doc_body_revision",
    "_task_doc_cache",
    "_task_doc_node",
    "_task_step_nodes",
    "logger",
    "read_drift_snapshots",
    "read_enclosures",
    "read_engine_process_facts",
    "read_ledger",
    "read_route_coverage",
    "read_start_progress_entries",
    "read_task_document_body",
    "read_task_documents",
    "read_tool_reports",
    "refresh_engine_process_landing",
]
