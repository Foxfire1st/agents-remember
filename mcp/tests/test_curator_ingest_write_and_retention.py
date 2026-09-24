"""One curator ingest run's own writes, retentions, replays and refusals.

``mcp/tests/test_curator_family_authoring.py`` owns the family plane's authored meaning: the joint
guarantee, its exact memberships, the three outcomes, successors, and retirement. These nine cases
measure the rest of the same operation, over a real pair of repositories beside a real contract,
which is what one run of it *writes*, what it *retains*, what it *replays*, and what it refuses to
claim. Eight drive ``ingest_curator_list`` directly through the sibling's own helper and the ninth
drives the curator's own command line:

* an external source is retained in a **bounded manifest** named by the record's own origin
  reference, and no source anchor is fabricated for it;
* a family identity allocated for an entry the run **refused** is never recorded, so the journal
  cannot name a family the dataset did not receive;
* a **replayed** invariant revision never skips the family plane the same run authors;
* an **exact replay** writes no row, spends no identity and leaves the journal byte-identical;
* a **changed** no-family basis on an immutable stored revision is refused by name, while an exact
  retry of the recorded basis stays a replay;
* a run that writes nothing retains **no manifest** and says so, while still reporting what the list
  declared;
* a source declaration findable again by **neither version nor retrieval time** is refused;
* a **planning** run projects the family plane and writes neither a row nor a file;
* the curator's own **command line** reaches both planes and prints them.

The authoring and read-back helpers are ``test_curator_family_authoring``'s and the list fixture is
``test_knowledge_curator_ingest_list``'s, imported rather than duplicated -- the sibling-fixture
pattern this test tree already uses for shared support.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_curator_ingest import COMMITTED
from agents_remember.cli.__main__ import main
from test_curator_family_authoring import (
    FAMILY_ALPHA,
    GUARANTEE_ALPHA,
    NO_FAMILY_BASIS,
    OTHER_SYMBOL,
    candidate_of,
    citation,
    committed_revision,
    conditions_of,
    database_of,
    declared,
    family_memberships,
    guarantees,
    ingest,
    members_of,
    membership,
    table_counts,
    with_family,
)
from test_knowledge_curator_ingest_list import (
    AUTHORIZATION,
    CODE_SYMBOL,
    GONE_PATH,
    SourcePair,
    entry,
    pair,
    symbol,
    target,
)

__all__ = ["pair"]

pytestmark = pytest.mark.evidence_unit

# The external document these cases retain. Nothing about it is a repository path, which is what
# makes "retained as a source, never as a fabricated Git anchor" measurable.
EXTERNAL_URL = "https://www.rfc-editor.org/rfc/rfc2119"
EXTERNAL_DIGEST = "b" * 64
EXTERNAL_LOCATION = "section 1, the keywords and their meanings"

# How one declared source identifies the revision that was read. ``absent`` is the case the
# manifest refuses, because a document nobody can find again is not an attributable source.
TIMING_VERSION = "version"
TIMING_RETRIEVED = "retrieved_at"
TIMING_ABSENT = "absent"


# --------------------------------------------------------------------------------------------
# The sources these cases declare, and the stored provenance read back
# --------------------------------------------------------------------------------------------


def with_sources(one: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    """One hand-off entry carrying the external sources the curator inspected."""

    one["external_sources"] = sources
    return one


def source(
    source_id: str,
    *,
    timing: str = TIMING_VERSION,
    content_digest: str | None = EXTERNAL_DIGEST,
    document: str = EXTERNAL_URL,
    location: str = EXTERNAL_LOCATION,
) -> dict[str, Any]:
    """One declared external source, with every field the manifest retains."""

    return {
        "id": source_id,
        "document": document,
        "version": "RFC 2119 (1997)" if timing == TIMING_VERSION else None,
        "retrieved_at": "2026-09-22T17:00:00+00:00" if timing == TIMING_RETRIEVED else None,
        "content_digest": content_digest,
        "location": location,
    }


def provenance_of(database: Path, revision_id: str) -> dict[str, Any]:
    """The stored provenance envelope of one invariant revision, decoded as recorded."""

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT provenance FROM invariant_revision WHERE revision_id = ?", (revision_id,)
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    return json.loads(row[0])


def test_an_external_source_is_retained_in_a_manifest_and_never_becomes_a_git_anchor(
    pair: SourcePair, tmp_path: Path
) -> None:
    """The declared source is recorded with its identity, version, digest and location, and no anchor.

    An external document is not a repository path with a Git blob, so it is retained in a bounded
    manifest that the authored record's own origin reference names, while the record's source anchors
    keep naming the code its citations actually resolved against.
    """

    candidate = candidate_of(tmp_path)
    entries = [
        with_sources(
            with_family(
                entry("E-sourced", targets=citation()),
                state="member",
                memberships=[
                    membership(FAMILY_ALPHA, declares=declared(FAMILY_ALPHA, GUARANTEE_ALPHA))
                ],
            ),
            [source("rfc-2119")],
        ),
        with_sources(entry("E-examined-none", targets=citation(OTHER_SYMBOL)), []),
        entry("E-unexamined", targets=citation(CODE_SYMBOL)),
    ]
    report = ingest(pair, candidate, entries)

    assert [one.state for one in report.committed] == [COMMITTED, COMMITTED, COMMITTED]
    assert report.sources.state == "recorded"
    assert report.sources.declared == 1
    assert report.sources.content_digests_recorded == 1
    assert report.sources.path is not None
    manifest_path = Path(report.sources.path)
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "curator-source-manifest/v1"
    assert manifest["declaredBy"] == "curator-hand-off-list"
    assert [one["entryId"] for one in manifest["entries"]] == ["E-sourced"]
    assert manifest["entries"][0]["sources"] == [
        {
            "id": "rfc-2119",
            "document": EXTERNAL_URL,
            "version": "RFC 2119 (1997)",
            "retrievedAt": None,
            "contentDigest": EXTERNAL_DIGEST,
            "location": EXTERNAL_LOCATION,
        }
    ]
    by_entry = {one.entry_id: one for one in report.sources.entries}
    assert (by_entry["E-sourced"].examined, by_entry["E-sourced"].declared) == (True, 1)
    assert (by_entry["E-examined-none"].examined, by_entry["E-examined-none"].declared) == (True, 0)
    assert by_entry["E-unexamined"].examined is False
    assert report.sources.unexamined == ("E-unexamined",)

    # The record's origin references name the hand-off list and the manifest by their own digests,
    # and the code anchor is still the only source anchor this run wrote.
    database = database_of(candidate)
    origin_refs = provenance_of(database, committed_revision(report, "E-sourced"))["origin_refs"]
    assert any(one.startswith("curator-handoff:v1:") for one in origin_refs)
    assert f"curator-source-manifest:v1:{report.sources.digest}" in origin_refs
    assert EXTERNAL_URL not in " ".join(origin_refs)
    assert table_counts(database)["source_anchor"] == 3


def test_a_declaration_whose_entry_is_refused_spends_no_identity(
    pair: SourcePair, tmp_path: Path
) -> None:
    """A family identity is allocated for the list, but recorded only for a run that writes.

    The declaration is resolved for the whole list before any entry is planned -- two entries may name
    one family and only one of them authors its guarantee -- so a declaration is allocated even when
    the entry that carries it is later refused for a target nobody can resolve. Recording that
    allocation would leave the journal naming a family the dataset never received, and a later run of
    the same key would be answered as an already-allocated identity with nothing to examine.
    """

    candidate = candidate_of(tmp_path)
    report = ingest(
        pair,
        candidate,
        [
            with_family(
                entry(
                    "E-gone", targets=[target(GONE_PATH, locator=symbol(CODE_SYMBOL), route="pkg")]
                ),
                state="member",
                memberships=[
                    membership(FAMILY_ALPHA, declares=declared(FAMILY_ALPHA, GUARANTEE_ALPHA))
                ],
            )
        ],
    )

    assert [one.entry_id for one in report.refused] == ["E-gone"]
    assert report.committed == ()
    assert report.family.state == "not-recorded"
    assert report.family.unresolved == ("E-gone",)
    assert not (candidate / "curator-family-allocation-journal.json").exists()
    assert table_counts(database_of(candidate))["family_revision"] == 0


def test_a_replayed_revision_still_writes_the_family_plane_its_entry_now_authors(
    pair: SourcePair, tmp_path: Path
) -> None:
    """The replay of one half never skips the other, and the report never claims a row it did not write.

    Step 1 stores an obligation with a deliberate no-family outcome. Step 2 re-runs the **same entry
    id** with the same statement and targets -- so its invariant revision replays -- and a *new*
    membership. The invariant and its citations are already in the dataset and must not be re-issued,
    while the family rows are this run's to write; deciding "is there anything left to write" from the
    replay set alone skipped the whole batch and left the report describing a family the store did not
    hold, with the identities already spent in the allocation journal.
    """

    candidate = candidate_of(tmp_path)
    first = ingest(
        pair,
        candidate,
        [
            with_family(
                entry("E-replayed", targets=citation()),
                state="no_family",
                basis=NO_FAMILY_BASIS,
            )
        ],
    )
    assert [one.state for one in first.committed] == [COMMITTED]
    revision = committed_revision(first, "E-replayed")
    journal = Path(first.candidate_directory) / "curator-family-allocation-journal.json"
    assert not journal.exists()

    second = ingest(
        pair,
        candidate,
        [
            with_family(
                entry("E-replayed", targets=citation()),
                state="member",
                memberships=[
                    membership(FAMILY_ALPHA, declares=declared(FAMILY_ALPHA, GUARANTEE_ALPHA))
                ],
            )
        ],
    )

    # The invariant revision replayed, and the family plane was still written by this run.
    assert [one.entry_id for one in second.refused] == []
    assert [one.entry_id for one in second.committed] == ["E-replayed"]
    assert second.batch_state == "changed"
    # Three commands: the family identity, its guarantee revision, and the membership. Nothing was
    # re-issued for the replayed half -- an invariant revision command alone would make this four.
    assert second.counts.commands_sent == 3
    database = database_of(candidate)
    counts = table_counts(database)
    assert counts["invariant_revision"] == 1
    assert counts["family"] == 1
    assert counts["family_revision"] == 1
    assert counts["family_member"] == 1
    assert second.family.state == "recorded"
    assert [one.state for one in second.family.guarantees] == ["authored"]
    assert [one.state for one in second.family.memberships] == ["added"]
    assert second.family.guarantees[0].members_recorded == 1
    # The report's own claim and the store agree, revision for revision.
    guarantee = second.family.guarantees[0]
    assert members_of(database, second.repository_id, guarantee.family_revision_id) == {revision}
    assert guarantees(database, second.repository_id) == {
        guarantee.family_revision_id: GUARANTEE_ALPHA
    }
    # The identities that were allocated are the ones the dataset received.
    recorded = json.loads(journal.read_text(encoding="utf-8"))
    assert [one["familyRevisionId"] for one in recorded] == [guarantee.family_revision_id]


def test_an_exact_replay_writes_nothing_and_spends_no_identity(
    pair: SourcePair, tmp_path: Path
) -> None:
    """Repeating the same list is a replay: no row, no second family, and no identity minted.

    The allocation journal is what a retry is answered from, so a run that writes nothing must not
    extend it: an entry that replayed and minted a family would leave the journal naming a family the
    dataset never received, and the next run of that key would be told it already holds an identity
    with nothing to examine.
    """

    candidate = candidate_of(tmp_path)
    entries = [
        with_family(
            entry("E-exact", targets=citation()),
            state="member",
            memberships=[
                membership(FAMILY_ALPHA, declares=declared(FAMILY_ALPHA, GUARANTEE_ALPHA))
            ],
        )
    ]
    first = ingest(pair, candidate, entries)
    assert [one.state for one in first.committed] == [COMMITTED]
    database = database_of(candidate)
    rows = table_counts(database)
    journal = Path(first.candidate_directory) / "curator-family-allocation-journal.json"
    recorded = journal.read_text(encoding="utf-8")

    second = ingest(pair, candidate, entries)

    assert second.batch_state == "replayed"
    assert second.counts.commands_sent == 0
    assert second.counts.records_written == 0
    assert table_counts(database) == rows
    assert journal.read_text(encoding="utf-8") == recorded
    assert [one.state for one in second.family.guarantees] == ["examined"]
    assert [one.state for one in second.family.memberships] == ["reused"]
    assert second.family.guarantees[0].members_recorded == 1
    assert second.family.guarantees[0].joint_guarantee == GUARANTEE_ALPHA


def test_a_changed_no_family_basis_on_a_stored_revision_is_refused_by_name(
    pair: SourcePair, tmp_path: Path
) -> None:
    """A changed outcome on an immutable stored revision is refused, not reported as recorded.

    A no-family outcome is retained in the revision's own conditions, and a revision is immutable. A
    replay of that revision therefore has nowhere to put a *different* basis, so the entry is refused
    by name instead of the report claiming an outcome the dataset does not hold. The exact retry of
    the recorded basis is not a contradiction and stays a replay.
    """

    candidate = candidate_of(tmp_path)
    first = ingest(
        pair,
        candidate,
        [
            with_family(
                entry("E-free", targets=citation()), state="no_family", basis=NO_FAMILY_BASIS
            )
        ],
    )
    assert [one.state for one in first.committed] == [COMMITTED]
    revision = committed_revision(first, "E-free")
    assert any(
        NO_FAMILY_BASIS in one
        for one in conditions_of(database_of(candidate), first.repository_id, revision)
    )

    # An exact retry of the recorded outcome is a replay, not a refusal.
    retry = ingest(
        pair,
        candidate,
        [
            with_family(
                entry("E-free", targets=citation()), state="no_family", basis=NO_FAMILY_BASIS
            )
        ],
    )
    assert retry.batch_state == "replayed"
    assert [one.basis for one in retry.family.no_family] == [NO_FAMILY_BASIS]

    changed = ingest(
        pair,
        candidate,
        [
            with_family(
                entry("E-free", targets=citation()),
                state="no_family",
                basis="a different basis authored after the revision was recorded",
            )
        ],
    )
    assert [one.entry_id for one in changed.refused] == ["E-free"]
    assert "family_outcome_not_writable" in changed.refused[0].refusal
    assert changed.family.state == "not-recorded"
    assert changed.family.no_family == ()
    assert changed.family.unresolved == ("E-free",)
    assert any(
        NO_FAMILY_BASIS in one
        for one in conditions_of(database_of(candidate), changed.repository_id, revision)
    )


def test_a_run_that_writes_nothing_retains_no_manifest_and_says_so(
    pair: SourcePair, tmp_path: Path
) -> None:
    """A list whose entries are all refused writes nothing -- not even the manifest it declared.

    The manifest is written so that the origin references the batch is about to stamp resolve to real
    bytes. A run that opens no batch has no such rows, so writing the file would leave an artifact
    claiming a retention that nothing refers to, and reporting a digest for it would name bytes no
    reader can find. The declaration is still reported as a fact about the *list*: what it declared is
    measured from the list, and ``state`` is what says whether it was retained.
    """

    candidate = candidate_of(tmp_path)
    report = ingest(
        pair,
        candidate,
        [
            with_sources(
                with_family(
                    entry("E-bad-family", targets=citation()),
                    state="member",
                    memberships=[membership("never-declared")],
                ),
                [source("rfc-2119")],
            )
        ],
    )

    assert [one.entry_id for one in report.refused] == ["E-bad-family"]
    assert "family_not_declared" in report.refused[0].refusal
    assert report.committed == ()
    assert report.sources.state == "not-recorded"
    assert report.sources.path is None
    assert report.sources.digest is None
    assert report.sources.declared == 1
    assert not (candidate / "curator-source-manifest.json").exists()
    assert report.family.state == "not-recorded"
    assert report.family.guarantees == ()
    assert report.family.memberships == ()
    assert report.family.unresolved == ("E-bad-family",)
    assert table_counts(database_of(candidate))["family_member"] == 0


def test_a_source_with_neither_version_nor_retrieval_time_is_refused(
    pair: SourcePair, tmp_path: Path
) -> None:
    """A declaration that cannot be found again is refused rather than recorded as attributable."""

    candidate = candidate_of(tmp_path)
    report = ingest(
        pair,
        candidate,
        [
            with_sources(
                entry("E-unversioned", targets=citation()),
                [source("loose-doc", timing=TIMING_ABSENT)],
            )
        ],
    )

    assert [one.entry_id for one in report.refused] == ["E-unversioned"]
    assert "external_source_unversioned" in report.refused[0].refusal
    assert report.sources.state == "not-recorded"
    assert not (candidate / "curator-source-manifest.json").exists()


def test_a_planning_run_writes_no_family_row_and_says_so(pair: SourcePair, tmp_path: Path) -> None:
    """The dry run projects the family plane and writes nothing, with no measured count claimed."""

    candidate = candidate_of(tmp_path)
    report = ingest(
        pair,
        candidate,
        [
            with_family(
                entry("E-alpha", targets=citation()),
                state="member",
                memberships=[
                    membership(FAMILY_ALPHA, declares=declared(FAMILY_ALPHA, GUARANTEE_ALPHA))
                ],
            )
        ],
        dry_run=True,
    )

    assert report.dry_run is True
    assert report.family.state == "projected"
    assert report.family.guarantees[0].state == "projected"
    assert report.family.guarantees[0].members_recorded is None
    assert report.family.guarantees[0].unchanged_sibling_members is None
    assert report.sources.state == "not-recorded"
    assert not database_of(candidate).exists()


def test_the_curator_command_line_authors_the_family_plane_and_reports_it(
    pair: SourcePair, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The curator's own invocation reaches the plane, and its JSON report carries both planes.

    Every case above drives the operation in process, which proves the operation works and proves
    nothing about whether the curator can reach it: ``roles/curator.md`` and
    ``operations/curation.md`` both hand the curator ``agents-remember knowledge-ingest``, so this
    case runs that command through the umbrella ``main`` with a hand-off list on disk and reads the
    printed report's own ``family`` and ``sources`` blocks.
    """

    list_path = tmp_path / "hand-off-list.json"
    list_path.write_text(
        json.dumps(
            [
                with_sources(
                    with_family(
                        entry("E-cli", targets=citation()),
                        state="member",
                        memberships=[
                            membership(
                                FAMILY_ALPHA, declares=declared(FAMILY_ALPHA, GUARANTEE_ALPHA)
                            )
                        ],
                    ),
                    [source("rfc-2119")],
                ),
                with_family(
                    entry("E-cli-free", targets=citation(OTHER_SYMBOL)),
                    state="no_family",
                    basis=NO_FAMILY_BASIS,
                ),
            ]
        ),
        encoding="utf-8",
    )
    candidate = tmp_path / "cli-candidate"
    argv = [
        "knowledge-ingest",
        "--contract",
        str(pair.contract_path),
        "--list",
        str(list_path),
        "--candidate-directory",
        str(candidate),
        "--authorization-ref",
        AUTHORIZATION,
        "--commit",
        "--json",
    ]

    capsys.readouterr()
    assert main(argv) == 0
    printed = json.loads(capsys.readouterr().out)

    assert printed["family"]["state"] == "recorded"
    assert [one["familyKey"] for one in printed["family"]["guarantees"]] == [FAMILY_ALPHA]
    assert printed["family"]["guarantees"][0]["jointGuarantee"] == GUARANTEE_ALPHA
    assert printed["family"]["guarantees"][0]["state"] == "authored"
    assert [one["state"] for one in printed["family"]["memberships"]] == ["added"]
    assert [one["basis"] for one in printed["family"]["noFamily"]] == [NO_FAMILY_BASIS]
    assert printed["family"]["unexamined"] == []
    assert printed["sources"]["state"] == "recorded"
    assert printed["sources"]["declared"] == 1
    assert printed["sources"]["contentDigestsRecorded"] == 1
    assert printed["sources"]["originRefs"] == [
        f"curator-handoff:v1:{printed['sources']['originRefs'][0].split(':')[-1]}",
        f"curator-source-manifest:v1:{printed['sources']['manifestDigest']}",
    ]

    database = database_of(candidate)
    stored = family_memberships(database)
    assert len(stored) == 1
    recorded = guarantees(database, printed["repositoryId"])
    assert recorded[stored[0][1]] == GUARANTEE_ALPHA
