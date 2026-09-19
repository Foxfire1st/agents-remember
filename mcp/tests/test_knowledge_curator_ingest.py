"""The curator's ingest: one hand-off entry becomes one admitted batch and one readable citation.

The knowledge write plane had no production caller until
:mod:`agents_remember.application.knowledge_ingest` existed -- every importer of the application
knowledge seam was a test and the mounted change tool refuses every record kind -- so these cases are
the reachable path's own evidence, and each one names the part of the claim it measures:

* a citation committed for a **symbol** and one committed for a **line range** both read back with
  their path, their source identity and their decoded locator, on the mounted view surface;
* the locator of a row the ingest did **not** write is decoded too, which is the read-path defect the
  fix closes: a consumer handed the stored text cannot classify a locator at all;
* the claim really is the edge ``invariant_revision_id -> anchor_id`` the statement is reached by;
* the recorded blob identity is what the knowledge lane's own observation can verify -- the exact
  recorded bytes, and a refusal to call different bytes at the same path a success;
* and none of it moves a sealed revision: equal sealed content digests equally with and without a
  citation, and every pre-existing revision row stays byte-identical across an ingest.

The fixtures are the shipped ones. ``read_scope_test_support`` builds a real dataset beside a real
Git tree, which is what lets the observation cases resolve against bytes rather than assert a
constant.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

import pytest
from agents_remember.application.knowledge import (
    admitted_knowledge_destination,
    write_authorship,
)
from agents_remember.application.knowledge_ingest import (
    CuratorCitation,
    CuratorEntry,
    commit_curator_entry,
)
from agents_remember.application.knowledge_read import open_read_context
from agents_remember.application.knowledge_views import read_knowledge_view
from agents_remember.memory.knowledge import anchors, realizations
from agents_remember.memory.knowledge.connection import inspect_schema
from agents_remember.memory.knowledge.read_anchors import observe_anchor
from agents_remember.memory.knowledge.view_source import store_view_reader
from agents_remember.models.knowledge.candidate import CandidateResolution
from agents_remember.models.knowledge.context import AdmittedKnowledgeDestination
from agents_remember.models.knowledge.graph import RealizationRole
from agents_remember.models.knowledge.read import AnchorResolution
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.source import (
    GitBlobIdentity,
    LineRangeLocator,
    SourceAnchor,
    SourceAnchorDraft,
    SourceLocator,
    SymbolLocator,
)
from agents_remember.models.knowledge.view import SourceContextRow, SourceContextView, ViewRequest
from candidate_batch_test_support import (
    CandidateHarness,
    build_candidate_harness,
    table_counts,
)
from read_scope_test_support import (
    INTEGRATION_PATH,
    REPOSITORY_AUTHORITY_HOME,
    SYNCHRONIZATION_PATH,
    ReadScopeFixture,
    build_read_scope_fixture,
)

pytestmark = pytest.mark.evidence_unit

# A recorded identity the fixture tree does not hold at ``INTEGRATION_PATH``, so "the bytes at this
# path are not the bytes this anchor recorded" is a measurement about the tree and not a constant.
WRONG_RECORDED_BLOB = "0" * 40

# The symbol the fixture's ``INTEGRATION_PATH`` really binds, as ``class SharedBudget`` at line 5
# with its ``retry_budget`` method at 6-7. A qualified name is resolved by its real halves, so this
# is exactly the shape the ingest stores and the shape the rail has to observe.
SYMBOL_LOCATOR = SymbolLocator(language="python", qualified_name="SharedBudget.retry_budget")
# A name the same bytes mention in prose and never bind, so the rail has one symbol it must report
# as present-but-not-a-definition rather than resolving it off the mention.
UNBOUND_SYMBOL_LOCATOR = SymbolLocator(language="python", qualified_name="SharedBudget.nowhere")
RANGE_LOCATOR = LineRangeLocator(start_line=3, end_line=7)
RATIONALE = "The statement is realized by the construct this recorded anchor names."
FIXED_REPOSITORY_ID = "7c1f2a4e-9b3d-4f6a-8e21-5d0b7c9a1e33"


@dataclass(frozen=True)
class Authored:
    """One curator entry and the identities its assertions name later."""

    entry: CuratorEntry
    citation: CuratorCitation


@pytest.fixture
def fixture(tmp_path: Path) -> ReadScopeFixture:
    """One real dataset beside one real Git tree, built through the public write operations."""

    return build_read_scope_fixture(tmp_path / "read-scope")


def admitted(fixture: ReadScopeFixture) -> AdmittedKnowledgeDestination:
    """The fixture's own dataset, admitted as the curator's draft-candidate destination."""

    return admitted_knowledge_destination(
        fixture.database_path,
        RepositoryIdentity(
            repository_id=fixture.repository_id, authority_home=REPOSITORY_AUTHORITY_HOME
        ),
        fixture.authorship,
    )


def resolution(fixture: ReadScopeFixture) -> CandidateResolution:
    """The candidate inputs one ingest resolves its batch context against."""

    return CandidateResolution(
        lane="draft-candidate",
        code_tree_id=fixture.git_tree_id,
        memory_tree_id="b" * 40,
        snapshot_ref="candidate:curator-ingest",
        candidate_ref="draft:curator-ingest",
    )


def authored(
    *,
    path: str,
    blob: str,
    locator: SourceLocator,
    role: RealizationRole = "primary-authority",
) -> Authored:
    """One entry carrying one resolved citation at one recorded location."""

    citation = CuratorCitation(
        anchor=SourceAnchorDraft(
            anchor_id=uuid4(),
            path=path,
            source_identity=GitBlobIdentity(object_id=blob),
            locator=locator,
        ),
        claim_id=str(uuid4()),
        role=role,
        rationale=RATIONALE,
    )
    return Authored(
        entry=CuratorEntry(
            invariant_id=str(uuid4()),
            display_label="curator-ingest-citation",
            revision_id=str(uuid4()),
            display_version="v1",
            statement="A curator entry carries the citation its statement is read beside.",
            applicability="Every curator entry committed into this candidate.",
            conditions=("The entry names a confined repository-relative path.",),
            citations=(citation,),
        ),
        citation=citation,
    )


def committed(fixture: ReadScopeFixture, held: Authored) -> None:
    """Commit one authored entry through the ingest, failing loudly on a refused batch."""

    receipt = commit_curator_entry(admitted(fixture), resolution(fixture), held.entry)
    if receipt.state != "changed":
        raise AssertionError(f"the curator entry was not committed: {receipt.refusal!r}")


def stored_anchor(fixture: ReadScopeFixture, anchor_id: str) -> SourceAnchor:
    """One stored anchor as the store reads it back, decoded to the typed union."""

    with fixture.reopen() as store:
        anchor = anchors.get_anchor(store, anchor_id)
    if anchor is None:
        raise AssertionError(f"anchor {anchor_id} is not stored")
    return anchor


def view_rows(fixture: ReadScopeFixture) -> tuple[SourceContextRow, ...]:
    """The source-context view's rows, read through the mounted view seam."""

    context = open_read_context(fixture.database_path, fixture.repository_id)
    result = read_knowledge_view(
        fixture.database_path,
        context,
        ViewRequest(view="source_context", repository_id=fixture.repository_id),
    )
    if result.state != "view" or not isinstance(result.payload, SourceContextView):
        raise AssertionError(f"the source-context view did not render: {result.refusal!r}")
    return result.payload.rows


