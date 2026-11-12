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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application.review_artifact_cleanup import cleanup_review_artifacts
from agents_remember.application.review_planned_comparison import (
    explainer_application_ports,
)
from agents_remember.canvas import explainer_maintenance as maintenance
from agents_remember.canvas import explainer_server, explainer_service
from agents_remember.canvas.explainer_grades import GradeStore, ScalePoint, grades_path
from agents_remember.canvas.explainer_maintenance import (
    retire_seat,
    seat_state,
    update_seat,
)
from agents_remember.canvas.explainer_paths import (
    explainer_task_folder,
    generation_path,
    leaf_lock_resource,
    leaf_workspace,
    lock_path,
    retire_receipts_root,
)
from agents_remember.canvas.explainer_server import ExplainerJudgment
from agents_remember.canvas.explainer_service import (
    _BUILD_CANCELS,
    _BUILD_STATES,
    ARCHIVED,
    BUILD_STALL_SECONDS,
    BUSY,
    END_TO_END_REQUEST,
    IN_TURN,
    MISSING,
    BoundComparison,
    ExplainerDeps,
    ExplainerRefused,
    LeafFacts,
    LeafScope,
    PreparedPacket,
    _attempt_for,
    _fail_stalled_builds,
    _progress_tracker,
    build_seat,
    cancel_build,
)
from agents_remember.canvas.explainer_state import (
    agent_id,
    generation,
    raise_generation,
    read_binding,
    read_receipt,
    seat_for_workspace,
)
from agents_remember.kernel.file_lock import lock_held, lock_path_for
from agents_remember.models.knowledge.review_frame import FrameBinding
from agents_remember.serving import explainer as explainer_routes_module
from agents_remember.serving.explainer import (
    ExplainerComposition,
    ExplainerWiring,
    explainer_composition,
)
from agents_remember.serving.explainer import _facts as explainer_facts
from agents_remember.serving.explainer_routes import register_explainer_routes
from agents_remember.tasks.task_paths import slugify
from agents_remember.worktrees.services import ReviewArtifactCleanupRequest
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
        self.held_seats: set[str] = set()
        self.send_while_locked: list[Path] = []
        self.lock_probe: Path | None = None

    def create_seat(self, *, agent_id, workspace=None, title=None, capsule=None, labels=None):
        # A host that still holds the first generation's seat id, as it does after a failed attempt
        # that created the seat and never used it.
        held = getattr(self, "held_seats", None)
        if held and agent_id in held:
            raise ExplainerRefused("explainer-seat-held", f"the host already holds {agent_id}")
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
        recheck=lambda _scope, _facts, _bound: True,
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
                update={
                    "repository": str(code),
                    "tree": _git(code, "rev-parse", "HEAD^{tree}"),
                }
            ),
            "code_candidate": record(7).code_candidate.model_copy(
                update={
                    "repository": str(code),
                    "tree": _git(code, "rev-parse", "HEAD^{tree}"),
                }
            ),
            "memory_base": record(7).memory_base.model_copy(
                update={
                    "repository": str(memory),
                    "tree": _git(memory, "rev-parse", "HEAD^{tree}"),
                }
            ),
            "memory_candidate": record(7).memory_candidate.model_copy(
                update={
                    "repository": str(memory),
                    "tree": _git(memory, "rev-parse", "HEAD^{tree}"),
                }
            ),
        }
    )
    binding = FrameBinding.from_packet(packet(comparison).comparison)
    return runtime, BoundComparison(binding=binding, record_digest="sha256:" + "1" * 64)


def test_a_cancelled_build_reports_its_step_and_stops_at_the_next_checkpoint(
    tmp_path: Path,
) -> None:
    """MIK-R211 v2 rules 4 and 5: Cancel ends the attempt, and its record says where it stopped."""

    runtime, bound = _world(tmp_path)
    deps = _deps(runtime, FakeHost(), bound)
    scope = _scope()
    report, state = _progress_tracker(scope, "attempt-1", None)
    report("checkout-code", {"state": "running"})
    assert state["percent"] == 45

    ended = cancel_build(deps, scope, request_id="attempt-1")

    assert ended is not None
    assert ended["state"] == "failed"
    assert ended["failedStep"] == "checkout-code"
    assert str(ended["error"]).startswith("build-cancelled")
    with pytest.raises(ExplainerRefused) as stopped:
        report("checkout-code", {"state": "done"})
    assert stopped.value.reason == "build-cancelled"
    _BUILD_STATES.pop((scope.repository_id, scope.task, scope.leaf_id), None)
    _BUILD_CANCELS.pop((scope.repository_id, scope.task, scope.leaf_id), None)


