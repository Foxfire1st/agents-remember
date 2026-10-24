"""The explainer's seat order: build, Update, Retire and their states (MIK-R186 rules 1, 5, 6, 8).

Each case protects one promise of the order rather than the bytes: one environment per leaf, a
generation that only grows, the first message sent after the lock is released, an Update that
refuses instead of waiting, and a Retire that waits and leaves a finished receipt.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.application.review_planned_comparison import (
    explainer_application_ports,
)
from agents_remember.canvas import explainer_service
from agents_remember.canvas.explainer_grades import GradeStore, ScalePoint, grades_path
from agents_remember.canvas.explainer_paths import (
    generation_path,
    leaf_workspace,
    lock_path,
    retire_receipts_root,
)
from agents_remember.canvas.explainer_service import (
    ARCHIVED,
    BUSY,
    IN_TURN,
    RETIRED,
    BoundComparison,
    ExplainerDeps,
    ExplainerRefused,
    LeafFacts,
    LeafScope,
    PreparedPacket,
    build_seat,
    retire_seat,
    seat_state,
    update_seat,
)
from agents_remember.canvas.explainer_state import (
    agent_id,
    generation,
    raise_generation,
    read_binding,
    read_receipt,
)
from agents_remember.kernel.file_lock import lock_held
from agents_remember.models.knowledge.review_frame import FrameBinding
from agents_remember.serving.explainer import ExplainerComposition, register_explainer_routes
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS, wait_until
from canvas_host_test_support import REPO, TASK, config, packet, record
from fastapi import FastAPI
from fastapi.testclient import TestClient

LEAF = "260928-MIK-L183"
NOW = "2026-10-22T08:00:00+00:00"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _repository(root: Path, name: str, files: dict[str, str]) -> Path:
    repo = root / name
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    for path, text in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one")
    return repo


class FakeHost:
    """A host that records what it was asked, and answers the states a test sets."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.sent: list[tuple[str, str]] = []
        self.archived: list[str] = []
        self.resumed: list[str] = []
        self.states: dict[str, str] = {}
        self.turns: set[str] = set()
        self.send_while_locked: list[Path] = []
        self.lock_probe: Path | None = None

    def create_seat(self, *, agent_id, workspace=None, title=None, capsule=None, labels=None):
        self.created.append(agent_id)
        self.workspaces = getattr(self, "workspaces", [])
        self.workspaces.append((workspace, title, labels))
        self.capsules = getattr(self, "capsules", [])
        self.capsules.append(capsule)
        self.states[agent_id] = "live"

    def send(self, agent_id: str, text: str, *, message_id: str | None = None) -> None:
        if self.lock_probe is not None:
            base = (
                self.lock_probe.with_name(self.lock_probe.name.removesuffix(".lock"))
                if self.lock_probe.name.endswith(".lock")
                else self.lock_probe
            )
            if lock_held(base) or lock_held(self.lock_probe):
                self.send_while_locked.append(self.lock_probe)
        self.message_ids = getattr(self, "message_ids", [])
        self.message_ids.append(message_id)
        self.sent.append((agent_id, text))

    def resume(self, agent_id: str) -> None:
        self.resumed.append(agent_id)
        self.states[agent_id] = "live"

    def archive(self, agent_id: str) -> None:
        self.archived.append(agent_id)
        self.states[agent_id] = "archived"

    def seat_state(self, agent_id: str) -> str:
        return self.states.get(agent_id, "missing")

    def cancel(self, agent_id: str, *, request_id: str):
        del agent_id, request_id
        return {
            "status": "cancelled",
            "turnStopped": True,
            "permissionClosed": True,
            "requestUncertain": False,
        }

    def in_turn(self, agent_id: str) -> bool:
        return agent_id in self.turns


def _scope() -> LeafScope:
    return LeafScope(repository_id=REPO, task=TASK, leaf_id=LEAF)