def row_for(rows: tuple[SourceContextRow, ...], claim_id: str) -> SourceContextRow:
    """The one view row the named recorded claim produced."""

    found = [row for row in rows if row.subject.record_id == claim_id]
    if len(found) != 1:
        raise AssertionError(f"claim {claim_id} produced {len(found)} view rows")
    return found[0]


def observed(fixture: ReadScopeFixture, anchor: SourceAnchor) -> AnchorResolution:
    """One recorded anchor observed against the fixture's own tree.

    The observation is handed the *decoded* locator, which is what the read path produces and what
    the resolver classifies on -- the whole point of decoding it before anything resolves.
    """

    return observe_anchor(
        {
            "anchor_id": str(anchor.anchor_id),
            "path": anchor.path,
            "source_identity": anchor.source_identity.model_dump(mode="json"),
            "locator": anchor.locator.model_dump(mode="json"),
        },
        repository_root=fixture.git_root,
        tree_id=fixture.git_tree_id,
    )


def test_a_symbol_citation_reads_back_with_its_path_identity_and_locator(
    fixture: ReadScopeFixture,
) -> None:
    """A symbol is the preferred pointer, and the recorded pair survives the round trip whole."""

    blob = fixture.git_blobs[INTEGRATION_PATH]
    held = authored(path=INTEGRATION_PATH, blob=blob, locator=SYMBOL_LOCATOR)
    committed(fixture, held)

    stored = stored_anchor(fixture, str(held.citation.anchor.anchor_id))
    assert stored.path == INTEGRATION_PATH
    assert stored.source_identity.object_id == blob
    assert isinstance(stored.locator, SymbolLocator), stored.locator
    assert stored.locator.language == "python"
    assert stored.locator.qualified_name == "SharedBudget.retry_budget"

    row = row_for(view_rows(fixture), held.citation.claim_id)
    assert row.path == INTEGRATION_PATH
    assert isinstance(row.locator, SymbolLocator), row.locator
    assert row.locator == SYMBOL_LOCATOR

    # A symbol locator IS observed, through the shipped tree-sitter extractor the citation fixer,
    # repair and migration paths already use: the recorded bytes are present at the recorded path
    # and they bind the qualified name the locator carries, so the resolution is the same
    # ``exact_recorded_blob`` a file locator earns -- and the detail carries the extent the
    # extractor found, which is what makes a symbol citation verifiable rather than merely stored.
    observation = observed(fixture, stored)
    assert observation.resolution == "exact_recorded_blob"
    assert "src/integration.py:6-7" in observation.detail
    assert observation.locator == SYMBOL_LOCATOR
    assert observation.recorded_source_identity == blob

    # The same bytes, asked about a symbol they do NOT bind: present-but-not-a-definition is its
    # own answer and not a silent success. This is the distinction the whole observation exists to
    # make, so the case carries both directions.
    unbound = authored(
        path=INTEGRATION_PATH, blob=blob, locator=UNBOUND_SYMBOL_LOCATOR, role="enforcement"
    )
    committed(fixture, unbound)
    unbound_observation = observed(
        fixture, stored_anchor(fixture, str(unbound.citation.anchor.anchor_id))
    )
    assert unbound_observation.resolution == "recorded_blob_mismatch"
    assert "SharedBudget.nowhere" in unbound_observation.detail
    assert unbound_observation.recorded_source_identity == blob


