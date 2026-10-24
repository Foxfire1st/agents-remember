"""EG01 (ruling 7884d154 rule 3a): one in-process end-to-end run of the explainer.

One integration-marked test composes the real server routes, the real explainer host and the real
stores, with a stub harness in place of the model: open the review page's explainer for a task,
request an explanation, record a grade, retire the seat. What the developer would see is asserted
at each step. No sandbox, browser, Paseo host or hosted seat.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.review_planned_comparison import explainer_application_ports
from agents_remember.application.review_tree_knowledge import read_review_trees
from agents_remember.canvas.explainer_grades import grades_path
from agents_remember.canvas.explainer_inputs import ExplainerInputs
from agents_remember.canvas.explainer_paths import leaf_workspace
from agents_remember.cli import paseo_explainer_host as host_module
from agents_remember.cli.paseo_explainer_host import PaseoExplainerHost
from agents_remember.kernel.primitives.runtime_config import RepositoryScope
from agents_remember.mcp.registration.explainer_tools import register_bound_explainer_tools
from agents_remember.serving.explainer import (
    ExplainerWiring,
    explainer_composition,
    register_explainer_routes,
)
from agents_remember_test_support.testing.review_git_trees import (
    CODE_FILE,
    INVARIANT,
    LEAF,
    MASTER,
    build_world,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.fastmcp import FastMCP

pytestmark = pytest.mark.integration

REPOSITORY = "agents-remember"
BASE = f"/api/explainer/{REPOSITORY}/{MASTER}/{LEAF}"

SCALE = [
    {"grade": "sound", "meaning": "the claim holds against the recorded evidence"},
    {"grade": "thin", "meaning": "the claim rests on less than it says"},
]


def _fake_bridge(agents: dict[str, dict[str, Any]], calls: list[tuple[str, dict]]):
    """The stub harness in place of the model: a fake daemon behind the real bridge seam."""

    def bridge(_config, command, payload):
        calls.append((command, dict(payload)))
        if command == "workspace-open":
            return {"workspace": {"id": "w1"}}
        if command == "agent-create":
            agent_id = str(payload["agentId"])
            agents[agent_id] = {"status": "idle", "turnActive": False, "archivedAt": None}
            return {
                "workspace": {"id": "w1"},
                "agent": {
                    "id": agent_id,
                    "provider": "claude",
                    "model": payload.get("model"),
                    "thinkingOptionId": payload.get("thinkingOptionId"),
                    "modeId": payload.get("modeId"),
                },
            }
        if command == "agent-send":
            return {"delivery": {"delivered": True}}
        if command == "agent-state":
            agent = agents.get(str(payload["agentId"]))
            reply: dict[str, Any] = (
                {"agent": {"id": str(payload["agentId"]), **agent}}
                if agent is not None
                else {"agent": None}
            )
            return reply
        if command == "agent-archive":
            return _archive(agents, str(payload["agentId"]))
        return {"present": False} if command == "agent-has-message" else {}

    return bridge


def _archive(agents: dict[str, dict[str, Any]], agent_id: str) -> dict[str, Any]:
    if agent_id not in agents:
        return {"found": False, "archived": False}
    agents[agent_id] = {
        "status": "archived",
        "turnActive": False,
        "archivedAt": "2026-10-24T00:00:00Z",
    }
    return {
        "found": True,
        "archived": True,
        "alreadyArchived": False,
        "archivedAt": "2026-10-24T00:00:00Z",
    }


def _world(tmp_path: Path) -> Any:
    world = build_world(tmp_path)
    world.config = replace(
        world.config,
        repositories={
            REPOSITORY: RepositoryScope(
                repo_id=REPOSITORY, path=world.code, memory_root=world.memory
            )
        },
    )
    world.contract()
    (world.task_root / f"{LEAF}.json").write_text(
        json.dumps({"id": LEAF, "status": "inProgress"}), encoding="utf-8"
    )
    world.edit()
    return world


def _tool_answer(server: FastMCP, tool: str, arguments: dict[str, Any]) -> Any:
    """One mounted tool call, decoded to the JSON body the tool answered."""

    result = asyncio.run(server.call_tool(tool, dict(arguments)))
    if isinstance(result, tuple):
        result = result[0]
    blocks = result if isinstance(result, list) else getattr(result, "content", None)
    if isinstance(blocks, list):
        for block in blocks:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                return json.loads(text)
    return json.loads(str(result))


def _record_grade(world: Any, agent_id: str, binding_label: str) -> dict[str, Any]:
    """Step 3 of the chain: the seat's own real tool server grades one bound block."""

    workspace = leaf_workspace(world.config, REPOSITORY, MASTER, LEAF)
    server = FastMCP("review-explainer")
    register_bound_explainer_tools(
        server,
        workspace=workspace,
        seat=agent_id,
        inputs=ExplainerInputs(
            task_root=world.task_root,
            coordination_root=world.config.coordination_root,
            code_repository=world.code,
            memory_repository=world.memory,
        ),
    )
    defined = _tool_answer(
        server, "define_category", {"name": "risk", "scale": SCALE, "binding": binding_label}
    )
    assert defined["state"] == "defined"
    graded = _tool_answer(
        server,
        "grade",
        {
            "references": [f"inv:{INVARIANT}/code-candidate/{CODE_FILE}"],
            "category": "risk",
            "grade": "sound",
            "reason": "verified against the bound trees",
            "binding": binding_label,
        },
    )
    assert graded["state"] == "graded" and graded["graded"] == 1
    return graded