def _deps(
    runtime,
    host: FakeHost,
    bound: BoundComparison,
    *,
    comparison=None,
    facts: LeafFacts | None = None,
) -> ExplainerDeps:
    return ExplainerDeps(
        config=runtime,
        host=host,
        facts=lambda _scope: facts or LeafFacts(status="inProgress"),
        comparison=lambda _scope, _facts: bound,
        prepare=lambda _scope, _facts, _bound: PreparedPacket(
            packet=packet(comparison or record(7)), digest="sha256:" + "0" * 64
        ),
        capsule=lambda _scope: "You are the explainer.\n",
        now=lambda: NOW,
    )


def _world(tmp_path: Path):
    """A code and a memory repository, one recorded comparison, and the task folder."""

    code = _repository(tmp_path, "repo", {"src/app.py": "x = 1\n"})
    memory = _repository(tmp_path, "memory", {"system/settings.md": "s\n"})
    runtime = config(tmp_path)
    (tmp_path / "tasks" / REPO / TASK).mkdir(parents=True, exist_ok=True)
    comparison = record(7).model_copy(
        update={
            "code_base": record(7).code_base.model_copy(
                update={"repository": str(code), "tree": _git(code, "rev-parse", "HEAD^{tree}")}
            ),
            "code_candidate": record(7).code_candidate.model_copy(
                update={"repository": str(code), "tree": _git(code, "rev-parse", "HEAD^{tree}")}
            ),
            "memory_base": record(7).memory_base.model_copy(
                update={"repository": str(memory), "tree": _git(memory, "rev-parse", "HEAD^{tree}")}
            ),
            "memory_candidate": record(7).memory_candidate.model_copy(
                update={"repository": str(memory), "tree": _git(memory, "rev-parse", "HEAD^{tree}")}
            ),
        }
    )
    binding = FrameBinding.from_packet(packet(comparison).comparison)
    return runtime, BoundComparison(binding=binding, record_digest="sha256:" + "1" * 64)


def test_a_send_builds_one_environment_and_one_seat_after_the_lock(tmp_path: Path) -> None:
    runtime, bound = _world(tmp_path)
    comparison = record(7)
    host = FakeHost()
    deps = _deps(runtime, host, bound, comparison=comparison)
    scope = _scope()
    host.lock_probe = lock_path(runtime, REPO, TASK, LEAF)

    outcome = build_seat(deps, scope, request_id="r1", message="explain this")

    workspace = leaf_workspace(runtime, REPO, TASK, LEAF)
    assert outcome.built and outcome.message_sent
    assert host.created == [outcome.agent_id]
    assert [text for _agent, text in host.sent] == ["explain this"]
    assert host.send_while_locked == []
    # The seat is created with the capsule as its instructions, and no turn is started (rule 5).
    assert host.capsules == ["You are the explainer.\n"]
    stored = read_binding(workspace)
    assert stored is not None
    assert (workspace / stored.snapshot / "code" / "src" / "app.py").read_text() == "x = 1\n"
    assert generation(generation_path(runtime, REPO, TASK, LEAF)) == 1


def test_a_repeated_request_reconciles_the_same_build(tmp_path: Path) -> None:
    runtime, bound = _world(tmp_path)
    comparison = record(7)
    host = FakeHost()
    deps = _deps(runtime, host, bound, comparison=comparison)
    scope = _scope()
    first = build_seat(deps, scope, request_id="r1", message="explain this")

    second = build_seat(deps, scope, request_id="r1", message="explain this again")

    assert second.duplicate and not second.message_sent
    assert second.agent_id == first.agent_id
    assert host.created == [first.agent_id]
    assert len(host.sent) == 1
    assert generation(generation_path(runtime, REPO, TASK, LEAF)) == 1


def test_a_later_send_reaches_the_live_seat_without_a_second_one(tmp_path: Path) -> None:
    runtime, bound = _world(tmp_path)
    comparison = record(7)
    host = FakeHost()
    deps = _deps(runtime, host, bound, comparison=comparison)
    scope = _scope()
    first = build_seat(deps, scope, request_id="r1", message="explain this")

    later = build_seat(deps, scope, request_id="r2", message="and the tray pick")

    assert later.current and later.message_sent and not later.built
    assert later.agent_id == first.agent_id
    assert host.created == [first.agent_id]
    assert [text for _agent, text in host.sent] == ["explain this", "and the tray pick"]
    assert generation(generation_path(runtime, REPO, TASK, LEAF)) == 1


