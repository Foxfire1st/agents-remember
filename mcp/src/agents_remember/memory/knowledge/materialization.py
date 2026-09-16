"""The publication gate a read has to pass before it may answer from a closed snapshot.

An authored write lands in the runtime candidate; a *published* snapshot is a separate artifact
produced by an explicit publication. Those two can disagree, and when they do the disagreement
is the whole point: a reader that answers from the closed file while the runtime candidate holds
newer committed work would silently present stale knowledge as current.

So a read acquires its candidate-tree context only after this comparison, which reads the same
identity every consumer uses and reports the two it measured. It never resolves the difference:
publishing is the caller's explicit operation, and no read writes rows, publishes bytes or
attaches them to an older memory tree. The remaining half of that contract -- proving the
observed memory tree contains this exact published blob -- belongs to the candidate-tree
acquisition that already owns Git object identity.
"""

from __future__ import annotations

from pathlib import Path

import apsw

from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.refusals import (
    KnowledgeStorageError,
    RefusalFacts,
    candidate_snapshot_unpublished_refusal,
    refusal,
    selected_input_unavailable_refusal,
)
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore
from agents_remember.models.knowledge.snapshot import PublicationState


def publication_state(candidate: OpenedKnowledgeStore, published_path: Path) -> PublicationState:
    """Compare one live candidate with the closed snapshot at ``published_path``.

    ``current`` means the two hold the same logical dataset, whatever their page layout is.
    ``candidate_snapshot_unpublished`` names both identities and leaves the caller to decide
    which side moved: a newer runtime candidate needs a publication, and a stale published file
    needs to be republished or refused by the caller's own context comparison.
    """

    try:
        live = candidate.snapshot_identity()
    except (KnowledgeStorageError, apsw.Error, OSError) as error:
        return PublicationState(
            state="refused",
            refusal=refusal(
                "candidate_binding_changed",
                "read_published_snapshot",
                str(error),
                facts=RefusalFacts(record_id=str(candidate.database_path)),
                next_action=(
                    "Admit the candidate through the operation that owns its binding; a read "
                    "never repairs a namespace."
                ),
            ),
        )
    if not published_path.is_file():
        return PublicationState(
            state="refused",
            refusal=selected_input_unavailable_refusal(
                "read_published_snapshot",
                f"the closed snapshot does not exist: {published_path}",
                record_id=str(published_path),
            ),
        )
    try:
        published = dataset_identity(published_path)
    except KnowledgeStorageError as error:
        return PublicationState(
            state="refused",
            refusal=refusal(
                "unsupported_schema",
                "read_published_snapshot",
                f"the closed snapshot is not a dataset of this schema: {error}",
                facts=RefusalFacts(record_id=str(published_path)),
                next_action=(
                    "Republish the closed snapshot from the admitted candidate. A file this code "
                    "cannot read is not knowledge it can answer from."
                ),
            ),
        )
    except (apsw.Error, OSError) as error:
        return PublicationState(
            state="refused",
            refusal=selected_input_unavailable_refusal(
                "read_published_snapshot",
                f"the closed snapshot could not be read: {error}",
                record_id=str(published_path),
            ),
        )
    if published.logical_digest == live.logical_digest:
        return PublicationState(state="current", candidate=live, published=published)
    return PublicationState(
        state="candidate_snapshot_unpublished",
        candidate=live,
        published=published,
    )


def unpublished_refusal(state: PublicationState, destination_ref: str):
    """Restate a ``candidate_snapshot_unpublished`` state as the read-side refusal.

    Kept as a separate step so the state value stays a measurement and the refusal is built only
    by the caller that has decided to refuse. A state that is not the unpublished one has no
    refusal to restate and is a caller defect rather than a returned value.
    """

    if state.state != "candidate_snapshot_unpublished" or state.candidate is None:
        raise KnowledgeStorageError(
            "only a candidate_snapshot_unpublished state can be restated as a refusal"
        )
    published = state.published
    return candidate_snapshot_unpublished_refusal(
        "read_published_snapshot",
        expected=state.candidate.logical_digest,
        observed="<absent>" if published is None else published.logical_digest,
        destination_ref=destination_ref,
    )