def test_a_line_range_citation_reads_back_as_its_recorded_extent(
    fixture: ReadScopeFixture,
) -> None:
    """The extent is carried, and it is the recorded rendering rather than the record's identity."""

    blob = fixture.git_blobs[INTEGRATION_PATH]
    held = authored(path=INTEGRATION_PATH, blob=blob, locator=RANGE_LOCATOR)
    committed(fixture, held)

    stored = stored_anchor(fixture, str(held.citation.anchor.anchor_id))
    assert stored.locator == LineRangeLocator(start_line=3, end_line=7)

    row = row_for(view_rows(fixture), held.citation.claim_id)
    assert row.path == INTEGRATION_PATH
    assert isinstance(row.locator, LineRangeLocator), row.locator
    assert (row.locator.start_line, row.locator.end_line) == (3, 7)


def test_the_read_surface_decodes_the_locator_of_a_row_the_ingest_did_not_write(
    fixture: ReadScopeFixture,
) -> None:
    """A payload value that is the stored TEXT cannot be classified, so it must be decoded.

    The row read here was written by the single-record operation before any ingest ran, so this is a
    statement about the reader rather than about the ingest: the same decode is what keeps a symbol
    locator from falling through to the file branch and being published as a path resolution.
    """

    with fixture.reopen() as store:
        raw = store.connection.execute(
            "SELECT locator FROM source_anchor WHERE repository_id = ? AND anchor_id = ?",
            (fixture.repository_id, fixture.synchronization.anchor_id),
        ).fetchone()
        if raw is None:
            raise AssertionError("the fixture's synchronization anchor is not stored")
        reader = store_view_reader(store, inspect_schema(store.connection))
        # The reader port hands the view a flat row, keyed by record identity rather than by subject.
        payload = next(
            row.payload
            for row in reader.realization_rows()
            if row.record_id == fixture.synchronization.claim_id
        )

    assert isinstance(raw[0], str), raw[0]
    assert payload["locator"] == {"kind": "line_range", "start_line": 3, "end_line": 7}

    row = row_for(view_rows(fixture), fixture.synchronization.claim_id)
    assert row.path == SYNCHRONIZATION_PATH
    assert row.locator == LineRangeLocator(start_line=3, end_line=7)