def test_a_closed_seat_is_resumed_by_the_send_that_reaches_it(tmp_path: Path) -> None:
    """Rule 5: a closed seat (a host restart) is the same seat, and this send resumes it.

    The same id takes the message and the generation does not move; a closed seat that was never
    resumed would refuse the message, which is what the sandbox run met before this rule held.
    """

    runtime, bound = _world(tmp_path)
    host = FakeHost()
    deps = _deps(runtime, host, bound, comparison=record(7))
    scope = _scope()
    first = build_seat(deps, scope, request_id="r1", message="explain this")
    host.states[first.agent_id] = "closed"

    later = build_seat(deps, scope, request_id="r2", message="go on")

    assert host.resumed == [first.agent_id]
    assert later.agent_id == first.agent_id and later.generation == 1
    assert host.created == [first.agent_id]
    assert host.sent[-1][0] == first.agent_id
    assert generation(generation_path(runtime, REPO, TASK, LEAF)) == 1


def test_an_archived_seat_gets_a_new_generation_without_a_new_environment(
    tmp_path: Path,
) -> None:
    runtime, bound = _world(tmp_path)
    comparison = record(7)
    host = FakeHost()
    deps = _deps(runtime, host, bound, comparison=comparison)
    scope = _scope()
    first = build_seat(deps, scope, request_id="r1", message="explain this")
    host.states[first.agent_id] = "missing"

    second = build_seat(deps, scope, request_id="r2", message="try again")

    assert second.generation == 2 and second.agent_id != first.agent_id
    assert host.created == [first.agent_id, second.agent_id]
    assert not second.built
    assert generation(generation_path(runtime, REPO, TASK, LEAF)) == 2


def test_a_build_after_master_retirement_is_refused_by_name(tmp_path: Path) -> None:
    runtime, bound = _world(tmp_path)
    comparison = record(7)
    host = FakeHost()
    deps = _deps(
        runtime,
        host,
        bound,
        comparison=comparison,
        facts=LeafFacts(status="inProgress", archived=True),
    )

    with pytest.raises(ExplainerRefused) as refused:
        build_seat(deps, _scope(), request_id="r1", message="explain this")

    assert refused.value.reason == ARCHIVED
    assert host.created == [] and host.sent == []


def test_an_unchanged_update_answers_current(tmp_path: Path) -> None:
    runtime, bound = _world(tmp_path)
    comparison = record(7)
    host = FakeHost()
    deps = _deps(runtime, host, bound, comparison=comparison)
    scope = _scope()
    build_seat(deps, scope, request_id="r1", message="explain this")

    update = update_seat(deps, scope)

    assert update.current and update.snapshot is None


def test_an_update_refuses_while_the_lock_is_held(tmp_path: Path) -> None:
    runtime, bound = _world(tmp_path)
    comparison = record(7)
    host = FakeHost()
    deps = _deps(runtime, host, bound, comparison=comparison)
    scope = _scope()
    build_seat(deps, scope, request_id="r1", message="explain this")
    workspace = leaf_workspace(runtime, REPO, TASK, LEAF)
    lock_file = lock_path(runtime, REPO, TASK, LEAF)
    assert lock_file.is_file()
    assert not lock_file.with_name(f"{lock_file.name}.lock").exists()
    script = (
        "import sys, time\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from pathlib import Path\n"
        "from agents_remember.kernel.file_lock import exclusive_file_lock\n"
        "with exclusive_file_lock(Path(sys.argv[2]), 'explainer leaf'):\n"
        "    print('held', flush=True)\n"
        "    time.sleep(10)\n"
    )
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(Path(__file__).resolve().parents[1] / "src"),
            str(workspace),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(ExplainerRefused) as refused:
            update_seat(deps, scope)
    finally:
        holder.kill()
        holder.communicate(timeout=HANG_GUARD_SECONDS)

    assert refused.value.reason == BUSY


def test_an_update_refuses_while_the_explainer_is_in_a_turn(tmp_path: Path) -> None:
    runtime, bound = _world(tmp_path)
    comparison = record(7)
    host = FakeHost()
    deps = _deps(runtime, host, bound, comparison=comparison)
    scope = _scope()
    first = build_seat(deps, scope, request_id="r1", message="explain this")
    host.turns.add(first.agent_id)

    with pytest.raises(ExplainerRefused) as refused:
        update_seat(deps, scope)

    assert refused.value.reason == IN_TURN


