"""MIK-R186 @ v2 rule 3 and MIK-R211 v5 rule 3: one server, bound at launch, with the explainer's four.

The binding is the launch's, written into the server process's environment. These cases hold what the
requirement asks for, in one process and without a seat:

* a bound server **advertises** AR's general tools, as a task agent has them, and exactly the explainer's
  four tools (`render`, `define_category`, `remove_category`, `grade`), and an ordinary server is unchanged;
* a bound server **refuses nothing for being the explainer**: AR's general tools answer the seat as they
  answer any agent;
* an invalid binding **refuses service** as `explainer-binding-invalid` instead of serving an unbound
  seat, so a forged or stale launch cannot become another leaf's explainer.

What these do not show, and must not be read as showing: that a real seat's launch writes this binding, or
that a harness attaches to it. That is the launch's own evidence.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
from agents_remember.canvas.explainer_paths import GENERATION_SUFFIX
from agents_remember.canvas.explainer_state import ExplainerRefused, agent_id
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.mcp.registration.explainer_surface import (
    EXPLAINER_BINDING_ENV,
    EXPLAINER_SURFACE,
    read_explainer_binding,
)
from agents_remember.mcp.server import create_server
from agents_remember.mcp.tools import PUBLIC_TOOLS

REPO = "agents-remember"
TASK = "260928_maintained-invariant-knowledge"
LEAF = "260928-MIK-L183"
SEAT = "ca6f591f-1b27-4554-a530-0cca576b7529"


def _world(tmp_path: Path) -> tuple[McpRuntimeConfig, Path]:
    """A coordination root with one repository and one explainer environment in it."""

    (tmp_path / "tasks" / REPO / TASK).mkdir(parents=True)
    (tmp_path / "repo").mkdir()
    (tmp_path / "memory").mkdir()
    workspace = tmp_path / "explainer" / REPO / TASK / LEAF
    workspace.mkdir(parents=True)
    config = McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path,
        workspace_root=tmp_path / "workspace",
        transcript_root=tmp_path / "transcripts",
        repositories={
            REPO: RepositoryScope(
                repo_id=REPO, path=tmp_path / "repo", memory_root=tmp_path / "memory"
            )
        },
    )
    return config, workspace


def _binding(workspace: Path, **overrides: object) -> dict[str, object]:
    given: dict[str, object] = {
        "capability": "explainer",
        "agent": SEAT,
        "request": "req-1",
        "repository": REPO,
        "task": TASK,
        "leaf": LEAF,
        "workspace": str(workspace),
        "generation": 1,
        "seat": SEAT,
    }
    given.update(overrides)
    return given


def _advertised(server: Any) -> tuple[str, ...]:
    return tuple(sorted(tool.name for tool in asyncio.run(server.list_tools())))


def test_a_bound_server_advertises_ars_general_tools_and_exactly_the_explainers_four(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Evidence 11: the registered explainer tools are exactly rule 3's four, beside AR's general tools."""

    config, workspace = _world(tmp_path)
    monkeypatch.setenv(EXPLAINER_BINDING_ENV, json.dumps(_binding(workspace)))

    server = create_server(config)

    assert EXPLAINER_SURFACE == ("define_category", "grade", "remove_category", "render")
    assert set(EXPLAINER_SURFACE).isdisjoint(PUBLIC_TOOLS)
    assert _advertised(server) == tuple(sorted({*PUBLIC_TOOLS, *EXPLAINER_SURFACE}))


def test_a_bound_server_refuses_nothing_for_being_the_explainer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MIK-R211 v5 rule 3: no confinement -- AR's general tools answer the seat as they answer anyone."""

    config, workspace = _world(tmp_path)
    monkeypatch.setenv(EXPLAINER_BINDING_ENV, json.dumps(_binding(workspace)))
    server = create_server(config)

    answer = _answer(server, "server_info")
    assert answer.get("refusal") not in {"explainer-tool-not-exposed", "not-this-explainer"}


