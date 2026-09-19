"""Generation 6's appended tables: family composition, its declared policy, and the family
revision's owning route and authored explanatory context.

This module owns **only what generation 6 appends**. Generation 1's ten tables, generation 2's six,
generation 3's four, generation 4's one and generation 5's one stay declared, verbatim, in
:mod:`…schema`, :mod:`…schema_v2`, :mod:`…schema_v3`, :mod:`…schema_v4` and :mod:`…schema_v5`;
nothing here redeclares, reorders, renames, retypes or drops one of them, and no ``ALTER TABLE``
against an earlier generation's table appears anywhere in this package. ``KS-R10@v1`` §1.3 makes
appending the only sanctioned way to add a table, and this generation's own case asserts
``GENERATION_6.columns[table] == GENERATION_5.columns[table]`` for every one of generation 5's
twenty-two names.

**Why this module is numbered 6 rather than 5.** These tables were authored as the generation above
this leaf's cut, which was generation 4. A sibling leaf landed its own generation 5 -- the authored
citation binding -- on the accumulated line first, so this module was renumbered to 6 and re-based
onto generation 5 at the sync. The six tables below, their order and their declarations are
unchanged; what moved is the number and the generation they are appended after. That renumber was a
rename plus a base-generation update rather than a re-derivation, because the inherited declarations
are compared **by name** (``GENERATION_6`` against ``GENERATION_5``) and never against a literal.

Why the tables are shaped this way:

* ``family_composition`` is one authored edge between **two exact family revisions** in one
  repository namespace. Both endpoints are typed columns with repository-scoped composite foreign
  keys to ``family_revision(repository_id, revision_id)``, so a wrong endpoint kind -- an invariant
  revision, an anchor, a claim, a facet record -- is unrepresentable rather than merely rejected
  (``KS-R17@v1`` §1.1-§1.2, ``Doc13:102``). There is deliberately **no** polymorphic
  ``(subject_kind, subject_id, predicate, target_kind, target_id)`` shape: with no unconstrained
  target column there is nothing for an unchecked identity to land in, which is what makes a cycle
  rule over this table a rule over a graph somebody can type.
* The edge's declared traversal policy is **not** a column pair on this table. A policy is its own
  authored record with its own immutable version set (``family_composition_policy`` and
  ``family_composition_policy_version``), and the edge names ``(policy_id, policy_version_id)``
  together or leaves both ``NULL``. Storing them as two nullable text columns on the edge would
  make identity-without-version and version-without-identity representable states, which
  ``KS-R17@v1`` §3.2 must refuse; the composite foreign key below refuses them structurally
  instead, and a policy's *version* is addressable because it is a row.
* ``CHECK (from_family_revision_id <> to_family_revision_id)`` catches the one-node cycle; the
  longer cycle is caught by the shared lineage rule
  (:mod:`agents_remember.memory.knowledge.lineage`), never by a second check grown beside it.
* The declared unique tuple names the exact endpoints and the policy identity **and version**:
  one authored relationship between one pair of exact revisions under one declared policy version is
  stored once. Two *different* policy versions or identities over one pair are two rows -- two
  separately authored meanings, each declaring the version it was authored under -- because a pair
  of revisions may carry more than one declared relationship and the honest representation of that
  is two rows, not a silently preferred one. Both rows are reported by the read, in declared order.
  That tuple is declared as **two partial unique indexes** rather than one table ``UNIQUE``
  constraint, and the reason is exact: SQLite treats every ``NULL`` as distinct in a unique key, so
  a table constraint over a nullable column would not stop a second *bare* edge -- an edge with no
  declared policy, which is the default state -- from being stored beside the first. The two indexes
  split on whether a policy is declared, so "stored once" holds for a bare edge and for a declared
  one, and the write path's own duplicate lookup refuses the state with a typed refusal before
  either index is reached.
* ``family_revision_route`` records the canonical owning route of the **revision aggregate**
  (``Doc13:87``'s altitude), which is a different fact from generation 2's ``family_route`` (the
  identity's own governing route). It is a new table of this generation rather than a column added
  to any of generation 2's joins, because an append is the only sanctioned way to add a table
  (``KS-R17@v1`` §9.1). The governed row's key is the join table's primary key, so "at most one
  canonical owning route per family revision" is a constraint rather than a convention -- and a
  family revision with no row here is the explicit **ungoverned** state, never a default to the
  repository root or to its identity's route.
* ``family_revision_context`` and ``family_revision_context_revision`` are the authored explanatory
  context: prose explaining the family's place and purpose, with its own authorship, its own
  identity and an append-only revision chain. ``predecessor_revision_id`` is ``NOT NULL`` and a
  chain's first revision names **itself**, which is the declared representation of "no predecessor"
  -- the alternative, a nullable column, would make "first" a value the reader has to distinguish
  from "absent". A *successor* cannot exploit the idiom: its predecessor must already be stored,
  which the write path checks against the stored rows before it writes anything, so a revision
  naming itself as its own successor has no stored predecessor to cite. The subject is bound exactly -- both the family
  identity and the family revision -- through the three-column key ``family_revision`` already
  declares a ``UNIQUE`` for, so a context cannot be attached to a revision of a statement identity
  it does not belong to, and the context is separable from ``joint_guarantee`` by construction
  (``KS-R17@v1`` §6.1-§6.2): there is no column here that could hold a guarantee, and no statement
  column is reachable from this row.
* The context record carries **no** content address, logical fingerprint and no digest of its own:
  the table below declares no such column, and ``family_revision_context_revision``'s identity is
  the pair the substrate already uses for an authored row -- its key. The prose is not a second
  identity authority and it deliberately does not reproduce the sealed family-revision payload,
  whose ``payload_digest`` and sealed field set are unchanged by this generation
  (``KS-R17@v1`` §9.2).

No table here duplicates task status, seat ownership, lifecycle gates or approval authority, and no
row of theirs becomes an independently editable competing contract (``Doc13:106``).
"""

