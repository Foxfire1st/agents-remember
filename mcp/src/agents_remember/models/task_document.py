"""Task-document wire vocabulary shared with response models.

``StepStatus`` / ``DocStatus`` and the terminal-readiness blocker moved here
from the tasks package so response models can import them without reaching up.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

# Step/substep status carries the dashboard's granularity; the markdown render
# only has a binary checkbox, so the richer state lives in the JSON.
StepStatus = Literal["pending", "inProgress", "blocked", "done"]
# Document status stays in the ``w-02-light-task-workflow`` template vocabulary so
# the rendered ``**Status:**`` line is always a valid template value.
DocStatus = Literal["planning", "inProgress", "Completed"]
# A commanded master's closed execution contract. This is shared by persisted task documents and
# the served projection so generated clients receive the same finite vocabulary.
MasterExecutionNature = Literal["organizational", "atomic"]

CompletionUnitStatus = StepStatus | DocStatus


class CompletionBlocker(BaseModel):
    """One exact declared work unit that prevents terminal completion."""

    model_config = ConfigDict(extra="forbid")

    id: str
    parentId: str | None = None
    title: str
    status: CompletionUnitStatus
