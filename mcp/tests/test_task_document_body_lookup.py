"""On-demand task-document bodies use named inputs, not a task-corpus scan.

``read_task_document_body`` serves the dashboard's task-document endpoint. Its projection
must match corpus admission while reading the requested document, named graph masters,
owning master and explicitly listed children, never unrelated corpus documents.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.serving.projections.snapshots_impl import _common, _task_documents, _task_order
from agents_remember.serving.projections.snapshots_impl._common import (
    _iter_task_document_payloads,
    _iter_task_json,
)
from agents_remember.serving.projections.snapshots_impl._task_documents import (
    _master_docs_by_ref,
    _projected_document,
    _task_doc_node,
    _task_document_lifecycle_maps,
    _TaskDocProjectionOptions,
    read_task_document_body,
)
from agents_remember.tasks import TASK_DOCUMENT_SCHEMA, TaskDocument
from agents_remember.tasks.master_order import MasterOrder
from agents_remember.worktrees.task_resolver import ARCHIVE_DIR, ENCLOSURES_DIR
from test_observer_projection import FRESH

REPO = "repo-a"


def _write(path: Path, **fields: object) -> Path:
    payload: dict[str, object] = {
        "id": path.stem.upper(),
        "slug": path.stem,
        "title": f"Title {path.parent.name}/{path.stem}",
        "kind": "subTask",
        "repo": REPO,
        "createdAt": "2026-08-15T00:00:00+00:00",
        "objective": f"Objective of {path.stem}",
    }
    payload.update(fields)
    document = TaskDocument.model_validate(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document.model_dump(mode="json", by_alias=True)), encoding="utf-8")
    return path


def _master(path: Path, *, title: str, status: str = "inProgress") -> Path:
    return _write(
        path,
        kind="master",
        title=title,
        status=status,
        executionNature="atomic",
        subTasks=[{"number": "L1", "name": f"Leaf of {title}", "status": status}],
    )


def _node(ref_path: str) -> dict[str, object]:
    return {"ref": {"repository": REPO, "path": ref_path}}


def _corpus(root: Path, *, unrelated: int) -> dict[str, Path]:
    """A sprint, its masters, a leaf, a standalone document, decoys, and unrelated tasks.

    The decoys hold master-shaped JSON the corpus-wide join never admitted: a historical copy
    under ``notes/`` and a symlinked repository folder carry ``master-a``'s ref path with a
    wrong title and sort after the real master, so a join that admitted either would show its
    title. A second real repository folder holds the other named master under ``repo-a``'s
    identity, which the corpus-wide join keys by the document's own repo. A third holds a
    master-shaped JSON at ``master-b``'s ref path with no ``schema`` key; it would validate
    (the model defaults the schema) and win last, so only the schema filter keeps it out.
    """
    tasks = root / "tasks"
    series = tasks / REPO / "series"
    paths = {
        "master": _master(series / "task.json", title="Series master"),
        "leaf": _write(series / "01_leaf.json", master="task.md"),
        "standalone": _write(tasks / REPO / "light" / "task.json", kind="light"),
        "master-a": _master(tasks / REPO / "master-a" / "task.json", title="Master A"),
        "master-b": _master(tasks / "other-folder" / "master-b" / "task.json", title="Master B"),
        "sprint": _write(
            tasks / REPO / "sprint" / "task.json",
            kind="master",
            orchestrates=["master-a", "master-b", "missing"],
            executionGraph={
                "nodes": [
                    _node("master-a/task.json"),
                    _node("master-b/task.json"),
                    _node("missing/task.json"),
                ],
                "edges": [
                    {
                        "predecessor": {"ref": {"repository": REPO, "path": "master-a/task.json"}},
                        "successor": {"ref": {"repository": REPO, "path": "master-b/task.json"}},
                        "reason": "A gates B",
                    }
                ],
            },
        ),
    }
    _master(series / "notes" / "master-a" / "task.json", title="Historical copy")
    _master(tasks / REPO / ARCHIVE_DIR / "task.json", title="Archived")
    _master(root / "elsewhere" / "master-a" / "task.json", title="Symlinked")
    (tasks / "zz-linked-folder").symlink_to(root / "elsewhere", target_is_directory=True)
    schemaless = _master(tasks / "zz-schemaless" / "master-b" / "task.json", title="No-schema B")
    payload = json.loads(schemaless.read_text(encoding="utf-8"))
    del payload["schema"]
    schemaless.write_text(json.dumps(payload), encoding="utf-8")
    for index in range(unrelated):
        _master(tasks / REPO / f"unrelated-{index:03d}" / "task.json", title=f"U{index}")
        _write(tasks / REPO / f"unrelated-{index:03d}" / "notes" / "copy.json")
    return paths


def _corpus_wide_body(root: Path, path: Path) -> str:
    """The body as projected with the corpus-wide master join table."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    doc = _projected_document(payload)
    assert doc is not None
    docs = _iter_task_document_payloads(root / "tasks", now=None)
    node = _task_doc_node(
        doc,
        path,
        _task_document_lifecycle_maps([]),
        FRESH,
        _TaskDocProjectionOptions(include_body=True, master_docs=_master_docs_by_ref(docs)),
    )
    return node.model_dump_json(by_alias=True, exclude_none=True)


