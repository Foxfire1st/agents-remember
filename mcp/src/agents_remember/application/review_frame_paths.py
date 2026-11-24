"""Canonical storage names and task-wide lock resource derivation for the review frame store."""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "ASSETS_DIRECTORY",
    "DRAFTS_DIRECTORY",
    "DRAFT_NAME",
    "FRAMES_DIRECTORY",
    "MANIFEST_DIGEST_NAME",
    "MANIFEST_NAME",
    "READY_POINTER",
    "RECLAMATION_NAME",
    "RECLAMATION_RECEIPT",
    "REVISIONS_DIRECTORY",
    "SEAT_COUNTER_NAME",
    "TASK_LOCK_NAME",
    "task_lock_for_frame_dir",
    "task_lock_resource",
]

FRAMES_DIRECTORY = "review-frames"
READY_POINTER = "ready.json"
REVISIONS_DIRECTORY = "revisions"
DRAFTS_DIRECTORY = "drafts"
MANIFEST_NAME = "manifest.json"
MANIFEST_DIGEST_NAME = "manifest.sha256"
ASSETS_DIRECTORY = "assets"
DRAFT_NAME = "draft.json"
RECLAMATION_NAME = "reclaimed.json"
SEAT_COUNTER_NAME = "seat-generation"
TASK_LOCK_NAME = ".frames.lock"
# The record that survives an archived task's reclamation, beside its other durable reports.
RECLAMATION_RECEIPT = "review-frames-reclaimed.json"


def task_lock_resource(task_area: Path) -> Path:
    """Return the shared task-wide frame store lock resource path under ``task_area``."""

    return task_area / TASK_LOCK_NAME


def task_lock_for_frame_dir(frame_dir: Path) -> Path:
    """Derive the shared task-wide frame store lock resource from one lineage ``frame_dir``."""

    return task_lock_resource(frame_dir.parent.parent)
