"""Strict response models for the five mounted ``knowledge_*`` operation families.

``Doc13:181-187`` names the five operations and ``KS-R20@v1`` §6 mounts them. Each model here is a
strict ``ToolResponse``, so the field set is the wire contract rather than a convention.

**One shape, not five.** Requirement 6.8 says a mounted tool's response payload is the same view
payload requirements 2 and 3 define, "not a second shape". A tool that re-rendered a view in its own
format would create a second renderer and therefore a second place for the classification rule to be
violated, so the payload travels as the typed view payload's own JSON and this module adds only the
envelope around it.

**A refusal is a state, not a partial success.** ``state`` is ``view``/``result`` or ``refused``, and
the refusal fields name the offending input. A handler never translates a refusal into an empty
result or a default value, so a caller can always tell "nothing was selected" from "the selection was
refused".
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agents_remember.models.base import ToolResponse

__all__ = [
    "KnowledgeChangeResponse",
    "KnowledgeDiffResponse",
    "KnowledgeIntegrityCheckResponse",
    "KnowledgeProjectResponse",
    "KnowledgeReadResponse",
]


class KnowledgeReadResponse(ToolResponse):
    """``knowledge_read``: one named view's payload, or one typed refusal.

    ``view`` and ``snapshot`` are echoed beside the payload so a caller can tell which of the five
    views it received and which snapshot it was read at without parsing the payload's own body. The
    payload itself is the view payload verbatim -- the same object requirements 2 and 3 define.
    """

    operation: Literal["knowledge_read"] = "knowledge_read"
    state: Literal["view", "refused"]
    view: str
    repositoryId: str
    snapshot: str | None = None
    completeWithinDeclaredScope: bool | None = None
    continuation: str | None = None
    payload: dict[str, Any] | None = None
    refusalCode: str | None = None
    refusalDetail: str | None = None


class KnowledgeChangeResponse(ToolResponse):
    """``knowledge_change``: recorded, or refused. The tool records; it does not author."""

    operation: Literal["knowledge_change"] = "knowledge_change"
    state: Literal["recorded", "no_change", "refused"]
    recordKind: str
    repositoryId: str
    recordId: str | None = None
    revisionId: str | None = None
    refusalCode: str | None = None
    refusalDetail: str | None = None


class KnowledgeDiffResponse(ToolResponse):
    """``knowledge_diff``: the shipped comparison result, with no inferred semantic label.

    ``semanticEffectLabels`` carries only labels an identified agent or assessment supplied. A diff
    with no supplied label returns an empty list, and the handler never infers one from the change:
    requirement 6.2 quotes ``Doc13:184``'s "not inferred from the diff" as the whole contract.
    """

    operation: Literal["knowledge_diff"] = "knowledge_diff"
    state: Literal["compared", "refused"]
    repositoryId: str
    semanticEffectLabels: list[dict[str, Any]] = Field(default_factory=list)
    payload: dict[str, Any] | None = None
    refusalCode: str | None = None
    refusalDetail: str | None = None


class KnowledgeIntegrityCheckResponse(ToolResponse):
    """``knowledge_integrity_check``: conditions, their limits -- and no verdict.

    ``compatible`` is ``None`` by design and not by omission. ``Doc13:186`` says the operation
    "produces no compatibility verdict or causal explanation", so a caller reads the conditions and
    the limitations and decides; computing ``true`` from the absence of a matched condition would be
    inferring a semantic conclusion from a detection's silence.

    The five run fields bind those conditions to the inputs they were measured over: the digest of
    the scope's exact inputs the caller named, the selected run's own identity, the digest over its
    exact inputs, the input identity itself, and the runs this scope holds. A response carrying
    conditions alone left a caller unable to tell a report about its own candidate from a report
    about another run recorded in the same scope.
    """

    operation: Literal["knowledge_integrity_check"] = "knowledge_integrity_check"
    state: Literal["reported", "refused"]
    repositoryId: str
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    traversalScope: str | None = None
    selectedRunId: str | None = None
    inputDigest: str | None = None
    inputIdentities: list[dict[str, Any]] = Field(default_factory=list)
    matchingRunIds: list[dict[str, Any]] = Field(default_factory=list)
    exactInputSelector: dict[str, Any] | None = None
    limitations: list[str] = Field(default_factory=list)
    compatible: None = None
    assessment: dict[str, Any] | None = None
    unresolved: list[str] = Field(default_factory=list)
    refusalCode: str | None = None
    refusalDetail: str | None = None


class KnowledgeProjectResponse(ToolResponse):
    """``knowledge_project``: one managed projection's per-path outcomes, or one refusal."""

    operation: Literal["knowledge_project"] = "knowledge_project"
    state: Literal["projected", "refused"]
    destinationRoot: str
    rendererVersion: str
    manifestGeneration: int | None = None
    published: list[str] = Field(default_factory=list)
    retained: list[dict[str, Any]] = Field(default_factory=list)
    discrepancies: list[dict[str, Any]] = Field(default_factory=list)
    refusalCode: str | None = None
    refusalDetail: str | None = None
