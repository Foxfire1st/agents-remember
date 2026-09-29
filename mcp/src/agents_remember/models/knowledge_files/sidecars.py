"""Local knowledge: onboarding sidecars beside their Markdown, and the layout marker (MIK-R21 rules 1, 5).

* **File sidecar** ``onboarding/<source path>.json`` (``ar-onboarding-file/v1``): ``path``,
  ``references{}`` for the ``[n]`` markers of the paired Markdown, ``realizes[]`` (symbol realizes
  invariant), and ``proves[]`` (test proves invariant) on a test file's sidecar only. Realization
  and proof anchors, and any reference anchor for the sidecar's own file, omit ``path``.
* **Route sidecar** ``onboarding/<route>/overview.json`` (``ar-onboarding-route/v1``): ``path`` (the
  route directory, ``.`` for the repository root route) and ``references{}``. Every anchor names its
  path. The route-index fields (``sourceScope``, ``childRoutes``, ``coveredFiles``,
  ``routingTerms``, ``hotPath``, ``fallback``) are **not** part of it: they stay in the generated,
  uncommitted ``overview.index.json`` cache, and ``extra="forbid"`` refuses them here.
* **Layout marker** ``knowledge/layout.json`` (``ar-memory-layout/v2``): a memory tree is converted
  exactly when it holds this file.

An *onboarding route* is a directory under ``onboarding/`` that contains an ``overview.md``; the
*governing route* of a path is the onboarding route that is the nearest ancestor of its onboarding
location.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import AfterValidator, Field, model_validator

from agents_remember.models.knowledge.base import PATH_MAX_LENGTH
from agents_remember.models.knowledge_files.ids import (
    PROOF_ID_PATTERN,
    REALIZATION_ID_PATTERN,
)
from agents_remember.models.knowledge_files.shapes import (
    Anchor,
    EntryOrigin,
    FileModel,
    InvariantId,
    Label,
    References,
    RepositoryPath,
    Text,
    anchors_of,
    require_repository_path,
    require_unique,
)

FILE_SIDECAR_SCHEMA: Final = "ar-onboarding-file/v1"
ROUTE_SIDECAR_SCHEMA: Final = "ar-onboarding-route/v1"
LAYOUT_MARKER_SCHEMA: Final = "ar-memory-layout/v2"
ROOT_ROUTE_PATH: Final = "."

RealizationRole = Literal[
    "primary-authority",
    "enforcement",
    "propagation-persistence",
    "support",
    "presentation",
    "unclassified",
]


class _Entry(FileModel):
    invariant: InvariantId
    anchor: Anchor
    origin: EntryOrigin | None = None

    @model_validator(mode="after")
    def _require_own_file_anchor(self) -> _Entry:
        if self.anchor.path is not None:
            raise ValueError(
                "a realization or proof anchor sits in its sidecar's own file and omits 'path'"
            )
        return self


class RealizationEntry(_Entry):
    """One ``realizes`` entry: this file's anchor realizes ``invariant`` in ``role``, because ..."""

    id: str = Field(pattern=REALIZATION_ID_PATTERN)
    role: RealizationRole
    rationale: Text


class ProofEntry(_Entry):
    """One ``proves`` entry: the anchored test demonstrates ``facet`` of ``invariant``."""

    id: str = Field(pattern=PROOF_ID_PATTERN)
    facet: Text


class FileSidecar(FileModel):
    """``ar-onboarding-file/v1``: the machine facts of one onboarded source file."""

    schema_: Literal["ar-onboarding-file/v1"] = Field(default=FILE_SIDECAR_SCHEMA, alias="schema")
    path: RepositoryPath
    references: References
    realizes: tuple[RealizationEntry, ...]
    proves: tuple[ProofEntry, ...] | None = None

    @model_validator(mode="after")
    def _require_distinct_entries_and_own_path_omitted(self) -> FileSidecar:
        entry_ids = tuple(entry.id for entry in (*self.realizes, *(self.proves or ())))
        require_unique(entry_ids, what="entry ids")
        for anchor in anchors_of(self.references):
            if anchor.path == self.path:
                raise ValueError(
                    "an anchor in the sidecar's own source file omits 'path' "
                    f"(found path {self.path!r})"
                )
        return self


def _require_route_path(value: str) -> str:
    return value if value == ROOT_ROUTE_PATH else require_repository_path(value)


RoutePath = Annotated[
    str, Field(min_length=1, max_length=PATH_MAX_LENGTH), AfterValidator(_require_route_path)
]
"""A route directory: a repository-relative path, or ``.`` for the repository root route."""


class RouteSidecar(FileModel):
    """``ar-onboarding-route/v1``: the references of one route's ``overview.md``."""

    schema_: Literal["ar-onboarding-route/v1"] = Field(default=ROUTE_SIDECAR_SCHEMA, alias="schema")
    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    references: References

    @model_validator(mode="after")
    def _require_route_path_and_anchor_paths(self) -> RouteSidecar:
        _require_route_path(self.path)
        if any(anchor.path is None for anchor in anchors_of(self.references)):
            raise ValueError("every anchor in a route sidecar names its path")
        return self


class LayoutMarker(FileModel):
    """``knowledge/layout.json``: the tree is converted, with this conversion-format version."""

    schema_: Literal["ar-memory-layout/v2"] = Field(default=LAYOUT_MARKER_SCHEMA, alias="schema")
    conversion: Label
