"""F10: every current-seat consumer names the attempt the launch actually minted.

MIK-R211 v5 rule 12: the attempts lie in the workspace's ``state/`` and the generation counter
with the task's explainer records, so each world here has its task folder.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from agents_remember.canvas import explainer_binding as binding
from agents_remember.canvas.explainer_archive import LeafEnvironment
from agents_remember.canvas.explainer_paths import seat_counter_path
from agents_remember.canvas.explainer_state import (
    ExplainerRefused,
    agent_id,
    mint_seat_attempt,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving import explainer_watcher as watcher
from agents_remember.serving.explainer_watcher import WatcherApplicationPorts


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "explainer" / "repo" / "master" / "LEAF"
    workspace.mkdir(parents=True)
    (tmp_path / "tasks" / "repo" / "master").mkdir(parents=True)
    counter = seat_counter_path(workspace)
    counter.parent.mkdir(parents=True)
    counter.write_text("1\n")
    return workspace, counter


def _check(workspace: Path, seat: str, guard: str) -> str:
    if guard == "writer":
        return binding.require_current_seat(workspace, seat)
    return binding.require_launched_seat(
        cast(
            binding.LaunchedSeat,
            SimpleNamespace(workspace=workspace, agent=seat, generation=1, leaf=workspace.name),
        )
    )


@pytest.fixture(autouse=True)
def _no_host_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PASEO_AGENT_ID", raising=False)
    monkeypatch.delenv("AR_EXPLAINER_WORKSPACE", raising=False)


@pytest.mark.parametrize("guard", ["writer", "dispatch"])
def test_latest_recorded_attempt_is_the_current_seat(tmp_path: Path, guard: str) -> None:
    workspace, _counter = _workspace(tmp_path)
    mint_seat_attempt(workspace, "first-failed")
    mint_seat_attempt(workspace, "second-live")
    seat = agent_id(workspace, 1, "second-live")
    assert _check(workspace, seat, guard) == seat


@pytest.mark.parametrize("guard", ["writer", "dispatch"])
@pytest.mark.parametrize("attempt", ["", "first-failed", "unrecorded"])
def test_noncurrent_attempt_is_refused(tmp_path: Path, guard: str, attempt: str) -> None:
    workspace, _counter = _workspace(tmp_path)
    mint_seat_attempt(workspace, "first-failed")
    mint_seat_attempt(workspace, "second-live")
    with pytest.raises(ExplainerRefused) as refused:
        _check(workspace, agent_id(workspace, 1, attempt), guard)
    assert refused.value.reason == "not-this-explainer"


@pytest.mark.parametrize("guard", ["writer", "dispatch"])
def test_a_generation_without_attempts_keeps_its_native_identity(
    tmp_path: Path, guard: str
) -> None:
    workspace, _ = _workspace(tmp_path)
    seat = agent_id(workspace, 1)
    assert _check(workspace, seat, guard) == seat


@pytest.mark.parametrize("guard", ["writer", "dispatch"])
def test_generation_change_still_revokes_the_previous_seat(tmp_path: Path, guard: str) -> None:
    workspace, counter = _workspace(tmp_path)
    mint_seat_attempt(workspace, "live")
    seat = agent_id(workspace, 1, "live")
    counter.write_text("2\n")
    with pytest.raises(ExplainerRefused) as refused:
        _check(workspace, seat, guard)
    assert refused.value.reason == "not-this-explainer"


def test_dispatch_still_refuses_another_calling_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, _counter = _workspace(tmp_path)
    mint_seat_attempt(workspace, "live")
    monkeypatch.setenv("PASEO_AGENT_ID", "some-other-process")
    with pytest.raises(ExplainerRefused) as refused:
        _check(workspace, agent_id(workspace, 1, "live"), "dispatch")
    assert refused.value.reason == "not-this-explainer"


def test_archive_names_the_recorded_attempt(tmp_path: Path) -> None:
    workspace, _counter = _workspace(tmp_path)
    mint_seat_attempt(workspace, "live")
    environment = LeafEnvironment(workspace.parent, workspace.name, 1)
    assert environment.agent == agent_id(workspace, 1, "live")


def test_watcher_observes_the_recorded_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, _counter = _workspace(tmp_path)
    mint_seat_attempt(workspace, "live")
    monkeypatch.setattr(watcher, "explainer_roots", lambda _root: [workspace.parent])
    monkeypatch.setattr(watcher, "_receipt_leaf_seats", lambda *_args: {})
    seats = watcher.leaf_seats(
        cast(McpRuntimeConfig, SimpleNamespace(coordination_root=tmp_path)),
        cast(WatcherApplicationPorts, SimpleNamespace()),
    )
    assert agent_id(workspace, 1, "live") in seats
    assert agent_id(workspace, 1) not in seats
