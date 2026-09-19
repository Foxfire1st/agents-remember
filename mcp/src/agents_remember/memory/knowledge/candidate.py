"""The one admitted candidate-change operation: a validated batch, or nothing at all.

Everything else in the substrate mutates through here. The operation is deliberately narrow:

* **It writes candidates only.** A batch names its lane, and the lane has to be the admitted draft
  or task candidate. A historical or accepted snapshot is refused by name before a statement runs,
  and the command union contains no promotion of any kind, so "this operation never accepts
  knowledge" is a property of its vocabulary rather than a rule it remembers to apply.
* **It is all-or-nothing.** The preconditions, the commands and the integrity passes all run inside
  one ``BEGIN IMMEDIATE`` transaction under the candidate's one resource lock. A refusal rolls that
  transaction back, so a batch whose last command fails leaves the dataset exactly as it was --
  including the rows its earlier commands had already inserted.
* **Its receipts are facts.** :class:`MutationResult` reports the logical identity before and after,
  the exact rows written and, on failure, the typed refusal. It has no field that could carry a
  semantic judgement, an approval or an acceptance.

Two things this operation deliberately does *not* offer. There is no retry: a caller that loses the
response rereads the candidate and reconciles its own explicit proposal rather than having the
operation replay it. And there is no arbitrary SQL: the union's own membership is the entire reach,
and a caller cannot construct a member outside it. That membership has exactly one declaration --
``ProposedCommand`` in :mod:`agents_remember.models.knowledge.candidate` -- so no count of it is
retyped here: a retyped count is a second declaration, and this one had already rotted to "twelve"
while the union carried thirty-one members (D-40).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import apsw

from agents_remember.memory.knowledge import logical
from agents_remember.memory.knowledge.batch_commands import (
    BatchLedger,
    apply_commands,
    require_after_integrity,
)
from agents_remember.memory.knowledge.batch_preconditions import require_preconditions
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    KnowledgeStorageError,
    SqliteFailureContext,
    batch_context_digest_refusal,
    batch_context_refusal,
    batch_target_not_candidate_refusal,
    batch_task_binding_unresolved_refusal,
    map_sqlite_error,
)
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.candidate import (
    CANDIDATE_LANES,
    ChangeBatch,
    KnowledgeContext,
    MutationResult,
    SnapshotIdentity,
    context_digest,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore


def change_candidate(
    store: OpenedKnowledgeStore,
    batch: ChangeBatch,
    *,
    authorship: Authorship,
) -> MutationResult:
    """Apply one candidate change batch atomically, or refuse without writing anything.

    ``authorship`` is the admitted provenance envelope, not a field of the batch: it comes from the
    application that resolved the destination, so no part of a submitted payload can become the
    stored author, the stored authorization or the recorded instant.
    """

    denied = require_writable_lane(batch.expected)
    if denied is not None:
        return _refused_result(batch, denied, _unbound_snapshot(batch))
    with store.exclusive_candidate_lock("change_candidate") as lock_refusal:
        if lock_refusal is not None:
            return _refused_result(batch, lock_refusal, _unbound_snapshot(batch))
        return _within_batch_transaction(store, batch, authorship)


def require_writable_lane(context: KnowledgeContext) -> KnowledgeRefusal | None:
    """Refuse a context whose lane this increment may not write, or return ``None``.

    Two rules, both of them fail-closed, and both checked before the lock because they are
    properties of the request rather than of the database:

    * a lane that is not a candidate at all -- a historical or accepted snapshot -- is refused by
      name, without the operation opening a transaction on the candidate;
    * a ``task-candidate`` lane is refused until the resolved, owner-validated task binding the
      packet requires exists. This operation cannot resolve a task contract, so accepting the lane
      on the strength of a caller-supplied reference would be exactly the fail-open the packet's
      task-authority sentence forbids. The refusal names the missing binding rather than pretending
      the lane is unsupported.
    """

    if context.lane not in CANDIDATE_LANES:
        return batch_target_not_candidate_refusal(context.lane, context.repository_id)
    if context.lane == "task-candidate":
        return batch_task_binding_unresolved_refusal(context.task_ref, context.repository_id)
    return None


# The name this check carried before the task lane was closed. Kept so an existing caller keeps
# working, and so the earlier spelling does not silently acquire the weaker meaning.
require_candidate_lane = require_writable_lane


def _within_batch_transaction(
    store: OpenedKnowledgeStore, batch: ChangeBatch, authorship: Authorship
) -> MutationResult:
    """Run the whole operation inside one immediate transaction over the candidate."""

    before = _bound_snapshot(store)
    try:
        with store.immediate_transaction():
            return _apply_within_transaction(store, batch, authorship, before)
    except KnowledgeRefused as refused:
        return _refused_result(batch, refused.refusal, before)
    except apsw.Error as error:
        # A failure raised outside the apply loop -- during the preconditions, the integrity pass or
        # the commit itself -- has no observed command position, so the mapping says so instead of
        # naming a command the batch merely happened to declare last.
        return _refused_result(batch, _mapped_failure(error, None, None), before)


def _apply_within_transaction(
    store: OpenedKnowledgeStore,
    batch: ChangeBatch,
    authorship: Authorship,
    before: SnapshotIdentity,
) -> MutationResult:
    """Check, apply and re-prove one batch inside the caller's open transaction."""

    _require_bound_context(store, batch)
    require_preconditions(store, batch)
    application = apply_commands(store, batch.commands, authorship)
    observed = application.observed_failure()
    if observed is not None:
        # The database refused a command. The index it was running travels with the outcome, so the
        # refusal names the command that failed rather than the last one the caller declared.
        raise KnowledgeRefused(_mapped_failure(*observed))
    require_after_integrity(store)
    return _success_result(before, _bound_snapshot(store), application.ledger)