def test_a_cancel_naming_another_attempt_leaves_the_live_build_alone(
    tmp_path: Path,
) -> None:
    """A stale page must not end an attempt it is not looking at."""

    runtime, bound = _world(tmp_path)
    deps = _deps(runtime, FakeHost(), bound)
    scope = _scope()
    _report, state = _progress_tracker(scope, "attempt-2", None)

    assert cancel_build(deps, scope, request_id="attempt-1") is None
    assert state["state"] == "building"
    _BUILD_STATES.clear()
    _BUILD_CANCELS.clear()


def test_a_build_that_stops_reporting_is_failed_with_its_step_and_reason(
    tmp_path: Path,
) -> None:
    """A dead build may not answer 'building' forever (MIK-R211 v2 rule 4)."""

    _report, state = _progress_tracker(_scope(), "attempt-3", None)
    state["step"] = "checkout-memory"
    state["percent"] = 45
    quiet_since = state["updatedAt"]

    # Inside the window nothing is touched; past it the attempt is ended and named.
    assert _fail_stalled_builds(now=quiet_since + 60.0) == []
    ended = _fail_stalled_builds(now=quiet_since + BUILD_STALL_SECONDS + 1.0)

    assert len(ended) == 1
    assert ended[0]["state"] == "failed"
    assert ended[0]["failedStep"] == "checkout-memory"
    assert "build-stalled" in str(ended[0]["error"])
    assert "checkout-memory" in str(ended[0]["error"])
    assert "45%" in str(ended[0]["error"])
    # A second pass has nothing left to end.
    assert _fail_stalled_builds(now=quiet_since + 10 * BUILD_STALL_SECONDS) == []
    _BUILD_STATES.clear()
    _BUILD_CANCELS.clear()


def test_checkout_progress_reports_real_start_and_finish_stages(tmp_path: Path) -> None:
    """The page distinguishes ongoing Git checkouts and completed ones, without file chunks."""
    observed = []
    report, state = _progress_tracker(
        _scope(), "attempt-4", lambda name, facts: observed.append((name, facts))
    )
    report("checkout-code", {"state": "running"})
    assert state["step"] == "checkout-code" and state["percent"] == 45
    report(
        "checkout-code",
        {"state": "done", "folder": "code", "files": 10, "bytes": 40},
    )
    assert state["steps"][-1]["state"] == "done"
    report("checkout-memory", {"state": "running"})
    assert state["step"] == "checkout-memory" and state["percent"] == 65
    report(
        "checkout-memory",
        {"state": "done", "folder": "memory", "files": 4, "bytes": 20},
    )
    assert state["steps"][-1]["state"] == "done"
    assert [(name, facts["state"]) for name, facts in observed] == [
        ("checkout-code", "running"),
        ("checkout-code", "done"),
        ("checkout-memory", "running"),
        ("checkout-memory", "done"),
    ]
    assert all("done" not in facts and "total" not in facts for _, facts in observed)
    _BUILD_STATES.clear()
    _BUILD_CANCELS.clear()


def test_a_seat_id_the_host_already_holds_is_never_reused(tmp_path: Path) -> None:
    """A retry after a failed attempt creates a seat, not a refusal (MIK-R211 v2 rule 6).

    The host keeps every seat id it has seen, archived ones included, so an attempt whose seat the
    host already holds names the next id instead: the generation stays what it was, and one seat is
    made.
    """

    runtime, bound = _world(tmp_path)
    host = FakeHost()
    deps = _deps(runtime, host, bound, comparison=record(7))
    scope = _scope()
    # The host holds the id the first attempt for this request would name, as it does after an
    # attempt that created the seat and failed before using it.
    first = agent_id(leaf_workspace(runtime, REPO, TASK, LEAF), 1, _attempt_for("r1"))
    host.held_seats = {first}

    outcome = build_seat(deps, scope, request_id="r1", message="explain this")

    assert outcome.built and outcome.message_sent
    assert outcome.agent_id != first
    assert generation(generation_path(runtime, REPO, TASK, LEAF)) == 1
    assert host.created == [outcome.agent_id]


