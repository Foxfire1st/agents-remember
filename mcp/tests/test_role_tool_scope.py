"""The role-scoped task-server surface: one static allowlist per role, applied at the server.

Each case protects one part of the contract:

* the allowlist table itself -- ordered public subsequences, one union, alias, unknown role;
* the bound server -- exact advertised order per role, surviving read-only marks, and the
  read-only classification still running over a tool the role excludes;
* the binding precedence -- unbound, explainer-only, valid mixed, malformed, malformed mixed,
  including dispatch of a pruned explainer tool through the unknown-tool path;
* the Architect compact-schema size, measured on the same build beside the unbound server;
* a real MCP stdio session for a bound Worker and one for a malformed binding, so discovery,
  dispatch and the raised-call log are the protocol's own answers.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.agent_binding import TOOL_SERVER_NAME, AgentBinding
from agents_remember.cli.paseo_launch import tool_server_definition
from agents_remember.cli.paseo_seat_policy import preapproval_policy
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.mcp import server as server_module
from agents_remember.mcp.read_only import UnknownReadOnlyMark, read_only_hint
from agents_remember.mcp.registration.explainer_surface import EXPLAINER_BINDING_ENV
from agents_remember.mcp.server import create_server
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.models.tools.role_tool_scope import (
    ROLE_TOOL_ALLOWLISTS,
    role_tool_roster,
    tools_for_role,
)
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp.exceptions import ToolError

from mcp import ClientSession, StdioServerParameters

REPO = "agents-remember"
TASK = "261124_cap"
LEAF = "261124-CAP-L1"
ROLES = (
    "architect",
    "orchestrator",
    "manager",
    "worker",
    "curator",
    "reviewer",
    "investigator",
    "bootstrap",
)
# The explainer's four, in the order registration adds them.
EXPLAINER_ORDER = ("render", "define_category", "remove_category", "grade")
# Which task references each role's launch carries: sprint, master, task.
REFERENCE_SHAPES = {
    "architect": (False, False, False),
    "orchestrator": (True, False, False),
    "manager": (True, True, False),
    "worker": (True, True, True),
    "curator": (True, True, True),
    "reviewer": (True, True, True),
    "investigator": (True, True, False),
    "bootstrap": (False, False, False),
}


@pytest.fixture(autouse=True)
def _no_inherited_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    """No launch binding is inherited from whoever runs the tests."""

    for key in list(os.environ):
        if key.startswith("AR_"):
            monkeypatch.delenv(key, raising=False)


def _world(root: Path) -> tuple[McpRuntimeConfig, Path]:
    """A coordination root with one repository and one explainer leaf environment."""

    (root / "tasks" / REPO / TASK).mkdir(parents=True, exist_ok=True)
    (root / "repo").mkdir(exist_ok=True)
    (root / "memory").mkdir(exist_ok=True)
    workspace = root / "explainer" / REPO / TASK / LEAF
    workspace.mkdir(parents=True)
    config = McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root / "workspace",
        transcript_root=root / "transcripts",
        repositories={
            REPO: RepositoryScope(repo_id=REPO, path=root / "repo", memory_root=root / "memory")
        },
    )
    return config, workspace


def _binding(role: str, *, agent_id: str = "3b7c1d2e-5f4a-4c6b-9d8e-0a1b2c3d4e5f") -> AgentBinding:
    """One launch binding of ``role``, built by the product's own type."""

    sprint, master, task = REFERENCE_SHAPES[role]
    return AgentBinding(
        agent_id=agent_id,
        role=role,
        request_id="9f8e7d6c-5b4a-4392-8170-6f5e4d3c2b1a",
        report_path="/tmp/seat-report.md",
        sprint_ref=TaskDocumentRef(repository=REPO, path=f"{TASK}/task.json") if sprint else None,
        master_ref=TaskDocumentRef(repository=REPO, path="task.json") if master else None,
        task_ref=TaskDocumentRef(repository=REPO, path=f"{TASK}/{LEAF}.json") if task else None,
    )


def _bind(monkeypatch: pytest.MonkeyPatch, binding: AgentBinding) -> None:
    for key, value in binding.environment().items():
        monkeypatch.setenv(key, value)


def _explainer_binding(workspace: Path) -> str:
    return json.dumps(
        {
            "capability": "explainer",
            "agent": "ca6f591f-1b27-4554-a530-0cca576b7529",
            "request": "req-1",
            "repository": REPO,
            "task": TASK,
            "leaf": LEAF,
            "workspace": str(workspace),
            "generation": 1,
            "seat": "ca6f591f-1b27-4554-a530-0cca576b7529",
        }
    )


def _tools(server: Any) -> list[Any]:
    return asyncio.run(server.list_tools())


def _advertised(server: Any) -> tuple[str, ...]:
    return tuple(tool.name for tool in _tools(server))