@pytest.mark.parametrize(
    "broken",
    [
        pytest.param({"capability": "worker"}, id="not-an-explainer-capability"),
        pytest.param({"generation": 0}, id="generation-below-one"),
        pytest.param({"generation": "1"}, id="generation-as-text"),
        pytest.param({"leaf": "260928-OTHER"}, id="workspace-is-another-leaf"),
        pytest.param({"task": "260928_other"}, id="workspace-is-another-task"),
        pytest.param({"repository": "somebody-else"}, id="workspace-is-another-repository"),
        pytest.param({"agent": ""}, id="missing-agent"),
    ],
)
def test_an_invalid_binding_refuses_service_rather_than_the_ordinary_roster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, broken: dict[str, object]
) -> None:
    """A wrong binding is not the same as no binding, and it never widens to the ordinary roster."""

    config, workspace = _world(tmp_path)
    monkeypatch.setenv(EXPLAINER_BINDING_ENV, json.dumps(_binding(workspace, **broken)))

    with pytest.raises(ExplainerRefused) as refused:
        create_server(config)
    assert refused.value.reason == "explainer-binding-invalid"


def test_a_binding_that_is_not_json_refuses_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _workspace = _world(tmp_path)
    monkeypatch.setenv(EXPLAINER_BINDING_ENV, "{not json")

    with pytest.raises(ExplainerRefused) as refused:
        create_server(config)
    assert refused.value.reason == "explainer-binding-invalid"


def test_an_ordinary_server_keeps_the_full_roster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No binding is the ordinary case: every family registers, and the AR tools are advertised."""

    config, _workspace = _world(tmp_path)
    monkeypatch.delenv(EXPLAINER_BINDING_ENV, raising=False)

    server = create_server(config)

    advertised = _advertised(server)
    assert len(advertised) > len(EXPLAINER_SURFACE)
    assert "worktree_start" in advertised and "role_message" in advertised
    # The explainer's four are not advertised on an ordinary server: it is the architect's
    # `explainer_control` that belongs there, not the seat's own tools.
    assert not set(EXPLAINER_SURFACE) <= set(advertised)


def test_a_binding_without_a_config_still_validates_itself(tmp_path: Path) -> None:
    """The reader is usable where no config is at hand, and still refuses a self-inconsistent binding."""

    _config, workspace = _world(tmp_path)
    raw = json.dumps(_binding(workspace))
    previous = os.environ.get(EXPLAINER_BINDING_ENV)
    os.environ[EXPLAINER_BINDING_ENV] = raw
    try:
        binding = read_explainer_binding()
    finally:
        if previous is None:
            os.environ.pop(EXPLAINER_BINDING_ENV, None)
        else:
            os.environ[EXPLAINER_BINDING_ENV] = previous
    assert binding is not None and binding.leaf == LEAF and binding.generation == 1
    assert binding.label == f"{REPO}/{TASK}/{LEAF}"


def test_a_lost_binding_on_a_launched_explainer_seat_refuses_rather_than_taking_the_ordinary_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fail-open path: absence must not be what chooses the roster.

    An ordinary seat has neither the binding nor this capability's launch marker, and that absence is
    deliberate -- it is the ordinary branch. An explainer seat is launched with both, so a **missing**
    binding beside a present marker means the binding was lost, and a lost binding hands the seat the
    general roster without its binding if absence is allowed to decide. It refuses instead.
    """

    config, workspace = _world(tmp_path)
    monkeypatch.delenv(EXPLAINER_BINDING_ENV, raising=False)
    monkeypatch.setenv("AR_EXPLAINER_WORKSPACE", str(workspace))

    with pytest.raises(ExplainerRefused) as refused:
        read_explainer_binding(config)
    assert refused.value.reason == "explainer-binding-invalid"
    assert "missing" in refused.value.detail
    with pytest.raises(ExplainerRefused):
        create_server(config)


def test_an_empty_binding_is_not_a_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty is not the same as absent: a seat handed `""` was launched wrong, not launched ordinary."""

    config, _workspace = _world(tmp_path)
    monkeypatch.setenv(EXPLAINER_BINDING_ENV, "   ")

    with pytest.raises(ExplainerRefused) as refused:
        create_server(config)
    assert refused.value.reason == "explainer-binding-invalid"
    assert "empty" in refused.value.detail


def test_only_a_seat_with_neither_marker_nor_binding_takes_the_ordinary_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deliberate ordinary seat: no binding, no explainer marker, and the full roster as before."""

    config, _workspace = _world(tmp_path)
    monkeypatch.delenv(EXPLAINER_BINDING_ENV, raising=False)
    monkeypatch.delenv("AR_EXPLAINER_WORKSPACE", raising=False)

    server = create_server(config)

    advertised = _advertised(server)
    assert len(advertised) > len(EXPLAINER_SURFACE)
    assert "worktree_start" in advertised


