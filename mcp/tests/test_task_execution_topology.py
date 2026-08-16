from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import agents_remember.tasks.document_refs as task_document_refs
import agents_remember.tasks.store as task_store
from agents_remember.application import task_doc_queue_scope
from agents_remember.application.task_doc_queue_scope import QueuePublicationScope
from agents_remember.application.task_doc_tools import (
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    _publish_task_doc_set,
    _TaskDocPublication,
    task_doc_tool,
)
from agents_remember.application.task_execution_topology import (
    ExecutionTopologyError,
    require_commanded_masters_completed,
)
from agents_remember.controlplane.closeout_queue_store import queue_store_paths
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.projections.snapshots_impl._task_documents import read_task_documents
from agents_remember.tasks import SprintExecutionGraph, TaskDocument, read_task_doc, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from pydantic import ValidationError
from test_worktree_support import git, init_repo

REPOSITORY = "agents-remember"
SPRINT = TaskDocumentRef(repository=REPOSITORY, path="sprint/task.json")
MASTER_A = TaskDocumentRef(repository=REPOSITORY, path="master-a/task.json")
MASTER_B = TaskDocumentRef(repository=REPOSITORY, path="master-b/task.json")
MASTER_C = TaskDocumentRef(repository=REPOSITORY, path="master-c/task.json")


def _config(coordination_root: Path, code_repository: Path) -> McpRuntimeConfig:
    scope = RepositoryScope(REPOSITORY, code_repository)
    return cast(
        McpRuntimeConfig,
        SimpleNamespace(
            coordination_root=coordination_root,
            repositories={REPOSITORY: scope},
        ),
    )


def _master(
    *,
    identity: str,
    orchestrates: list[str] | None = None,
    execution_nature: str | None = None,
    execution_graph: dict[str, Any] | None = None,
    title: str | None = None,
) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": identity,
            "slug": identity,
            "title": title or identity,
            "kind": "master",
            "repo": REPOSITORY,
            "type": "Master",
            "createdAt": "2026-08-15T00:00:00+00:00",
            "orchestrates": orchestrates or [],
            "executionNature": execution_nature,
            "executionGraph": execution_graph,
        }
    )


def _graph(*, reverse: bool = False) -> dict[str, Any]:
    predecessor, successor = (MASTER_B, MASTER_A) if reverse else (MASTER_A, MASTER_B)
    return {
        "nodes": [MASTER_A.model_dump(), MASTER_B.model_dump()],
        "edges": [
            {
                "predecessor": predecessor.model_dump(),
                "successor": successor.model_dump(),
                "reason": "Shared contract must land first.",
            }
        ],
    }


