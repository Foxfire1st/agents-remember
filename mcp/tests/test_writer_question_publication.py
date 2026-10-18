"""MIK-R54 keyed open questions use the task document's own publication."""

from __future__ import annotations

import argparse
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from agents_remember.application import knowledge_write_route as write_body
from agents_remember.application.knowledge_writer import (
    Owner,
    WriteRequest,
    open_questions,
    write_knowledge,
)
from agents_remember.application.knowledge_writer.open_questions import TaskDocOpenQuestions
from agents_remember.application.task_docs import task_doc_publication
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.cli import knowledge_write_route
from agents_remember.kernel.file_lock import try_file_lease
from agents_remember.models.knowledge_files.reconsideration import question_key
from agents_remember.tasks import TaskDocument
from agents_remember.worktrees.worktree_contract import load_contract
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from knowledge_writer_test_support import build_world, tree_bytes
from reconsideration_test_support import write_master
from test_knowledge_worklist import World as ReconsiderWorld
from test_reconsideration_surfacing import LEAF, S12, S18, _config

pytest_plugins = ("test_reconsideration_surfacing",)


def setup_port(tmp_path: Path) -> tuple[TaskDocOpenQuestions, Path]:
    coordination = tmp_path / "coordination"
    task_root = coordination / "tasks/agents-remember/260928_case"
    task_root.mkdir(parents=True)
    leaf = TaskDocument.model_validate(
        {
            "id": LEAF,
            "slug": "99_leaf",
            "title": "Leaf",
            "kind": "subTask",
            "repo": "agents-remember",
            "createdAt": "2026-09-29T00:00+02:00",
            "objective": "Surface reconsideration.",
            "openQuestions": ["An earlier question?"],
        }
    )
    path = task_root / "99_leaf.json"
    path.write_text(leaf.model_dump_json(by_alias=True, exclude_none=True), "utf-8")
    write_master(task_root, leaf)
    return TaskDocOpenQuestions(
        _config(coordination),
        "agents-remember",
        task_root / "series-contract.md",
        task_root,
        LEAF,
    ), path


def test_keyed_put_appends_then_replaces_and_dry_run_leaves_bytes(tmp_path: Path) -> None:
    port, path = setup_port(tmp_path)
    target = TaskDocTarget(port.repo_id, contract_path=str(port.contract_path), slug="99_leaf")
    before = path.read_bytes()
    task_doc_tool(
        port.config,
        target,
        operation="put_open_question",
        edit=TaskDocEdit(fields={"key": "[key]", "text": "[key] first"}),
        call=TaskDocCall(dry_run=True),
    )
    assert path.read_bytes() == before
    assert port.append("[key]", "[key] first") is None
    assert port.append("[key]", "[key] reworded") is None
    assert json.loads(path.read_text())["openQuestions"] == [
        "An earlier question?",
        "[key] reworded",
    ]


def test_question_added_between_check_and_append_is_preserved(tmp_path: Path) -> None:
    port, path = setup_port(tmp_path)
    assert port.check("[key]", "[key] first") is None
    assert port.append("[other]", "[other] intervening") is None
    assert port.append("[key]", "[key] first") is None
    assert json.loads(path.read_text())["openQuestions"] == [
        "An earlier question?",
        "[other] intervening",
        "[key] first",
    ]


def test_native_returned_master_sync_failure_refuses_port_and_writer(
    world: ReconsiderWorld, tmp_path: Path
) -> None:
    port, path = setup_port(tmp_path / "questions")
    leaf = json.loads(path.read_bytes())
    leaf["master"] = "task.md"
    path.write_text(json.dumps(leaf))
    (path.parent / "task.json").write_text('{"schema":"ar-task-document/v1",')
    before = tree_bytes(path.parent), tree_bytes(world.memory)
    for call in (port.check, port.append):
        refusal = call("[key]", "[key] question")
        assert refusal and "master-sync-failed" in refusal
    report = write_knowledge(
        WriteRequest(
            world.memory,
            world.code,
            Owner("260928-MIK", "leaf", LEAF),
            "handoff.json",
            {"history": [{"subject": S18, "disposition": "raise", "reason": "Review it."}]},
            commit=True,
            questions=port,
            worklist={"items": []},
        )
    )
    assert report.refused and "master-sync-failed" in report.render()
    assert not report.questions_published
    assert (tree_bytes(path.parent), tree_bytes(world.memory)) == before


def test_loaded_settings_root_mismatch_refuses_raise_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = build_world(tmp_path / "writer")
    port, path = setup_port(tmp_path / "questions")
    port.config.config_path.write_text("{}")
    monkeypatch.setattr(write_body, "load_config", lambda _path: port.config)
    questions = knowledge_write_route.LeafQuestions(
        argparse.Namespace(config=str(port.config.config_path)), load_contract(world.contract)
    )
    before = path.read_bytes()
    refusal = questions.check("[key]", "[key] raised")
    assert refusal is not None
    assert str(port.config.coordination_root) in refusal
    assert str(world.root) in refusal
    assert str(port.config.config_path) in refusal
    assert path.read_bytes() == before


