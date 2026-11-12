"""D569 mean 4: a read whose client went away stops computing, answers nothing and keeps nothing.

The cases follow the cancellation from the route to every safe point it reaches: the route's
disconnect watch, a shared flight's askers, a Git child, the reviewer worklist child, and the
kept/persisted answer and closure proof a finished computation would otherwise leave behind.
"""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from tempfile import TemporaryFile
from typing import Any

import pytest
from agents_remember.application import review_answer_preparation as reads
from agents_remember.application.review_closure_proofs import ClosureProofStore
from agents_remember.kernel import reviewer_worklist_process as process_owner
from agents_remember.kernel.git_command import GitRunnerOptions, run_git, run_git_into
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.read_cancellation import (
    ReadCancelled,
    SharedFlights,
    cancellable_read,
    raise_if_read_cancelled,
    read_cancellation,
)
from agents_remember.models.knowledge.review import ReviewEntryListResult
from agents_remember.serving.build_info import process_serving_build
from agents_remember.serving.read_disconnect import CLIENT_CLOSED_REQUEST
from agents_remember.serving.review import KNOWLEDGE_REVIEW_ENTRIES_ROUTE, register_review_routes
from agents_remember.serving.review_answer_reuse import ANSWER_WRITES, RecordedAnswerReuse
from agents_remember_test_support.testing.review_git_trees import World, git
from agents_remember_test_support.testing.waits import (
    HANG_GUARD_SECONDS,
    async_wait_until,
    wait_until,
)
from fastapi import FastAPI
from reviewer_worklist_process_test_support import one_shot_worklist_child
from starlette.types import Message
from test_changeset_cancellation import _gated_git
from test_review_git_trees import world as _world_fixture
from test_review_persisted_routes import fixture as persisted_fixture
from test_review_persisted_routes import query as persisted_query

world = _world_fixture

_ENTRIES = ReviewEntryListResult(state="entries", repository_id="r", master="m", leaf_id="l")


def _config(root: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        workspace_root=root,
        coordination_root=root,
        config_path=root / "config.json",
        transcript_root=root / "transcripts",
    )


async def _get(app: FastAPI, leave: threading.Event) -> tuple[int, bytes]:
    """One GET of the entry route whose client disconnects once ``leave`` is set."""

    sent: list[Message] = []

    async def receive() -> Message:
        if leave.is_set():
            return {"type": "http.disconnect"}
        await asyncio.sleep(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": KNOWLEDGE_REVIEW_ENTRIES_ROUTE,
        "raw_path": KNOWLEDGE_REVIEW_ENTRIES_ROUTE.encode(),
        "query_string": b"repo=r&master=m&leaf=l",
        "headers": [],
        "server": ("localhost", 1),
        "client": ("localhost", 2),
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=HANG_GUARD_SECONDS)
    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return status, body


def _entries_app(tmp_path: Path, port: Any) -> FastAPI:
    app = FastAPI()
    register_review_routes(app, _config(tmp_path), None, entries_port=port)
    return app


def test_a_disconnected_client_stops_the_read_at_its_next_safe_point_and_is_answered_nothing(
    tmp_path: Path,
) -> None:
    started, outcome = threading.Event(), []

    def entries(*_question: object) -> ReviewEntryListResult:
        token = read_cancellation()
        assert isinstance(token, threading.Event), "the route bound no cancellation token"
        started.set()
        token.wait(HANG_GUARD_SECONDS)  # the work in progress; the client leaving sets it
        try:
            raise_if_read_cancelled()
        except ReadCancelled:
            outcome.append("stopped")
            raise
        outcome.append("computed to the end")
        return _ENTRIES

    status, body = asyncio.run(_get(_entries_app(tmp_path, entries), started))
    wait_until(lambda: outcome, "the abandoned read's worker to end")
    assert outcome == ["stopped"]
    assert status == CLIENT_CLOSED_REQUEST and body == b""


def test_a_shared_read_continues_for_the_client_that_remains_when_its_first_asker_leaves(
    tmp_path: Path,
) -> None:
    answers = reads.RecordedReviewAnswers(_config(tmp_path), RecordedAnswerReuse())
    release, computed = threading.Event(), []

    def produce() -> ReviewEntryListResult:
        assert release.wait(HANG_GUARD_SECONDS)
        raise_if_read_cancelled()
        computed.append(1)
        return _ENTRIES

    app = _entries_app(tmp_path, lambda *_: answers._shared(("entries",), produce))

    async def scenario() -> tuple[tuple[int, bytes], tuple[int, bytes]]:
        first_leaves, second_stays = threading.Event(), threading.Event()
        first = asyncio.ensure_future(_get(app, first_leaves))
        flights = answers._flights._flights
        await async_wait_until(lambda: ("entries",) in flights, "the first asker's flight")
        second = asyncio.ensure_future(_get(app, second_stays))
        await async_wait_until(lambda: len(flights[("entries",)]._askers) == 2, "the second asker")
        flight = flights[("entries",)]
        first_token = flight._askers[0]
        assert first_token is not None
        first_leaves.set()
        await async_wait_until(first_token.is_set, "the first client to leave")
        assert not flight.is_set(), "a remaining asker's computation was cancelled"
        release.set()
        return await first, await second

    left, stayed = asyncio.run(scenario())
    assert left[0] == CLIENT_CLOSED_REQUEST
    assert stayed[0] == 200 and b'"state":"entries"' in stayed[1]
    assert computed == [1], "the remaining client's answer was not the one shared computation"