def test_every_document_kind_projects_the_same_body_as_the_corpus_wide_join(
    tmp_path: Path,
) -> None:
    paths = _corpus(tmp_path, unrelated=3)

    for name, path in paths.items():
        node = read_task_document_body(tmp_path, doc_path=str(path), enclosures=[], now=FRESH)
        assert node is not None, name
        assert node.model_dump_json(by_alias=True, exclude_none=True) == _corpus_wide_body(
            tmp_path, path.resolve()
        ), name

    sprint = read_task_document_body(
        tmp_path, doc_path=str(paths["sprint"]), enclosures=[], now=FRESH
    )
    assert sprint is not None and sprint.executionGraphView is not None
    titles = sorted(node.masterTitle for node in sprint.executionGraphView.nodes)
    assert titles == ["Master A", "Master B", f"{REPO}/missing/task.json"]
    absent = read_task_document_body(
        tmp_path, doc_path="repo-a/absent/task.json", enclosures=[], now=FRESH
    )
    assert absent is None


class _Touches:
    """Records every directory listed and every task JSON file read during one call."""

    def __init__(self) -> None:
        self.listed: list[Path] = []
        self.read: list[Path] = []


@pytest.fixture
def touches(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Touches]:
    record = _Touches()
    scandir = os.scandir
    read_json = _task_documents._read_json

    def listing(directory: Path) -> object:
        record.listed.append(Path(directory))
        return scandir(directory)

    def reading(path: Path) -> dict[str, object] | None:
        record.read.append(path)
        return read_json(path)

    monkeypatch.setattr(os, "scandir", listing)
    monkeypatch.setattr(_common, "_read_json", reading)
    monkeypatch.setattr(_task_documents, "_read_json", reading)
    monkeypatch.setattr(_task_order, "_read_json", reading)
    yield record


@pytest.mark.parametrize("document", ["leaf", "master", "sprint"])
def test_one_body_read_touches_the_same_files_whatever_the_corpus_size(
    tmp_path: Path, touches: _Touches, document: str
) -> None:
    observed = []
    for unrelated in (4, 40):
        root = tmp_path / f"corpus-{unrelated}"
        path = _corpus(root, unrelated=unrelated)[document]
        touches.listed.clear()
        touches.read.clear()
        assert read_task_document_body(root, doc_path=str(path), enclosures=[], now=FRESH)
        observed.append(
            (
                [item.relative_to(root.resolve()).as_posix() for item in touches.read],
                [item.relative_to(root.resolve()).as_posix() for item in touches.listed],
            )
        )

    assert observed[0] == observed[1]
    read, listed = observed[0]
    if document == "sprint":
        assert read == [
            "tasks/repo-a/sprint/task.json",
            "tasks/other-folder/master-b/task.json",
            "tasks/repo-a/master-a/task.json",
            "tasks/zz-schemaless/master-b/task.json",
        ]
        # The tasks root, its three real repository folders, then only the named task folders.
        assert listed[0] == "tasks"
        assert sorted(listed[1:4]) == ["tasks/other-folder", "tasks/repo-a", "tasks/zz-schemaless"]
        assert sorted(listed[4:]) == [
            "tasks/other-folder/master-b",
            "tasks/repo-a/master-a",
            "tasks/zz-schemaless/master-b",
        ]
    else:
        own = {"leaf": "01_leaf.json", "master": "task.json"}[document]
        assert read == [f"tasks/repo-a/series/{own}"] + (
            ["tasks/repo-a/series/task.json"] if document == "leaf" else []
        )
        assert listed == []


def test_enumeration_keeps_exactly_the_canonical_depth_documents_the_recursive_glob_kept(
    tmp_path: Path,
) -> None:
    _corpus(tmp_path, unrelated=2)
    tasks = tmp_path / "tasks"
    (tasks / REPO / "series" / "folder.json").mkdir()  # an entry the reader refuses
    _write(tasks / REPO / "series" / ENCLOSURES_DIR / "leaf" / "copy.json")
    _write(tasks / REPO / ENCLOSURES_DIR / "task.json")
    _write(tasks / REPO / "top-level.json")
    (tasks / REPO / "linked-task").symlink_to(tasks / REPO / "series", target_is_directory=True)

    recursive_glob = [
        path
        for path in sorted(tasks.rglob("*.json"))
        if ARCHIVE_DIR not in path.parts
        and ENCLOSURES_DIR not in path.parts
        and len(path.relative_to(tasks).parts) == 3
    ]

    enumerated = _iter_task_json(tasks)
    assert enumerated == recursive_glob
    relative = {path.relative_to(tasks).as_posix() for path in enumerated}
    assert "repo-a/series/folder.json" in relative
    assert relative.isdisjoint(
        {
            "repo-a/series/notes/master-a/task.json",
            "repo-a/top-level.json",
            f"repo-a/{ARCHIVE_DIR}/task.json",
            f"repo-a/{ENCLOSURES_DIR}/task.json",
            "zz-linked-folder/master-a/task.json",
            "repo-a/linked-task/task.json",
        }
    )


