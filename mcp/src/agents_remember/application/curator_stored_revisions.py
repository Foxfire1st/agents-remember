"""Which invariant revisions a curator candidate already stores, answered by the dataset itself.

This is what makes a repeat of a creation operation a **replay** rather than a second write. The
identity a repeat resolves to is the one its allocation recorded, so the candidate that already
holds that revision answers "was this creation admitted?" from its own rows -- never from the
allocation journal, which is written before the batch and cannot know whether that batch committed.

The same answer decides admission. An operation whose recorded revision the candidate already
stores writes nothing new when it is repeated, so the checks that guard what a new realization is
written with are not asked of it again: its retry replays, or conflicts on changed content, exactly
as it always did.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from agents_remember.memory.knowledge.connection import open_read_only_database


def stored_revisions(database: Path) -> dict[str, str]:
    """Every revision the candidate already holds, by revision id, with the statement it records."""

    connection = open_read_only_database(database)
    try:
        return {
            str(row[0]): str(row[1])
            for row in connection.execute("SELECT revision_id, statement FROM invariant_revision")
        }
    finally:
        connection.close()


def committed_revisions(database: Path, recorded: Iterable[str]) -> frozenset[str]:
    """The recorded revision ids the candidate already stores; none while it has no dataset yet."""

    wanted = set(recorded)
    if not wanted or not database.is_file():
        return frozenset()
    return frozenset(wanted.intersection(stored_revisions(database)))
