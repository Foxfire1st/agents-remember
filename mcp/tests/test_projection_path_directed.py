"""MIK-R81 rule 2 (F09): one-change reads ignore unrelated corpus size.

Documentary edits, appends, deletes and declared-row changes re-read only the
inputs they touch. Reads stay flat as unrelated documents grow; canonical task
entries are byte-equal to a full reference at the same clock.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.delta import stable_projection_state
from agents_remember.serving.projections.projection_inputs import (
    ProjectionDomain,
    ProjectionInputs,
    ProjectionInputState,
    ProjectionReaders,
    ProjectionRefresh,
    RefreshPass,
)
from agents_remember.serving.projections.projection_store import (
    project_and_write,
)
from agents_remember.serving.projections.snapshots_impl import _task_document_closure

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)

DOC = {
    "schema": "ar-task-document/v1",
    "id": "t1",
    "slug": "t1",
    "title": "one",
    "kind": "light",
    "status": "planning",
    "repo": "r",
    "createdAt": "2026-10-13T00:00:00+00:00",
}

MASTER = {
    **DOC,
    "id": "m1",
    "slug": "m1",
    "title": "master one",
    "kind": "master",
    "subTasks": [
        {"file": "s1.json", "name": "sub one", "number": "1", "status": "planning"},
    ],
}

SUBTASK = {
    **DOC,
    "id": "s1",
    "slug": "s1",
    "title": "sub one",
    "kind": "subTask",
    "master": "master.json",
    "status": "planning",
}


def _config(root: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root,
        transcript_root=root / "logs" / "mcp",
    )


class _ReadCounter:
    """Counts content reads (not stats) under the projection input trees."""

    def __init__(self, roots: list[Path]) -> None:
        self.roots = roots
        self.reads: list[str] = []
        self._real_read_text = Path.read_text
        self._real_read_bytes = Path.read_bytes

    def __enter__(self) -> _ReadCounter:
        counter = self

        def read_text(path: Path, *args: object, **kwargs: object) -> str:
            for root in counter.roots:
                try:
                    path.relative_to(root)
                except ValueError:
                    continue
                counter.reads.append(path.as_posix())
                break
            return counter._real_read_text(path, *args, **kwargs)  # type: ignore[arg-type]

        def read_bytes(path: Path, *args: object, **kwargs: object) -> bytes:
            for root in counter.roots:
                try:
                    path.relative_to(root)
                except ValueError:
                    continue
                counter.reads.append(path.as_posix())
                break
            return counter._real_read_bytes(path, *args, **kwargs)  # type: ignore[arg-type]

        Path.read_text = read_text  # type: ignore[method-assign]
        Path.read_bytes = read_bytes  # type: ignore[method-assign]
        return self

    def __exit__(self, *exc: object) -> None:
        Path.read_text = self._real_read_text  # type: ignore[method-assign]
        Path.read_bytes = self._real_read_bytes  # type: ignore[method-assign]

    def distinct_under(self, *names: str) -> set[str]:
        return {read for read in self.reads for name in names if f"/{name}/" in read}


@dataclass
class _World:
    root: Path
    observer: Path
    config: McpRuntimeConfig
    calls: dict[str, int] = field(default_factory=lambda: {"surfaces": 0})


class PathDirectedRefreshTests(unittest.TestCase):
    def _world(self, tmp: str, extra: int) -> _World:
        root = Path(tmp)
        tasks = root / "tasks"
        (tasks / "r" / "t1").mkdir(parents=True)
        (tasks / "r" / "t1" / "task.json").write_text(json.dumps(DOC), encoding="utf-8")
        (tasks / "r" / "t1" / "master.json").write_text(json.dumps(MASTER), encoding="utf-8")
        (tasks / "r" / "t1" / "s1.json").write_text(json.dumps(SUBTASK), encoding="utf-8")
        (tasks / "r" / "t2").mkdir(parents=True)
        (tasks / "r" / "t2" / "task.json").write_text(
            json.dumps({**DOC, "id": "t2", "slug": "t2", "title": "two"}), encoding="utf-8"
        )
        (tasks / "r" / "t2" / "master.json").write_text(
            json.dumps(
                {
                    **MASTER,
                    "id": "m2",
                    "slug": "m2",
                    "subTasks": [
                        {
                            "file": "../t1/s1.json",
                            "name": "linked sub",
                            "number": "1",
                            "status": "planning",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        for index in range(extra):
            extra_dir = tasks / "r" / f"tx{index}"
            extra_dir.mkdir(parents=True)
            extra_dir.joinpath("task.json").write_text(
                json.dumps({**DOC, "id": f"tx{index}", "slug": f"tx{index}"}),
                encoding="utf-8",
            )
        observer = root / "logs" / "observer"
        (observer / "workspace").mkdir(parents=True)
        return _World(root, observer, _config(root))

    def _readers(self, world: _World) -> ProjectionReaders:
        def count_surfaces(cfg: McpRuntimeConfig, moment: datetime) -> tuple:
            world.calls["surfaces"] += 1
            return ([], [], [])

        return ProjectionReaders(
            repo_surfaces=count_surfaces,
            landing_state=None,
        )

    def _prime(self, world: _World) -> ProjectionInputState:
        state = ProjectionInputState()
        state.read(
            world.config,
            self._readers(world),
            observer_root=world.observer,
            pass_=RefreshPass(now=NOW, refresh=ProjectionRefresh.full()),
        )
        return state

    def _change(
        self,
        state: ProjectionInputState,
        world: _World,
        changed: Path,
        domain: ProjectionDomain,
    ) -> ProjectionInputs:
        return state.read(
            world.config,
            self._readers(world),
            observer_root=world.observer,
            pass_=RefreshPass(
                now=NOW,
                refresh=ProjectionRefresh.change(
                    frozenset({domain}),
                    paths=(changed.as_posix(),),
                    paths_complete=True,
                ),
            ),
        )

    def _full_reference(self, world: _World) -> ProjectionInputs:
        state = ProjectionInputState()
        return state.read(
            world.config,
            self._readers(world),
            observer_root=world.observer,
            pass_=RefreshPass(now=NOW, refresh=ProjectionRefresh.full()),
        )

    def _dump(self, inputs: ProjectionInputs, world: _World) -> dict[str, object]:
        # The complete production reducer consumes each actual incremental/reference snapshot.
        with mock.patch.object(ProjectionInputState, "read", return_value=inputs):
            projection = project_and_write(world.config, now=NOW)
        return {
            "projectionBytes": json.dumps(
                asdict(stable_projection_state(projection)), sort_keys=True, separators=(",", ":")
            ),
            "tasks": [node.model_dump(mode="json", by_alias=True) for node in projection.tasks],
            "task_sources": [
                source.summary.model_dump(mode="json", by_alias=True)
                for source in inputs.task_sources
            ],
            "closeout_queues": [
                node.model_dump(mode="json", by_alias=True) for node in inputs.closeout_queues
            ],
        }

    def _one_change_case(
        self,
        extra: int,
        mutate: Callable[[Path, Path], None],
        changed: Callable[[Path], Path],
        domain: ProjectionDomain,
    ) -> tuple[dict[str, object], dict[str, object], set[str]]:
        with tempfile.TemporaryDirectory() as tmp:
            world = self._world(tmp, extra)
            root, observer = world.root, world.observer
            state = self._prime(world)
            mutate(root, observer)
            with _ReadCounter([root / "tasks", observer]) as counter:
                incremental = self._change(state, world, changed(root), domain)
            with _ReadCounter([root / "tasks", observer]):
                reference = self._full_reference(world)
            return self._dump(incremental, world), self._dump(reference, world), set(counter.reads)

    def test_documentary_rewrite_is_path_local_and_byte_equal(self) -> None:
        """One keystroke re-reads its document, not the corpus (F09)."""
        doc_rel = Path("tasks") / "r" / "t1" / "task.json"

        def mutate(root: Path, observer: Path) -> None:
            (root / doc_rel).write_text(
                json.dumps({**DOC, "title": "one edited"}), encoding="utf-8"
            )

        small = self._one_change_case(
            0, mutate, lambda root: root / doc_rel, ProjectionDomain.TASKS
        )
        big = self._one_change_case(8, mutate, lambda root: root / doc_rel, ProjectionDomain.TASKS)
        for incremental, reference, _reads in (small, big):
            self.assertEqual(incremental, reference)
        self.assertGreater(len(small[2]), 0, "the changed document is actually read")
        self.assertGreater(len(big[2]), 0, "the changed document is actually read")
        self.assertEqual(
            {Path(read).name for read in small[2]},
            {Path(read).name for read in big[2]},
            "one-change reads ignore unrelated corpus size",
        )
        self.assertLessEqual(len(big[2]), 6, f"unexpected reads: {sorted(big[2])}")

    def test_ordered_sibling_edit_refreshes_master_and_both_dependency_lists(self) -> None:
        """Breaking a retained cycle keeps L162 order data byte-equal without corpus reads."""
        reads = []
        for extra in (0, 8):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as tmp:
                world = self._world(tmp, extra)
                folder = world.root / "tasks/r/t1"
                master = {
                    **MASTER,
                    "subTasks": [
                        {"number": key, "name": key, "file": f"{key}.md"} for key in ("s1", "s2")
                    ],
                }
                (folder / "master.json").write_text(json.dumps(master))
                for key, waits in (("s1", ["s2"]), ("s2", ["s1"])):
                    payload = {
                        **SUBTASK,
                        "id": key,
                        "slug": key,
                        "master": "master.md",
                        "waitsFor": waits,
                    }
                    (folder / f"{key}.json").write_text(json.dumps(payload))
                state = self._prime(world)
                changed = folder / "s2.json"
                payload = json.loads(changed.read_text())
                changed.write_text(json.dumps({**payload, "waitsFor": []}))
                with _ReadCounter([world.root / "tasks", world.observer]) as counter:
                    actual = self._change(state, world, changed, ProjectionDomain.TASKS)
                reference = self._full_reference(world)
                self.assertEqual(self._dump(actual, world), self._dump(reference, world))
                nodes = {source.summary.id: source.summary for source in actual.task_sources}
                self.assertEqual(nodes["s1"].neededByTasks, [])
                self.assertEqual(nodes["s2"].waitsForTasks, [])
                graph = nodes["m1"].executionGraphView
                assert graph is not None
                self.assertEqual([node.frontierState for node in graph.nodes], ["waiting", "ready"])
                self.assertTrue(counter.reads, "the changed file is actually read")
                reads.append({Path(path).name for path in counter.reads})
        self.assertEqual(reads[0], reads[1], "unrelated corpus growth adds no reads")

    def test_append_delete_and_cross_document_join(self) -> None:
        """Append, delete and subtask->master joins merge byte-equal (F09)."""
        new_rel = Path("tasks") / "r" / "t1" / "extra.json"
        sub_rel = Path("tasks") / "r" / "t1" / "s1.json"

        def mutate_append(root: Path, observer: Path) -> None:
            (root / new_rel).write_text(
                json.dumps({**DOC, "id": "extra", "slug": "extra"}), encoding="utf-8"
            )

        incremental, reference, reads = self._one_change_case(
            0, mutate_append, lambda root: root / new_rel, ProjectionDomain.TASKS
        )
        self.assertEqual(incremental, reference)
        self.assertTrue(any("extra.json" in read for read in reads), "the appended file is read")

        def mutate_delete(root: Path, observer: Path) -> None:
            (root / new_rel).write_text(
                json.dumps({**DOC, "id": "extra", "slug": "extra"}), encoding="utf-8"
            )

        with tempfile.TemporaryDirectory() as tmp:
            world = self._world(tmp, 0)
            root, observer = world.root, world.observer
            state = self._prime(world)
            mutate_delete(root, observer)
            self._change(state, world, root / new_rel, ProjectionDomain.TASKS)
            (root / new_rel).unlink()
            with _ReadCounter([root / "tasks", observer]):
                incremental = self._change(state, world, root / new_rel, ProjectionDomain.TASKS)
            reference = self._full_reference(world)
            self.assertEqual(self._dump(incremental, world), self._dump(reference, world))
            self.assertNotIn("extra", json.dumps(self._dump(incremental, world)))

        def mutate_cross(root: Path, observer: Path) -> None:
            (root / sub_rel).write_text(
                json.dumps({**SUBTASK, "createdAt": "2026-10-13T01:00:00+00:00"}),
                encoding="utf-8",
            )

        incremental, reference, reads = self._one_change_case(
            0, mutate_cross, lambda root: root / sub_rel, ProjectionDomain.TASKS
        )
        self.assertEqual(incremental, reference)
        sources = incremental["task_sources"]
        assert isinstance(sources, list)
        child = next(node for node in sources if node["id"] == "s1")
        self.assertEqual(child["createdAt"], "2026-10-13T01:00:00+00:00")
        # Reciprocal membership chooses one parent; the child's own entry owns its facts.
        tasks = incremental["tasks"]
        assert isinstance(tasks, list)
        parents = [node for node in tasks if node["id"] in {"m1", "m2"}]
        self.assertEqual(len(parents), 2)
        self.assertEqual(sum(len(parent["children"]) for parent in parents), 1)
        child_entry = next(node for node in tasks if node["id"] == "s1")
        self.assertEqual(
            child_entry["parent"], next(node["key"] for node in parents if node["id"] == "m1")
        )
        owner = next(parent for parent in parents if parent["children"])
        entry = next(node for node in tasks if node["id"] == "s1")
        self.assertEqual(entry["parent"], owner["key"])
        self.assertEqual(owner["children"], [entry["key"]])

    def test_atomic_rewrites_and_deletes_at_both_sizes(self) -> None:
        cases = (
            ("task-rewrite", Path("tasks/r/t1/task.json"), ProjectionDomain.TASKS),
            ("task-delete", Path("tasks/r/t1/task.json"), ProjectionDomain.TASKS),
        )
        for name, relative, domain in cases:
            read_counts = []
            for extra in (0, 8):
                with self.subTest(case=name, extra=extra), tempfile.TemporaryDirectory() as tmp:
                    world = self._world(tmp, extra)
                    state = self._prime(world)
                    target = world.root / relative
                    if name.endswith("delete"):
                        target.unlink()
                    else:
                        content = json.dumps({**DOC, "title": "atomic replacement"})
                        replacement = target.with_suffix(".replacement")
                        replacement.write_text(content)
                        replacement.replace(target)
                    with _ReadCounter([world.root / "tasks", world.observer]) as counter:
                        actual = self._change(state, world, target, domain)
                    read_counts.append(len(counter.reads))
                    reference = self._full_reference(world)
                    self.assertEqual(self._dump(actual, world), self._dump(reference, world))
            self.assertEqual(read_counts[0], read_counts[1], (name, read_counts))

    def test_new_membership_and_cross_folder_consumers_at_both_sizes(self) -> None:
        counts = []
        for extra in (0, 8):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as tmp:
                world = self._world(tmp, extra)
                state = self._prime(world)
                appended = world.root / "tasks/r/t1/extra.json"
                appended.write_text(json.dumps({**DOC, "id": "extra", "slug": "extra"}))
                actual = self._change(state, world, appended, ProjectionDomain.TASKS)
                self.assertEqual(
                    self._dump(actual, world), self._dump(self._full_reference(world), world)
                )
                appended.unlink()
                actual = self._change(state, world, appended, ProjectionDomain.TASKS)
                self.assertEqual(
                    self._dump(actual, world), self._dump(self._full_reference(world), world)
                )
                subtask = world.root / "tasks/r/t1/s1.json"
                replacement = subtask.with_suffix(".replacement")
                replacement.write_text(
                    json.dumps({**SUBTASK, "createdAt": "2026-10-13T01:00:00+00:00"})
                )
                replacement.replace(subtask)
                with _ReadCounter([world.root / "tasks", world.observer]) as counter:
                    actual = self._change(state, world, subtask, ProjectionDomain.TASKS)
                counts.append(len(counter.reads))
                self.assertEqual(
                    self._dump(actual, world), self._dump(self._full_reference(world), world)
                )
                child = next(source for source in actual.task_sources if source.summary.id == "s1")
                self.assertEqual(child.summary.createdAt, "2026-10-13T01:00:00+00:00")
                consumers = [
                    source for source in actual.task_sources if source.summary.kind == "master"
                ]
                self.assertEqual(len(consumers), 2)
                for consumer in consumers:
                    self.assertEqual(len(consumer.children), 1)
                    assert consumer.children[0].reference is not None
                    self.assertEqual(consumer.children[0].reference.path, "t1/s1.json")
        self.assertGreater(counts[0], 0)
        self.assertEqual(counts[0], counts[1])

    def test_closure_validation_ignores_unrelated_document_count(self) -> None:
        counts = []
        for extra in (0, 8):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as tmp:
                world = self._world(tmp, extra)
                state = self._prime(world)
                doc = world.root / "tasks/r/t1/task.json"
                doc.write_text(json.dumps({**DOC, "title": "edited"}))
                with mock.patch.object(
                    _task_document_closure,
                    "_projected_document",
                    wraps=_task_document_closure._projected_document,
                ) as validate:
                    self._change(state, world, doc, ProjectionDomain.TASKS)
                counts.append(validate.call_count)
        self.assertGreater(counts[0], 0)
        self.assertEqual(counts[0], counts[1])
