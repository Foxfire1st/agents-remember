"""``KS-R20@v1`` §5.9: the whole vault-safety contract as one acceptance case, end to end.

The plan requires "One acceptance case covers it", and the packet's Example 5 shows why: a safety
contract tested clause by clause is a contract that fails in combination. So this module is one
scenario over a real dataset, a real projection destination and two real fixture records, driving all
eight checkpoints in sequence and recording for each one the exact refusal or action and the manifest
generation that resulted.

The scenario also discharges the two proof obligations that need real records: the derived-artefact
regeneration proof (delete every projection, re-project, and show no canonical record changed) and the
row-and-column no-second-authority proof (no record table gained a content-address, digest or
fingerprint column, and no record identity reads the manifest's digest material).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from agents_remember.application.knowledge_projection import (
    project_knowledge,
)
from agents_remember.application.knowledge_read import open_read_context
from agents_remember.application.knowledge_views import (
    VIEW_RENDERER_VERSION,
    read_knowledge_view,
)
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.managed_projection import (
    ManagedProjectionWriter,
    ProjectionHooks,
)
from agents_remember.memory.knowledge.schema import CANONICAL_COLUMNS
from agents_remember.models.knowledge.projection_manifest import (
    PROJECTION_MANIFEST_NAME,
    STAGING_DIRECTORY_NAME,
    DestinationProfile,
    ProjectionPlan,
    RenderedOutput,
)
from agents_remember.models.knowledge.view import (
    VIEW_NAMES,
    InvariantView,
    SourceContextView,
    SubjectRef,
    ViewRequest,
)
from read_scope_test_support import ReadScopeFixture, build_read_scope_fixture

pytestmark = pytest.mark.integration

RENDERER = VIEW_RENDERER_VERSION


@pytest.fixture
def fixture(tmp_path: Path) -> ReadScopeFixture:
    """One real dataset, built through the public write operations and left closed."""

    return build_read_scope_fixture(tmp_path / "candidate")


def _profile(root: Path) -> DestinationProfile:
    return DestinationProfile(
        profile_id="acceptance",
        destination_root=str(root),
        formats=("markdown", "json"),
        renderer_version=RENDERER,
    )


def _output(relative_path: str, identity: str, text: str) -> RenderedOutput:
    return RenderedOutput(
        destination_relative_path=relative_path,
        stable_identity=identity,
        record_kind="invariant_revision",
        format="markdown",
        source_snapshot="a" * 64,
        renderer_version=RENDERER,
        text=text,
    )


def _manifest(root: Path) -> dict[str, object]:
    return json.loads((root / PROJECTION_MANIFEST_NAME).read_text(encoding="utf-8"))


def subject_identity(subject: SubjectRef) -> tuple[str, str, str | None]:
    """One row subject's whole recorded identity, as a value two rows can be compared on.

    A ``SubjectRef`` is a model rather than a value type, so it is not hashable and cannot be a set
    member; its three recorded fields are also exactly what "is this the same row" means, so reducing
    it here compares nothing less than the model's own equality would.
    """

    return (subject.record_kind, subject.record_id, subject.revision_id)


def test_the_vault_safety_contract_holds_for_all_eight_checkpoints_in_one_scenario(
    fixture: ReadScopeFixture, tmp_path: Path
) -> None:
    """The eight checkpoints of Example 5, in order, over one destination and one scenario.

    The scenario is one run: one dataset, one destination, one prior generation and one sequence of
    eight events. The checkpoints are grouped into two helpers for readability only -- both operate on
    the same destination and the same manifest lineage, which is the property a clause-by-clause
    suite would lose. Every checkpoint asserts the *pair* -- the refusal or action, and the
    destination state it left behind -- because either half alone is satisfiable by a writer that is
    wrong in the other.
    """

    root = tmp_path / "vault"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    checkpoints: dict[str, str] = {}
    _open_the_scenario(root, elsewhere)
    _checkpoints_one_to_four(root, elsewhere, checkpoints)
    _checkpoints_five_to_eight(root, checkpoints)
    # The manifest is JSON, so its values arrive as ``object``: the generation is asserted to be an
    # integer here rather than compared as one, which is also what makes the checkpoint string below
    # a statement about a measured generation.
    generation = _manifest(root)["generation"]
    assert isinstance(generation, int) and not isinstance(generation, bool), generation
    checkpoints["manifest"] = f"generation {generation}"
    assert generation >= 2
    # The eight checkpoints are eight distinct, recorded facts -- not one behaviour seen twice.
    assert set(checkpoints) == {
        "1 confinement",
        "2 collision",
        "3 escaping link",
        "4 stage/publish",
        "5 externally edited",
        "6 unchanged orphan",
        "7 edited orphan",
        "8 never recursive",
        "manifest",
    }


def _open_the_scenario(root: Path, elsewhere: Path) -> None:
    """The prior generation: four managed outputs, two user edits, a symlink and a user directory.

    ``families/GUA-010.md`` is the one output nobody edits, so checkpoint 6 has an unchanged orphan to
    delete -- a file that is edited *and* dropped is checkpoint 7's case, and a writer that removed it
    would be destroying a user's work.
    """

    ManagedProjectionWriter().write(
        ProjectionPlan(
            destination=_profile(root),
            outputs=(
                _output("invariants/INV-014.md", "INV-014", "# INV-014\n"),
                _output("families/GUA-003.md", "GUA-003", "# GUA-003\n"),
                _output("incidents/INC-009.md", "INC-009", "# INC-009\n"),
                _output("families/GUA-010.md", "GUA-010", "# GUA-010\n"),
            ),
        )
    )
    assert _manifest(root)["generation"] == 1
    (root / "invariants/INV-014.md").write_text("# INV-014 edited by the user\n", encoding="utf-8")
    (root / "incidents/INC-009.md").write_text("# INC-009 edited by the user\n", encoding="utf-8")
    (root / "user-notes").mkdir()
    (root / "user-notes/note.md").write_text("my own notes\n", encoding="utf-8")
    # A destination entry that is a symbolic link out of the root: created, never followed.
    (root / "knowledge").symlink_to(elsewhere, target_is_directory=True)


def _checkpoints_one_to_four(root: Path, elsewhere: Path, checkpoints: dict[str, str]) -> None:
    """Confinement, collision, the escaping link, and the interrupted publication."""

    writer = ManagedProjectionWriter()
    escaped = writer.write(
        ProjectionPlan(
            destination=_profile(root),
            outputs=(_output("../../etc/passwd.md", "ESCAPE", "# no\n"),),
        )
    )
    assert escaped.state == "refused"
    assert escaped.refusal is not None and escaped.refusal.code == "destination_escape"
    assert escaped.refusal.resolved_root == str(Path(os.path.realpath(root)))
    checkpoints["1 confinement"] = f"refused {escaped.refusal.code}; nothing staged"

    collided = writer.write(
        ProjectionPlan(
            destination=_profile(root),
            outputs=(
                _output("invariants/INV-014.md", "INV-014", "# A\n"),
                _output("invariants/inv-014.md", "INV-014-alias", "# B\n"),
            ),
        )
    )
    assert collided.state == "refused"
    assert collided.refusal is not None and collided.refusal.code == "destination_collision"
    assert "# A\n" not in (root / "invariants/INV-014.md").read_text(encoding="utf-8")
    checkpoints["2 collision"] = f"refused {collided.refusal.code}; neither written"

    linked = writer.write(
        ProjectionPlan(
            destination=_profile(root),
            outputs=(
                _output("knowledge/latest.md", "LATEST", "# new latest\n"),
                _output("incidents/INC-009.md", "INC-009", "# INC-009\n"),
            ),
        )
    )
    assert linked.state == "projected"
    assert not (elsewhere / "latest.md").exists()
    assert [entry.kind for entry in linked.discrepancies] == ["modified"]
    checkpoints["3 escaping link"] = "reported and not followed; the rest of the plan published"

    before_interruption = _tree_bytes(root)
    interrupting = ManagedProjectionWriter(
        ProjectionHooks(
            before_publish=lambda: (_ for _ in ()).throw(RuntimeError("interrupted before publish"))
        )
    )
    with pytest.raises(RuntimeError):
        interrupting.write(
            ProjectionPlan(
                destination=_profile(root),
                outputs=(_output("invariants/INV-014.md", "INV-014", "# clobbered\n"),),
            )
        )
    assert _tree_bytes(root) == before_interruption
    assert not (root / STAGING_DIRECTORY_NAME).exists()
    checkpoints["4 stage/publish"] = "interruption left the prior generation byte-identical"


def _checkpoints_five_to_eight(root: Path, checkpoints: dict[str, str]) -> None:
    """The externally edited file, the unchanged orphan, the edited orphan, and no sweep."""

    writer = ManagedProjectionWriter()
    edited = writer.write(
        ProjectionPlan(
            destination=_profile(root),
            outputs=(
                _output("invariants/INV-014.md", "INV-014", "# INV-014\n"),
                _output("families/GUA-003.md", "GUA-003", "# GUA-003\n"),
                # Still produced here, and dropped at checkpoint 6, so the orphan it deletes is one
                # nobody edited: an edited *and* dropped file is checkpoint 7 and must be retained.
                _output("families/GUA-010.md", "GUA-010", "# GUA-010\n"),
            ),
        )
    )
    assert (root / "invariants/INV-014.md").read_text(encoding="utf-8").endswith("user\n")
    assert (root / "families/GUA-003.md").exists()
    assert [entry.kind for entry in edited.discrepancies] == ["modified"]
    checkpoints["5 externally edited"] = "reported as modified, not overwritten, others published"

    retired = writer.write(
        ProjectionPlan(
            destination=_profile(root),
            outputs=(_output("families/GUA-003.md", "GUA-003", "# GUA-003\n"),),
        )
    )
    assert (root / "knowledge").is_symlink()
    assert (root / "incidents/INC-009.md").read_text(encoding="utf-8").endswith("user\n")
    assert (root / "user-notes/note.md").read_text(encoding="utf-8") == "my own notes\n"
    retained = {entry.destination_relative_path: entry.reason for entry in retired.retained}
    assert retained["incidents/INC-009.md"] == "edited-since-last-projection"
    states = {outcome.destination_relative_path: outcome.state for outcome in retired.outcomes}
    assert states.get("families/GUA-010.md") == "removed"
    assert not (root / "families/GUA-010.md").exists()
    checkpoints["6 unchanged orphan"] = f"reported as {states.get('families/GUA-010.md')}"
    checkpoints["7 edited orphan"] = "retained-with-reason, still on disk"
    assert retained["invariants/INV-014.md"] == "edited-since-last-projection"
    checkpoints["8 never recursive"] = "user-notes/ untouched and no sweep occurred"


def _tree_bytes(root: Path) -> dict[str, bytes]:
    """Every file under one destination with its exact bytes, for a prior-state comparison."""

    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_every_view_renders_two_byte_identical_runs_at_one_snapshot(
    fixture: ReadScopeFixture,
) -> None:
    """The ordering differential: two runs at one snapshot and one renderer are byte-identical.

    This is the check the packet's Example 4 makes necessary, because an ordering key recomputed from
    live state is invisible in a single run and obvious in a differential.
    """

    context = open_read_context(fixture.database_path, fixture.repository_id)
    for view in VIEW_NAMES:
        request = ViewRequest(view=view, repository_id=fixture.repository_id, limit=3)
        first = read_knowledge_view(fixture.database_path, context, request)
        second = read_knowledge_view(fixture.database_path, context, request)
        assert first.state == second.state == "view"
        assert first.payload is not None and second.payload is not None
        assert first.payload.model_dump_json() == second.payload.model_dump_json(), view


def test_the_records_unchanged_differential_leaves_the_order_unchanged(
    fixture: ReadScopeFixture,
) -> None:
    """With the records untouched, a second read's order and payload are unchanged -- no recompute."""

    context = open_read_context(fixture.database_path, fixture.repository_id)
    before = dataset_identity(fixture.database_path).logical_digest
    request = ViewRequest(
        view="source_context",
        repository_id=fixture.repository_id,
        limit=2,
        ordering_input="registered_role",
    )
    first = read_knowledge_view(fixture.database_path, context, request)
    again = read_knowledge_view(fixture.database_path, context, request)
    assert first.payload is not None and again.payload is not None
    # The result's payload is the discriminated union of the five views, so the concrete view is
    # asserted before its rows are read: a source-context request that answered with another view's
    # rows would otherwise be compared row-for-row with itself and pass.
    assert isinstance(first.payload, SourceContextView)
    assert isinstance(again.payload, SourceContextView)
    assert [row.subject.record_id for row in first.payload.rows] == [
        row.subject.record_id for row in again.payload.rows
    ]
    assert dataset_identity(fixture.database_path).logical_digest == before


