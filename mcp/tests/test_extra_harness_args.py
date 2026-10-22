"""MIK-R192: a role's settings can carry extra harness arguments, such as ``--chrome``.

The cases cover the settings family's three places and their layering, the shape and the refusals
that keep the user's file honest, the resolution of one start's arguments and their record, the
hand-over through the host's provider options, and the confined presenter's refusal.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, cast

import pytest
from _user_settings_guard import user_settings_path_for_write
from agents_remember.application.role_launch_context import RoleLaunchContext
from agents_remember.canvas import presenter_native
from agents_remember.canvas.presenter_host import HostIdentity
from agents_remember.canvas.presenter_native import NativePresenterHarness
from agents_remember.canvas.presenter_store import PresenterAttempt, PresenterRefused
from agents_remember.cli import paseo_bridge
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure
from agents_remember.cli.paseo_launch import (
    RoleLaunch,
    applied_to_agent,
    build_launch_call,
)
from agents_remember.cli.role_launch_defaults import extra_args_plan, extra_args_statement
from agents_remember.cli.role_step_up import select_step_up
from agents_remember.kernel._agentic_settings_core import (
    RESERVED_EXTRA_ARG_NAMES,
    RoleKnobs,
)
from agents_remember.kernel.agentic_settings import AgenticSettingsError, load_agentic_settings
from agents_remember.kernel.primitives.paseo_runtime_settings import (
    PaseoRuntimeSettingsError,
    parse_paseo_runtime_settings,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import RoleAgentOverride
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from canvas_host_test_support import config as presenter_config
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = REPO_ROOT / "docs" / "reference" / "settings-json.md"
REPO = "agents-remember"
CLAUDE = {"harness": "claude"}


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated user settings folder and coordination root for one case."""

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    (tmp_path / "coordination").mkdir()
    return tmp_path


def write_user(root: Path, orchestration: dict[str, Any]) -> Path:
    path = user_settings_path_for_write()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"orchestration": orchestration}), encoding="utf-8")
    return path


def write_repository(root: Path, orchestration: dict[str, Any]) -> Path:
    path = root / "repo" / "system" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"orchestration": orchestration}), encoding="utf-8")
    return path


def load(root: Path, *, repository: bool = False):
    repo = root / "repo" if repository else None
    return load_agentic_settings(root / "coordination", repo)


def stated(
    settings: Any, role: str, level: str = "leaf", provider: str = "claude"
) -> dict[str, Any] | None:
    return extra_args_plan(
        extra_args_statement(settings, settings.resolved_role_knobs(role, level), role),
        provider,
    )


def test_harness_role_and_per_level_entries_layer_key_by_key(root: Path) -> None:
    path = write_user(
        root,
        {
            "harnesses": {"claude": {"extraArgs": {"chrome": None, "name": "harness"}}},
            "roles": {
                "investigator": {
                    "harness": "claude",
                    "extraArgs": {"name": "role", "trace": "on"},
                }
            },
            "rolesPerLevel": {"portfolio": {"investigator": {"extraArgs": {"trace": False}}}},
        },
    )

    settings = load(root)
    knobs = settings.resolved_role_knobs("investigator", "portfolio")

    assert knobs.extra_args == {"name": "role", "trace": False}
    harness_entry = settings.harness_extra_args["claude"]["chrome"]
    assert harness_entry.value is None
    assert harness_entry.key == "orchestration.harnesses.claude.extraArgs.chrome"
    assert harness_entry.source == str(path)
    assert (
        knobs.origins["extraArgs.trace"].key
        == "orchestration.rolesPerLevel.portfolio.investigator.extraArgs.trace"
    )
    assert knobs.origins["extraArgs.name"].source == str(path)


def test_false_at_the_harness_entry_is_refused_because_nothing_lies_below_it(root: Path) -> None:
    path = write_user(root, {"harnesses": {"claude": {"extraArgs": {"chrome": False}}}})

    with pytest.raises(AgenticSettingsError) as refused:
        load(root)

    assert "orchestration.harnesses.claude.extraArgs.chrome" in str(refused.value)
    assert str(path) in str(refused.value)


@pytest.mark.parametrize(
    "entries",
    [
        {"-chrome": None},
        {"chr ome": None},
        {"a=b": None},
        {"": None},
        {"chrome": 3},
        {"chrome": True},
    ],
)
def test_a_malformed_name_or_value_is_refused(root: Path, entries: dict[str, Any]) -> None:
    path = write_user(root, {"harnesses": {"claude": {"extraArgs": entries}}})

    with pytest.raises(AgenticSettingsError) as refused:
        load(root)

    assert str(path) in str(refused.value)
    assert "orchestration.harnesses.claude.extraArgs" in str(refused.value)


