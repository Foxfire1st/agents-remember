"""The observation one recorded source anchor earns against a requested code tree.

This is the anchor half of the read vocabulary in :mod:`agents_remember.models.knowledge.read`,
which re-exports every name here: the closed set of resolution states an observation can reach and
the observation value itself, including the structured line ranges an exact recorded blob supports.
It is a value vocabulary only; the resolver that produces it is
:mod:`agents_remember.memory.knowledge.read_anchors`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PATH_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    UUID_PATTERN,
    KnowledgeModel,
)
from agents_remember.models.knowledge.source import LineRangeLocator, SourceLocator

__all__ = ["ANCHOR_RESOLUTIONS", "AnchorResolution", "AnchorResolutionState"]

AnchorResolutionState = Literal[
    "exact_recorded_blob",
    "recorded_blob_mismatch",
    "path_absent",
    "entry_not_blob",
    "recorded_object_unavailable",
    "unsupported_locator",
    "not_requested",
]

ANCHOR_RESOLUTIONS: tuple[str, ...] = (
    "exact_recorded_blob",
    "recorded_blob_mismatch",
    "path_absent",
    "entry_not_blob",
    "recorded_object_unavailable",
    "unsupported_locator",
    "not_requested",
)


class AnchorResolution(KnowledgeModel):
    """One recorded anchor observed against the requested code snapshot.

    The recorded identity stays on the observation whatever the outcome: an anchor whose bytes
    differ, whose path is gone or whose locator this increment cannot resolve is reported as that
    observation and is never promoted to a current realization, and no path is looked up in a
    working tree or at HEAD instead.
    """

    anchor_id: str = Field(pattern=UUID_PATTERN)
    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    recorded_source_identity: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    observed_source_identity: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    locator: SourceLocator
    resolution: AnchorResolutionState
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    # The one-based inclusive line ranges the recorded locator addresses in the recorded blob, as
    # structured values: the recorded range of a ``line_range`` locator, or every extent the shipped
    # extractor found defining a ``symbol`` locator's name. They are present only on an exact
    # recorded blob, because a range is a statement about bytes this observation actually holds;
    # a ``file`` locator addresses the whole blob and carries none.
    resolved_ranges: tuple[LineRangeLocator, ...] = ()

    @model_validator(mode="after")
    def _require_ranges_only_on_the_recorded_bytes(self) -> AnchorResolution:
        if self.resolved_ranges and self.resolution != "exact_recorded_blob":
            raise ValueError(
                "a resolved range addresses the exact recorded blob; beside any other observation "
                "it would place the recorded claim on bytes this read did not find at the path"
            )
        return self
