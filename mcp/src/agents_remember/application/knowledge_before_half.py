"""Preflight the derived index sides a review reads; no canonical dataset layout is created."""

from pathlib import Path
from typing import Literal

import apsw

from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.review import ReviewRefusal

NOT_RECORDED: Literal["not-recorded"] = "not-recorded"


def read_dataset_identity(database: Path) -> SnapshotIdentity | str:
    """The selected index's identity, or the reason it cannot be read."""
    if not database.is_file():
        return f"the index {database} is not a file"
    try:
        return dataset_identity(database)
    except (KnowledgeStorageError, apsw.Error, OSError) as error:
        return f"the index {database} could not be read ({error})"


def unreadable_half_refusal(
    baseline_database: Path, candidate_database: Path
) -> ReviewRefusal | None:
    for half, database in (("baseline", baseline_database), ("candidate", candidate_database)):
        if not database.is_file():
            continue
        reading = read_dataset_identity(database)
        if isinstance(reading, str):
            return ReviewRefusal(
                code="candidate_dataset_absent",
                detail=f"the {half} index cannot be read: {reading}",
                next_action="rebuild the index from the recorded memory tree, then reopen the review",
                offending_input=half,
            )
    return None
