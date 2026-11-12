"""Reclaiming one durable comparison generation: the two operations that may delete its content.

Existing historical generation records name each retained artifact and its deletion owner.
These deletion-only operations preserve that ownership during task archival; no new dataset
snapshot or comparison pin is produced here.

Two deliberate properties, both of which the reopen depends on:

* **Deletion history follows each owner's ordering.** Code-object release writes its
  unavailable-history record before invoking the release owner. Snapshot discard validates and
  removes a present artifact before writing its deletion record; an already absent artifact is
  recorded with no removed digest. A failure after snapshot removal can leave content gone without
  that record. A recorded deletion lets a later reopen explain why the content is unavailable.
* **Only what the manifest named, inside the scope the manifest recorded.** A code release deletes
  exactly the ref the manifest recorded, and refuses a ref that has moved. A snapshot discard deletes
  exactly the recorded ``cleanup_scope`` of each retained half. Neither touches the manifest itself,
  because the manifest is the record that the generation *existed* and a reopen has to be able to say
  so; neither reaches into another generation's directory or another leaf's namespace.

Nothing here decides *when* a comparison is finished with. Release and discard are explicit acts a
caller performs; the freeze and reopen paths never call them.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agents_remember.application.review_comparison_generation import (
    COMPARISON_MANIFEST_NAME,
    ComparisonGenerationManifest,
    ComparisonHistoryDeletion,
    generation_directory,
    read_manifest,
    write_history_deletion,
)
from agents_remember.errors import CodeObjectRetentionError, ComparisonReclamationError
from agents_remember.kernel.git_command import run_git
from agents_remember.worktrees.modules.code_object_retention import (
    CustodyNames,
    RetainedCodeObject,
    code_object_observation,
    release_retained_code_object,
    retained_object_readable,
)

__all__ = [
    "CODE_OBJECT_DELETION_OWNER",
    "SNAPSHOT_DELETION_OWNER",
    "discard_comparison_snapshots",
    "release_comparison_code_object",
]

# The two canonical deletion owners the manifest names on everything the freeze creates. They are
# spelled once, here, by the operations that perform them, so "who may delete this" is a value a
# reader can act on rather than a convention it has to know.
CODE_OBJECT_DELETION_OWNER = (
    "application.review_comparison_reclamation.release_comparison_code_object"
)
SNAPSHOT_DELETION_OWNER = "application.review_comparison_reclamation.discard_comparison_snapshots"

# The two deletion targets, spelled with the record's own literal types so a release and a discard
# cannot come to name a target the record does not have.
CodeObjectTarget = Literal["code-object"]
KnowledgeTarget = Literal["knowledge-before", "knowledge-after"]


def _now() -> str:
    """The one clock this module reads, spelled as the stored record expects it."""

    return datetime.now(UTC).isoformat(timespec="seconds")


def release_comparison_code_object(
    task_root: Path,
    leaf_id: str,
    generation_id: str,
    *,
    reason: str,
    recorded_at: str | None = None,
) -> ComparisonHistoryDeletion:
    """The deletion owner for one generation's code pin: release it and record that it is gone.

    The record is written **before** the ref is deleted, because the record is what a later reopen
    reads to tell a deliberate release from an accidental loss -- and a crash between the two then
    leaves the honest ordering (a record for a pin that is still there) rather than the misleading
    one. Release is explicit and never implicit: nothing in the freeze path calls this.
    """

    manifest = _read_manifest_for_owner(task_root, leaf_id, generation_id)
    retained = manifest.source.retained
    repository = Path(manifest.source.code_repository_root)
    if retained is None:
        raise CodeObjectRetentionError(
            "code-object-ref-absent",
            f"generation {generation_id} recorded no code pin: its tree was already held by "
            "committed history, so there is nothing this owner may release",
        )
    _require_the_ref_names_the_record(repository, retained)
    names = _custody_names(manifest)
    deletion = ComparisonHistoryDeletion(
        target=_CODE_OBJECT_TARGET,
        deletion_owner=CODE_OBJECT_DELETION_OWNER,
        cleanup_scope=retained.ref,
        reason=reason,
        recorded_at=recorded_at or _now(),
        released_custody=code_object_observation(repository, retained.tree, names),
    )
    record = write_history_deletion(task_root, manifest.leaf_id, manifest.generation_id, deletion)
    try:
        release_retained_code_object(
            repository,
            retained,
            reason=reason,
            recorded_at=deletion.recorded_at,
            names=names,
        )
    except CodeObjectRetentionError:
        record.unlink(missing_ok=True)
        raise
    return deletion


def _custody_names(manifest: ComparisonGenerationManifest) -> CustodyNames:
    """The durable history the record itself measured custody against.

    Read back from the manifest rather than re-derived from a live contract: a release happens long
    after the leaf's enclosure may be gone, and the record is the only thing that still knows which
    refs were asked. Two measurements are comparable exactly when they asked the same names.
    """

    return CustodyNames(
        durable_refs=manifest.source.custody_refs,
        recorded_commits=manifest.source.custody_commits,
    )


_CODE_OBJECT_TARGET: CodeObjectTarget = "code-object"


def _knowledge_target(side: str) -> KnowledgeTarget:
    """The deletion target of one knowledge half, in the record's own vocabulary."""

    return "knowledge-before" if side == "before" else "knowledge-after"


