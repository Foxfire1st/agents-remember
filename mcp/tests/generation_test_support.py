"""Support for cases that must observe a specific schema *generation*.

Generation 2 makes "which schema is this" a property of the dataset rather than of the build, and
the created store is generation 2 after this leaf. A case that must assert a **generation-1** fact
therefore cannot use the ordinary creation path: it has to bring a version-1 dataset into being and
open it, which is also exactly the in-flight version-1 candidate the substrate has to keep
operable.

:func:`create_generation_1_store` does that through generation 1's own recorded DDL and its own
recorded ``user_version`` -- never by downgrading a generation-2 database, which would leave
generation 2's tables behind and test something else -- and then opens it through the production
open path so the case exercises real validation rather than a private shortcut.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.memory.knowledge.connection import (
    create_or_validate_schema,
    open_database,
    open_read_only_database,
)
from agents_remember.memory.knowledge.schema_generations import (
    GENERATION_1,
    create_schema_statements,
    generation_for_version,
)
from agents_remember.memory.knowledge.store import (
    OpenedKnowledgeStore,
    open_existing_knowledge_store,
)


def create_generation_1_store(database_path: Path, repository_id: str) -> OpenedKnowledgeStore:
    """Create a version-1 knowledge database and return it opened as the production path opens it.

    The DDL comes from generation 1's own record, so a case built here is a genuine version-1
    dataset rather than a version-2 dataset with an old number: the file carries generation 1's ten
    tables, generation 1's triggers and ``PRAGMA user_version = 1``.
    """

    path = Path(database_path)
    connection = open_database(path)
    try:
        for statement in create_schema_statements(GENERATION_1):
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {GENERATION_1.user_version}")
        connection.execute(
            "INSERT INTO repository (repository_id, authority_home) VALUES (?, ?)",
            (repository_id, f"memory:{repository_id}"),
        )
    finally:
        connection.close()
    return open_existing_knowledge_store(path, repository_id)


def create_generation_2_store(database_path: Path, repository_id: str) -> OpenedKnowledgeStore:
    """Create a version-2 knowledge database through the production creation path, and open it.

    Generation 2 *is* the created generation after this leaf, so this builder is the ordinary path
    with a name: a case that has to distinguish "the store I created" from "the store a version-1
    artifact installs" names the generation it is creating rather than relying on the build's
    current default, and it creates through :func:`create_or_validate_schema` so the dataset is the
    one production would create.

    The returned store is opened by the production open path, so the case still exercises real
    validation rather than a private shortcut.
    """

    path = Path(database_path)
    connection = open_database(path)
    try:
        create_or_validate_schema(connection)
        connection.execute(
            "INSERT INTO repository (repository_id, authority_home) VALUES (?, ?)",
            (repository_id, f"memory:{repository_id}"),
        )
    finally:
        connection.close()
    return open_existing_knowledge_store(path, repository_id)


def declared_schema_name(database_path: Path) -> str:
    """Return the schema name the generation one dataset declares carries."""

    return declared_pair(database_path)[0]


def declared_pair(database_path: Path) -> tuple[str, int]:
    """Return the ``(schema name, user_version)`` pair one open database file declares.

    The schema name is not stored in a SQLite file, so what this returns for it is the name the
    registry associates with the file's recorded version -- which is what the open path resolves.
    """

    connection = open_read_only_database(Path(database_path))
    try:
        version = int(next(iter(connection.execute("PRAGMA user_version")))[0])
    finally:
        connection.close()
    generation = generation_for_version(version)
    assert generation is not None, f"no registered generation declares user_version {version}"
    return generation.schema_name, version