def test_an_update_after_retire_is_refused(tmp_path: Path) -> None:
    runtime, bound = _world(tmp_path)
    comparison = record(7)
    host = FakeHost()
    deps = _deps(runtime, host, bound, comparison=comparison)
    scope = _scope()
    build_seat(deps, scope, request_id="r1", message="explain this")
    retire_seat(deps, scope, receipt_id="retire-1")

    with pytest.raises(ExplainerRefused) as refused:
        update_seat(deps, scope)

    assert refused.value.reason == RETIRED


def test_retire_leaves_a_finished_receipt_and_keeps_the_counter(tmp_path: Path) -> None:
    runtime, bound = _world(tmp_path)
    comparison = record(7)
    host = FakeHost()
    deps = _deps(runtime, host, bound, comparison=comparison)
    scope = _scope()
    first = build_seat(deps, scope, request_id="r1", message="explain this")

    outcome = retire_seat(deps, scope, receipt_id="retire-1")

    receipts = retire_receipts_root(tmp_path / "tasks" / REPO / TASK, LEAF)
    stored = read_receipt(receipts, "retire-1")
    assert outcome.receipt.state == "finished"
    assert stored is not None and stored.agent_id == first.agent_id
    assert host.archived == [first.agent_id]
    assert generation(generation_path(runtime, REPO, TASK, LEAF)) == 1


def test_a_repeated_retire_answers_the_same_receipt(tmp_path: Path) -> None:
    runtime, bound = _world(tmp_path)
    comparison = record(7)
    host = FakeHost()
    deps = _deps(runtime, host, bound, comparison=comparison)
    scope = _scope()
    build_seat(deps, scope, request_id="r1", message="explain this")
    first = retire_seat(deps, scope, receipt_id="retire-1")

    second = retire_seat(deps, scope, receipt_id="retire-1")

    assert second.receipt.receipt_id == first.receipt.receipt_id
    assert host.archived == [first.receipt.agent_id]


def test_the_page_state_names_the_binding_and_the_seat(tmp_path: Path) -> None:
    runtime, bound = _world(tmp_path)
    comparison = record(7)
    host = FakeHost()
    deps = _deps(runtime, host, bound, comparison=comparison)
    scope = _scope()

    empty = seat_state(deps, scope)
    assert not empty.present and empty.seat == "missing"

    build_seat(deps, scope, request_id="r1", message="explain this")
    live = seat_state(deps, scope)
    assert live.present and live.seat == "live"
    assert live.as_record()["label"] == "#7"

    retire_seat(deps, scope, receipt_id="retire-1")
    gone = seat_state(deps, scope)
    assert gone.seat == "retired" and gone.receipt is not None


def test_a_planned_binding_is_labelled_planned(tmp_path: Path) -> None:
    runtime, bound = _world(tmp_path)
    planned = bound.binding.model_copy(
        update={"comparison_owner": "planned", "comparison_number": 0}
    )
    host = FakeHost()
    deps = _deps(
        runtime,
        host,
        BoundComparison(binding=planned, planned=None, record_digest=None),
        comparison=record(7),
        facts=LeafFacts(status="planning"),
    )

    build_seat(deps, _scope(), request_id="r1", message="explain the plan")
    state = seat_state(deps, _scope())

    assert state.as_record()["label"] == "planned"


# -- the routes ---------------------------------------------------------------------------------


def _client(composition) -> TestClient:
    app = FastAPI()
    register_explainer_routes(app, _with_ports(composition))
    return TestClient(app)


def _with_ports(composition: ExplainerComposition) -> ExplainerComposition:
    if composition.application is None:
        return replace(composition, application=explainer_application_ports())
    return composition


def _url(tail: str) -> str:
    return f"/api/explainer/{REPO}/{TASK}/{LEAF}/{tail}"


def test_the_routes_refuse_by_name_when_no_service_is_wired() -> None:
    client = _client(ExplainerComposition())

    answer = client.get(_url("state"))

    assert answer.status_code == 503
    assert answer.json()["reason"] == "explainer-unavailable"


