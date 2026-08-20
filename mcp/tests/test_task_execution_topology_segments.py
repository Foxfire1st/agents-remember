"""L11 segment-graph schema, endpoint grammar, derived placement, and partition facts.

Split from ``test_task_execution_topology.py`` (file-size limit); fixtures and shared
helpers are imported from it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.projection import TaskExecutionEndpointNode, TaskExecutionNode
from agents_remember.tasks import (
    SprintExecutionEndpoint,
    SprintExecutionGraph,
    SubTaskRef,
    derived_leaf_placement,
    leaf_placement_facts,
    numbering_drift_hints,
    write_task_doc,
)
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from pydantic import ValidationError
from test_task_execution_topology import (
    MASTER_A,
    MASTER_B,
    MASTER_C,
    REPOSITORY,
    SPRINT,
    _master,
)


def _segment(ref: TaskDocumentRef, leaf_ids: list[str]) -> dict[str, Any]:
    return {"kind": "segment", "ref": ref.model_dump(), "leafIds": leaf_ids}


class ExecutionGraphSegmentSchemaTests(unittest.TestCase):
    """L11-R1/R3/R4/R7: node kinds, leaf-level uniqueness, endpoint grammar, compat."""

    def test_legacy_bare_node_graph_parses_as_lumps_and_roundtrips_byte_identical(self) -> None:
        # The IAS-shaped 16-node/0-edge graph is deliberately left as-is (L11-R7).
        payload = {
            "nodes": [
                {"repository": REPOSITORY, "path": f"ias-master-{number:02d}/task.json"}
                for number in range(16)
            ],
            "edges": [],
        }
        graph = SprintExecutionGraph.model_validate(payload)
        self.assertEqual([node.kind for node in graph.nodes], ["master"] * 16)
        self.assertTrue(all(node.leafIds == [] for node in graph.nodes))
        self.assertEqual(graph.model_dump(mode="json"), payload)
        self.assertEqual(len(graph.derived_waves()), 1)

    def test_constructor_lifts_bare_ref_instances_and_compares_equal_for_lumps(self) -> None:
        graph = SprintExecutionGraph.model_validate({"nodes": [MASTER_A, MASTER_B], "edges": []})
        self.assertEqual(graph.derived_waves(), [[MASTER_A, MASTER_B]])
        self.assertIn(MASTER_A, graph.nodes)
        self.assertEqual(graph.master_refs(), [MASTER_A, MASTER_B])

    def test_segment_shape_rules(self) -> None:
        mutations = (
            {"kind": "segment", "ref": MASTER_A.model_dump(), "leafIds": []},
            {"kind": "segment", "ref": MASTER_A.model_dump(), "leafIds": ["L1", "L1"]},
            {"kind": "master", "ref": MASTER_A.model_dump(), "leafIds": ["L1"]},
            {"kind": "segment", "ref": MASTER_A.model_dump(), "leafIds": ["  "]},
        )
        for node in mutations:
            with self.subTest(node=node), self.assertRaises(ValidationError):
                SprintExecutionGraph.model_validate({"nodes": [node, MASTER_B.model_dump()]})

    def test_leaf_ids_are_unique_sprint_wide(self) -> None:
        with self.assertRaisesRegex(ValidationError, "more than one node"):
            SprintExecutionGraph.model_validate(
                {
                    "nodes": [
                        _segment(MASTER_A, ["L1"]),
                        _segment(MASTER_B, ["L1"]),
                    ]
                }
            )

    def test_lump_and_segment_appearances_of_one_master_are_mutually_exclusive(self) -> None:
        with self.assertRaisesRegex(ValidationError, "mutually exclusive"):
            SprintExecutionGraph.model_validate(
                {"nodes": [MASTER_A.model_dump(), _segment(MASTER_A, ["L1"])]}
            )

    def test_edge_endpoints_address_segments_by_leaf_sample(self) -> None:
        graph = SprintExecutionGraph.model_validate(
            {
                "nodes": [
                    _segment(MASTER_A, ["L1"]),
                    _segment(MASTER_A, ["L2", "L3"]),
                    MASTER_B.model_dump(),
                ],
                "edges": [
                    {
                        "predecessor": MASTER_B.model_dump(),
                        "successor": {"ref": MASTER_A.model_dump(), "leafId": "L2"},
                        "reason": "framework first",
                        "judgmentId": "J-1",
                    }
                ],
            }
        )
        edge = graph.edges[0]
        self.assertEqual(edge.judgmentId, "J-1")
        self.assertEqual(graph.resolve_endpoint(edge.successor).leafIds, ["L2", "L3"])
        waves = graph.derived_waves()
        self.assertEqual(
            [[(node.kind, node.leafIds) for node in wave] for wave in waves],
            [
                [("segment", ["L1"]), ("master", [])],
                [("segment", ["L2", "L3"])],
            ],
        )

    def test_edge_endpoint_resolution_refusals(self) -> None:
        base = [
            _segment(MASTER_A, ["L1"]),
            _segment(MASTER_A, ["L2"]),
            MASTER_B.model_dump(),
        ]
        mutations = (
            # bare ref is ambiguous across multiple segments of one master
            {
                "predecessor": MASTER_B.model_dump(),
                "successor": MASTER_A.model_dump(),
                "reason": "ambiguous",
            },
            # leaf id placed in no node of that master
            {
                "predecessor": MASTER_B.model_dump(),
                "successor": {"ref": MASTER_A.model_dump(), "leafId": "L9"},
                "reason": "unplaced",
            },
            # undeclared master
            {
                "predecessor": MASTER_B.model_dump(),
                "successor": MASTER_C.model_dump(),
                "reason": "unknown",
            },
            # resolves to the same segment
            {
                "predecessor": {"ref": MASTER_A.model_dump(), "leafId": "L1"},
                "successor": {"ref": MASTER_A.model_dump(), "leafId": "L1"},
                "reason": "self",
            },
        )
        for edge in mutations:
            with self.subTest(edge=edge), self.assertRaises(ValidationError):
                SprintExecutionGraph.model_validate({"nodes": base, "edges": [edge]})

    def test_equivalent_edge_addressing_is_a_duplicate(self) -> None:
        nodes = [_segment(MASTER_A, ["L1"]), MASTER_B.model_dump()]
        edges = [
            {
                "predecessor": MASTER_B.model_dump(),
                "successor": MASTER_A.model_dump(),
                "reason": "bare form",
            },
            {
                "predecessor": MASTER_B.model_dump(),
                "successor": {"ref": MASTER_A.model_dump(), "leafId": "L1"},
                "reason": "leaf-sample form",
            },
        ]
        with self.assertRaisesRegex(ValidationError, "edges must be unique"):
            SprintExecutionGraph.model_validate({"nodes": nodes, "edges": edges})

    def test_cycle_through_segments_is_refused(self) -> None:
        with self.assertRaisesRegex(ValidationError, "acyclic"):
            SprintExecutionGraph.model_validate(
                {
                    "nodes": [_segment(MASTER_A, ["L1"]), _segment(MASTER_B, ["L2"])],
                    "edges": [
                        {
                            "predecessor": {"ref": MASTER_A.model_dump(), "leafId": "L1"},
                            "successor": {"ref": MASTER_B.model_dump(), "leafId": "L2"},
                            "reason": "a before b",
                        },
                        {
                            "predecessor": {"ref": MASTER_B.model_dump(), "leafId": "L2"},
                            "successor": {"ref": MASTER_A.model_dump(), "leafId": "L1"},
                            "reason": "b before a",
                        },
                    ],
                }
            )

    def test_edge_judgment_id_is_trimmed_and_never_blank(self) -> None:
        graph = SprintExecutionGraph.model_validate(
            {
                "nodes": [MASTER_A.model_dump(), MASTER_B.model_dump()],
                "edges": [
                    {
                        "predecessor": MASTER_A.model_dump(),
                        "successor": MASTER_B.model_dump(),
                        "reason": "x",
                        "judgmentId": "  J-7  ",
                    }
                ],
            }
        )
        self.assertEqual(graph.edges[0].judgmentId, "J-7")
        with self.assertRaises(ValidationError):
            SprintExecutionGraph.model_validate(
                {
                    "nodes": [MASTER_A.model_dump(), MASTER_B.model_dump()],
                    "edges": [
                        {
                            "predecessor": MASTER_A.model_dump(),
                            "successor": MASTER_B.model_dump(),
                            "reason": "x",
                            "judgmentId": " ",
                        }
                    ],
                }
            )

    def test_endpoint_leaf_id_defaults_to_none_and_must_not_be_blank(self) -> None:
        endpoint = SprintExecutionEndpoint.model_validate(
            {"ref": MASTER_A.model_dump(), "leafId": None}
        )
        self.assertIsNone(endpoint.leafId)
        with self.assertRaises(ValidationError):
            SprintExecutionEndpoint.model_validate({"ref": MASTER_A.model_dump(), "leafId": " "})

    def test_nodes_compare_unequal_to_other_types(self) -> None:
        node = SprintExecutionGraph.model_validate({"nodes": [MASTER_A.model_dump()]}).nodes[0]
        self.assertFalse(node == "master-a/task.json")
        self.assertTrue(node != "master-a/task.json")

    def test_cross_addressed_self_edge_is_refused_at_resolution(self) -> None:
        # A bare ref addresses the master's only node; so does the leaf sample of it.
        with self.assertRaisesRegex(ValidationError, "itself"):
            SprintExecutionGraph.model_validate(
                {
                    "nodes": [_segment(MASTER_A, ["L1"])],
                    "edges": [
                        {
                            "predecessor": MASTER_A.model_dump(),
                            "successor": {"ref": MASTER_A.model_dump(), "leafId": "L1"},
                            "reason": "same segment twice",
                        }
                    ],
                }
            )


class ExecutionGraphProjectionLiftTests(unittest.TestCase):
    """The projection models lift the same bare-ref grammar the schema does."""

    def test_node_model_accepts_bare_ref_instances_and_dicts(self) -> None:
        node = TaskExecutionNode.model_validate(MASTER_A)
        self.assertEqual((node.kind, node.ref, node.leafIds), ("master", MASTER_A, []))
        lifted = TaskExecutionNode.model_validate(MASTER_A.model_dump())
        self.assertEqual(lifted.ref, MASTER_A)
        segment = TaskExecutionNode.model_validate(
            {"kind": "segment", "ref": MASTER_A.model_dump(), "leafIds": ["L1"]}
        )
        self.assertEqual(segment.leafIds, ["L1"])

    def test_endpoint_model_lifts_bare_refs_and_keeps_leaf_samples(self) -> None:
        endpoint = TaskExecutionEndpointNode.model_validate(MASTER_A)
        self.assertEqual((endpoint.ref, endpoint.leafId), (MASTER_A, None))
        lifted = TaskExecutionEndpointNode.model_validate(MASTER_A.model_dump())
        self.assertIsNone(lifted.leafId)
        sampled = TaskExecutionEndpointNode.model_validate(
            {"ref": MASTER_A.model_dump(), "leafId": "L1"}
        )
        self.assertEqual(sampled.leafId, "L1")


class DerivedLeafPlacementTests(unittest.TestCase):
    """L11-R2/R8: unplaced-leaf derived placement and numbering-drift hints."""

    def _graph(self, *, edge_to: str | None = None) -> SprintExecutionGraph:
        nodes: list[dict[str, Any]] = [
            _segment(MASTER_A, ["L1"]),
            _segment(MASTER_A, ["L2"]),
            MASTER_B.model_dump(),
        ]
        edges = []
        if edge_to is not None:
            edges.append(
                {
                    "predecessor": MASTER_B.model_dump(),
                    "successor": {"ref": MASTER_A.model_dump(), "leafId": edge_to},
                    "reason": "B first",
                }
            )
        return SprintExecutionGraph.model_validate({"nodes": nodes, "edges": edges})

    def test_unplaced_leaf_derives_to_the_latest_unblocked_segment(self) -> None:
        # L2's segment sits in the later wave and is blocked by incomplete MASTER-B;
        # the derived target is the latest *unblocked* segment.
        placement = derived_leaf_placement(
            self._graph(edge_to="L2"), MASTER_A, ["L1", "L2", "L3"], set()
        )
        self.assertEqual(placement.unplaced_leaf_ids, ("L3",))
        self.assertEqual(placement.derived["L3"].leafIds, ["L1"])
        self.assertFalse(placement.derived_all_blocked)
        facts = leaf_placement_facts(MASTER_A.key, placement)
        self.assertEqual(
            facts,
            [
                {
                    "kind": "unplaced-leaf",
                    "master": MASTER_A.key,
                    "leafId": "L3",
                    "derivedSegmentLeafs": ["L1"],
                    "derivedAllSegmentsBlocked": False,
                }
            ],
        )

    def test_all_blocked_falls_back_to_the_latest_segment_flagged(self) -> None:
        graph = SprintExecutionGraph.model_validate(
            {
                "nodes": [
                    _segment(MASTER_A, ["L1"]),
                    _segment(MASTER_A, ["L2"]),
                    MASTER_B.model_dump(),
                ],
                "edges": [
                    {
                        "predecessor": MASTER_B.model_dump(),
                        "successor": {"ref": MASTER_A.model_dump(), "leafId": leaf},
                        "reason": f"blocks {leaf}",
                    }
                    for leaf in ("L1", "L2")
                ],
            }
        )
        placement = derived_leaf_placement(graph, MASTER_A, ["L1", "L2", "L3"], set())
        self.assertEqual(placement.derived["L3"].leafIds, ["L2"])
        self.assertTrue(placement.derived_all_blocked)
        completed = derived_leaf_placement(graph, MASTER_A, ["L1", "L2", "L3"], {MASTER_B})
        self.assertEqual(completed.derived["L3"].leafIds, ["L2"])
        self.assertFalse(completed.derived_all_blocked)

    def test_lump_masters_have_no_segment_placement(self) -> None:
        graph = SprintExecutionGraph.model_validate(
            {"nodes": [MASTER_A.model_dump(), MASTER_B.model_dump()]}
        )
        placement = derived_leaf_placement(graph, MASTER_A, ["L1"], set())
        self.assertEqual(placement.unplaced_leaf_ids, ())
        self.assertEqual(placement.derived, {})

    def test_unknown_leafs_are_facts(self) -> None:
        placement = derived_leaf_placement(self._graph(), MASTER_A, ["L1"], set())
        self.assertEqual(placement.unknown_leaf_ids, ("L2",))
        facts = leaf_placement_facts(MASTER_A.key, placement)
        self.assertEqual(facts, [{"kind": "unknown-leaf", "master": MASTER_A.key, "leafId": "L2"}])

    def test_numbering_inversion_across_waves_is_a_hint_never_a_refusal(self) -> None:
        # L1 sits in wave 2 (blocked by B) while the higher-numbered L3 is in wave 1.
        graph = SprintExecutionGraph.model_validate(
            {
                "nodes": [
                    _segment(MASTER_A, ["L3"]),
                    _segment(MASTER_A, ["L1", "L2"]),
                    MASTER_B.model_dump(),
                ],
                "edges": [
                    {
                        "predecessor": MASTER_B.model_dump(),
                        "successor": {"ref": MASTER_A.model_dump(), "leafId": "L1"},
                        "reason": "B first",
                    }
                ],
            }
        )
        hints = numbering_drift_hints(graph)
        self.assertEqual(len(hints), 2)
        self.assertEqual(
            hints[0],
            {
                "kind": "leaf-numbering-inversion",
                "master": MASTER_A.key,
                "lowerNumberLeafId": "L1",
                "lowerNumberWave": 2,
                "higherNumberLeafId": "L3",
                "higherNumberWave": 1,
            },
        )
        self.assertEqual(numbering_drift_hints(self._graph()), [])

    def test_numbering_hints_ignore_leafs_without_trailing_numbers(self) -> None:
        graph = SprintExecutionGraph.model_validate(
            {
                "nodes": [
                    _segment(MASTER_A, ["LEAF-B"]),
                    _segment(MASTER_A, ["LEAF-A"]),
                    MASTER_B.model_dump(),
                ],
                "edges": [
                    {
                        "predecessor": MASTER_B.model_dump(),
                        "successor": {"ref": MASTER_A.model_dump(), "leafId": "LEAF-A"},
                        "reason": "B first",
                    }
                ],
            }
        )
        self.assertEqual(numbering_drift_hints(graph), [])


class ExecutionTopologySegmentValidationTests(unittest.TestCase):
    """L11-R1/R2/R6: cross-document node-kind legality and partition facts."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.coord = Path(self.temp.name)
        self.tasks = self.coord / "tasks" / REPOSITORY
        self.tasks.mkdir(parents=True)
        self.topology = TaskDocumentTopology(self.coord)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_segmented_sprint(
        self,
        *,
        nature_a: str = "organizational",
        leafs_a: list[str] | None = None,
    ) -> None:
        leafs = leafs_a or ["L1", "L2", "L3"]
        write_task_doc(
            self.tasks / "master-a",
            _master(identity="MASTER-A", execution_nature=nature_a).model_copy(
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
        write_task_doc(
            self.tasks / "sprint",
            _master(
                identity="SPRINT",
                orchestrates=["master-a", "master-b"],
                execution_graph={
                    "nodes": [
                        _segment(MASTER_A, ["L1"]),
                        _segment(MASTER_A, ["L2", "L3"]),
                        MASTER_B.model_dump(),
                    ],
                    "edges": [],
                },
            ),
        )

    def test_segmented_membership_matches_orchestrates_and_waves_run_over_nodes(self) -> None:
        self._write_segmented_sprint()
        masters = self.topology.validate_execution_topology(SPRINT)
        self.assertEqual([master.ref for master in masters], [MASTER_A, MASTER_B])
        waves = self.topology.execution_waves(SPRINT)
        # Waves run over nodes: both master-a segments land in wave 1.
        self.assertEqual(
            [[node.ref for node in wave] for wave in waves], [[MASTER_A, MASTER_A, MASTER_B]]
        )

    def test_segment_on_atomic_master_is_refused_citing_the_node(self) -> None:
        self._write_segmented_sprint(nature_a="atomic")
        with self.assertRaises(TaskDocumentRefError) as raised:
            self.topology.validate_execution_topology(SPRINT)
        self.assertEqual(raised.exception.status, "task-execution-graph-node-kind-invalid")
        self.assertIn(MASTER_A.key, str(raised.exception))
        self.assertIn("L1", str(raised.exception))

    def test_leaf_placement_reports_unplaced_and_unknown_against_the_live_plan(self) -> None:
        self._write_segmented_sprint(leafs_a=["L1", "L2", "L3", "L4"])
        reports = {
            report.master.ref: report.placement
            for report in self.topology.execution_leaf_placement(SPRINT)
        }
        placement = reports[MASTER_A]
        self.assertEqual(placement.unplaced_leaf_ids, ("L4",))
        self.assertEqual(placement.derived["L4"].leafIds, ["L2", "L3"])
        self.assertEqual(reports[MASTER_B].unplaced_leaf_ids, ())

        self._write_segmented_sprint(leafs_a=["L1", "L2"])
        reports = {
            report.master.ref: report.placement
            for report in self.topology.execution_leaf_placement(SPRINT)
        }
        self.assertEqual(reports[MASTER_A].unknown_leaf_ids, ("L3",))
        self.assertEqual(reports[MASTER_A].unplaced_leaf_ids, ())
