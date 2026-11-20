"""A record's standing in one tree: its derived state and the writer of its current revision.

The state of a revision (proposed, reviewed or consolidated) is never stored in a record; it is
derived from the history files of the same tree and the converted review facts beside them
(:mod:`agents_remember.models.knowledge_files.record_state`). The index derives it once, when it is built from a tree, and stores it with the
record's row together with the role, harness and model of the writer of the current revision, so
a reader learns both by one lookup and never searches the history files.

The writer is the stamp the record carries for its current revision, or the unique stamp whose
history row proves its exact content. An unconverted record whose stored state word still shows
its legacy shape reads as proposed and written by ``unknown``. A retired record has no state.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.memory.knowledge_index.projection import RETIRED_STATUS
from agents_remember.models.knowledge_files.record_state import RecordState, RevisionStates
from agents_remember.models.knowledge_files.records import KnowledgeRecord
from agents_remember.models.knowledge_files.writer_stamp import UNKNOWN


@dataclass(frozen=True)
class RecordWriter:
    """Who wrote a record's current revision, as writers are counted: role, harness and model."""

    role: str
    harness: str
    model: str


UNKNOWN_WRITER = RecordWriter(UNKNOWN, UNKNOWN, UNKNOWN)


@dataclass(frozen=True)
class RecordStanding:
    """A record's state at its revision in one tree, and the writer of that revision."""

    id: str
    revision: int | None
    state: RecordState | None
    writer: RecordWriter


class StandingDeriver:
    """The history files of one tree, read once, answering each record's standing by lookup."""

    def __init__(self, states: RevisionStates) -> None:
        self._states = states

    def standing_of(self, record: KnowledgeRecord) -> RecordStanding:
        revision = getattr(record, "revision", None)
        state, stamp = (
            self._states.current_standing(
                record, retired=getattr(record, "status", None) == RETIRED_STATUS
            )
            if isinstance(revision, int)
            else (None, record.writer)
        )
        writer = (
            UNKNOWN_WRITER
            if stamp is None
            else RecordWriter(stamp.role, stamp.harness, stamp.model)
        )
        return RecordStanding(id=record.id, revision=revision, state=state, writer=writer)
