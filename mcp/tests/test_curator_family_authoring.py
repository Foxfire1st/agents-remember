"""The curator's family plane and external-source manifest, driven through the real ingest.

ICR-R28@v2 asks the curator to author, as part of a grounded foundation, *justified independent
family guarantees and exact memberships*, with multiple and no-family outcomes deliberate and never
forced, and to retain external sources as attributable origin references rather than as fabricated Git
anchors. Nothing here re-protects a citation round trip: the write half has its own cases
(``test_knowledge_curator_ingest.py``) and the list-driven operation has five more
(``test_knowledge_curator_ingest_list.py``) which this module imports as its fixture rather than
introducing a second one.

What this module adds is what those cannot reach, and each case measures one of them through the real
operation -- ``ingest_curator_list`` over a real pair of repositories beside a real contract, with the
public read route reading the stored result back:

* a family the curator declared is stored as **its own** joint guarantee with exact memberships, and
  its text is not any member's statement;
* one invariant revision can belong to **two** families, and one family to several revisions;
* a **deliberate no-family** outcome is retained with its basis in the revision's recorded conditions,
  while an entry the curator never examined is reported as unexamined and not as family-free;
* membership in a family is never **inferred** from a file, a route or a label, which is the packet's
  non-conforming import;
* a changed guarantee is a **successor** revision that preserves the earlier membership;
* a stored family revision is **examined** -- its recorded text is read back and reported -- and a
  membership can join it without re-declaring it;
* an external source is retained in a **bounded manifest** named by the record's own origin reference,
  and no source anchor is fabricated for it;
* a membership can be **retired** by the identity of the row this run read.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from agents_remember.application.knowledge_curator_ingest import (
    COMMITTED,
    IngestReport,
    IngestSelection,
    ingest_curator_list,
)
from agents_remember.application.knowledge_read import open_read_context
from agents_remember.application.knowledge_views import read_knowledge_view
from agents_remember.cli.__main__ import main
from agents_remember.models.knowledge.snapshot import candidate_database_path
from agents_remember.models.knowledge.view import FamilyView, InvariantView, ViewRequest
from test_knowledge_curator_ingest_list import (
    AUTHORIZATION,
    CODE_FILE,
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

# The second construct in the same file the fixture's other cases cite, so "one file is not one
# family" is measured against two real places in one real file rather than a synthetic pair.
OTHER_SYMBOL = "other"
# The two families the shared-membership case places one revision in. They are declared by two
# different entries in one list, which is the only way a family exists here.
FAMILY_ALPHA = "alpha-joint-obligation"
FAMILY_BETA = "beta-joint-obligation"
GUARANTEE_ALPHA = (
    "Every accepted source citation resolves to the exact bytes the recorded tree holds, so an "
    "anchor and the claim citing it agree at one identity."
)
# The successor's own key and text. A family key names ONE declaration operation, exactly as an
# entry id names one creation operation, so a re-authored guarantee is declared under a new key that
# names the stored family identity and the revision it supersedes.
FAMILY_ALPHA_V2 = "alpha-joint-obligation-v2"
GUARANTEE_ALPHA_V2 = GUARANTEE_ALPHA + " A second condition joins it."
GUARANTEE_BETA = (
    "Nothing an operation reports about a run is authored by the run itself: the receipt names what "
    "was measured."
)
NO_FAMILY_BASIS = (
    "the obligation stands alone and shares no joint guarantee with any other revision"
)
EXTERNAL_URL = "https://www.rfc-editor.org/rfc/rfc2119"
EXTERNAL_DIGEST = "b" * 64
EXTERNAL_LOCATION = "section 1, the keywords and their meanings"


# --------------------------------------------------------------------------------------------
# The lists these cases hand over, and the ways they read the result back
# --------------------------------------------------------------------------------------------


def declared(
    key: str,
    guarantee: str,
    *,
    version: str = "v1",
    family_id: str | None = None,
    predecessors: list[str] | None = None,
) -> dict[str, Any]:
    """One declaration of a family's own joint guarantee, as the curator authors it."""

    declaration: dict[str, Any] = {
        "label": key,
        "version": version,
        "guarantee": guarantee,
        "predecessor_revision_ids": predecessors or [],
    }
    if family_id is not None:
        declaration["family_id"] = family_id
    return declaration