@pytest.mark.parametrize("value", ["", " ", "   ", " a b "])
def test_a_string_value_crosses_exactly_as_written_empty_or_blank(root: Path, value: str) -> None:
    """MIK-R192 rule 1: the name must be non-empty; its value is carried as the user wrote it.

    A name is what the contract makes non-empty. The pinned host takes any string value, and the
    qualified presenter hands Claude ``tools: ''`` itself, so refusing ``""`` here would refuse a
    value the host accepts -- and the two create routes would disagree (L184-R1-F23).
    """

    write_user(root, {"harnesses": {"claude": {"extraArgs": {"chrome": value}}}})

    settings = load(root)

    assert settings.harness_extra_args["claude"]["chrome"].value == value


def test_extra_args_null_is_refused(root: Path) -> None:
    path = write_user(root, {"roles": {"investigator": {**CLAUDE, "extraArgs": None}}})

    with pytest.raises(AgenticSettingsError) as refused:
        load(root)

    assert "orchestration.roles.investigator.extraArgs" in str(refused.value)
    assert str(path) in str(refused.value)


@pytest.mark.parametrize("name", sorted(RESERVED_EXTRA_ARG_NAMES))
def test_every_reserved_name_is_refused_naming_the_key(root: Path, name: str) -> None:
    write_user(root, {"harnesses": {"claude": {"extraArgs": {name: None}}}})

    with pytest.raises(AgenticSettingsError) as refused:
        load(root)

    assert f"orchestration.harnesses.claude.extraArgs.{name}" in str(refused.value)


@pytest.mark.parametrize(
    "orchestration,key",
    [
        (
            {"roles": {"worker": {**CLAUDE, "extraArgs": {"chrome": None}}}},
            "roles.worker.extraArgs",
        ),
        (
            {"rolesPerLevel": {"leaf": {"worker": {**CLAUDE, "extraArgs": {"chrome": None}}}}},
            "rolesPerLevel.leaf.worker.extraArgs",
        ),
        (
            {"harnesses": {"claude": {"extraArgs": {"chrome": None}}}},
            "harnesses.claude.extraArgs",
        ),
    ],
)
def test_the_repository_layer_carries_no_extra_arguments(
    root: Path, orchestration: dict[str, Any], key: str
) -> None:
    path = write_repository(root, orchestration)

    with pytest.raises(AgenticSettingsError) as refused:
        load(root, repository=True)

    assert f"orchestration.{key}" in str(refused.value)
    assert str(path) in str(refused.value)


def test_a_step_up_row_carries_no_extra_arguments(root: Path) -> None:
    write_user(
        root,
        {
            "roles": {
                "worker": {
                    **CLAUDE,
                    "stepUp": {**CLAUDE, "model": "other", "extraArgs": {"chrome": None}},
                }
            }
        },
    )

    with pytest.raises(AgenticSettingsError) as refused:
        load(root)

    assert "orchestration.roles.worker.stepUp.extraArgs" in str(refused.value)


@pytest.mark.parametrize(
    "orchestration",
    [
        {"roles": {"review-presenter": {**CLAUDE, "extraArgs": {"chrome": None}}}},
        {
            "rolesPerLevel": {
                "leaf": {"review-presenter": {**CLAUDE, "extraArgs": {"chrome": None}}}
            }
        },
    ],
)
def test_the_confined_capability_carries_no_extra_arguments(
    root: Path, orchestration: dict[str, Any]
) -> None:
    write_user(root, orchestration)

    with pytest.raises(AgenticSettingsError) as refused:
        load(root)

    assert "review-presenter.extraArgs" in str(refused.value)
    assert "confined" in str(refused.value)


@pytest.mark.parametrize("harness", ["codex", "hermes", "pi"])
def test_a_harness_without_host_support_is_refused_by_name(root: Path, harness: str) -> None:
    write_user(root, {"harnesses": {harness: {"extraArgs": {"chrome": None}}}})

    with pytest.raises(AgenticSettingsError) as refused:
        load(root)

    assert f"orchestration.harnesses.{harness}.extraArgs" in str(refused.value)
    assert harness in str(refused.value)