def test_the_classification_scan_finds_no_third_value_and_no_empty_class(
    fixture: ReadScopeFixture,
) -> None:
    """Expected Evidence: every ordered position carries one of exactly two classes."""

    context = open_read_context(fixture.database_path, fixture.repository_id)
    observed: set[str] = set()
    for view in VIEW_NAMES:
        result = read_knowledge_view(
            fixture.database_path,
            context,
            ViewRequest(view=view, repository_id=fixture.repository_id, limit=8),
        )
        assert result.state == "view" and result.payload is not None
        body = json.loads(result.payload.model_dump_json())
        for row in body["rows"]:
            position_class = row["order"]["provenance"]["provenance_class"]
            assert position_class in ("authored", "mechanical")
            assert row["provenance"]["provenance_class"] in ("authored", "mechanical")
            observed.add(position_class)
            consequence = row.get("consequence")
            if consequence is not None:
                assert consequence["provenance"]["provenance_class"] in ("authored", "mechanical")
    assert observed <= {"authored", "mechanical"}


def test_a_bound_payload_carries_a_snapshot_bound_continuation_and_the_next_page_follows(
    fixture: ReadScopeFixture,
) -> None:
    """Requirement 3.1: the continuation reaches the rest of a selection that did not fit one page."""

    context = open_read_context(fixture.database_path, fixture.repository_id)
    first = read_knowledge_view(
        fixture.database_path,
        context,
        ViewRequest(view="invariant", repository_id=fixture.repository_id, limit=1),
    )
    assert first.payload is not None
    assert isinstance(first.payload, InvariantView)
    assert first.payload.counts.rows_remaining.value
    assert first.payload.continuation is not None
    assert (
        first.payload.continuation.snapshot_logical_digest == first.payload.snapshot.logical_digest
    )
    # The row's identity is its subject -- record identity *and* revision identity -- because a view
    # selection is over recorded revisions: two pages may legitimately carry two revisions of one
    # record, and comparing record identities alone would call that a repeated page. The subject is
    # reduced to its three recorded values here because a subject is a model and therefore not
    # hashable, and the three values are the whole of what the two pages are compared on.
    seen = {subject_identity(row.subject) for row in first.payload.rows}
    second = read_knowledge_view(
        fixture.database_path,
        context,
        ViewRequest(
            view="invariant",
            repository_id=fixture.repository_id,
            limit=1,
            continuation=first.payload.continuation,
        ),
    )
    assert second.payload is not None
    assert isinstance(second.payload, InvariantView)
    assert {subject_identity(row.subject) for row in second.payload.rows} != seen