def test_a_new_request_does_not_name_the_seat_a_failed_attempt_spent(
    tmp_path: Path,
) -> None:
    """A Send after a failed attempt is a new request, so its seat id is a new one (rule 6)."""

    runtime, bound = _world(tmp_path)
    host = FakeHost()
    deps = _deps(runtime, host, bound, comparison=record(7))
    scope = _scope()
    # The host still holds the id the request "r1" named, as it does after that attempt made the
    # seat and failed before using it.
    spent = agent_id(leaf_workspace(runtime, REPO, TASK, LEAF), 1, _attempt_for("r1"))
    host.held_seats = {spent}

    outcome = build_seat(deps, scope, request_id="r2", message="explain this again")

    assert outcome.built and outcome.message_sent
    assert outcome.agent_id != spent
    assert host.created == [outcome.agent_id]


def test_a_send_builds_one_environment_and_one_seat_after_the_lock(
    tmp_path: Path,
) -> None:
    """MIK-R211 v5 rules 3 and 12: one build, one seat, and a workspace with its documents in it.

    The task's reference notes, the kit, and every published revision are copied in as plain
    writable folders, the draft starts from the latest revision, the candidate trees are plain
    checkouts, and nothing of the leaf lies beside its folder: the counter is with the task.
    """

    runtime, bound = _world(tmp_path)
    comparison = record(7)
    host = FakeHost()
    deps = _deps(runtime, host, bound, comparison=comparison)
    scope = _scope()
    host.lock_probe = lock_path(runtime, REPO, TASK, LEAF)
    task = tmp_path / "tasks" / REPO / TASK
    (task / "notes" / "reference" / LEAF).mkdir(parents=True)
    (task / "notes" / "reference" / LEAF / "agreed.md").write_text("agreed\n")
    revisions = (
        task / "notes" / "reports" / "review-frames" / LEAF.lower() / "explainer" / "revisions"
    )
    for number in (1, 2):
        (revisions / str(number) / "assets").mkdir(parents=True)
        (revisions / str(number) / "manifest.json").write_text(f'{{"revision": {number}}}')
        (revisions / str(number) / "assets" / "index.html").write_text(f"<p>{number}</p>")
        (revisions / str(number) / "assets" / "index.html").chmod(0o444)

    outcome = build_seat(deps, scope, request_id="r1", message="explain this")

    workspace = leaf_workspace(runtime, REPO, TASK, LEAF)
    assert outcome.built and outcome.message_sent
    assert host.created == [outcome.agent_id]
    assert [text for _agent, text in host.sent] == ["explain this"]
    assert host.send_while_locked == []
    # The seat is created with the capsule as its instructions, and no turn is started (rule 5).
    assert host.capsules == ["You are the explainer.\n"]
    stored = read_binding(workspace)
    assert stored is not None and stored.snapshot is None
    assert (workspace / "trees" / "code" / "src" / "app.py").read_text() == "x = 1\n"
    assert (workspace / "instructions" / "evidence.json").is_file()
    assert (workspace / "reference" / "agreed.md").read_text() == "agreed\n"
    assert (workspace / "kit" / "manifest.json").is_file()
    assert any((workspace / "kit").rglob("*.js"))
    for number in (1, 2):
        copied = workspace / "revisions" / str(number) / "assets" / "index.html"
        assert copied.read_text() == f"<p>{number}</p>"
        assert os.access(copied, os.W_OK)  # a plain writable copy, not the task's read-only file
    assert (workspace / "drafts" / "assets" / "index.html").read_text() == "<p>2</p>"
    assert not any(workspace.glob("snapshot-*")) and not (workspace / "drafts" / "host").exists()
    assert sorted(child.name for child in workspace.parent.iterdir()) == [LEAF]
    counter = generation_path(runtime, REPO, TASK, LEAF)
    assert counter.is_relative_to(task) and generation(counter) == 1


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


