"""L15-era graph-authoring hygiene tests (split from ``test_task_execution_topology.py``).

The parent file exceeded the 1200-line hard limit after the L15 gate-repair test
additions; the L15-era model/authoring tests moved here with identical names and
assertions. The scratch harness is replicated locally: the split deliberately keeps
this module self-contained, because a TestCase subclass would make pytest re-collect
the parent's tests through ``dir()`` and duplicate the whole suite.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.application.task_docs.task_execution_topology import (
    ExecutionTopologyEditRequest,
    ExecutionTopologyError,
    _apply_move_leaf,
    _edit_emits_topology_schema,
    _GraphDraft,
    _MoveLeafMutation,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import (
    Section,
    SprintExecutionEdge,
    SprintExecutionEndpoint,
    SprintExecutionGraph,
    SprintExecutionNode,
    read_task_doc,
    write_task_doc,
)
from agents_remember.tasks.document import _find_cycle_members
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.tasks.serving_preflight import TopologyServingBuildError
from pydantic import ValidationError
from test_task_execution_topology import (
    _JUDGMENT_HEADER,
    MASTER_A,
    MASTER_B,
    REPOSITORY,
    _config,
    _graph,
    _judgment_row,
    _master,
)
from test_worktree_support import git, init_repo


class ExecutionGraphSchemaL15Tests(unittest.TestCase):
    def test_edge_without_a_judgment_id_parses_to_none(self) -> None:
        # judgmentId=None is passed EXPLICITLY: an omitted field never runs the
        # field validator, so the None branch of _trim_nonblank_judgment_id would
        # stay uncovered.
        edge = SprintExecutionEdge(
            predecessor=MASTER_A, successor=MASTER_B, reason="x", judgmentId=None
        )
        self.assertIsNone(edge.judgmentId)

    def test_edge_blank_judgment_id_after_strip_is_refused(self) -> None:
        with self.assertRaisesRegex(ValidationError, "judgmentId must not be blank"):
            SprintExecutionEdge(
                predecessor=MASTER_A,
                successor=MASTER_B,
                reason="x",
                judgmentId="   ",
            )

    def test_cycle_refusal_names_the_cycle_members(self) -> None:
        with self.assertRaisesRegex(
            ValidationError, r"cycle members: .*task\.json -> .*task\.json"
        ):
            SprintExecutionGraph.model_validate(
                {
                    "nodes": [MASTER_A.model_dump(), MASTER_B.model_dump()],
                    "edges": [*_graph()["edges"], *_graph(reverse=True)["edges"]],
                }
            )

    def test_cycle_search_backtracks_through_dead_end_seeds(self) -> None:
        c = SprintExecutionNode(ref=TaskDocumentRef(repository=REPOSITORY, path="c/task.json"))
        d = SprintExecutionNode(ref=TaskDocumentRef(repository=REPOSITORY, path="d/task.json"))
        a = SprintExecutionNode(ref=MASTER_A)
        b = SprintExecutionNode(ref=MASTER_B)
        graph = SprintExecutionGraph.model_construct(
            nodes=[c, d, a, b],
            edges=[
                SprintExecutionEdge(predecessor=c.ref, successor=d.ref, reason="x"),
                SprintExecutionEdge(predecessor=a.ref, successor=b.ref, reason="y"),
                SprintExecutionEdge(predecessor=b.ref, successor=a.ref, reason="z"),
            ],
        )
        # Seeding from c descends into the dead-end d (backtrack), then the seed
        # loop finds the a <-> b cycle; d is skipped as already visited.
        cycle = _find_cycle_members(graph, [c, d, a, b])
        self.assertEqual([node.ref for node in cycle], [MASTER_A, MASTER_B])

    def test_cycle_search_returns_the_residual_when_no_seed_reaches_a_cycle(
        self,
    ) -> None:
        c = SprintExecutionNode(ref=TaskDocumentRef(repository=REPOSITORY, path="c/task.json"))
        d = SprintExecutionNode(ref=TaskDocumentRef(repository=REPOSITORY, path="d/task.json"))
        graph = SprintExecutionGraph.model_construct(
            nodes=[c, d],
            edges=[SprintExecutionEdge(predecessor=c.ref, successor=d.ref, reason="x")],
        )
        # Residual restricted to c: its only successor d is outside the residual
        # set, so no seed reaches a cycle and the fallback returns the residual.
        self.assertEqual(_find_cycle_members(graph, [c]), [c])

    def test_cycle_search_with_an_empty_residual_returns_it(self) -> None:
        graph = SprintExecutionGraph.model_construct(
            nodes=[
                SprintExecutionNode(ref=MASTER_A),
                SprintExecutionNode(ref=MASTER_B),
            ],
            edges=[],
        )
        self.assertEqual(_find_cycle_members(graph, []), [])


class ExecutionTopologyL15Tests(unittest.TestCase):
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

    def _write_legacy(self, *, register: bool = True) -> None:
        sections = (
            [
                Section(
                    kind="freeform",
                    heading="Judgment Register (canonical judgment authority)",
                    body="\n".join(
                        [
                            _JUDGMENT_HEADER,
                            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                            _judgment_row("J-nature-a"),
                            _judgment_row("J-nature-b"),
                            _judgment_row("J-edge"),
                        ]
                    ),
                )
            ]
            if register
            else []
        )
        write_task_doc(
            self.tasks / "sprint",
            _master(identity="SPRINT", orchestrates=["master-a", "master-b"]).model_copy(
                update={"integrationBranch": "super", "sections": sections}
            ),
        )
        write_task_doc(self.tasks / "master-a", _master(identity="MASTER-A"))
        write_task_doc(self.tasks / "master-b", _master(identity="MASTER-B"))

    def _bootstrap_fields(self) -> dict[str, Any]:
        """The first-batch mutations that author a graph onto a graph-less sprint (L13)."""

        return {
            "mutations": [
                {"op": "add_node", "ref": MASTER_A.model_dump()},
                {"op": "add_node", "ref": MASTER_B.model_dump()},
                {
                    "op": "set_nature",
                    "ref": MASTER_A.model_dump(),
                    "executionNature": "organizational",
                    "judgmentId": "J-nature-a",
                },
                {
                    "op": "set_nature",
                    "ref": MASTER_B.model_dump(),
                    "executionNature": "atomic",
                    "judgmentId": "J-nature-b",
                },
                {
                    "op": "add_edge",
                    "predecessor": MASTER_A.model_dump(),
                    "successor": MASTER_B.model_dump(),
                    "reason": "Shared contract must land first.",
                    "judgmentId": "J-edge",
                },
            ]
        }

    def _task_doc(
        self,
        task_name: str,
        operation: str,
        *,
        fields: dict[str, Any],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id=REPOSITORY, task_name=task_name),
            operation=operation,
            edit=TaskDocEdit(fields=fields),
            call=TaskDocCall(dry_run=dry_run),
        )

    def _bootstrap(self, *, dry_run: bool = False) -> dict[str, Any]:
        return task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id=REPOSITORY, task_name="sprint"),
            operation="author_execution_graph",
            edit=TaskDocEdit(fields=self._bootstrap_fields()),
            call=TaskDocCall(dry_run=dry_run),
        )

    def test_dry_run_authoring_writes_no_controlplane_lock_file(self) -> None:
        self._write_legacy()
        preview = self._bootstrap(dry_run=True)
        self.assertEqual(preview["state"], "would-author")
        self.assertFalse(
            (self.coord / "controlplane").exists(),
            "dry_run must not create the integration-authority lock file (F2)",
        )

    def test_segment_on_atomic_master_refuses_kind_rule_before_mutual_exclusion(
        self,
    ) -> None:
        self._write_legacy()
        fields = self._bootstrap_fields()
        fields["mutations"].append(
            {
                "op": "add_node",
                "ref": MASTER_B.model_dump(),
                "kind": "segment",
                "leafIds": ["L1"],
                "judgmentId": "J-edge",
            }
        )
        # The lump node for MASTER_B is still present: the node-kind rule must
        # fire before the lump/segment mutual-exclusion check masks it (F6).
        with self.assertRaisesRegex(TaskDocError, "lump nodes only"):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(repo_id=REPOSITORY, task_name="sprint"),
                operation="author_execution_graph",
                edit=TaskDocEdit(fields=fields),
            )

    def test_authoring_refuses_a_missing_edge_judgment_id_with_the_typed_refusal(
        self,
    ) -> None:
        self._write_legacy()
        fields = self._bootstrap_fields()
        fields["mutations"][4] = {
            "op": "add_edge",
            "predecessor": MASTER_A.model_dump(),
            "successor": MASTER_B.model_dump(),
            "reason": "Shared contract must land first.",
        }
        with self.assertRaisesRegex(TaskDocError, "task-execution-graph-judgment-required"):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(repo_id=REPOSITORY, task_name="sprint"),
                operation="author_execution_graph",
                edit=TaskDocEdit(fields=fields),
            )

    def test_authoring_refuses_an_unresolvable_segment_ref_with_a_typed_error(
        self,
    ) -> None:
        self._write_legacy()
        fields = self._bootstrap_fields()
        ghost = TaskDocumentRef(repository=REPOSITORY, path="ghost/task.json")
        fields["mutations"].append(
            {
                "op": "add_node",
                "ref": ghost.model_dump(),
                "kind": "segment",
                "leafIds": ["L1"],
                "judgmentId": "J-edge",
            }
        )
        # The unresolvable ref must surface as the typed membership refusal that
        # names it (L15-FIX-1), never a raw KeyError from the node-kind scan.
        with self.assertRaisesRegex(TaskDocError, "membership-invalid.*ghost"):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(repo_id=REPOSITORY, task_name="sprint"),
                operation="author_execution_graph",
                edit=TaskDocEdit(fields=fields),
            )

    def test_authoring_refuses_a_judgment_authored_by_a_non_authorized_role(
        self,
    ) -> None:
        sections = [
            Section(
                kind="freeform",
                heading="Judgment Register (canonical judgment authority)",
                body="\n".join(
                    [
                        _JUDGMENT_HEADER,
                        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                        _judgment_row("J-nature-a", author="worker"),
                        _judgment_row("J-nature-b", author="worker"),
                        _judgment_row("J-edge", author="worker"),
                    ]
                ),
            )
        ]
        write_task_doc(
            self.tasks / "sprint",
            _master(identity="SPRINT", orchestrates=["master-a", "master-b"]).model_copy(
                update={"integrationBranch": "super", "sections": sections}
            ),
        )
        write_task_doc(self.tasks / "master-a", _master(identity="MASTER-A"))
        write_task_doc(self.tasks / "master-b", _master(identity="MASTER-B"))
        fields = self._bootstrap_fields()
        with self.assertRaisesRegex(TaskDocError, "judgment-author-refused"):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(repo_id=REPOSITORY, task_name="sprint"),
                operation="author_execution_graph",
                edit=TaskDocEdit(fields=fields),
            )

    def test_enforce_preflight_refuses_a_topology_emitting_edit(self) -> None:
        write_task_doc(self.tasks / "master-a", _master(identity="MASTER-A"))
        with (
            mock.patch(
                "agents_remember.application.task_docs.task_execution_topology.require_serving_topology_schema",
                side_effect=TopologyServingBuildError(
                    "task-execution-topology-serving-build-unsupported: probe"
                ),
            ),
            self.assertRaisesRegex(TaskDocError, "serving-build-unsupported"),
        ):
            self._task_doc("master-a", "set_field", fields={"executionNature": "organizational"})
        self.assertIsNone(read_task_doc(self.tasks / "master-a" / "task.json").executionNature)

    def test_plain_master_create_skips_the_serving_preflight(self) -> None:
        created = self._task_doc(
            "master-c",
            "create",
            fields=_master(identity="MASTER-C").model_dump(by_alias=True),
        )
        self.assertEqual(created["status"], "planning")

    def test_edit_emits_topology_schema_detects_set_field_topology_keys(self) -> None:
        plain = _master(identity="MASTER-C")
        topology_edit = ExecutionTopologyEditRequest(
            coordination_root=self.coord,
            repo_id=REPOSITORY,
            task_root=self.tasks,
            operation="set_field",
            original=None,
            candidate=plain,
            fields={"executionNature": "atomic"},
        )
        self.assertTrue(_edit_emits_topology_schema(topology_edit))
        non_topology_edit = ExecutionTopologyEditRequest(
            coordination_root=self.coord,
            repo_id=REPOSITORY,
            task_root=self.tasks,
            operation="set_field",
            original=None,
            candidate=plain,
            fields={"title": "Renamed"},
        )
        self.assertFalse(_edit_emits_topology_schema(non_topology_edit))

    def test_serving_preflight_refuses_authoring_when_the_serving_build_is_unsupported(
        self,
    ) -> None:
        self._write_legacy()
        before = {path: path.read_bytes() for path in self.tasks.rglob("*") if path.is_file()}
        with (
            mock.patch(
                "agents_remember.application.task_docs.task_execution_topology.require_serving_topology_schema",
                side_effect=TopologyServingBuildError(
                    "task-execution-topology-serving-build-unsupported: probe"
                ),
            ),
            self.assertRaisesRegex(TaskDocError, "serving-build-unsupported"),
        ):
            self._bootstrap()
        self.assertEqual(
            before,
            {path: path.read_bytes() for path in self.tasks.rglob("*") if path.is_file()},
        )

    def test_move_leaf_that_retargets_an_edge_endpoint_refuses_with_named_cause(
        self,
    ) -> None:
        ref = TaskDocumentRef(repository=REPOSITORY, path="segmented/task.json")
        first = SprintExecutionNode(kind="segment", ref=ref, leafIds=["L1", "L2"])
        second = SprintExecutionNode(kind="segment", ref=ref, leafIds=["L3", "L4"])
        edge = SprintExecutionEdge(
            predecessor=SprintExecutionEndpoint(ref=ref, leafId="L3"),
            successor=TaskDocumentRef(repository=REPOSITORY, path="framework/task.json"),
            reason="gates",
            judgmentId="J-1",
        )
        draft = _GraphDraft(nodes=[first, second], edges=[edge], natures={})
        mutation = _MoveLeafMutation.model_validate(
            {
                "op": "move_leaf",
                "ref": ref,
                "leafId": "L3",
                "toSegment": "L1",
                "judgmentId": "J-1",
            }
        )
        with self.assertRaisesRegex(ExecutionTopologyError, "move-retargets-edge"):
            _apply_move_leaf(draft, mutation, {ref})
