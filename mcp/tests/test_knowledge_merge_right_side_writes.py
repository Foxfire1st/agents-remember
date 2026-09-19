"""What the merge does with the right side's own writes: replay them, then judge the result.

``merge_knowledge_datasets`` replays one side's whole base-to-side delta onto a copy of the other
side, so the *right* side is the one whose operations travel as a changeset and the one whose
in-place edits are the merge's own work rather than a copied file. Two properties of that replay are
protected here:

* **A right-side ``UPDATE`` is an operation whose key lives on its old side.** SQLite reports only
  the columns an operation changes, so an update's *new* entries carry the not-supplied marker
  exactly where the primary key is. A postcondition check that read the key from that side looks for
  a row named ``repository_id=<not-supplied>/invariant_id=<not-supplied>``, does not find it, and
  refuses every update with ``changeset_postcondition_failed`` -- so a side that edited an existing
  record in place could not be merged at all, whatever it edited.
* **A replayed ``route.parent_route_id`` change is judged by the acyclicity rule, in the same
  transaction.** ``route`` is inside the merge's attached table set and a reparent is a real
  operation, so the merge is a production path that writes route hierarchies. The walk runs on the
  connection the delta was applied on, after the rows are written and before that application
  commits, so a candidate whose hierarchy reaches itself is rolled back rather than published.

The cases drive the public merge entry point over the shared three-dataset case, so what they
measure is the operation a caller invokes rather than a helper beside it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import apsw
import pytest
from agents_remember.application.knowledge_merge import (
    merge_resolved_knowledge_datasets,
    resolve_knowledge_merge_base,
)
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.merge_base import MERGE_OPERATION
from agents_remember.models.knowledge.merge import (
    MergeBaseRequest,
    MergeBaseResolution,
    MergeOutcome,
    MergeRequest,
    ResolvedGitBase,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.knowledge.snapshot import SnapshotDestinationRequest
from merge_case_test_support import MergeCase, build_case, labels_of, set_label

pytestmark = pytest.mark.evidence_unit

# The three routes the base case carries, so a side has a hierarchy to reparent instead of a table
# whose only rows arrive with the side that is being judged.
ROOT_ROUTE_ID = "root"
MID_ROUTE_ID = "mid"
LEAF_ROUTE_ID = "leaf"

CaseShaper = Callable[[MergeCase, dict[str, Path]], None]


def _run(path: Path, statement: str, parameters: apsw.Bindings) -> None:
    """Run one explicit statement against a dataset, with foreign keys enforced."""

    connection = apsw.Connection(str(path))
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.execute(statement, parameters)
    finally:
        connection.close()


def author_routes(case: MergeCase, states: dict[str, Path]) -> None:
    """Author one three-node route hierarchy into the base state before the sides are derived."""

    del case
    connection = open_read_only_database(states["base"])
    try:
        repository_id = str(
            next(iter(connection.execute("SELECT repository_id FROM repository")))[0]
        )
    finally:
        connection.close()
    for route_id, parent, path in (
        (ROOT_ROUTE_ID, None, "src"),
        (MID_ROUTE_ID, ROOT_ROUTE_ID, "src/mid"),
        (LEAF_ROUTE_ID, MID_ROUTE_ID, "src/mid/leaf"),
    ):
        _run(
            states["base"],
            "INSERT INTO route (repository_id, route_id, parent_route_id, path, provenance) "
            "VALUES (?, ?, ?, ?, ?)",
            (repository_id, route_id, parent, path, "{}"),
        )


def reparent_the_leaf_under_the_root(case: MergeCase, states: dict[str, Path]) -> None:
    """Move the leaf onto the root on the right side, through an ``UPDATE`` the merge replays."""

    del case
    _run(
        states["right"],
        "UPDATE route SET parent_route_id = ? WHERE route_id = ?",
        (ROOT_ROUTE_ID, LEAF_ROUTE_ID),
    )


def reparent_the_root_under_the_leaf(case: MergeCase, states: dict[str, Path]) -> None:
    """Close a three-node route cycle on the right side, through an ``UPDATE`` the merge replays."""

    del case
    _run(
        states["right"],
        "UPDATE route SET parent_route_id = ? WHERE route_id = ?",
        (LEAF_ROUTE_ID, ROOT_ROUTE_ID),
    )


def resolved_base(case: MergeCase) -> MergeBaseResolution:
    """Resolve this case's base, requiring that the resolution succeeded."""

    world = case.world
    assert world is not None
    outcome = resolve_knowledge_merge_base(
        MergeBaseRequest(
            repository=case.repository,
            git_base=ResolvedGitBase(
                repository_root=world.root,
                base_commit_id=world.base_commit,
                left_commit_id=world.left_commit,
                right_commit_id=world.right_commit,
            ),
            inputs=case.merge_inputs(),
        )
    )
    assert not isinstance(outcome, KnowledgeRefusal), outcome
    return outcome


def merge(case: MergeCase, *, destination: Path | None = None) -> MergeOutcome:
    """Merge this case's three datasets, optionally publishing the candidate."""

    return merge_resolved_knowledge_datasets(
        MergeRequest(
            resolution=resolved_base(case),
            databases=case.databases_by_role(),
            destination=(
                None
                if destination is None
                else SnapshotDestinationRequest(destination_path=destination)
            ),
        )
    )