def test_a_later_send_reaches_the_live_seat_without_a_second_one(
    tmp_path: Path,
) -> None:
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


def test_a_build_after_master_retirement_or_an_archive_is_refused_by_name(tmp_path: Path) -> None:
    """A retired master builds nothing, and neither does a leaf whose task an archive reclaimed.

    R103 v2: the archive receipt names the explainer's reclaimed revisions and seat counter, and a
    reopen is refused with its reason, before any counter could start again at a seat id an
    archived chat still holds.
    """

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

    live = _deps(runtime, host, bound, comparison=comparison)
    first = build_seat(live, _scope(), request_id="r2", message="explain this")
    task = tmp_path / "tasks" / REPO / TASK
    cleanup_review_artifacts(
        ReviewArtifactCleanupRequest(
            task_root=task,
            task_name=TASK,
            code_repository=tmp_path / "repo",
            memory_repository=tmp_path / "memory",
            dry_run=False,
            explainer_root=explainer_task_folder(runtime.coordination_root, REPO, TASK),
        )
    )
    counter = generation_path(runtime, REPO, TASK, LEAF)
    assert not counter.exists()

    with pytest.raises(ExplainerRefused) as reopened:
        build_seat(live, _scope(), request_id="r3", message="explain again")

    assert reopened.value.reason == ARCHIVED
    assert "cannot be reopened" in reopened.value.detail and LEAF in reopened.value.detail
    assert host.created == [first.agent_id]
    assert not counter.exists() and not leaf_workspace(runtime, REPO, TASK, LEAF).exists()

    # A sprint archive moves the task under 0_archive/. The refusal names the loss in the product's
    # own words and where the receipt is filed, from the archive's naming rule alone: nothing
    # under 0_archive is opened, and the leaf's document there is not read.
    moved = tmp_path / "tasks" / REPO / "0_archive" / TASK
    moved.parent.mkdir()
    task.rename(moved)
    archived = replace(live, facts=explainer_facts(lambda _scope: moved))

    with (
        mock.patch.object(explainer_routes_module, "leaf_document", side_effect=AssertionError),
        pytest.raises(ExplainerRefused) as after_move,
    ):
        build_seat(archived, _scope(), request_id="r4", message="explain again")

    assert after_move.value.reason == ARCHIVED
    detail = after_move.value.detail
    assert "revisions" in detail and "seat counter" in detail and "does not restore" in detail
    assert (moved / "notes" / "reports" / "review-artifact-cleanup.json").as_posix() in detail
    assert host.created == [first.agent_id]
    assert not counter.exists() and not leaf_workspace(runtime, REPO, TASK, LEAF).exists()

    # The production resolver knows only tasks/<repo>/<master>, now absent. The refusal says that
    # plainly, then names the check a reader can make under 0_archive -- never making it itself.
    wired = explainer_composition(
        runtime,
        cast(Any, object()),
        wiring=ExplainerWiring(
            host=host,
            application=explainer_application_ports(),
            comparison_prepare=cast(Any, None),
        ),
    ).deps
    assert wired is not None
    with (
        _nothing_under(tmp_path / "tasks" / REPO / "0_archive"),
        pytest.raises(ExplainerRefused) as absent,
    ):
        build_seat(wired, _scope(), request_id="r5", message="explain again")

    place = tmp_path / "tasks" / REPO / TASK
    receipt = tmp_path / "tasks" / REPO / "0_archive" / TASK / "notes/reports"
    assert absent.value.detail.startswith(f"the task {REPO}/{TASK} has no directory at {place}; ")
    assert "; if this master was archived, the archive reclaimed" in absent.value.detail
    assert absent.value.detail.endswith(f"at {receipt / 'review-artifact-cleanup.json'}")
    assert host.created == [first.agent_id]
    assert not counter.exists() and not leaf_workspace(runtime, REPO, TASK, LEAF).exists()