@pytest.mark.parametrize("level", [None, "leaf"])
def test_a_role_row_for_a_harness_without_host_support_is_refused(
    root: Path, level: str | None
) -> None:
    row = {**CLAUDE, "harness": "codex", "extraArgs": {"chrome": None}}
    orchestration = (
        {"roles": {"investigator": row}}
        if level is None
        else {"rolesPerLevel": {level: {"investigator": row}}}
    )
    write_user(root, orchestration)

    with pytest.raises(AgenticSettingsError) as refused:
        load(root)

    owner = (
        "orchestration.roles.investigator"
        if level is None
        else f"orchestration.rolesPerLevel.{level}.investigator"
    )
    assert f"{owner}.extraArgs" in str(refused.value)
    assert "codex" in str(refused.value)


def test_the_old_launch_args_and_argv_stay_unapplied(root: Path) -> None:
    write_user(
        root,
        {
            "harnesses": {"claude": {"argv": ["--chrome"]}},
            "roles": {"worker": {"launchArgs": ["--chrome"]}},
        },
    )

    settings = load(root)

    assert settings.harness_extra_args == {}
    assert settings.role_knobs("worker").extra_args == {}
    assert "orchestration.roles.worker.launchArgs" in settings.unused_keys
    assert "orchestration.harnesses.claude.argv" in settings.unused_keys


def runtime_block(providers: dict[str, Any]) -> dict[str, Any]:
    return {
        "installPrefix": "/prefix",
        "home": "/home",
        "listen": "127.0.0.1:6831",
        "providers": providers,
        "embed": [],
    }


@pytest.mark.parametrize(
    "provider_id,entry",
    [
        ("claude", {"options": {"extraArgs": {"chrome": None}}}),
        ("claude", {"params": {"extraArgs": {"chrome": None}}}),
        ("my-claude", {"extends": "claude", "options": {"extraArgs": {"chrome": None}}}),
        ("grandchild", {"extends": "my-claude", "options": {"extraArgs": {"chrome": None}}}),
    ],
)
def test_the_hosts_provider_map_carries_no_extra_arguments_for_claude(
    provider_id: str, entry: dict[str, Any]
) -> None:
    providers = {"claude": {}, "my-claude": {"extends": "claude"}, provider_id: entry}

    with pytest.raises(PaseoRuntimeSettingsError) as refused:
        parse_paseo_runtime_settings(runtime_block(providers))

    message = str(refused.value)
    block = "params" if "params" in entry else "options"
    assert f"paseoRuntime.providers.{provider_id}.{block}.extraArgs" in message
    assert "orchestration.harnesses.claude.extraArgs" in message
    assert str(user_settings_path_for_write()) in message


def test_a_provider_that_does_not_extend_claude_may_state_extra_args() -> None:
    settings = parse_paseo_runtime_settings(
        runtime_block({"hermes": {"extends": "acp", "options": {"extraArgs": {"x": None}}}})
    )

    assert settings is not None
    assert settings.providers["hermes"]["options"]["extraArgs"] == {"x": None}


def test_a_role_entry_is_taken_on_the_roles_settings_harness(root: Path) -> None:
    write_user(
        root,
        {"roles": {"investigator": {**CLAUDE, "extraArgs": {"name": "investigator"}}}},
    )

    plan = stated(load(root), "investigator")

    assert plan is not None
    assert plan["entries"] == {"name": "investigator"}
    assert plan["arguments"][0]["source"] == "role entry"
    assert plan["arguments"][0]["key"] == "orchestration.roles.investigator.extraArgs.name"


def test_a_role_entry_on_a_per_level_row_is_a_per_level_entry(root: Path) -> None:
    write_user(
        root,
        {
            "roles": {"investigator": {**CLAUDE, "extraArgs": {"name": "role"}}},
            "rolesPerLevel": {"portfolio": {"investigator": {"extraArgs": {"name": "level"}}}},
        },
    )

    plan = stated(load(root), "investigator", "portfolio")

    assert plan is not None
    assert plan["entries"] == {"name": "level"}
    assert plan["arguments"][0]["source"] == "per-level entry"


def test_a_role_entry_is_not_taken_on_another_harness_and_says_so(root: Path) -> None:
    write_user(
        root,
        {"roles": {"investigator": {**CLAUDE, "extraArgs": {"name": "investigator"}}}},
    )

    plan = stated(load(root), "investigator", provider="codex")

    assert plan is not None
    assert plan["entries"] == {}
    assert plan["arguments"] == []
    assert [entry["name"] for entry in plan["notTaken"]] == ["name"]
    assert "claude" in plan["notTakenReason"]


def test_role_entries_are_not_taken_with_no_settings_harness(root: Path) -> None:
    write_user(root, {"roles": {"investigator": {"extraArgs": {"name": "investigator"}}}})

    plan = stated(load(root), "investigator")

    assert plan is not None
    assert plan["entries"] == {}
    assert plan["notTakenReason"] == (
        "no settings harness is stated, so the role's extra arguments are not taken"
    )


