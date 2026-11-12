"""A leaf's typed planned comparison and the application ports for the explainer routes (MIK-R189 rule 4).

Re-exports the pure task-document and manifest helpers from :mod:`agents_remember.tasks.planned_comparison`
and binds the ``application``-tier adapters into :mod:`agents_remember.serving.explainer` and
:mod:`agents_remember.serving.explainer_watcher` (``application`` rank 21 -> ``serving`` rank 17).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agents_remember.application.master_architect_record import leaf_execution_receipts
from agents_remember.application.review_candidate_resolution import recorded_leaf_contract
from agents_remember.application.review_evidence_adapter import prepare_evidence
from agents_remember.application.review_explainer_receipts import compose_receipts
from agents_remember.application.review_frame_store import (
    FrameRefused,
    frame_scope,
    frames_area,
    remove_revisions,
    revision_manifest,
    usage,
)
from agents_remember.application.review_lane_classification import (
    CodeReadError,
    LaneReadError,
    open_tree_lane,
)
from agents_remember.application.review_supplemental_evidence import admit_supplemental
from agents_remember.application.review_tree_comparison import (
    TreeSideUnreadable,
    _code_sides,
    _live_draft,
    _missing_tree,
    latest_record,
    memory_converted,
    reopen_review_trees,
)
from agents_remember.models.knowledge.review_evidence import SupplementalReceipt
from agents_remember.models.knowledge.review_frame import MAX_TASK_BYTES
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord
from agents_remember.serving.explainer import ExplainerApplicationPorts
from agents_remember.serving.explainer_watcher import LeafSelection, WatcherApplicationPorts
from agents_remember.tasks.planned_comparison import (
    PLANNED_RECORDED_AT,
    agreed_revision_number,
    compose_explainer_send_message,
    contract_branch_lines,
    leaf_document,
    leaf_document_stem,
    manifest_claims,
    manifest_parts,
    master_is_organizational,
    planned_comparison,
    resolve_line_commit,
    resolve_master_contract,
    retained_revision_numbers,
    revision_kind_of,
    revision_row_of,
    serialize_frame_binding,
    serialize_grades_and_categories,
)

__all__ = [
    "PLANNED_RECORDED_AT",
    "agreed_revision_number",
    "compose_explainer_send_message",
    "contract_branch_lines",
    "explainer_application_ports",
    "fresh_supplemental_receipts",
    "leaf_document",
    "leaf_document_stem",
    "manifest_claims",
    "manifest_parts",
    "master_is_organizational",
    "planned_comparison",
    "resolve_line_commit",
    "resolve_master_contract",
    "retained_revision_numbers",
    "revision_kind_of",
    "revision_row_of",
    "serialize_frame_binding",
    "serialize_grades_and_categories",
    "task_frames_total",
    "watcher_application_ports",
]


def fresh_supplemental_receipts(
    task_root: Path, leaf_id: str, retained: Sequence[SupplementalReceipt]
) -> tuple[SupplementalReceipt, ...]:
    stem = leaf_document_stem(task_root, leaf_id)
    document = leaf_document(task_root, leaf_id) or {}
    composed = {
        receipt.canonical_source: receipt
        for receipt in compose_receipts(task_root, document, leaf_id)
    }
    fresh: list[SupplementalReceipt] = []
    seen_sources: set[str] = set()
    for previous in retained:
        seen_sources.add(previous.canonical_source)
        admitted = admit_supplemental(task_root, previous.canonical_source, leaf_document=stem)
        if admitted.state != "present":
            fresh.append(
                previous.model_copy(
                    update={
                        "digest": None,
                        "byte_size": 0,
                        "read_state": "unavailable",
                        "limitation": admitted.detail,
                    }
                )
            )
        else:
            changed = admitted.digest != previous.digest
            fresh.append(
                previous.model_copy(
                    update={
                        "digest": admitted.digest,
                        "byte_size": admitted.byte_size,
                        "read_state": "changed" if changed else "present",
                    }
                )
            )
    for source, receipt in composed.items():
        if source not in seen_sources:
            fresh.append(receipt)
    return tuple(fresh)


def task_frames_total(task_root: Path, leaf_id: str) -> dict[str, Any]:
    """The task's frame area in bytes across all leaves, against the task's ceiling."""

    try:
        task_area = frames_area(task_root, leaf_id).parent
        used = 0
        if task_area.is_dir():
            for child in sorted(task_area.iterdir()):
                if child.name.startswith(".") or not child.is_dir():
                    continue
                if child.is_symlink():
                    raise FrameRefused(
                        f"the task store path {child} is not physically inside its area"
                    )
                held = usage(child, include_frames=False)
                if held.get("held"):
                    raise FrameRefused(str(held["held"][0].get("reason") or "unreadable frame"))
                used += int(held.get("bytes") or 0)
    except (ValueError, FrameRefused) as error:
        return {"state": "unavailable", "detail": str(error), "limit": MAX_TASK_BYTES}
    return {"state": "ok", "used": used, "bytes": used, "limit": MAX_TASK_BYTES}


def watcher_application_ports(
    turn_end_captured: Callable[[LeafSelection, ReviewTreeComparisonRecord], None] | None = None,
    coverage: Callable[[Mapping[tuple[str, str, str], frozenset[str]]], None] | None = None,
) -> WatcherApplicationPorts:
    """The application-tier comparison and lane ports the moved watcher calls."""

    return WatcherApplicationPorts(
        recorded_leaf_contract=recorded_leaf_contract,
        latest_comparison=latest_record,
        memory_converted=memory_converted,
        draft_live_comparison=_live_draft,
        open_tree_lane=open_tree_lane,
        execution_receipts=leaf_execution_receipts,
        capture_errors=(TreeSideUnreadable,),
        lane_errors=(CodeReadError, LaneReadError),
        turn_end_captured=turn_end_captured,
        coverage=coverage,
    )


def explainer_application_ports(
    turn_end_captured: Callable[[LeafSelection, ReviewTreeComparisonRecord], None] | None = None,
    coverage: Callable[[Mapping[tuple[str, str, str], frozenset[str]]], None] | None = None,
) -> ExplainerApplicationPorts:
    """The application-tier frame, evidence and comparison ports the explainer routes call.

    ``turn_end_captured`` is handed each capture the moved watcher makes at a turn end;
    ``coverage`` the leaves the moved watcher tracks, with why, for the worktree watch.
    """

    return ExplainerApplicationPorts(
        recorded_leaf_contract=recorded_leaf_contract,
        prepare_evidence=prepare_evidence,
        compose_receipts=compose_receipts,
        frame_refused_cls=FrameRefused,
        frame_scope=frame_scope,
        remove_revisions=remove_revisions,
        revision_manifest=revision_manifest,
        fresh_supplemental_receipts=fresh_supplemental_receipts,
        task_frames_total=task_frames_total,
        code_sides=_code_sides,
        missing_tree=_missing_tree,
        reopen_review_trees=reopen_review_trees,
        watcher=watcher_application_ports(turn_end_captured, coverage),
    )
