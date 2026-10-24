"""MIK-R190 rule 19: the tick's usage read crosses no layer, it is injected.

The finding this file guards (L184-R1-F14) was a new upward import: the serving lifespan
reaching into ``agents_remember.cli`` for the bridge's usage read. ``serving`` ranks below
``cli`` in ``layers.toml``, so that edge is a violation the base tree did not have. The
repair is the composition root's port: ``serving`` declares and calls it, and
``cli.dashboard`` binds the real one. These cases hold both halves to that.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from agents_remember.cli import role_held_messages as held
from agents_remember.cli.dashboard import serving_collaborators
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving import _app_lifespan as life
from agents_remember.serving._app_common import ServingCollaborators
from agents_remember_test_support.code_quality import layering

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "mcp" / "src" / "agents_remember"
LIFESPAN = SOURCE_ROOT / "serving" / "_app_lifespan.py"
# The edge the finding named, and the bridge's own caller: neither may be imported here.
FORBIDDEN = frozenset(
    {"agents_remember.cli.provider_limit_hold", "agents_remember.cli.paseo_bridge"}
)
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
AGENT = "11111111-1111-5111-8111-111111111111"


def test_the_serving_tick_imports_no_bridge_or_limit_owner() -> None:
    """No import statement anywhere in the lifespan names the limit owner (rule 19)."""

    imported = {module for _line, module, _names in layering.imports_of(LIFESPAN)}

    assert imported & FORBIDDEN == set()


def test_the_measured_layering_report_has_no_such_added_violation() -> None:
    """The measured graph agrees with the AST check, on the contract this tree declares."""

    contract = layering.load_contract(REPOSITORY_ROOT / "layers.toml")
    violations, _undeclared, _scanned, _edges = layering._collect_violations(SOURCE_ROOT, contract)

    in_lifespan = [
        statement
        for statement in violations
        if Path(statement.path) == LIFESPAN and statement.imported == "cli"
    ]
    assert [statement for statement in in_lifespan if statement.module in FORBIDDEN] == []
    # The repair adds no edge of its own: the lifespan's cli reach is still the one the base
    # tree already had (its warmup and archive-recovery callers), not one more.
    assert in_lifespan


def test_the_usage_read_is_a_port_the_composition_root_binds(tmp_path: Path) -> None:
    """``None`` on the port is a process with no usage read; the dashboard binds the real one."""

    assert ServingCollaborators().usage_read is None

    bound = serving_collaborators(_config(tmp_path))

    assert bound.usage_read is not None
    assert getattr(bound.usage_read, "__name__", "") == "read_usage_for_agents"


def test_the_tick_calls_the_port_with_the_agents_it_named(tmp_path: Path, monkeypatch) -> None:
    """The signal still reaches the port, with exactly the agents the publication stamped."""

    _inputs(tmp_path, {AGENT: {"state": "idle", "eventAt": NOW.isoformat()}})
    monkeypatch.setattr(held, "deliver_ended_turns", lambda *args: None)
    monkeypatch.setattr(held, "sweep_held_messages", lambda *args: None)
    seen: list[list[str]] = []

    life._held_delivery_tick(
        _runtime(tmp_path),
        {},
        {},
        sweep=False,
        collaborators=life._DeliveryCollaborators(
            usage_read=lambda _config, ids: seen.append(list(ids))
        ),
    )

    assert seen == [[AGENT]]


def test_the_tick_without_a_port_makes_no_usage_read(tmp_path: Path, monkeypatch) -> None:
    """A process the composition root gave no port keeps every other tick obligation."""

    _inputs(tmp_path, {AGENT: {"state": "ended", "eventAt": NOW.isoformat()}})
    drained: list[list[str]] = []
    monkeypatch.setattr(held, "deliver_ended_turns", lambda _config, ids: drained.append(list(ids)))
    monkeypatch.setattr(held, "sweep_held_messages", lambda *args: None)

    life._held_delivery_tick(_runtime(tmp_path), {}, {}, sweep=False)

    # The ended turn is still drained; a missing port costs only the usage read.
    assert drained == [[AGENT]]


def _inputs(tmp_path: Path, agents: dict[str, dict[str, Any]]) -> Path:
    """The two files the tick reads, for one publication and one registered seat each."""

    home = tmp_path / "home" / "agents-remember"
    home.mkdir(parents=True)
    (home / "role-agents.json").write_text(
        json.dumps(
            {
                "schema": "ar-role-agents/v1",
                "agents": {
                    agent: {
                        "role": "worker",
                        "task": "repo/master/leaf.json",
                        "reportPath": f"/tmp/{agent}.md",
                    }
                    for agent in agents
                },
            }
        ),
        encoding="utf-8",
    )
    (home / "host-activity.json").write_text(
        json.dumps(
            {
                "bootId": "boot-1",
                "complete": True,
                "agents": agents,
                "ignoredEvents": 0,
            }
        ),
        encoding="utf-8",
    )
    return home


def _runtime(tmp_path: Path) -> McpRuntimeConfig:
    """A stand-in config: these cases read only the Paseo runtime's home."""

    return cast(
        McpRuntimeConfig, SimpleNamespace(paseo_runtime=SimpleNamespace(home=tmp_path / "home"))
    )


def _config(tmp_path: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=tmp_path / "settings" / "mcp.json",
        coordination_root=tmp_path / "coordination",
        workspace_root=tmp_path / "projects",
        transcript_root=tmp_path / "coordination" / "logs" / "mcp",
    )