def test_the_claim_is_the_edge_from_the_revision_to_its_anchor(
    fixture: ReadScopeFixture,
) -> None:
    """The citation is reached by a recorded edge, not by convention: revision -> claim -> anchor."""

    blob = fixture.git_blobs[INTEGRATION_PATH]
    held = authored(path=INTEGRATION_PATH, blob=blob, locator=SYMBOL_LOCATOR, role="enforcement")
    committed(fixture, held)

    anchor_id = str(held.citation.anchor.anchor_id)
    with fixture.reopen() as store:
        claim = realizations.get_realization_claim(store, held.citation.claim_id)
        pair = realizations.find_claim_by_pair(store, held.entry.revision_id, anchor_id)

    assert claim is not None
    assert claim.invariant_revision_id == held.entry.revision_id
    assert claim.anchor_id == anchor_id
    assert claim.role == "enforcement"
    assert claim.rationale == RATIONALE
    assert pair is not None and pair.claim_id == held.citation.claim_id

    # The view's realization row names the claim as its subject and the cited revision alongside it,
    # so a consumer reading the citation reads which statement it belongs to.
    row = row_for(view_rows(fixture), held.citation.claim_id)
    assert row.subject.revision_id == held.entry.revision_id
    assert row.role == "enforcement"


def test_the_recorded_blob_identity_is_what_the_observation_can_verify(
    fixture: ReadScopeFixture,
) -> None:
    """The verifiable half of a citation is its identity, and it refuses a false success."""

    exact = authored(
        path=INTEGRATION_PATH, blob=fixture.git_blobs[INTEGRATION_PATH], locator=RANGE_LOCATOR
    )
    mismatched = authored(path=INTEGRATION_PATH, blob=WRONG_RECORDED_BLOB, locator=RANGE_LOCATOR)
    committed(fixture, exact)
    committed(fixture, mismatched)

    observed_exact = observed(fixture, stored_anchor(fixture, str(exact.citation.anchor.anchor_id)))
    assert observed_exact.resolution == "exact_recorded_blob"
    assert observed_exact.recorded_source_identity == fixture.git_blobs[INTEGRATION_PATH]
    assert observed_exact.observed_source_identity == fixture.git_blobs[INTEGRATION_PATH]

    observed_mismatch = observed(
        fixture, stored_anchor(fixture, str(mismatched.citation.anchor.anchor_id))
    )
    assert observed_mismatch.resolution == "recorded_blob_mismatch"
    assert observed_mismatch.recorded_source_identity == WRONG_RECORDED_BLOB
    assert observed_mismatch.observed_source_identity == fixture.git_blobs[INTEGRATION_PATH]