def test_a_role_false_removes_a_harness_entry_and_the_removal_is_recorded(root: Path) -> None:
    write_user(
        root,
        {
            "harnesses": {"claude": {"extraArgs": {"chrome": None, "name": "harness"}}},
            "roles": {"reviewer": {**CLAUDE, "extraArgs": {"chrome": False}}},
        },
    )

    plan = stated(load(root), "reviewer")

    assert plan is not None
    assert plan["entries"] == {"name": "harness"}
    assert [entry["name"] for entry in plan["arguments"]] == ["name"]
    assert [entry["name"] for entry in plan["removed"]] == ["chrome"]
    assert plan["removed"][0]["source"] == "role entry"


def test_the_hosts_configured_arguments_are_noted_and_never_taken(root: Path) -> None:
    statement = extra_args_statement(
        load(root),
        RoleKnobs(model="m"),
        "investigator",
    )
    statement["hostConfiguration"] = {"claude": ["chrome"]}

    plan = extra_args_plan(statement, "claude")

    assert plan is not None
    assert plan["entries"] == {}
    assert plan["hostConfiguration"] == [{"name": "chrome", "source": "host configuration"}]


def test_a_drifted_host_map_is_read_for_an_ordinary_start(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "agents_remember.serving.paseo.paseo_daemon_config.daemon_provider_extra_args",
        lambda config, provider, read_config=None: (),
    )
    config = host_config(root, {"claude": {"options": {"extraArgs": {"chrome": None}}}})
    settings = load(root)

    statement = extra_args_statement(
        settings,
        settings.resolved_role_knobs("investigator", "leaf"),
        "investigator",
        config=config,
    )

    assert statement["hostConfiguration"] == {"claude": ["chrome"]}
    assert statement["harness"] == {}
    plan = extra_args_plan(statement, "claude")
    assert plan is not None
    assert plan["hostConfiguration"] == [{"name": "chrome", "source": "host configuration"}]


def test_a_start_without_arguments_has_no_plan(root: Path) -> None:
    statement = extra_args_statement(load(root), RoleKnobs(model="m"), "investigator")

    assert extra_args_plan(statement, "claude") is None


def launch(
    session_options: dict[str, Any],
    *,
    provider: str = "claude",
    accepts_tool_servers: bool = True,
) -> RoleLaunch:
    return RoleLaunch(
        agent_id="agent-1",
        request_id=uuid.uuid4(),
        context=RoleLaunchContext("reviewer", None, None, None, None),
        folder="/tmp",
        provider=provider,
        session_options=session_options,
        prompt="prompt",
        report_path="/tmp/report.md",
        handover_artifact={"path": "/tmp/report.md", "sha256": "0" * 64},
        settings_file=Path("/tmp/settings.json"),
        accepts_tool_servers=accepts_tool_servers,
        host_denied_tools=("mcp__other",),
    )


def plan_with_entries(entries: dict[str, str | None]) -> dict[str, Any]:
    return {
        "entries": dict(entries),
        "arguments": [
            {"name": name, "value": value, "source": "harness entry", "key": "k", "file": "f"}
            for name, value in entries.items()
        ],
        "removed": [],
        "notTaken": [],
        "notTakenReason": None,
        "hostConfiguration": [],
    }


def test_a_claude_create_carries_the_arguments_beside_the_denied_tools() -> None:
    call = build_launch_call(launch({"extraArgs": plan_with_entries({"chrome": None})}))

    assert call["agent"]["providerOptions"] == {
        "disallowedTools": ["mcp__other", "mcp__agents-remember"],
        "extraArgs": {"chrome": None},
    }
    assert call["extraArguments"]["arguments"][0]["source"] == "harness entry"


def test_a_claude_create_without_the_task_server_carries_the_arguments_alone() -> None:
    call = build_launch_call(
        launch(
            {"extraArgs": plan_with_entries({"name": "investigator"})}, accepts_tool_servers=False
        )
    )

    assert call["agent"]["providerOptions"] == {"extraArgs": {"name": "investigator"}}


def test_a_start_without_extra_arguments_carries_none() -> None:
    call = build_launch_call(launch({"model": "claude-opus-5-5"}))

    assert "extraArgs" not in call["agent"]["providerOptions"]
    assert "extraArguments" not in call
    assert "extraArguments" not in applied_to_agent(call)


