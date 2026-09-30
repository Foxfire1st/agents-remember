"""An unchanged file that the bound comparison's recorded realization links opens as context (ICR-R03).

The source-content route reads the inventory's own entries, and it now reads exactly one more
population: an unchanged path that a realization recorded in the *same comparison's* knowledge is
anchored at -- the statement is realized by code the task did not change, and the reviewer has to be
able to read it. These cases measure that admission through the operations the dashboard serves: a
real leaf enclosure and worktree, the real review route's inventory, the real capture, R11's real
freeze, and the real expansion route wired to the real application owner. Bytes are checked against
Git directly, never against the owner under test.

One case per property:

* a path a recorded realization links, on either snapshot, opens at the requested endpoints as
  ``attributed_unchanged`` / ``unchanged`` -- and the inventory and its counts do not change;
* an unchanged path no recorded realization links is still refused by name;
* a path linked only by another comparison's knowledge -- an earlier generation of the same leaf --
  is refused for the current pair, while that earlier pair still opens it from its own record;
* after the live tree moves, the listed pair still opens the exact historical bytes from its own
  generation, a superseded pair with no record of its knowledge is refused rather than answered from
  the knowledge the leaf holds now, and the moved path becomes an ordinary change of the new pair;
* a closed leaf's recorded comparison opens the same attributed path from its retained snapshots;
* a whitespace-padded spelling of a linked, unlinked or changed path is never admitted as attributed
  context -- the link is established for the exact requested spelling or not at all;
* the read owner's selection bound, forced below the fixture's scope, cannot turn a linked path into
  a refusal, because the link is asked with the exact claim-at-path query rather than a scope read;
* an unreadable snapshot leaves the link *undetermined*: the refusal names that cause and its remedy
  and never asserts that no realization links the path.

The confinement case for arbitrary unchanged paths stays in the sibling module and is unchanged.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import apsw
import pytest
from agents_remember.application.knowledge_read import open_read_context, read_knowledge_scope
from agents_remember.application.review_candidate_resolution import review_namespace
from agents_remember.application.review_comparison_freeze import freeze_review_comparison
from agents_remember.application.review_source_realization_link import _proof_at_path
from agents_remember.memory.knowledge import read as read_selection
from agents_remember.models.knowledge.read import KnowledgeReadRequest, PathSeed
from agents_remember.models.knowledge.review_source_content import ReviewSourceExpansion
from read_scope_test_support import INTEGRATION_PATH, RESOLUTION_PATH, UNPARSED_PATH
from test_knowledge_review_source_content import (
    _blob_text,
    _expand,
    _listed_inventory,
    _object_id,
)
from test_knowledge_review_source_endpoints import (
    MODIFIED_PATH,
    EndpointFixture,
    _commit,
    build_endpoint_fixture,
)

pytestmark = pytest.mark.evidence_unit

# Unchanged by the fixture's candidate, and linked by a realization in both snapshots.
ATTRIBUTED_PATH = RESOLUTION_PATH
# Unchanged, and linked only by the before snapshot (the candidate removed that claim).
BEFORE_ONLY_PATH = INTEGRATION_PATH
# Unchanged, and linked by no realization in either snapshot.
UNRELATED_PATH = UNPARSED_PATH
# The four near-miss spellings the path-seed shape rule would normalize onto the path itself.
_PADDINGS = (" {}", "{} ", "\t{}", "{}\n")
# The sentence an established negative carries; an undetermined link must never carry it.
_NOT_LINKED = "no realization recorded in the comparison's knowledge links it"


@pytest.fixture
def attributed_fixture(tmp_path: Path) -> EndpointFixture:
    """One fresh live enclosure with both knowledge halves, per case."""

    return build_endpoint_fixture(tmp_path / "attributed")


def _freeze(fixture: EndpointFixture) -> str:
    """Publish the current comparison as a generation and return its id."""

    outcome = freeze_review_comparison(fixture.config, fixture.request())
    assert outcome.state == "published", outcome.refusal
    assert outcome.manifest is not None
    return outcome.manifest.generation_id


def _pair(inventory: dict) -> dict[str, str]:
    return {"before": inventory["before_code_tree_id"], "after": inventory["after_code_tree_id"]}


def _refused(body: dict, path: str) -> str:
    assert body["_status"] == 400, body
    assert body["state"] == "refused"
    assert body["refusal"]["code"] == "source_content_unresolved"
    assert "expansion" not in body
    detail = body["refusal"]["detail"]
    assert path in detail
    return detail


def _attributed(body: dict) -> dict:
    assert body["_status"] == 200, body
    expansion = body["expansion"]
    ReviewSourceExpansion.model_validate(expansion)
    assert expansion["admission"] == "attributed_unchanged"
    assert expansion["status"] == "unchanged"
    assert expansion["path_bound"] == "requested_generation"
    return expansion


def _write_and_commit(fixture: EndpointFixture, path: str, text: str) -> None:
    (fixture.worktree / path).write_text(text, encoding="utf-8")
    _commit(fixture.worktree, f"move {path}")


def test_an_unchanged_path_a_recorded_realization_links_opens_as_context_without_counting(
    attributed_fixture: EndpointFixture,
) -> None:
    """Both snapshots' links admit, each side's bytes are the pair's own, and the inventory is fixed.

    The path linked on both sides and the path linked only by the before snapshot both open; each
    side carries the exact blob the requested tree holds (the same object on both sides, because the
    pair did not change it), and the admission is stated as a typed field rather than left to be
    inferred. Re-listing after the reads shows the inventory still lists neither path and still
    counts the same changed files.
    """

    fixture = attributed_fixture
    inventory = _listed_inventory(fixture)
    listed = {entry["path"] for entry in inventory["entries"]}
    assert ATTRIBUTED_PATH not in listed and BEFORE_ONLY_PATH not in listed
    repository = fixture.contract.code_repo_path

    for path, sides in ((ATTRIBUTED_PATH, "before and after"), (BEFORE_ONLY_PATH, "before")):
        expansion = _attributed(_expand(fixture, path, **_pair(inventory)))
        blob = _object_id(repository, inventory["before_code_tree_id"], path)
        assert blob is not None
        assert _object_id(repository, inventory["after_code_tree_id"], path) == blob
        text = _blob_text(repository, inventory["before_code_tree_id"], path)
        for side in ("before", "after"):
            assert expansion[side]["state"] == "present"
            assert expansion[side]["object_id"] == blob
            assert expansion[side]["text"] == text
        assert f"comparison's {sides} knowledge" in expansion["admission_detail"]
        assert expansion["currentness"] == "current"

    relisted = _listed_inventory(fixture)
    assert relisted["entries"] == inventory["entries"]
    assert relisted["listed_total"] == inventory["listed_total"]


def test_an_unchanged_path_no_recorded_realization_links_is_still_refused(
    attributed_fixture: EndpointFixture,
) -> None:
    """With both knowledge halves present, an unlinked unchanged path is refused, naming both answers."""

    fixture = attributed_fixture
    inventory = _listed_inventory(fixture)
    assert UNRELATED_PATH not in {entry["path"] for entry in inventory["entries"]}

    detail = _refused(_expand(fixture, UNRELATED_PATH, **_pair(inventory)), UNRELATED_PATH)
    assert "the before snapshot records none" in detail
    assert "the after snapshot records none" in detail


def test_a_path_linked_only_in_another_comparison_is_refused_for_this_one(
    attributed_fixture: EndpointFixture,
) -> None:
    """An earlier generation's link does not admit the path into the current comparison.

    The first comparison is frozen while its before snapshot links ``BEFORE_ONLY_PATH``. The leaf's
    live baseline half is then replaced by knowledge that links it nowhere and the tree moves, so the
    current comparison records no such realization and must refuse -- even though a comparison of the
    same leaf does. The control shows the earlier pair still opens it, from that generation's record.
    """

    fixture = attributed_fixture
    first = _listed_inventory(fixture)
    generation_id = _freeze(fixture)
    halves = fixture.contract.worktree_group / "provider-runtime/dev-ar-coordination/knowledge"
    shutil.copyfile(
        halves / "candidate/knowledge-candidate.sqlite",
        halves / "baseline/knowledge-candidate.sqlite",
    )
    _write_and_commit(fixture, MODIFIED_PATH, "# batch\na later generation\n")
    current = _listed_inventory(fixture)
    assert current["after_code_tree_id"] != first["after_code_tree_id"]
    assert BEFORE_ONLY_PATH not in {entry["path"] for entry in current["entries"]}

    detail = _refused(_expand(fixture, BEFORE_ONLY_PATH, **_pair(current)), BEFORE_ONLY_PATH)
    assert "the before snapshot records none" in detail
    assert generation_id not in detail

    earlier = _attributed(_expand(fixture, BEFORE_ONLY_PATH, **_pair(first)))
    assert generation_id in earlier["admission_detail"]
    assert earlier["currentness"] == "superseded"


def test_after_the_live_tree_moves_the_listed_pair_keeps_its_exact_attributed_bytes(
    attributed_fixture: EndpointFixture,
) -> None:
    """The recorded pair serves its own bytes; an unrecorded superseded pair is refused, not rebound.

    ``ATTRIBUTED_PATH`` is unchanged in the first two pairs and rewritten in the third. The first pair
    was frozen, so its own retained knowledge admits the path and the served bytes are the requested
    tree's -- not the moved working tree's. The second pair was listed but never recorded: the leaf's
    current knowledge still links the path, and it is not substituted for the knowledge of a
    comparison nothing recorded. In the third pair the path is an ordinary change.
    """

    fixture = attributed_fixture
    first = _listed_inventory(fixture)
    generation_id = _freeze(fixture)
    _write_and_commit(fixture, MODIFIED_PATH, "# batch\nan unrecorded generation\n")
    second = _listed_inventory(fixture)
    moved = "# resolution\nrewritten after both listings\n"
    _write_and_commit(fixture, ATTRIBUTED_PATH, moved)
    third = _listed_inventory(fixture)
    assert (
        len(
            {first["after_code_tree_id"], second["after_code_tree_id"], third["after_code_tree_id"]}
        )
        == 3
    )

    historical = _attributed(_expand(fixture, ATTRIBUTED_PATH, **_pair(first)))
    original = _blob_text(
        fixture.contract.code_repo_path, first["after_code_tree_id"], ATTRIBUTED_PATH
    )
    assert original != moved
    assert historical["before"]["text"] == historical["after"]["text"] == original
    assert historical["after_code_tree_id"] == first["after_code_tree_id"]
    assert historical["currentness"] == "superseded"
    assert generation_id in historical["admission_detail"]

    unrecorded = _refused(_expand(fixture, ATTRIBUTED_PATH, **_pair(second)), ATTRIBUTED_PATH)
    assert "no comparison generation this leaf published records exactly the requested pair" in (
        unrecorded
    )

    changed = _expand(fixture, ATTRIBUTED_PATH, **_pair(third))
    assert changed["_status"] == 200, changed
    assert changed["expansion"]["admission"] == "changed"
    assert changed["expansion"]["status"] == "modified"
    assert changed["expansion"]["after"]["text"] == moved
    assert ATTRIBUTED_PATH in {entry["path"] for entry in third["entries"]}


def test_a_closed_leaf_opens_its_attributed_path_from_the_retained_generation(
    attributed_fixture: EndpointFixture,
) -> None:
    """With the worktree group removed, the recorded comparison still admits exactly the same paths."""

    fixture = attributed_fixture
    inventory = _listed_inventory(fixture)
    _freeze(fixture)
    shutil.rmtree(fixture.contract.worktree_group)

    expansion = _attributed(_expand(fixture, ATTRIBUTED_PATH, **_pair(inventory)))
    assert expansion["after"]["text"] == _blob_text(
        fixture.contract.code_repo_path, inventory["after_code_tree_id"], ATTRIBUTED_PATH
    )
    assert expansion["currentness"] == "current"

    changed = _expand(fixture, MODIFIED_PATH, **_pair(inventory))
    assert changed["_status"] == 200, changed
    assert changed["expansion"]["admission"] == "changed"
    _refused(_expand(fixture, UNRELATED_PATH, **_pair(inventory)), UNRELATED_PATH)


def test_a_padded_spelling_is_never_admitted_as_attributed_context(
    attributed_fixture: EndpointFixture,
) -> None:
    """Near-miss spellings of a linked, an unlinked and a changed path are all refused by name.

    The path-seed shape rule strips surrounding whitespace, so a padded spelling used to be answered
    with the link of the path it normalizes to and served as ``attributed_unchanged``. A padded
    changed path is refused too, exactly as the base route refused it (no inventory entry has that
    spelling). The exact spellings beside them still open as before.
    """

    fixture = attributed_fixture
    inventory = _listed_inventory(fixture)
    for path in (ATTRIBUTED_PATH, UNRELATED_PATH, MODIFIED_PATH):
        for padding in _PADDINGS:
            spelling = padding.format(path)
            detail = _refused(_expand(fixture, spelling, **_pair(inventory)), spelling.strip())
            assert "not exactly a path a recorded source anchor can carry" in detail, spelling

    assert _attributed(_expand(fixture, ATTRIBUTED_PATH, **_pair(inventory)))["path"] == (
        ATTRIBUTED_PATH
    )
    exact_changed = _expand(fixture, MODIFIED_PATH, **_pair(inventory))
    assert exact_changed["_status"] == 200, exact_changed
    assert exact_changed["expansion"]["admission"] == "changed"


def test_a_selection_bound_below_the_scope_cannot_refuse_a_linked_path(
    attributed_fixture: EndpointFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's lowered-bound reproduction: the link question does not ride on scope size.

    With the read owner's selection bound forced to one item, a path-seeded scope read of the linked
    path is refused ``selection_incomplete`` (measured first, so the bound demonstrably bites). The
    link is asked with the exact claim-at-path query, which expands no family, so the path still opens.
    """

    fixture = attributed_fixture
    inventory = _listed_inventory(fixture)
    monkeypatch.setattr(read_selection, "SELECTION_ITEM_LIMIT", 1)
    resolved = fixture.resolve()
    database = resolved.candidate_database
    namespace = review_namespace(resolved.repository_id, database)
    scope = read_knowledge_scope(
        database,
        open_read_context(database, namespace),
        KnowledgeReadRequest(seed=PathSeed(path=ATTRIBUTED_PATH)),
    )
    assert scope.refusal is not None and scope.refusal.code == "selection_incomplete", scope

    expansion = _attributed(_expand(fixture, ATTRIBUTED_PATH, **_pair(inventory)))
    assert "comparison's before and after knowledge" in expansion["admission_detail"]


