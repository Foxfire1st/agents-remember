"""Public family successors retain exact stored siblings without manufacturing invariant revisions."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

import pytest
from agents_remember.cli.__main__ import main
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.worktrees.worktree_contract import load_contract, write_contract
from test_curator_family_authoring import declared, family_view, members_of, membership, with_family
from test_knowledge_bootstrap import CODE_SYMBOL as BOOTSTRAP_SYMBOL
from test_knowledge_bootstrap import argv as bootstrap_argv
from test_knowledge_bootstrap import entry as bootstrap_entry
from test_knowledge_bootstrap import hand_off, world
from test_knowledge_curator_ingest_list import (
    AUTHORIZATION,
    CODE_FILE,
    CODE_SYMBOL,
    entry,
    symbol,
    target,
)
from test_knowledge_ingest_publication_route import OrdinaryEnclosure, _ordinary_enclosure

pytestmark = pytest.mark.evidence_unit

GUARANTEE = (
    "Recorded obligations keep their exact meaning while family support is explicitly extended."
)
PROTECTED_TABLES = (
    "invariant",
    "invariant_revision",
    "family",
    "family_revision",
    "family_member",
    "source_anchor",
    "realization_claim",
)


@dataclass(frozen=True)
class Journey:
    pair: OrdinaryEnclosure
    root: Path
    published: Path
    baseline: dict


def _record(key: str, family_key: str, declaration: dict | None = None) -> dict:
    return with_family(
        entry(key, targets=[target(CODE_FILE, locator=symbol(CODE_SYMBOL))]),
        state="member",
        memberships=[membership(family_key, declares=declaration)],
    )


def _invoke(argv: list[str], capsys) -> dict:
    capsys.readouterr()
    assert main(argv) == 0, capsys.readouterr().out
    return json.loads(capsys.readouterr().out)


def _author(
    journey: Journey, operation: str, entries: list[dict], capsys, *, commit: bool = True
) -> dict:
    contract = journey.root / f"{operation}-contract.md"
    original = load_contract(journey.pair.contract_path)
    write_contract(
        contract,
        replace(
            original,
            contract_path=contract,
            leaf_id=f"{original.leaf_id}-{operation}",
            worktree_group=journey.root / f"work-{operation}",
        ),
    )
    listed = journey.root / f"{operation}.json"
    listed.write_text(json.dumps(entries))
    argv = [
        "knowledge-ingest",
        "--contract",
        str(contract),
        "--list",
        str(listed),
        "--candidate-directory",
        str(journey.root / f"candidate-{operation}"),
        "--authorization-ref",
        AUTHORIZATION,
        "--publish",
        "--json",
    ]
    if journey.published.exists():
        argv += ["--baseline", str(journey.published)]
    if commit:
        argv += ["--commit"]
    return _invoke(argv, capsys)


def _baseline(root: Path, capsys, count: int = 2) -> Journey:
    pair = _ordinary_enclosure(root / "repo")
    journey = Journey(pair, root, pair.memory_worktree / "knowledge.sqlite", {})
    entries = [
        _record(f"S-{i}", "foundation", declared("foundation", GUARANTEE) if i == 0 else None)
        for i in range(count)
    ]
    report = _author(journey, "base", entries, capsys)
    assert not report["refused"] and len(report["committed"]) == count, report
    assert report["publishedIdentity"]["state"] == "confirmed"
    return Journey(pair, root, journey.published, report)


def _successor(journey: Journey, references: list[dict] | None = None) -> dict:
    previous = journey.baseline["family"]["guarantees"][0]
    if references is None:
        references = [
            {
                "member_id": row["memberId"],
                "basis": "The unchanged obligation still supports this guarantee.",
            }
            for row in journey.baseline["family"]["memberships"]
        ]
    declaration = declared(
        "extended",
        GUARANTEE,
        version="v2",
        family_id=previous["familyId"],
        predecessors=[previous["familyRevisionId"]],
    )
    declaration["retain_memberships"] = references
    return _record("PAGING-NEW", "extended", declaration)


def _rows(database: Path) -> dict[str, set[tuple]]:
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        return {
            table: set(connection.execute(f"SELECT * FROM {table}")) for table in PROTECTED_TABLES
        }


@pytest.mark.parametrize("siblings", [2, 11])
def test_public_successor_retains_exact_siblings_and_publishes_across_leaf_scopes(
    tmp_path: Path, capsys, siblings
):
    journey = _baseline(tmp_path, capsys, siblings)
    before_bytes = journey.published.read_bytes()
    before_rows = _rows(journey.published)
    old_members = journey.baseline["family"]["memberships"]
    old_revisions = {row["invariantRevisionId"] for row in old_members}
    request = _successor(journey)
    preview = _author(journey, "next", [request], capsys, commit=False)
    assert preview["family"]["state"] == "projected" and not preview["refused"]
    assert not Path(preview["candidateDirectory"]).exists()
    assert journey.published.read_bytes() == before_bytes
    projected_retained = [
        row for row in preview["family"]["memberships"] if row["retainedFromMemberId"]
    ]
    assert {row["invariantRevisionId"] for row in projected_retained} == old_revisions

    published = _author(journey, "next", [request], capsys)
    assert not published["refused"] and len(published["committed"]) == 1, published
    assert published["publication"]["state"] == "published"
    assert published["publishedIdentity"]["state"] == "confirmed"
    family = published["family"]["guarantees"][0]
    assert family["familyId"] == journey.baseline["family"]["guarantees"][0]["familyId"]
    assert family["membersRecorded"] == siblings + 1
    assert family["unchangedSiblingMembers"] == siblings
    edges = published["family"]["memberships"]
    retained = [row for row in edges if row["retainedFromMemberId"]]
    assert len(retained) == siblings and all(row["state"] == "added" for row in retained)
    assert {row["retainedFromMemberId"] for row in retained} == {
        row["memberId"] for row in old_members
    }
    assert {row["invariantRevisionId"] for row in retained} == old_revisions
    assert {row["memberId"] for row in edges}.isdisjoint(row["memberId"] for row in old_members)
    namespace = published["repositoryId"]
    assert members_of(journey.published, namespace, family["familyRevisionId"]) == {
        row["invariantRevisionId"] for row in edges
    }
    old_family = journey.baseline["family"]["guarantees"][0]["familyRevisionId"]
    assert members_of(journey.published, namespace, old_family) == old_revisions
    after_rows = _rows(journey.published)
    assert all(before_rows[table] <= after_rows[table] for table in PROTECTED_TABLES)
    assert len(after_rows["invariant_revision"]) == len(before_rows["invariant_revision"]) + 1
    assert len(after_rows["invariant"]) == len(before_rows["invariant"]) + 1

    # A reordered retention set is the same exact request; no identity or row is allocated again.
    request["family"]["memberships"][0]["declares"]["retain_memberships"].reverse()
    replay = _author(journey, "next", [request], capsys)
    assert not replay["refused"] and replay["publication"]["state"] == "no_change", replay
    assert _rows(journey.published) == after_rows
    assert replay["family"]["guarantees"][0]["familyRevisionId"] == family["familyRevisionId"]
    assert all(row["state"] == "reused" for row in replay["family"]["memberships"])


@pytest.mark.parametrize(
    "invalid",
    [
        "malformed",
        "absent",
        "wrong-family",
        "undeclared-predecessor",
        "duplicate-id",
        "foreign-namespace",
    ],
)
def test_invalid_retention_refuses_without_changing_existing_knowledge(
    tmp_path: Path, capsys, invalid
):
    journey = _baseline(tmp_path, capsys)
    requested = _successor(journey)
    declaration = requested["family"]["memberships"][0]["declares"]
    retained = declaration["retain_memberships"]
    if invalid == "malformed":
        retained[0]["member_id"] = "not-a-membership-uuid"
    elif invalid == "absent":
        retained[0]["member_id"] = str(uuid4())
    elif invalid == "undeclared-predecessor":
        declaration["predecessor_revision_ids"] = []
    elif invalid == "duplicate-id":
        retained.append(deepcopy(retained[0]))
    elif invalid == "wrong-family":
        other = _author(
            journey,
            "base",
            [_record("OTHER", "other", declared("other", "Another joint guarantee."))],
            capsys,
        )
        assert not other["refused"]
        retained[0]["member_id"] = other["family"]["memberships"][0]["memberId"]
    else:
        foreign = world(tmp_path / "foreign")
        foreign_entry = with_family(
            bootstrap_entry("FOREIGN", symbol_name=BOOTSTRAP_SYMBOL),
            state="member",
            memberships=[
                membership(
                    "foreign", declares=declared("foreign", "A separate repository's guarantee.")
                )
            ],
        )
        listed = hand_off(foreign.root, "family", [foreign_entry])
        other = _invoke(bootstrap_argv(foreign, listed, "--commit", "--json"), capsys)
        assert other["publication"]["state"] == "published", other
        assert other["publication"]["identity"]["repositoryId"] != journey.baseline["repositoryId"]
        retained[0]["member_id"] = next(
            row.subject.record_id
            for row in family_view(
                foreign.destination, other["publication"]["identity"]["repositoryId"]
            ).rows
            if row.subject.record_kind == "family_member"
        )
    before = journey.published.read_bytes()
    candidate = tmp_path / "candidate-base" / "knowledge-candidate.sqlite"
    candidate_before = dataset_identity(candidate)
    for commit in (False, True):
        refused = _author(journey, "base", [requested], capsys, commit=commit)
        assert not refused["committed"] and len(refused["refused"]) == 1, refused
        assert "family_retention_" in refused["refused"][0]["refusal"]
        assert refused["publication"] is None
        assert journey.published.read_bytes() == before
        assert dataset_identity(candidate) == candidate_before


def test_distinct_old_memberships_cannot_duplicate_one_successor_endpoint(tmp_path: Path, capsys):
    journey = _baseline(tmp_path, capsys)
    successor = _author(journey, "next", [_successor(journey)], capsys)
    assert not successor["refused"]
    old = journey.baseline["family"]["memberships"][0]
    retained_edge = next(
        row
        for row in successor["family"]["memberships"]
        if row["retainedFromMemberId"] == old["memberId"]
    )
    request = _successor(
        journey,
        [
            {
                "member_id": old["memberId"],
                "basis": "Retain the first predecessor's exact sibling.",
            },
            {
                "member_id": retained_edge["memberId"],
                "basis": "Retain the second predecessor's same sibling.",
            },
        ],
    )
    declaration = request["family"]["memberships"][0]["declares"]
    declaration["predecessor_revision_ids"].append(
        successor["family"]["guarantees"][0]["familyRevisionId"]
    )
    before = journey.published.read_bytes()
    result = _author(journey, "third", [request], capsys)
    assert (
        not result["committed"] and "family_retention_duplicate" in result["refused"][0]["refusal"]
    )
    assert journey.published.read_bytes() == before


@pytest.mark.parametrize("retiring_entry", ["same", "separate"])
def test_retaining_and_retiring_the_same_source_membership_refuses_without_history_change(
    tmp_path: Path, capsys, retiring_entry
):
    journey = _baseline(tmp_path, capsys)
    request = _successor(journey)
    member_id = journey.baseline["family"]["memberships"][0]["memberId"]
    entries = [request]
    if retiring_entry == "same":
        request["family"]["retire"] = [member_id]
    else:
        retirement = _record("S-0", "foundation")
        retirement["family"] = {"state": "member", "retire": [member_id]}
        entries.append(retirement)
    before = journey.published.read_bytes()
    result = _author(journey, "base", entries, capsys)
    assert not result["committed"] and result["refused"], result
    assert all(
        "family_retention_retirement_conflict" in one["refusal"] for one in result["refused"]
    )
    assert journey.published.read_bytes() == before


def test_refused_retirement_does_not_block_an_eligible_retaining_successor(tmp_path: Path, capsys):
    journey = _baseline(tmp_path, capsys)
    successor = _successor(journey)
    duplicate = _record(
        "REFUSED-DUPLICATE",
        "extended",
        deepcopy(successor["family"]["memberships"][0]["declares"]),
    )
    old_members = journey.baseline["family"]["memberships"]
    duplicate["family"]["retire"] = [old_members[0]["memberId"]]
    before_bytes = journey.published.read_bytes()
    before_rows = _rows(journey.published)
    entries = [successor, duplicate]

    preview = _author(journey, "next", entries, capsys, commit=False)
    assert not Path(preview["candidateDirectory"]).exists()
    assert journey.published.read_bytes() == before_bytes
    published = _author(journey, "next", entries, capsys)
    for report in (preview, published):
        assert [row["entryId"] for row in report["refused"]] == ["REFUSED-DUPLICATE"]
        assert report["refused"][0]["refusal"].startswith("family_declared_twice:")
        assert {row["entryId"] for row in report["family"]["memberships"]} == {"PAGING-NEW"}
        assert {row["familyKey"] for row in report["family"]["guarantees"]} == {"extended"}
        retained = [row for row in report["family"]["memberships"] if row["retainedFromMemberId"]]
        assert {row["invariantRevisionId"] for row in retained} == {
            row["invariantRevisionId"] for row in old_members
        }
    assert [row["entryId"] for row in published["committed"]] == ["PAGING-NEW"]
    assert published["publication"]["state"] == "published"
    assert published["publishedIdentity"]["state"] == "confirmed"
    namespace = published["repositoryId"]
    old_family = journey.baseline["family"]["guarantees"][0]["familyRevisionId"]
    assert members_of(journey.published, namespace, old_family) == {
        row["invariantRevisionId"] for row in old_members
    }
    new_family = published["family"]["guarantees"][0]["familyRevisionId"]
    assert members_of(journey.published, namespace, new_family) == {
        row["invariantRevisionId"] for row in published["family"]["memberships"]
    }
    after_rows = _rows(journey.published)
    assert all(before_rows[table] <= after_rows[table] for table in PROTECTED_TABLES)
    assert len(after_rows["invariant"]) == len(before_rows["invariant"]) + 1
    assert len(after_rows["invariant_revision"]) == len(before_rows["invariant_revision"]) + 1
    assert len(after_rows["family_revision"]) == len(before_rows["family_revision"]) + 1


def test_refused_retention_does_not_block_eligible_retirement_or_independent_entry(
    tmp_path: Path, capsys
):
    journey = _baseline(tmp_path, capsys)
    independent = _record(
        "VALID-NEW", "duplicate-key", declared("duplicate-key", "A separate explicit guarantee.")
    )
    refused = _successor(journey)
    refused["id"] = "REFUSED-OWNER"
    refused["family"]["memberships"].append(
        membership("duplicate-key", declares=declared("duplicate-key", "A conflicting guarantee."))
    )
    old_members = journey.baseline["family"]["memberships"]
    retired_member = old_members[0]
    retirement = _record("S-0", "foundation")
    retirement["family"] = {"state": "member", "retire": [retired_member["memberId"]]}
    before_bytes = journey.published.read_bytes()
    before_rows = _rows(journey.published)
    candidate = tmp_path / "candidate-base" / "knowledge-candidate.sqlite"
    before_candidate = dataset_identity(candidate)
    entries = [independent, refused, retirement]

    preview = _author(journey, "base", entries, capsys, commit=False)
    assert journey.published.read_bytes() == before_bytes
    assert dataset_identity(candidate) == before_candidate
    published = _author(journey, "base", entries, capsys)
    for report in (preview, published):
        assert [row["entryId"] for row in report["refused"]] == ["REFUSED-OWNER"]
        assert report["refused"][0]["refusal"].startswith("family_declared_twice:")
        assert {row["entryId"] for row in report["family"]["memberships"]} == {"VALID-NEW", "S-0"}
        assert all(row["familyKey"] != "extended" for row in report["family"]["guarantees"])
        assert not any(row["retainedFromMemberId"] for row in report["family"]["memberships"])
    assert {row["entryId"] for row in published["committed"]} == {"VALID-NEW", "S-0"}
    assert published["publication"]["state"] == "published"
    assert published["publishedIdentity"]["state"] == "confirmed"
    assert any(
        row["memberId"] == retired_member["memberId"] and row["state"] == "retired"
        for row in published["family"]["memberships"]
    )
    old_family = journey.baseline["family"]["guarantees"][0]["familyRevisionId"]
    assert members_of(journey.published, published["repositoryId"], old_family) == {
        row["invariantRevisionId"] for row in old_members if row != retired_member
    }
    after_rows = _rows(journey.published)
    assert all(
        before_rows[table] <= after_rows[table]
        for table in PROTECTED_TABLES
        if table != "family_member"
    )
    assert len(before_rows["family_member"] - after_rows["family_member"]) == 1
    assert len(after_rows["invariant"]) == len(before_rows["invariant"]) + 1
    assert len(after_rows["invariant_revision"]) == len(before_rows["invariant_revision"]) + 1
    assert len(after_rows["family_revision"]) == len(before_rows["family_revision"]) + 1


@pytest.mark.parametrize("change", ["set", "basis"])
def test_changed_retention_cannot_reuse_an_allocated_family_declaration(
    tmp_path: Path, capsys, change
):
    journey = _baseline(tmp_path, capsys)
    request = _successor(journey)
    first = _author(journey, "next", [request], capsys)
    assert not first["refused"]
    before = journey.published.read_bytes()
    retained = request["family"]["memberships"][0]["declares"]["retain_memberships"]
    if change == "set":
        retained.pop()
    else:
        retained[0]["basis"] = "A different authored rationale."
    result = _author(journey, "next", [request], capsys)
    assert (
        not result["committed"] and "family_allocation_conflict" in result["refused"][0]["refusal"]
    )
    assert journey.published.read_bytes() == before
