"""``CitationBinding`` at the real boundaries: real Git objects, a real store, and a real read-back.

Each case crosses a boundary the unit lane can only imitate, and each protects one fact of
`KS-R18@v1` that an in-process double could not falsify:

* the owner revision is resolved against a **real committed object store**, so "the recorded revision
  is available" and "the recorded revision cannot be obtained" are measurements of Git rather than of
  a stub (`read_owner_revisions.py`; 1.1);
* a **stale** binding is produced by really rewriting the document and re-committing it, so "stale is
  not unresolved and neither is repaired by inference" is measured against a moved revision rather
  than asserted (4.4, Example 8);
* the durable evidence read-back is performed against a **real filesystem**, including the failure
  direction -- a destination that was never published, and a destination whose bytes changed after
  publication, each reporting the blocked state with the exact destination and both digests (3.3,
  Example 10);
* the binding rows land in the store's **own committed tables**, which is the recorded sentence
  `CR18V-1` asks for: the dataset identity names the binding table and the rows are part of the
  dataset rather than of anything enclosure-local (3.3);
* a wrong-kind target and a dangling governing route are refused **at the write boundary**, so the
  read path's kind-mismatch state reports a store changed after the write rather than an accepted one
  (1.3, 1.6, Expected Evidence);
* two bindings that claim one key in one owner revision are refused by the store, which is what makes
  the reported ``ambiguous_key`` state a fact about an unreviewed write instead of about an arbitrary
  winner (4.1, Failure And Recovery Behavior).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from agents_remember.memory.knowledge import citations, facets, routes
from agents_remember.memory.knowledge.citation_closure import (
    assemble_citation_closure,
    enumerate_recorded_bindings,
)
from agents_remember.memory.knowledge.durable_evidence import (
    DURABLE_EVIDENCE_REFUSED_DESTINATIONS,
    enclosure_reports_removed,
    publish_durable_evidence,
    read_back_evidence,
)
from agents_remember.memory.knowledge.facet_records import (
    RecordRevisionDraft,
    record_revision_row,
)
from agents_remember.memory.knowledge.logical import logical_body
from agents_remember.memory.knowledge.read_owner_revisions import owner_revision_resolver_for
from agents_remember.memory.knowledge.schema_generations import (
    CURRENT_GENERATION,
    GENERATION_4,
)
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.citation import (
    CitationBindingClosureRequest,
    CitationBindingPayload,
    CitationBindingRequest,
    CitationTargetReference,
    ProseCitationKey,
    ProseOwnerRevision,
    TableRowKeyForm,
    WrittenSource,
)
from agents_remember.models.knowledge.facet import AddFacet, FacetWriteRequest
from agents_remember.models.knowledge.source import FileLocator, LineRangeLocator
from generation_test_support import create_recorded_generation_store
from knowledge_fixture_test_support import build_branching_knowledge_fixture, make_authorship

pytestmark = pytest.mark.integration

# One real key, read as written from the external memory root this master binds
# (``onboarding/scripts/e2e_harness/run.py.md:43``), used verbatim so the recorded construct is the
# one the corpus writes.
CORPUS_DOCUMENT = "onboarding/scripts/e2e_harness/run.py.md"
CORPUS_KEY = "cit:([`_candidate_identity`], scripts/e2e_harness/run.py:206-218)"
CORPUS_SOURCE = WrittenSource(path="scripts/e2e_harness/run.py", start_line=206, end_line=218)

# A second real key, so one owner revision can record two distinct keys -- which "the declared bound
# was reached" needs, because the stored `UNIQUE` key refuses a second claim of the *same* key.
SECOND_KEY = "cit:([`main`], scripts/e2e_harness/run.py:88-96)"
SECOND_SOURCE = WrittenSource(path="scripts/e2e_harness/run.py", start_line=88, end_line=96)


class _MemoryRepository:
    """One real Git repository holding the prose document at a recorded revision."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self._git("init", "-q")
        self._git("config", "user.email", "l18@example.invalid")
        self._git("config", "user.name", "L18 fixture")

    def _git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(self.root), *args], capture_output=True, text=True, check=True
        )
        return completed.stdout.strip()

    def write_document(self, body: str) -> str:
        """Write the document at its recorded path, commit it, and return its blob identity."""

        document = self.root.joinpath(*CORPUS_DOCUMENT.split("/"))
        document.parent.mkdir(parents=True, exist_ok=True)
        document.write_text(body, encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-qm", "record the prose revision")
        return self._git("rev-parse", f"HEAD:{CORPUS_DOCUMENT}")


def _memory_repository(tmp_path: Path, body: str) -> tuple[_MemoryRepository, str]:
    """Build a real memory repository holding one document, and return its recorded blob identity."""

    repository = _MemoryRepository(tmp_path / "memory-repo")
    return repository, repository.write_document(body)


def _prose_key(written: str, source: WrittenSource) -> ProseCitationKey:
    return ProseCitationKey(
        written=written, anchor_texts=("_candidate_identity",), sources=(source,)
    )


def _owner_revision(blob: str) -> ProseOwnerRevision:
    return ProseOwnerRevision(
        repository="ar-agents-remember", document_path=CORPUS_DOCUMENT, blob_object_id=blob
    )


@dataclass(frozen=True)
class _BindingCase:
    """The identity facts one binding write needs, so the helper takes one value rather than seven."""

    store: OpenedKnowledgeStore
    repository_id: str
    record_id: str
    authorship: Authorship

    def author(self, *, owner, key, source, **overrides):
        """Author one binding against this case's fixture record and return the receipt."""

        payload = CitationBindingPayload(
            owner_revision=owner,
            local_key=_prose_key(key, source),
            target=CitationTargetReference(
                record_id=overrides.pop("target_record_id", self.record_id),
                kind=overrides.pop("target_kind", "terminology"),
                revision_id=overrides.pop("target_revision_id", None),
                locator=overrides.pop("locator", LineRangeLocator(start_line=5, end_line=9)),
            ),
            asserted_by_ref=overrides.pop("asserted_by_ref", "curator:l18"),
        )
        return citations.author_citation_binding(
            self.store,
            CitationBindingRequest(
                repository_id=self.repository_id,
                binding_id=overrides.pop("binding_id", str(uuid4())),
                payload=payload,
                governing_route_id=overrides.pop("governing_route_id", None),
            ),
            self.authorship,
        )


def _binding_fixture(tmp_path: Path):
    """Build the shared branching fixture plus one real authored knowledge record to bind against."""

    fixture = build_branching_knowledge_fixture(tmp_path / "knowledge")
    store = fixture.reopen()
    record_id = str(uuid4())
    authorship = make_authorship()
    created = facets.add_facet(
        store,
        FacetWriteRequest(
            repository_id=fixture.repository_id,
            provenance=authorship,
            command=AddFacet(
                record_id=record_id,
                revision_id=str(uuid4()),
                facet_kind="terminology",
                payload={
                    "facet_kind": "terminology",
                    "term": "citation binding",
                    "definition": (
                        "the recorded statement that one prose citation key denotes one knowledge "
                        "record at one locator"
                    ),
                    "scope": "the prose corpus of this memory repository",
                },
            ),
        ),
    )
    assert created.state == "applied", created.refusal
    return store, fixture, record_id, authorship


# ---------------------------------------------------------------------------
# The owner revision, against a real object store.


def test_a_binding_recorded_against_a_real_document_revision_resolves_to_it(tmp_path: Path) -> None:
    """§1.1 and Expected Evidence: a binding authored against a **real** measured corpus key.

    The owner revision is read from a real commit, so the case measures Git: the recorded blob really
    is in the object store and the closure reports the resolved state for a key that really is in the
    recorded bytes. A fixture that invented the identity would pass this case whether or not the
    resolution worked.
    """

    repository, blob = _memory_repository(tmp_path, f"# run.py\n\nprose {CORPUS_KEY} end\n")
    store, fixture, record_id, authorship = _binding_fixture(tmp_path)
    case = _BindingCase(
        store=store,
        repository_id=fixture.repository_id,
        record_id=record_id,
        authorship=authorship,
    )
    try:
        owner = _owner_revision(blob)
        written = case.author(owner=owner, key=CORPUS_KEY, source=CORPUS_SOURCE)
        assert written.state == "applied", written.refusal
        result = assemble_citation_closure(
            store,
            CitationBindingClosureRequest(
                repository_id=fixture.repository_id, selected_owner_revisions=(owner,)
            ),
            resolver=owner_revision_resolver_for(repository.root),
        )
        assert result.state == "assembled"
        assert result.counts is not None
        assert result.counts.resolved == 1
        assert result.counts.unresolved == 0
        item = result.items[0]
        assert item.observation.state == "exact_recorded_blob"
        assert item.observation.owner_revision.blob_object_id == blob
        assert item.target_kind == "terminology"
        assert item.target_lifecycle == "proposed"
        assert item.target_authority_home == "agents-remember"
        assert item.observation.governing_route_id is None
    finally:
        store.close()


def test_a_rewritten_document_leaves_the_binding_stale_and_never_re_bound(tmp_path: Path) -> None:
    """§4.4 and Example 8: stale is not unresolved, and no resolver relocates a moved range.

    The document really is rewritten and re-committed, so the revision the binding was authored
    against still exists in the object store while the document now says something else. The binding
    is reported against the revision it was authored against -- stale, readable, attributed, and not
    re-bound to the new text -- and the recorded key survives on the report.
    """

    repository, blob = _memory_repository(tmp_path, f"# run.py\n\nprose {CORPUS_KEY} end\n")
    store, fixture, record_id, authorship = _binding_fixture(tmp_path)
    case = _BindingCase(
        store=store,
        repository_id=fixture.repository_id,
        record_id=record_id,
        authorship=authorship,
    )
    try:
        owner = _owner_revision(blob)
        written = case.author(owner=owner, key=CORPUS_KEY, source=CORPUS_SOURCE)
        assert written.state == "applied", written.refusal
        # The prose is rewritten: the cited range moved exactly as Example 8 describes.
        moved = CORPUS_KEY.replace("206-218", "240-252")
        rewritten_blob = repository.write_document(f"# run.py\n\nprose {moved} end\n")
        assert rewritten_blob != blob

        resolver = owner_revision_resolver_for(repository.root)
        authored = assemble_citation_closure(
            store,
            CitationBindingClosureRequest(
                repository_id=fixture.repository_id, selected_owner_revisions=(owner,)
            ),
            resolver=resolver,
        )
        # The binding is authored against the *old* revision, so the old revision still holds its key
        # and the binding resolves. What must not happen is that it silently follows the document.
        assert authored.state == "assembled"
        assert authored.items[0].observation.owner_revision.blob_object_id == blob
        assert authored.items[0].observation.owner_revision.blob_object_id != rewritten_blob
        assert "cit:([" in authored.items[0].observation.detail
        assert "240-252" not in authored.items[0].observation.detail

        # A view whose declared set is the rewritten revision sees no binding for it, because no
        # binding was authored against it: the view cannot inherit the old binding's meaning.
        rewritten_owner = _owner_revision(rewritten_blob)
        fresh = assemble_citation_closure(
            store,
            CitationBindingClosureRequest(
                repository_id=fixture.repository_id, selected_owner_revisions=(rewritten_owner,)
            ),
            resolver=resolver,
        )
        assert fresh.state == "assembled"
        assert fresh.counts is not None
        assert fresh.counts.selected == 0
        assert fresh.counts.resolved == 0
    finally:
        store.close()


def test_a_key_absent_from_its_owner_revision_reports_the_shipped_mismatch_literal(
    tmp_path: Path,
) -> None:
    """§4.1a: the key is not recorded in its owner revision, reported under the shipped literal.

    The owner revision really exists and really is readable, so the only fact left is that the
    recorded blob does not hold the recorded key. Reporting anything else -- ``path_absent``,
    ``uncovered_key_form``, or a resolution -- would be a different fact from the one measured.
    """

    repository, blob = _memory_repository(tmp_path, "# run.py\n\nprose with no citation at all\n")
    store, fixture, record_id, authorship = _binding_fixture(tmp_path)
    case = _BindingCase(
        store=store,
        repository_id=fixture.repository_id,
        record_id=record_id,
        authorship=authorship,
    )
    try:
        owner = _owner_revision(blob)
        written = case.author(owner=owner, key=CORPUS_KEY, source=CORPUS_SOURCE)
        assert written.state == "applied", written.refusal
        result = assemble_citation_closure(
            store,
            CitationBindingClosureRequest(
                repository_id=fixture.repository_id, selected_owner_revisions=(owner,)
            ),
            resolver=owner_revision_resolver_for(repository.root),
        )
        assert result.counts is not None
        assert result.counts.by_state["recorded_blob_mismatch"] == 1
        assert result.counts.unresolved == 1
        assert result.counts.stale == 1
        assert "stale_bindings_present" in result.limitations
        item = result.items[0]
        assert item.observation.state == "recorded_blob_mismatch"
        # The key this case recorded is a prose ``cit:`` body, so its written construct is the fact
        # that survives on the failure; the other recorded key form carries an anchor cell instead.
        assert isinstance(item.observation.local_key, ProseCitationKey)
        assert item.observation.local_key.written == CORPUS_KEY
        assert item.observation.target.record_id == record_id
    finally:
        store.close()


def test_an_owner_revision_the_object_store_cannot_obtain_is_reported_as_unavailable(
    tmp_path: Path,
) -> None:
    """§Failure And Recovery Behavior: no fallback to the working tree, HEAD, a branch or Markdown.

    The document really is on disk at its recorded path and the recorded identity really is not in
    the object store, so a resolver that fell back to the file would report a resolution here. It
    reports the unavailable state instead, with the recorded identity preserved.
    """

    repository, _blob = _memory_repository(tmp_path, f"# run.py\n\nprose {CORPUS_KEY} end\n")
    store, fixture, record_id, authorship = _binding_fixture(tmp_path)
    case = _BindingCase(
        store=store,
        repository_id=fixture.repository_id,
        record_id=record_id,
        authorship=authorship,
    )
    try:
        # A well-formed identity nothing holds: the object store is real and is really asked.
        absent = "1" * 40
        owner = _owner_revision(absent)
        written = case.author(owner=owner, key=CORPUS_KEY, source=CORPUS_SOURCE)
        assert written.state == "applied", written.refusal
        result = assemble_citation_closure(
            store,
            CitationBindingClosureRequest(
                repository_id=fixture.repository_id, selected_owner_revisions=(owner,)
            ),
            resolver=owner_revision_resolver_for(repository.root),
        )
        assert result.counts is not None
        assert result.counts.by_state["recorded_object_unavailable"] == 1
        assert result.counts.unresolved == 1
        item = result.items[0]
        assert item.observation.state == "recorded_object_unavailable"
        assert item.observation.owner_revision.blob_object_id == absent
        # The document really is on disk at the recorded path, so a working-tree fallback would have
        # found it. It did not: the recorded identity is what was asked about.
        assert repository.root.joinpath(*CORPUS_DOCUMENT.split("/")).is_file()
    finally:
        store.close()


# ---------------------------------------------------------------------------
# The write boundary's refusals.


def test_a_wrong_kind_target_is_refused_at_the_write_boundary(tmp_path: Path) -> None:
    """§1.3 and Expected Evidence: a wrong-kind target is refused rather than stored.

    The target record really is stored, so the only fact left is that its recorded kind is not the
    kind the reference declares. Refusing here is what keeps "which kind is this" from having two
    answers, and it is why the read path's kind-mismatch state reports a store changed after the
    write rather than an accepted one.
    """

    _repository, blob = _memory_repository(tmp_path, f"# run.py\n\nprose {CORPUS_KEY} end\n")
    store, fixture, record_id, authorship = _binding_fixture(tmp_path)
    case = _BindingCase(
        store=store,
        repository_id=fixture.repository_id,
        record_id=record_id,
        authorship=authorship,
    )
    try:
        owner = _owner_revision(blob)
        refused = case.author(
            owner=owner, key=CORPUS_KEY, source=CORPUS_SOURCE, target_kind="decision"
        )
        assert refused.state == "refused"
        assert refused.written == ()
        assert getattr(refused.refusal, "code", None) == "invalid_reference"
        assert getattr(refused.refusal, "expected", None) == "terminology"
        assert getattr(refused.refusal, "observed", None) == "decision"
        # Nothing was written: the refusal is a fact about the store, not a rollback to remember.
        assert citations.load_binding_ids(store) == ()
    finally:
        store.close()


def test_a_target_record_the_namespace_does_not_hold_is_refused(tmp_path: Path) -> None:
    """§1.3: a target that does not exist is refused rather than substituted."""

    _repository, blob = _memory_repository(tmp_path, f"# run.py\n\nprose {CORPUS_KEY} end\n")
    store, fixture, record_id, authorship = _binding_fixture(tmp_path)
    case = _BindingCase(
        store=store,
        repository_id=fixture.repository_id,
        record_id=record_id,
        authorship=authorship,
    )
    try:
        refused = case.author(
            owner=_owner_revision(blob),
            key=CORPUS_KEY,
            source=CORPUS_SOURCE,
            target_record_id=str(uuid4()),
        )
        assert refused.state == "refused"
        assert getattr(refused.refusal, "code", None) == "missing_expected_row"
        assert citations.load_binding_ids(store) == ()
    finally:
        store.close()


def test_a_dangling_governing_route_is_refused_and_an_ungoverned_binding_is_not(
    tmp_path: Path,
) -> None:
    """§1.6: the route association is recorded, an ungoverned binding is a state, and nothing infers.

    Two directions in one case. A named route that does not exist is a dangling reference and is
    refused; ``None`` is the explicit ungoverned state and is stored and reported as ungoverned --
    never defaulted to a route that happens to exist, and never inferred from the document path or
    from the repository root.
    """

    _repository, blob = _memory_repository(tmp_path, f"# run.py\n\nprose {CORPUS_KEY} end\n")
    store, fixture, record_id, authorship = _binding_fixture(tmp_path)
    case = _BindingCase(
        store=store,
        repository_id=fixture.repository_id,
        record_id=record_id,
        authorship=authorship,
    )
    try:
        owner = _owner_revision(blob)
        dangling = case.author(
            owner=owner,
            key=CORPUS_KEY,
            source=CORPUS_SOURCE,
            governing_route_id=str(uuid4()),
        )
        assert dangling.state == "refused"
        assert getattr(dangling.refusal, "code", None) == "missing_expected_row"
        assert citations.load_binding_ids(store) == ()

        route_id = str(uuid4())
        authored = routes.author_route(
            store.connection,
            fixture.repository_id,
            routes.RouteDraft(route_id=route_id, path="mcp/src", parent_route_id=None),
            authorship,
        )
        assert authored == route_id
        governed = case.author(
            owner=owner, key=CORPUS_KEY, source=CORPUS_SOURCE, governing_route_id=route_id
        )
        assert governed.state == "applied", governed.refusal
        ungoverned = case.author(owner=owner, key=SECOND_KEY, source=SECOND_SOURCE)
        assert ungoverned.state == "applied", ungoverned.refusal
        items = enumerate_recorded_bindings(store, resolver=owner_revision_resolver_for(None))
        by_binding = {item.observation.binding_id: item for item in items}
        assert len(by_binding) == 2
        routes_recorded = {
            item.observation.governing_route_id for item in items if item.observation.state != ""
        }
        assert route_id in routes_recorded
        assert None in routes_recorded
    finally:
        store.close()


def test_two_bindings_claiming_one_key_in_one_owner_revision_are_refused_by_the_store(
    tmp_path: Path,
) -> None:
    """§4.1 and Failure And Recovery Behavior: ambiguity is reported, never resolved by picking.

    The stored ``UNIQUE`` key refuses the second claim, which is what makes the reported
    ``ambiguous_key`` state a fact about an unreviewed write rather than about an arbitrary winner:
    an operator can only create the ambiguity deliberately, and the reading side then reports it
    instead of choosing. An arbitrary winner would be a fabricated claim about what the prose meant.
    """

    _repository, blob = _memory_repository(tmp_path, f"# run.py\n\nprose {CORPUS_KEY} end\n")
    store, fixture, record_id, authorship = _binding_fixture(tmp_path)
    case = _BindingCase(
        store=store,
        repository_id=fixture.repository_id,
        record_id=record_id,
        authorship=authorship,
    )
    try:
        owner = _owner_revision(blob)
        first = case.author(owner=owner, key=CORPUS_KEY, source=CORPUS_SOURCE)
        assert first.state == "applied", first.refusal
        second = case.author(owner=owner, key=CORPUS_KEY, source=CORPUS_SOURCE)
        assert second.state == "refused"
        assert len(citations.load_binding_ids(store)) == 1
    finally:
        store.close()


# ---------------------------------------------------------------------------
# The rows land in the dataset's own committed tables -- the CR18V-1 sentence.


def test_the_binding_rows_live_in_the_datasets_own_declared_tables(tmp_path: Path) -> None:
    """``CR18V-1``: the store the binding rows live in, named by the dataset identity.

    The dataset's own logical body is the readable statement of where the rows are: the identity
    names ``citation_binding``, the rows are inside it, and the store the dataset is read from is the
    memory leg's own file -- so the binding is not enclosure-local evidence. A case that only read
    the row back through the operation would pass whether or not the table belonged to the dataset.

    RE-SCOPED for ``KS-R13@v1``, which registers generation 8 above this leaf's generation 5. The
    store this case writes into is *created* by the test, so it declares the registry's tip, and the
    identity ``store.generation is GENERATION_5`` held only while generation 5 happened to be that
    tip. The property is unchanged and is now stated as itself: the store is at ``CURRENT_GENERATION``
    and the dataset's own logical body -- read at exactly the generation the store declares -- carries
    the binding row. The generation-5 fact this leaf owns is the *append*, and it is still asserted
    below: generation 4 has no ``citation_binding`` table, so the table is generation 5's addition
    rather than a rename of something already there.
    """

    _repository, blob = _memory_repository(tmp_path, f"# run.py\n\nprose {CORPUS_KEY} end\n")
    store, fixture, record_id, authorship = _binding_fixture(tmp_path)
    case = _BindingCase(
        store=store,
        repository_id=fixture.repository_id,
        record_id=record_id,
        authorship=authorship,
    )
    try:
        owner = _owner_revision(blob)
        written = case.author(owner=owner, key=CORPUS_KEY, source=CORPUS_SOURCE)
        assert written.state == "applied", written.refusal
        assert store.generation is CURRENT_GENERATION
        body = logical_body(store.connection, CURRENT_GENERATION)
        tables = body["tables"]
        assert "citation_binding" in tables
        assert len(tables["citation_binding"]) == 1
        row = tables["citation_binding"][0]
        assert row["owner_document_path"] == CORPUS_DOCUMENT
        assert row["owner_blob_object_id"] == blob
        assert row["local_key_text"] == CORPUS_KEY
        assert row["target_record_id"] == record_id
        assert row["governing_route_id"] is None
        # The dataset's generation-4 predecessor carries no such table, so the append is additive
        # rather than a rename of something that was already there.
        assert "citation_binding" not in GENERATION_4.tables
    finally:
        store.close()


def test_a_generation_4_dataset_is_refused_the_binding_table_rather_than_widened(
    tmp_path: Path,
) -> None:
    """§1.7 and Failure And Recovery Behavior: a dataset that predates the table is refused.

    The dataset's **own** recorded generation is what is compared, so a generation-4 file is refused
    with both numbers as facts. Nothing is migrated, widened or written through -- which is the
    difference between an additive generation and a repair in place.
    """

    _repository, blob = _memory_repository(tmp_path, f"# run.py\n\nprose {CORPUS_KEY} end\n")
    repository_id = str(uuid4())
    # A genuine generation-4 dataset: the file carries that generation's own recorded DDL and its own
    # ``user_version`` rather than being a newer file with an old number.
    store = create_recorded_generation_store(
        tmp_path / "generation-4.db", repository_id, GENERATION_4
    )
    try:
        assert store.generation is GENERATION_4
        refused = _BindingCase(
            store=store,
            repository_id=repository_id,
            record_id=str(uuid4()),
            authorship=make_authorship(),
        ).author(owner=_owner_revision(blob), key=CORPUS_KEY, source=CORPUS_SOURCE)
        assert refused.state == "refused"
        assert getattr(refused.refusal, "code", None) == "unsupported_schema"
        assert getattr(refused.refusal, "observed", None) == "4"
        assert getattr(refused.refusal, "expected", None) == "5"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# The durable evidence read-back -- the clause whose first version failed.


def test_a_published_artifact_reads_back_with_its_published_digest(tmp_path: Path) -> None:
    """§3.3: the destination and the digest are recorded, and the read-back compares them."""

    task_root = tmp_path / "task-root"
    publication = publish_durable_evidence(
        task_root, "260915-KS-L18-citation-binding-evidence.md", "# evidence\n\nbody\n"
    )
    assert enclosure_reports_removed is not None
    assert publication.reference() == str(task_root / "notes" / "reports" / publication.file_name)
    assert "worktrees" not in publication.reference()
    read_back = read_back_evidence(publication)
    assert read_back.matched() is True
    assert read_back.state == "matched"
    assert read_back.observed_sha256 == publication.sha256
    assert read_back.blocked_reason() == ""


def test_the_destination_is_outside_the_enclosure_and_the_archive_by_construction(
    tmp_path: Path,
) -> None:
    """§3.3: the two unacceptable answers are unrepresentable rather than merely discouraged.

    The destination is built from the task root, so it can never be the enclosure's own ``reports/``
    directory and never widens the terminal archive's fixed content set. A file name that could leave
    the reports directory is refused, because a name that is not one segment is not a name.
    """

    assert DURABLE_EVIDENCE_REFUSED_DESTINATIONS == ("enclosure_reports", "terminal_archive")
    task_root = tmp_path / "task-root"
    publication = publish_durable_evidence(task_root, "evidence.md", "x\n")
    assert publication.destination.parent == task_root / "notes" / "reports"
    assert publication.destination.parent.name == "reports"
    for bad in ("../escape.md", "sub/dir.md", "..", "", "a\\b.md"):
        with pytest.raises(ValueError, match=r"one path segment|must be nonempty"):
            publish_durable_evidence(task_root, bad, "x\n")


def test_a_missing_durable_destination_reads_back_as_a_blocked_state(tmp_path: Path) -> None:
    """§3.3 and Example 10: a failed read-back is a blocked state, never reported as published.

    The publication really is made and the destination really is then removed -- the enclosure-cleanup
    event this leaf cannot run itself -- so the read-back measures the failure direction. The blocked
    reason carries the exact destination, the expected digest and the observed state.
    """

    task_root = tmp_path / "task-root"
    publication = publish_durable_evidence(task_root, "evidence.md", "# gone\n")
    publication.destination.unlink()
    read_back = read_back_evidence(publication)
    assert read_back.matched() is False
    assert read_back.state == "missing"
    assert read_back.observed_sha256 is None
    reason = read_back.blocked_reason()
    assert str(publication.destination) in reason
    assert publication.sha256 in reason
    assert "missing" in reason
    assert "published" in reason


def test_a_destination_whose_bytes_changed_reads_back_as_mismatched(tmp_path: Path) -> None:
    """§3.3: a mismatching read-back is its own blocked state, distinct from a missing one.

    The bytes really do change after publication, so the digest comparison is exercised in the
    direction a code-inspection claim could never see: the destination exists, and it is not what was
    published.
    """

    task_root = tmp_path / "task-root"
    publication = publish_durable_evidence(task_root, "evidence.md", "published\n")
    publication.destination.write_text("something else\n", encoding="utf-8")
    read_back = read_back_evidence(publication)
    assert read_back.state == "mismatched"
    assert read_back.observed_sha256 is not None
    assert read_back.observed_sha256 != publication.sha256
    reason = read_back.blocked_reason()
    assert publication.sha256 in reason
    assert read_back.observed_sha256 in reason


def test_an_uncovered_key_form_is_counted_and_reported_on_a_real_store(tmp_path: Path) -> None:
    """§4.1c and Example 3b: an unread form is visible as unread, never as absent.

    The row-form binding is written **directly**, and that is the point rather than a shortcut: the
    table admits the form and this increment has no writer for it, so the state is otherwise
    unreachable and "a counted state that no operation can populate today" would be a claim about the
    code rather than a measurement. The read path is what is under test here, and it must report the
    key under ``uncovered_key_form``, count it in the denominator, name it in the partial-coverage
    limitation and never fold it into "the key is absent from its owner revision".
    """

    repository, blob = _memory_repository(tmp_path, f"# run.py\n\nprose {CORPUS_KEY} end\n")
    store, fixture, record_id, authorship = _binding_fixture(tmp_path)
    case = _BindingCase(
        store=store, repository_id=fixture.repository_id, record_id=record_id, authorship=authorship
    )
    try:
        owner = _owner_revision(blob)
        written = case.author(owner=owner, key=CORPUS_KEY, source=CORPUS_SOURCE)
        assert written.state == "applied", written.refusal
        row_binding_id = str(uuid4())
        # The envelope and the sealed revision too, so the row pair is the one the write path would
        # produce for this form: a binding row whose sealed payload is missing is a *store defect*
        # the read path reports rather than skips, which the next case measures on its own.
        row_payload = CitationBindingPayload(
            owner_revision=owner,
            local_key=TableRowKeyForm(
                anchor_cell="`_candidate_identity`",
                source_cell="scripts/e2e_harness/run.py:206-218",
            ),
            target=CitationTargetReference(
                record_id=record_id, kind="terminology", locator=FileLocator()
            ),
            asserted_by_ref="curator:l18",
        )
        store.write(
            "INSERT INTO knowledge_record (repository_id, record_id, kind, authority_home, "
            "lifecycle, governing_route_id, record_schema, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fixture.repository_id,
                row_binding_id,
                "citation_binding",
                "agents-remember",
                "proposed",
                None,
                "citation-binding/v1",
                "{}",
            ),
        )
        draft = RecordRevisionDraft(
            record_id=row_binding_id,
            revision_id=row_binding_id,
            record_schema="citation-binding/v1",
            predecessor_revision_id=None,
        )
        payload = row_payload.model_dump(mode="json")
        store.write(
            "INSERT INTO record_revision (repository_id, revision_id, record_id, record_schema, "
            "payload, predecessor_revision_id, content_digest, provenance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (fixture.repository_id, *record_revision_row(draft, payload, authorship)),
        )
        store.write(
            "INSERT INTO citation_binding (repository_id, binding_id, owner_document_path, "
            "owner_blob_object_id, local_key_form, local_key_text, target_record_id, "
            "target_record_kind, target_revision_id, target_locator_kind, governing_route_id, "
            "provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fixture.repository_id,
                row_binding_id,
                CORPUS_DOCUMENT,
                blob,
                "table_row_anchor_source",
                "`_candidate_identity` scripts/e2e_harness/run.py:206-218",
                record_id,
                "terminology",
                None,
                "file",
                None,
                "{}",
            ),
        )
        result = assemble_citation_closure(
            store,
            CitationBindingClosureRequest(
                repository_id=fixture.repository_id, selected_owner_revisions=(owner,)
            ),
            resolver=owner_revision_resolver_for(repository.root),
        )
        assert result.state == "assembled"
        assert result.counts is not None
        assert result.counts.selected == 2
        assert result.counts.by_state["uncovered_key_form"] == 1
        assert result.counts.by_state["exact_recorded_blob"] == 1
        # It is counted as unresolved rather than dropped from the denominator.
        assert result.counts.unresolved == 1
        assert "partial_key_form_coverage" in result.limitations
        assert result.key_form_coverage is not None
        assert result.key_form_coverage.uncovered_counts == {"table_row_anchor_source": 1}
        by_binding = {item.observation.binding_id: item for item in result.items}
        row_item = by_binding[row_binding_id]
        assert row_item.observation.state == "uncovered_key_form"
        # The recorded key text survives on the failure, and it is not the absent-from-revision state.
        # This record's key is the table-row form, which is exactly why the prose form does not cover it.
        assert isinstance(row_item.observation.local_key, TableRowKeyForm)
        assert row_item.observation.local_key.anchor_cell == "`_candidate_identity`"
        assert row_item.observation.state != "recorded_blob_mismatch"
    finally:
        store.close()
