"""The curator's family plane, driven through the real ingest.

ICR-R28@v2 asks the curator to author, as part of a grounded foundation, *justified independent
family guarantees and exact memberships*, with multiple and no-family outcomes deliberate and never
forced. Nothing here re-protects a citation round trip: the write half has its own cases
(``test_knowledge_curator_ingest.py``) and the list-driven operation has its own
(``test_knowledge_curator_ingest_list.py``), which this module imports as its fixture rather than
introducing a second one.

What this module adds is what those cannot reach, and each case measures one of them through the real
operation -- ``ingest_curator_list`` over a real pair of repositories beside a real contract, with the
public read route reading the stored result back:

* a family the curator declared is stored as **its own** joint guarantee with exact memberships, and
  its text is not any member's statement;
* one invariant revision can belong to **two** families, and one family to several revisions;
* membership in a family is never **inferred** from a file, a route or a label, which is the packet's
  non-conforming import;
* a **deliberate no-family** outcome is retained with its basis in the revision's recorded conditions,
  while an entry the curator never examined is reported as unexamined and not as family-free;
* an authored family decision carrying **no basis** is refused instead of stored as an unexplained
  claim, and a membership naming a family **no entry declared** is refused by name;
* a stored family revision is **examined** -- its recorded text is read back and reported -- and a
  membership can join it without re-declaring it;
* a changed guarantee is a **successor** revision that preserves the earlier membership, while a
  changed guarantee under the **same key** is refused rather than rewritten;
* a membership can be **retired** by the identity of the row this run read, and an invented
  membership identity is refused.

The rest of the same operation -- the external-source manifest, the identity a run spends or does not
spend, the replays, the dry run and the command line -- is
``test_curator_ingest_write_and_retention``'s own subject.
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
from agents_remember.models.knowledge.snapshot import candidate_database_path
from agents_remember.models.knowledge.view import FamilyView, InvariantView, ViewRequest
from test_knowledge_curator_ingest_list import (
    AUTHORIZATION,
    CODE_FILE,
    CODE_SYMBOL,
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