def _require_the_ref_names_the_record(repository: Path, retained: RetainedCodeObject) -> None:
    """Refuse a release before anything is recorded, when the ref no longer names the record.

    Checked ahead of the record for the reason the record exists: a deletion record written for a
    release that was then refused would claim a history this owner never made unavailable, and a
    later reopen would report that claim. The retention owner re-checks under its own call, so this
    is a fail-fast rather than the authority -- and a refusal that still arrives from there removes
    the record again rather than leaving it behind.
    """

    if not retained_object_readable(repository, retained) and _ref_exists(repository, retained.ref):
        raise CodeObjectRetentionError(
            "code-object-ref-moved",
            f"the retention ref {retained.ref} does not point at the recorded commit "
            f"{retained.commit}, so no release and no deletion record are made",
        )


def _ref_exists(repository: Path, ref: str) -> bool:
    """Whether a ref resolves at all, whatever it points at."""

    return run_git(repository, ["rev-parse", "--verify", "--quiet", ref]).returncode == 0


def discard_comparison_snapshots(
    task_root: Path,
    leaf_id: str,
    generation_id: str,
    *,
    reason: str,
    recorded_at: str | None = None,
) -> tuple[ComparisonHistoryDeletion, ...]:
    """The deletion owner for one generation's retained knowledge snapshots.

    Each retained half is deleted within its own recorded ``cleanup_scope`` and nowhere else -- the
    manifest is not touched, because the manifest is the record that the generation *existed* and a
    reopen has to be able to say so. A present snapshot is read, verified and removed before its
    deletion history is written; an already absent snapshot is recorded with no removed digest.
    A failure between removal and the history write can leave the snapshot gone without a record.
    """

    manifest = _read_manifest_for_owner(task_root, leaf_id, generation_id)
    directory = generation_directory(task_root, manifest.leaf_id, generation_id)
    deletions: list[ComparisonHistoryDeletion] = []
    for binding in manifest.knowledge:
        if binding.state != "retained" or binding.artifact is None:
            continue
        deletion = ComparisonHistoryDeletion(
            target=_knowledge_target(binding.side),
            deletion_owner=SNAPSHOT_DELETION_OWNER,
            cleanup_scope=binding.artifact.cleanup_scope,
            reason=reason,
            recorded_at=recorded_at or _now(),
            deleted_digest=_measure_and_remove(
                directory / binding.artifact.relative_path,
                expected=binding.artifact.sha256,
                side=binding.side,
            ),
        )
        write_history_deletion(task_root, manifest.leaf_id, manifest.generation_id, deletion)
        deletions.append(deletion)
    return tuple(deletions)


def _measure_and_remove(target: Path, *, expected: str, side: str) -> str | None:
    """Digest one retained snapshot as it is now, remove it, and return the digest that was removed.

    The record's digest is *measured*, never copied from the manifest: a snapshot whose bytes are no
    longer the ones the generation froze is refused before anything is recorded or removed, because a
    deletion record that named content this owner did not delete would be a false record of its own
    act. A snapshot that is already gone is recorded as such -- ``None`` -- so a retry of an
    interrupted discard converges instead of failing, and the record still states what was found.
    """

    try:
        payload = target.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ComparisonReclamationError(
            "snapshot-unreadable",
            f"the retained {side} snapshot {target} could not be read before its discard: {error}",
        ) from error
    observed = hashlib.sha256(payload).hexdigest()
    if observed != expected:
        raise ComparisonReclamationError(
            "snapshot-bytes-mismatch",
            f"the retained {side} snapshot {target} holds sha256 {observed}, while the generation "
            f"froze {expected}; refusing to remove content this generation did not retain, and "
            "recording no deletion for it",
        )
    target.unlink()
    return observed


def _read_manifest_for_owner(
    task_root: Path, leaf_id: str, generation_id: str
) -> ComparisonGenerationManifest:
    """Read one generation's manifest for a deletion owner, refusing an unreadable record."""

    directory = generation_directory(task_root, leaf_id, generation_id)
    return read_manifest(directory / COMPARISON_MANIFEST_NAME)
