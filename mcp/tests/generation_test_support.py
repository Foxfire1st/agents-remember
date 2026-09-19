"""Support for cases that must observe a specific schema *generation*.

Generation 2 made "which schema is this" a property of the dataset rather than of the build, and
each later generation keeps that property: the created store is the newest registered generation,
which is generation 3 once the authored-judgment leaf lands. A case that must assert a **generation
1 or 2** fact therefore cannot use the ordinary creation path: it has to bring a dataset of that
generation into being and open it, which is also exactly the in-flight earlier-generation candidate
the substrate has to keep operable (``KS-R10@v1`` §5.1).

:func:`create_recorded_generation_store` does that through the observed generation's own recorded
DDL and its own recorded ``user_version`` -- never by downgrading a newer database, which would
leave the newer generation's tables behind and test something else -- and then opens it through the
production open path so the case exercises real validation rather than a private shortcut.
:func:`create_current_generation_store` is the other half: the dataset the *build* would create
today, named rather than assumed, so a case that means "the created generation" says so.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.memory.knowledge.connection import (
    create_or_validate_schema,
    open_database,
    open_read_only_database,
)
from agents_remember.memory.knowledge.schema_generations import (
    CURRENT_GENERATION,
    GENERATION_1,
    GENERATION_2,
    SchemaGeneration,
    create_schema_statements,
    generation_for_version,
)
from agents_remember.memory.knowledge.store import (
    OpenedKnowledgeStore,
    open_existing_knowledge_store,
)


def create_recorded_generation_store(
    database_path: Path, repository_id: str, generation: SchemaGeneration
) -> OpenedKnowledgeStore:
    """Create a knowledge database at one recorded generation, and open it as production would.

    The DDL comes from that generation's own record, so a case built here is a genuine dataset of
    that generation rather than a newer dataset with an old number: the file carries that
    generation's tables, that generation's triggers and that generation's ``PRAGMA user_version``.
    """

    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = open_database(path)
    try:
        for statement in create_schema_statements(generation):
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {generation.user_version}")
        connection.execute(
            "INSERT INTO repository (repository_id, authority_home) VALUES (?, ?)",
            (repository_id, f"memory:{repository_id}"),
        )
    finally:
        connection.close()
    return open_existing_knowledge_store(path, repository_id)


def create_generation_1_store(database_path: Path, repository_id: str) -> OpenedKnowledgeStore:
    """Create a genuine version-1 knowledge database and return it opened by the production path."""

    return create_recorded_generation_store(database_path, repository_id, GENERATION_1)


def create_generation_2_store(database_path: Path, repository_id: str) -> OpenedKnowledgeStore:
    """Create a genuine version-2 knowledge database and return it opened by the production path.

    Generation 2 stopped being *the created generation* when generation 3 landed, so this builder
    cannot be the ordinary creation path with a name any more: a case that has to observe a
    generation-2 fact -- the appended route and envelope tables, a generation-2 artifact's table
    set, a v1/v2 merge refusal -- needs a dataset that really declares version 2, and the recorded
    DDL is what produces one.
    """

    return create_recorded_generation_store(database_path, repository_id, GENERATION_2)


def create_current_generation_store(
    database_path: Path, repository_id: str
) -> OpenedKnowledgeStore:
    """Create the dataset *this build* would create, through the production creation path.

    A case that means "the created generation" names it here rather than assuming which one it is,
    so the builder keeps its meaning when the next generation lands.
    """

    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
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


def current_generation_name() -> str:
    """Return the schema name the build's own created generation declares."""

    return CURRENT_GENERATION.schema_name


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
