"""MIK-R42: real child/source/read parity, explicit failures and native process reclamation."""

from __future__ import annotations

import asyncio
import json
import math
import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
import pytest
from agents_remember.application import (
    review_leaf_view_memo,
    review_rename_inference,
    review_source_inventory,
    review_tree_comparison,
    review_tree_knowledge,
)
from agents_remember.application.knowledge_worklist import code, onboarding_trace
from agents_remember.application.knowledge_worklist import leaf as worklist_leaf
from agents_remember.application.knowledge_worklist.leaf import (
    CandidateTrees,
    CapturedBase,
    leaf_worklist,
)
from agents_remember.application.reviewer_worklist_child import isolated_leaf_worklist
from agents_remember.kernel import reviewer_worklist_process as process_owner
from agents_remember.kernel.git_command import DIFF_PREFIX_OPTIONS, PARSED_DIFF_OPTIONS
from agents_remember.kernel.recorded_reads import (
    recorded_reads,
)
from agents_remember.memory.knowledge.diff_display import TREE_DIFF_COMMAND, TreeSide
from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH, REFERENCE_MAX_LENGTH
from agents_remember.models.knowledge.review import ReviewRefusal
from agents_remember.models.knowledge.review_trees import ReviewTreesResult
from agents_remember.serving import build_info
from agents_remember.serving.build_info import process_serving_build
from agents_remember.serving.review_trees import register_review_trees_route
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_review_git_trees import (  # noqa: F401 - fixture
    CODE_FILE,
    CODE_V1,
    LEAF,
    MASTER,
    REPO,
    World,
    _repository,
    commit,
    world,
)
from test_review_read_latency import _body, _live, _ports, _query, _unconverted_base
from test_reviewer_worklist_reads import (  # noqa: F401 - fixture
    _child_script,
    _fresh_memo,
    _observations,
)


def _captured(contract: Any, trees: Any) -> tuple[CandidateTrees, CapturedBase]:
    return (
        CandidateTrees(trees.record.code_candidate.tree, trees.record.memory_candidate.tree),
        CapturedBase(
            trees.record.code_base.commit, trees.record.memory_base.commit, trees.before.wire.tree
        ),
    )


def test_real_child_parity_source_reads_and_held_conversion_at_two_result_sizes(
    world: World,  # noqa: F811
) -> None:
    files = _unconverted_base(world)
    files["onboarding/pkg/overview.md"] = b"# The governing package route\n"
    ports = _ports(world)
    replies: list[dict[str, Any]] = []
    reply = process_owner._reply

    def observed(data: bytes, request: Any) -> Any:
        answer = reply(data, request)
        replies.append({**answer, "bytes": len(data), "request_payload": request["payload"]})
        return answer

    with mock.patch.object(review_tree_comparison, "converted_base_files", return_value=files):
        for size in (1, 180):
            for number in range(size):
                (world.code_worktree / "pkg" / f"extra-{number:03}.py").write_text("VALUE = 1\n")
            contract, trees = _live(world)
            candidate, _base = _captured(contract, trees)
            # The old computation consumes full converted files. Comparing against the held
            # branch itself masked a dropped Markdown/route-coverage regression in L42 attempt 1.
            with (
                recorded_reads() as baseline_reads,
                mock.patch.object(worklist_leaf, "converted_base_files", return_value=files),
                mock.patch.object(onboarding_trace, "converted_base_files", return_value=files),
            ):
                baseline = leaf_worklist(contract, persist=False, candidate=candidate)
            assert baseline is not None and baseline["state"] == "complete"
            changed = [
                item
                for item in baseline["items"]
                if item["kind"] == "unexplained_hunk" and "extra-" in item["subject"]
            ]
            assert changed and all(item["facts"]["coverage"]["route"] == "pkg" for item in changed)

            def direct(admitted: Any, **supplied: Any) -> Any:
                return leaf_worklist(
                    admitted, persist=False, candidate=supplied["candidate"], base=supplied["base"]
                )

            with mock.patch.object(review_tree_knowledge, "isolated_leaf_worklist", direct):
                baseline_public = ports.trees(_query())
            review_leaf_view_memo.LEAF_VIEW_MEMO.clear()
            with mock.patch.object(process_owner, "_reply", observed), recorded_reads() as imported:
                answer = ports.trees(_query())
            assert answer.state == "trees" and answer.worklist is not None and answer.worklist.bound
            child = replies[-1]
            assert child["document"] == baseline and child["reads"] == baseline_reads
            assert json.dumps(child["document"]) == json.dumps(baseline)
            assert _body(answer, by_alias=True) == _body(baseline_public, by_alias=True)
            assert child["pid"] != os.getpid()
            assert child["source"]["pythonExecutable"] == Path(sys.executable).resolve().as_posix()
            assert child["module"] == str(
                Path(child["source"]["packageRoot"]) / "application/reviewer_worklist_child.py"
            )
            assert all(imported[path] == identity for path, identity in child["reads"].items())
            assert child["request_payload"]["base"]["read_tree"] == trees.before.wire.tree
            assert child["request_payload"]["candidate"] == {
                "code": candidate.code,
                "memory": candidate.memory,
            }
            print(
                json.dumps(
                    {
                        "size": size,
                        "protocol_bytes": child["bytes"],
                        "read_manifest_bytes": len(json.dumps(child["reads"]).encode()),
                        "child_pid": child["pid"],
                        "source": child["source"],
                        "module": child["module"],
                    },
                    sort_keys=True,
                )
            )
            count = len(replies)
            assert _body(ports.trees(_query()), by_alias=True) == _body(answer, by_alias=True)
            assert len(replies) == count
        assert replies[-1]["bytes"] > 65536 and replies[-1]["bytes"] > replies[0]["bytes"]

        # The same real child entry fails loudly if it tries a second capture, pairing or
        # conversion. Only the test launcher inserts these sentinels into that child process.
        guarded = """
from agents_remember.application import reviewer_worklist_child as child
from agents_remember.application.knowledge_worklist import leaf, onboarding_trace
def forbidden(*args, **kwargs):
    raise AssertionError('second capture, pairing or conversion')
leaf.paired_memory_commit = forbidden
leaf._captured_code = forbidden
leaf.directory_snapshot = forbidden
leaf.converted_base_files = forbidden
onboarding_trace.converted_base_files = forbidden
child.main()
"""
        review_leaf_view_memo.LEAF_VIEW_MEMO.clear()
        with _child_script(guarded):
            guarded_answer = ports.trees(_query())
        assert _body(guarded_answer, by_alias=True) == _body(answer, by_alias=True)