from __future__ import annotations

from collections.abc import Mapping

# Every table this generation appends, in the order the encoder serializes them. Appended after
# generation 5's twenty-two, so generation 6's manifest begins with generation 5's, unchanged.
APPENDED_TABLES: tuple[str, ...] = (
    "family_composition",
    "family_composition_policy",
    "family_composition_policy_version",
    "family_revision_route",
    "family_revision_context",
    "family_revision_context_revision",
)

APPENDED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "family_composition": (
        "repository_id",
        "composition_id",
        "from_family_revision_id",
        "to_family_revision_id",
        "policy_id",
        "policy_version_id",
        "provenance",
    ),
    "family_composition_policy": (
        "repository_id",
        "policy_id",
        "provenance",
    ),
    "family_composition_policy_version": (
        "repository_id",
        "policy_id",
        "policy_version_id",
        "declared_version",
        "direction",
        "depth_bound",
        "widened_scope",
        "provenance",
    ),
    "family_revision_route": (
        "repository_id",
        "family_revision_id",
        "route_id",
        "provenance",
    ),
    "family_revision_context": (
        "repository_id",
        "context_id",
        "family_id",
        "family_revision_id",
        "current_revision_id",
        "provenance",
    ),
    "family_revision_context_revision": (
        "repository_id",
        "context_id",
        "revision_id",
        "predecessor_revision_id",
        "body",
        "provenance",
    ),
}

# The declared primary key of each appended table, as its DDL declares it. The encoder orders a
# table's rows by this tuple, so it is part of the generation's pinned structure rather than a
# derivation from the DDL text.
APPENDED_PRIMARY_KEYS: Mapping[str, tuple[str, ...]] = {
    "family_composition": ("repository_id", "composition_id"),
    "family_composition_policy": ("repository_id", "policy_id"),
    "family_composition_policy_version": (
        "repository_id",
        "policy_id",
        "policy_version_id",
    ),
    "family_revision_route": ("repository_id", "family_revision_id"),
    "family_revision_context": ("repository_id", "context_id"),
    "family_revision_context_revision": ("repository_id", "revision_id"),
}

# The columns whose stored text is a typed JSON value, decoded at the portable boundary. Every
# ``provenance`` column in this generation is one, on the shipped idiom. ``direction``,
# ``widened_scope`` and ``depth_bound`` are not: a direction and a named scope are closed
# vocabularies rather than structured documents, and ``body`` is authored prose -- the same reason
# generation 3's explanation ``body`` is not a JSON column.
APPENDED_JSON_COLUMNS: Mapping[str, frozenset[str]] = {
    "family_composition": frozenset({"provenance"}),
    "family_composition_policy": frozenset({"provenance"}),
    "family_composition_policy_version": frozenset({"provenance"}),
    "family_revision_route": frozenset({"provenance"}),
    "family_revision_context": frozenset({"provenance"}),
    "family_revision_context_revision": frozenset({"provenance"}),
}

