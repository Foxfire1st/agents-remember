"""Support for cases that must name the schema a dataset file declares.

There is one schema (MIK-R26, ruling Q1): the schema of a memory tree's derived index. A case that
states a context matching a file reads the name from here rather than spelling it, so it keeps its
meaning if the declared schema is ever deliberately changed.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.schema_generations import CURRENT_GENERATION


def current_generation_name() -> str:
    """Return the schema name this build declares."""

    return CURRENT_GENERATION.schema_name


def declared_schema_name(database_path: Path) -> str:
    """Return the schema name the dataset file declares."""

    return declared_pair(database_path)[0]


def declared_pair(database_path: Path) -> tuple[str, int]:
    """Return the ``(schema name, user_version)`` pair one open database file declares.

    The schema name is not stored in a SQLite file, so what this returns for it is the name of the
    one declared schema, after checking that the file's recorded version is that schema's.
    """

    connection = open_read_only_database(Path(database_path))
    try:
        version = int(next(iter(connection.execute("PRAGMA user_version")))[0])
    finally:
        connection.close()
    assert version == CURRENT_GENERATION.user_version, f"the file declares user_version {version}"
    return CURRENT_GENERATION.schema_name, version