@pytest.mark.parametrize("replace_existing", [False, True])
def test_refused_multi_raise_keeps_no_own_append_or_replacement(
    world: ReconsiderWorld, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replace_existing: bool
) -> None:
    port, path = setup_port(tmp_path / "questions")
    if replace_existing:
        key = question_key(S18, LEAF)
        assert port.append(key, f"{key} original") is None
    original_questions = json.loads(path.read_bytes())["openQuestions"]
    memory = tree_bytes(world.memory)
    native = open_questions.task_doc_tool
    publications = []

    def refuse_later(*args, **kwargs):
        if not kwargs["call"].dry_run:
            publications.append(kwargs["edit"].fields)
            if len(publications) == 1:
                native(
                    port.config,
                    TaskDocTarget(
                        port.repo_id, contract_path=str(port.contract_path), slug=path.stem
                    ),
                    operation="put_open_question",
                    edit=TaskDocEdit(fields={"key": "[foreign]", "text": "[foreign] intervening"}),
                    call=TaskDocCall(),
                )
            if "questions" in kwargs["edit"].fields or len(publications) == 2:
                raise OSError("injected later question publication refusal")
        return native(*args, **kwargs)

    monkeypatch.setattr(open_questions, "task_doc_tool", refuse_later)
    report = write_knowledge(
        WriteRequest(
            world.memory,
            world.code,
            Owner("260928-MIK", "leaf", LEAF),
            "handoff.json",
            {
                "history": [
                    {"subject": subject, "disposition": "raise", "reason": "Review it."}
                    for subject in (S18, S12)
                ]
            },
            commit=True,
            questions=port,
            worklist={"items": []},
        )
    )
    assert report.refused and "injected later question" in report.render()
    assert tree_bytes(world.memory) == memory
    assert json.loads(path.read_bytes())["openQuestions"] == [
        *original_questions,
        "[foreign] intervening",
    ]
    assert not report.questions_published
    assert len(publications) == 1