# -- MIK-R186 @ v2 rule 3 sentences 3-4 and Expected Evidence 7 (a forged seat) -----------------------
#
# The launch writes the seat into two places: the binding in this process's environment, and the id the
# harness puts in PASEO_AGENT_ID for every process of that seat. These cases hold that each of the
# explainer's four tools -- not only the grade bodies -- answers a forged or replaced seat by name, and
# that the seat the server was launched for is served.


def _seated_world(tmp_path: Path) -> tuple[McpRuntimeConfig, Path, str]:
    """A world whose leaf has one seat: the generation counter the seat's id is derived from."""

    config, workspace = _world(tmp_path)
    seat = agent_id(workspace, 1)
    (workspace.parent / f"{LEAF}{GENERATION_SUFFIX}").write_text("1", encoding="utf-8")
    return config, workspace, seat


def _answer(server: Any, name: str) -> dict[str, Any]:
    """One tool call's answer as data: the transport hands a tool's dict back as its JSON text."""

    answer = asyncio.run(server.call_tool(name, {}))
    if isinstance(answer, tuple):  # (content blocks, structured body) on a dict return
        answer = answer[0]
    if isinstance(answer, list):
        assert len(answer) == 1, answer
        answer = json.loads(answer[0].text)
    assert isinstance(answer, dict)
    return answer


def test_every_tool_of_the_bound_server_answers_a_forged_seat_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caller is the seat the server was launched for, on each of the four and not only on grade."""

    config, workspace, seat = _seated_world(tmp_path)
    monkeypatch.setenv(
        EXPLAINER_BINDING_ENV, json.dumps(_binding(workspace, agent=seat, seat=seat))
    )
    monkeypatch.setenv("PASEO_AGENT_ID", "0f6a1f7c-2a4f-4a6c-9f8e-5d0b1c7e9a11")
    server = create_server(config)

    for name in EXPLAINER_SURFACE:
        answer = _answer(server, name)
        assert answer.get("refusal") == "not-this-explainer", name


def test_the_seat_the_server_was_launched_for_is_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same call the forged caller loses is answered, not refused, for the launched seat."""

    config, workspace, seat = _seated_world(tmp_path)
    monkeypatch.setenv(
        EXPLAINER_BINDING_ENV, json.dumps(_binding(workspace, agent=seat, seat=seat))
    )
    monkeypatch.setenv("PASEO_AGENT_ID", seat)
    server = create_server(config)

    answer = _answer(server, "render")

    assert answer.get("refusal") != "not-this-explainer"


def test_a_new_generation_refuses_the_old_servers_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resume makes a new seat; the server launched for the old one refuses instead of serving on."""

    config, workspace, seat = _seated_world(tmp_path)
    monkeypatch.setenv(
        EXPLAINER_BINDING_ENV, json.dumps(_binding(workspace, agent=seat, seat=seat))
    )
    monkeypatch.setenv("PASEO_AGENT_ID", seat)
    server = create_server(config)
    (workspace.parent / f"{LEAF}{GENERATION_SUFFIX}").write_text("2", encoding="utf-8")

    answer = _answer(server, "render")

    assert answer.get("refusal") == "not-this-explainer"
    assert "generation 2" in answer["detail"]


def test_a_marker_pointing_at_another_environment_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace marker cannot move the binding to another leaf's environment."""

    config, workspace, seat = _seated_world(tmp_path)
    monkeypatch.setenv(
        EXPLAINER_BINDING_ENV, json.dumps(_binding(workspace, agent=seat, seat=seat))
    )
    monkeypatch.setenv("PASEO_AGENT_ID", seat)
    somewhere_else = tmp_path / "explainer" / REPO / TASK / "260928-OTHER"
    somewhere_else.mkdir(parents=True)
    monkeypatch.setenv("AR_EXPLAINER_WORKSPACE", str(somewhere_else))
    server = create_server(config)

    answer = _answer(server, "render")

    assert answer.get("refusal") == "foreign-workspace"


def _server_stub() -> Any:
    """A stand-in FastMCP instance; the captured registration never touches it."""

    class _Stub:
        pass

    return _Stub()
