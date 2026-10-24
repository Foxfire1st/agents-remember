"""One call starts Bootstrap; the user settings are written once (MIK-R196 rules 1-3, 11).

Each case protects one distinct operation the leaf adds: the free agent's admission through the
same tables the dashboard uses, the caller-harness mapping that decides its harness, the host
authority and role-settings writes with their refusals, and the dashboard address a start answer
carries.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from agents_remember.application import user_settings_init as user_settings_init_module
from agents_remember.application.caller_harness import provider_for_caller
from agents_remember.application.role_launch_context import (
    PARENTLESS_START_SELECTIONS,
    TASKLESS_ROLES,
    parentless_selection_violation,
)
from agents_remember.application.user_settings_init import (
    SettingsInitRefused,
    SettingsInitRequest,
    initialize_role_settings,
    role_settings_state,
)
from agents_remember.cli import paseo_role_tools as role_tools
from agents_remember.cli import role_agent_override as overrides
from agents_remember.cli.dashboard_address import chat_address, dashboard_address
from agents_remember.cli.role_launch_first_run import SCAFFOLD_ENTRIES, scaffold_present
from agents_remember.kernel.primitives.paseo_authority import (
    AUTHORITY_INIT_KEY,
    AUTHORITY_INIT_STATE,
    establish_authority_paseo_runtime,
)
from agents_remember.kernel.primitives.paseo_runtime_settings import (
    PaseoEmbedEntry,
    PaseoRuntimeSettings,
)
from agents_remember.kernel.primitives.runtime_config import DashboardSettings, McpRuntimeConfig
from agents_remember.models.role_agents import RoleStartCall
from agents_remember.models.role_launcher import LauncherRole, RoleDispatchRequest
from fastapi import HTTPException


def _config(tmp_path: Path) -> McpRuntimeConfig:
    root = tmp_path / "coordination"
    root.mkdir(exist_ok=True)
    return McpRuntimeConfig(
        config_path=tmp_path / "mcp" / "settings.json",
        coordination_root=root,
        workspace_root=tmp_path / "projects",
        transcript_root=tmp_path / "transcripts",
        dashboard=DashboardSettings(auto_start=False, port=9123),
    )


@pytest.fixture(autouse=True)
def _user_settings_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The one user settings path, inside the test's own XDG config home."""

    config_home = tmp_path / "xdg-config"
    config_home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", config_home.as_posix())
    monkeypatch.setenv("XDG_DATA_HOME", (tmp_path / "xdg-data").as_posix())
    monkeypatch.setenv("XDG_STATE_HOME", (tmp_path / "xdg-state").as_posix())
    return config_home / "agents-remember" / "settings.json"


# --------------------------------------------------------------------------------------------
# Rule 1: the free agent is a launchable role, and only through the parentless tables.
# --------------------------------------------------------------------------------------------


def test_bootstrap_is_a_launcher_role_with_no_document_and_no_parent() -> None:
    assert "bootstrap" in LauncherRole.__args__
    assert "bootstrap" in TASKLESS_ROLES
    assert PARENTLESS_START_SELECTIONS["bootstrap"] == (False, False, False)


def test_a_bootstrap_selection_refuses_every_document_reference() -> None:
    """MIK-R196 rule 2: no sprint, master or leaf is invented for the free agent."""

    assert parentless_selection_violation("bootstrap", None, None, None) is None
    for sprint, master, task in (
        ("sprint", None, None),
        (None, "master", None),
        (None, None, "leaf"),
    ):
        violated = parentless_selection_violation("bootstrap", sprint, master, task)
        assert violated is not None and "does not accept" in violated