def test_a_send_builds_over_the_route_and_names_the_seat(tmp_path: Path) -> None:
    runtime, bound = _world(tmp_path)
    host = FakeHost()
    client = _client(ExplainerComposition(deps=_deps(runtime, host, bound, comparison=record(7))))

    answer = client.post(_url("build"), json={"requestId": "r1", "message": "explain this"})

    assert answer.status_code == 200
    seat = answer.json()["seat"]
    assert seat["built"] and seat["messageSent"]
    assert seat["agentId"] == host.created[0]
    state = client.get(_url("state")).json()["explainer"]
    assert state["present"] and state["seat"] == "live"
    assert state["label"] == "#7"


def test_a_send_without_its_request_id_is_a_400(tmp_path: Path) -> None:
    runtime, bound = _world(tmp_path)
    client = _client(
        ExplainerComposition(deps=_deps(runtime, FakeHost(), bound, comparison=record(7)))
    )

    answer = client.post(_url("build"), json={"message": "explain this"})

    assert answer.status_code == 400
    assert answer.json()["reason"] == "request-invalid"


def test_an_update_refusal_is_a_typed_409(tmp_path: Path) -> None:
    runtime, bound = _world(tmp_path)
    client = _client(
        ExplainerComposition(deps=_deps(runtime, FakeHost(), bound, comparison=record(7)))
    )

    answer = client.post(_url("update"))

    assert answer.status_code == 409
    assert answer.json()["reason"] == "explainer-missing"


def test_the_retire_route_leaves_a_finished_receipt(tmp_path: Path) -> None:
    runtime, bound = _world(tmp_path)
    host = FakeHost()
    client = _client(ExplainerComposition(deps=_deps(runtime, host, bound, comparison=record(7))))
    client.post(_url("build"), json={"requestId": "r1", "message": "explain this"})

    answer = client.post(_url("retire"), json={"receiptId": "retire-1"})

    assert answer.status_code == 200
    assert answer.json()["retire"]["receipt"]["state"] == "finished"


def test_request_reconciliation_is_leaf_bound_concurrent_safe_and_reconciles_failed_delivery(
    tmp_path: Path,
) -> None:
    """MIK-R186 rule 5 / MIK-R108: per-leaf ledger, concurrent same-id deduplication, failed send retry."""

    runtime, bound = _world(tmp_path)
    comparison = record(7)
    host = FakeHost()
    deps = _deps(runtime, host, bound, comparison=comparison)
    scope_a = _scope()
    scope_b = LeafScope(repository_id=REPO, task=TASK, leaf_id="260928-MIK-L184")

    # Concurrent same-ID requests on scope_a deliver once.
    barrier = threading.Barrier(2)
    outcomes: list = []

    def send_concurrent() -> None:
        barrier.wait(timeout=HANG_GUARD_SECONDS)
        outcomes.append(build_seat(deps, scope_a, request_id="r-same", message="concurrent"))

    t1 = threading.Thread(target=send_concurrent)
    t2 = threading.Thread(target=send_concurrent)
    t1.start()
    t2.start()
    t1.join(timeout=HANG_GUARD_SECONDS)
    t2.join(timeout=HANG_GUARD_SECONDS)
    assert len(outcomes) == 2
    assert sorted(o.duplicate for o in outcomes) == [False, True]
    assert len(host.sent) == 1

    # Equal request_id on another leaf builds its own seat and never collides with scope_a.
    out_b = build_seat(deps, scope_b, request_id="r-same", message="other leaf")
    assert not out_b.duplicate and out_b.agent_id != outcomes[0].agent_id

    # Failed send records failed outcome; retry reconciles with exact deterministic message UUID.
    fail_once = {"fail": True}
    orig_send = host.send

    def flaky_send(agent_id: str, text: str, *, message_id: str | None = None) -> None:
        if fail_once["fail"]:
            fail_once["fail"] = False
            raise ExplainerRefused("host-paseo_send_outcome_unknown", "connection dropped")
        orig_send(agent_id, text, message_id=message_id)

    host.send = flaky_send  # type: ignore[method-assign]
    with pytest.raises(ExplainerRefused):
        build_seat(deps, scope_a, request_id="r-flaky", message="retry me")
    recovered = build_seat(deps, scope_a, request_id="r-flaky", message="retry me")
    assert not recovered.duplicate and recovered.message_sent
    msg_id_fn = getattr(explainer_service, "request_message_id", None)
    assert msg_id_fn is not None
    assert recovered.message_id == msg_id_fn(LEAF, "r-flaky")
    assert host.message_ids[-1] == msg_id_fn(LEAF, "r-flaky")