def test_the_explainer_end_to_end_open_request_grade_retire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole chain once, in process: open, request, grade, retire."""

    world = _world(tmp_path)
    agents: dict[str, dict[str, Any]] = {}
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(host_module, "bridge_call", _fake_bridge(agents, calls))
    monkeypatch.setattr(
        host_module,
        "qualified_launch",
        lambda _config, _repo: (
            "claude",
            {"model": "claude-opus-5-5", "effort": "high", "mode": "auto-review"},
        ),
    )
    host = PaseoExplainerHost(world.config)

    def port(query: Any) -> Any:
        return read_review_trees(world.config, query)

    composition = explainer_composition(
        world.config,
        port,
        wiring=ExplainerWiring(host=host, application=explainer_application_ports()),
    )
    app = FastAPI()
    register_explainer_routes(app, composition)
    client = TestClient(app)

    # 1. Open the review page's explainer for the task: an honest missing seat, nothing started.
    opened = client.get(f"{BASE}/state")
    assert opened.status_code == 200, opened.text
    first = opened.json()
    assert first["state"] == "ok"
    assert first["seat"]["state"] == "missing"
    assert first["seat"]["agentId"] is None and first["seat"]["generation"] is None
    assert all(command != "agent-create" for command, _payload in calls)

    # 2. Request an explanation: the build makes the environment and the seat, and the first
    # message is the request itself. The developer sees the seat, its generation and the binding.
    built = client.post(
        f"{BASE}/send", json={"requestId": "e2e-request", "message": "explain the change set"}
    )
    assert built.status_code == 200, built.text
    answer = built.json()
    assert answer["state"] == "ok"
    assert answer["generation"] == 1
    assert answer["seat"]["state"] == "live"
    agent_id = answer["agentId"]
    assert agent_id
    binding_label = answer["binding"]["label"]
    assert binding_label == "#1"
    assert [payload["text"] for command, payload in calls if command == "agent-send"] == [
        "[developer] explain the change set"
    ]

    live = client.get(f"{BASE}/state").json()
    assert live["seat"] == {"state": "live", "agentId": agent_id, "generation": 1}

    # 3. Record a grade: the seat's own real tool server grades one bound block, and the grade
    # reaches the store the page reads, with a fingerprint computed from the bound trees.
    _record_grade(world, agent_id, binding_label)
    stored = json.loads(grades_path(world.task_root, LEAF).read_text(encoding="utf-8"))
    rows = stored["grades"]
    assert len(rows) == 1 and rows[0]["category"] == "risk"
    after_grade = client.get(f"{BASE}/state").json()
    assert after_grade["seat"]["state"] == "live"
    assert after_grade["seat"]["agentId"] == agent_id

    # 4. Retire the seat: the receipt finishes, the workspace is removed, and the page shows it.
    retired = client.post(f"{BASE}/retire", json={"receiptId": "e2e-retire"})
    assert retired.status_code == 200, retired.text
    record = retired.json()["retire"]
    assert record["workspaceRemoved"] is True
    assert record["receipt"]["state"] == "finished"
    assert all(
        command != "agent-archive" or payload["agentId"] == agent_id for command, payload in calls
    )

    after_retire = client.get(f"{BASE}/state").json()
    assert after_retire["retired"] is True
    assert after_retire["seat"]["state"] == "retired"
    # The environment's live content is gone; only the retired marker remains in the folder.
    workspace = leaf_workspace(world.config, REPOSITORY, MASTER, LEAF)
    assert not (workspace / "binding.json").exists() and not (workspace / "instructions").exists()