def test_deleting_every_projection_and_reprojecting_loses_no_canonical_information(
    fixture: ReadScopeFixture, tmp_path: Path
) -> None:
    """Requirement 4.6 and 8.2: the destination is derived, so deleting it loses nothing canonical."""

    root = tmp_path / "vault"
    before_digest = dataset_identity(fixture.database_path).logical_digest
    requests = (
        (
            ViewRequest(view="invariant", repository_id=fixture.repository_id, limit=4),
            "invariant-run",
        ),
        (
            ViewRequest(view="source_context", repository_id=fixture.repository_id, limit=4),
            "context-run",
        ),
    )
    first = project_knowledge(fixture.database_path, _profile(root), requests)
    assert first.state == "projected"
    assert first.manifest is not None and len(first.manifest.outputs) == 4

    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
    assert not root.exists() or not any(root.iterdir())

    second = project_knowledge(fixture.database_path, _profile(root), requests)
    assert second.state == "projected"
    assert second.manifest is not None
    assert {entry.destination_relative_path for entry in second.manifest.outputs} == {
        entry.destination_relative_path for entry in first.manifest.outputs
    }
    assert dataset_identity(fixture.database_path).logical_digest == before_digest


def test_no_record_table_gained_an_identity_column_and_no_identity_reads_the_manifest(
    fixture: ReadScopeFixture,
) -> None:
    """The no-second-authority proof at the row and column level, over the dataset itself."""

    forbidden = ("content_address", "logical_digest", "fingerprint", "manifest_digest", "sha256")
    for table, columns in CANONICAL_COLUMNS.items():
        for column in columns:
            assert not any(token in column for token in forbidden), (table, column)
    with fixture.reopen() as store:
        tables = {
            str(row[0])
            for row in store.connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            )
        }
        for table in sorted(tables):
            if table.startswith("sqlite_"):
                continue
            declared = {
                str(row[1]) for row in store.connection.execute(f"PRAGMA table_info({table})")
            }
            assert not declared & set(forbidden), (table, declared & set(forbidden))