def test_failed_build_cleans_up_partials_and_lost_create_reply_reconciles_same_seat(
    tmp_path: Path,
) -> None:
    """MIK-R186 rules 2, 5: pre-seat failure rolls back workspace/binding; lost create reply reconciles."""

    runtime, bound = _world(tmp_path)
    comparison = record(7)
    host = FakeHost()
    scope = _scope()
    workspace = leaf_workspace(runtime, REPO, TASK, LEAF)
    gen_file = generation_path(runtime, REPO, TASK, LEAF)

    # Pre-create refusal rolls back partial workspace, binding.json, and generation counter.
    orig_create = host.create_seat

    def refuse_create(**_kwargs):
        raise ExplainerRefused("explainer-seat-config-unapplied", "wrong model")

    host.create_seat = refuse_create  # type: ignore[method-assign]
    deps = _deps(runtime, host, bound, comparison=comparison)
    with pytest.raises(ExplainerRefused):
        build_seat(deps, scope, request_id="r-fail", message="explain")
    assert not workspace.exists()
    assert read_binding(workspace) is None
    assert generation(gen_file) == 0

    # Remotely successful create with lost reply reconciles the same seat and generation 1 on retry.
    lost_once = {"lost": True}

    def lost_reply_create(**kwargs):
        orig_create(**kwargs)
        if lost_once["lost"]:
            lost_once["lost"] = False
            raise TimeoutError("bridge reply timed out")

    host.create_seat = lost_reply_create  # type: ignore[method-assign]
    with pytest.raises(TimeoutError):
        build_seat(deps, scope, request_id="r-lost", message="explain")
    assert read_binding(workspace) is None
    reconciled = build_seat(deps, scope, request_id="r-lost", message="explain")
    assert reconciled.generation == 1
    assert reconciled.agent_id == agent_id(workspace, 1)
    assert read_binding(workspace) is not None


@pytest.mark.parametrize(
    "fault_step",
    ["comparison", "prepare", "build"],
    ids=["comparison", "prepare", "build"],
)
def test_a_build_faulted_before_the_seat_rolls_back_every_effect(
    tmp_path: Path, fault_step: str
) -> None:
    """S09: each pre-seat step faulted leaves zero partials: no workspace, no binding, no seat."""

    runtime, bound = _world(tmp_path)
    comparison = record(7)
    host = FakeHost()
    scope = _scope()
    workspace = leaf_workspace(runtime, REPO, TASK, LEAF)
    gen_file = generation_path(runtime, REPO, TASK, LEAF)
    deps = _deps(runtime, host, bound, comparison=comparison)

    def refused(_scope, _facts, _bound=None):
        raise ExplainerRefused("evidence-unavailable", "faulted for the test")

    if fault_step == "comparison":
        deps = replace(deps, comparison=refused)
    elif fault_step == "prepare":
        deps = replace(deps, prepare=refused)
    if fault_step in {"comparison", "prepare"}:
        with pytest.raises(ExplainerRefused):
            build_seat(deps, scope, request_id="r-fault", message="explain")
    else:

        def partial_build(*args, **kwargs):
            workspace.mkdir(parents=True, exist_ok=True)
            (workspace / "partial.txt").write_text("partial\n")
            raise OSError("the tree cannot be read")

        with (
            mock.patch.object(explainer_service, "_build_environment", side_effect=partial_build),
            pytest.raises(OSError),
        ):
            build_seat(deps, scope, request_id="r-fault", message="explain")
    assert host.created == []
    assert not workspace.exists()
    assert read_binding(workspace) is None
    assert generation(gen_file) == 0