@pytest.mark.parametrize(
    ("selection", "configured", "refused"),
    [
        (("codex", "missing"), True, True),
        (("missing", None), True, True),
        (("codex", "offered"), True, False),
        ((None, "offered"), True, False),
        (("codex", "missing"), False, False),
    ],
)
def test_bootstrap_overrides_are_validated_before_dispatch_when_a_host_is_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selection: tuple[str | None, str | None],
    configured: bool,
    refused: bool,
) -> None:
    """887182ab: configured overrides need no preparation; a cold catalog still needs its host."""

    agent, model = selection
    config = _config(tmp_path)
    if configured:
        establish_authority_paseo_runtime(config)
    before = config.config_path.read_bytes() if configured else None
    events: list[str] = []
    monkeypatch.setattr(role_tools, "_caller", lambda *_args: None)

    def bind(_config: Any, request: RoleDispatchRequest) -> RoleDispatchRequest:
        selected = request.agent_override
        assert selected is not None
        if selected.agent_id is None:
            assert request.caller_harness == "codex"
            return request.model_copy(
                update={"agent_override": selected.model_copy(update={"agent_id": "codex"})}
            )
        return request

    def validate(_config: Any, _defaults: Any, _layers: Any, selected: Any) -> dict[str, Any]:
        events.append("validated")
        assert selected.agent_id == (agent or "codex")
        assert selected.model_id == model
        if selected.agent_id == "missing" or selected.model_id == "missing":
            raise ValueError("the selected agent/model is not offered")
        return {}

    def dispatch(*_args: Any, **_kwargs: Any) -> None:
        events.append("dispatched")
        raise HTTPException(status_code=409, detail="test dispatch boundary reached")

    monkeypatch.setattr(role_tools, "with_caller_harness", bind)
    monkeypatch.setattr(overrides, "resolve_agent_selection", validate)
    monkeypatch.setattr(role_tools, "_role_launch_dispatch_endpoint", dispatch)
    answer = role_tools.start_role(
        config,
        RoleStartCall(
            role="bootstrap",
            request_id=UUID("f131a006-26f5-497a-bb98-044eec00b0d3"),
            caller_harness="codex",
            agent=agent,
            model=model,
        ),
        environment={},
    )

    assert events == (
        ["validated"] if refused else ["validated", "dispatched"] if configured else ["dispatched"]
    )
    assert ("not offered" if refused else "dispatch boundary reached") in answer["detail"]
    assert answer["status"] == "refused"
    assert (config.config_path.read_bytes() if config.config_path.exists() else None) == before
    assert not (config.coordination_root / "tasks").exists(), "validation prepared no scaffold"


def test_other_roles_still_refuse_a_model_without_its_agent() -> None:
    with pytest.raises(ValueError, match="without the agent"):
        overrides.agent_override(
            RoleStartCall(
                role="architect",
                request_id=UUID("b782cae4-f92e-4374-bb94-dc6434f93269"),
                model="offered",
            )
        )


# --------------------------------------------------------------------------------------------
# Rule 1: the caller's own harness, from the honest signal, and a refusal instead of a guess.
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("named", "offered", "expected"),
    [
        ("claude-code", ["claude", "codex"], "claude"),
        ("Codex", ["claude", "codex"], "codex"),
        ("hermes-agent", ["hermes"], "hermes"),
        ("cursor-vscode", ["claude", "cursor"], "cursor"),
        # A name nothing maps to is never replaced by the first provider the host lists.
        ("some-unknown-harness", ["claude", "codex"], None),
        (None, ["claude"], None),
        ("claude", [], None),
    ],
)
def test_the_caller_harness_maps_to_a_provider_or_to_nothing(
    named: str | None, offered: list[str], expected: str | None
) -> None:
    assert provider_for_caller(named, offered) == expected


# --------------------------------------------------------------------------------------------
# Rule 11: the host authority, written once and never over a block that exists.
# --------------------------------------------------------------------------------------------


def test_the_host_authority_is_created_with_the_five_key_block(
    tmp_path: Path, _user_settings_in_tmp: Path
) -> None:
    """MIK-R196 rule 3: the host block is the MCP authority's, and the user file is not touched."""

    config = _config(tmp_path)
    answer = establish_authority_paseo_runtime(config)

    assert answer["state"] == "created" and answer["changed"] is True
    written = json.loads(config.config_path.read_text(encoding="utf-8"))
    assert set(written["paseoRuntime"]) == {"installPrefix", "home", "listen", "providers", "embed"}
    assert written[AUTHORITY_INIT_KEY] == AUTHORITY_INIT_STATE
    assert written["paseoRuntime"]["listen"] == "127.0.0.1:8766"
    assert written["coordinationRoot"] == config.coordination_root.as_posix()
    assert not _user_settings_in_tmp.exists(), "MIK-R137: the user settings file stays the user's"


def test_a_block_that_already_exists_is_never_rewritten(
    tmp_path: Path, _user_settings_in_tmp: Path
) -> None:
    config = _config(tmp_path)
    first = establish_authority_paseo_runtime(config)
    assert first["state"] == "created"
    before = config.config_path.read_bytes()

    answer = establish_authority_paseo_runtime(config)

    assert answer["changed"] is False and answer["state"] == "present"
    assert config.config_path.read_bytes() == before, "an existing authority is read, not edited"


