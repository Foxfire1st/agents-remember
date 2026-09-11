"""Canonical task-document identity shared by catalog, control plane, and wire models.

The task document already is the durable work-domain identity.  A seat therefore needs
no invented logical id: its stable address is this reference plus its role.  Runtime
session, lifecycle, adapter, and vendor ids remain separate control-plane correlation.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, field_validator

MAX_TASK_REPOSITORY_LENGTH = 128
MAX_TASK_DOCUMENT_PATH_LENGTH = 4096


class TaskDocumentRef(BaseModel):
    """One JSON-primary task document under ``tasks/<repository>``.

    ``path`` is coordination-root-relative *inside* the repository task root.  Keeping
    the root out of the value makes the reference stable when the same coordination
    tree is mounted at another absolute location, while ``repository`` prevents two
    repositories with the same task-folder layout from colliding.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    repository: str
    path: str

    def __hash__(self) -> int:
        """Hash the immutable document identity by its two canonical components."""

        return hash((self.repository, self.path))

    @field_validator("repository")
    @classmethod
    def _repository_is_one_segment(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) > MAX_TASK_REPOSITORY_LENGTH:
            raise ValueError(
                f"repository exceeds {MAX_TASK_REPOSITORY_LENGTH} canonical characters"
            )
        if not cleaned or cleaned in {".", ".."} or "/" in cleaned or "\\" in cleaned:
            raise ValueError("repository must be one non-blank path segment")
        return cleaned

    @field_validator("path")
    @classmethod
    def _path_is_confined_json(cls, value: str) -> str:
        cleaned = value.strip().replace("\\", "/")
        if len(cleaned) > MAX_TASK_DOCUMENT_PATH_LENGTH:
            raise ValueError(
                f"task document path exceeds {MAX_TASK_DOCUMENT_PATH_LENGTH} canonical characters"
            )
        path = PurePosixPath(cleaned)
        if not cleaned or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("task document path must be a confined relative path")
        if path.suffix.lower() != ".json":
            raise ValueError("task document path must name the JSON-primary task document")
        return path.as_posix()

    @property
    def key(self) -> str:
        """Opaque comparison/debug key; never an agent-facing replacement identity."""

        return f"{self.repository}/{self.path}"