def route_count(path: Path) -> int:
    """Return one dataset's stored route count."""

    connection = open_read_only_database(path)
    try:
        return int(next(iter(connection.execute("SELECT count(*) FROM route")))[0])
    finally:
        connection.close()


def parent_of(path: Path, route_id: str) -> str | None:
    """Return one route's recorded parent, read through a separate read-only connection."""

    connection = open_read_only_database(path)
    try:
        row = next(
            iter(
                connection.execute(
                    "SELECT parent_route_id FROM route WHERE route_id = ?", (route_id,)
                )
            ),
            None,
        )
    finally:
        connection.close()
    assert row is not None, f"{route_id} is not a route in {path}"
    return None if row[0] is None else str(row[0])


def test_a_right_side_update_is_replayed_and_the_published_candidate_carries_it(
    tmp_path: Path,
) -> None:
    """A side's in-place edit reaches the merged candidate instead of refusing the merge.

    The right side changes one stored invariant's label and nothing else, which is the smallest
    right-side ``UPDATE`` a merge can be asked to replay. The case publishes the candidate and reads
    the label back out of the published file, so the evidence is the merged dataset rather than the
    outcome the merge reported about it.

    Reading the key from the update's new side reddens it: the postcondition check looks for
    ``invariant_id=<not-supplied>``, finds no such row, and refuses with
    ``changeset_postcondition_failed`` naming the marker in ``record_id``. Checking the changed
    column without the key would not redden it, which is why the label is read back rather than
    inferred from the state.
    """

    right_label = "the right side's own label"
    case = build_case(
        tmp_path / "right-update",
        shape=lambda _case, states: set_label(states["right"], right_label),
    )
    destination = tmp_path / "memory" / "merged.sqlite"

    outcome = merge(case, destination=destination)

    assert outcome.state == "structurally_merged", outcome.refusal
    assert outcome.refusal is None
    assert outcome.publication_state == "published"
    assert right_label in labels_of(case.state_path("right"))
    assert right_label in labels_of(destination)
    assert outcome.merged_identity is not None
    assert dataset_identity(destination).logical_digest == outcome.merged_identity.logical_digest


def test_a_replayed_reparent_moves_the_row_it_names_and_no_other(tmp_path: Path) -> None:
    """The replayed update changes the right row, and the rest of the hierarchy is untouched.

    A three-node hierarchy is reparented on the right side -- an ``UPDATE`` of a row both sides
    already hold -- and the published candidate must carry the moved parent while its siblings keep
    theirs. The key is what selects the row here, so a key read from the wrong side would either
    refuse the merge or fail to name this row at all; the two sibling assertions separate "the right
    row moved" from "something moved".
    """

    case = build_case(
        tmp_path / "right-reparent",
        base_shape=author_routes,
        shape=reparent_the_leaf_under_the_root,
    )
    destination = tmp_path / "memory" / "merged.sqlite"

    outcome = merge(case, destination=destination)

    assert outcome.state == "structurally_merged", outcome.refusal
    assert parent_of(destination, LEAF_ROUTE_ID) == ROOT_ROUTE_ID
    assert parent_of(destination, MID_ROUTE_ID) == ROOT_ROUTE_ID
    assert parent_of(destination, ROOT_ROUTE_ID) is None
    assert route_count(destination) == route_count(case.state_path("left"))


def test_a_replayed_route_reparent_that_closes_a_cycle_refuses_the_whole_merge(
    tmp_path: Path,
) -> None:
    """The acyclicity rule runs over the rows the delta just wrote, inside that application.

    The right side reparents the root under the leaf, so the merged candidate's hierarchy reaches
    itself. The merge must refuse with the rule's own code, attributed to the operation the caller
    invoked, and it must publish nothing: the application is rolled back with the check, so the
    private candidate never becomes a destination and the three inputs keep their exact bytes.

    Removing the walk from the merge reddens it -- the candidate would publish a cyclic hierarchy --
    and running the walk on the *inputs* instead of the result reddens it too: neither input is
    cyclic, and only the replayed rows make the candidate so.
    """

    case = build_case(
        tmp_path / "right-cycle",
        base_shape=author_routes,
        shape=reparent_the_root_under_the_leaf,
    )
    destination = tmp_path / "memory" / "merged.sqlite"
    before = {
        role: dataset_identity(case.state_path(role)).logical_digest
        for role in ("base", "left", "right")
    }
    assert parent_of(case.state_path("right"), ROOT_ROUTE_ID) == LEAF_ROUTE_ID
    assert parent_of(case.state_path("left"), ROOT_ROUTE_ID) is None

    outcome = merge(case, destination=destination)

    assert outcome.state == "refused"
    assert outcome.merged_identity is None
    assert not destination.exists()
    assert outcome.refusal is not None
    assert outcome.refusal.code == "lineage_cycle"
    assert outcome.refusal.operation == MERGE_OPERATION
    assert outcome.refusal.table == "route"
    assert outcome.refusal.observed is not None
    assert ROOT_ROUTE_ID in outcome.refusal.observed
    assert LEAF_ROUTE_ID in outcome.refusal.observed
    for role, digest in before.items():
        assert dataset_identity(case.state_path(role)).logical_digest == digest