APPENDED_TABLE_DDL: Mapping[str, str] = {
    "family_composition": """
CREATE TABLE family_composition (
  repository_id TEXT NOT NULL,
  composition_id TEXT NOT NULL,
  from_family_revision_id TEXT NOT NULL,
  to_family_revision_id TEXT NOT NULL,
  policy_id TEXT,
  policy_version_id TEXT,
  provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, composition_id),
  CHECK (from_family_revision_id <> to_family_revision_id),
  CHECK (
    (policy_id IS NULL AND policy_version_id IS NULL)
    OR (policy_id IS NOT NULL AND policy_version_id IS NOT NULL)
  ),
  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, from_family_revision_id)
    REFERENCES family_revision(repository_id, revision_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, to_family_revision_id)
    REFERENCES family_revision(repository_id, revision_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, policy_id, policy_version_id)
    REFERENCES family_composition_policy_version(
      repository_id, policy_id, policy_version_id
    )
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
    "family_composition_policy": """
CREATE TABLE family_composition_policy (
  repository_id TEXT NOT NULL,
  policy_id TEXT NOT NULL,
  provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, policy_id),
  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
    "family_composition_policy_version": """
CREATE TABLE family_composition_policy_version (
  repository_id TEXT NOT NULL,
  policy_id TEXT NOT NULL,
  policy_version_id TEXT NOT NULL,
  declared_version TEXT NOT NULL,
  direction TEXT NOT NULL,
  depth_bound INTEGER NOT NULL,
  widened_scope TEXT NOT NULL,
  provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, policy_id, policy_version_id),
  UNIQUE (repository_id, policy_id, declared_version),
  CHECK (declared_version <> ''),
  CHECK (direction IN ('forward', 'reverse', 'both')),
  CHECK (depth_bound >= 1),
  CHECK (widened_scope <> ''),
  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, policy_id)
    REFERENCES family_composition_policy(repository_id, policy_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
    "family_revision_route": """
CREATE TABLE family_revision_route (
  repository_id TEXT NOT NULL,
  family_revision_id TEXT NOT NULL,
  route_id TEXT NOT NULL,
  provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, family_revision_id),
  FOREIGN KEY (repository_id, family_revision_id)
    REFERENCES family_revision(repository_id, revision_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, route_id)
    REFERENCES route(repository_id, route_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
    "family_revision_context": """
CREATE TABLE family_revision_context (
  repository_id TEXT NOT NULL,
  context_id TEXT NOT NULL,
  family_id TEXT NOT NULL,
  family_revision_id TEXT NOT NULL,
  current_revision_id TEXT NOT NULL,
  provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, context_id),
  UNIQUE (repository_id, family_revision_id),
  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, family_id, family_revision_id)
    REFERENCES family_revision(repository_id, family_id, revision_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, current_revision_id)
    REFERENCES family_revision_context_revision(repository_id, revision_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
    "family_revision_context_revision": """
CREATE TABLE family_revision_context_revision (
  repository_id TEXT NOT NULL,
  context_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  predecessor_revision_id TEXT NOT NULL,
  body TEXT NOT NULL,
  provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, revision_id),
  UNIQUE (repository_id, context_id, revision_id),
  CHECK (body <> ''),
  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, context_id)
    REFERENCES family_revision_context(repository_id, context_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, predecessor_revision_id)
    REFERENCES family_revision_context_revision(repository_id, revision_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
}

# Index names are a local choice; each covers the reverse direction of a declared lookup, which is
# the same reason the earlier generations declare their own. One name is *not* a local choice: an
# index may not carry a table's name, because SQLite refuses a second object under one identifier --
# so the index over the composition edge's ``policy_version_id`` is named
# ``family_composition_policy_version_edge`` rather than after the table it points at.
APPENDED_INDEX_DDL: tuple[str, ...] = (
    "CREATE INDEX family_composition_from_endpoint "
    "ON family_composition (repository_id, from_family_revision_id)",
    "CREATE INDEX family_composition_to_endpoint "
    "ON family_composition (repository_id, to_family_revision_id)",
    "CREATE INDEX family_composition_policy_version_edge "
    "ON family_composition (repository_id, policy_version_id)",
    # The declared unique tuple, as two partial indexes: see the module docstring for why a table
    # ``UNIQUE`` constraint over a nullable ``policy_id`` would not have been the same rule.
    "CREATE UNIQUE INDEX family_composition_pair_with_policy "
    "ON family_composition ("
    "repository_id, from_family_revision_id, to_family_revision_id, policy_id, policy_version_id"
    ") WHERE policy_id IS NOT NULL",
    "CREATE UNIQUE INDEX family_composition_pair_without_policy "
    "ON family_composition (repository_id, from_family_revision_id, to_family_revision_id) "
    "WHERE policy_id IS NULL",
    "CREATE INDEX family_composition_policy_version_policy "
    "ON family_composition_policy_version (repository_id, policy_id)",
    "CREATE INDEX family_revision_route_route ON family_revision_route (repository_id, route_id)",
    "CREATE INDEX family_revision_context_revision_context "
    "ON family_revision_context_revision (repository_id, context_id)",
    "CREATE INDEX family_revision_context_revision_predecessor "
    "ON family_revision_context_revision (repository_id, predecessor_revision_id)",
)

# The same trigger idiom the earlier generations use: the operation's preconditions return a typed
# refusal, and these exist so a changeset, a repair script or a future code path that forgot the
# rule still cannot rewrite a sealed row or rebind a sealed association.
#
# Two absences are deliberate and are the point of the record kinds they belong to:
# * ``family_composition`` has no whole-row update trigger, because its declared traversal policy is
#   the one thing a later authoring act may set on it -- an edge stored before its policy was
#   declared becomes traversable by declaring the policy, and *nothing else* on the row may move.
#   The trigger names every other column for exactly that reason.
# * ``family_composition_policy_version`` has no delete trigger, because an edge may cite it and the
#   foreign key is what refuses a version that is still cited; its rows are sealed against rewrite.
APPENDED_TRIGGERS: Mapping[str, str] = {
    "family_composition_no_repoint": """
CREATE TRIGGER family_composition_no_repoint
BEFORE UPDATE OF repository_id, composition_id, from_family_revision_id, to_family_revision_id,
  provenance ON family_composition
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a composition edge cannot be repointed in place'); END
""",
    "family_composition_no_delete": """
CREATE TRIGGER family_composition_no_delete BEFORE DELETE ON family_composition
BEGIN SELECT RAISE(ABORT, 'immutable_revision: an authored composition edge cannot be deleted'); END
""",
    "family_composition_policy_no_rebind": """
CREATE TRIGGER family_composition_policy_no_rebind
BEFORE UPDATE OF repository_id, policy_id ON family_composition_policy
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a policy identity cannot be rebound'); END
""",
    "family_composition_policy_no_delete": """
CREATE TRIGGER family_composition_policy_no_delete BEFORE DELETE ON family_composition_policy
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a declared policy identity cannot be deleted'); END
""",
    "family_composition_policy_version_no_rewrite": """
CREATE TRIGGER family_composition_policy_version_no_rewrite
BEFORE UPDATE OF policy_id, policy_version_id, declared_version, direction, depth_bound,
  widened_scope, provenance ON family_composition_policy_version
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a declared policy version cannot be rewritten'); END
""",
    "family_composition_policy_version_no_delete": """
CREATE TRIGGER family_composition_policy_version_no_delete
BEFORE DELETE ON family_composition_policy_version
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a declared policy version cannot be deleted'); END
""",
    "family_revision_route_no_repoint": """
CREATE TRIGGER family_revision_route_no_repoint
BEFORE UPDATE OF route_id, provenance ON family_revision_route
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a canonical owning route cannot be repointed'); END
""",
    "family_revision_route_no_delete": """
CREATE TRIGGER family_revision_route_no_delete BEFORE DELETE ON family_revision_route
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a recorded owning route cannot be deleted'); END
""",
    "family_revision_context_no_rebind": """
CREATE TRIGGER family_revision_context_no_rebind
BEFORE UPDATE OF repository_id, context_id, family_id, family_revision_id, provenance
ON family_revision_context
BEGIN SELECT RAISE(ABORT, 'immutable_revision: an explanatory context cannot be rebound'); END
""",
    "family_revision_context_no_delete": """
CREATE TRIGGER family_revision_context_no_delete BEFORE DELETE ON family_revision_context
BEGIN SELECT RAISE(ABORT, 'immutable_revision: an explanatory context cannot be deleted'); END
""",
    "family_revision_context_revision_no_rewrite": """
CREATE TRIGGER family_revision_context_revision_no_rewrite
BEFORE UPDATE OF context_id, predecessor_revision_id, body, provenance
ON family_revision_context_revision
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a sealed context revision cannot be rewritten'); END
""",
    "family_revision_context_revision_no_delete": """
CREATE TRIGGER family_revision_context_revision_no_delete
BEFORE DELETE ON family_revision_context_revision
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a sealed context revision cannot be deleted'); END
""",
}

# Generation 6 needs no SQLite feature generation 2 does not already require: it adds tables,
# checked foreign-key groups, indexes and triggers, all of which ``strict_tables``,
# ``deferrable_foreign_keys``, ``trigger_raise_abort`` and ``json_functions`` already cover. The
# tuple is declared (empty) rather than omitted so the composition of generation 6 states the same
# fact generations 3, 4 and 5 do, and a reader does not have to infer "no new feature" from an
# absence.
APPENDED_FEATURES: tuple[str, ...] = ()