_REPLY = """
import json, os, sys, time
r = json.load(sys.stdin)
answer = {'operation': r['operation'], 'request': r['request'], 'source': r['source'],
          'module': r['source']['packageRoot'] + '/application/reviewer_worklist_child.py',
          'pid': os.getpid(), 'document': None, 'reads': {}, 'computation': [time.monotonic(), time.monotonic()], 'error': None}
"""


def test_real_child_transport_failures_are_explicit_uncached_and_recoverable(world: World) -> None:  # noqa: F811
    world.edit()
    ports = _ports(world)
    cases = (
        "sys.stdout.write('{')",
        "sys.exit(7)",
        "answer['request'] = 'other'; json.dump(answer, sys.stdout)",
        "answer['source'] = {**answer['source'], 'sourceDigest': 'other'}; json.dump(answer, sys.stdout)",
        "answer['module'] = '/another/package.py'; json.dump(answer, sys.stdout)",
        "answer['reads'] = {'/missing': 'bad identity'}; json.dump(answer, sys.stdout)",
        "answer['reads'] = {'resolve:relative': 'resolved:/target'}; json.dump(answer, sys.stdout)",
        "answer['reads'] = {'resolve:/locator': 'sha256:' + '0' * 64}; json.dump(answer, sys.stdout)",
        "answer['reads'] = {'exists:/locator': 'resolved:/target'}; json.dump(answer, sys.stdout)",
        "answer['reads'] = {'resolve:/locator': 'resolved:relative'}; json.dump(answer, sys.stdout)",
        "answer['reads'] = {'resolve:/locator': 'resolved:/bad\\0path'}; json.dump(answer, sys.stdout)",
        "answer['document'] = {'schema': 'knowledge-worklist/v1', 'state': 'complete', 'pairing': {}, 'items': [], 'incomplete': []}; json.dump(answer, sys.stdout)",
        "sys.stderr.write('x' * 262144); sys.stdout.write('invalid JSON')",
        "answer['document'] = {'schema': 'knowledge-worklist/v1', 'state': 'incomplete', 'pairing': {}, 'items': [], 'incomplete': [{'input': 'run', 'detail': float('nan')}]}; json.dump(answer, sys.stdout)",
    )
    for fault in cases:
        with _child_script(_REPLY + fault) as children:
            answer = ports.trees(_query())
        assert answer.state == "refused" and answer.refusal is not None, fault
        assert "worklist" in answer.refusal.detail and answer.refusal.next_action
        assert len(review_leaf_view_memo.LEAF_VIEW_MEMO) == 0
        assert all(child.poll() is not None for child in children)
    app = FastAPI()
    answers: list[Any] = []

    def routed(query: Any) -> Any:
        value = ports.trees(query)
        answers.append(value)
        return value

    register_review_trees_route(app, routed)
    client = TestClient(app)
    for number, valid in (
        ("1e400", False),
        ("-1e400", False),
        ("1e308", True),
        ("-1e308", True),
        ("-1e-400", True),
    ):
        script = f"""
import json, sys
from agents_remember.application import reviewer_worklist_child as child
answer = child._run(json.load(sys.stdin))
answer['document']['items'][0]['facts']['numeric_control'] = 'NUMBER'
sys.stdout.write(json.dumps(answer).replace('"NUMBER"', {number!r}))
"""
        review_leaf_view_memo.LEAF_VIEW_MEMO.clear()
        with _child_script(script) as children:
            response = client.get(
                "/api/review/trees", params={"repo": REPO, "master": MASTER, "leaf": LEAF}
            )
        answer = answers[-1]
        assert response.status_code == 200
        public = response.json()
        if not valid:
            assert answer.state == "refused" and answer.refusal is not None, number
            assert "malformed" in answer.refusal.detail and answer.refusal.next_action
            assert len(review_leaf_view_memo.LEAF_VIEW_MEMO) == 0
            assert public["state"] == "refused" and "worklist" not in public
        else:
            assert answer.state == "trees" and answer.worklist is not None
            value = answer.worklist.items[0]["facts"]["numeric_control"]
            assert value == float(number)
            serialized = public["worklist"]["items"][0]["facts"]["numeric_control"]
            assert serialized == value and math.copysign(1, serialized) == math.copysign(1, value)
            assert len(review_leaf_view_memo.LEAF_VIEW_MEMO) == 1
        assert all(child.poll() is not None for child in children)
        print(
            json.dumps(
                {
                    "numeric_token": number,
                    "public": public,
                    "memo_entries": len(review_leaf_view_memo.LEAF_VIEW_MEMO),
                    "child_pids": [child.pid for child in children],
                }
            )
        )
    review_leaf_view_memo.LEAF_VIEW_MEMO.clear()
    contract, trees = _live(world)
    actual = subprocess.Popen

    def unavailable(argv: list[str], *args: Any, **kwargs: Any) -> Any:
        if argv[1:] == ["-P", "-m", process_owner.CHILD_MODULE]:
            raise FileNotFoundError("launch unavailable")
        return actual(argv, *args, **kwargs)

    with mock.patch.object(subprocess, "Popen", unavailable):
        refused = review_tree_knowledge._leaf_wide_parts(contract, trees)
    assert getattr(refused, "code", None) == "candidate_unresolved"
    assert len(review_leaf_view_memo.LEAF_VIEW_MEMO) == 0
    assert ports.trees(_query()).state == "trees"