def _compact_bytes(tools: list[Any]) -> int:
    """The measured metric: compact UTF-8 JSON of every advertised tool, as the wire serializes it."""

    return sum(
        len(
            json.dumps(
                tool.model_dump(mode="json", by_alias=True, exclude_none=True),
                separators=(",", ":"),
            ).encode("utf-8")
        )
        for tool in tools
    )


def test_every_allowlist_is_an_ordered_public_subsequence_and_the_union_is_the_roster() -> None:
    for role, allowlist in ROLE_TOOL_ALLOWLISTS.items():
        assert allowlist, f"{role} declares no tool"
        assert len(set(allowlist)) == len(allowlist), f"{role} names a tool twice"
        assert set(allowlist) <= set(PUBLIC_TOOLS), f"{role} names a tool outside the roster"
        ordered = tuple(name for name in PUBLIC_TOOLS if name in allowlist)
        assert allowlist == ordered, f"{role} is not in PUBLIC_TOOLS order"

    union: set[str] = set()
    for allowlist in ROLE_TOOL_ALLOWLISTS.values():
        union.update(allowlist)
    assert union == set(PUBLIC_TOOLS)
    assert set(ROLE_TOOL_ALLOWLISTS) == set(ROLES)

    # Manager keeps the tools the curated lifecycle depends on beside each other in wire order.
    manager = ROLE_TOOL_ALLOWLISTS["manager"]
    start = manager.index("task_doc")
    assert manager[start : start + 5] == (
        "task_doc",
        "curator_coherence",
        "closeout_queue",
        "role_start",
        "role_start_set",
    )

    # The supported earlier spelling resolves to the one Investigator entry.
    assert tools_for_role("system-specialist") == tools_for_role("investigator")
    assert role_tool_roster(None) == PUBLIC_TOOLS
    assert role_tool_roster("worker") == tools_for_role("worker")
    with pytest.raises(ValueError, match="no canonical tool allowlist"):
        tools_for_role("not-a-role")


@pytest.mark.parametrize("role", [*ROLES, "system-specialist"])
def test_a_bound_server_advertises_only_its_roles_tools_in_public_order(
    tmp_path: Path, role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _workspace = _world(tmp_path)
    canonical = "investigator" if role == "system-specialist" else role
    # The alias spelling is what the launch is given; the allowlist answers the canonical role.
    _bind(monkeypatch, _binding(canonical))
    monkeypatch.setenv("AR_ROLE", role)

    server = create_server(config)
    tools = _tools(server)

    assert tuple(tool.name for tool in tools) == tools_for_role(canonical)
    for tool in tools:
        assert tool.annotations is not None, f"{tool.name} is advertised unmarked"
        assert tool.annotations.readOnlyHint is read_only_hint(tool.name)


def test_an_unbound_server_keeps_the_whole_public_roster(tmp_path: Path) -> None:
    config, _workspace = _world(tmp_path)
    assert _advertised(create_server(config)) == PUBLIC_TOOLS


def test_an_explainer_only_launch_keeps_the_public_roster_then_its_four_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, workspace = _world(tmp_path)
    monkeypatch.setenv(EXPLAINER_BINDING_ENV, _explainer_binding(workspace))

    assert _advertised(create_server(config)) == (*PUBLIC_TOOLS, *EXPLAINER_ORDER)


def test_a_valid_mixed_launch_serves_the_roles_tools_then_the_explainers_four(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, workspace = _world(tmp_path)
    _bind(monkeypatch, _binding("worker"))
    monkeypatch.setenv(EXPLAINER_BINDING_ENV, _explainer_binding(workspace))

    assert _advertised(create_server(config)) == (*tools_for_role("worker"), *EXPLAINER_ORDER)


def test_a_malformed_mixed_launch_prunes_the_explainers_tools_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seat guard no longer answers for a tool the malformed binding removed."""

    config, workspace = _world(tmp_path)
    monkeypatch.setenv("AR_EXPLAINER_WORKSPACE", str(workspace))
    monkeypatch.setenv(EXPLAINER_BINDING_ENV, _explainer_binding(workspace))
    _bind(monkeypatch, _binding("worker", agent_id="not-a-minted-id"))

    server = create_server(config)
    assert _advertised(server) == ("server_info",)
    with pytest.raises(ToolError, match="Unknown tool: render"):
        asyncio.run(server.call_tool("render", {}))


def test_a_read_only_mark_still_validates_a_tool_the_role_excludes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The classification runs over the whole registered surface, before the role narrows it."""

    config, _workspace = _world(tmp_path)
    _bind(monkeypatch, _binding("worker"))

    def register_unclassified(server: Any, _config: McpRuntimeConfig) -> None:
        @server.tool()
        def unclassified_probe(value: str) -> dict[str, str]:
            """An unclassified tool: excluded for a Worker and still unmarked."""

            return {"value": value}

    monkeypatch.setattr(
        server_module, "TOOL_REGISTRARS", (*server_module.TOOL_REGISTRARS, register_unclassified)
    )

    with pytest.raises(UnknownReadOnlyMark, match="unclassified_probe"):
        create_server(config)


def test_the_architect_schema_budget_holds_on_the_bound_and_unbound_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bound Architect fits under the host's eager pack; the unbound server does not."""

    config, _workspace = _world(tmp_path)
    unbound = _compact_bytes(_tools(create_server(config)))

    _bind(monkeypatch, _binding("architect"))
    bound = _compact_bytes(_tools(create_server(config)))

    assert unbound == 72_769, f"the unbound roster measured {unbound} B"
    assert bound == 44_167, f"the bound Architect measured {bound} B"
    assert bound <= 45_000
    assert bound < unbound


def test_the_bound_grant_and_the_advertised_surface_are_the_same_ordered_tuple() -> None:
    """Codex grants from the same table the server narrows with, per role."""

    for role in ROLES:
        policy = preapproval_policy("codex", TOOL_SERVER_NAME, role=role)
        assert policy is not None
        assert tuple(grant["tool"] for grant in policy["preapproved"]) == tools_for_role(role)
    full = preapproval_policy("codex", TOOL_SERVER_NAME)
    assert full is not None
    assert tuple(grant["tool"] for grant in full["preapproved"]) == PUBLIC_TOOLS
    claude = preapproval_policy("claude", TOOL_SERVER_NAME, role="worker")
    assert claude is not None
    assert tuple(grant["tool"] for grant in claude["preapproved"]) == ("server_info",)


def _settings_world(root: Path) -> Path:
    """A real settings file and coordination root for a stdio child."""

    for folder in ("coordination", "projects", "transcripts"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    (root / "coordination" / "tasks" / REPO / TASK).mkdir(parents=True, exist_ok=True)
    (root / "coordination" / "repo").mkdir(exist_ok=True)
    (root / "coordination" / "memory").mkdir(exist_ok=True)
    settings = root / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "coordinationRoot": str(root / "coordination"),
                "workspaceRoot": str(root / "projects"),
                "transcriptRoot": str(root / "transcripts"),
                "repositories": {
                    REPO: {
                        "path": str(root / "coordination" / "repo"),
                        "memoryRoot": str(root / "coordination" / "memory"),
                    }
                },
                "providers": {},
            }
        ),
        encoding="utf-8",
    )
    return settings