@pytest.mark.parametrize("boundary", ["owner", "listed-child"])
@pytest.mark.parametrize("schema", [None, TASK_DOCUMENT_SCHEMA, "wrong-schema"])
def test_named_order_reads_preserve_raw_corpus_admission(
    tmp_path: Path, touches: _Touches, boundary: str, schema: str | None
) -> None:
    series = tmp_path / "tasks" / REPO / "series"
    master = _write(
        series / "task.json",
        kind="master",
        executionNature="atomic",
        subTasks=[
            {"number": "L1", "name": "Leaf", "status": "planning", "file": "01_leaf.md"},
            {"number": "L2", "name": "Gate", "status": "planning", "file": "02_gate.md"},
            {"number": "L3", "name": "Missing", "status": "planning", "file": "03_missing.md"},
        ],
    )
    leaf = _write(series / "01_leaf.json", id="L1", master="task.md", waitsFor=["L2"])
    gate = _write(series / "02_gate.json", id="L2", master="task.md", waitsFor=["L1", "L404"])
    unrelated = _write(tmp_path / "tasks" / REPO / "unrelated" / "task.json", kind="light")
    target = master if boundary == "owner" else gate
    payload = json.loads(target.read_bytes())
    payload.pop("schema")
    if schema is not None:
        payload["schema"] = schema
    payload["futureVersionField"] = {"retained": True}
    target.write_text(json.dumps(payload), encoding="utf-8")
    before = {path: path.read_bytes() for path in (master, leaf, gate, unrelated)}

    body = read_task_document_body(tmp_path, doc_path=str(leaf), enclosures=[], now=FRESH)
    assert body is not None
    expected_reads = [leaf, master]
    if boundary != "owner" or schema == TASK_DOCUMENT_SCHEMA:
        expected_reads += [gate, series / "03_missing.json"]
    assert touches.read == expected_reads
    assert touches.listed == []
    corpus = _task_documents.read_task_documents(tmp_path, enclosures=[], now=FRESH)
    summary = next(node for node in corpus if node.id == "L1")
    assert (body.waitsForTasks, body.neededByTasks, body.executionGraphView) == (
        summary.waitsForTasks,
        summary.neededByTasks,
        summary.executionGraphView,
    )
    admitted_owner = boundary != "owner" or schema == TASK_DOCUMENT_SCHEMA
    assert [node.id for node in body.waitsForTasks or []] == (["L2"] if admitted_owner else [])
    assert [node.id for node in body.neededByTasks or []] == (
        ["L2"] if schema == TASK_DOCUMENT_SCHEMA else []
    )
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("size", [32, 128])
@pytest.mark.parametrize("entry", ["body", "corpus"])
def test_ready_membership_is_indexed_once_per_native_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    size: int,
    entry: str,
    record_property: Callable[[str, object], None],
) -> None:
    series = tmp_path / "tasks" / REPO / "series"
    rows = [
        {"number": f"L{i}", "name": f"Leaf {i}", "status": "planning", "file": f"{i}.md"}
        for i in range(1, size + 1)
    ]
    rows.append({"number": "L0", "name": "Landed", "status": "Completed", "file": "0.md"})
    master = _write(series / "task.json", kind="master", executionNature="atomic", subTasks=rows)
    _write(series / "0.json", id="L0", master="task.md", status="Completed")
    for i in range(1, size + 1):
        _write(series / f"{i}.json", id=f"L{i}", master="task.md", waitsFor=["L0"])

    def read() -> list[str]:
        nodes = (
            [read_task_document_body(tmp_path, doc_path=str(master), enclosures=[], now=FRESH)]
            if entry == "body"
            else _task_documents.read_task_documents(tmp_path, enclosures=[], now=FRESH)
        )
        assert all(node is not None for node in nodes)
        return [node.model_dump_json(by_alias=True, exclude_none=True) for node in nodes if node]

    control = read()
    derive = _task_order.derive_master_order
    counts = {"comparisons": 0, "iterations": 0, "items": 0}

    class CountedId(str):
        def __eq__(self, other: object) -> bool:
            counts["comparisons"] += 1
            return super().__eq__(other)

        __hash__ = str.__hash__

    class CountedReady(tuple):
        def __iter__(self) -> Iterator[str]:
            counts["iterations"] += 1
            for value in super().__iter__():
                counts["items"] += 1
                yield value

    def counted(master: TaskDocument, children: dict[str, TaskDocument]) -> MasterOrder | None:
        order = derive(master, children)
        assert order is not None and len(order.ready_ids) == size
        return replace(order, ready_ids=CountedReady(CountedId(key) for key in order.ready_ids))

    monkeypatch.setattr(_task_order, "derive_master_order", counted)
    for call in range(2):
        counts.update(comparisons=0, iterations=0, items=0)
        assert read() == control
        record_property(f"ready_operations_call_{call}", dict(counts))
        assert counts["comparisons"] <= 2 * size
        assert counts["iterations"] == 1
        assert counts["items"] == size
