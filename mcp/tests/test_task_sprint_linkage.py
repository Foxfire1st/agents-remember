"""L14: sprint↔master typed linkage — attach_master / detach_master / linkage_report.

Covers the atomic four-artifact attach, judgment enforcement with zero writes on
refusal, dry-run previews, detach edge guards, the graph-absent default fact, the
drift report (M16 and ledger-reconciliation shapes), old-shape tolerance, seats
schema rules, and rendering of master links / the generated master index.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import agents_remember.application.task_sprint_linkage as sprint_linkage
from agents_remember.application.task_doc_tools import (
    VALID_OPERATIONS,
    TaskDocCall,
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.application.task_sprint_linkage import (
    _AttachMasterPayload,
    collect_linkage_facts,
    linkage_facts_for_get,
)
from agents_remember.mcp.registration import tasks as registration_tasks
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import (
    Decision,
    Section,
    SprintSeat,
    SubTaskRef,
    TaskDocument,
    read_task_doc,
    render_markdown,
    write_task_doc,
)
from agents_remember.tasks.document_refs import (
    TaskDocumentRefError,
    TaskDocumentTopology,
)
from pydantic import ValidationError
from test_task_execution_topology import (
    MASTER_A,
    MASTER_B,
    MASTER_C,
    REPOSITORY,
    SPRINT,
    _config,
    _master,
)
from test_worktree_support import git, init_repo

JUDGMENT_HEADING = "Judgment Register (canonical judgment authority)"
JUDGMENT_HEADER = (
    "| Judgment id | Kind (dependency meaning, execution nature, blast radius, priority, "
    "blocker placement, reprioritization, or leaf move) | Subject | Decision | Rationale | "
    "Evidence/fact refs | Author | Confidence | Supersedes |"
)
JUDGMENT_SEPARATOR = "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"


def _judgment_register(rows: list[str]) -> str:
    return "\n".join([JUDGMENT_HEADER, JUDGMENT_SEPARATOR, *rows])


def _judgment_row(judgment_id: str, author: str = "strategist") -> str:
    return (
        f"| {judgment_id} | execution nature | graph | nature=ruled | Explicit ruling. | "
        f"notes.md | {author} | high | |"
    )


def _register_section(*judgment_ids: str) -> Section:
    return Section(
        kind="freeform",
        heading=JUDGMENT_HEADING,
        body=_judgment_register([_judgment_row(judgment_id) for judgment_id in judgment_ids]),
    )


def _seat_doc(slug: str, references: list[str]) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": slug,
            "slug": slug,
            "title": slug,
            "kind": "subTask",
            "repo": REPOSITORY,
            "createdAt": "2026-08-15T00:00:00+00:00",
            "references": references,
        }
    )


class SprintLinkageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.coord = Path(self.temp.name)
        self.tasks = self.coord / "tasks" / REPOSITORY
        self.tasks.mkdir(parents=True)
        self.code = self.coord / "code"
        init_repo(self.code)
        git(self.code, "branch", "super", "main")
        self.cfg = _config(self.coord, self.code)
        self.topology = TaskDocumentTopology(self.coord)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_master(
        self,
        ref: TaskDocumentRef,
        *,
        nature: str | None = None,
        status: str = "planning",
    ) -> None:
        folder = Path(ref.path).parent.name
        write_task_doc(
            self.tasks / folder,
            _master(identity=folder, execution_nature=nature).model_copy(update={"status": status}),
        )

    def _write_sprint(
        self,
        *,
        graph: dict[str, Any] | None = None,
        rows: list[SubTaskRef] | None = None,
        orchestrates: list[str] | None = None,
        decisions: list[dict[str, str]] | None = None,
        seats: list[dict[str, Any]] | None = None,
    ) -> None:
        write_task_doc(
            self.tasks / "sprint",
            _master(
                identity="SPRINT",
                orchestrates=(
                    orchestrates if orchestrates is not None else ["master-a", "master-b"]
                ),
                execution_graph=graph,
            ).model_copy(
                update={
                    "integrationBranch": "super",
                    "sections": [_register_section("J-1", "J-2")],
                    "subTasks": rows or [],
                    "decisions": [Decision.model_validate(d) for d in decisions or []],
                    "seats": [SprintSeat.model_validate(s) for s in seats or []],
                }
            ),
        )

    def _graph_ful_sprint(self) -> None:
        self._write_master(MASTER_A, nature="organizational")
        self._write_master(MASTER_B, nature="atomic")
        self._write_sprint(
            graph={"nodes": [MASTER_A.model_dump(), MASTER_B.model_dump()], "edges": []}
        )

    def _op(
        self,
        operation: str,
        fields: dict[str, Any],
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id=REPOSITORY, task_name="sprint"),
            operation=operation,
            edit=TaskDocEdit(fields=fields),
            call=TaskDocCall(dry_run=dry_run),
        )

    def _attach(self, fields: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
        return self._op("attach_master", fields, dry_run=dry_run)

    def _detach(self, fields: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
        return self._op("detach_master", fields, dry_run=dry_run)

    def _report(self) -> dict[str, Any]:
        return self._op("linkage_report", {})

    def _snapshot(self) -> dict[Path, bytes]:
        return {path: path.read_bytes() for path in self.tasks.rglob("*") if path.is_file()}

    def _sprint(self) -> TaskDocument:
        return read_task_doc(self.tasks / "sprint" / "task.json")

    # --- attach ---------------------------------------------------------------

    def test_attach_writes_all_four_artifacts_atomically(self) -> None:
        self._graph_ful_sprint()
        self._write_master(MASTER_C)  # nature-less

        result = self._attach(
            {
                "masterRef": MASTER_C.model_dump(),
                "number": "M3",
                "executionNature": "organizational",
                "judgmentId": "J-1",
            }
        )

        self.assertEqual(result["state"], "attached")
        self.assertEqual(result["graphNode"], "added")
        self.assertEqual(result["executionNatureAsserted"], True)
        self.assertEqual(len(result["documents"]), 2)
        sprint = self._sprint()
        row = next(row for row in sprint.subTasks if row.number == "M3")
        self.assertEqual(row.masterRef, MASTER_C)
        self.assertEqual(row.file, "")
        self.assertEqual(row.name, "master-c")
        self.assertIn("master-c", sprint.orchestrates)
        assert sprint.executionGraph is not None
        self.assertIn(MASTER_C, sprint.executionGraph.master_refs())
        self.assertEqual(
            read_task_doc(self.tasks / "master-c" / "task.json").executionNature,
            "organizational",
        )

    def test_attach_judgment_refusals_write_nothing(self) -> None:
        self._graph_ful_sprint()
        self._write_master(MASTER_C)  # nature-less
        attach = {
            "masterRef": MASTER_C.model_dump(),
            "number": "M3",
            "executionNature": "organizational",
        }
        before = self._snapshot()
        with self.assertRaisesRegex(TaskDocError, "nature-required"):
            self._attach(attach)  # nature-less master, no judgmentId
        with self.assertRaisesRegex(TaskDocError, "judgment-unknown"):
            self._attach({**attach, "judgmentId": "J-missing"})
        with self.assertRaisesRegex(TaskDocError, "judgmentId must not be blank"):
            self._attach({**attach, "judgmentId": "  "})
        self.assertEqual(before, self._snapshot())
        # Nature disagreement with an existing nature refuses, also without writes.
        self._write_master(MASTER_C, nature="atomic")
        before = self._snapshot()
        with self.assertRaisesRegex(TaskDocError, "nature-mismatch"):
            self._attach({**attach, "executionNature": "organizational", "judgmentId": "J-1"})
        self.assertEqual(before, self._snapshot())

    def test_attach_existing_nature_needs_no_nature_payload(self) -> None:
        self._graph_ful_sprint()
        self._write_master(MASTER_C, nature="atomic")
        result = self._attach({"masterRef": MASTER_C.model_dump(), "number": "M3"})
        self.assertEqual(result["executionNatureAsserted"], False)
        self.assertEqual(self._sprint().orchestrates.count("master-c"), 1)

    def test_attach_target_and_uniqueness_refusals(self) -> None:
        self._graph_ful_sprint()
        self._write_master(MASTER_C, nature="atomic")
        write_task_doc(
            self.tasks / "other-sprint",
            _master(
                identity="OTHER",
                orchestrates=["master-a"],
                execution_graph={"nodes": [MASTER_A.model_dump()], "edges": []},
            ),
        )
        sprint = self._sprint().model_copy(
            update={"subTasks": [SubTaskRef(number="M1", name="master-a", masterRef=MASTER_A)]}
        )
        write_task_doc(self.tasks / "sprint", sprint)
        before = self._snapshot()
        other = TaskDocumentRef(repository=REPOSITORY, path="other-sprint/task.json")
        with self.assertRaisesRegex(TaskDocError, "target-is-sprint"):
            self._attach({"masterRef": other.model_dump(), "number": "M3"})
        with self.assertRaisesRegex(TaskDocError, "already-attached"):
            self._attach({"masterRef": MASTER_A.model_dump(), "number": "M3"})
        with self.assertRaisesRegex(TaskDocError, "self-attach"):
            self._attach({"masterRef": SPRINT.model_dump(), "number": "M3"})
        with self.assertRaisesRegex(TaskDocError, "cross-repo"):
            self._attach(
                {
                    "masterRef": {"repository": "other-repo", "path": "master-c/task.json"},
                    "number": "M3",
                }
            )
        with self.assertRaisesRegex(TaskDocError, "row-number-taken"):
            self._attach({"masterRef": MASTER_C.model_dump(), "number": "M1"})
        self.assertEqual(before, self._snapshot())

    def test_attach_dry_run_previews_and_writes_nothing(self) -> None:
        self._graph_ful_sprint()
        self._write_master(MASTER_C)
        before = self._snapshot()
        preview = self._attach(
            {
                "masterRef": MASTER_C.model_dump(),
                "number": "M3",
                "executionNature": "atomic",
                "judgmentId": "J-2",
            },
            dry_run=True,
        )
        self.assertEqual(preview["state"], "would-attach")
        self.assertTrue(preview["dryRun"])
        self.assertEqual(len(preview["documents"]), 2)
        rendered = preview["documents"][0]["rendered"]
        self.assertIn("[**master-c**](../master-c/task.md)", rendered)
        self.assertEqual(before, self._snapshot())

    def test_attach_completed_row_requires_a_completed_master(self) -> None:
        self._graph_ful_sprint()
        self._write_master(MASTER_C, nature="atomic")
        with self.assertRaisesRegex(TaskDocError, "cannot mark master row"):
            self._attach(
                {"masterRef": MASTER_C.model_dump(), "number": "M3", "status": "Completed"}
            )
        self._write_master(MASTER_C, nature="atomic", status="Completed")
        result = self._attach(
            {"masterRef": MASTER_C.model_dump(), "number": "M3", "status": "Completed"}
        )
        self.assertEqual(result["state"], "attached")
        row = next(row for row in self._sprint().subTasks if row.number == "M3")
        self.assertEqual(row.status, "Completed")

    def test_graph_absent_attach_reports_deferred_fact(self) -> None:
        # L13 default: a graph-less sprint attaches without a graph node.
        self._write_master(MASTER_A, nature="organizational")
        self._write_master(MASTER_B, nature="atomic")
        self._write_master(MASTER_C)  # nature-less
        self._write_sprint(
            graph=None,
            rows=[
                SubTaskRef(number="M1", name="master-a", masterRef=MASTER_A),
                SubTaskRef(number="M2", name="master-b", masterRef=MASTER_B),
            ],
        )
        result = self._attach(
            {
                "masterRef": MASTER_C.model_dump(),
                "number": "M3",
                "executionNature": "atomic",
                "judgmentId": "J-1",
            }
        )
        self.assertEqual(result["graphNode"], "deferred-no-graph-default")
        sprint = self._sprint()
        self.assertIsNone(sprint.executionGraph)
        self.assertEqual(
            [row.masterRef for row in sprint.subTasks if row.number == "M3"], [MASTER_C]
        )
        self.assertEqual(sprint.orchestrates, ["master-a", "master-b", "master-c"])
        self.topology.validate_sprint_linkage(SPRINT)
        self.assertEqual(self._report()["linkageFacts"], [])

    # --- detach ---------------------------------------------------------------

    def test_detach_refuses_while_edges_touch_the_node(self) -> None:
        self._graph_ful_sprint()
        self._write_master(MASTER_C, nature="atomic")
        self._attach({"masterRef": MASTER_C.model_dump(), "number": "M3"})
        self._op(
            "author_execution_graph",
            {
                "mutations": [
                    {
                        "op": "add_edge",
                        "predecessor": MASTER_A.model_dump(),
                        "successor": MASTER_C.model_dump(),
                        "reason": "A before C",
                        "judgmentId": "J-1",
                    }
                ]
            },
        )
        before = self._snapshot()
        with self.assertRaisesRegex(TaskDocError, "node-in-use"):
            self._detach({"masterRef": MASTER_C.model_dump()})
        self.assertEqual(before, self._snapshot())

    def test_detach_succeeds_clean_and_never_deletes_files(self) -> None:
        self._graph_ful_sprint()
        self._write_master(MASTER_C, nature="atomic")
        self._attach({"masterRef": MASTER_C.model_dump(), "number": "M3"})
        preview = self._detach({"masterRef": MASTER_C.model_dump()}, dry_run=True)
        self.assertEqual(preview["state"], "would-detach")
        self.assertEqual(preview["removedSubtask"], "M3")
        self.assertEqual(preview["removedOrchestrates"], ["master-c"])
        self.assertEqual(preview["removedGraphNodes"], 1)
        result = self._detach({"masterRef": MASTER_C.model_dump()})
        self.assertEqual(result["state"], "detached")
        sprint = self._sprint()
        self.assertNotIn("master-c", sprint.orchestrates)
        self.assertEqual([row.number for row in sprint.subTasks], [])
        assert sprint.executionGraph is not None
        self.assertNotIn(MASTER_C, sprint.executionGraph.master_refs())
        # The master document itself is never deleted.
        self.assertTrue((self.tasks / "master-c" / "task.json").is_file())
        self.assertTrue((self.tasks / "master-c" / "task.md").is_file())
        self.topology.validate_execution_topology(SPRINT)

    def test_detach_refusals(self) -> None:
        self._graph_ful_sprint()
        with self.assertRaisesRegex(TaskDocError, "not-attached"):
            self._detach({"masterRef": MASTER_C.model_dump()})
        # Detaching the last master would empty the graph.
        self._write_master(MASTER_A, nature="atomic")
        write_task_doc(
            self.tasks / "sprint",
            _master(
                identity="SPRINT",
                orchestrates=["master-a"],
                execution_graph={"nodes": [MASTER_A.model_dump()], "edges": []},
            ).model_copy(
                update={
                    "integrationBranch": "super",
                    "sections": [_register_section("J-1")],
                    "subTasks": [SubTaskRef(number="M1", name="master-a", masterRef=MASTER_A)],
                }
            ),
        )
        with self.assertRaisesRegex(TaskDocError, "graph-empty"):
            self._detach({"masterRef": MASTER_A.model_dump()})

    def test_detach_on_a_graph_less_sprint(self) -> None:
        self._write_master(MASTER_A, nature="organizational")
        self._write_master(MASTER_B, nature="atomic")
        self._write_sprint(
            graph=None,
            rows=[
                SubTaskRef(number="M1", name="master-a", masterRef=MASTER_A),
                SubTaskRef(number="M2", name="master-b", masterRef=MASTER_B),
            ],
        )
        result = self._detach({"masterRef": MASTER_B.model_dump()})
        self.assertEqual(result["state"], "detached")
        self.assertEqual(result["removedGraphNodes"], 0)
        sprint = self._sprint()
        self.assertEqual(sprint.orchestrates, ["master-a"])
        self.assertEqual([row.number for row in sprint.subTasks], ["M1"])
        self.assertIsNone(sprint.executionGraph)
        self.topology.validate_sprint_linkage(SPRINT)

    # --- linkage report ---------------------------------------------------------

    def test_report_flags_m16_shape(self) -> None:
        """Row-without-membership: the seat row names a master the sprint never commands."""
        self._write_master(MASTER_A, nature="organizational")
        self._write_master(MASTER_B, nature="atomic")
        self._write_master(MASTER_C, status="Completed")  # the rc7 shape: completed, uncommanded
        write_task_doc(
            self.tasks / "sprint",
            _seat_doc("03_manage-master-c", ["../master-c/task.json"]),
        )
        self._write_sprint(
            rows=[
                SubTaskRef(number="M1", name="master-a", masterRef=MASTER_A),
                SubTaskRef(number="M3", name="manage-c", file="03_manage-master-c.md"),
            ],
        )
        facts = self._report()["linkageFacts"]
        kinds = {(fact["kind"], fact.get("master")) for fact in facts}
        self.assertIn(("seat-doc-row", MASTER_C.key), kinds)
        self.assertIn(("row-without-membership", MASTER_C.key), kinds)
        # The typed row is clean; master-b is commanded but has no row at all.
        self.assertIn(("membership-without-row", MASTER_B.key), kinds)
        self.assertNotIn(("row-without-membership", MASTER_A.key), kinds)

    def test_report_flags_ledger_reconciliation_shape(self) -> None:
        """A decision-attached but structurally uncommanded master is reported."""
        self._write_master(MASTER_A, nature="organizational")
        self._write_master(MASTER_B, nature="atomic")
        self._write_master(MASTER_C)
        self._write_sprint(
            rows=[SubTaskRef(number="M1", name="master-a", masterRef=MASTER_A)],
            decisions=[
                {
                    "at": "2026-08-15T02:15:00+00:00",
                    "decision": "Attach master-c as a bounded commanded repair master.",
                    "rationale": "Ledger reconciliation must land under sprint command.",
                }
            ],
        )
        facts = self._report()["linkageFacts"]
        kinds = {(fact["kind"], fact.get("master")) for fact in facts}
        self.assertIn(("uncommanded-master", MASTER_C.key), kinds)
        self.assertIn(("membership-without-row", MASTER_B.key), kinds)
        decision_fact = next(fact for fact in facts if fact["kind"] == "uncommanded-master")
        self.assertEqual(decision_fact["decisionAt"], "2026-08-15T02:15:00+00:00")

    def test_old_shape_sprint_validates_with_facts_only(self) -> None:
        """L14-R7: a legacy seat-row sprint (the IAS shape) reads clean, facts only."""
        self._write_master(MASTER_A, nature="organizational")
        self._write_master(MASTER_B, nature="atomic")
        write_task_doc(
            self.tasks / "sprint", _seat_doc("01_manage-master-a", ["../master-a/task.json"])
        )
        write_task_doc(
            self.tasks / "sprint", _seat_doc("02_manage-master-b", ["../master-b/task.json"])
        )
        self._write_sprint(
            rows=[
                SubTaskRef(number="M1", name="a", file="01_manage-master-a.md"),
                SubTaskRef(number="M2", name="b", file="02_manage-master-b.md"),
            ],
            graph={"nodes": [MASTER_A.model_dump(), MASTER_B.model_dump()], "edges": []},
        )
        # Hard validation stays green: legacy rows carry no masterRef, nothing hard-fails.
        masters = self.topology.validate_execution_topology(SPRINT)
        self.assertEqual([master.ref for master in masters], [MASTER_A, MASTER_B])
        facts = self._report()["linkageFacts"]
        self.assertEqual(
            {fact["kind"] for fact in facts},
            {"seat-doc-row", "slug-only-membership"},
        )
        pairs = {(fact["kind"], fact.get("master")) for fact in facts}
        self.assertIn(("slug-only-membership", MASTER_A.key), pairs)
        self.assertIn(("slug-only-membership", MASTER_B.key), pairs)

    def test_get_surfaces_linkage_facts_for_sprints_only(self) -> None:
        self._graph_ful_sprint()
        self._write_master(MASTER_C, nature="atomic")
        self._write_sprint(
            graph={
                "nodes": [
                    MASTER_A.model_dump(),
                    MASTER_B.model_dump(),
                    MASTER_C.model_dump(),
                ],
                "edges": [],
            },
            orchestrates=["master-a", "master-b", "master-c"],
            rows=[
                SubTaskRef(number="M1", name="master-a", masterRef=MASTER_A),
                SubTaskRef(number="M2", name="master-b", masterRef=MASTER_B),
                SubTaskRef(number="M3", name="master-c", masterRef=MASTER_C),
            ],
        )
        result = task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id=REPOSITORY, task_name="sprint"),
            operation="get",
        )
        self.assertEqual(result["linkageFacts"], [])
        masters = task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id=REPOSITORY, task_name="master-a"),
            operation="get",
        )
        self.assertNotIn("linkageFacts", masters)

    def test_report_requires_a_sprint(self) -> None:
        self._write_master(MASTER_A, nature="atomic")
        with self.assertRaisesRegex(TaskDocError, "requires an orchestration sprint"):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(repo_id=REPOSITORY, task_name="master-a"),
                operation="linkage_report",
                edit=TaskDocEdit(fields={}),
            )

    # --- row completion through set_subtask ------------------------------------

    def test_completed_linked_row_validates_the_master_document(self) -> None:
        self._graph_ful_sprint()
        self._write_master(MASTER_C, nature="atomic")
        self._attach({"masterRef": MASTER_C.model_dump(), "number": "M3"})
        with self.assertRaisesRegex(TaskDocError, "cannot mark master row"):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(repo_id=REPOSITORY, task_name="sprint"),
                operation="set_subtask",
                edit=TaskDocEdit(subtask={"number": "M3", "status": "Completed"}),
            )
        self._write_master(MASTER_C, nature="atomic", status="Completed")
        task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id=REPOSITORY, task_name="sprint"),
            operation="set_subtask",
            edit=TaskDocEdit(subtask={"number": "M3", "status": "Completed"}),
        )
        row = next(row for row in self._sprint().subTasks if row.number == "M3")
        self.assertEqual(row.status, "Completed")
        # The row keeps its typed link and gains no leaf file.
        self.assertEqual(row.masterRef, MASTER_C)
        self.assertEqual(row.file, "")

    # --- schema -----------------------------------------------------------------

    def test_seats_schema_rules(self) -> None:
        seats = [
            {"role": "architect", "label": "Design seat", "identity": "chat-1", "state": "active"},
            {"role": "orchestrator", "state": "planned"},
            {"role": "strategist", "state": "retired"},
            {"role": "strategist", "state": "active"},  # retired + active is legal
        ]
        self._graph_ful_sprint()
        self._write_sprint(seats=seats)
        sprint = self._sprint()
        self.assertEqual(
            [seat.role for seat in sprint.seats],
            ["architect", "orchestrator", "strategist", "strategist"],
        )
        with self.assertRaises(ValidationError):
            SprintSeat(role="worker")  # a leaf role is not a sprint seat
        with self.assertRaises(ValidationError):
            SprintSeat(role="orchestrator", identity="  ")
        with self.assertRaisesRegex(ValidationError, "unique among planned/active"):
            TaskDocument.model_validate(
                {
                    "id": "S",
                    "slug": "S",
                    "title": "S",
                    "kind": "master",
                    "repo": REPOSITORY,
                    "createdAt": "2026-08-19T00:00:00+00:00",
                    "orchestrates": ["master-a"],
                    "seats": [{"role": "architect"}, {"role": "architect", "state": "planned"}],
                }
            )
        with self.assertRaisesRegex(ValidationError, "seats belong only"):
            TaskDocument.model_validate(
                {
                    "id": "M",
                    "slug": "M",
                    "title": "M",
                    "kind": "master",
                    "repo": REPOSITORY,
                    "createdAt": "2026-08-19T00:00:00+00:00",
                    "seats": [{"role": "architect"}],
                }
            )

    def test_master_ref_row_requires_a_sprint(self) -> None:
        with self.assertRaisesRegex(ValidationError, "masterRef belongs only"):
            TaskDocument.model_validate(
                {
                    "id": "M",
                    "slug": "M",
                    "title": "M",
                    "kind": "master",
                    "repo": REPOSITORY,
                    "createdAt": "2026-08-19T00:00:00+00:00",
                    "subTasks": [
                        {"number": "L1", "name": "leaf", "masterRef": MASTER_A.model_dump()}
                    ],
                }
            )

    # --- rendering ----------------------------------------------------------------

    def test_render_links_and_generated_master_index(self) -> None:
        self._graph_ful_sprint()
        sprint = self._sprint().model_copy(
            update={
                "subTasks": [
                    SubTaskRef(number="M1", name="master-a", masterRef=MASTER_A),
                    SubTaskRef(number="M2", name="legacy", file="02_manage-master-b.md"),
                ]
            }
        )
        rendered = render_markdown(sprint)
        # Typed rows link the master doc; legacy rows keep the code span (L14-R1).
        self.assertIn("[**master-a**](../master-a/task.md)", rendered)
        self.assertIn("**legacy** · `02_manage-master-b.md`", rendered)
        # No subTasks-kind section: the generated master index makes rows visible.
        self.assertIn("## Master Index", rendered)

    def test_render_explicit_subtasks_section_suppresses_the_generated_index(self) -> None:
        self._graph_ful_sprint()
        sprint = self._sprint().model_copy(
            update={
                "subTasks": [SubTaskRef(number="M1", name="master-a", masterRef=MASTER_A)],
                "sections": [Section(kind="subTasks", heading="Masters")],
            }
        )
        rendered = render_markdown(sprint)
        self.assertNotIn("## Master Index", rendered)
        self.assertIn("## Masters", rendered)
        self.assertIn("[**master-a**](../master-a/task.md)", rendered)

    def test_render_seats_header_block(self) -> None:
        self._graph_ful_sprint()
        sprint = self._sprint().model_copy(
            update={
                "seats": [
                    SprintSeat(role="architect", label="Design seat", state="active"),
                    SprintSeat(role="orchestrator", identity="chat-9"),
                ]
            }
        )
        rendered = render_markdown(sprint)
        self.assertIn("**Seats:**", rendered)
        self.assertIn("- **architect** (active) — Design seat", rendered)
        self.assertIn("- **orchestrator** (planned) · `chat-9`", rendered)
        self.assertNotIn("**Seats:**", render_markdown(self._sprint()))

    # --- registration --------------------------------------------------------------

    def test_operations_are_registered_and_documented(self) -> None:
        for operation in ("attach_master", "detach_master", "linkage_report"):
            self.assertIn(operation, VALID_OPERATIONS)
        source = Path(registration_tasks.__file__).read_text(encoding="utf-8")
        for operation in ("'attach_master'", "'detach_master'", "'linkage_report'"):
            self.assertIn(operation, source)


class SprintLinkageEdgeTests(unittest.TestCase):
    """Refusal and edge paths of the linkage module (diff-coverage repair)."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.coord = Path(self.temp.name)
        self.tasks = self.coord / "tasks" / REPOSITORY
        self.tasks.mkdir(parents=True)
        self.code = self.coord / "code"
        init_repo(self.code)
        git(self.code, "branch", "super", "main")
        self.cfg = _config(self.coord, self.code)
        self.topology = TaskDocumentTopology(self.coord)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_master(self, ref: TaskDocumentRef, *, nature: str | None = None) -> None:
        folder = Path(ref.path).parent.name
        write_task_doc(self.tasks / folder, _master(identity=folder, execution_nature=nature))

    def _write_sprint(
        self,
        *,
        graph: dict[str, Any] | None = None,
        rows: list[SubTaskRef] | None = None,
        orchestrates: list[str] | None = None,
    ) -> None:
        write_task_doc(
            self.tasks / "sprint",
            _master(
                identity="SPRINT",
                orchestrates=(
                    orchestrates if orchestrates is not None else ["master-a", "master-b"]
                ),
                execution_graph=graph,
            ).model_copy(
                update={
                    "integrationBranch": "super",
                    "sections": [_register_section("J-1", "J-2")],
                    "subTasks": rows or [],
                }
            ),
        )

    def _graph_ful_sprint(self) -> None:
        self._write_master(MASTER_A, nature="organizational")
        self._write_master(MASTER_B, nature="atomic")
        self._write_sprint(
            graph={"nodes": [MASTER_A.model_dump(), MASTER_B.model_dump()], "edges": []}
        )

    def _op(
        self,
        operation: str,
        fields: dict[str, Any],
        *,
        dry_run: bool = False,
        slug: str | None = None,
    ) -> dict[str, Any]:
        return task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id=REPOSITORY, task_name="sprint", slug=slug),
            operation=operation,
            edit=TaskDocEdit(fields=fields),
            call=TaskDocCall(dry_run=dry_run),
        )

    def _attach(self, fields: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return self._op("attach_master", fields, **kwargs)

    def _detach(self, fields: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return self._op("detach_master", fields, **kwargs)

    def _snapshot(self) -> dict[Path, bytes]:
        return {path: path.read_bytes() for path in self.tasks.rglob("*") if path.is_file()}

    def _sprint(self) -> TaskDocument:
        return read_task_doc(self.tasks / "sprint" / "task.json")

    def test_payload_trimming_rules(self) -> None:
        base: dict[str, Any] = {"masterRef": MASTER_C.model_dump(), "number": "M3"}
        payload = _AttachMasterPayload.model_validate({**base, "judgmentId": None})
        self.assertIsNone(payload.judgmentId)
        with self.assertRaisesRegex(ValidationError, "nonblank row number"):
            _AttachMasterPayload.model_validate({**base, "number": "  "})
        payload = _AttachMasterPayload.model_validate({**base, "name": None})
        self.assertIsNone(payload.name)
        with self.assertRaisesRegex(ValidationError, "row name must not be blank"):
            _AttachMasterPayload.model_validate({**base, "name": " "})

    def test_attach_writes_scope_and_custom_name(self) -> None:
        self._graph_ful_sprint()
        self._write_master(MASTER_C, nature="atomic")
        self._attach(
            {
                "masterRef": MASTER_C.model_dump(),
                "number": "M3",
                "name": "C master",
                "scope": "Frontier",
                "judgmentId": None,
            }
        )
        row = next(row for row in self._sprint().subTasks if row.number == "M3")
        self.assertEqual(row.name, "C master")
        self.assertEqual(row.scope, "Frontier")

    def test_attach_target_resolution_refusals(self) -> None:
        self._graph_ful_sprint()
        write_task_doc(self.tasks / "leaf-x", _seat_doc("01_leaf", []))
        before = self._snapshot()
        with self.assertRaisesRegex(TaskDocError, "task-document-not-found"):
            self._attach(
                {
                    "masterRef": {"repository": REPOSITORY, "path": "ghost/task.json"},
                    "number": "M3",
                }
            )
        with self.assertRaisesRegex(TaskDocError, "target-not-a-master"):
            self._attach(
                {
                    "masterRef": {"repository": REPOSITORY, "path": "leaf-x/01_leaf.json"},
                    "number": "M3",
                }
            )
        self.assertEqual(before, self._snapshot())

    def test_attach_already_attached_variants(self) -> None:
        self._graph_ful_sprint()
        self._write_master(MASTER_C, nature="atomic")
        self._attach({"masterRef": MASTER_C.model_dump(), "number": "M3"})
        with self.assertRaisesRegex(TaskDocError, "a typed row already links"):
            self._attach({"masterRef": MASTER_C.model_dump(), "number": "M4"})
        # Drift shape: the graph already places the master while orchestrates/rows do not.
        self._write_sprint(
            graph={
                "nodes": [
                    MASTER_A.model_dump(),
                    MASTER_B.model_dump(),
                    MASTER_C.model_dump(),
                ],
                "edges": [],
            },
            rows=[],
        )
        with self.assertRaisesRegex(TaskDocError, "executionGraph already places"):
            self._attach({"masterRef": MASTER_C.model_dump(), "number": "M4"})
        # Alias membership without a typed row: refused by the orchestrates check.
        self._write_sprint(
            graph={"nodes": [MASTER_A.model_dump(), MASTER_B.model_dump()], "edges": []},
            rows=[],
        )
        with self.assertRaisesRegex(TaskDocError, "orchestrates already commands"):
            self._attach({"masterRef": MASTER_A.model_dump(), "number": "M4"})

    def test_attach_requires_an_existing_document_and_confinement(self) -> None:
        self._graph_ful_sprint()
        self._write_master(MASTER_C, nature="atomic")
        with self.assertRaisesRegex(TaskDocError, "task document not found"):
            self._attach({"masterRef": MASTER_C.model_dump(), "number": "M3"}, slug="missing")
        outside = self.coord / "outside"
        write_task_doc(outside, _master(identity="SPRINT", orchestrates=["master-a"]))
        with self.assertRaisesRegex(TaskDocError, "outside tasks/agents-remember"):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(
                    repo_id=REPOSITORY,
                    contract_path=(outside / "series-contract.md").as_posix(),
                ),
                operation="attach_master",
                edit=TaskDocEdit(fields={"masterRef": MASTER_C.model_dump(), "number": "M3"}),
            )

    def test_detach_cross_repo_and_duplicate_rows(self) -> None:
        self._graph_ful_sprint()
        with self.assertRaisesRegex(TaskDocError, "cross-repo"):
            self._detach({"masterRef": {"repository": "other-repo", "path": "master-c/task.json"}})
        self._write_sprint(
            graph={"nodes": [MASTER_A.model_dump(), MASTER_B.model_dump()], "edges": []},
            rows=[
                SubTaskRef(number="M1", name="a", masterRef=MASTER_A),
                SubTaskRef(number="M1b", name="a again", masterRef=MASTER_A),
            ],
        )
        with self.assertRaisesRegex(TaskDocError, "row-duplicate"):
            self._detach({"masterRef": MASTER_A.model_dump()})

    def test_detach_tolerates_a_deleted_master_document(self) -> None:
        self._write_master(MASTER_A, nature="organizational")
        self._write_master(MASTER_C)
        self._write_sprint(
            graph=None,
            orchestrates=["master-a", "master-c"],
            rows=[
                SubTaskRef(number="M1", name="a", masterRef=MASTER_A),
                SubTaskRef(number="M3", name="c", masterRef=MASTER_C),
            ],
        )
        (self.tasks / "master-c" / "task.json").unlink()
        result = self._detach({"masterRef": MASTER_C.model_dump()})
        self.assertEqual(result["masterResolved"], False)
        self.assertEqual(result["removedOrchestrates"], ["master-c"])
        self.assertEqual(self._sprint().orchestrates, ["master-a"])

    def test_detach_propagates_resolution_errors(self) -> None:
        self._write_master(MASTER_A, nature="organizational")
        write_task_doc(
            self.tasks / "master-c",
            _master(identity="master-c").model_copy(update={"repo": "other-repo"}),
        )
        self._write_sprint(
            graph=None,
            orchestrates=["master-a", "master-c"],
            rows=[
                SubTaskRef(number="M1", name="a", masterRef=MASTER_A),
                SubTaskRef(number="M3", name="c", masterRef=MASTER_C),
            ],
        )
        with self.assertRaisesRegex(TaskDocError, "task-document-repo-mismatch"):
            self._detach({"masterRef": MASTER_C.model_dump()})

    def test_attach_validates_the_candidate_against_existing_drift(self) -> None:
        self._write_master(MASTER_A, nature="organizational")
        self._write_master(MASTER_B, nature="atomic")
        self._write_master(MASTER_C, nature="atomic")
        self._write_sprint(
            graph={"nodes": [MASTER_A.model_dump(), MASTER_B.model_dump()], "edges": []},
            orchestrates=["master-a", "master-b", "master-zzz"],
        )
        before = self._snapshot()
        with self.assertRaisesRegex(TaskDocError, "membership-invalid"):
            self._attach({"masterRef": MASTER_C.model_dump(), "number": "M3"})
        self.assertEqual(before, self._snapshot())

    def test_attach_wraps_publication_authority_failures(self) -> None:
        self._graph_ful_sprint()
        self._write_master(MASTER_C, nature="atomic")
        with (
            mock.patch.object(
                sprint_linkage,
                "require_topology_migration_authority",
                side_effect=RuntimeError("no authority"),
            ),
            self.assertRaisesRegex(TaskDocError, "no authority"),
        ):
            self._attach({"masterRef": MASTER_C.model_dump(), "number": "M3"}, dry_run=True)

    def test_linkage_facts_unit_edges(self) -> None:
        self._graph_ful_sprint()
        outside_path = self.coord / "outside" / "task.json"
        self.assertIsNone(
            linkage_facts_for_get(self.coord, REPOSITORY, outside_path, self._sprint())
        )
        ghost = TaskDocumentRef(repository=REPOSITORY, path="ghost/task.json")
        facts = collect_linkage_facts(self.topology, ghost)
        self.assertEqual([fact["kind"] for fact in facts], ["sprint-scan-failed"])

    def test_report_seat_row_edge_shapes(self) -> None:
        self._graph_ful_sprint()
        write_task_doc(
            self.tasks / "sprint",
            _seat_doc("06_manage-master-a", ["../notes/x.md", "../master-a/task.json"]),
        )
        write_task_doc(
            self.tasks / "sprint",
            _seat_doc("07_manage-master-y", ["../notes/x.md"]),
        )
        self._write_sprint(
            graph={"nodes": [MASTER_A.model_dump(), MASTER_B.model_dump()], "edges": []},
            rows=[
                SubTaskRef(number="M1", name="a", masterRef=MASTER_A),
                SubTaskRef(number="M2", name="b", masterRef=MASTER_B),
                SubTaskRef(number="M3", name="plain", file="plain.md"),
                SubTaskRef(number="M4", name="z", file="05_manage-master-z.md"),
                SubTaskRef(number="M5", name="a-seat", file="06_manage-master-a.md"),
                SubTaskRef(number="M6", name="y-seat", file="07_manage-master-y.md"),
            ],
        )
        facts = self._op("linkage_report", {})["linkageFacts"]
        self.assertEqual([fact["kind"] for fact in facts], ["seat-doc-row"] * 3)
        by_number = {fact["number"]: fact for fact in facts}
        self.assertNotIn("master", by_number["M4"])  # seat doc absent
        self.assertEqual(by_number["M5"]["master"], MASTER_A.key)  # later reference wins
        self.assertNotIn("master", by_number["M6"])  # no master reference

    def test_validate_sprint_linkage_refusal_branches(self) -> None:
        self._write_master(MASTER_A, nature="organizational")
        self._write_master(MASTER_B, nature="atomic")
        other_repo_row = SubTaskRef(
            number="M1",
            name="x",
            masterRef=TaskDocumentRef(repository="other-repo", path="master-a/task.json"),
        )
        self._write_sprint(orchestrates=["master-a"], rows=[other_repo_row])
        with self.assertRaisesRegex(TaskDocumentRefError, "outside the sprint repository"):
            self.topology.validate_sprint_linkage(SPRINT)
        write_task_doc(
            self.tasks / "other-sprint",
            _master(identity="OTHER", orchestrates=["master-a"]),
        )
        sprint_row = SubTaskRef(
            number="M1",
            name="x",
            masterRef=TaskDocumentRef(repository=REPOSITORY, path="other-sprint/task.json"),
        )
        self._write_sprint(orchestrates=["master-a"], rows=[sprint_row])
        with self.assertRaisesRegex(TaskDocumentRefError, "must name a commanded master document"):
            self.topology.validate_sprint_linkage(SPRINT)
        self._write_sprint(
            orchestrates=["master-a"],
            rows=[
                SubTaskRef(number="M1", name="a", masterRef=MASTER_A),
                SubTaskRef(number="M2", name="a again", masterRef=MASTER_A),
            ],
        )
        with self.assertRaisesRegex(TaskDocumentRefError, "multiple rows link"):
            self.topology.validate_sprint_linkage(SPRINT)
        self._write_sprint(
            orchestrates=["master-a"],
            rows=[SubTaskRef(number="M1", name="b", masterRef=MASTER_B)],
        )
        with self.assertRaisesRegex(TaskDocumentRefError, "orchestrates does not command"):
            self.topology.validate_sprint_linkage(SPRINT)

    def test_completed_linked_row_requires_a_readable_master(self) -> None:
        self._write_master(MASTER_A, nature="organizational")
        self._write_sprint(
            graph=None,
            orchestrates=["master-a", "master-c"],
            rows=[SubTaskRef(number="M3", name="c", masterRef=MASTER_C)],
        )
        with self.assertRaisesRegex(TaskDocError, "cannot read the linked master"):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(repo_id=REPOSITORY, task_name="sprint"),
                operation="set_subtask",
                edit=TaskDocEdit(subtask={"number": "M3", "status": "Completed"}),
            )

    def test_seats_on_leaf_docs_and_explicit_none_identity(self) -> None:
        with self.assertRaisesRegex(ValidationError, "has no seats"):
            TaskDocument.model_validate(
                {
                    "id": "L1",
                    "slug": "l1",
                    "title": "leaf",
                    "kind": "subTask",
                    "repo": REPOSITORY,
                    "createdAt": "2026-08-19T00:00:00+00:00",
                    "seats": [{"role": "architect"}],
                }
            )
        seat = SprintSeat.model_validate({"role": "architect", "identity": None})
        self.assertIsNone(seat.identity)


if __name__ == "__main__":
    unittest.main()