def membership(
    key: str,
    *,
    basis: str = "the same joint obligation covers both revisions",
    declares: dict[str, Any] | None = None,
    family_revision_id: str | None = None,
) -> dict[str, Any]:
    """One membership, joining a key this list declares or a revision the dataset holds."""

    authored: dict[str, Any] = {"family": key, "basis": basis}
    if declares is not None:
        authored["declares"] = declares
    if family_revision_id is not None:
        authored["family_revision_id"] = family_revision_id
    return authored


def with_family(one: dict[str, Any], **family: Any) -> dict[str, Any]:
    """One hand-off entry carrying a curator-authored family decision."""

    one["family"] = family
    return one


def with_sources(one: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    """One hand-off entry carrying the external sources the curator inspected."""

    one["external_sources"] = sources
    return one


# How one declared source identifies the revision that was read. ``absent`` is the case the
# manifest refuses, because a document nobody can find again is not an attributable source.
TIMING_VERSION = "version"
TIMING_RETRIEVED = "retrieved_at"
TIMING_ABSENT = "absent"


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


def citation(symbol_name: str = CODE_SYMBOL) -> list[dict[str, Any]]:
    """One resolved code place, which is the citation every family case needs."""

    return [target(CODE_FILE, locator=symbol(symbol_name), route="pkg")]


def ingest(
    pair: SourcePair, candidate: Path, entries: list[dict[str, Any]], *, dry_run: bool = False
) -> IngestReport:
    """Run one hand-off list through the real operation, into a candidate this case owns."""

    return ingest_curator_list(
        pair.contract_path,
        entries,
        IngestSelection(
            candidate_directory=candidate,
            authorization_ref=AUTHORIZATION,
            dry_run=dry_run,
        ),
    )


def candidate_of(tmp_path: Path, name: str = "candidate") -> Path:
    """One candidate directory, named so a case can run the same list twice into one candidate."""

    return tmp_path / name


def database_of(candidate: Path) -> Path:
    """The candidate's own dataset, as the shipped model names it."""

    return candidate_database_path(candidate)


def family_view(database: Path, repository_id: str) -> FamilyView:
    """The family view's payload, read through the public read route over the dataset itself."""

    context = open_read_context(database, repository_id)
    result = read_knowledge_view(
        database,
        context,
        ViewRequest(view="family", repository_id=repository_id),
    )
    assert result.state == "view", result
    assert isinstance(result.payload, FamilyView), result
    return result.payload


def invariant_view(database: Path, repository_id: str, revision_id: str) -> InvariantView:
    """The invariant view's payload for one exact revision, through the same public route."""

    context = open_read_context(database, repository_id)
    result = read_knowledge_view(
        database,
        context,
        ViewRequest(
            view="invariant", repository_id=repository_id, invariant_revision_id=revision_id
        ),
    )
    assert result.state == "view", result
    assert isinstance(result.payload, InvariantView), result
    return result.payload


def guarantees(database: Path, repository_id: str) -> dict[str, str]:
    """Each family revision the family view reports, keyed by the guarantee it records."""

    return {
        row.subject.revision_id: row.statement or ""
        for row in family_view(database, repository_id).rows
        if row.fact_kind == "joint_guarantee" and row.subject.revision_id
    }


def members_of(database: Path, repository_id: str, family_revision_id: str) -> set[str]:
    """The exact invariant revisions the family view reports as one family revision's members."""

    return {
        row.statement or ""
        for row in family_view(database, repository_id).rows
        if row.fact_kind == "member"
        and row.subject.record_kind == "family_member"
        and row.subject.revision_id == family_revision_id
    }


def conditions_of(
    database: Path, repository_id: str, invariant_revision_id: str
) -> tuple[str, ...]:
    """The recorded conditions of one invariant revision, read through the invariant view."""

    return tuple(
        condition
        for row in invariant_view(database, repository_id, invariant_revision_id).rows
        if row.fact_kind == "statement"
        for condition in row.essential_conditions
    )


def statements_of(database: Path, repository_id: str, invariant_revision_id: str) -> str:
    """The statement one invariant revision records, read through the invariant view."""

    rows = invariant_view(database, repository_id, invariant_revision_id).rows
    return next(row.statement or "" for row in rows if row.fact_kind == "statement")


def table_counts(database: Path) -> dict[str, int]:
    """The family tables' row counts, read from the dataset itself."""

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        return {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in (
                "invariant",
                "invariant_revision",
                "family",
                "family_revision",
                "family_member",
                "source_anchor",
            )
        }
    finally:
        connection.close()


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


def family_memberships(database: Path) -> list[tuple[str, str, str]]:
    """Every stored membership as ``(member_id, family_revision_id, invariant_revision_id)``."""

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        return [
            (str(row[0]), str(row[1]), str(row[2]))
            for row in connection.execute(
                "SELECT member_id, family_revision_id, invariant_revision_id FROM family_member "
                "ORDER BY member_id"
            )
        ]
    finally:
        connection.close()


def committed_revision(report: IngestReport, entry_id: str) -> str:
    """The invariant revision one committed entry was allocated, from the operation's own journal."""

    journal = json.loads(
        (Path(report.candidate_directory) / "curator-allocation-journal.json").read_text(
            encoding="utf-8"
        )
    )
    for record in journal:
        if record["retryKey"].endswith(f"|{entry_id}"):
            return str(record["revisionId"])
    raise AssertionError(f"no allocation for {entry_id}: {journal}")


def committed_invariant(report: IngestReport, entry_id: str) -> str:
    """The invariant identity one committed entry was allocated."""

    journal = json.loads(
        (Path(report.candidate_directory) / "curator-allocation-journal.json").read_text(
            encoding="utf-8"
        )
    )
    for record in journal:
        if record["retryKey"].endswith(f"|{entry_id}"):
            return str(record["invariantId"])
    raise AssertionError(f"no allocation for {entry_id}: {journal}")


# --------------------------------------------------------------------------------------------
# The family guarantee, its exact memberships, and the inference it never makes
# --------------------------------------------------------------------------------------------


def test_a_declared_family_is_stored_as_its_own_guarantee_with_exact_memberships(
    pair: SourcePair, tmp_path: Path
) -> None:
    """One authored joint guarantee, two exact members, and a text that is nobody's statement.

    The guarantee is the family's own authored claim: it is not the first member's statement, not the
    second's, and not their concatenation. A member keeps its own obligation, and the two are
    separate claims a reader derives from neither.
    """

    candidate = candidate_of(tmp_path)
    entries = [
        with_family(
            entry("E-alpha", targets=citation()),
            state="member",
            memberships=[
                membership(FAMILY_ALPHA, declares=declared(FAMILY_ALPHA, GUARANTEE_ALPHA))
            ],
        ),
        with_family(
            entry("E-beta", targets=citation(OTHER_SYMBOL)),
            state="member",
            memberships=[membership(FAMILY_ALPHA)],
        ),
    ]
    report = ingest(pair, candidate, entries)

    assert [one.state for one in report.committed] == [COMMITTED, COMMITTED]
    assert report.family.state == "recorded"
    assert [one.family_key for one in report.family.guarantees] == [FAMILY_ALPHA]
    guarantee = report.family.guarantees[0]
    assert guarantee.state == "authored"
    assert guarantee.joint_guarantee == GUARANTEE_ALPHA
    assert guarantee.members_recorded == 2
    assert guarantee.unchanged_sibling_members == 0
    assert [one.state for one in report.family.memberships] == ["added", "added"]
    assert report.family.no_family == ()
    assert report.family.unexamined == ()

    database = database_of(candidate)
    revision_alpha = committed_revision(report, "E-alpha")
    revision_beta = committed_revision(report, "E-beta")
    recorded = guarantees(database, report.repository_id)
    assert recorded == {guarantee.family_revision_id: GUARANTEE_ALPHA}
    assert members_of(database, report.repository_id, guarantee.family_revision_id) == {
        revision_alpha,
        revision_beta,
    }

    # The guarantee is the family's text and not a member's: neither statement appears in it, and the
    # two member statements are read back as their own obligations.
    first = statements_of(database, report.repository_id, revision_alpha)
    second = statements_of(database, report.repository_id, revision_beta)
    assert first != second
    assert first not in GUARANTEE_ALPHA and second not in GUARANTEE_ALPHA
    assert first + second not in GUARANTEE_ALPHA


def test_one_file_and_one_route_are_not_a_family(pair: SourcePair, tmp_path: Path) -> None:
    """Two obligations in one file with no authored joint obligation stay two family-free entries.

    This is the packet's non-conforming import made measurable: a directory listing, a shared path or
    a shared route is not evidence of a joint obligation, and an entry the curator did not examine is
    reported as unexamined rather than resolved into a family by inference.
    """

    candidate = candidate_of(tmp_path)
    report = ingest(
        pair,
        candidate,
        [
            entry("E-one", targets=citation()),
            entry("E-two", targets=citation(OTHER_SYMBOL)),
        ],
    )

    assert [one.state for one in report.committed] == [COMMITTED, COMMITTED]
    assert report.family.state == "recorded"
    assert report.family.guarantees == ()
    assert report.family.memberships == ()
    assert report.family.no_family == ()
    assert report.family.unexamined == ("E-one", "E-two")
    counts = table_counts(database_of(candidate))
    assert counts["family"] == 0
    assert counts["family_revision"] == 0
    assert counts["family_member"] == 0


def test_one_revision_can_belong_to_two_families(pair: SourcePair, tmp_path: Path) -> None:
    """Shared membership: one exact revision is placed in two families, and both record the edge."""

    candidate = candidate_of(tmp_path)
    entries = [
        with_family(
            entry("E-alpha", targets=citation()),
            state="member",
            memberships=[
                membership(FAMILY_ALPHA, declares=declared(FAMILY_ALPHA, GUARANTEE_ALPHA)),
                membership(FAMILY_BETA),
            ],
        ),
        with_family(
            entry("E-beta", targets=citation(OTHER_SYMBOL)),
            state="member",
            memberships=[membership(FAMILY_BETA, declares=declared(FAMILY_BETA, GUARANTEE_BETA))],
        ),
    ]
    report = ingest(pair, candidate, entries)

    assert [one.state for one in report.committed] == [COMMITTED, COMMITTED], (
        report.refused,
        report.committed,
    )
    by_key = {one.family_key: one for one in report.family.guarantees}
    assert set(by_key) == {FAMILY_ALPHA, FAMILY_BETA}
    assert [one.family_key for one in report.family.memberships] == [
        FAMILY_ALPHA,
        FAMILY_BETA,
        FAMILY_BETA,
    ]

    database = database_of(candidate)
    revision = committed_revision(report, "E-alpha")
    assert revision in members_of(
        database, report.repository_id, by_key[FAMILY_ALPHA].family_revision_id
    )
    assert revision in members_of(
        database, report.repository_id, by_key[FAMILY_BETA].family_revision_id
    )
    stored = family_memberships(database)
    assert sorted(one[1] for one in stored if one[2] == revision) == sorted(
        [by_key[FAMILY_ALPHA].family_revision_id, by_key[FAMILY_BETA].family_revision_id]
    )


# --------------------------------------------------------------------------------------------
# The three outcomes: membership, deliberate no-family, and unexamined
# --------------------------------------------------------------------------------------------


def test_a_deliberate_no_family_outcome_is_retained_while_an_unexamined_entry_is_not(
    pair: SourcePair, tmp_path: Path
) -> None:
    """Three entries, three different outcomes, and the basis is readable in the dataset itself.

    A deliberate no-family outcome has no row of its own to live in, so it is retained in the
    revision's recorded conditions -- which is what lets a reader of the dataset, and not only a
    reader of the report, tell it apart from an entry the curator never examined.
    """

    candidate = candidate_of(tmp_path)
    entries = [
        with_family(entry("E-free", targets=citation()), state="no_family", basis=NO_FAMILY_BASIS),
        entry("E-unexamined", targets=citation(OTHER_SYMBOL)),
        with_family(
            entry("E-member", targets=citation(CODE_SYMBOL)),
            state="member",
            memberships=[
                membership(FAMILY_ALPHA, declares=declared(FAMILY_ALPHA, GUARANTEE_ALPHA))
            ],
        ),
    ]
    report = ingest(pair, candidate, entries)

    assert [one.state for one in report.committed] == [COMMITTED, COMMITTED, COMMITTED]
    assert [one.entry_id for one in report.family.no_family] == ["E-free"]
    assert report.family.no_family[0].basis == NO_FAMILY_BASIS
    assert report.family.unexamined == ("E-unexamined",)
    assert report.family.no_family[0].invariant_revision_id == committed_revision(report, "E-free")

    database = database_of(candidate)
    free = committed_revision(report, "E-free")
    recorded = conditions_of(database, report.repository_id, free)
    assert any(NO_FAMILY_BASIS in one for one in recorded), recorded
    assert any(
        one.startswith("Family examination: no joint obligation is supported") for one in recorded
    )
    stored = family_memberships(database)
    assert [one[2] for one in stored] == [committed_revision(report, "E-member")]
    assert free not in {one[2] for one in stored}
    assert committed_revision(report, "E-unexamined") not in {one[2] for one in stored}


def test_a_blank_basis_is_refused_instead_of_stored_as_an_unexplained_claim(
    pair: SourcePair, tmp_path: Path
) -> None:
    """Both authored family decisions require their justification, and a blank one is refused."""

    candidate = candidate_of(tmp_path)
    report = ingest(
        pair,
        candidate,
        [
            with_family(entry("E-free", targets=citation()), state="no_family", basis="   "),
            with_family(
                entry("E-member", targets=citation(OTHER_SYMBOL)),
                state="member",
                memberships=[
                    membership(
                        FAMILY_ALPHA, basis="", declares=declared(FAMILY_ALPHA, GUARANTEE_ALPHA)
                    )
                ],
            ),
        ],
    )

    assert [one.entry_id for one in report.refused] == ["E-free", "E-member"]
    assert all("family_basis_missing" in one.refusal for one in report.refused)
    assert report.family.state == "not-recorded"
    assert table_counts(database_of(candidate))["family_revision"] == 0


def test_a_membership_that_joins_no_declared_family_is_refused_by_name(
    pair: SourcePair, tmp_path: Path
) -> None:
    """A membership is authored against an exact family revision, so an undeclared key is refused."""

    candidate = candidate_of(tmp_path)
    report = ingest(
        pair,
        candidate,
        [
            with_family(
                entry("E-orphan", targets=citation()),
                state="member",
                memberships=[membership("never-declared")],
            ),
            with_family(
                entry("E-real", targets=citation(OTHER_SYMBOL)),
                state="member",
                memberships=[
                    membership(FAMILY_ALPHA, declares=declared(FAMILY_ALPHA, GUARANTEE_ALPHA))
                ],
            ),
        ],
    )

    assert [one.entry_id for one in report.refused] == ["E-orphan"]
    assert "family_not_declared" in report.refused[0].refusal
    assert [one.entry_id for one in report.committed] == ["E-real"]
    assert report.family.unresolved == ("E-orphan",)
    # The other entry still committed, and only its own membership exists.
    assert len(family_memberships(database_of(candidate))) == 1


# --------------------------------------------------------------------------------------------
# Identity reuse, revision successors, and retirement
# --------------------------------------------------------------------------------------------


def test_a_stored_family_revision_is_examined_and_joined_without_being_re_declared(
    pair: SourcePair, tmp_path: Path
) -> None:
    """A second run joins the revision the first one authored, and reports the text it read back.

    Re-declaring a stored revision is refused outright by the batch's own insert-absence
    precondition, so the reuse half has to be expressible: the membership names the stored revision,
    the guarantee is *examined* rather than authored, and the text the report carries is the recorded
    one.
    """

    candidate = candidate_of(tmp_path)
    first = ingest(
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
    )
    stored_revision = first.family.guarantees[0].family_revision_id

    second = ingest(
        pair,
        candidate,
        [
            with_family(
                entry("E-gamma", targets=citation(OTHER_SYMBOL)),
                state="member",
                memberships=[
                    membership(FAMILY_ALPHA, family_revision_id=stored_revision),
                ],
            )
        ],
    )

    assert [one.state for one in second.committed] == [COMMITTED]
    assert [one.state for one in second.family.guarantees] == ["examined"]
    assert second.family.guarantees[0].joint_guarantee == GUARANTEE_ALPHA
    assert second.family.guarantees[0].members_recorded == 2
    assert second.family.guarantees[0].unchanged_sibling_members == 1
    database = database_of(candidate)
    assert table_counts(database)["family_revision"] == 1
    assert members_of(database, second.repository_id, stored_revision) == {
        committed_revision(first, "E-alpha"),
        committed_revision(second, "E-gamma"),
    }


def test_a_changed_guarantee_is_a_successor_that_preserves_the_earlier_membership(
    pair: SourcePair, tmp_path: Path
) -> None:
    """A revised joint guarantee is a new immutable revision; the earlier one keeps its members.

    A member's own revision changes, and the family's meaning is re-authored separately: the successor
    names the revision it replaces, cites its own members, and leaves the earlier revision and its
    membership exactly as they were recorded.
    """

    candidate = candidate_of(tmp_path)
    first = ingest(
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
    )
    first_guarantee = first.family.guarantees[0]
    first_revision = committed_revision(first, "E-alpha")

    successor_statement = "The obligation E-alpha records, restated for a second condition."
    second = ingest(
        pair,
        candidate,
        [
            {
                **with_family(
                    entry("E-alpha-successor", targets=citation()),
                    state="member",
                    memberships=[
                        membership(
                            FAMILY_ALPHA_V2,
                            declares=declared(
                                FAMILY_ALPHA_V2,
                                GUARANTEE_ALPHA_V2,
                                version="v2",
                                family_id=first_guarantee.family_id,
                                predecessors=[first_guarantee.family_revision_id],
                            ),
                        )
                    ],
                ),
                "statement": successor_statement,
                "invariant_id": committed_invariant(first, "E-alpha"),
                "predecessor_revision_ids": [first_revision],
            }
        ],
    )

    assert [one.state for one in second.committed] == [COMMITTED], (
        [one.refusal for one in second.refused],
        second.batch_state,
    )
    assert [one.state for one in second.family.guarantees] == ["authored"]
    assert second.family.guarantees[0].predecessors == (first_guarantee.family_revision_id,)
    database = database_of(candidate)
    assert table_counts(database)["family_revision"] == 2
    successor_revision = second.family.guarantees[0].family_revision_id
    assert members_of(database, second.repository_id, first_guarantee.family_revision_id) == {
        first_revision
    }
    assert members_of(database, second.repository_id, successor_revision) == {
        committed_revision(second, "E-alpha-successor")
    }
    recorded = guarantees(database, second.repository_id)
    assert recorded[first_guarantee.family_revision_id] == GUARANTEE_ALPHA
    assert recorded[successor_revision].endswith("A second condition joins it.")


def test_a_changed_guarantee_under_one_key_is_refused_rather_than_rewritten(
    pair: SourcePair, tmp_path: Path
) -> None:
    """One key names one creation operation, so a changed guarantee cannot arrive under it.

    The alternative is the failure this guard exists for: a second run quietly writing a different
    joint guarantee where the first one stands, which would leave the memberships already citing the
    first revision attached to a meaning nobody authored.
    """

    candidate = candidate_of(tmp_path)
    first = ingest(
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
    )
    assert [one.state for one in first.committed] == [COMMITTED]

    second = ingest(
        pair,
        candidate,
        [
            with_family(
                entry("E-alpha-again", targets=citation(OTHER_SYMBOL)),
                state="member",
                memberships=[
                    membership(
                        FAMILY_ALPHA,
                        declares=declared(FAMILY_ALPHA, GUARANTEE_ALPHA + " And more."),
                    )
                ],
            )
        ],
    )

    assert [one.entry_id for one in second.refused] == ["E-alpha-again"]
    assert "family_allocation_conflict" in second.refused[0].refusal
    assert "new key" in second.refused[0].refusal
    database = database_of(candidate)
    assert table_counts(database)["family_revision"] == 1
    assert guarantees(database, second.repository_id) == {
        first.family.guarantees[0].family_revision_id: GUARANTEE_ALPHA
    }


def test_a_stored_membership_can_be_retired_by_the_identity_this_run_read(
    pair: SourcePair, tmp_path: Path
) -> None:
    """A membership leaves a family by naming the row the run read, and the report says so."""

    candidate = candidate_of(tmp_path)
    first = ingest(
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
    )
    member_id = family_memberships(database_of(candidate))[0][0]

    second = ingest(
        pair,
        candidate,
        [
            {
                **with_family(
                    entry("E-alpha-successor", targets=citation()),
                    state="member",
                    retire=[member_id],
                ),
                "statement": "The obligation E-alpha records, after leaving its family.",
                "invariant_id": committed_invariant(first, "E-alpha"),
                "predecessor_revision_ids": [committed_revision(first, "E-alpha")],
            }
        ],
    )

    assert [one.state for one in second.committed] == [COMMITTED]
    assert [one.state for one in second.family.memberships] == ["retired"]
    assert second.family.memberships[0].member_id == member_id
    database = database_of(candidate)
    assert family_memberships(database) == []
    assert second.family.guarantees[0].members_recorded == 0


def test_a_retirement_that_names_no_stored_membership_is_refused(
    pair: SourcePair, tmp_path: Path
) -> None:
    """A removal names a row this run read, so an invented membership identity is refused."""

    candidate = candidate_of(tmp_path)
    report = ingest(
        pair,
        candidate,
        [
            with_family(
                entry("E-alpha", targets=citation()),
                state="member",
                retire=[str(uuid4())],
            )
        ],
    )

    assert [one.entry_id for one in report.refused] == ["E-alpha"]
    assert "membership_not_stored" in report.refused[0].refusal
    assert report.family.state == "not-recorded"


# --------------------------------------------------------------------------------------------
# External sources: a bounded manifest, bound by the record's own origin references
# --------------------------------------------------------------------------------------------


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