def test_a_flight_every_asker_left_stops_and_a_later_asker_computes_its_own_answer() -> None:
    flights: SharedFlights[str, str] = SharedFlights()
    entered, release, calls = threading.Event(), threading.Event(), []
    outcomes: dict[str, object] = {}

    def produce() -> str:
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(HANG_GUARD_SECONDS)
        raise_if_read_cancelled()
        return "answer"

    def ask(name: str, token: threading.Event | None) -> None:
        with cancellable_read(token):
            try:
                outcomes[name] = flights.share("key", produce)
            except ReadCancelled:
                outcomes[name] = "cancelled"

    tokens = {"owner": threading.Event(), "waiter": threading.Event()}
    owner = threading.Thread(target=ask, args=("owner", tokens["owner"]))
    owner.start()
    wait_until(entered.is_set, "the owner computing")
    waiter = threading.Thread(target=ask, args=("waiter", tokens["waiter"]))
    waiter.start()
    wait_until(lambda: len(flights._flights["key"]._askers) == 2, "the waiter joining")
    flight = flights._flights["key"]
    for token in tokens.values():
        token.set()
    waiter.join(HANG_GUARD_SECONDS)
    assert outcomes["waiter"] == "cancelled" and flight.is_set()
    # The flight is gone: a newcomer is never handed the cancellation of clients it is not.
    ask("newcomer", None)
    assert outcomes["newcomer"] == ("answer", True)
    release.set()
    owner.join(HANG_GUARD_SECONDS)
    assert outcomes["owner"] == "cancelled" and len(calls) == 2


@pytest.mark.parametrize("runner", ["piped", "into-file"])
def test_an_abandoned_read_terminates_its_git_child_and_launches_no_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: str
) -> None:
    pid_file, release, finished = _gated_git(tmp_path, monkeypatch)
    token, outcome = threading.Event(), []
    options = GitRunnerOptions(timeout=HANG_GUARD_SECONDS)

    def git_read() -> None:
        if runner == "piped":
            run_git(tmp_path, ["status"], options)
        else:
            with TemporaryFile() as output:
                run_git_into(tmp_path, ["status"], output, options)

    def read() -> None:
        with cancellable_read(token):
            try:
                git_read()
            except ReadCancelled:
                outcome.append("cancelled")
            else:
                outcome.append("answered a Git result")

    thread = threading.Thread(target=read)
    thread.start()
    try:
        wait_until(lambda: pid_file.exists() and pid_file.read_text().strip(), "the Git child")
        pid = int(pid_file.read_text())
        token.set()
        thread.join(HANG_GUARD_SECONDS)
        assert outcome == ["cancelled"]
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)  # killed with its group and reaped before the read returned
        pid_file.unlink()
        with cancellable_read(token), pytest.raises(ReadCancelled):
            git_read()
        assert not pid_file.exists(), "a read whose client left launched another Git child"
    finally:
        release.touch()
    assert not finished.exists()


def test_a_requester_that_left_terminates_the_worklist_child_only_it_asked_for() -> None:
    owner = process_owner.ReviewerWorklistProcesses()
    source = process_owner.build_identity(
        process_serving_build().payload().model_dump(mode="json", exclude_none=True)
    )
    token, outcome = threading.Event(), []

    def ask() -> None:
        with cancellable_read(token), process_owner.worklist_request():
            try:
                owner.compute({"tree": "alone"}, source)
            except ReadCancelled:
                outcome.append("cancelled")
            except process_owner.WorklistProcessError as error:
                outcome.append(f"a worklist failure: {error}")

    try:
        with one_shot_worklist_child("import threading; threading.Event().wait()") as children:
            thread = threading.Thread(target=ask)
            thread.start()
            wait_until(lambda: bool(children), "the worklist child")
            token.set()
            thread.join(HANG_GUARD_SECONDS)
            assert outcome == ["cancelled"]
            assert children[0].poll() is not None, "the abandoned worklist child still runs"
        assert not owner._active and not owner._flights
    finally:
        owner.shutdown()


def test_a_cancelled_read_keeps_no_answer_in_memory_or_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, produce, calls, _latest, _index = persisted_fixture(tmp_path, monkeypatch)
    answers = owner()
    token = threading.Event()

    def client_leaves_at_the_end() -> Any:
        value = produce()
        token.set()  # every safe point inside the work has passed; the answer is complete
        return value

    with cancellable_read(token), pytest.raises(ReadCancelled):
        answers.trees(persisted_query(), client_leaves_at_the_end)
    ANSWER_WRITES.drain()
    assert not list((tmp_path / "cache").glob("*.review.json")), "a cancelled answer was persisted"
    answers.trees(persisted_query(), produce)
    assert len(calls) == 2, "a cancelled answer was kept in memory"
    ANSWER_WRITES.drain()
    assert list((tmp_path / "cache").glob("*.review.json")), "an answered read is persisted"


def test_a_cancelled_closure_check_leaves_no_proof_and_no_failed_verdict(
    world: World, tmp_path: Path
) -> None:
    proofs = ClosureProofStore(tmp_path / "closures", "source-digest")
    repository = world.code_worktree
    trees = (git(repository, "rev-parse", "HEAD^{tree}"),)
    reads._CLOSURE_PROOFS.clear()
    reads._LATEST_PROOF.clear()
    left = threading.Event()
    left.set()
    with cancellable_read(left), pytest.raises(ReadCancelled):
        reads._trees_still_readable(((repository, trees[0]),), proofs)
    assert reads._CLOSURE_PROOFS.get((repository, trees)) is None
    assert proofs.load(repository, trees) is None
    assert reads._trees_still_readable(((repository, trees[0]),), proofs)
    assert proofs.load(repository, trees) is not None
