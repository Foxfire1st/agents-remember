"""Deterministic mermaid execution-graph render tests (L12-R1)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, render_markdown, write_task_doc
from agents_remember.tasks.execution_graph_titles import (
    SprintGraphTitles,
    build_graph_titles,
    read_graph_titles,
)

REPO = "repo-a"
MASTER_A = TaskDocumentRef(repository=REPO, path="master-a/task.json")
MASTER_B = TaskDocumentRef(repository=REPO, path="master-b/task.json")


def _sprint(
    *,
    nodes: list[dict[str, object]],
    edges: list[dict[str, object]] | None = None,
    orchestrates: list[str] | None = None,
) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": "SPRINT",
            "slug": "task",
            "kind": "master",
            "title": "Sprint",
            "repo": REPO,
            "createdAt": "2026-08-15T00:00:00+00:00",
            "orchestrates": orchestrates or ["master-a", "atomic-f"],
            "executionGraph": {"nodes": nodes, "edges": edges or []},
        }
    )


def _graph_section(md: str) -> str:
    """The Execution Graph section: from its heading to the next section separator."""
    start = md.index("## Execution Graph")
    body = md[start + len("## Execution Graph") :]
    end = body.find("\\n---")
    return md[start:] if end < 0 else md[start : start + len("## Execution Graph") + end]


def _mermaid_block(section: str) -> str:
    """Just the fenced mermaid diagram inside an Execution Graph section."""
    start = section.index("```mermaid")
    end = section.index("```", start + len("```mermaid"))
    return section[start:end]


class ExecutionGraphMermaidRenderTests(unittest.TestCase):
    def test_renders_subgraph_per_master_with_leaf_nodes_and_lump(self) -> None:
        doc = _sprint(
            nodes=[
                {
                    "kind": "segment",
                    "ref": {"repository": REPO, "path": "master-a/task.json"},
                    "leafIds": ["A-L1", "A-L2"],
                },
                {"ref": {"repository": REPO, "path": "atomic-f/task.json"}},
                {
                    "kind": "segment",
                    "ref": {"repository": REPO, "path": "master-a/task.json"},
                    "leafIds": ["A-L3"],
                },
            ],
            edges=[
                {
                    "predecessor": {
                        "ref": {"repository": REPO, "path": "master-a/task.json"},
                        "leafId": "A-L1",
                    },
                    "successor": {"ref": {"repository": REPO, "path": "atomic-f/task.json"}},
                    "reason": "early segment lands before the atomic block",
                },
                {
                    "predecessor": {"ref": {"repository": REPO, "path": "atomic-f/task.json"}},
                    "successor": {
                        "ref": {"repository": REPO, "path": "master-a/task.json"},
                        "leafId": "A-L3",
                    },
                    "reason": "the atomic block gates the late segment",
                },
            ],
        )
        titles = SprintGraphTitles(
            master_titles={
                "repo-a/master-a/task.json": "Master A",
                "repo-a/atomic-f/task.json": "Atomic F",
            },
            leaf_titles={
                (MASTER_A, "A-L1"): "Leaf one",
                (MASTER_A, "A-L2"): "Leaf two",
                (MASTER_A, "A-L3"): "Leaf three",
            },
        )
        section = _graph_section(render_markdown(doc, graph_titles=titles))
        self.assertIn("```mermaid", section)
        self.assertIn("flowchart TD", section)
        # one subgraph per master box, labeled with the master title; one node per leaf
        self.assertIn('subgraph sg0["Master A"]', section)
        self.assertIn('n0_l0["A-L1 — Leaf one"]', section)
        self.assertIn('n0_l1["A-L2 — Leaf two"]', section)
        self.assertIn('n2_l0["A-L3 — Leaf three"]', section)
        self.assertIn("end", section)
        # the atomic master is a single lump node labeled with its title
        self.assertIn('n1["Atomic F"]', section)
        # labeled edges
        self.assertIn("n0_l0 -->|early segment lands before the atomic block| n1", section)
        self.assertIn("n1 -->|the atomic block gates the late segment| n2_l0", section)
        # the compact machine-readable list form stays alongside the diagram
        self.assertIn("### Nodes", section)
        self.assertIn("### Dependencies", section)
        self.assertIn("### Derived Waves", section)
        self.assertIn("- Wave 1:", section)

    def test_renders_without_titles_using_ref_key_and_leaf_id_fallbacks(self) -> None:
        doc = _sprint(
            nodes=[
                {
                    "kind": "segment",
                    "ref": {"repository": REPO, "path": "master-a/task.json"},
                    "leafIds": ["A-L1"],
                }
            ],
        )
        section = _graph_section(render_markdown(doc))
        self.assertIn('subgraph sg0["repo-a/master-a/task.json"]', section)
        self.assertIn('n0_l0["A-L1 — A-L1"]', section)

    def test_render_is_deterministic_and_wave_ordered(self) -> None:
        doc = _sprint(
            nodes=[
                {
                    "kind": "segment",
                    "ref": {"repository": REPO, "path": "master-a/task.json"},
                    "leafIds": ["A-L1"],
                },
                {"ref": {"repository": REPO, "path": "atomic-f/task.json"}},
            ],
            edges=[
                {
                    "predecessor": {
                        "ref": {"repository": REPO, "path": "master-a/task.json"},
                        "leafId": "A-L1",
                    },
                    "successor": {"ref": {"repository": REPO, "path": "atomic-f/task.json"}},
                    "reason": "gates",
                }
            ],
        )
        first = render_markdown(doc)
        self.assertEqual(first, render_markdown(doc))
        # the subgraph (wave-1 master) is emitted before the wave-2 lump node
        section = _graph_section(first)
        self.assertLess(section.index("subgraph sg0"), section.index('n1["'))

    def test_private_leaf_ids_do_not_collapse_sanitizer_equivalent_labels(self) -> None:
        doc = _sprint(
            nodes=[
                {
                    "kind": "segment",
                    "ref": MASTER_A.model_dump(),
                    "leafIds": ["a/b"],
                },
                {"ref": {"repository": REPO, "path": "atomic-f/task.json"}},
                {
                    "kind": "segment",
                    "ref": MASTER_A.model_dump(),
                    "leafIds": ["a?b"],
                },
            ],
            edges=[
                {
                    "predecessor": {"ref": MASTER_A.model_dump(), "leafId": "a/b"},
                    "successor": {"ref": {"repository": REPO, "path": "atomic-f/task.json"}},
                    "reason": "first leaf gates the lump",
                },
                {
                    "predecessor": {"ref": {"repository": REPO, "path": "atomic-f/task.json"}},
                    "successor": {"ref": MASTER_A.model_dump(), "leafId": "a?b"},
                    "reason": "lump gates the second leaf",
                },
            ],
        )
        titles = SprintGraphTitles(
            leaf_titles={
                (MASTER_A, "a/b"): "Slash leaf",
                (MASTER_A, "a?b"): "Question leaf",
            }
        )
        section = _graph_section(render_markdown(doc, graph_titles=titles))
        mermaid = _mermaid_block(section)
        self.assertIn('n0_l0["a/b — Slash leaf"]', mermaid)
        self.assertIn('n2_l0["a?b — Question leaf"]', mermaid)
        self.assertIn("n0_l0 -->|first leaf gates the lump| n1", mermaid)
        self.assertIn("n1 -->|lump gates the second leaf| n2_l0", mermaid)
        self.assertNotIn("leaf_a_b", mermaid)
        self.assertEqual(
            mermaid,
            _mermaid_block(_graph_section(render_markdown(doc, graph_titles=titles))),
        )
        renamed = SprintGraphTitles(
            leaf_titles={
                (MASTER_A, "a/b"): "Renamed slash leaf",
                (MASTER_A, "a?b"): "Renamed question leaf",
            }
        )
        renamed_mermaid = _mermaid_block(_graph_section(render_markdown(doc, graph_titles=renamed)))
        self.assertIn('n0_l0["a/b — Renamed slash leaf"]', renamed_mermaid)
        self.assertIn('n2_l0["a?b — Renamed question leaf"]', renamed_mermaid)
        self.assertEqual(renamed_mermaid.count("n0_l0["), 1)
        self.assertEqual(renamed_mermaid.count("n2_l0["), 1)

    def test_escapes_pipes_and_quotes_in_edge_reasons(self) -> None:
        doc = _sprint(
            nodes=[
                {"ref": {"repository": REPO, "path": "master-a/task.json"}},
                {"ref": {"repository": REPO, "path": "atomic-f/task.json"}},
            ],
            edges=[
                {
                    "predecessor": {"ref": {"repository": REPO, "path": "master-a/task.json"}},
                    "successor": {"ref": {"repository": REPO, "path": "atomic-f/task.json"}},
                    "reason": 'says "go" | now',
                }
            ],
        )
        section = _graph_section(render_markdown(doc))
        self.assertIn("n0 -->|says &#34;go&#34; &#124; now| n1", section)

    def test_truncates_long_labels_with_ellipsis(self) -> None:
        # The label truncation branch: a master title longer than the cap renders with a
        # trailing ellipsis (and the full text never appears).
        long_title = "Master " + "x" * 100
        doc = _sprint(nodes=[{"ref": {"repository": REPO, "path": "master-a/task.json"}}])
        titles = SprintGraphTitles(master_titles={"repo-a/master-a/task.json": long_title})
        section = _graph_section(render_markdown(doc, graph_titles=titles))
        self.assertIn('…"', section)
        self.assertNotIn(long_title, section)
        # an over-long edge reason is truncated too
        long_reason = "gate " + "y" * 200
        doc2 = _sprint(
            nodes=[
                {"ref": {"repository": REPO, "path": "master-a/task.json"}},
                {"ref": {"repository": REPO, "path": "atomic-f/task.json"}},
            ],
            edges=[
                {
                    "predecessor": {"ref": {"repository": REPO, "path": "master-a/task.json"}},
                    "successor": {"ref": {"repository": REPO, "path": "atomic-f/task.json"}},
                    "reason": long_reason,
                }
            ],
        )
        section2 = _graph_section(render_markdown(doc2))
        # the machine list below the diagram intentionally carries the full reason, so pin the
        # truncation to the mermaid block alone
        mermaid2 = _mermaid_block(section2)
        self.assertIn("-->|gate yyy", mermaid2)
        self.assertNotIn(long_reason, mermaid2)

    def test_bare_ref_endpoint_resolves_to_single_segment_subgraph(self) -> None:
        # The subgraph-endpoint branch of ``_mermaid_endpoint_id``: a bare ref to a master
        # with exactly one segment resolves to that master's subgraph id.
        doc = _sprint(
            nodes=[
                {
                    "kind": "segment",
                    "ref": {"repository": REPO, "path": "master-a/task.json"},
                    "leafIds": ["A-L1"],
                },
                {"ref": {"repository": REPO, "path": "atomic-f/task.json"}},
            ],
            edges=[
                {
                    "predecessor": {"ref": {"repository": REPO, "path": "master-a/task.json"}},
                    "successor": {"ref": {"repository": REPO, "path": "atomic-f/task.json"}},
                    "reason": "gates",
                }
            ],
        )
        section = _graph_section(render_markdown(doc))
        self.assertIn('subgraph sg0["repo-a/master-a/task.json"]', section)
        self.assertIn("sg0 -->|gates| n1", section)


class ExecutionGraphTitlesReadTests(unittest.TestCase):
    """Disk-backed ``read_graph_titles`` join (L12-R1 application writers)."""

    def test_same_numbered_rows_retain_their_owning_master_identity(self) -> None:
        sprint = _sprint(
            nodes=[
                {"kind": "segment", "ref": MASTER_A.model_dump(), "leafIds": ["L1"]},
                {"ref": MASTER_B.model_dump()},
            ],
            orchestrates=["master-a", "master-b"],
        )
        master_a = TaskDocument.model_validate(
            {
                "id": "MASTER-A",
                "slug": "task",
                "kind": "master",
                "title": "Master A",
                "repo": REPO,
                "createdAt": "2026-08-15T00:00:00+00:00",
                "subTasks": [{"number": "L1", "name": "A title", "status": "planning"}],
            }
        )
        master_b = master_a.model_copy(
            update={"id": "MASTER-B", "title": "Master B", "subTasks": []}
        )
        master_b = TaskDocument.model_validate(
            {
                **master_b.model_dump(by_alias=True),
                "subTasks": [{"number": "L1", "name": "B title", "status": "planning"}],
            }
        )
        graph = sprint.executionGraph
        assert graph is not None
        titles = build_graph_titles(graph, {MASTER_A: master_a, MASTER_B: master_b})
        reversed_titles = build_graph_titles(graph, {MASTER_B: master_b, MASTER_A: master_a})
        self.assertEqual(titles, reversed_titles)
        self.assertEqual(
            titles.leaf_titles,
            {(MASTER_A, "L1"): "A title", (MASTER_B, "L1"): "B title"},
        )
        section = _graph_section(render_markdown(sprint, graph_titles=titles))
        self.assertIn('n0_l0["L1 — A title"]', section)
        self.assertNotIn("L1 — B title", section)
        missing_owner_titles = build_graph_titles(graph, {MASTER_B: master_b})
        missing_owner_section = _graph_section(
            render_markdown(sprint, graph_titles=missing_owner_titles)
        )
        self.assertIn('n0_l0["L1 — L1"]', missing_owner_section)
        self.assertNotIn("L1 — B title", missing_owner_section)

    def test_reads_master_documents_and_joins_titles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tasks_root = Path(tmp) / "tasks"
            master_root = tasks_root / REPO / "master-a"
            master_root.mkdir(parents=True)
            write_task_doc(
                master_root,
                TaskDocument.model_validate(
                    {
                        "id": "MASTER-A",
                        "slug": "master-a",
                        "title": "Title master-a",
                        "kind": "master",
                        "repo": REPO,
                        "createdAt": "2026-08-15T00:00:00+00:00",
                        "subTasks": [
                            {
                                "number": "A-L1",
                                "name": "Leaf one",
                                "status": "planning",
                            }
                        ],
                    }
                ),
            )
            doc = _sprint(nodes=[{"ref": {"repository": REPO, "path": "master-a/task.json"}}])
            graph = doc.executionGraph
            assert graph is not None
            titles = read_graph_titles(tasks_root, graph)
            self.assertEqual(titles.master_titles, {"repo-a/master-a/task.json": "Title master-a"})
            self.assertEqual(titles.leaf_titles, {(MASTER_A, "A-L1"): "Leaf one"})

    def test_tolerates_missing_and_invalid_master_documents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tasks_root = Path(tmp) / "tasks"
            invalid_root = tasks_root / REPO / "broken"
            invalid_root.mkdir(parents=True)
            (invalid_root / "task.json").write_text(
                '{"schema": "ar-task-document/v1", "kind": "master"}', encoding="utf-8"
            )
            doc = _sprint(
                nodes=[
                    {"ref": {"repository": REPO, "path": "missing/task.json"}},
                    {"ref": {"repository": REPO, "path": "broken/task.json"}},
                ]
            )
            graph = doc.executionGraph
            assert graph is not None
            titles = read_graph_titles(tasks_root, graph)
            # both the absent and the invalid master fall back to empty title maps
            self.assertEqual(titles.master_titles, {})
            self.assertEqual(titles.leaf_titles, {})