def test_another_harness_is_never_handed_the_arguments() -> None:
    call = build_launch_call(
        launch({"extraArgs": plan_with_entries({"chrome": None})}, provider="codex")
    )

    assert "providerOptions" not in call["agent"]


def test_the_receipt_records_the_arguments_and_the_role_entries_not_taken() -> None:
    plan = plan_with_entries({"chrome": None})
    plan["notTaken"] = [
        {"name": "name", "value": "x", "source": "role entry", "key": "k2", "file": "f2"}
    ]
    plan["notTakenReason"] = "the start uses codex; the role's extra arguments belong to claude"

    receipt = applied_to_agent(build_launch_call(launch({"extraArgs": plan})))

    assert receipt["extraArguments"]["arguments"][0]["name"] == "chrome"
    assert receipt["extraArguments"]["notTaken"][0]["name"] == "name"
    assert "codex" in receipt["extraArguments"]["notTakenReason"]


def host_config(root: Path, providers: dict[str, Any]) -> McpRuntimeConfig:
    home = root / "home"
    home.mkdir(exist_ok=True)
    (home / "config.json").write_text(json.dumps({"agents": {"providers": providers}}))
    path = user_settings_path_for_write()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "orchestration": {},
                "paseoRuntime": {
                    "installPrefix": (root / "prefix").as_posix(),
                    "home": home.as_posix(),
                    "listen": "127.0.0.1:6831",
                    "providers": {},
                    "embed": [],
                },
            }
        ),
        encoding="utf-8",
    )
    return McpRuntimeConfig(
        config_path=root / "mcp.json",
        coordination_root=root / "coordination",
        workspace_root=root / "projects",
        transcript_root=root / "coordination" / "logs" / "mcp",
    )


def presenter(config: McpRuntimeConfig) -> NativePresenterHarness:
    return NativePresenterHarness(config, identity=HostIdentity(REPO, "task", "leaf", "frame"))


@pytest.mark.parametrize("block", ["options", "params"])
def test_each_confined_step_refuses_a_host_map_that_gives_claude_arguments(
    root: Path, monkeypatch: pytest.MonkeyPatch, block: str
) -> None:
    monkeypatch.setattr(
        "agents_remember.serving.paseo.paseo_daemon_config.daemon_provider_extra_args",
        lambda config, provider, read_config=None: (),
    )
    config = host_config(root, {"claude": {block: {"extraArgs": {"chrome": None}}}})
    runtime = config.paseo_runtime
    assert runtime is not None
    harness = presenter(config)

    for step in (
        lambda: harness.message("agent-1", role="developer", text="hello"),
        lambda: harness.start(cast(PresenterAttempt, object()), "instructions", root / "working"),
        lambda: harness.reconcile(cast(PresenterAttempt, object()), root / "working"),
        lambda: harness.resume("agent-1"),
    ):
        with pytest.raises(PresenterRefused) as refused:
            step()
        assert refused.value.reason == "presenter-host-extra-arguments"
        assert str(runtime.home / "config.json") in refused.value.detail
        assert "chrome" in refused.value.detail
        assert "runtime_install" in refused.value.detail
        assert "paseo provision" in refused.value.detail


def test_a_host_map_without_claude_arguments_lets_a_confined_step_run(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "agents_remember.serving.paseo.paseo_daemon_config.daemon_provider_extra_args",
        lambda config, provider, read_config=None: (),
    )
    config = host_config(root, {"claude": {"options": {"disallowedTools": ["mcp__x"]}}})
    harness = presenter(config)

    assert harness._require_confined_host() is None


