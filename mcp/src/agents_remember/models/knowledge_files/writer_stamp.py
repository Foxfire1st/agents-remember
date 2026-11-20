"""Who wrote a revision of a knowledge record: the writer's stamp.

The product stamps a revision when it stores it; no tool input carries a stamp. The stamp names the
role, harness, model, effort and seat of the caller, the task and the owner (leaf, wave or
crossing) of the write, and the time. Role, seat and task come from the caller's launch binding;
harness, model and effort from its launch receipt. A value the product cannot resolve reads
:data:`UNKNOWN`, and ``unknown`` says why for each such field, so a reader never takes a missing
fact for a recorded one.

``by`` says what wrote the stamp: ``agent`` for an agent's call of a writing tool, ``product`` for a
write the product makes on its own (``operation`` names it), ``conversion`` for the one-time
conversion of the existing memory, which gives old revisions a stamp without writing a revision.

The stamp of a record's current revision stands in the record (``writer``); the history file of the
owner that wrote a revision names it with its stamp (:class:`.history.WrittenRevision`), so the
stamps of earlier revisions are read from the same tree.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import model_validator

from agents_remember.models.knowledge_files.shapes import FileModel, Label, Text

UNKNOWN: Final = "unknown"
StampSource = Literal["agent", "product", "conversion"]
STAMP_VALUE_FIELDS: Final = ("role", "harness", "model", "effort", "seat", "task")


class WriterStamp(FileModel):
    """``{ by, role, harness, model, effort, seat, task, leaf|wave|crossing?, at, operation?, unknown? }``."""

    by: StampSource
    role: Label
    harness: Label
    model: Label
    effort: Label
    seat: Label
    task: Label
    leaf: Label | None = None
    wave: Label | None = None
    crossing: Label | None = None
    at: Label
    operation: Label | None = None
    unknown: dict[str, Text] | None = None

    @model_validator(mode="after")
    def _require_consistent_stamp(self) -> WriterStamp:
        owners = [one for one in (self.leaf, self.wave, self.crossing) if one is not None]
        if len(owners) > 1:
            raise ValueError("a stamp names at most one of leaf, wave or crossing")
        if (self.by != "agent") != (self.operation is not None):
            raise ValueError("'operation' names a product or conversion write and nothing else")
        reasons = self.unknown or {}
        unexplained = [
            name
            for name in STAMP_VALUE_FIELDS
            if getattr(self, name) == UNKNOWN and name not in reasons
        ]
        if unexplained:
            raise ValueError(f"an unknown stamp value needs its reason in 'unknown': {unexplained}")
        stray = sorted(
            set(reasons) - {n for n in STAMP_VALUE_FIELDS if getattr(self, n) == UNKNOWN}
        )
        if stray:
            raise ValueError(f"'unknown' explains only fields that read {UNKNOWN!r}: {stray}")
        return self

    @property
    def owner_id(self) -> str | None:
        """The leaf, wave or crossing the stamped write belonged to; None outside any owner."""

        return self.leaf or self.wave or self.crossing

    def writer_key(self) -> tuple[str, str, str]:
        """The writer as counted: role, harness and model."""

        return (self.role, self.harness, self.model)