def _children(pid: int) -> list[int]:
    try:
        return [int(one) for one in Path(f"/proc/{pid}/task/{pid}/children").read_text().split()]
    except FileNotFoundError:
        return []


_BLOCKED_GIT = """
import os, subprocess
from agents_remember.application import reviewer_worklist_child as child
def blocked(*args, **kwargs):
    read_end, write_end = os.pipe()
    try:
        subprocess.run(['git', 'cat-file', '--batch'], cwd=args[0].code_repo_path,
                       stdin=read_end, capture_output=True, check=True)
    finally:
        os.close(read_end); os.close(write_end)
child.leaf_worklist = blocked
child.main()
"""


def test_actual_disconnect_deadline_shutdown_and_queue_reap_children(world: World) -> None:  # noqa: F811
    world.edit()
    ports = _ports(world)
    assert ports.trees is not None
    contract, trees = _live(world)
    candidate, base = _captured(contract, trees)

    for stop in ("disconnect", "shutdown"):
        _route_stop(world, stop)

    owner = process_owner.ReviewerWorklistProcesses()
    with (
        _child_script("import time; time.sleep(30)"),
        mock.patch.object(process_owner, "DEADLINE_SECONDS", 0.2),
        pytest.raises(process_owner.WorklistProcessError, match="deadline"),
    ):
        isolated_leaf_worklist(contract, candidate=candidate, base=base, processes=owner)
    assert not owner._active

    # Two different computations hold both slots; a third waits its turn instead of being refused,
    # and a shutdown ends all three (MIK-R42 ruling 1).
    owner = process_owner.ReviewerWorklistProcesses()
    failures: list[Exception] = []

    def work(number: int) -> None:
        distinct = replace(candidate, memory=f"{candidate.memory[:-1]}{number:x}")
        try:
            isolated_leaf_worklist(contract, candidate=distinct, base=base, processes=owner)
        except Exception as error:
            failures.append(error)

    with _child_script("import time; time.sleep(30)") as children:
        workers = [threading.Thread(target=work, args=(number,)) for number in range(3)]
        for worker in workers:
            worker.start()
        until = time.monotonic() + 5
        while owner._waiting != 3 and time.monotonic() < until:
            time.sleep(0.01)
        time.sleep(0.3)
        assert owner._waiting == 3 and len(children) == 2 and not failures
        owner.shutdown()
        for worker in workers:
            worker.join(timeout=5)
    assert len(failures) == 3 and not owner._active and not owner._queue
    assert all(child.poll() is not None for child in children)


def _route_stop(world: World, stop: str) -> None:  # noqa: F811
    owner = process_owner.ReviewerWorklistProcesses()
    git_pids: list[int] = []
    with _child_script(_BLOCKED_GIT) as children:

        def read(query: Any) -> Any:
            return review_tree_knowledge.read_review_trees(world.config, query, processes=owner)

        async def received() -> dict[str, Any]:
            if children:
                git_pids[:] = _children(children[0].pid)
            if git_pids:
                if stop == "shutdown":
                    owner.shutdown()
                return {"type": "http.disconnect"}
            await asyncio.sleep(0)
            return {"type": "http.request", "body": b""}

        sent: list[dict[str, Any]] = []

        async def send(message: Any) -> None:
            sent.append(message)

        app = FastAPI()
        register_review_trees_route(app, read)
        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/review/trees",
            "raw_path": b"/api/review/trees",
            "query_string": f"repo={REPO}&master={MASTER}&leaf={LEAF}".encode(),
            "headers": [],
            "server": ("localhost", 1),
            "client": ("localhost", 2),
        }
        asyncio.run(asyncio.wait_for(app(scope, received, send), timeout=10))
    assert git_pids and children and all(child.poll() is not None for child in children)
    assert all(not Path(f"/proc/{pid}").exists() for pid in git_pids)
    assert not owner._active and len(review_leaf_view_memo.LEAF_VIEW_MEMO) == 0


# -- MIK-R42 round 3: admission, sharing, lifetime, build mismatch, configuration independence -----


def _source() -> dict[str, Any]:
    return process_owner.build_identity(
        process_serving_build().payload().model_dump(mode="json", exclude_none=True)
    )


_SLEEPING_REPLY = _REPLY + "time.sleep(0.8); json.dump(answer, sys.stdout)"


def _alive(children: list[subprocess.Popen[Any]]) -> int:
    return sum(child.poll() is None for child in children)


def _ask(owner: Any, payload: dict[str, Any], answers: list[Any], errors: list[Exception]) -> None:
    try:
        answers.append(owner.compute(payload, _source()))
    except Exception as error:
        errors.append(error)