@contextmanager
def _nothing_under(folder: Path) -> Iterator[None]:
    """Fail any stat, listing or open of a path below ``folder`` while the block runs."""

    prefix = os.fspath(folder)

    def guarded(real: Callable[..., Any]) -> Callable[..., Any]:
        def call(path: Any, *args: Any, **kwargs: Any) -> Any:
            if isinstance(path, (str, os.PathLike)) and os.fspath(path).startswith(prefix):
                raise AssertionError(f"{path} lies under {folder} and was touched")
            return real(path, *args, **kwargs)

        return call

    with (
        mock.patch("os.stat", guarded(os.stat)),
        mock.patch("os.lstat", guarded(os.lstat)),
        mock.patch("os.scandir", guarded(os.scandir)),
        mock.patch("os.listdir", guarded(os.listdir)),
        mock.patch("builtins.open", guarded(open)),
    ):
        yield


def test_an_unchanged_update_answers_current(tmp_path: Path) -> None:
    runtime, bound = _world(tmp_path)
    comparison = record(7)
    host = FakeHost()
    deps = _deps(runtime, host, bound, comparison=comparison)
    scope = _scope()
    build_seat(deps, scope, request_id="r1", message="explain this")

    update = update_seat(deps, scope)

    assert update.current and update.code is None and update.memory is None


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
            str(leaf_lock_resource(workspace)),
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


def test_an_update_after_a_removal_is_refused_as_missing(tmp_path: Path) -> None:
    runtime, bound = _world(tmp_path)
    comparison = record(7)
    host = FakeHost()
    deps = _deps(runtime, host, bound, comparison=comparison)
    scope = _scope()
    build_seat(deps, scope, request_id="r1", message="explain this")
    retire_seat(deps, scope, receipt_id="retire-1")

    with pytest.raises(ExplainerRefused) as refused:
        update_seat(deps, scope)

    assert refused.value.reason == MISSING
    assert not leaf_workspace(runtime, REPO, TASK, LEAF).exists()


def test_a_removal_in_any_seat_state_leaves_nothing_and_a_reopen_takes_a_new_seat(
    tmp_path: Path,
) -> None:
    """MIK-R211 v5 rule 12 (evidence 17): a removal leaves nothing under the explainer folder.

    For a live, closed, archived and missing seat alike, the chat is archived, both Git worktrees
    are removed and pruned, and the leaf's folder is deleted, while the task's published revisions,
    grades and receipt stay. The seat counter stays with the task, so a reopen takes a new seat id.
    The reopen copies the revisions from the task, never from a workspace copy the seat changed.
    """

    for state in ("live", "closed", "archived", "missing"):
        root = tmp_path / state
        runtime, bound = _world(root)
        host = FakeHost()
        deps = _deps(runtime, host, bound, comparison=record(7))
        scope = _scope()
        task = root / "tasks" / REPO / TASK
        revision = task / "notes/reports/review-frames" / slugify(LEAF) / "explainer/revisions/1"
        revision.mkdir(parents=True)
        (revision / "index.html").write_text("<p>revision 1</p>\n")
        grades = GradeStore(grades_path(task, LEAF), LEAF)
        grades.define(
            "risk", (ScalePoint(grade="high", meaning="h"), ScalePoint(grade="low", meaning="l"))
        )
        first = build_seat(deps, scope, request_id="r1", message="explain this")
        kept = {path: path.read_bytes() for path in (revision / "index.html", grades.path)}
        (first.workspace / "revisions/1/index.html").write_text("<p>an edited copy</p>\n")
        if state == "missing":
            host.states.pop(first.agent_id)
        else:
            host.states[first.agent_id] = state

        outcome = retire_seat(deps, scope, receipt_id="retire-1")

        assert outcome.receipt.state == "finished", state
        stored = read_receipt(retire_receipts_root(task, LEAF), "retire-1")
        assert stored is not None and stored.agent_id == first.agent_id
        assert list(leaf_workspace(runtime, REPO, TASK, LEAF).parent.iterdir()) == [], state
        for repository in (root / "repo", root / "memory"):
            listed = _git(repository, "worktree", "list", "--porcelain")
            assert listed.count("worktree ") == 1, (state, listed)
        assert {path: path.read_bytes() for path in kept} == kept
        assert generation(generation_path(runtime, REPO, TASK, LEAF)) == 1

        reopened = build_seat(deps, scope, request_id="r2", message="explain again")

        assert reopened.built and reopened.generation == 2
        assert reopened.agent_id != first.agent_id
        copied = reopened.workspace / "revisions/1/index.html"
        assert copied.read_bytes() == kept[revision / "index.html"]