def test_a_clean_file_and_a_dirty_running_map_refuse_a_confined_step(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MIK-R192 rule 8: the running daemon's map is read at the envelope the daemon reports.

    The file holds no extra arguments and the daemon reports ``chrome`` at ``config.providers``,
    where Paseo's client puts it. An adapter that looked under the file's ``agents.providers``
    found nothing and let the step run with the host's argument (L184-R1-F21).
    """

    config = host_config(root, {"claude": {"options": {"disallowedTools": ["mcp__x"]}}})
    monkeypatch.setattr(
        paseo_bridge,
        "bridge_call",
        lambda config, command, payload: {
            "config": {"providers": {"claude": {"options": {"extraArgs": {"chrome": None}}}}},
            "readAt": "2026-10-21T09:00:00.000Z",
        },
    )

    with pytest.raises(PresenterRefused) as refused:
        presenter(config)._require_confined_host()

    assert refused.value.reason == "presenter-host-extra-arguments"
    assert "chrome" in refused.value.detail


def test_a_live_map_that_cannot_be_read_refuses_a_confined_step(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A map that was not read is not a clean one: the confined step refuses before the host."""

    config = host_config(root, {"claude": {"options": {"disallowedTools": ["mcp__x"]}}})

    def unread(config: Any, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise PaseoBridgeFailure("paseo_daemon_unreachable", "the daemon did not answer")

    monkeypatch.setattr(paseo_bridge, "bridge_call", unread)

    with pytest.raises(PresenterRefused) as refused:
        presenter(config)._require_confined_host()

    assert refused.value.reason == "presenter-host-map-unread"
    assert "paseo_daemon_unreachable" in refused.value.detail


def test_an_ordinary_start_records_a_live_map_it_could_not_read(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The launch record says the running map was not read; the file's map is not called clean."""

    config = host_config(root, {"claude": {"options": {"extraArgs": {"chrome": None}}}})

    def unread(config: Any, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise PaseoBridgeFailure("paseo_daemon_unreachable", "the daemon did not answer")

    monkeypatch.setattr(paseo_bridge, "bridge_call", unread)
    settings = load(root)

    statement = extra_args_statement(
        settings,
        settings.resolved_role_knobs("investigator", "leaf"),
        "investigator",
        config=config,
    )

    assert statement["hostConfiguration"] == {"claude": ["chrome"]}
    assert statement["hostConfigurationUnread"] == ["claude"]
    plan = extra_args_plan(statement, "claude")
    assert plan is not None
    assert plan["hostConfigurationUnread"] == ["claude"]


def test_an_ordinary_start_with_no_arguments_still_records_an_unread_map(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F21: a clean disk is not a verified-clean host, even when the start adds nothing.

    With nothing in the file, no own arguments and a live read that failed, the plan used to be
    ``None`` — so the record could not tell this start from one whose map was read and found
    clean. The unread marker alone must keep the plan alive.
    """

    host_config(root, {})

    def unread(config: Any, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise PaseoBridgeFailure("paseo_daemon_unreachable", "the daemon did not answer")

    monkeypatch.setattr(paseo_bridge, "bridge_call", unread)
    settings = load(root)

    statement = extra_args_statement(
        settings,
        settings.resolved_role_knobs("investigator", "leaf"),
        "investigator",
        config=host_config(root, {}),
    )

    assert not statement.get("hostConfiguration", {}).get("claude")
    assert statement["hostConfigurationUnread"] == ["claude"]

    plan = extra_args_plan(statement, "claude")

    assert plan is not None
    assert plan["entries"] == {}
    assert plan["arguments"] == []
    assert plan["hostConfigurationUnread"] == ["claude"]


def test_a_start_whose_map_was_read_and_clean_records_no_plan(root: Path) -> None:
    """The other side of F21: nothing to say and nothing unread still means no plan."""

    host_config(root, {})
    settings = load(root)

    statement = extra_args_statement(
        settings,
        settings.resolved_role_knobs("investigator", "leaf"),
        "investigator",
    )

    assert not statement.get("hostConfigurationUnread")
    assert extra_args_plan(statement, "claude") is None


def test_a_presenter_resume_refuses_the_host_map_before_any_host_call(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MIK-R192 rule 8: the public resume route checks the map before the host is asked.

    The set, the daemon's file stands under a clean map, is dirty, so the host must not be called
    at all: a resume that reached it would run the seat with the configured arguments.
    """
    monkeypatch.setattr(
        "agents_remember.serving.paseo.paseo_daemon_config.daemon_provider_extra_args",
        lambda config, provider, read_config=None: (),
    )
    config = host_config(root, {"claude": {"options": {"extraArgs": {"chrome": None}}}})
    calls: list[str] = []

    def bridge(config: Any, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(command)
        raise AssertionError("the host must not be asked while the map gives Claude arguments")

    monkeypatch.setattr(presenter_native, "bridge_call", bridge)
    harness = presenter(config)

    with pytest.raises(PresenterRefused) as refused:
        harness.resume("agent-1")

    assert refused.value.reason == "presenter-host-extra-arguments"
    assert "chrome" in refused.value.detail
    assert calls == []


def test_a_presenter_resume_refuses_a_running_daemon_map_the_file_does_not_show(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The host's running map alone is enough: a clean file does not let the resume through."""
    monkeypatch.setattr(
        "agents_remember.serving.paseo.paseo_daemon_config.daemon_provider_extra_args",
        lambda config, provider, read_config=None: ("chrome",),
    )
    config = host_config(root, {"claude": {"options": {"disallowedTools": ["mcp__x"]}}})
    calls: list[str] = []
    monkeypatch.setattr(
        presenter_native,
        "bridge_call",
        lambda config, command, payload: calls.append(command) or {},
    )
    harness = presenter(config)

    with pytest.raises(PresenterRefused) as refused:
        harness.resume("agent-1")

    assert refused.value.reason == "presenter-host-extra-arguments"
    assert calls == []


def test_a_presenter_resume_under_a_clean_map_reaches_the_same_session(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The positive control: the same session, one host call, nothing refused."""
    monkeypatch.setattr(
        "agents_remember.serving.paseo.paseo_daemon_config.daemon_provider_extra_args",
        lambda config, provider, read_config=None: (),
    )
    config = host_config(root, {"claude": {"options": {"disallowedTools": ["mcp__x"]}}})
    calls: list[tuple[str, dict[str, Any]]] = []

    def bridge(config: Any, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append((command, payload))
        return {"agent": {"status": "running", "archivedAt": None}, "resume": {"resumed": True}}

    monkeypatch.setattr(presenter_native, "bridge_call", bridge)
    harness = presenter(config)

    receipt = harness.resume("agent-1")

    assert calls == [("agent-resume", {"agentId": "agent-1"})]
    assert receipt.session_id == "agent-1"
    assert receipt.execution_id == "agent-1"


def test_the_presenter_launch_states_no_extra_arguments(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_user(
        root,
        {
            "harnesses": {"claude": {"extraArgs": {"chrome": None}}},
            "roles": {"review-presenter": CLAUDE},
        },
    )
    config = presenter_config(root)
    captured: dict[str, Any] = {}

    def resolve(config: Any, defaults: dict[str, Any], ids: Any, override: Any):
        captured.update(defaults)
        return "claude", {"model": "the-model", "choices": {}}

    monkeypatch.setattr(presenter_native, "resolve_agent_selection", resolve)

    presenter_native.qualified_launch(config, REPO)

    assert "extraArgs" not in captured


def test_the_reference_lists_exactly_the_reserved_names() -> None:
    text = DOCS.read_text(encoding="utf-8")
    start = text.index("<!-- MIK-R192 reserved names")
    end = text.index("<!-- /MIK-R192 reserved names -->")
    listed = [
        line.strip()
        for line in text[start:end].splitlines()
        if line.strip() and not line.strip().startswith(("```", "<!--"))
    ]

    assert sorted(listed) == sorted(RESERVED_EXTRA_ARG_NAMES)


def test_the_reference_names_the_key_its_places_and_the_example() -> None:
    text = DOCS.read_text(encoding="utf-8")

    assert (
        "### orchestration.harnesses.<harness>.extraArgs, orchestration.roles.<role>.extraArgs"
        in text
    )
    assert "orchestration.rolesPerLevel.<level>.<role>.extraArgs" in text
    assert '"chrome": null' in text
    assert "paseoRuntime.providers" in text


def test_the_reference_does_not_claim_a_launch_never_reads_the_daemon_configuration() -> None:
    """MIK-R192 rule 10: the reference states the one read-only read the launch makes.

    The old blanket sentence said a launch never reads ``config.json``; the start now reads the
    host's configured provider options there, so the claim is narrowed to profile resolution and
    the read-only exception is named beside it.
    """

    text = DOCS.read_text(encoding="utf-8")

    assert "The launch never reads or edits `config.json`." not in text
    assert "it never reads or edits `config.json`." in text
    assert "Profile resolution reads the host's profiles" in text
    paragraph = text[text.index("One read of that file is not profile resolution") :]
    paragraph = paragraph[: paragraph.index("\n\n")]
    assert "read-only" in paragraph
    assert "`extraArgs`" in paragraph
    assert "every start and every resume" in paragraph
    assert "nothing in a launch creates, changes or deletes it" in paragraph


def test_a_start_call_cannot_carry_extra_arguments() -> None:
    with pytest.raises(ValidationError):
        RoleAgentOverride(**{"agentId": "claude", "extraArgs": {"chrome": None}})


def test_a_step_up_to_another_harness_does_not_take_the_role_entries(root: Path) -> None:
    write_user(
        root,
        {"roles": {"worker": {**CLAUDE, "extraArgs": {"chrome": None}}}},
    )
    settings = load(root)
    statement = extra_args_statement(
        settings, settings.resolved_role_knobs("worker", "leaf"), "worker"
    )
    defaults = {
        "role": "worker",
        "agent": "claude",
        "model": "claude-opus-5-5",
        "extraArgs": statement,
        "stepUp": {
            "harness": "codex",
            "model": "gpt-a",
            "source": "role settings",
            "key": "orchestration.roles.worker.stepUp",
        },
    }

    stepped = select_step_up(defaults, None, reason="the model is unavailable today")

    assert stepped["agent"] == "codex"
    assert stepped["extraArgs"] == statement
    role_plan = extra_args_plan(stepped["extraArgs"], "claude")
    assert role_plan is not None
    assert role_plan["entries"] == {"chrome": None}
    stepped_plan = extra_args_plan(stepped["extraArgs"], "codex")
    assert stepped_plan is not None
    assert stepped_plan["entries"] == {}


# ── the two create routes admit the same values (L184-R1-F23) ─────────────────

BRIDGE_SCRIPT = REPO_ROOT / "mcp/src/agents_remember/cli/paseo_bridge.mjs"
# One entry per admission the two routes must answer alike: a name and its JSON value.
ARGUMENT_CASES: tuple[tuple[str, Any], ...] = (
    ("chrome", ""),
    ("chrome", " "),
    ("chrome", "   "),
    ("chrome", " a b "),
    ("chrome", "x"),
    ("chrome", None),
    ("chrome", 3),
    ("chrome", True),
    ("-chrome", "x"),
    ("", "x"),
)
JS_ADMISSIONS = (
    "import(process.env.BRIDGE_URL).then(async (m) => {"
    " const base = {agentId:'a', idempotencyKey:'k', workspaceId:'w', provider:'claude',"
    "  title:'t', labels:{}};"
    " const made = [];"
    " const daemon = { createAgent: async (payload) => { made.push(payload); return {}; } };"
    " const workspace = { refresh: async () => ({ workspaceDirectory: '/w' }),"
    "  agents: { create: async () => ({ current: () => ({}) }) } };"
    " const api = { workspaces: { ref: () => workspace } };"
    " const decisions = [];"
    " const errors = [];"
    " for (const [name, value] of JSON.parse(process.env.ARGUMENT_CASES)) {"
    "  try {"
    "   await m.agentCreation({...base, providerOptions: {extraArgs: {[name]: value}}}, 'a')"
    "    .create(api, daemon);"
    "   decisions.push('admitted');"
    "  } catch (error) { decisions.push('refused'); errors.push((error && error.code) || String(error)); }"
    " }"
    " const crossed = made.map((payload) => payload.config.providerOptions.extraArgs);"
    " process.stdout.write(JSON.stringify({decisions, errors, crossed}));"
    "}).catch((error) => { process.stdout.write('IMPORT-FAIL ' + error.message); process.exit(1); });"
)


@pytest.mark.skipif(shutil.which("node") is None, reason="the bridge script needs Node.js")
def test_the_two_create_routes_admit_the_same_argument_values(root: Path) -> None:
    """MIK-R192 rule 1: the settings parser and the bridge narrow a value by one rule, not two.

    Each side is asked the same cases -- empty, blank, padded, plain, bare flag, number, boolean,
    malformed name -- and the answers must agree case for case. The strings the bridge admits are
    read back off the create request it hands the host, so a value it accepted but rewrote fails
    here too. (L184-R1-F23: the Python side refused ``""`` while the bridge passed it.)
    """

    python: list[str] = []
    for name, value in ARGUMENT_CASES:
        write_user(root, {"harnesses": {"claude": {"extraArgs": {name: value}}}})
        try:
            settings = load(root)
        except AgenticSettingsError:
            python.append("refused")
            continue
        python.append("admitted")
        assert settings.harness_extra_args["claude"][name].value == value

    completed = subprocess.run(
        [shutil.which("node") or "node", "--input-type=module", "-e", JS_ADMISSIONS],
        capture_output=True,
        check=False,
        text=True,
        timeout=HANG_GUARD_SECONDS,
        env={
            **os.environ,
            "BRIDGE_URL": BRIDGE_SCRIPT.resolve().as_uri(),
            "ARGUMENT_CASES": json.dumps(ARGUMENT_CASES),
        },
    )
    assert completed.returncode == 0, completed.stderr
    assert not completed.stdout.startswith("IMPORT-FAIL"), completed.stdout
    javascript = json.loads(completed.stdout)

    assert javascript["decisions"] == python, javascript["errors"]
    assert javascript["crossed"] == [
        {"chrome": value}
        for name, value in ARGUMENT_CASES
        if (name, value)
        in {
            ("chrome", ""),
            ("chrome", " "),
            ("chrome", "   "),
            ("chrome", " a b "),
            ("chrome", "x"),
            ("chrome", None),
        }
    ]