def _require_bound_context(store: OpenedKnowledgeStore, batch: ChangeBatch) -> None:
    """Compare the batch's resolved context with the candidate the store actually holds open.

    Three comparisons, and each one is a refusal rather than a repair:

    * the namespace the destination is bound to, so a batch cannot address another repository's
      knowledge through an admitted handle;
    * the presented context's own digest, so a context whose fields were edited after it was
      resolved is refused instead of being compared field by field against the database;
    * the logical dataset identity, so a batch authored against a different dataset is refused with
      both identities named rather than rebased onto whatever arrived meanwhile.
    """

    context = batch.expected
    if context.repository_id != store.repository_id:
        raise KnowledgeRefused(
            batch_context_refusal(expected=store.repository_id, observed=context.repository_id)
        )
    observed_context = context_digest(context)
    if observed_context != context.context_digest:
        raise KnowledgeRefused(
            batch_context_digest_refusal(context.context_digest, observed_context)
        )
    observed = _bound_snapshot(store)
    if observed.logical_digest != context.knowledge.logical_digest:
        raise KnowledgeRefused(
            batch_context_refusal(
                expected=context.knowledge.logical_digest, observed=observed.logical_digest
            )
        )
    if observed.schema_version != context.knowledge.schema_version:
        raise KnowledgeRefused(
            batch_context_refusal(
                expected=context.knowledge.schema_version, observed=observed.schema_version
            )
        )


def _success_result(
    before: SnapshotIdentity, after: SnapshotIdentity, ledger: BatchLedger
) -> MutationResult:
    """Build the receipt for a batch that committed, or for one that changed nothing."""

    if after == before:
        return MutationResult(state="no_change", before=before, after=after)
    return _changed_result(before, after, ledger)


def _changed_result(
    before: SnapshotIdentity, after: SnapshotIdentity, ledger: BatchLedger
) -> MutationResult:
    """Build the receipt for a batch whose committed result differs from the dataset it started on.

    Every command that changed the dataset contributes an entry, including a removal, which reports
    the identity and the digest of the row that is gone. A dataset that moved with no entry at all
    would mean the ledger missed its own writes, and the model refuses such a receipt -- that
    combination is a defect the result cannot express, not an outcome this function invents.
    """

    return MutationResult(
        state="changed", before=before, after=after, changed=tuple(ledger.changed)
    )


def _refused_result(
    batch: ChangeBatch, refusal: KnowledgeRefusal, before: SnapshotIdentity
) -> MutationResult:
    """Build the receipt for a refusal: nothing was written, so after equals before.

    The result model enforces that equality, which is why a refusal never has to describe a
    partially applied batch: there is no state in which one exists.
    """

    del batch
    return MutationResult(state="refused", before=before, after=before, refusal=refusal)


def _bound_snapshot(store: OpenedKnowledgeStore) -> SnapshotIdentity:
    """Return the candidate's logical identity, refusing an unbound or damaged database."""

    repository = store.get_repository()
    if repository is None:
        raise KnowledgeStorageError(
            f"the candidate database is not bound to repository namespace {store.repository_id}"
        )
    return logical.snapshot_identity(store.connection, repository, store.schema.schema_name)


def _unbound_snapshot(batch: ChangeBatch) -> SnapshotIdentity:
    """Return the identity a pre-transaction refusal reports, derived from the request context.

    A refusal raised before the dataset was read cannot report a digest it did not observe, so it
    reports the context's own -- which is exactly the identity the caller stated and the operation
    never reached.
    """

    return SnapshotIdentity(
        repository_id=batch.expected.repository_id,
        schema_version=batch.expected.knowledge.schema_version,
        logical_digest=batch.expected.knowledge.logical_digest,
    )


def _mapped_failure(error: apsw.Error, position: int | None, kind: str | None) -> KnowledgeRefusal:
    """Translate a surviving database failure into the batch's typed refusal.

    A constraint failure carries no operation identity of its own, so the mapping takes the position
    and kind the apply loop *observed*. When the failure arrived outside the loop -- during a
    precondition, the integrity pass or the commit -- there is no observed position, and the refusal
    says that rather than naming a command the batch merely happened to declare last.
    """

    return map_sqlite_error(
        error,
        SqliteFailureContext(
            operation="change_candidate",
            table="candidate",
            record_id=f"{position}:{kind}" if position is not None else "outside_the_apply_loop",
        ),
    )


__all__ = [
    "change_candidate",
    "require_candidate_lane",
    "require_writable_lane",
]
