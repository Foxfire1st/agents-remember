"""L11-R5/R6/R8: the incremental ``author_execution_graph`` operation.

Split from ``test_task_execution_topology.py`` (file-size limit); fixtures and shared
helpers are imported from it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from agents_remember.application.task_doc_tools import (
    VALID_OPERATIONS,
    TaskDocCall,
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.mcp.registration import tasks as registration_tasks
from agents_remember.tasks import Section, SubTaskRef, read_task_doc, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentTopology
from test_task_execution_topology import (
    MASTER_A,
    MASTER_B,
    MASTER_C,
    REPOSITORY,
    SPRINT,
    _config,
    _master,
)
from test_task_execution_topology_segments import _segment
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
        f"| {judgment_id} | leaf move | graph | segmentation=a | Explicit graph ruling. | "
        f"notes.md | {author} | high | |"
    )


class ExecutionGraphAuthoringTests(unittest.TestCase):
    """L11-R5/R6/R8: the incremental authoring operation."""

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

    def _write_fixture(
        self,
        *,
        register: bool = True,
        graph: dict[str, Any] | None = None,
        judgment_author: str = "strategist",
        leafs_a: list[str] | None = None,
    ) -> None:
        leafs = leafs_a or ["L1", "L2", "L3"]
        write_task_doc(
            self.tasks / "master-a",
            _master(identity="MASTER-A", execution_nature="organizational").model_copy(
                update={
                    "subTasks": [
                        SubTaskRef(number=leaf, name=leaf, file=f"{leaf.lower()}.md")
                        for leaf in leafs
                    ]
                }
            ),
        )
        write_task_doc(
            self.tasks / "master-b",
            _master(identity="MASTER-B", execution_nature="atomic").model_copy(
                update={"subTasks": [SubTaskRef(number="L1", name="L1", file="l1.md")]}
            ),
        )
        sections = []
        if register:
            sections.append(
                Section(
                    kind="freeform",
                    heading=JUDGMENT_HEADING,
                    body=_judgment_register([_judgment_row("J-1", judgment_author)]),
                )
            )
        write_task_doc(
            self.tasks / "sprint",
            _master(
                identity="SPRINT",
                orchestrates=["master-a", "master-b"],
                execution_graph=graph
                or {
                    "nodes": [MASTER_A.model_dump(), MASTER_B.model_dump()],
                    "edges": [],
                },
            ).model_copy(update={"integrationBranch": "super", "sections": sections}),
        )

    def _author(self, mutations: list[dict[str, Any]], *, dry_run: bool = False) -> dict[str, Any]:
        return task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id=REPOSITORY, task_name="sprint"),
            operation="author_execution_graph",
            edit=TaskDocEdit(fields={"mutations": mutations}),
            call=TaskDocCall(dry_run=dry_run),
        )

    def _snapshot(self) -> dict[Path, bytes]:
        return {path: path.read_bytes() for path in self.tasks.rglob("*") if path.is_file()}

    def test_graph_less_sprint_bootstraps_on_first_add_node_batch(self) -> None:
        # L13: no migrate prerequisite — the first add_node batch creates the graph,
        # and set_nature mutations in the same batch cover nature-less masters.
        sections = [
            Section(
                kind="freeform",
                heading=JUDGMENT_HEADING,
                body=_judgment_register(
                    [
                        _judgment_row("J-nature-a"),
                        _judgment_row("J-nature-b"),
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

        result = self._author(
            [
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
            ]
        )
        self.assertEqual(result["state"], "authored")
        self.assertEqual(result["bootstrapped"], True)
        sprint = read_task_doc(self.tasks / "sprint" / "task.json")
        assert sprint.executionGraph is not None
        self.assertEqual(
            [node.ref for node in sprint.executionGraph.nodes],
            [MASTER_A, MASTER_B],
        )
        self.assertEqual(
            read_task_doc(self.tasks / "master-a" / "task.json").executionNature,
            "organizational",
        )
        self.assertEqual(
            read_task_doc(self.tasks / "master-b" / "task.json").executionNature,
            "atomic",
        )

    def test_bootstrap_requires_exact_membership_and_natures(self) -> None:
        sections = [
            Section(
                kind="freeform",
                heading=JUDGMENT_HEADING,
                body=_judgment_register([_judgment_row("J-nature-a")]),
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

        # Missing the master-b node: the candidate graph does not cover orchestrates.
        with self.assertRaisesRegex(TaskDocError, "membership must exactly match"):
            self._author(
                [
                    {"op": "add_node", "ref": MASTER_A.model_dump()},
                    {
                        "op": "set_nature",
                        "ref": MASTER_A.model_dump(),
                        "executionNature": "organizational",
                        "judgmentId": "J-nature-a",
                    },
                ]
            )
        # Missing the master-b nature: exact membership, but a nature-less master remains.
        with self.assertRaisesRegex(TaskDocError, "has no executionNature"):
            self._author(
                [
                    {"op": "add_node", "ref": MASTER_A.model_dump()},
                    {"op": "add_node", "ref": MASTER_B.model_dump()},
                    {
                        "op": "set_nature",
                        "ref": MASTER_A.model_dump(),
                        "executionNature": "organizational",
                        "judgmentId": "J-nature-a",
                    },
                ]
            )

    def test_judgment_provenance_is_enforced(self) -> None:
        self._write_fixture()
        edge = {
            "op": "add_edge",
            "predecessor": MASTER_B.model_dump(),
            "successor": MASTER_A.model_dump(),
            "reason": "B first",
        }
        # F5 (L15-R8): a missing judgmentId is the typed judgment-required refusal,
        # not a raw pydantic parse failure.
        with self.assertRaisesRegex(TaskDocError, "task-execution-graph-judgment-required"):
            self._author([edge])
        with self.assertRaisesRegex(TaskDocError, "judgment-unknown"):
            self._author([{**edge, "judgmentId": "J-missing"}])
        with self.assertRaisesRegex(TaskDocError, "judgment-author-refused"):
            self._write_fixture(judgment_author="worker")
            self._author([{**edge, "judgmentId": "J-1"}])

    def test_missing_register_section_is_a_typed_refusal_naming_the_section(self) -> None:
        self._write_fixture(register=False)
        with self.assertRaises(TaskDocError) as raised:
            self._author(
                [
                    {
                        "op": "add_edge",
                        "predecessor": MASTER_B.model_dump(),
                        "successor": MASTER_A.model_dump(),
                        "reason": "B first",
                        "judgmentId": "J-1",
                    }
                ]
            )
        self.assertIn("judgment-register-missing", str(raised.exception))
        self.assertIn(JUDGMENT_HEADING, str(raised.exception))

    def test_dry_run_previews_and_writes_nothing_then_apply_publishes(self) -> None:
        self._write_fixture()
        before = self._snapshot()
        mutations = [
            {"op": "remove_node", "ref": MASTER_A.model_dump()},
            {
                "op": "add_node",
                "ref": MASTER_A.model_dump(),
                "kind": "segment",
                "leafIds": ["L1"],
                "judgmentId": "J-1",
            },
            {
                "op": "add_node",
                "ref": MASTER_A.model_dump(),
                "kind": "segment",
                "leafIds": ["L2", "L3"],
                "judgmentId": "J-1",
            },
            {
                "op": "add_edge",
                "predecessor": MASTER_B.model_dump(),
                "successor": {"ref": MASTER_A.model_dump(), "leafId": "L2"},
                "reason": "framework first",
                "judgmentId": "J-1",
            },
        ]
        preview = self._author(mutations, dry_run=True)
        self.assertEqual(preview["state"], "would-author")
        self.assertTrue(preview["dryRun"])
        self.assertEqual(len(preview["documents"]), 1)
        self.assertIn("(leafs: `L2`, `L3`)", preview["documents"][0]["rendered"])
        self.assertEqual(preview["leafPlacementFacts"], [])
        self.assertEqual(before, self._snapshot())

        applied = self._author(mutations)
        self.assertEqual(applied["state"], "authored")
        sprint = read_task_doc(self.tasks / "sprint" / "task.json")
        assert sprint.executionGraph is not None
        self.assertEqual(
            [(node.kind, node.ref, node.leafIds) for node in sprint.executionGraph.nodes],
            [
                ("master", MASTER_B, []),
                ("segment", MASTER_A, ["L1"]),
                ("segment", MASTER_A, ["L2", "L3"]),
            ],
        )
        waves = sprint.executionGraph.derived_waves()
        self.assertEqual(
            [[node.leafIds or [node.ref.key] for node in wave] for wave in waves],
            [[[MASTER_B.key], ["L1"]], [["L2", "L3"]]],
        )
        self.assertEqual(sprint.executionGraph.edges[0].judgmentId, "J-1")
        rendered = (self.tasks / "sprint" / "task.md").read_text(encoding="utf-8")
        self.assertIn(f"- `{MASTER_A.key}` (leafs: `L1`)", rendered)
        self.assertIn(
            f"`{MASTER_B.key}` → `{MASTER_A.key}` (leafs: `L2`, `L3`) — framework first",
            rendered,
        )
        self.assertIn(f"- Wave 2: `{MASTER_A.key}` (leafs: `L2`, `L3`)", rendered)
        masters = self.topology.validate_execution_topology(SPRINT)
        self.assertEqual([master.ref for master in masters], [MASTER_A, MASTER_B])

    def test_batch_atomicity_leaves_everything_untouched_on_failure(self) -> None:
        self._write_fixture()
        before = self._snapshot()
        with self.assertRaisesRegex(TaskDocError, "node-duplicate"):
            self._author(
                [
                    {
                        "op": "add_node",
                        "ref": MASTER_A.model_dump(),
                        "kind": "segment",
                        "leafIds": ["L1"],
                        "judgmentId": "J-1",
                    },
                    {
                        "op": "add_node",
                        "ref": MASTER_A.model_dump(),
                        "kind": "segment",
                        "leafIds": ["L1"],
                        "judgmentId": "J-1",
                    },
                ]
            )
        self.assertEqual(before, self._snapshot())

    def test_incomplete_and_unknown_partitions_are_refused(self) -> None:
        self._write_fixture()
        with self.assertRaisesRegex(TaskDocError, "partition-incomplete"):
            self._author(
                [
                    {"op": "remove_node", "ref": MASTER_A.model_dump()},
                    {
                        "op": "add_node",
                        "ref": MASTER_A.model_dump(),
                        "kind": "segment",
                        "leafIds": ["L1"],
                        "judgmentId": "J-1",
                    },
                ]
            )
        with self.assertRaisesRegex(TaskDocError, "partition-unknown-leaf"):
            self._author(
                [
                    {"op": "remove_node", "ref": MASTER_A.model_dump()},
                    {
                        "op": "add_node",
                        "ref": MASTER_A.model_dump(),
                        "kind": "segment",
                        "leafIds": ["L1", "L2", "L3", "L9"],
                        "judgmentId": "J-1",
                    },
                ]
            )

    def test_segment_on_atomic_and_uncommanded_set_nature_are_refused(self) -> None:
        self._write_fixture()
        with self.assertRaisesRegex(TaskDocError, "node-kind-invalid"):
            self._author(
                [
                    {"op": "remove_node", "ref": MASTER_A.model_dump()},
                    {
                        "op": "add_node",
                        "ref": MASTER_A.model_dump(),
                        "kind": "segment",
                        "leafIds": ["L1", "L2", "L3"],
                        "judgmentId": "J-1",
                    },
                    {
                        "op": "set_nature",
                        "ref": MASTER_A.model_dump(),
                        "executionNature": "atomic",
                        "judgmentId": "J-1",
                    },
                ]
            )
        with self.assertRaisesRegex(TaskDocError, "membership-invalid"):
            self._author(
                [
                    {
                        "op": "set_nature",
                        "ref": MASTER_C.model_dump(),
                        "executionNature": "atomic",
                        "judgmentId": "J-1",
                    }
                ]
            )

    def test_set_nature_rewrites_the_master_document(self) -> None:
        self._write_fixture()
        result = self._author(
            [
                {
                    "op": "set_nature",
                    "ref": MASTER_B.model_dump(),
                    "executionNature": "organizational",
                    "judgmentId": "J-1",
                }
            ]
        )
        self.assertEqual(result["state"], "authored")
        self.assertEqual(len(result["documents"]), 2)
        master_b = read_task_doc(self.tasks / "master-b" / "task.json")
        self.assertEqual(master_b.executionNature, "organizational")

    def test_remove_node_in_use_and_unknown_edge_are_refused(self) -> None:
        self._write_fixture()
        with self.assertRaisesRegex(TaskDocError, "node-in-use"):
            self._author(
                [
                    {
                        "op": "add_edge",
                        "predecessor": MASTER_A.model_dump(),
                        "successor": MASTER_B.model_dump(),
                        "reason": "A first",
                        "judgmentId": "J-1",
                    },
                    {"op": "remove_node", "ref": MASTER_B.model_dump()},
                ]
            )
        with self.assertRaisesRegex(TaskDocError, "edge-unknown"):
            self._author(
                [
                    {
                        "op": "remove_edge",
                        "predecessor": MASTER_A.model_dump(),
                        "successor": MASTER_B.model_dump(),
                        "judgmentId": "J-1",
                    }
                ]
            )

    def test_move_leaf_moves_places_unplaced_and_refuses_emptied_segments(self) -> None:
        self._write_fixture(
            graph={
                "nodes": [
                    _segment(MASTER_A, ["L1", "L2"]),
                    _segment(MASTER_A, ["L3"]),
                    MASTER_B.model_dump(),
                ],
                "edges": [],
            }
        )
        moved = self._author(
            [
                {
                    "op": "move_leaf",
                    "ref": MASTER_A.model_dump(),
                    "leafId": "L2",
                    "toSegment": "L3",
                    "judgmentId": "J-1",
                }
            ]
        )
        self.assertEqual(moved["state"], "authored")
        sprint = read_task_doc(self.tasks / "sprint" / "task.json")
        assert sprint.executionGraph is not None
        self.assertEqual(
            [node.leafIds for node in sprint.executionGraph.nodes if node.ref == MASTER_A],
            [["L1"], ["L3", "L2"]],
        )
        with self.assertRaisesRegex(TaskDocError, "already-placed"):
            self._author(
                [
                    {
                        "op": "move_leaf",
                        "ref": MASTER_A.model_dump(),
                        "leafId": "L3",
                        "toSegment": "L2",
                        "judgmentId": "J-1",
                    }
                ]
            )
        with self.assertRaisesRegex(TaskDocError, "segment-empty"):
            self._author(
                [
                    {
                        "op": "move_leaf",
                        "ref": MASTER_A.model_dump(),
                        "leafId": "L1",
                        "toSegment": "L2",
                        "judgmentId": "J-1",
                    }
                ]
            )
        # A leaf the master gained after authoring is placed by move_leaf (L11-R2/R6).
        master_a = read_task_doc(self.tasks / "master-a" / "task.json")
        write_task_doc(
            self.tasks / "master-a",
            master_a.model_copy(
                update={
                    "subTasks": [
                        *master_a.subTasks,
                        SubTaskRef(number="L4", name="L4", file="l4.md"),
                    ]
                }
            ),
        )
        placed = self._author(
            [
                {
                    "op": "move_leaf",
                    "ref": MASTER_A.model_dump(),
                    "leafId": "L4",
                    "toSegment": "L1",
                    "judgmentId": "J-1",
                }
            ]
        )
        self.assertEqual(placed["state"], "authored")
        sprint = read_task_doc(self.tasks / "sprint" / "task.json")
        assert sprint.executionGraph is not None
        self.assertEqual(
            [node.leafIds for node in sprint.executionGraph.nodes if node.ref == MASTER_A],
            [["L1", "L4"], ["L3", "L2"]],
        )

    def test_numbering_hints_are_reported_and_never_refuse(self) -> None:
        self._write_fixture()
        result = self._author(
            [
                {"op": "remove_node", "ref": MASTER_A.model_dump()},
                {
                    "op": "add_node",
                    "ref": MASTER_A.model_dump(),
                    "kind": "segment",
                    "leafIds": ["L3"],
                    "judgmentId": "J-1",
                },
                {
                    "op": "add_node",
                    "ref": MASTER_A.model_dump(),
                    "kind": "segment",
                    "leafIds": ["L1", "L2"],
                    "judgmentId": "J-1",
                },
                {
                    "op": "add_edge",
                    "predecessor": MASTER_B.model_dump(),
                    "successor": {"ref": MASTER_A.model_dump(), "leafId": "L1"},
                    "reason": "B first",
                    "judgmentId": "J-1",
                },
            ]
        )
        self.assertEqual(result["state"], "authored")
        self.assertEqual(
            [hint["kind"] for hint in result["numberingHints"]],
            ["leaf-numbering-inversion"] * 2,
        )

    def test_operation_is_registered_and_documented(self) -> None:
        self.assertIn("author_execution_graph", VALID_OPERATIONS)
        source = Path(registration_tasks.__file__).read_text(encoding="utf-8")
        self.assertIn("'author_execution_graph'", source)
        # L13-R5f: the finite migration operation is removed; authoring bootstraps.
        self.assertNotIn("migrate_execution_topology", VALID_OPERATIONS)
        self.assertNotIn("migrate_execution_topology", source)

    def test_blank_mutation_judgment_id_is_refused(self) -> None:
        self._write_fixture()
        with self.assertRaisesRegex(TaskDocError, "judgmentId must not be blank"):
            self._author(
                [
                    {
                        "op": "add_node",
                        "ref": MASTER_A.model_dump(),
                        "kind": "segment",
                        "leafIds": ["L1"],
                        "judgmentId": "  ",
                    }
                ]
            )

    def test_lump_only_batch_needs_no_register(self) -> None:
        self._write_fixture(register=False)
        result = self._author(
            [
                {"op": "remove_node", "ref": MASTER_A.model_dump(), "judgmentId": None},
                {"op": "add_node", "ref": MASTER_A.model_dump(), "judgmentId": None},
            ]
        )
        self.assertEqual(result["state"], "authored")
        sprint = read_task_doc(self.tasks / "sprint" / "task.json")
        assert sprint.executionGraph is not None
        self.assertEqual(
            [(node.kind, node.ref) for node in sprint.executionGraph.nodes],
            [("master", MASTER_B), ("master", MASTER_A)],
        )

    def test_authoring_requires_an_orchestration_sprint_document(self) -> None:
        write_task_doc(
            self.tasks / "master-a",
            _master(identity="MASTER-A", execution_nature="organizational"),
        )
        with self.assertRaisesRegex(TaskDocError, "requires an orchestration sprint"):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(repo_id=REPOSITORY, task_name="master-a"),
                operation="author_execution_graph",
                edit=TaskDocEdit(
                    fields={"mutations": [{"op": "remove_node", "ref": MASTER_A.model_dump()}]}
                ),
            )

    def test_authoring_confines_the_sprint_to_the_repository_tasks_root(self) -> None:
        outside = self.coord / "outside"
        write_task_doc(
            outside,
            _master(
                identity="SPRINT",
                orchestrates=["master-a", "master-b"],
                execution_graph={"nodes": [MASTER_A.model_dump(), MASTER_B.model_dump()]},
            ),
        )
        with self.assertRaisesRegex(TaskDocError, "outside tasks/agents-remember"):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(
                    repo_id=REPOSITORY,
                    contract_path=(outside / "series-contract.md").as_posix(),
                ),
                operation="author_execution_graph",
                edit=TaskDocEdit(
                    fields={"mutations": [{"op": "remove_node", "ref": MASTER_A.model_dump()}]}
                ),
            )

    def test_authoring_refuses_a_sprint_whose_membership_already_drifted(self) -> None:
        self._write_fixture()
        sprint = read_task_doc(self.tasks / "sprint" / "task.json")
        write_task_doc(
            self.tasks / "sprint",
            sprint.model_copy(update={"orchestrates": ["master-a", "master-zzz"]}),
        )
        with self.assertRaisesRegex(TaskDocError, "membership-invalid"):
            self._author([{"op": "remove_node", "ref": MASTER_A.model_dump()}])

    def test_overlapping_leaf_ids_across_one_batch_fail_final_validation(self) -> None:
        self._write_fixture()
        with self.assertRaisesRegex(TaskDocError, "invalid execution graph after mutations"):
            self._author(
                [
                    {"op": "remove_node", "ref": MASTER_A.model_dump()},
                    {
                        "op": "add_node",
                        "ref": MASTER_A.model_dump(),
                        "kind": "segment",
                        "leafIds": ["L1"],
                        "judgmentId": "J-1",
                    },
                    {
                        "op": "add_node",
                        "ref": MASTER_A.model_dump(),
                        "kind": "segment",
                        "leafIds": ["L1", "L2", "L3"],
                        "judgmentId": "J-1",
                    },
                ]
            )

    def test_segment_add_node_requires_judgment(self) -> None:
        self._write_fixture()
        with self.assertRaisesRegex(TaskDocError, "judgment-required"):
            self._author(
                [
                    {
                        "op": "add_node",
                        "ref": MASTER_A.model_dump(),
                        "kind": "segment",
                        "leafIds": ["L1"],
                    }
                ]
            )

    def test_malformed_register_is_a_typed_refusal(self) -> None:
        self._write_fixture()
        sprint = read_task_doc(self.tasks / "sprint" / "task.json")
        duplicated = [
            section.model_copy(
                update={"body": _judgment_register([_judgment_row("J-1"), _judgment_row("J-1")])}
            )
            if section.heading == JUDGMENT_HEADING
            else section
            for section in sprint.sections
        ]
        write_task_doc(self.tasks / "sprint", sprint.model_copy(update={"sections": duplicated}))
        with self.assertRaisesRegex(TaskDocError, "judgment-duplicate"):
            self._author(
                [
                    {
                        "op": "add_edge",
                        "predecessor": MASTER_B.model_dump(),
                        "successor": MASTER_A.model_dump(),
                        "reason": "B first",
                        "judgmentId": "J-1",
                    }
                ]
            )

    def test_invalid_add_node_shape_is_refused(self) -> None:
        self._write_fixture()
        with self.assertRaisesRegex(TaskDocError, "invalid add_node mutation"):
            self._author(
                [
                    {
                        "op": "add_node",
                        "ref": MASTER_A.model_dump(),
                        "kind": "segment",
                        "leafIds": [],
                        "judgmentId": "J-1",
                    }
                ]
            )

    def test_remove_node_by_leaf_sample_ambiguity_and_unknown_leaf(self) -> None:
        self._write_fixture(
            graph={
                "nodes": [
                    _segment(MASTER_A, ["L1"]),
                    _segment(MASTER_A, ["L2", "L3"]),
                    MASTER_B.model_dump(),
                ],
                "edges": [],
            }
        )
        with self.assertRaisesRegex(TaskDocError, "node-ambiguous"):
            self._author([{"op": "remove_node", "ref": MASTER_A.model_dump()}])
        with self.assertRaisesRegex(TaskDocError, "node-unknown"):
            self._author([{"op": "remove_node", "ref": MASTER_A.model_dump(), "leafId": "L9"}])
        with self.assertRaisesRegex(TaskDocError, "judgment-required"):
            self._author([{"op": "remove_node", "ref": MASTER_A.model_dump(), "leafId": "L1"}])
        removed = self._author(
            [
                {
                    "op": "remove_node",
                    "ref": MASTER_A.model_dump(),
                    "leafId": "L1",
                    "judgmentId": "J-1",
                },
                {
                    "op": "move_leaf",
                    "ref": MASTER_A.model_dump(),
                    "leafId": "L1",
                    "toSegment": "L2",
                    "judgmentId": "J-1",
                },
            ]
        )
        self.assertEqual(removed["state"], "authored")
        sprint = read_task_doc(self.tasks / "sprint" / "task.json")
        assert sprint.executionGraph is not None
        self.assertEqual(
            [node.leafIds for node in sprint.executionGraph.nodes if node.ref == MASTER_A],
            [["L2", "L3", "L1"]],
        )

    def test_remove_node_succeeds_when_an_edge_does_not_touch_it(self) -> None:
        # The edge targets the L1 segment, so removing the L2 segment passes the
        # per-edge touch check (False arm) and the batch stays partition-complete.
        self._write_fixture(
            graph={
                "nodes": [
                    _segment(MASTER_A, ["L1"]),
                    _segment(MASTER_A, ["L2", "L3"]),
                    MASTER_B.model_dump(),
                ],
                "edges": [
                    {
                        "predecessor": MASTER_B.model_dump(),
                        "successor": {"ref": MASTER_A.model_dump(), "leafId": "L1"},
                        "reason": "B first",
                        "judgmentId": "J-1",
                    }
                ],
            }
        )
        result = self._author(
            [
                {
                    "op": "move_leaf",
                    "ref": MASTER_A.model_dump(),
                    "leafId": "L3",
                    "toSegment": "L1",
                    "judgmentId": "J-1",
                },
                {
                    "op": "remove_node",
                    "ref": MASTER_A.model_dump(),
                    "leafId": "L2",
                    "judgmentId": "J-1",
                },
                {
                    "op": "move_leaf",
                    "ref": MASTER_A.model_dump(),
                    "leafId": "L2",
                    "toSegment": "L1",
                    "judgmentId": "J-1",
                },
            ]
        )
        self.assertEqual(result["state"], "authored")
        sprint = read_task_doc(self.tasks / "sprint" / "task.json")
        assert sprint.executionGraph is not None
        self.assertEqual(
            [node.leafIds for node in sprint.executionGraph.nodes if node.ref == MASTER_A],
            [["L1", "L3", "L2"]],
        )

    def test_add_edge_endpoint_resolution_blank_reason_self_and_duplicate(self) -> None:
        self._write_fixture()
        with self.assertRaisesRegex(TaskDocError, "not placed in any node"):
            self._author(
                [
                    {"op": "remove_node", "ref": MASTER_A.model_dump()},
                    {
                        "op": "add_node",
                        "ref": MASTER_A.model_dump(),
                        "kind": "segment",
                        "leafIds": ["L1", "L2", "L3"],
                        "judgmentId": "J-1",
                    },
                    {
                        "op": "add_edge",
                        "predecessor": MASTER_B.model_dump(),
                        "successor": {"ref": MASTER_A.model_dump(), "leafId": "L9"},
                        "reason": "bad leaf",
                        "judgmentId": "J-1",
                    },
                ]
            )
        with self.assertRaisesRegex(TaskDocError, "invalid add_edge mutation"):
            self._author(
                [
                    {
                        "op": "add_edge",
                        "predecessor": MASTER_B.model_dump(),
                        "successor": MASTER_A.model_dump(),
                        "reason": " ",
                        "judgmentId": "J-1",
                    }
                ]
            )
        with self.assertRaisesRegex(TaskDocError, "itself"):
            self._author(
                [
                    {
                        "op": "add_edge",
                        "predecessor": MASTER_A.model_dump(),
                        "successor": MASTER_A.model_dump(),
                        "reason": "self",
                        "judgmentId": "J-1",
                    }
                ]
            )
        # Cross-addressed endpoints (bare ref vs leaf sample) resolve to the same node:
        # the edge-level shape check passes, the draft-level self check refuses.
        with self.assertRaisesRegex(TaskDocError, "itself"):
            self._author(
                [
                    {"op": "remove_node", "ref": MASTER_A.model_dump()},
                    {
                        "op": "add_node",
                        "ref": MASTER_A.model_dump(),
                        "kind": "segment",
                        "leafIds": ["L1", "L2", "L3"],
                        "judgmentId": "J-1",
                    },
                    {
                        "op": "add_edge",
                        "predecessor": MASTER_A.model_dump(),
                        "successor": {"ref": MASTER_A.model_dump(), "leafId": "L1"},
                        "reason": "self via leaf sample",
                        "judgmentId": "J-1",
                    },
                ]
            )
        with self.assertRaisesRegex(TaskDocError, "edge-duplicate"):
            self._author(
                [
                    {
                        "op": "add_edge",
                        "predecessor": MASTER_B.model_dump(),
                        "successor": MASTER_A.model_dump(),
                        "reason": "one",
                        "judgmentId": "J-1",
                    },
                    {
                        "op": "add_edge",
                        "predecessor": MASTER_B.model_dump(),
                        "successor": MASTER_A.model_dump(),
                        "reason": "two",
                        "judgmentId": "J-1",
                    },
                ]
            )

    def test_remove_edge_happy_path(self) -> None:
        self._write_fixture(
            graph={
                "nodes": [MASTER_A.model_dump(), MASTER_B.model_dump()],
                "edges": [
                    {
                        "predecessor": MASTER_B.model_dump(),
                        "successor": MASTER_A.model_dump(),
                        "reason": "B first",
                        "judgmentId": "J-1",
                    }
                ],
            }
        )
        result = self._author(
            [
                {
                    "op": "remove_edge",
                    "predecessor": MASTER_B.model_dump(),
                    "successor": MASTER_A.model_dump(),
                    "judgmentId": "J-1",
                }
            ]
        )
        self.assertEqual(result["state"], "authored")
        sprint = read_task_doc(self.tasks / "sprint" / "task.json")
        assert sprint.executionGraph is not None
        self.assertEqual(sprint.executionGraph.edges, [])

    def test_move_leaf_refuses_an_unknown_target_segment_sample(self) -> None:
        self._write_fixture(
            graph={
                "nodes": [
                    _segment(MASTER_A, ["L1", "L2"]),
                    _segment(MASTER_A, ["L3"]),
                    MASTER_B.model_dump(),
                ],
                "edges": [],
            }
        )
        with self.assertRaisesRegex(TaskDocError, "segment-unknown"):
            self._author(
                [
                    {
                        "op": "move_leaf",
                        "ref": MASTER_A.model_dump(),
                        "leafId": "L2",
                        "toSegment": "L9",
                        "judgmentId": "J-1",
                    }
                ]
            )


if __name__ == "__main__":
    unittest.main()
