"""Schema name and structural identity for knowledge SQLite shapes.

KNOWLEDGE_SCHEMA_NAME names the schema. KnowledgeSchemaIdentity records
the schema name, integer user version and structural fingerprint.
"""

from __future__ import annotations

from pydantic import Field

from agents_remember.models.knowledge.base import LABEL_MAX_LENGTH, KnowledgeModel

# The application-owned schema version string, separate from SQLite's own ``user_version``
# integer so a reader can name the shape it expects instead of comparing bare numbers.
KNOWLEDGE_SCHEMA_NAME = "ar-knowledge-sqlite/v1"


class KnowledgeSchemaIdentity(KnowledgeModel):
    """The schema a store was opened as, plus its declared structural fingerprint."""

    schema_name: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    user_version: int = Field(ge=1)
    fingerprint: str = Field(min_length=1, max_length=128)