def test_identical_requests_share_one_child_and_others_wait_for_one_of_two_slots() -> None:
    _source()  # Resolve the build once before concurrent requesters use it.
    owner = process_owner.ReviewerWorklistProcesses()
    same: list[Any] = []
    other: list[Any] = []
    errors: list[Exception] = []
    with _child_script(_SLEEPING_REPLY) as children:
        threads = [
            threading.Thread(target=_ask, args=(owner, {"tree": "a"}, same, errors))
            for _ in range(4)
        ] + [
            threading.Thread(target=_ask, args=(owner, {"tree": name}, other, errors))
            for name in ("b", "c", "d")
        ]
        for thread in threads:
            thread.start()
        most = 0
        while any(thread.is_alive() for thread in threads):
            most = max(most, _alive(children))
            time.sleep(0.01)
    assert not errors
    assert len(same) == 4 and all(answer == same[0] for answer in same)  # one answer, four copies
    assert len({id(answer) for answer in same}) == 4 and len(other) == 3
    assert len(children) == 4 and most == 2  # one shared child, three more, never over two at once
    assert not owner._active and not owner._flights and not owner._queue and owner._waiting == 0


def test_overload_is_refused_only_past_the_waiting_bound_or_the_deadline_and_says_so() -> None:
    _source()  # Resolve the build once before concurrent requesters use it.
    owner = process_owner.ReviewerWorklistProcesses()
    answers: list[Any] = []
    errors: list[Exception] = []
    with (
        _child_script("import time; time.sleep(30)") as children,
        mock.patch.object(process_owner, "MAX_WAITING_COMPUTATIONS", 4),
        mock.patch.object(process_owner, "DEADLINE_SECONDS", 1.5),
    ):
        threads = [
            threading.Thread(target=_ask, args=(owner, {"tree": number}, answers, errors))
            for number in range(4)
        ]
        for thread in threads:
            thread.start()
        until = time.monotonic() + 5
        while owner._waiting != 4 and time.monotonic() < until:
            time.sleep(0.01)
        started = time.monotonic()
        with pytest.raises(process_owner.WorklistOverloaded) as beyond:
            owner.compute({"tree": "fifth"}, _source())
        assert time.monotonic() - started < 0.5  # the bound refuses at once; waiting is not refusal
        for thread in threads:
            thread.join(timeout=10)
    assert beyond.value.code == "reviewer_busy"
    assert beyond.value.next_action == "the reviewer is computing other worklists; retry"
    # Two held the children and ran out of time; two never got a child, which is overload.
    kinds = sorted(type(error).__name__ for error in errors)
    assert kinds == ["WorklistOverloaded"] * 2 + ["WorklistProcessError"] * 2
    assert len(children) == 2 and _alive(children) == 0 and not answers
    assert not owner._active and owner._waiting == 0


def test_a_cancelled_requester_leaves_the_shared_child_to_the_others_and_the_last_reaps_it() -> (
    None
):
    owner = process_owner.ReviewerWorklistProcesses()
    answers: list[Any] = []
    errors: list[Exception] = []
    leaving = threading.Event()

    def impatient() -> None:
        with process_owner.worklist_request(leaving):
            _ask(owner, {"tree": "a"}, [], errors)

    with _child_script(_SLEEPING_REPLY) as children:
        first = threading.Thread(target=impatient)
        second = threading.Thread(target=_ask, args=(owner, {"tree": "a"}, answers, errors))
        first.start()
        second.start()
        until = time.monotonic() + 5
        while owner._waiting != 2 and time.monotonic() < until:
            time.sleep(0.01)
        leaving.set()
        first.join(timeout=5)
        second.join(timeout=10)
    assert len(answers) == 1 and len(children) == 1  # the other requester still got its answer
    assert [str(error) for error in errors] == ["reviewer worklist computation was cancelled"]

    sole = threading.Event()
    with _child_script("import time; time.sleep(30)") as children:
        errors.clear()

        def alone() -> None:
            with process_owner.worklist_request(sole):
                _ask(owner, {"tree": "b"}, [], errors)

        thread = threading.Thread(target=alone)
        thread.start()
        until = time.monotonic() + 5
        while not children and time.monotonic() < until:
            time.sleep(0.01)
        sole.set()
        thread.join(timeout=10)
        assert children and children[0].poll() is not None  # reaped before the request returned
    assert not owner._active


def test_build_mismatch_asks_for_a_dashboard_restart(world: World) -> None:  # noqa: F811
    world.edit()
    ports = _ports(world)
    for script in (
        f"import sys; sys.exit({process_owner.EXIT_BUILD_MISMATCH})",
        _REPLY + "answer['source'] = {**answer['source'], 'sourceDigest': 'other'}; "
        "json.dump(answer, sys.stdout)",
    ):
        with _child_script(script):
            answer = ports.trees(_query())
        assert answer.state == "refused" and answer.refusal is not None
        assert answer.refusal.code == "candidate_unresolved"
        assert "restart the dashboard" in answer.refusal.next_action
        assert "installed package changed" in answer.refusal.detail
    assert len(review_leaf_view_memo.LEAF_VIEW_MEMO) == 0


def test_overload_is_its_own_refusal_code_with_a_true_action(world: World) -> None:  # noqa: F811
    world.edit()
    contract, trees = _live(world)
    overloaded = process_owner.WorklistOverloaded("queue full")
    with mock.patch.object(review_tree_knowledge, "isolated_leaf_worklist", side_effect=overloaded):
        refusal = review_tree_knowledge._leaf_wide_parts(contract, trees)
    assert getattr(refusal, "code", None) == "reviewer_busy"
    assert getattr(refusal, "next_action", "") == "the reviewer is computing other worklists; retry"
    assert len(review_leaf_view_memo.LEAF_VIEW_MEMO) == 0