def _stdio_server(root: Path, binding: AgentBinding) -> StdioServerParameters:
    definition = tool_server_definition(_settings_world(root), binding)
    assert definition["command"] == sys.executable
    return StdioServerParameters(
        command=definition["command"],
        args=definition["args"],
        env=definition["env"],
        cwd=root / "projects",
    )


def _raised_rows(root: Path) -> list[dict[str, Any]]:
    """Every raised-call row the child wrote, in order."""

    rows: list[dict[str, Any]] = []
    for path in sorted((root / "transcripts").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def test_a_real_stdio_session_advertises_and_dispatches_only_the_bound_roles_tools(
    tmp_path: Path,
) -> None:
    root = tmp_path / "world"
    server = _stdio_server(root, _binding("worker"))

    async def exercise() -> tuple[tuple[str, ...], bool, bool]:
        async with (
            stdio_client(server) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            before = await session.call_tool("role_start", {"role": "worker"})
            listed = await session.list_tools()
            after = await session.call_tool("role_start", {"role": "worker"})
            return (
                tuple(tool.name for tool in listed.tools),
                bool(before.isError),
                bool(after.isError),
            )

    names, before_error, after_error = asyncio.run(exercise())

    assert names == tools_for_role("worker")
    assert (before_error, after_error) == (True, True)
    raised = [row for row in _raised_rows(root) if row.get("tool") == "role_start"]
    assert len(raised) == 2, raised
    assert {row.get("code") for row in raised} == {"raised"}
    assert {row.get("ok") for row in raised} == {False}


def test_a_real_stdio_session_on_a_malformed_binding_serves_only_server_info(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bad-world"
    server = _stdio_server(root, _binding("worker", agent_id="not-a-minted-id"))

    async def exercise() -> tuple[Any, tuple[str, ...], Any, list[Any]]:
        async with (
            stdio_client(server) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            opened = await session.initialize()
            listed = await session.list_tools()
            info = await session.call_tool("server_info", {})
            siblings = [await session.call_tool(name, {}) for name in ("task_doc", "render")]
            return opened.instructions, tuple(tool.name for tool in listed.tools), info, siblings

    instructions, names, info, siblings = asyncio.run(exercise())

    assert instructions is None
    assert names == ("server_info",)
    assert info.isError is True
    assert any("AR_PASEO_AGENT_ID" in getattr(content, "text", "") for content in info.content)
    for sibling in siblings:
        assert sibling.isError is True
        assert any("Unknown tool" in getattr(content, "text", "") for content in sibling.content)
