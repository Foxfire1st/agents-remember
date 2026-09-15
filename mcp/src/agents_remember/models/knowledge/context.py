"""The schema identity and the admitted candidate-write destination.

The schema name and its integer user version are declared here so every reader names the
same shape. The admitted destination is the runtime handle a storage operation receives: it
is constructed only after the existing application authority checks, so a deserialized
request cannot mint one by asserting that it is authorized.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.base import LABEL_MAX_LENGTH, KnowledgeModel
from agents_remember.models.knowledge.repository import RepositoryIdentity

# The application-owned schema version string, separate from SQLite's own ``user_version``
# integer so a reader can name the shape it expects instead of comparing bare numbers.
KNOWLEDGE_SCHEMA_NAME = "ar-knowledge-sqlite/v1"


class KnowledgeSchemaIdentity(KnowledgeModel):
    """The schema a store was opened as, plus its declared structural fingerprint."""

    schema_name: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    user_version: int = Field(ge=1)
    fingerprint: str = Field(min_length=1, max_length=128)


class AdmittedKnowledgeDestination(KnowledgeModel):
    """One destination the runtime has already authorized for candidate writes."""

    database_path: Path
    repository: RepositoryIdentity
    authorship: Authorship
