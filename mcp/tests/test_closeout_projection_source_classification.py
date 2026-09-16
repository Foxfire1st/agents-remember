"""A capacity refusal is reported as an invalid source, not an unreadable one.

Measured on this leaf: `closeout_queue_graph` refuses a sprint whose graph is past its bound with
`closeout-queue-master-capacity-exceeded`, and `closeout_projection._problem` decided the reported
state by testing the substring `cap-exceeded`. Both surviving capacity codes spell the bound
`capacity-exceeded`, so the classifier missed them and the operator was told the source could not be
read -- when the source had been read perfectly and was invalid. The code and its classification are
declared once, in `closeout_queue_errors`, so the raiser and the classifier move together.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from agents_remember.kernel.primitives.checkout_coordination import declare_test_process

declare_test_process()

from agents_remember.models.queue.closeout_queue import (
    MAX_CLOSEOUT_MASTERS,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import (
    SprintExecutionGraph,
    SprintExecutionNode,
    TaskDocument,
    read_task_doc,
    write_task_doc,
)
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees.queue.closeout_projection import (
    _problem,
    capture_projection_source,
)
from agents_remember.worktrees.queue.closeout_queue_errors import (
    CAPACITY_REFUSAL_CODES,
    MASTER_CAPACITY_EXCEEDED,
    CloseoutQueueError,
)
from agents_remember.worktrees.queue.closeout_queue_graph import graph_context
from test_closeout_queue import REPO, SPRINT, QueueFixture


def _oversized_graph() -> SprintExecutionGraph:
    """One node past the master bound, every node a well-formed master reference."""

    return SprintExecutionGraph(
        nodes=[
            SprintExecutionNode(
                ref=TaskDocumentRef(repository=REPO, path=f"bulk-{index}/task.json")
            )
            for index in range(MAX_CLOSEOUT_MASTERS + 1)
        ],
        edges=[],
    )


def test_a_graph_past_its_bound_refuses_by_its_own_declared_code() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        fixture = QueueFixture(Path(tmp))
        # The bound is a property of the sprint's own authored graph, so the oversized graph is
        # the one the sprint declares -- exactly what the projection hands to this raiser.
        oversized = _oversized_graph()
        sprint = read_task_doc(fixture.tasks / "sprint" / "task.json")
        payload = sprint.model_dump(mode="json", by_alias=True)
        payload["executionGraph"] = oversized.model_dump(mode="json", by_alias=True)
        write_task_doc(fixture.tasks / "sprint", TaskDocument.model_validate(payload))
        topology = TaskDocumentTopology(fixture.coord)

        try:
            graph_context(
                topology,
                SPRINT,
                authored_graph=oversized,
                strict_registers=False,
                overrides=None,
            )
        except CloseoutQueueError as error:
            refused = error
        else:  # pragma: no cover - the bound is the subject of this case
            raise AssertionError("a graph past the master bound was admitted")

        assert refused.status == MASTER_CAPACITY_EXCEEDED
        # The refusal's own code is what the projection classifies: read perfectly, too big.
        assert _problem("task", SPRINT.key, refused.status, "split the sprint").state == "invalid"


def test_an_unreadable_source_and_the_ordinary_case_are_unchanged() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        fixture = QueueFixture(Path(tmp))

        ordinary = capture_projection_source(fixture.coord, SPRINT)

        assert ordinary.identity.readable
        assert ordinary.identity.problems == ()
        assert ordinary.classification == "active"
        # The other direction is not collapsed: a source that genuinely cannot be read says so.
        for code in ("contract-unreadable", "atomic-series-contract-unreadable"):
            assert _problem("door", "/door", code, "repair the door").state == "unreadable"


def test_every_declared_capacity_code_classifies_as_invalid() -> None:
    """The declaration and the classifier cannot disagree about a capacity refusal."""

    assert MASTER_CAPACITY_EXCEEDED in CAPACITY_REFUSAL_CODES
    for code in sorted(CAPACITY_REFUSAL_CODES):
        assert _problem("task", SPRINT.key, code, "split the sprint").state == "invalid"