def test_the_grade_writer_and_the_build_hold_one_lock_path(tmp_path: Path) -> None:
    """MIK-R187 rule 6: a grade write and a build of one leaf exclude each other.

    HEAD-era defect: the grade writer locked ``<task>/<leaf>`` while the service locked
    ``<leaf>.lock`` beside the workspace, so the two never shared a file.
    """

    runtime, bound = _world(tmp_path)
    deps = _deps(runtime, FakeHost(), bound)
    scope = _scope()
    build_seat(deps, scope, request_id="r1", message="explain this")
    workspace = leaf_workspace(runtime, REPO, TASK, LEAF)
    judgment = ExplainerJudgment(
        workspace=workspace,
        task_root=tmp_path / "tasks" / REPO / TASK,
        seat=None,
        fingerprints=None,
        now=lambda: NOW,
    )

    with (
        mock.patch.object(
            explainer_service, "exclusive_file_lock", wraps=explainer_service.exclusive_file_lock
        ) as build_lock,
        explainer_service._lock(deps, scope, wait=True),
    ):
        pass
    with (
        mock.patch.object(
            explainer_server, "exclusive_file_lock", wraps=explainer_server.exclusive_file_lock
        ) as grade_lock,
        judgment._leaf_lock(),
    ):
        pass

    held = {lock_path_for(call.args[0]) for call in (build_lock.call_args, grade_lock.call_args)}
    assert held == {workspace / "state" / "leaf.lock"}


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
    assert not gone.present and gone.seat == "missing" and gone.receipt is None


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


def test_an_empty_send_asks_for_the_end_to_end_review(tmp_path: Path) -> None:
    """The developer's F1: "leave it empty for the end-to-end review" must Send, and ask for it."""

    runtime, bound = _world(tmp_path)
    host = FakeHost()
    client = _client(ExplainerComposition(deps=_deps(runtime, host, bound, comparison=record(7))))

    answer = client.post(_url("build"), json={"requestId": "r-empty", "message": ""})

    assert answer.status_code == 200, answer.text
    assert answer.json()["seat"]["messageSent"]
    # The seat's first turn is the composed ask, not an empty string.
    assert host.sent[0][1] == END_TO_END_REQUEST
    assert "end to end" in END_TO_END_REQUEST


def test_a_send_with_no_message_field_at_all_also_asks_for_the_end_to_end_review(
    tmp_path: Path,
) -> None:
    runtime, bound = _world(tmp_path)
    host = FakeHost()
    client = _client(ExplainerComposition(deps=_deps(runtime, host, bound, comparison=record(7))))

    answer = client.post(_url("build"), json={"requestId": "r-bare"})

    assert answer.status_code == 200, answer.text
    assert host.sent[0][1] == END_TO_END_REQUEST


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


def test_failed_create_keeps_evidence_and_lost_reply_reconciles_same_seat(
    tmp_path: Path,
) -> None:
    """A failed create keeps its inputs, not its binding; a lost reply reconciles the same seat."""

    runtime, bound = _world(tmp_path)
    comparison = record(7)
    host = FakeHost()
    scope = _scope()
    workspace = leaf_workspace(runtime, REPO, TASK, LEAF)
    gen_file = generation_path(runtime, REPO, TASK, LEAF)

    # A create refusal keeps the completed workspace for inspection but rolls back admission.
    orig_create = host.create_seat

    def refuse_create(**_kwargs):
        raise ExplainerRefused("explainer-seat-config-unapplied", "wrong model")

    host.create_seat = refuse_create  # type: ignore[method-assign]
    deps = _deps(runtime, host, bound, comparison=comparison)
    with pytest.raises(ExplainerRefused):
        build_seat(deps, scope, request_id="r-fail", message="explain")
    assert workspace.is_dir()
    assert read_binding(workspace) is None
    assert generation(gen_file) == 0
    for side, expected_tree in (
        ("code", bound.binding.code_candidate_tree),
        ("memory", bound.binding.memory_candidate_tree),
    ):
        checkout = workspace / "trees" / side
        assert _git(checkout, "rev-parse", "HEAD^{tree}") == expected_tree
        assert (checkout / ".git").is_file()
        assert _git(checkout, "branch", "--show-current") == ""
    assert not list(workspace.glob("snapshot-*"))
    assert (workspace / "instructions" / "evidence.json").is_file()
    assert host.created == []

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
    assert reconciled.agent_id == seat_for_workspace(workspace, 1)
    assert read_binding(workspace) is not None
    for side in ("code", "memory"):
        checkout = workspace / "trees" / side
        registrations = _git(checkout, "worktree", "list", "--porcelain")
        assert registrations.count("worktree " + str(checkout) + "\n") == 1