class ExecutionGraphSchemaTests(unittest.TestCase):
    def test_graph_derives_stable_waves_without_persisting_positions(self) -> None:
        graph = SprintExecutionGraph.model_validate(_graph())
        self.assertEqual(graph.derived_waves(), [[MASTER_A], [MASTER_B]])
        self.assertNotIn("wave", graph.model_dump(mode="json"))
        self.assertNotIn("position", graph.model_dump(mode="json"))

    def test_graph_releases_a_multi_parent_successor_only_after_every_predecessor(self) -> None:
        graph = SprintExecutionGraph.model_validate(
            {
                "nodes": [
                    MASTER_A.model_dump(),
                    MASTER_B.model_dump(),
                    MASTER_C.model_dump(),
                ],
                "edges": [
                    {
                        "predecessor": MASTER_A.model_dump(),
                        "successor": MASTER_C.model_dump(),
                        "reason": "First dependency.",
                    },
                    {
                        "predecessor": MASTER_B.model_dump(),
                        "successor": MASTER_C.model_dump(),
                        "reason": "Second dependency.",
                    },
                ],
            }
        )
        self.assertEqual(graph.derived_waves(), [[MASTER_A, MASTER_B], [MASTER_C]])

    def test_graph_refuses_duplicates_unknown_endpoints_self_edges_blank_reasons_and_cycles(
        self,
    ) -> None:
        mutations = (
            {"nodes": [MASTER_A.model_dump(), MASTER_A.model_dump()], "edges": []},
            {
                "nodes": [MASTER_A.model_dump()],
                "edges": [
                    {
                        "predecessor": MASTER_A.model_dump(),
                        "successor": MASTER_B.model_dump(),
                        "reason": "x",
                    }
                ],
            },
            {
                "nodes": [MASTER_A.model_dump()],
                "edges": [
                    {
                        "predecessor": MASTER_A.model_dump(),
                        "successor": MASTER_A.model_dump(),
                        "reason": "x",
                    }
                ],
            },
            {
                "nodes": [MASTER_A.model_dump(), MASTER_B.model_dump()],
                "edges": [
                    {
                        "predecessor": MASTER_A.model_dump(),
                        "successor": MASTER_B.model_dump(),
                        "reason": " ",
                    }
                ],
            },
            {
                "nodes": [MASTER_A.model_dump(), MASTER_B.model_dump()],
                "edges": [*_graph()["edges"], *_graph()["edges"]],
            },
            {
                "nodes": [MASTER_A.model_dump(), MASTER_B.model_dump()],
                "edges": [*_graph()["edges"], *_graph(reverse=True)["edges"]],
            },
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValidationError):
                SprintExecutionGraph.model_validate(mutation)

    def test_execution_fields_are_master_only_and_split_sprint_from_commanded_master(self) -> None:
        with self.assertRaisesRegex(ValidationError, "master-only"):
            TaskDocument.model_validate(
                {
                    **_master(identity="plain").model_dump(by_alias=True),
                    "kind": "subTask",
                    "executionNature": "atomic",
                }
            )
        with self.assertRaisesRegex(ValidationError, "has no executionNature"):
            _master(
                identity="sprint",
                orchestrates=["master-a"],
                execution_nature="atomic",
            )
        with self.assertRaisesRegex(ValidationError, "orchestration sprint"):
            _master(identity="master-a", execution_graph={"nodes": [MASTER_A.model_dump()]})


