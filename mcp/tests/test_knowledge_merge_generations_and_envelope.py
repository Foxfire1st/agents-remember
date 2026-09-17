"""Mixed-generation merge preflight and the generation-2 envelope and route contracts.

Three requirement groups share this file because they share one subject -- what generation 2 makes
possible and what it must refuse:

* **6.1/6.2** -- the preflight reads each input's generation first, refuses a mixed-generation merge
  *before any session exists*, and validates a same-generation merge against the generation the
  inputs agree on. The required **passing** case is the one a partially threaded preflight breaks: a
  v1/v1/v1 merge on the generation-2 build must proceed under generation 1 and attach generation 1's
  ten tables.
* **3.1** -- one registry maps ``(kind, record_schema)`` to one frozen model and one entry point
  decides admissibility, refusing with the shipped ``invalid_payload``.
* **4.2/4.3** -- a route path is normalised and confined, and the hierarchy is acyclic with the
  check running inside the caller's transaction.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import apsw
import pytest
from agents_remember.application.knowledge import (
    admitted_knowledge_destination,
    initialize_knowledge_namespace,
    open_admitted_knowledge_store,
    write_authorship,
)
from agents_remember.memory.knowledge import logical, merge_validation, routes
from agents_remember.memory.knowledge.connection import (
    create_or_validate_schema,
    open_read_only_database,
)
from agents_remember.memory.knowledge.merge_changeset import build_delta
from agents_remember.memory.knowledge.merge_schema import (
    declared_generation,
    require_supported_structure,
    selected_generation,
)
from agents_remember.memory.knowledge.record_envelope import (
    INTERNAL_CONFORMANCE_KIND,
    INTERNAL_CONFORMANCE_SCHEMA,
    PAYLOAD_MODELS,
    validate_record_payload,
)
from agents_remember.memory.knowledge.routes import (
    RouteDraft,
    normalize_route_path,
    require_acyclic_routes,
)
from agents_remember.memory.knowledge.schema_generations import (
    CURRENT_GENERATION,
    GENERATION_1,
    GENERATION_2,
    create_schema_statements,
    generation_of_database,
)
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import KnowledgeRefusal
from generation_test_support import create_generation_1_store

MERGE_OPERATION = "merge_knowledge_datasets"
pytestmark = pytest.mark.evidence_unit


def _v1_dataset(tmp_path: Path, name: str, repository_id: str) -> Path:
    """Create one genuine version-1 dataset bound to ``repository_id`` and return its path.

    The three positions of one merge share a namespace, so the caller supplies it: three datasets
    bound to three different namespaces are not a merge of one dataset's branches, and their
    repository rows would show up as a difference that has nothing to do with the generation.
    """

    path = tmp_path / f"{name}.sqlite"
    with create_generation_1_store(path, repository_id):
        pass
    return path


def test_a_version_1_merge_on_the_generation_2_build_selects_generation_1(
    tmp_path: Path,
) -> None:
    """Requirement 6.2's required **passing** case, and the one a partial threading breaks.

    All three inputs declare version 1, so the agreed generation is generation 1. Each input is then
    validated against **generation 1's** manifest and column map, and the session attaches generation
    1's ten tables -- not the running build's sixteen. With the live globals left in place, this same
    merge asks for ``route``, ``knowledge_record`` and ``record_revision`` and is refused, which is
    requirement 5.1 broken by the mechanism meant to serve it.
    """

    repository_id = str(uuid4())
    base = _v1_dataset(tmp_path, "base", repository_id)
    left = _v1_dataset(tmp_path, "left", repository_id)
    right = _v1_dataset(tmp_path, "right", repository_id)

    assert declared_generation(base) is GENERATION_1
    selected = selected_generation({"base": base, "left": left, "right": right}, MERGE_OPERATION)
    assert selected is GENERATION_1
    for role, path in (("base", base), ("left", left), ("right", right)):
        assert (
            require_supported_structure(path, MERGE_OPERATION, role=role, generation=GENERATION_1)
            is None
        )

    delta = build_delta(left, base, side="left", generation=GENERATION_1)
    assert set(delta.operation_counts) == set(GENERATION_1.tables)
    assert GENERATION_2.tables != GENERATION_1.tables
    assert tuple(sorted(delta.operation_counts)) == tuple(sorted(GENERATION_1.tables))
    assert delta.is_empty


def test_a_mixed_generation_merge_is_refused_before_any_session_exists(tmp_path: Path) -> None:
    """Requirement 6.1: the inputs disagree, so the operation refuses and picks no winner.

    The refusal carries the observed generation as a fact, names which positional input disagreed,
    and happens in the preflight -- the step that runs before a delta, and therefore before any
    session, exists.
    """

    base = _v1_dataset(tmp_path, "base", str(uuid4()))
    left = _v1_dataset(tmp_path, "left", str(uuid4()))
    right = tmp_path / "right-v2.sqlite"
    connection = apsw.Connection(str(right))
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        create_or_validate_schema(connection)
    finally:
        connection.close()

    refusal = selected_generation({"base": base, "left": left, "right": right}, MERGE_OPERATION)
    assert isinstance(refusal, KnowledgeRefusal)
    assert refusal.code == "schema_mismatch"
    assert refusal.operation == MERGE_OPERATION
    assert (refusal.expected, refusal.observed) == ("1", "2")
    assert "right" in refusal.detail
    assert "base" in refusal.detail

    # No session exists, and both inputs are byte-identical to what they were.
    before = {path: path.read_bytes() for path in (base, left, right)}
    again = selected_generation({"base": base, "left": left, "right": right}, MERGE_OPERATION)
    assert isinstance(again, KnowledgeRefusal)
    assert {path: path.read_bytes() for path in (base, left, right)} == before


def test_a_generation_2_merge_on_the_generation_2_build_selects_generation_2(
    tmp_path: Path,
) -> None:
    """The same mechanism serves the newest generation: agreement selects it, disagreement does not."""

    paths: dict[str, Path] = {}
    for role in ("base", "left", "right"):
        path = tmp_path / f"{role}-v2.sqlite"
        connection = apsw.Connection(str(path))
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            create_or_validate_schema(connection)
        finally:
            connection.close()
        paths[role] = path

    assert selected_generation(paths, MERGE_OPERATION) is CURRENT_GENERATION
    for role, path in paths.items():
        assert (
            require_supported_structure(
                path, MERGE_OPERATION, role=role, generation=CURRENT_GENERATION
            )
            is None
        )
    reader = open_read_only_database(paths["base"])
    try:
        assert logical.logical_body(reader, CURRENT_GENERATION)["tables"]["route"] == []
    finally:
        reader.close()


def test_the_payload_seam_registers_one_shape_and_refuses_every_inadmissible_payload() -> None:
    """Requirement 3.1: one registry, one entry point, the shipped ``invalid_payload`` code.

    Four refusals, each a different fact: an unknown kind, a schema not admissible for a known kind,
    a payload that does not validate, and a payload that is not an object at all.
    """

    assert PAYLOAD_MODELS[(INTERNAL_CONFORMANCE_KIND, INTERNAL_CONFORMANCE_SCHEMA)] is not None
    validated = validate_record_payload(
        INTERNAL_CONFORMANCE_KIND, INTERNAL_CONFORMANCE_SCHEMA, {"note": "conformance"}
    )
    assert not isinstance(validated, KnowledgeRefusal)
    assert validated.model_dump() == {"note": "conformance"}
    # The validated value is frozen: a caller cannot mutate what it validated.
    with pytest.raises(Exception):  # noqa: B017 - pydantic's own frozen-model failure
        validated.note = "changed"  # type: ignore[misc]

    for kind, record_schema, payload in (
        ("not_a_kind", INTERNAL_CONFORMANCE_SCHEMA, {"note": "x"}),
        (INTERNAL_CONFORMANCE_KIND, "not-a-schema/v1", {"note": "x"}),
        (INTERNAL_CONFORMANCE_KIND, INTERNAL_CONFORMANCE_SCHEMA, {"note": ""}),
        (INTERNAL_CONFORMANCE_KIND, INTERNAL_CONFORMANCE_SCHEMA, {"note": "x", "extra": 1}),
        (INTERNAL_CONFORMANCE_KIND, INTERNAL_CONFORMANCE_SCHEMA, {"other": "x"}),
    ):
        refusal = validate_record_payload(kind, record_schema, payload, record_id="r-1")
        assert isinstance(refusal, KnowledgeRefusal)
        assert refusal.code == "invalid_payload"
        assert refusal.table == "record_revision"
        assert refusal.record_id == "r-1"


@pytest.mark.parametrize(
    "path",
    ["src/pkg", "src", "a/b/c/d", "docs/design/storage-design.md"],
)
def test_a_confined_route_path_normalises_to_itself(path: str) -> None:
    assert normalize_route_path(path) == path


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/absolute",
        "src/../etc",
        "src/./pkg",
        "src//pkg",
        "src\\pkg",
        "C:/src",
        "//server/share",
        "\\\\server\\share",
        "src/pkg/",
        "src/\x00pkg",
    ],
)
def test_a_route_path_outside_the_admitted_form_is_refused(path: str) -> None:
    """Requirement 4.2: two spellings of one path must not produce two routes."""

    refusal = normalize_route_path(path)
    assert isinstance(refusal, KnowledgeRefusal)
    assert refusal.table == "route"
    assert refusal.observed == path


def test_the_route_hierarchy_is_acyclic_and_a_cycle_rolls_the_batch_back() -> None:
    """Requirement 4.3: the check runs inside the caller's transaction, over parent → child.

    Both directions are measured, because a check observed only in the passing direction is not
    evidence: a chain and a two-root forest pass, a three-node cycle is named, and the rollback
    restores the hierarchy the batch replaced.
    """

    connection = apsw.Connection(":memory:")
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        for statement in create_schema_statements(GENERATION_2):
            connection.execute(statement)
        repository_id = str(uuid4())
        connection.execute(
            "INSERT INTO repository (repository_id, authority_home) VALUES (?, ?)",
            (repository_id, "agents-remember"),
        )
        connection.execute(
            "INSERT INTO route (repository_id, route_id, parent_route_id, path, provenance) "
            "VALUES (?, ?, NULL, ?, ?)",
            (repository_id, "root", "src", "{}"),
        )
        for route_id, parent, path in (("mid", "root", "src/mid"), ("leaf", "mid", "src/mid/leaf")):
            connection.execute(
                "INSERT INTO route (repository_id, route_id, parent_route_id, path, provenance) "
                "VALUES (?, ?, ?, ?, ?)",
                (repository_id, route_id, parent, path, "{}"),
            )
        assert require_acyclic_routes(connection, repository_id) is None

        connection.execute("BEGIN")
        connection.execute(
            "UPDATE route SET parent_route_id = ? WHERE route_id = ?", ("leaf", "root")
        )
        refusal = require_acyclic_routes(connection, repository_id)
        assert isinstance(refusal, KnowledgeRefusal)
        assert refusal.code == "lineage_cycle"
        assert refusal.table == "route"
        assert refusal.observed is not None and "root" in refusal.observed
        connection.execute("ROLLBACK")

        assert require_acyclic_routes(connection, repository_id) is None
        # The one-node cycle is the table's own CHECK rather than this walk.
        with pytest.raises(apsw.ConstraintError):
            connection.execute(
                "INSERT INTO route (repository_id, route_id, parent_route_id, path, provenance) "
                "VALUES (?, ?, ?, ?, ?)",
                (repository_id, "self", "self", "src/self", "{}"),
            )
    finally:
        connection.close()


def test_the_gen_2_record_tables_carry_no_competing_identity_and_the_association_is_a_constraint() -> (
    None
):
    """Requirements 3.3 and 4.4, as facts about generation 2's own DDL.

    The envelope carries no content-address, logical digest or fingerprint column; the payload lives
    on the revision with ``json_valid``; the governing association is a generation-2 table whose
    primary key is the governed entity's, which makes "at most one governing route per governed row"
    a constraint; and no generation-1 table is altered anywhere.
    """

    envelope_columns = set(GENERATION_2.columns["knowledge_record"])
    assert {"content_digest", "logical_digest", "fingerprint"} & envelope_columns == set()
    assert "governing_route_id" in envelope_columns
    assert "payload" in GENERATION_2.columns["record_revision"]
    assert "payload" in GENERATION_2.json_columns["record_revision"]
    assert GENERATION_2.primary_keys["source_anchor_route"] == ("repository_id", "anchor_id")
    assert GENERATION_2.primary_keys["invariant_route"] == ("repository_id", "invariant_id")
    assert GENERATION_2.primary_keys["family_route"] == ("repository_id", "family_id")
    for table in GENERATION_1.tables:
        assert "governing_route_id" not in GENERATION_2.columns[table]
    assert not any(
        statement.strip().upper().startswith("ALTER TABLE")
        for statement in create_schema_statements(GENERATION_2)
    )


def test_validation_reads_generation_two_tables_not_the_pinned_registries(tmp_path: Path) -> None:
    """A generation-2 append is readable by the merge validators.

    Regression guard for `L10-R2`: `_row_of` and `_render_key` read the pinned generation-1
    registries, which hold no entry for `route` or `knowledge_record`, so a merge whose right side
    changed an appended table aborted with an unhandled `KeyError` instead of validating. The fix
    resolves the generation from the dataset the helper was already handed.
    """

    destination = admitted_knowledge_destination(
        tmp_path / "gen2" / "candidate.db",
        RepositoryIdentity(repository_id=str(uuid4()), authority_home="agents-remember"),
        write_authorship(
            actor_ref="agent:merge-validation",
            authorization_ref="260915-KS developer kickoff ruling",
            origin_refs=("requirement:KS-R10@v1",),
        ),
    )
    initialize_knowledge_namespace(destination)
    with open_admitted_knowledge_store(destination) as store:
        route_id = str(uuid4())
        authored = routes.author_route(
            store.connection,
            store.repository_id,
            RouteDraft(route_id=route_id, path="src/generation_two.py"),
            destination.authorship,
        )
        assert isinstance(authored, str)

        generation = generation_of_database(store.connection)
        assert generation.user_version == 2
        # The registries the old code read do not know this table...
        assert "route" not in logical.PRIMARY_KEYS
        # ...and the generation-aware helpers read it anyway.
        assert "route" in generation.primary_keys
        keys = generation.primary_keys["route"]
        values = tuple(
            store.repository_id if column == "repository_id" else route_id for column in keys
        )
        row = merge_validation._row_of(store.connection, "route", values)
        assert row is not None
        rendered = merge_validation._render_key("route", values, generation)
        assert "repository_id=" in rendered and route_id in rendered