@pytest.mark.parametrize(
    "fault_step",
    ["comparison", "prepare", "build"],
    ids=["comparison", "prepare", "build"],
)
def test_a_build_faulted_before_the_seat_rolls_back_every_effect(
    tmp_path: Path, fault_step: str
) -> None:
    """S09: each pre-seat step faulted leaves zero partials: no environment, no binding, no seat.

    The leaf's state lives inside its folder (MIK-R211 v5 rule 12), so what stays is its lock and
    the failure the page reads, in ``state/``.
    """

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
    left = {path.relative_to(workspace).as_posix() for path in workspace.rglob("*")}
    # A rolled-back build removed the folder with its lock file; the failure record is written after.
    assert left - {"state/leaf.lock"} == {"state", "state/requests.json"}
    assert read_binding(workspace) is None
    assert generation(gen_file) == 0
    # The removal deletes that state too: nothing is left under the explainer folder.
    retire_seat(deps, scope, receipt_id="retire-fault")
    assert list(workspace.parent.iterdir()) == []


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
    seat2 = seat_for_workspace(workspace, 2)
    host.turns.add(seat2)
    with pytest.raises(ExplainerRefused) as in_turn_err:
        update_seat(deps, scope)
    assert in_turn_err.value.reason == IN_TURN
    host.turns.clear()

    # Replacement branch checks archive exception and ends marked processes before raising generation.
    host.states[seat2] = "missing"
    child = subprocess.Popen(
        ["sleep", "300"],
        env={
            **os.environ,
            "AR_EXPLAINER_WORKSPACE": str(workspace),
            "PASEO_AGENT_ID": seat2,
        },
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
    state_fn = getattr(maintenance, "seat_state", seat_state)
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


def test_state_reports_the_native_build_progress_before_the_seat_exists(
    tmp_path: Path,
) -> None:
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


def test_a_comparison_that_moves_during_the_build_wins_over_the_packet(
    tmp_path: Path,
) -> None:
    """E03: the recheck after the environment write refuses a move instead of binding stale sides."""

    runtime, bound = _world(tmp_path)
    moved = replace(
        bound,
        binding=bound.binding.model_copy(update={"code_candidate_tree": "f" * 40}),
    )
    host = FakeHost()
    deps = replace(
        _deps(runtime, host, bound, comparison=record(7)),
        comparison=lambda _scope, _facts: bound,
        recheck=lambda _scope, _facts, _bound: _bound.binding.same_comparison(moved.binding),
    )
    client = _client(ExplainerComposition(deps=deps))

    answer = client.post(_url("build"), json={"requestId": "r1", "message": "explain this"})

    assert answer.status_code == 409
    assert answer.json()["reason"] == "binding-moved"
    assert host.created == []


def test_build_uses_one_preparation_and_the_native_final_witness(tmp_path: Path) -> None:
    runtime, bound = _world(tmp_path)
    host = FakeHost()
    calls = []

    def comparison(_scope, _facts):
        calls.append("comparison")
        return bound

    def recheck(_scope, _facts, candidate):
        assert candidate is bound
        calls.append("recheck")
        return True

    deps = replace(
        _deps(runtime, host, bound, comparison=record(7)), comparison=comparison, recheck=recheck
    )
    answer = build_seat(deps, _scope(), request_id="prepared-once", message="explain")
    assert answer.built
    assert calls == ["comparison", "recheck"]
    assert len(host.created) == 1