def test_failed_memory_write_reports_published_question_and_retry_is_keyed(
    world: ReconsiderWorld,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, task_path = setup_port(tmp_path / "questions")
    before = tree_bytes(world.memory)
    original = Path.replace
    replacements = []

    def failing_replace(path: Path, destination: str | Path) -> Path:
        if Path(destination).is_relative_to(world.memory):
            replacements.append(str(destination))
            if len(replacements) == 2:
                raise OSError("memory disk failure after question publication")
        return original(path, destination)

    monkeypatch.setattr(Path, "replace", failing_replace)

    def raised(reason: str):
        return write_knowledge(
            WriteRequest(
                world.memory,
                world.code,
                Owner("260928-MIK", "leaf", LEAF),
                "handoff.json",
                {"history": [{"subject": S18, "disposition": "raise", "reason": reason}]},
                commit=True,
                questions=port,
                worklist={"items": []},
            )
        )

    refused = raised("First reason.")
    assert refused.refused, refused.render()
    assert tree_bytes(world.memory) == before
    assert str(task_path) in refused.render()
    assert "question was appended before" in refused.render()
    assert "same command, run again" in refused.render()
    assert "appends nothing twice" in refused.render()
    questions = json.loads(task_path.read_text())["openQuestions"]
    assert len(questions) == 2
    written = raised("Reworded reason.")
    assert written.state == "written", written.render()
    after = json.loads(task_path.read_text())["openQuestions"]
    assert len(after) == 2 and "Reworded reason." in after[1]
    assert str(task_path) in written.render()


def test_native_question_batch_validates_every_key_before_replacing_any(tmp_path: Path) -> None:
    port, path = setup_port(tmp_path / "questions")
    assert port.append("[one]", "[one] original") is None
    before = tree_bytes(path.parent)
    refusal = port.append_many([("[one]", "[one] changed"), ("[two]", "not keyed")])
    assert refusal and "put_open_question requires" in refusal
    assert tree_bytes(path.parent) == before
    assert port.append_many([("[one]", "[one] changed"), ("[two]", "[two] second")]) is None
    assert json.loads(path.read_bytes())["openQuestions"] == [
        "An earlier question?",
        "[one] changed",
        "[two] second",
    ]


def test_native_multi_raise_publishes_one_keyed_batch_and_rewords_on_retry(
    world: ReconsiderWorld, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port, path = setup_port(tmp_path / "questions")
    native = open_questions.task_doc_tool
    writes = []

    def capture(*args, **kwargs):
        if not kwargs["call"].dry_run:
            writes.append(kwargs["edit"].fields)
        return native(*args, **kwargs)

    monkeypatch.setattr(open_questions, "task_doc_tool", capture)
    for reason in ("First reason.", "Reworded reason."):
        report = write_knowledge(
            WriteRequest(
                world.memory,
                world.code,
                Owner("260928-MIK", "leaf", LEAF),
                "handoff.json",
                {
                    "history": [
                        {"subject": subject, "disposition": "raise", "reason": reason}
                        for subject in (S18, S12)
                    ]
                },
                commit=True,
                questions=port,
                worklist={"items": []},
            )
        )
        assert report.state == "written", report.render()
        assert len(report.questions_published) == 2
        questions = json.loads(path.read_bytes())["openQuestions"]
        assert len(questions) == 3 and questions[0] == "An earlier question?"
        assert all(reason in question for question in questions[1:])
    assert len(writes) == 2 and all(len(fields["questions"]) == 2 for fields in writes)


@pytest.mark.parametrize("replace_existing", [False, True])
def test_native_refresh_failure_refuses_nested_publication_and_restores_sources(
    world: ReconsiderWorld, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replace_existing: bool
) -> None:
    port, path = setup_port(tmp_path / "questions")
    if replace_existing:
        key = question_key(S18, LEAF)
        assert port.append(key, key + " original") is None
    before = tree_bytes(path.parent), tree_bytes(world.memory)
    original = task_doc_publication.refresh_task_document_copies
    nested = []

    def fail_refresh(*args, **kwargs):
        if nested:
            return original(*args, **kwargs)
        result = task_doc_tool(
            port.config,
            TaskDocTarget(port.repo_id, contract_path=str(port.contract_path), slug=path.stem),
            operation="put_open_question",
            edit=TaskDocEdit(fields={"key": "[foreign]", "text": "[foreign] nested"}),
            call=TaskDocCall(),
        )
        nested.append(result)
        assert result["ok"] is False
        assert "already in progress" in result["error"]
        raise OSError("copy refresh failed after refused nested publication")

    monkeypatch.setattr(task_doc_publication, "refresh_task_document_copies", fail_refresh)
    report = write_knowledge(
        WriteRequest(
            world.memory,
            world.code,
            Owner("260928-MIK", "leaf", LEAF),
            "handoff.json",
            {
                "history": [
                    {"subject": subject, "disposition": "raise", "reason": "Atomic raise."}
                    for subject in (S18, S12)
                ]
            },
            commit=True,
            questions=port,
            worklist={"items": []},
        )
    )
    assert report.refused and "copy refresh failed" in report.render()
    assert not report.questions_published and len(nested) == 1
    assert (tree_bytes(path.parent), tree_bytes(world.memory)) == before


@pytest.mark.parametrize("replace_existing", [False, True])
def test_waiting_question_writer_prepares_after_failed_batch_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replace_existing: bool
) -> None:
    port, path = setup_port(tmp_path)
    if replace_existing:
        assert port.append("[own]", "[own] original") is None
    before = json.loads(path.read_bytes())["openQuestions"]
    original = task_doc_publication.refresh_task_document_copies
    entered, finished = threading.Event(), threading.Event()
    results = []
    workers = []
    excluded = []
    native_lock = task_doc_publication.exclusive_file_lock

    @contextmanager
    def observe_lock(resource_path: Path, resource: str) -> Iterator[None]:
        if threading.current_thread() is not threading.main_thread() and not entered.is_set():
            lease = try_file_lease(resource_path, resource)
            excluded.append(lease is None)
            if lease is not None:
                lease.close()
            entered.set()
        with native_lock(resource_path, resource):
            yield

    monkeypatch.setattr(task_doc_publication, "exclusive_file_lock", observe_lock)

    def foreign_writer():
        results.append(port.append("[foreign]", "[foreign] kept after rollback"))
        finished.set()

    def fail_own_refresh(*args, **kwargs):
        if threading.current_thread() is not threading.main_thread():
            return original(*args, **kwargs)
        worker = threading.Thread(target=foreign_writer)
        workers.append(worker)
        worker.start()
        assert entered.wait(HANG_GUARD_SECONDS)
        assert excluded == [True], "foreign publication entered the own batch interval"
        assert not finished.is_set()
        raise OSError("own copy refresh fails while another writer waits")

    monkeypatch.setattr(task_doc_publication, "refresh_task_document_copies", fail_own_refresh)
    try:
        refusal = port.append_many([("[own]", "[own] changed"), ("[second]", "[second] new")])
    finally:
        for worker in workers:
            worker.join(HANG_GUARD_SECONDS)
    assert refusal and "own copy refresh fails" in refusal
    assert finished.is_set() and results == [None]
    assert json.loads(path.read_bytes())["openQuestions"] == [
        *before,
        "[foreign] kept after rollback",
    ]