def test_update_refreshes_generation_under_lock_and_seat_replacement_cleans_processes(
    tmp_path: Path,
) -> None:
    """MIK-R186 rules 5, 6, 8: Update refreshes generation under lock; replacement ends old processes."""

    runtime, bound = _world(tmp_path)
    comparison = record(7)
    host = FakeHost()
    deps = _deps(runtime, host, bound, comparison=comparison)
    scope = _scope()
    build_seat(deps, scope, request_id="r1", message="explain")
    workspace = leaf_workspace(runtime, REPO, TASK, LEAF)

    # Advance generation to 2 and mark seat 2 in_turn while seat 1 is not in_turn.
    gen_file = generation_path(runtime, REPO, TASK, LEAF)
    raise_generation(gen_file)
    seat2 = agent_id(workspace, 2)
    host.turns.add(seat2)
    with pytest.raises(ExplainerRefused) as in_turn_err:
        update_seat(deps, scope)
    assert in_turn_err.value.reason == IN_TURN
    host.turns.clear()

    # Replacement branch checks archive exception and ends marked processes before raising generation.
    host.states[seat2] = "missing"
    child = subprocess.Popen(
        ["sleep", "300"],
        env={**os.environ, "AR_EXPLAINER_WORKSPACE": str(workspace), "PASEO_AGENT_ID": seat2},
    )
    try:
        replaced = build_seat(deps, scope, request_id="r2", message="replace")
        child.wait(timeout=HANG_GUARD_SECONDS)
        assert replaced.generation == 3
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()


def test_failed_retire_stays_pending_and_resumes_original_receipt_and_prunes_gone_grades(
    tmp_path: Path,
) -> None:
    """MIK-R186 rule 8 / MIK-R187 rule 4: pending retire retry, grade pruning, and state fields."""

    runtime, bound = _world(tmp_path)
    comparison = record(7)
    host = FakeHost()
    deps = _deps(runtime, host, bound, comparison=comparison)
    scope = _scope()
    built = build_seat(
        deps,
        scope,
        request_id="r1",
        message="",
        picks=[{"reference": "src/app.py", "note": "focus here"}],
    )
    assert "src/app.py" in host.sent[-1][1]
    state_fn = getattr(explainer_service, "state_of", seat_state)
    st = state_fn(deps, scope).as_record()
    assert st.get("workspaceId") == str(built.workspace)
    assert st["agentId"] == built.agent_id

    # Seed grades on a grade-only leaf (no published page): one present path, one gone path.
    task_dir = tmp_path / "tasks" / REPO / TASK
    store = GradeStore(grades_path(task_dir, LEAF), LEAF)
    store.define(
        "risk",
        (
            ScalePoint(grade="high", meaning="high risk"),
            ScalePoint(grade="low", meaning="low risk"),
        ),
    )
    store.grade(
        references=("file:src/app.py@a", "file:src/gone.py@b"),
        category="risk",
        grade="high",
        reason="checked",
        fingerprints={"file:src/app.py@a": "fp-1", "file:src/gone.py@b": "fp-2"},
        binding=bound.binding,
        at=NOW,
    )
    updated = update_seat(deps, scope)
    assert updated.removed_grades == 1
    assert updated.stale_grades == 1
    assert [g.reference for g in store.load().grades] == ["file:src/app.py@a"]

    # Archive failure during retire_seat leaves receipt pending and workspace intact; retry resumes it.
    fail_archive = {"fail": True}
    orig_archive = host.archive

    def flaky_archive(agent_id: str) -> None:
        if fail_archive["fail"]:
            fail_archive["fail"] = False
            raise RuntimeError("host unreachable")
        orig_archive(agent_id)

    host.archive = flaky_archive  # type: ignore[method-assign]
    pending_out = retire_seat(deps, scope, receipt_id="retire-retry")
    assert pending_out.receipt.state == "pending"
    assert not pending_out.workspace_removed
    assert built.workspace.is_dir()

    # Bump generation counter to 99; retry still resumes the original generation 1 pending receipt!
    raise_generation(generation_path(runtime, REPO, TASK, LEAF))
    finished_out = retire_seat(deps, scope, receipt_id="retire-retry")
    assert finished_out.receipt.state == "finished"
    assert finished_out.workspace_removed
    assert finished_out.receipt.generation == 1
    assert finished_out.receipt.agent_id == built.agent_id


