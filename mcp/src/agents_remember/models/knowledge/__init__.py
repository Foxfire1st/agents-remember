"""Shared immutable vocabulary for the knowledge substrate.

The models here are the served wire vocabulary of the knowledge store: entity, operation,
refusal and snapshot shapes that cross the storage boundary. They hold no SQL, no Git
resolution and no authorization decision. A literal state or result code that decides
something lives here and is imported by the decider, never defined by it and imported back
down.
"""

from agents_remember.models.knowledge.authorship import (
    ACCEPTED_STATE,
    PROPOSED_STATE,
    Authorship,
    KnowledgeState,
)
from agents_remember.models.knowledge.context import (
    KNOWLEDGE_SCHEMA_NAME,
    AdmittedKnowledgeDestination,
    KnowledgeSchemaIdentity,
)
from agents_remember.models.knowledge.digest import (
    REVISION_PAYLOAD_VERSION,
    canonical_revision_payload,
    revision_payload_digest,
    sealed_revision,
)
from agents_remember.models.knowledge.invariant import (
    InvariantDraft,
    InvariantIdentity,
    InvariantRevision,
    StoredInvariantRevision,
)
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import (
    CreateInvariantResult,
    CreateRevisionResult,
    InvariantRequest,
    KnowledgeOperation,
    KnowledgeRefusal,
    KnowledgeRefusalCode,
    RepositoryCreationResult,
    RevisionDraft,
    RevisionRequest,
)
from agents_remember.models.knowledge.source import (
    FileLocator,
    GitBlobIdentity,
    LineRangeLocator,
    SourceAnchor,
    SourceIdentity,
    SourceLocator,
    SymbolLocator,
)

__all__ = [
    "ACCEPTED_STATE",
    "KNOWLEDGE_SCHEMA_NAME",
    "PROPOSED_STATE",
    "REVISION_PAYLOAD_VERSION",
    "AdmittedKnowledgeDestination",
    "Authorship",
    "CreateInvariantResult",
    "CreateRevisionResult",
    "FileLocator",
    "GitBlobIdentity",
    "InvariantDraft",
    "InvariantIdentity",
    "InvariantRequest",
    "InvariantRevision",
    "KnowledgeOperation",
    "KnowledgeRefusal",
    "KnowledgeRefusalCode",
    "KnowledgeSchemaIdentity",
    "KnowledgeState",
    "LineRangeLocator",
    "RepositoryCreationResult",
    "RepositoryIdentity",
    "RevisionDraft",
    "RevisionRequest",
    "SourceAnchor",
    "SourceIdentity",
    "SourceLocator",
    "StoredInvariantRevision",
    "SymbolLocator",
    "canonical_revision_payload",
    "revision_payload_digest",
    "sealed_revision",
]