def test_an_unreadable_snapshot_leaves_the_link_undetermined_rather_than_absent(
    attributed_fixture: EndpointFixture,
) -> None:
    """A damaged half never becomes "not linked": the refusal names the cause and its remedy.

    The after half is overwritten with bytes that are not a database. A path the readable before half
    links still opens (one established link is enough); a path it does not link is refused as
    undetermined, naming the unreadable after snapshot and a remedy for that cause.
    """

    fixture = attributed_fixture
    inventory = _listed_inventory(fixture)
    after_half = fixture.resolve().candidate_database
    after_half.write_bytes(b"not a knowledge database\n")

    linked = _attributed(_expand(fixture, BEFORE_ONLY_PATH, **_pair(inventory)))
    assert "comparison's before knowledge" in linked["admission_detail"]
    assert linked["admission_detail"].startswith("a realization or proof recorded for the path")

    body = _expand(fixture, UNRELATED_PATH, **_pair(inventory))
    detail = _refused(body, UNRELATED_PATH)
    assert "could not be determined" in detail
    assert _NOT_LINKED not in detail
    assert "the before snapshot records none" in detail
    assert "the after snapshot at" in detail and "could not be read" in detail
    assert "restore or repair the knowledge snapshot" in body["refusal"]["next_action"]


def test_never_initialized_knowledge_asks_to_initialize_it(tmp_path: Path) -> None:
    """ICR-L43-R2 O1 (MIK-R31 rule 6): with no knowledge halves at all, the remedy is to initialize.

    The link stays undetermined -- nothing was read -- but a snapshot that never existed is not a
    damaged one, so the next action names initialization and not restoring or repairing.
    """

    fixture = build_endpoint_fixture(tmp_path / "never", datasets=False)
    inventory = _listed_inventory(fixture)
    body = _expand(fixture, UNRELATED_PATH, **_pair(inventory))
    detail = _refused(body, UNRELATED_PATH)
    assert "could not be determined" in detail and "is absent at" in detail
    assert body["refusal"]["next_action"].startswith("initialize this leaf's knowledge")
    assert "restore or repair" not in body["refusal"]["next_action"]


def test_a_tree_index_links_a_path_its_proof_entry_is_anchored_at() -> None:
    """MIK-R31 rule 5: a proof card's unchanged test file opens; a dataset (no ix_entry) links none."""

    index = apsw.Connection(":memory:")
    index.execute(
        "CREATE TABLE ix_entry (id TEXT, kind TEXT, invariant TEXT, path TEXT, sidecar TEXT, "
        "document TEXT)"
    )
    index.execute(
        "INSERT INTO ix_entry VALUES ('PRF-AAAAAA', 'proof', 'INV-AAAAAA', 'tests/t.py', 's', '{}')"
    )
    assert _proof_at_path(index, "tests/t.py")
    assert not _proof_at_path(index, "tests/other.py")
    assert not _proof_at_path(apsw.Connection(":memory:"), "tests/t.py")