def test_retire_prunes_only_gone_grades_and_keeps_changed_but_present_ones(
    tmp_path: Path,
) -> None:
    """G02: Retire removes only absent references, counts them, and keeps changed/unread-but-present."""

    runtime, bound = _world(tmp_path)
    comparison = record(7)
    host = FakeHost()
    deps = _deps(runtime, host, bound, comparison=comparison)
    deps = replace(
        deps,
        present_references=lambda _scope, _binding: {
            "file:src/app.py@a",
            "file:src/changed.py@c",
        },
    )
    scope = _scope()
    build_seat(deps, scope, request_id="r1", message="explain this")
    task_dir = tmp_path / "tasks" / REPO / TASK
    store = GradeStore(grades_path(task_dir, LEAF), LEAF)
    store.define(
        "risk",
        (
            ScalePoint(grade="high", meaning="high risk"),
            ScalePoint(grade="low", meaning="low risk"),
        ),
    )
    # Three grades on a grade-only leaf (no page): one present, one gone, one changed-but-present.
    store.grade(
        references=("file:src/app.py@a", "file:src/gone.py@b", "file:src/changed.py@c"),
        category="risk",
        grade="high",
        reason="checked",
        fingerprints={
            "file:src/app.py@a": "fp-1",
            "file:src/gone.py@b": "fp-2",
            "file:src/changed.py@c": "fp-old",
        },
        binding=bound.binding,
        at=NOW,
    )

    outcome = retire_seat(deps, scope, receipt_id="retire-prune")

    assert outcome.receipt.state == "finished"
    assert outcome.removed_grades == 1
    remaining = {g.reference for g in store.load().grades}
    # The gone block's grade is pruned; the present one and the changed-but-present one are kept.
    assert remaining == {"file:src/app.py@a", "file:src/changed.py@c"}


class _BlockingHost(FakeHost):
    """A host whose create waits, so a state read can answer while the build is in flight."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.gate = threading.Event()

    def create_seat(self, **kwargs):
        self.entered.set()
        self.gate.wait(HANG_GUARD_SECONDS)
        return super().create_seat(**kwargs)


def test_state_reports_the_native_build_progress_before_the_seat_exists(tmp_path: Path) -> None:
    """U05: /state reads the build the service is running, not a map the route never writes."""

    runtime, bound = _world(tmp_path)
    host = _BlockingHost()
    client = _client(ExplainerComposition(deps=_deps(runtime, host, bound, comparison=record(7))))
    answer: dict[str, object] = {}

    thread = threading.Thread(
        target=lambda: answer.setdefault(
            "sent",
            client.post(_url("build"), json={"requestId": "r1", "message": "explain this"}),
        )
    )
    thread.start()
    assert host.entered.wait(HANG_GUARD_SECONDS)
    building: dict[str, object] | None = None

    def _build_progress() -> bool:
        nonlocal building
        body = client.get(_url("state")).json()
        building = body.get("building")
        return bool(building)

    wait_until(_build_progress, "the /state build progress to appear")
    host.gate.set()
    thread.join(HANG_GUARD_SECONDS)

    assert not thread.is_alive()
    assert building is not None
    assert building["state"] == "building"
    assert building["step"] in {"bound", "seat"}


def test_a_comparison_that_moves_during_the_build_wins_over_the_packet(tmp_path: Path) -> None:
    """E03: the recheck after the environment write refuses a move instead of binding stale sides."""

    runtime, bound = _world(tmp_path)
    moved = replace(
        bound,
        binding=bound.binding.model_copy(update={"code_candidate_tree": "f" * 40}),
    )
    calls = {"comparison": 0}

    def comparison(_scope, _facts):
        calls["comparison"] += 1
        return bound if calls["comparison"] == 1 else moved

    host = FakeHost()
    deps = replace(_deps(runtime, host, bound, comparison=record(7)), comparison=comparison)
    client = _client(ExplainerComposition(deps=deps))

    answer = client.post(_url("build"), json={"requestId": "r1", "message": "explain this"})

    assert answer.status_code == 409
    assert answer.json()["reason"] == "binding-moved"
    assert host.created == []