class ExecutionTopologyTests(unittest.TestCase):
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

    def test_queue_scope_split_has_direct_topology_test_ownership(self) -> None:
        self.assertTrue(callable(task_doc_queue_scope.governing_queue_scope))

    def test_queue_scope_refuses_multiple_sprints_and_wrong_leaf_owner(self) -> None:
        affected = [
            SimpleNamespace(ref=SPRINT),
            SimpleNamespace(ref=MASTER_C),
        ]
        with self.assertRaisesRegex(task_doc_queue_scope.QueueScopeError, "multiple"):
            task_doc_queue_scope._single_scope(cast(Any, affected), MASTER_A)

        leaf_ref = TaskDocumentRef(repository=REPOSITORY, path="master-a/leaf/task.json")
        leaf_path = self.tasks / leaf_ref.path
        leaf_path.parent.mkdir(parents=True)
        leaf_path.write_text("{}", encoding="utf-8")
        (self.tasks / "master-a" / "task.json").write_text("{}", encoding="utf-8")
        topology = mock.Mock()
        topology.canonical_ref.side_effect = [leaf_ref, MASTER_A]
        topology.resolve.return_value = SimpleNamespace(
            document=SimpleNamespace(kind="subTask", orchestrates=[])
        )
        topology.parent.return_value = MASTER_B
        context = task_doc_queue_scope._ScopeContext(
            topology=topology,
            repo_id=REPOSITORY,
            task_root=leaf_path.parent,
            repository_root=self.tasks,
            existing_path=leaf_path,
        )
        with (
            mock.patch.object(
                task_doc_queue_scope,
                "_unchanged_master_scope",
                return_value=QueuePublicationScope(SPRINT, MASTER_A),
            ),
            self.assertRaisesRegex(task_doc_queue_scope.QueueScopeError, "exact owning master"),
        ):
            task_doc_queue_scope._existing_scope(
                context,
                None,
                _master(identity="LEAF"),
            )

        broken_root = self.tasks / "broken"
        broken_root.mkdir()
        (broken_root / "task.json").write_text("not json\n", encoding="utf-8")
        with self.assertRaisesRegex(
            task_doc_queue_scope.QueueScopeError,
            "cannot resolve governing sprint queue",
        ):
            task_doc_queue_scope.governing_queue_scope(
                self.coord,
                REPOSITORY,
                broken_root,
                None,
                _master(identity="BROKEN"),
            )

    def test_light_task_has_no_queue_scope_and_missing_owner_fails_closed(self) -> None:
        light = _master(identity="LIGHT").model_copy(update={"kind": "light", "orchestrates": []})
        self.assertIsNone(
            task_doc_queue_scope.governing_queue_scope(
                self.coord,
                REPOSITORY,
                self.tasks / "light",
                None,
                light,
            )
        )
        publication = _TaskDocPublication(
            config=self.cfg,
            target=TaskDocTarget(repo_id=REPOSITORY, task_name="master-a"),
            task_root=self.tasks / "master-a",
            original=None,
            candidate=_master(identity="MASTER-A"),
            documents=[_master(identity="MASTER-A")],
            publisher=lambda: [],
        )
        with (
            mock.patch(
                "agents_remember.application.task_doc_tools.governing_queue_scope",
                return_value=QueuePublicationScope(SPRINT, None),
            ),
            self.assertRaisesRegex(TaskDocError, "no owning master"),
        ):
            _publish_task_doc_set(publication)

        publisher = mock.Mock(return_value=[])
        publication = replace(publication, publisher=publisher)
        with (
            mock.patch(
                "agents_remember.application.task_doc_tools.governing_queue_scope",
                side_effect=task_doc_queue_scope.QueueScopeError("broken queue scope"),
            ),
            self.assertRaisesRegex(TaskDocError, "broken queue scope"),
        ):
            _publish_task_doc_set(publication)
        publisher.assert_not_called()

    def test_completion_topology_errors_are_normalized_at_the_queue_boundary(self) -> None:
        topology = mock.Mock()
        topology.validate_execution_topology.side_effect = TaskDocumentRefError(
            "task-execution-topology-migration-required",
            "executionGraph is missing",
        )
        with self.assertRaisesRegex(
            ExecutionTopologyError,
            "task-execution-topology-migration-required",
        ):
            require_commanded_masters_completed(topology, SPRINT, {})

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_legacy(self) -> None:
        write_task_doc(
            self.tasks / "sprint",
            _master(identity="SPRINT", orchestrates=["master-a", "master-b"]).model_copy(
                update={"integrationBranch": "super"}
            ),
        )
        write_task_doc(self.tasks / "master-a", _master(identity="MASTER-A"))
        write_task_doc(self.tasks / "master-b", _master(identity="MASTER-B"))

    def _migration_fields(self) -> dict[str, Any]:
        return {
            "masters": [
                {
                    "taskDocumentRef": MASTER_A.model_dump(),
                    "executionNature": "organizational",
                },
                {
                    "taskDocumentRef": MASTER_B.model_dump(),
                    "executionNature": "atomic",
                },
            ],
            "executionGraph": _graph(),
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
            dry_run=dry_run,
        )

    def _migrate(self, *, dry_run: bool = False) -> dict[str, Any]:
        return task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id=REPOSITORY, task_name="sprint"),
            operation="migrate_execution_topology",
            edit=TaskDocEdit(fields=self._migration_fields()),
            dry_run=dry_run,
        )

    def test_legacy_documents_are_readable_but_topology_use_requires_migration(self) -> None:
        self._write_legacy()
        with self.assertRaises(TaskDocumentRefError) as raised:
            self.topology.validate_execution_topology(SPRINT)
        self.assertEqual(raised.exception.status, "task-execution-topology-migration-required")

        with self.assertRaisesRegex(TaskDocError, "migration-required"):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(repo_id=REPOSITORY, task_name="master-a"),
                operation="set_field",
                edit=TaskDocEdit(fields={"executionNature": "organizational"}),
            )

    def test_topology_refuses_a_non_sprint_and_confines_override_identity(self) -> None:
        write_task_doc(
            self.tasks / "master-a",
            _master(identity="MASTER-A", execution_nature="organizational"),
        )
        with self.assertRaises(TaskDocumentRefError) as raised:
            self.topology.validate_execution_topology(MASTER_A)
        self.assertEqual(raised.exception.status, "task-execution-graph-sprint-required")

        foreign_sprint = _master(
            identity="SPRINT",
            orchestrates=["master-a", "master-b"],
            execution_graph=_graph(),
        ).model_copy(update={"repo": "foreign"})
        with self.assertRaises(TaskDocumentRefError) as raised:
            self.topology.validate_execution_topology(
                SPRINT,
                overrides={SPRINT: foreign_sprint},
            )
        self.assertEqual(raised.exception.status, "task-document-repo-mismatch")

        outside = self.coord / "outside"
        outside.mkdir()
        escape = self.tasks / "escape"
        escape.symlink_to(outside, target_is_directory=True)
        escaped_ref = TaskDocumentRef(repository=REPOSITORY, path="escape/task.json")
        with self.assertRaises(TaskDocumentRefError) as raised:
            self.topology.validate_execution_topology(
                escaped_ref,
                overrides={escaped_ref: foreign_sprint.model_copy(update={"repo": REPOSITORY})},
            )
        self.assertEqual(raised.exception.status, "task-document-outside-root")

    def test_topology_refuses_unknown_duplicate_and_drifted_command_membership(self) -> None:
        write_task_doc(
            self.tasks / "master-a",
            _master(identity="MASTER-A", execution_nature="organizational"),
        )
        write_task_doc(
            self.tasks / "master-b",
            _master(identity="MASTER-B", execution_nature="atomic"),
        )
        cases = (
            (["master-a", "missing-master"], _graph()),
            (["master-a", "MASTER-A"], _graph()),
            (
                ["master-a", "master-b"],
                {"nodes": [MASTER_A.model_dump()], "edges": []},
            ),
        )
        for orchestrates, graph in cases:
            with self.subTest(orchestrates=orchestrates, graph=graph):
                write_task_doc(
                    self.tasks / "sprint",
                    _master(
                        identity="SPRINT",
                        orchestrates=orchestrates,
                        execution_graph=graph,
                    ),
                )
                with self.assertRaises(TaskDocumentRefError) as raised:
                    self.topology.validate_execution_topology(SPRINT)
                self.assertEqual(
                    raised.exception.status,
                    "task-execution-graph-membership-invalid",
                )

    def test_task_doc_create_completes_a_graph_and_refuses_an_alias_collision(self) -> None:
        write_task_doc(
            self.tasks / "master-b",
            _master(identity="MASTER-B", execution_nature="atomic"),
        )
        write_task_doc(
            self.tasks / "sprint",
            _master(
                identity="SPRINT",
                orchestrates=["Alias A", "master-b"],
                execution_graph=_graph(),
            ).model_copy(update={"integrationBranch": "super"}),
        )
        created = self._task_doc(
            "master-a",
            "create",
            fields=_master(
                identity="MASTER-A",
                title="Alias A",
                execution_nature="organizational",
            ).model_dump(by_alias=True),
        )
        self.assertEqual(created["status"], "planning")
        with self.assertRaisesRegex(TaskDocError, "membership-invalid"):
            self._task_doc(
                "master-c",
                "create",
                fields=_master(
                    identity="MASTER-C",
                    title="Alias A",
                    execution_nature="atomic",
                ).model_dump(by_alias=True),
            )

    def test_task_doc_set_field_and_replace_refuse_alias_drift_or_collision(self) -> None:
        write_task_doc(
            self.tasks / "master-a",
            _master(
                identity="MASTER-A",
                title="Alias A",
                execution_nature="organizational",
            ),
        )
        write_task_doc(
            self.tasks / "master-b",
            _master(identity="MASTER-B", execution_nature="atomic"),
        )
        write_task_doc(
            self.tasks / "sprint",
            _master(
                identity="SPRINT",
                orchestrates=["Alias A", "master-b"],
                execution_graph=_graph(),
            ).model_copy(update={"integrationBranch": "super"}),
        )
        with self.assertRaisesRegex(TaskDocError, "membership-invalid"):
            self._task_doc(
                "master-a",
                "set_field",
                fields={"title": "Renamed away"},
            )
        write_task_doc(self.tasks / "master-c", _master(identity="MASTER-C"))
        replacement = read_task_doc(self.tasks / "master-c" / "task.json").model_dump(by_alias=True)
        replacement["title"] = "Alias A"
        with self.assertRaisesRegex(TaskDocError, "membership-invalid"):
            self._task_doc("master-c", "replace", fields=replacement)
        changed = self._task_doc(
            "master-a",
            "set_field",
            fields={"executionNature": "atomic"},
        )
        self.assertEqual(changed["status"], "planning")

        downgraded_master = read_task_doc(self.tasks / "master-a" / "task.json").model_dump(
            by_alias=True
        )
        downgraded_master.update(
            {
                "kind": "subTask",
                "slug": "task",
                "executionNature": None,
            }
        )
        with self.assertRaisesRegex(TaskDocError, "migration-required"):
            self._task_doc("master-a", "replace", fields=downgraded_master)

        downgraded_sprint = read_task_doc(self.tasks / "sprint" / "task.json").model_dump(
            by_alias=True
        )
        downgraded_sprint.update(
            {
                "kind": "subTask",
                "slug": "task",
                "orchestrates": [],
                "integrationBranch": None,
                "executionGraph": None,
            }
        )
        with self.assertRaisesRegex(TaskDocError, "cannot remove its execution topology"):
            self._task_doc("sprint", "replace", fields=downgraded_sprint)

    def test_migration_previews_then_atomically_publishes_graph_natures_render_and_projection(
        self,
    ) -> None:
        self._write_legacy()
        before = {path: path.read_bytes() for path in self.tasks.rglob("*") if path.is_file()}
        preview = self._migrate(dry_run=True)
        self.assertEqual(preview["state"], "would-migrate")
        self.assertEqual(len(preview["documents"]), 3)
        self.assertEqual(
            preview["executionWaves"], [[MASTER_A.model_dump()], [MASTER_B.model_dump()]]
        )
        self.assertEqual(
            preview["migratedMasters"],
            [
                {
                    "taskDocumentRef": MASTER_A.model_dump(),
                    "executionNature": "organizational",
                },
                {
                    "taskDocumentRef": MASTER_B.model_dump(),
                    "executionNature": "atomic",
                },
            ],
        )
        self.assertEqual(
            before,
            {path: path.read_bytes() for path in self.tasks.rglob("*") if path.is_file()},
        )

        applied = self._migrate()
        self.assertEqual(applied["state"], "migrated")
        sprint = read_task_doc(self.tasks / "sprint" / "task.json")
        master_a = read_task_doc(self.tasks / "master-a" / "task.json")
        master_b = read_task_doc(self.tasks / "master-b" / "task.json")
        self.assertEqual(master_a.executionNature, "organizational")
        self.assertEqual(master_b.executionNature, "atomic")
        self.assertIsNotNone(sprint.executionGraph)
        assert sprint.executionGraph is not None
        self.assertEqual(sprint.executionGraph.derived_waves(), [[MASTER_A], [MASTER_B]])
        self.assertEqual(
            [item.ref for item in self.topology.validate_execution_topology(SPRINT)],
            [MASTER_A, MASTER_B],
        )
        self.assertEqual(self.topology.execution_waves(SPRINT), [[MASTER_A], [MASTER_B]])
        rendered = (self.tasks / "sprint" / "task.md").read_text(encoding="utf-8")
        self.assertIn("## Execution Graph", rendered)
        self.assertIn(f"- `{MASTER_A.key}`", rendered)
        self.assertIn(
            f"- `{MASTER_A.key}` → `{MASTER_B.key}` — Shared contract must land first.",
            rendered,
        )
        self.assertIn(f"- Wave 1: `{MASTER_A.key}`", rendered)
        self.assertIn(
            "**Execution nature:** `atomic`",
            (self.tasks / "master-b" / "task.md").read_text(encoding="utf-8"),
        )
        projected = {
            node.id: node
            for node in read_task_documents(
                self.coord,
                enclosures=[],
                now=datetime.now(UTC),
            )
        }
        self.assertEqual(projected["MASTER-A"].executionNature, "organizational")
        self.assertEqual(projected["SPRINT"].executionWaves, [[MASTER_A], [MASTER_B]])
        assert projected["SPRINT"].executionGraph is not None
        self.assertEqual(
            projected["SPRINT"].executionGraph.model_dump(mode="json"),
            _graph(),
        )

    def test_execution_waves_validates_and_returns_one_pinned_sprint_snapshot(self) -> None:
        self._write_legacy()
        self._migrate()
        sprint_path = self.tasks / "sprint" / "task.json"
        real_read = task_document_refs.read_task_doc
        sprint_reads = 0

        def drift_after_first_sprint_read(path: Path) -> TaskDocument:
            nonlocal sprint_reads
            if path == sprint_path:
                sprint_reads += 1
                if sprint_reads > 1:
                    return _master(
                        identity="SPRINT",
                        orchestrates=["master-a", "master-b"],
                    )
            return real_read(path)

        with mock.patch.object(
            task_document_refs,
            "read_task_doc",
            side_effect=drift_after_first_sprint_read,
        ):
            self.assertEqual(self.topology.execution_waves(SPRINT), [[MASTER_A], [MASTER_B]])
        self.assertGreaterEqual(sprint_reads, 2)

    def test_migration_refuses_non_exact_membership_and_rolls_back_cross_root_failure(self) -> None:
        self._write_legacy()
        incomplete = self._migration_fields()
        incomplete["masters"] = incomplete["masters"][:1]
        incomplete["executionGraph"] = {
            "nodes": [MASTER_A.model_dump()],
            "edges": [],
        }
        with self.assertRaisesRegex(TaskDocError, "membership must exactly match"):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(repo_id=REPOSITORY, task_name="sprint"),
                operation="migrate_execution_topology",
                edit=TaskDocEdit(fields=incomplete),
            )

        before = {
            path: path.read_bytes()
            for path in self.tasks.rglob("*")
            if path.is_file() and path.suffix in {".json", ".md"}
        }
        real_write = task_store.atomic_write_text
        calls = 0

        def fail_third_write(path: Path, text: str) -> None:
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("forced cross-root publication failure")
            real_write(path, text)

        with (
            mock.patch.object(task_store, "atomic_write_text", side_effect=fail_third_write),
            self.assertRaisesRegex(OSError, "forced cross-root"),
        ):
            self._migrate()
        self.assertEqual(
            before,
            {
                path: path.read_bytes()
                for path in self.tasks.rglob("*")
                if path.is_file() and path.suffix in {".json", ".md"}
            },
        )
        state_path, pending_path = queue_store_paths(self.coord, SPRINT)
        self.assertFalse(state_path.exists())
        self.assertFalse(pending_path.exists())

    def test_migration_refuses_invalid_request_shapes_before_reading_or_writing(self) -> None:
        self._write_legacy()
        invalid_requests = (
            (
                "at least 1 item",
                {"masters": [], "executionGraph": _graph()},
            ),
            (
                "must be unique",
                {
                    "masters": [
                        {
                            "taskDocumentRef": MASTER_A.model_dump(),
                            "executionNature": "organizational",
                        },
                        {
                            "taskDocumentRef": MASTER_A.model_dump(),
                            "executionNature": "atomic",
                        },
                    ],
                    "executionGraph": {
                        "nodes": [MASTER_A.model_dump()],
                        "edges": [],
                    },
                },
            ),
            (
                "must exactly match",
                {
                    "masters": [
                        {
                            "taskDocumentRef": MASTER_A.model_dump(),
                            "executionNature": "organizational",
                        }
                    ],
                    "executionGraph": {
                        "nodes": [MASTER_B.model_dump()],
                        "edges": [],
                    },
                },
            ),
        )
        before = {path: path.read_bytes() for path in self.tasks.rglob("*") if path.is_file()}
        for expected, fields in invalid_requests:
            with self.subTest(expected=expected), self.assertRaisesRegex(TaskDocError, expected):
                task_doc_tool(
                    self.cfg,
                    TaskDocTarget(repo_id=REPOSITORY, task_name="sprint"),
                    operation="migrate_execution_topology",
                    edit=TaskDocEdit(fields=fields),
                )
        self.assertEqual(
            before,
            {path: path.read_bytes() for path in self.tasks.rglob("*") if path.is_file()},
        )

    def test_migration_refuses_missing_or_non_sprint_target(self) -> None:
        with self.assertRaisesRegex(TaskDocError, "task document not found"):
            self._migrate()

        write_task_doc(self.tasks / "sprint", _master(identity="SPRINT"))
        with self.assertRaisesRegex(TaskDocError, "requires an orchestration sprint"):
            self._migrate()

    def test_migration_refuses_unresolved_or_non_master_entries(self) -> None:
        self._write_legacy()
        missing_fields = self._migration_fields()
        missing_fields["masters"][1]["taskDocumentRef"] = MASTER_C.model_dump()
        missing_fields["executionGraph"] = {
            "nodes": [MASTER_A.model_dump(), MASTER_C.model_dump()],
            "edges": [],
        }
        with self.assertRaisesRegex(TaskDocError, "does not exist"):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(repo_id=REPOSITORY, task_name="sprint"),
                operation="migrate_execution_topology",
                edit=TaskDocEdit(fields=missing_fields),
            )

        write_task_doc(
            self.tasks / "master-b",
            TaskDocument.model_validate(
                {
                    "id": "MASTER-B",
                    "slug": "task",
                    "title": "MASTER-B",
                    "kind": "subTask",
                    "repo": REPOSITORY,
                    "type": "Code",
                    "createdAt": "2026-08-15T00:00:00+00:00",
                }
            ),
        )
        with self.assertRaisesRegex(TaskDocError, "not a commanded master"):
            self._migrate()

    def test_regular_master_edit_refuses_a_task_root_outside_the_repository(self) -> None:
        outside_contract = self.coord / "outside" / "series-contract.md"
        with self.assertRaisesRegex(TaskDocError, "outside tasks/agents-remember"):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(
                    repo_id=REPOSITORY,
                    contract_path=outside_contract.as_posix(),
                ),
                operation="create",
                edit=TaskDocEdit(
                    fields=_master(
                        identity="OUTSIDE",
                        execution_nature="organizational",
                    ).model_dump(by_alias=True)
                ),
            )
        self.assertFalse((outside_contract.parent / "task.json").exists())
        self.assertFalse((outside_contract.parent / "task.md").exists())

    def test_migration_normalizes_an_out_of_root_sprint_to_task_doc_error(self) -> None:
        outside = self.coord / "outside"
        write_task_doc(
            outside,
            _master(identity="SPRINT", orchestrates=["master-a", "master-b"]),
        )
        with self.assertRaisesRegex(TaskDocError, "outside tasks/agents-remember"):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(
                    repo_id=REPOSITORY,
                    contract_path=(outside / "series-contract.md").as_posix(),
                ),
                operation="migrate_execution_topology",
                edit=TaskDocEdit(fields=self._migration_fields()),
            )


if __name__ == "__main__":
    unittest.main()