def test_the_host_authority_is_added_to_a_file_without_one(
    tmp_path: Path, _user_settings_in_tmp: Path
) -> None:
    config = _config(tmp_path)
    config.config_path.parent.mkdir(parents=True)
    config.config_path.write_text('{"version": 1, "benchmarksEnabled": false}', encoding="utf-8")

    answer = establish_authority_paseo_runtime(config)

    assert answer["state"] == "written"
    written = json.loads(config.config_path.read_text(encoding="utf-8"))
    assert written["version"] == 1 and written["benchmarksEnabled"] is False
    assert "paseoRuntime" in written
    assert not _user_settings_in_tmp.exists()


def test_a_symlinked_destination_is_never_written(
    tmp_path: Path, _user_settings_in_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_host(monkeypatch, ["claude"])
    _user_settings_in_tmp.parent.mkdir(parents=True)
    real = tmp_path / "elsewhere.json"
    real.write_text("{}", encoding="utf-8")
    _user_settings_in_tmp.symlink_to(real)

    with pytest.raises(SettingsInitRefused) as refused:
        initialize_role_settings(_config(tmp_path), SettingsInitRequest(harness="claude"))

    assert refused.value.code == "destination-is-symlink"
    assert json.loads(real.read_text(encoding="utf-8")) == {}


# --------------------------------------------------------------------------------------------
# Rule 11: role harnesses, validated, written once, and read back.
# --------------------------------------------------------------------------------------------


def _fake_host(monkeypatch: pytest.MonkeyPatch, providers: list[str]) -> None:
    monkeypatch.setattr(user_settings_init_module, "_runnable_harnesses", lambda _config: providers)


def test_role_settings_write_one_harness_per_role_and_read_back(
    tmp_path: Path, _user_settings_in_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_host(monkeypatch, ["claude", "codex"])

    answer = initialize_role_settings(_config(tmp_path), SettingsInitRequest(harness="codex"))

    assert answer["changed"] is True and answer["state"] == "created"
    written = json.loads(_user_settings_in_tmp.read_text(encoding="utf-8"))
    roles = written["orchestration"]["roles"]
    assert {row["harness"] for row in roles.values()} == {"codex"}
    assert "bootstrap" in roles and "architect" in roles
    # Model and effort are left unset so each harness starts on its own defaults (rule 6).
    assert all(set(row) == {"harness"} for row in roles.values() if isinstance(row, dict))
    assert "paseoRuntime" not in written, "MIK-R137: host authority is not the user's file"
    assert answer["readBack"]["roleHarnesses"]["architect"] == "codex"


def test_role_settings_on_an_existing_file_are_reported_not_written(
    tmp_path: Path, _user_settings_in_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_host(monkeypatch, ["claude", "codex"])
    _user_settings_in_tmp.parent.mkdir(parents=True)
    existing = {"orchestration": {"roles": {"worker": {"harness": "claude", "model": "m1"}}}}
    _user_settings_in_tmp.write_text(json.dumps(existing), encoding="utf-8")
    before = _user_settings_in_tmp.read_bytes()

    answer = initialize_role_settings(_config(tmp_path), SettingsInitRequest(harness="codex"))

    assert answer["changed"] is False and answer["state"] == "already-initialized"
    assert _user_settings_in_tmp.read_bytes() == before
    assert answer["roleHarnesses"] == {"worker": "claude"}


def test_a_harness_the_host_does_not_run_is_refused_by_name(
    tmp_path: Path, _user_settings_in_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_host(monkeypatch, ["claude"])

    with pytest.raises(SettingsInitRefused) as refused:
        initialize_role_settings(_config(tmp_path), SettingsInitRequest(harness="codex"))

    assert refused.value.code == "harness-not-hosted"
    assert "claude" in refused.value.detail
    assert role_settings_state(_config(tmp_path))["roleHarnesses"] == {}


def test_no_harness_chosen_is_refused(tmp_path: Path, _user_settings_in_tmp: Path) -> None:
    with pytest.raises(SettingsInitRefused) as refused:
        initialize_role_settings(_config(tmp_path), SettingsInitRequest())

    assert refused.value.code == "no-harness-chosen"


def test_a_preview_writes_nothing(
    tmp_path: Path, _user_settings_in_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_host(monkeypatch, ["claude"])
    assert not _user_settings_in_tmp.exists()

    answer = initialize_role_settings(
        _config(tmp_path), SettingsInitRequest(harness="claude", dry_run=True)
    )

    assert answer["dryRun"] is True
    assert answer["state"] == "created"
    assert not _user_settings_in_tmp.exists(), "a preview writes nothing"


def test_the_initializer_refuses_a_destination_that_changed_after_its_preview(
    tmp_path: Path, _user_settings_in_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule 11: a file that appears between preview and write is reported, never overwritten."""

    _fake_host(monkeypatch, ["claude"])
    assert role_settings_state(_config(tmp_path))["initializable"] is True

    # The developer's own settings appear between the preview and the write.
    _user_settings_in_tmp.parent.mkdir(parents=True, exist_ok=True)
    _user_settings_in_tmp.write_text('{"orchestration": {"roles": {}}}', encoding="utf-8")
    before = _user_settings_in_tmp.read_bytes()

    with pytest.raises(SettingsInitRefused) as refused:
        user_settings_init_module._write_after_preview(
            _user_settings_in_tmp, {"orchestration": {"roles": {}}}, existed=True
        )

    assert refused.value.code == "destination-appeared"
    assert _user_settings_in_tmp.read_bytes() == before


# --------------------------------------------------------------------------------------------
# Rule 1: the answer names the dashboard the seat is continued in.
# --------------------------------------------------------------------------------------------


def test_the_start_answer_names_the_dashboard_and_the_hosts_chat_route(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert dashboard_address(config) == "http://127.0.0.1:9123/"

    settings = PaseoRuntimeSettings(
        install_prefix=tmp_path / "prefix",
        home=tmp_path / "home",
        listen="127.0.0.1:8766",
        providers={},
        embed=(PaseoEmbedEntry("http://127.0.0.1:9123", "http://127.0.0.1:8766"),),
    )
    assert chat_address(settings, "srv-1", "agent-1") == (
        "http://127.0.0.1:8766/h/srv-1/agent/agent-1",
        None,
    )
    assert chat_address(None, "srv-1", "agent-1")[1] == "no Paseo runtime is configured"
    assert chat_address(settings, None, "agent-1")[1] == "the host did not name its server id"


# --------------------------------------------------------------------------------------------
# Rules 2-3: what counts as a scaffold, and a request that carries no document.
# --------------------------------------------------------------------------------------------


def test_a_coordination_root_without_its_scaffold_is_not_prepared(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert scaffold_present(config) is False
    for entry in SCAFFOLD_ENTRIES:
        (config.coordination_root / entry).mkdir(parents=True, exist_ok=True)
    assert scaffold_present(config) is True


def test_a_bootstrap_request_carries_no_document_reference() -> None:
    request = RoleDispatchRequest.model_validate(
        {"role": "bootstrap", "requestId": "0e2a2b4f-1c5e-4b1e-9a2e-2f0f1a2b3c4d"}
    )
    assert (request.sprint_document_ref, request.master_document_ref) == (None, None)
    assert request.task_document_ref is None
    assert request.caller_harness is None


def test_the_initializer_reports_an_unreadable_file_instead_of_replacing_it(
    tmp_path: Path, _user_settings_in_tmp: Path
) -> None:
    _user_settings_in_tmp.parent.mkdir(parents=True)
    _user_settings_in_tmp.write_text("not json", encoding="utf-8")
    before = _user_settings_in_tmp.read_bytes()

    with pytest.raises(SettingsInitRefused) as refused:
        initialize_role_settings(_config(tmp_path), SettingsInitRequest(harness="claude"))

    assert refused.value.code == "settings-unreadable"
    assert _user_settings_in_tmp.read_bytes() == before
    assert os.access(_user_settings_in_tmp, os.W_OK)


def test_the_created_file_is_not_world_readable(
    tmp_path: Path, _user_settings_in_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A settings file this surface creates carries no group or world read bit."""

    _fake_host(monkeypatch, ["claude"])
    initialize_role_settings(_config(tmp_path), SettingsInitRequest(harness="claude"))
    mode = _user_settings_in_tmp.stat().st_mode & 0o777
    assert mode == 0o600, oct(mode)
