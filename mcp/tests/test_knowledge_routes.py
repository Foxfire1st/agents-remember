"""Focused behaviour of the route write layer: authoring, confinement and governing association.

Each case protects one rule that makes ``Route`` an operable scope axis rather than a declaration:
a path that is not in the one admitted form is refused rather than rewritten, one scope keeps one
route however it is spelled, a hierarchy that reaches itself is refused, and a governed row names at
most one governing route. Constructor validation is not re-tested here.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from agents_remember.application.knowledge import (
    admitted_knowledge_destination,
    initialize_knowledge_namespace,
    open_admitted_knowledge_store,
    write_authorship,
)
from agents_remember.memory.knowledge import routes
from agents_remember.memory.knowledge.routes import GoverningRouteDraft, RouteDraft
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import InvariantRequest


@pytest.fixture
def admitted(tmp_path: Path):
    """One initialized generation-2 candidate, its store and its authored provenance."""

    destination = admitted_knowledge_destination(
        tmp_path / "routes" / "candidate.db",
        RepositoryIdentity(repository_id=str(uuid4()), authority_home="agents-remember"),
        write_authorship(
            actor_ref="agent:routes",
            authorization_ref="260915-KS developer kickoff ruling",
            origin_refs=("requirement:KS-R10@v1",),
        ),
    )
    initialize_knowledge_namespace(destination)
    with open_admitted_knowledge_store(destination) as store:
        yield store, destination.authorship


def _author(store, authorship, path: str, route_id: str | None = None, parent: str | None = None):
    return routes.author_route(
        store.connection,
        store.repository_id,
        RouteDraft(route_id=route_id or str(uuid4()), path=path, parent_route_id=parent),
        authorship,
    )


def test_a_confined_path_is_authored_and_its_identity_returned(admitted) -> None:
    store, authorship = admitted
    route_id = str(uuid4())
    assert _author(store, authorship, "src/knowledge/store.py", route_id) == route_id


def test_one_scope_keeps_one_route_when_the_same_spelling_is_restated(admitted) -> None:
    """A second authoring call for an authored scope returns the first route, not a second row."""

    store, authorship = admitted
    first = _author(store, authorship, "src/a.py")
    second = _author(store, authorship, "src/a.py", route_id=str(uuid4()))
    assert second == first
    rows = list(
        store.connection.execute("SELECT route_id FROM route WHERE path = ?", ("src/a.py",))
    )
    assert len(rows) == 1


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/absolute/path.py",
        "src\\windows.py",
        "C:/drive.py",
        "//unc/share.py",
        "src/../escape.py",
        "src/./dot.py",
        "src//double.py",
        "src/trailing/",
        "src/nul\x00byte.py",
    ],
)
def test_a_path_outside_the_one_admitted_form_is_refused(admitted, path: str) -> None:
    store, authorship = admitted
    authored = _author(store, authorship, path)
    assert not isinstance(authored, str)
    assert authored.code == "invalid_reference"
    assert list(store.connection.execute("SELECT 1 FROM route")) == []


def test_a_child_names_an_authored_parent_and_the_hierarchy_stays_acyclic(admitted) -> None:
    store, authorship = admitted
    parent = _author(store, authorship, "src")
    assert isinstance(parent, str)
    child = _author(store, authorship, "src/knowledge", parent=parent)
    assert isinstance(child, str)
    assert routes.require_acyclic_routes(store.connection, store.repository_id) is None


def test_a_child_naming_an_unauthored_parent_is_refused(admitted) -> None:
    store, authorship = admitted
    refused = _author(store, authorship, "src/knowledge", parent=str(uuid4()))
    assert not isinstance(refused, str)
    assert refused.code == "missing_expected_row"


def test_a_hierarchy_that_reaches_itself_is_refused(admitted) -> None:
    """A cycle is refused however it arrives, not only through the authoring path."""

    store, authorship = admitted
    first = _author(store, authorship, "src")
    assert isinstance(first, str)
    second = _author(store, authorship, "src/knowledge", parent=first)
    store.connection.execute(
        "UPDATE route SET parent_route_id = ? WHERE route_id = ?", (second, first)
    )
    refused = routes.require_acyclic_routes(store.connection, store.repository_id)
    assert refused is not None
    assert refused.code == "lineage_cycle"


def test_a_governed_row_names_at_most_one_governing_route(admitted) -> None:
    store, authorship = admitted
    invariant_id = str(uuid4())
    store.create_invariant(
        InvariantRequest(
            repository_id=store.repository_id,
            invariant_id=invariant_id,
            display_label="a governed invariant",
            provenance=authorship,
        )
    )
    first = _author(store, authorship, "src/one")
    second = _author(store, authorship, "src/two")
    assert isinstance(first, str)
    assert isinstance(second, str)
    assert (
        routes.set_governing_route(
            store.connection,
            store.repository_id,
            GoverningRouteDraft(
                governed_table="invariant", governed_id=invariant_id, route_id=first
            ),
            authorship,
        )
        is None
    )
    assert (
        routes.find_governing_route(
            store.connection, store.repository_id, "invariant", invariant_id
        )
        == first
    )
    restated = routes.set_governing_route(
        store.connection,
        store.repository_id,
        GoverningRouteDraft(governed_table="invariant", governed_id=invariant_id, route_id=first),
        authorship,
    )
    assert restated is None
    conflict = routes.set_governing_route(
        store.connection,
        store.repository_id,
        GoverningRouteDraft(governed_table="invariant", governed_id=invariant_id, route_id=second),
        authorship,
    )
    assert conflict is not None
    assert conflict.code == "relationship_constraint"
    assert (
        routes.find_governing_route(
            store.connection, store.repository_id, "invariant", invariant_id
        )
        == first
    )


def test_an_ungoverned_row_reports_ungoverned_rather_than_a_repository_root(admitted) -> None:
    store, _ = admitted
    assert (
        routes.find_governing_route(
            store.connection, store.repository_id, "invariant", str(uuid4())
        )
        is None
    )


def test_the_envelope_reports_its_own_governing_column_and_refuses_the_setter(admitted) -> None:
    """Requirement 4.4's fourth governed shape: the envelope carries its route in its own column.

    It is read from that column rather than through a join table, so a stored ``NULL`` is the same
    explicit ungoverned fact a missing join row is -- and the after-the-fact setter refuses the
    *operation* instead of claiming the envelope is not a governed entity, which 4.4 makes false.
    """

    store, authorship = admitted
    route_id = _author(store, authorship, "src/knowledge/records.py", str(uuid4()))
    assert isinstance(route_id, str)
    governed_record = str(uuid4())
    ungoverned_record = str(uuid4())
    for record_id, route in ((governed_record, route_id), (ungoverned_record, None)):
        store.connection.execute(
            "INSERT INTO knowledge_record (repository_id, record_id, kind, authority_home, "
            "lifecycle, governing_route_id, record_schema, provenance) VALUES (?, ?, ?, ?, ?, ?, "
            "?, ?)",
            (
                store.repository_id,
                record_id,
                "finding",
                "agents-remember",
                "draft",
                route,
                "finding/v1",
                "{}",
            ),
        )
    assert (
        routes.find_governing_route(
            store.connection, store.repository_id, "knowledge_record", governed_record
        )
        == route_id
    )
    assert (
        routes.find_governing_route(
            store.connection, store.repository_id, "knowledge_record", ungoverned_record
        )
        is None
    )
    # The envelope is a governed entity, so the refusal cannot be "not a governed entity": it names
    # the operation that does not exist and the write that owns the association.
    refused = routes.set_governing_route(
        store.connection,
        store.repository_id,
        GoverningRouteDraft(
            governed_table="knowledge_record", governed_id=governed_record, route_id=route_id
        ),
        authorship,
    )
    assert refused is not None
    assert refused.code == "invalid_reference"
    assert "not a governed entity" not in refused.detail
    assert "its own column" in refused.detail


def test_a_path_that_escapes_through_a_symlink_is_refused(admitted, tmp_path: Path) -> None:
    """Confinement has a lexical half and a resolution half; the spelling alone cannot see a link."""

    _store, _authorship = admitted  # the fixture builds the store; this case needs only its root
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("x = 1\n")
    (root / "src" / "link.py").symlink_to(outside / "secret.py")
    # The lexical rules admit this spelling...
    assert isinstance(routes.normalize_route_path("src/link.py"), str)
    # ...and the resolution check refuses it, because it lands outside the repository.
    refused = routes.require_confined_resolution(root, "src/link.py")
    assert refused is not None
    assert refused.code == "invalid_reference"
    # A path that stays inside resolves cleanly, and a not-yet-existing path is confined by
    # spelling rather than refused for being absent.
    assert routes.require_confined_resolution(root, "src/real.py") is None
    assert routes.require_confined_resolution(root, "src/not_yet.py") is None


def test_asking_about_a_non_governed_table_is_a_caller_error_not_an_ungoverned_row(
    admitted,
) -> None:
    """``None`` means ungoverned; "wrong table" must not collapse into the same answer."""

    store, _ = admitted
    with pytest.raises(ValueError):
        routes.find_governing_route(store.connection, store.repository_id, "route", str(uuid4()))


def test_governing_refuses_an_unknown_governed_table_and_an_unauthored_route(admitted) -> None:
    store, authorship = admitted
    unknown_table = routes.set_governing_route(
        store.connection,
        store.repository_id,
        GoverningRouteDraft(
            governed_table="route", governed_id=str(uuid4()), route_id=str(uuid4())
        ),
        authorship,
    )
    assert unknown_table is not None
    assert unknown_table.code == "invalid_reference"
    unauthored_route = routes.set_governing_route(
        store.connection,
        store.repository_id,
        GoverningRouteDraft(
            governed_table="invariant", governed_id=str(uuid4()), route_id=str(uuid4())
        ),
        authorship,
    )
    assert unauthored_route is not None
    assert unauthored_route.code == "missing_expected_row"