def test_four_concurrent_first_reads_answer_alike_from_one_child(world: World) -> None:  # noqa: F811
    """The pin race (MIK-R42 ruling 5) and the shared child (ruling 1) on a fresh comparison."""

    world.edit()
    ports = _ports(world)
    answers: list[Any] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(4)

    def first_read() -> None:
        barrier.wait()
        try:
            answers.append(ports.trees(_query()))
        except Exception as error:
            errors.append(error)

    with _observations() as replies:
        threads = [threading.Thread(target=first_read) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
    assert not errors and len(answers) == 4
    assert [answer.state for answer in answers] == ["trees"] * 4, [
        getattr(answer.refusal, "detail", None) for answer in answers
    ]
    assert len({_body(answer, by_alias=True) for answer in answers}) == 1
    assert len(replies) == 1  # one child answered all four


def test_three_changed_leaves_opened_in_quick_succession_all_answer(world: World) -> None:  # noqa: F811
    owner = process_owner.ReviewerWorklistProcesses()
    answers: list[Any] = []
    delayed = (
        "import time; time.sleep(2)\n"
        "from agents_remember.application import reviewer_worklist_child as child\n"
        "child.main()"
    )

    def read() -> None:
        answers.append(
            review_tree_knowledge.read_review_trees(world.config, _query(), processes=owner)
        )

    world.edit()
    with _child_script(delayed) as children:
        threads = []
        for number in range(3):
            (world.code_worktree / CODE_FILE).write_text(
                CODE_V1.replace("return value", f"return value + {number}"), encoding="utf-8"
            )
            threads.append(threading.Thread(target=read))
            threads[-1].start()
            until = time.monotonic() + 30
            while owner._waiting != number + 1 and time.monotonic() < until:
                time.sleep(0.01)
        most = 0
        while any(thread.is_alive() for thread in threads):
            most = max(most, _alive(children))
            time.sleep(0.02)
    assert [answer.state for answer in answers] == ["trees"] * 3
    assert all(answer.worklist is not None for answer in answers)
    assert len(children) == 3 and most == 2
    assert not owner._active and owner._waiting == 0


def _all_children(pid: int) -> list[int]:
    found: list[int] = []
    for task in Path(f"/proc/{pid}/task").glob("*/children"):
        found += [int(one) for one in task.read_text().split()]
    return found


def _gone(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1][0] == "Z"
    except FileNotFoundError:
        return True


def _wait_gone(pids: list[int], seconds: float) -> bool:
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if all(_gone(pid) for pid in pids):
            return True
        time.sleep(0.05)
    return all(_gone(pid) for pid in pids)


_LIFETIME_CHILD = """
import os, subprocess, sys
from agents_remember.kernel import reviewer_worklist_process as owner
owner.CHILD_DEADLINE_SECONDS = float(sys.argv[1])
owner.arm_child_lifetime()
subprocess.run(['git', 'cat-file', '--batch'], stdin=os.pipe()[0], cwd=sys.argv[2])
"""

_PARENT = """
import json, subprocess, sys
from agents_remember.kernel import reviewer_worklist_process as owner
spec = json.load(open(sys.argv[1]))
actual = subprocess.Popen
def spawn(argv, *args, **kwargs):
    if argv[1:] == ['-P', '-m', owner.CHILD_MODULE]:
        argv = [argv[0], '-P', '-c', spec['script']]
    return actual(argv, *args, **kwargs)
subprocess.Popen = spawn
print('ready', flush=True)
owner.ReviewerWorklistProcesses().compute(spec['payload'], spec['source'])
"""

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="parent-death signal")


