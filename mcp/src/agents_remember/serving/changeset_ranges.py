"""Typed disk answers for the master's immutable commit-pair ranges only."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from time import time
from typing import Any

from pydantic import BaseModel, ConfigDict

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.build_info import process_serving_build
from agents_remember.serving.review_answer_reuse import (
    AnswerCodec,
    DiskAnswers,
    DiskBudget,
    DiskPublication,
)

type RangeKey = tuple[str, str, str]


class _RangeFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str
    insertions: int | None
    deletions: int | None
    status: str


class _RangeAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    files: list[_RangeFile]


@dataclass
class MasterRangeAnswers:
    """Bounded storage below master selection and live sidecar decoration.

    Rows use the native producer's exact field order. No reader of the generic
    task/file-diff routes selects this capability.
    """

    directory: Path
    namespace: str
    capacity: int = 1024
    # Immutable pairs do not expire with wall time. Capacity and byte limits
    # reclaim them; a short TTL would force unchanged ranges to re-diff at restart.
    max_disk_bytes: int = 64 * 1024 * 1024

    def __post_init__(self) -> None:
        self._disk = DiskAnswers.open(self.directory, self.namespace)
        self._codec: AnswerCodec[_RangeAnswer] = AnswerCodec(_RangeAnswer)

    def _budget(self) -> DiskBudget:
        return DiskBudget(time(), self.max_disk_bytes, self.capacity)

    def load(self, key: RangeKey) -> list[dict[str, Any]] | None:
        if self._disk is None:
            return None
        found = self._disk.load(key, (), self._codec, self._budget())
        if found is None:
            return None
        answer = found
        return [file.model_dump(mode="json") for file in answer.files]

    def publish(
        self,
        answers: Mapping[RangeKey, list[dict[str, Any]]],
        abandoned: Event | None,
    ) -> tuple[DiskPublication, ...]:
        if self._disk is None:
            return ()
        values = tuple(
            (key, _RangeAnswer.model_validate({"files": rows})) for key, rows in answers.items()
        )
        return self._disk.save_batch(
            values,
            (),
            self._codec,
            self._budget(),
            cancelled=abandoned.is_set if abandoned is not None else lambda: False,
        )

    def withdraw(self, publications: tuple[DiskPublication, ...]) -> None:
        if self._disk is not None:
            self._disk.withdraw(publications)


def runtime_master_ranges(config: McpRuntimeConfig) -> MasterRangeAnswers | None:
    """Bind persistence once to the exact producing build and coordination owner."""
    source = process_serving_build().source_digest
    if source is None:
        return None
    return MasterRangeAnswers(config.coordination_root / "runtime" / "changeset-ranges", source)
