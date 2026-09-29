"""The index's own tables: every relationship of one memory tree, in both directions (MIK-R23 rule 3).

The index file holds two table sets side by side:

* the ``ix_*`` tables below, which answer the lookups of rule 3 directly -- each relationship the
  files record once, from its owner's side, is one row here, indexed on both ends so the reverse
  direction no file records is one ``SELECT`` too;
* the logical tables of the knowledge store's newest schema generation, filled by
  :mod:`.projection`, so the existing read, view and traversal code runs over the index unchanged
  (rule 6).

Nothing here is authored: every row is a join over recorded fields of one tree's files, and deleting
the file loses nothing (the preservation boundary). ``INDEX_FORMAT`` names this layout; a cached
file of another format is treated as absent and rebuilt.
"""

from __future__ import annotations

from typing import Final

INDEX_FORMAT: Final = "ar-knowledge-index/v1"

INDEX_DDL: Final[tuple[str, ...]] = (
    """
CREATE TABLE ix_meta (
  name TEXT PRIMARY KEY NOT NULL,
  value TEXT NOT NULL
) STRICT
""",
    """
CREATE TABLE ix_problem (
  path TEXT NOT NULL,
  detail TEXT NOT NULL
) STRICT
""",
    """
CREATE TABLE ix_record (
  id TEXT PRIMARY KEY NOT NULL,
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  revision INTEGER,
  status TEXT NOT NULL,
  document TEXT NOT NULL
) STRICT
""",
    """
CREATE TABLE ix_entry (
  id TEXT PRIMARY KEY NOT NULL,
  kind TEXT NOT NULL,
  invariant TEXT NOT NULL,
  path TEXT NOT NULL,
  sidecar TEXT NOT NULL,
  document TEXT NOT NULL
) STRICT
""",
    """
CREATE TABLE ix_member (
  family TEXT NOT NULL,
  invariant TEXT NOT NULL,
  PRIMARY KEY (family, invariant)
) STRICT
""",
    """
CREATE TABLE ix_route (
  family TEXT NOT NULL,
  route TEXT NOT NULL,
  PRIMARY KEY (family, route)
) STRICT
""",
    """
CREATE TABLE ix_link (
  source TEXT NOT NULL,
  source_kind TEXT NOT NULL,
  relation TEXT NOT NULL,
  target_kind TEXT NOT NULL,
  target TEXT NOT NULL,
  detail TEXT NOT NULL,
  origin_path TEXT NOT NULL
) STRICT
""",
    """
CREATE TABLE ix_history_row (
  id TEXT PRIMARY KEY NOT NULL,
  owner TEXT NOT NULL,
  owner_kind TEXT NOT NULL,
  closed INTEGER NOT NULL,
  path TEXT NOT NULL,
  subject TEXT NOT NULL,
  disposition TEXT NOT NULL,
  document TEXT NOT NULL
) STRICT
""",
    "CREATE INDEX ix_entry_path ON ix_entry (path)",
    "CREATE INDEX ix_entry_invariant ON ix_entry (invariant)",
    "CREATE INDEX ix_member_invariant ON ix_member (invariant)",
    "CREATE INDEX ix_route_route ON ix_route (route)",
    "CREATE INDEX ix_link_target ON ix_link (target_kind, target)",
    "CREATE INDEX ix_link_source ON ix_link (source)",
    "CREATE INDEX ix_history_subject ON ix_history_row (subject)",
    "CREATE INDEX ix_history_owner ON ix_history_row (owner)",
)