@linux_only
def test_the_child_and_its_git_children_end_at_its_own_deadline(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    environment = process_owner._environment(_source())
    started = time.monotonic()
    child = subprocess.Popen(
        [sys.executable, "-P", "-c", _LIFETIME_CHILD, "0.8", str(tmp_path)],
        env=environment,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
    )
    until = time.monotonic() + 15
    git: list[int] = []
    while not git and time.monotonic() < until:
        git = _children(child.pid)
        time.sleep(0.05)
    assert git, "the child never started its Git process"
    assert _wait_gone([child.pid, *git], 5)
    assert child.wait(timeout=5) < 0 and time.monotonic() - started < 15  # killed, not exited


@linux_only
def test_a_killed_dashboard_takes_the_child_blocked_in_git_and_its_git_process_with_it(
    world: World,  # noqa: F811
    tmp_path: Path,
) -> None:
    world.edit()
    contract, trees = _live(world)
    candidate, base = _captured(contract, trees)
    payload = json.loads(
        json.dumps(
            {
                "contract": asdict(contract),
                "candidate": asdict(candidate),
                "base": asdict(base),
            },
            default=lambda value: value.as_posix(),
        )
    )
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps({"payload": payload, "source": _source(), "script": _BLOCKED_GIT}),
        encoding="utf-8",
    )
    environment = process_owner._environment(_source())
    parent = subprocess.Popen(
        [sys.executable, "-P", "-c", _PARENT, str(spec)],
        env=environment,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert parent.stdout is not None and parent.stdout.readline().strip() == "ready"
        until = time.monotonic() + 30
        child: list[int] = []
        git: list[int] = []
        while not git and time.monotonic() < until:
            child = _all_children(parent.pid)
            git = _children(child[0]) if child else []
            time.sleep(0.05)
        assert child and git, "the child never blocked in Git"
        parent.kill()  # SIGKILL: no handler, no shutdown, no cleanup of any kind runs
        parent.wait(timeout=10)
        assert _wait_gone([*child, *git], 5), (child, git)
    finally:
        parent.kill()
        parent.wait(timeout=10)
        if parent.stdout is not None:
            parent.stdout.close()


_HOSTILE_GIT_CONFIG = """\
[diff]
    noprefix = true
    mnemonicPrefix = true
    srcPrefix = SRC/
    dstPrefix = DST/
    external = /bin/false
    renames = copies
    colorMoved = zebra
[color]
    ui = always
[core]
    quotePath = false
"""


def test_the_reviewers_diff_reads_do_not_depend_on_the_users_git_configuration(
    world: World,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prefix, mnemonic, external-driver and forced-colour settings change no answer (MIK-R42)."""

    world.edit()
    (world.code_worktree / "pkg" / "renamed.py").write_text("RENAMED = 1\n")
    ports = _ports(world)
    clean = tmp_path / "clean.gitconfig"
    clean.write_text("", encoding="utf-8")
    hostile = tmp_path / "hostile.gitconfig"
    hostile.write_text(_HOSTILE_GIT_CONFIG, encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    bodies: dict[str, tuple[bytes, bytes]] = {}
    for name, config in (("clean", clean), ("hostile", hostile)):
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
        review_leaf_view_memo.LEAF_VIEW_MEMO.clear()
        trees = ports.trees(_query())
        assert trees.state == "trees" and trees.knowledge_diff is not None, name
        assert trees.knowledge_diff.changed_files and trees.worklist is not None
        assert all(
            change.patch.startswith("diff --git a/")
            for group in trees.knowledge_diff.records
            for change in group.files
        )
        review = ports.review(world.review())
        bodies[name] = (_body(trees, by_alias=True), _body(review))
    assert bodies["hostile"] == bodies["clean"]


def test_every_parsed_diff_names_its_own_prefix_colour_and_driver() -> None:
    for args in (
        review_source_inventory._RAW_ARGS,
        review_source_inventory._NUMSTAT_ARGS,
    ):
        assert set(PARSED_DIFF_OPTIONS) <= set(args)
    assert set(DIFF_PREFIX_OPTIONS) <= set(code.BLOB_DIFF_ARGS)
    assert {"--no-color", "--no-ext-diff"} <= set(code.BLOB_DIFF_ARGS)


def test_each_published_diff_command_is_the_argument_list_its_measurement_executes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A published command text is ``git`` plus exactly the arguments its call ran (MIK-R42)."""

    before = TreeSide(tree_id="a" * 40, root="/scratch/before")
    after = TreeSide(tree_id="b" * 40, root="/scratch/after")
    executed: list[tuple[Path, list[str]]] = []

    def observed(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
        executed.append((root, list(args)))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(review_rename_inference, "run_git", observed)
    monkeypatch.setattr(review_source_inventory, "run_git", observed)
    assert review_rename_inference.git_rename_inference(before, after).available
    inventory = review_source_inventory.review_inventory(before, after)
    assert inventory.state == "measured"
    (_, rename_args), (raw_root, raw_args), (_, numstat_args) = executed
    sources = review_rename_inference.RenameInferenceSources(before_code=before, after_code=after)
    assert review_rename_inference.rename_command(sources) == " ".join(("git", *rename_args))
    assert inventory.command == " ".join(("git", "-C", str(raw_root), *raw_args))
    expansion = TREE_DIFF_COMMAND.format(before_tree=before.tree_id, after_tree=after.tree_id)
    assert expansion == " ".join(("git", *raw_args))
    assert numstat_args == [*review_source_inventory._NUMSTAT_ARGS, before.tree_id, after.tree_id]
    for args in (rename_args, raw_args, numstat_args):
        assert set(PARSED_DIFF_OPTIONS) <= set(args)


def test_a_busy_default_executor_cannot_delay_a_tree_read() -> None:
    """The route's blocking reads run on their own threads, not the loop's shared executor."""

    def port(query: Any) -> Any:
        return ReviewTreesResult(
            state="not-converted",
            repository_id=query.repository_id,
            master=query.master,
            leaf_id=query.leaf_id,
        )

    app = FastAPI()
    register_review_trees_route(app, port)

    async def scenario() -> float:
        loop = asyncio.get_running_loop()
        background = [
            loop.run_in_executor(None, time.sleep, 2.0) for _ in range((os.cpu_count() or 4) + 8)
        ]
        await asyncio.sleep(0.2)  # every default-executor thread is now busy, with work queued
        started = time.monotonic()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://reviewer"
        ) as client:
            response = await client.get(
                "/api/review/trees", params={"repo": REPO, "master": MASTER, "leaf": LEAF}
            )
        seconds = time.monotonic() - started
        assert response.status_code == 200 and response.json()["state"] == "not-converted"
        await asyncio.gather(*background)
        return seconds

    assert asyncio.run(scenario()) < 1.0


def test_a_pin_another_reader_made_or_is_making_is_the_answer_not_a_refusal(
    world: World,  # noqa: F811
) -> None:
    """MIK-R42 ruling 5: creating the pin is idempotent and a held ref lock is waited out."""

    world.edit()
    _contract, trees = _live(world)
    ref = trees.record.code_candidate.ref
    assert ref is not None
    # One side suffices: the code candidate's pin, in the code repository.
    record = trees.record.model_copy(
        update={"memory_candidate": trees.record.memory_candidate.model_copy(update={"ref": None})}
    )
    repository, tree = Path(record.code_candidate.repository), record.code_candidate.tree

    def git_in(*argv: str, check: bool = True) -> str:
        done = subprocess.run(
            ["git", *argv], cwd=repository, capture_output=True, text=True, check=check
        )
        return done.stdout.strip()

    git_in("update-ref", "-d", ref)
    actual = review_tree_comparison.run_git
    losing = [True]

    def racing(root: Path, argv: list[str], *args: Any) -> Any:
        if argv[:2] == ["update-ref", ref] and losing:
            losing.clear()
            actual(root, ["update-ref", ref, tree])  # another reader creates the very same pin
            return subprocess.CompletedProcess(
                argv, 128, "", f"fatal: cannot lock ref '{ref}': reference already exists"
            )
        return actual(root, argv, *args)

    with mock.patch.object(review_tree_comparison, "run_git", racing):
        assert review_tree_comparison._pin(record) is None
    assert git_in("rev-parse", ref) == tree  # kept, and not rolled back as if it were this one's

    git_in("update-ref", "-d", ref)
    attempts = []

    def last_race(root: Path, argv: list[str], *args: Any) -> Any:
        if argv[:2] == ["update-ref", ref]:
            attempts.append(argv)
            if len(attempts) == review_tree_comparison._PIN_ATTEMPTS:
                actual(root, ["update-ref", ref, tree])
            return subprocess.CompletedProcess(argv, 128, "", "cannot lock ref")
        return actual(root, argv, *args)

    with mock.patch.object(review_tree_comparison, "run_git", last_race):
        assert review_tree_comparison._pin(record) is None
    assert len(attempts) == 5 and git_in("rev-parse", ref) == tree

    # A ref naming another tree is never moved: that is still a refusal.
    git_in("update-ref", ref, record.code_base.tree or "", tree)
    refusal = review_tree_comparison._pin(record)
    assert refusal is not None and "already names" in refusal.detail
    assert git_in("rev-parse", ref) == record.code_base.tree

    # A lock another reader holds for a moment is waited out.
    git_in("update-ref", "-d", ref)
    lock = Path(git_in("rev-parse", "--path-format=absolute", "--git-path", ref) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("")
    threading.Timer(0.05, lock.unlink).start()
    assert review_tree_comparison._pin(record) is None
    assert git_in("rev-parse", ref) == tree and not lock.exists()


# -- MIK-R42 round 4: joining a cancelled flight, the sharing bound, one deadline per request -----


def test_a_request_arriving_while_a_cancelled_flight_is_reaped_starts_its_own_computation() -> None:
    owner = process_owner.ReviewerWorklistProcesses()
    answers: list[Any] = []
    errors: list[Exception] = []
    leaving = threading.Event()

    def impatient() -> None:
        with process_owner.worklist_request(leaving):
            _ask(owner, {"tree": "a"}, [], errors)

    with _child_script(_SLEEPING_REPLY) as children:
        first = threading.Thread(target=impatient)
        first.start()
        until = time.monotonic() + 5
        while not children and time.monotonic() < until:
            time.sleep(0.01)
        # Hold the first flight in its reaping: cancelled, child still alive, still registered.
        real_stop = process_owner._stop
        reaping = threading.Event()

        def slow_stop(process: Any) -> None:
            reaping.wait(5)
            real_stop(process)

        with mock.patch.object(process_owner, "_stop", slow_stop):
            leaving.set()
            until = time.monotonic() + 5
            while time.monotonic() < until:
                with owner._lock:
                    cancelled = [f for f in owner._flights.values() if f.cancel.is_set()]
                if cancelled:
                    break
                time.sleep(0.005)
            assert cancelled, "the flight was never cancelled"
            second = threading.Thread(target=_ask, args=(owner, {"tree": "a"}, answers, errors))
            second.start()
            time.sleep(0.2)
            reaping.set()
            first.join(timeout=10)
            second.join(timeout=15)
    assert len(answers) == 1, errors  # the newcomer was answered, not told "cancelled"
    assert [str(error) for error in errors] == ["reviewer worklist computation was cancelled"]
    assert len(children) == 2 and not owner._active and not owner._flights


def test_twelve_identical_concurrent_reads_share_one_child_without_a_refusal() -> None:
    _source()  # Resolve the build once before concurrent requesters use it.
    owner = process_owner.ReviewerWorklistProcesses()
    answers: list[Any] = []
    errors: list[Exception] = []
    with _child_script(_SLEEPING_REPLY) as children:
        threads = [
            threading.Thread(target=_ask, args=(owner, {"tree": "same"}, answers, errors))
            for _ in range(12)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
    assert not errors and len(answers) == 12 and len(children) == 1
    assert all(answer == answers[0] for answer in answers)


def test_the_requests_deadline_runs_from_its_arrival_and_the_child_gets_what_is_left(
    world: World,  # noqa: F811
) -> None:
    world.edit()
    ports = _ports(world)
    slow = review_tree_knowledge._measured_tree_diff

    def slow_diff(*args: Any) -> Any:
        time.sleep(1.2)  # the work before the child starts
        return slow(*args)

    started = time.monotonic()
    with (
        _child_script("import time; time.sleep(30)"),
        mock.patch.object(process_owner, "DEADLINE_SECONDS", 2.0),
        mock.patch.object(review_tree_knowledge, "_measured_tree_diff", slow_diff),
        process_owner.worklist_request(threading.Event()),
    ):
        answer = ports.trees(_query())
    assert answer.state == "refused" and answer.refusal is not None
    assert "deadline" in answer.refusal.detail
    assert time.monotonic() - started < 2.9  # not 1.2 s before plus a whole 2 s after


def test_the_waiting_bound_counts_computations_not_the_requests_sharing_them() -> None:
    process_serving_build.cache_clear()
    identity_threads: list[int] = []
    resolve = build_info.resolve_serving_build

    def observed_build() -> Any:
        identity_threads.append(threading.get_ident())
        return resolve()

    with mock.patch.object(build_info, "resolve_serving_build", observed_build):
        _prove_computation_bound()
    assert identity_threads == [threading.get_ident()]


def _prove_computation_bound() -> None:
    _source()  # Resolve the build once on the caller thread, even when this node runs alone.
    assert process_owner.MAX_WAITING_COMPUTATIONS == 8
    assert process_owner.MAX_ACTIVE_CHILDREN == 2
    assert process_owner.DEADLINE_SECONDS == 60
    owner = process_owner.ReviewerWorklistProcesses()
    answers: list[Any] = []
    errors: list[Exception] = []
    release = threading.Event()
    collect = owner._collect
    threads: list[threading.Thread] = []

    def held(*args: Any) -> Any:
        assert release.wait(30), "requesters did not reach the controlled collection barrier"
        return collect(*args)

    def joined(waiters: int, computations: int) -> None:
        until = time.monotonic() + 20
        while time.monotonic() < until:
            with owner._lock:
                if owner._waiting == waiters and len(owner._flights) == computations:
                    return
            time.sleep(0.005)
        pytest.fail(f"requesters did not join: waiting={owner._waiting}, errors={errors}")

    with (
        _child_script(_REPLY + "json.dump(answer, sys.stdout)") as children,
        mock.patch.object(owner, "_collect", held),
    ):
        try:
            # Sharers never use computation slots, including when the next distinct flight joins.
            threads = [
                threading.Thread(target=_ask, args=(owner, {"tree": 0}, answers, errors))
                for _ in range(12)
            ]
            for thread in threads:
                thread.start()
            joined(12, 1)
            more = [
                threading.Thread(target=_ask, args=(owner, {"tree": n}, answers, errors))
                for n in range(1, 8)
            ]
            threads.extend(more)
            for thread in more:
                thread.start()
            joined(19, 8)
            with pytest.raises(process_owner.WorklistOverloaded):
                owner.compute({"tree": "new"}, _source())
        finally:
            release.set()
            for thread in threads:
                thread.join(timeout=30)
            owner.shutdown()
    assert not errors and len(answers) == 19
    assert len(children) == 8 and not owner._flights and not owner._active


def test_a_flight_thread_start_failure_is_a_refusal_and_the_next_read_recovers(
    world: World,  # noqa: F811
) -> None:
    world.edit()
    owner = process_owner.ReviewerWorklistProcesses()
    with mock.patch("agents_remember.cli.dashboard.ReviewerWorklistProcesses", return_value=owner):
        ports = _ports(world)
    with mock.patch.object(threading.Thread, "start", side_effect=RuntimeError("cannot start")):
        answer = ports.trees(_query())
    assert answer.state == "refused" and answer.refusal is not None
    assert answer.refusal.code == "candidate_unresolved"
    assert "thread could not start" in answer.refusal.detail
    assert not owner._flights and not owner._active and not owner._queue and owner._waiting == 0
    assert not review_leaf_view_memo.LEAF_VIEW_MEMO
    try:
        assert ports.trees(_query()).state == "trees"
    finally:
        owner.shutdown()


def test_an_already_exhausted_idle_request_names_its_lifetime_not_busy_computations() -> None:
    source = _source()
    owner = process_owner.ReviewerWorklistProcesses()
    arrival = process_owner._ARRIVAL.set(time.monotonic() - process_owner.DEADLINE_SECONDS - 1)
    try:
        with pytest.raises(process_owner.WorklistOverloaded) as failed:
            owner.compute({}, source)
        assert "request's lifetime" in str(failed.value) and "busy" not in str(failed.value)
        assert failed.value.code == "reviewer_busy"
        assert failed.value.next_action == "the reviewer is computing other worklists; retry"
        assert not owner._flights and not owner._active and not owner._queue and owner._waiting == 0
    finally:
        process_owner._ARRIVAL.reset(arrival)
        owner.shutdown()


@pytest.mark.parametrize("process_error", [True, False], ids=["process-error", "moved-inputs"])
def test_oversized_failure_text_stays_a_valid_bounded_refusal(
    world: World,  # noqa: F811
    process_error: bool,
) -> None:
    world.edit()
    contract, trees = _live(world)
    text = "source-error-or-path-" * PROSE_MAX_LENGTH
    with (
        mock.patch.object(review_tree_knowledge, "leaf_view_key", return_value=None),
        mock.patch.object(
            review_tree_knowledge,
            "_compute_leaf_parts",
            side_effect=process_owner.WorklistProcessError(text) if process_error else None,
            return_value=(None, False, {}),
        ),
        mock.patch.object(review_tree_knowledge, "moved_inputs", return_value=(text,)),
    ):
        result = review_tree_knowledge._leaf_wide_parts(contract, trees)
    assert isinstance(result, ReviewRefusal)
    assert result.code == ("candidate_unresolved" if process_error else "inputs_changing")
    assert result.detail and len(result.detail) == PROSE_MAX_LENGTH
    assert result.offending_input and len(result.offending_input) <= REFERENCE_MAX_LENGTH
    assert result.next_action
    assert not review_leaf_view_memo.LEAF_VIEW_MEMO


def test_the_blob_hunks_and_unique_binder_ignore_colour_and_ambient_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path / "code")
    lines = [f"VALUE_{n} = {n}\n" for n in range(20)]
    lines[0] = "def land(): return 1\n"
    before = commit(repository, {"pkg/added.py": "".join(lines)})
    lines[9], lines[12] = "VALUE_9 = 100\n", "VALUE_12 = 200\n"
    after = commit(repository, {"pkg/added.py": "".join(lines)})
    clean, hostile = tmp_path / "clean.gitconfig", tmp_path / "hostile.gitconfig"
    clean.write_text("")
    hostile.write_text("[color]\n ui = always\n[diff]\n interHunkContext = 5\n")
    readings = []
    for configuration, context in ((clean, ""), (hostile, ""), (clean, "-u5"), (hostile, "-u5")):
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(configuration))
        monkeypatch.setenv("GIT_DIFF_OPTS", context)
        trees = code.CodeTrees.open(repository, before, after)
        hunks = trees.hunks(trees.base()["pkg/added.py"], trees.candidate()["pkg/added.py"])
        assert hunks is not None and len(hunks) == 2
        assert [(h.old_count, h.new_count) for h in hunks] == [(1, 1), (1, 1)]
        assert trees.unique_binder("land") == "pkg/added.py"
        readings.append([h.to_document() for h in hunks])
    assert all(reading == readings[0] for reading in readings)