def test_a_citation_moves_no_sealed_revision_byte(
    fixture: ReadScopeFixture, tmp_path: Path
) -> None:
    """No digest, seal or stored revision row moves: the citation is a second record.

    Two measurements, because the claim has two halves. Equal sealed content committed with and
    without a citation must digest equally -- that is the preimage statement -- and every revision
    already stored before an ingest must be byte-identical after it -- that is the stored-rows
    statement.
    """

    before = _revision_rows(fixture)
    held = authored(
        path=INTEGRATION_PATH, blob=fixture.git_blobs[INTEGRATION_PATH], locator=RANGE_LOCATOR
    )
    committed(fixture, held)
    after = _revision_rows(fixture)

    assert set(before.items()) <= set(after.items())
    assert set(after) - set(before) == {held.entry.revision_id}

    citation_entry = _harness_entry(held.entry)
    plain_entry = replace(citation_entry, citations=())
    with_citation = build_candidate_harness(
        tmp_path / "with-citation", repository_id=FIXED_REPOSITORY_ID
    )
    without_citation = build_candidate_harness(
        tmp_path / "without-citation", repository_id=FIXED_REPOSITORY_ID
    )
    shared = write_authorship(
        actor_ref="agent:curator-ingest-case",
        authorization_ref="260915-KS developer kickoff ruling",
    )
    with_citation = replace(with_citation, authorship=shared)
    without_citation = replace(without_citation, authorship=shared)

    receipt = commit_curator_entry(
        with_citation.destination(), with_citation.resolution, citation_entry
    )
    plain = commit_curator_entry(
        without_citation.destination(), without_citation.resolution, plain_entry
    )
    assert receipt.state == "changed" and receipt.refusal is None
    assert plain.state == "changed" and plain.refusal is None
    # The citation really was carried by the batch that digested equally with the plain one: the
    # receipt names all four rows, and the plain dataset holds no anchor or claim at all.
    assert {entry.table for entry in receipt.changed} == {
        "invariant",
        "invariant_revision",
        "source_anchor",
        "realization_claim",
    }
    assert _harness_revision_row(
        with_citation, citation_entry.revision_id
    ) == _harness_revision_row(without_citation, plain_entry.revision_id)
    with with_citation.open() as store:
        cited = (table_counts(store)["source_anchor"], table_counts(store)["realization_claim"])
    with without_citation.open() as store:
        uncited = (table_counts(store)["source_anchor"], table_counts(store)["realization_claim"])
    assert cited == (1, 1)
    assert uncited == (0, 0)


def _revision_rows(fixture: ReadScopeFixture) -> dict[str, tuple[object, ...]]:
    """Every stored invariant revision row, keyed by identity, as the database holds it."""

    with fixture.reopen() as store:
        rows = store.connection.execute(
            "SELECT revision_id, invariant_id, display_version, statement, applicability, "
            "conditions, exclusions, state_at_origin, acceptance_ref, provenance, payload_digest "
            "FROM invariant_revision WHERE repository_id = ? ORDER BY revision_id",
            (fixture.repository_id,),
        )
        return {str(row[0]): tuple(row) for row in rows}


def _harness_entry(entry: CuratorEntry) -> CuratorEntry:
    """One entry rebuilt with identities both candidate datasets can author independently."""

    return replace(
        entry,
        invariant_id=str(uuid4()),
        revision_id=str(uuid4()),
        citations=tuple(replace(citation, claim_id=str(uuid4())) for citation in entry.citations),
    )


def _harness_revision_row(harness: CandidateHarness, revision_id: str) -> tuple[object, ...]:
    """One candidate's stored revision row, digests included, as the database holds it."""

    with harness.open() as store:
        row = store.connection.execute(
            "SELECT revision_id, invariant_id, display_version, statement, applicability, "
            "conditions, exclusions, state_at_origin, acceptance_ref, provenance, payload_digest "
            "FROM invariant_revision WHERE repository_id = ? AND revision_id = ?",
            (harness.repository_id, revision_id),
        ).fetchone()
    if row is None:
        raise AssertionError(f"revision {revision_id} is not stored")
    return tuple(row)
