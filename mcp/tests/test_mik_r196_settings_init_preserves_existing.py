"""User settings initialization: one exclusive create, and an existing file left alone.

MIK-R196 rule 11 as sealed in L188-B03, L188-B04 and L188-B07: the surface creates a role-harness
settings file only where there is none, so every pre-existing destination -- marked by this
product or not -- keeps its raw bytes and its mode; there is no replacement step and no temporary
sibling path; and the public answer is a modeled refusal that never re-reads the file that refused
and never needs a live host to report what an existing file holds.

The cases below own that surface only. They never touch the developer's real settings file: every
corpus lives in the test's own ``XDG_CONFIG_HOME`` and its own coordination root.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from agents_remember.application import user_settings_init as user_settings_init_module
from agents_remember.application.user_settings_init import (
    SettingsInitRefused,
    SettingsInitRequest,
    initialize_role_settings,
    role_settings_state,
)
from agents_remember.kernel.primitives.runtime_config import DashboardSettings, McpRuntimeConfig
from agents_remember.mcp.tools import role_agents as role_agents_tools
from agents_remember.mcp.tools.role_agents import user_settings_init_payload


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


def _fake_host(monkeypatch: pytest.MonkeyPatch, providers: list[str]) -> None:
    monkeypatch.setattr(user_settings_init_module, "_runnable_harnesses", lambda _config: providers)
    monkeypatch.setattr(role_agents_tools, "runnable_harnesses", lambda _config: providers)


def _write(path: Path, content: dict[str, object], mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, indent=2), encoding="utf-8")
    os.chmod(path, mode)


def _assert_untouched(path: Path, before: bytes, mode: int = 0o640) -> None:
    assert path.read_bytes() == before, "an existing settings file is read, never rewritten"
    assert path.stat().st_mode & 0o777 == mode, "an existing settings file keeps its mode"


# ---------------------------------------------------------------------------------------------
# B03: an existing destination is reported as it stands, including one this product marked.
# ---------------------------------------------------------------------------------------------


def test_an_existing_marked_file_is_reported_and_kept_byte_for_byte(
    tmp_path: Path, _user_settings_in_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``settingsInit`` marker is provenance, not permission to complete the file."""

    _fake_host(monkeypatch, ["claude", "codex"])
    _write(
        _user_settings_in_tmp,
        {
            "settingsInit": "host-only",
            "orchestration": {"roles": {"worker": {"harness": "claude", "model": "m1"}}},
        },
    )
    before = _user_settings_in_tmp.read_bytes()

    answer = initialize_role_settings(_config(tmp_path), SettingsInitRequest(harness="codex"))

    assert answer["changed"] is False and answer["state"] == "already-initialized"
    assert answer["roleHarnesses"] == {"worker": "claude"}
    assert answer["preservedKeys"] == ["orchestration", "settingsInit"]
    assert "never overwrites" in answer["detail"]
    _assert_untouched(_user_settings_in_tmp, before)


def test_an_existing_file_without_role_choices_is_not_completed(
    tmp_path: Path, _user_settings_in_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule 11: absent choices are reported, never silently filled in by this surface."""

    _fake_host(monkeypatch, ["claude"])
    _write(_user_settings_in_tmp, {"unrelated": ["kept"]})
    before = _user_settings_in_tmp.read_bytes()

    answer = initialize_role_settings(_config(tmp_path), SettingsInitRequest(harness="claude"))

    assert answer["state"] == "already-initialized" and answer["roleHarnesses"] == {}
    assert role_settings_state(_config(tmp_path))["initializable"] is False
    _assert_untouched(_user_settings_in_tmp, before)


def test_the_existing_file_is_reported_before_any_choice_is_validated(
    tmp_path: Path, _user_settings_in_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No catalog and no host is asked anything when the file already answers (L188-B07)."""

    _write(_user_settings_in_tmp, {"orchestration": {"roles": {}}})
    before = _user_settings_in_tmp.read_bytes()

    def unavailable(_config: McpRuntimeConfig) -> list[str]:
        raise SettingsInitRefused("host-unreadable", "no host", "start the host")

    monkeypatch.setattr(user_settings_init_module, "_runnable_harnesses", unavailable)
    answer = initialize_role_settings(
        _config(tmp_path), SettingsInitRequest(harness="not-a-harness-anyone-runs")
    )

    assert answer["state"] == "already-initialized", "an existing file needs no live catalog"
    _assert_untouched(_user_settings_in_tmp, before)


# ---------------------------------------------------------------------------------------------
# B03: the one write, on an absent destination, and only there.
# ---------------------------------------------------------------------------------------------


def test_an_absent_destination_is_created_exclusively_with_the_chosen_roles(
    tmp_path: Path, _user_settings_in_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_host(monkeypatch, ["claude", "codex"])

    answer = initialize_role_settings(_config(tmp_path), SettingsInitRequest(harness="codex"))

    assert answer["changed"] is True and answer["state"] == "created"
    assert answer["writtenKeys"] == ["orchestration"]
    written = json.loads(_user_settings_in_tmp.read_text(encoding="utf-8"))
    assert set(written) == {"orchestration"}
    roles = written["orchestration"]["roles"]
    assert {row["harness"] for row in roles.values()} == {"codex"}
    # Model and effort stay unset so each harness starts on its own defaults (rule 6).
    assert all(set(row) == {"harness"} for row in roles.values())
    mode = _user_settings_in_tmp.stat().st_mode & 0o777
    assert mode == 0o600, oct(mode)


def test_a_preview_of_an_absent_destination_writes_nothing(
    tmp_path: Path, _user_settings_in_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_host(monkeypatch, ["claude"])

    answer = user_settings_init_payload(
        _config(tmp_path), harness="claude", roles=None, dry_run=True, target="role-settings"
    )

    assert answer["state"] == "preview" and answer["changed"] is True
    assert not _user_settings_in_tmp.exists()
    assert not list(_user_settings_in_tmp.parent.glob(".*")), "no temporary sibling is made"


# ---------------------------------------------------------------------------------------------
# B04: no temporary sibling path, no chmod-then-replace, no following a link.
# ---------------------------------------------------------------------------------------------


def test_a_sibling_init_symlink_is_neither_followed_nor_truncated(
    tmp_path: Path, _user_settings_in_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old writer truncated ``.<name>.init`` and then replaced the destination with it."""

    _fake_host(monkeypatch, ["claude"])
    _user_settings_in_tmp.parent.mkdir(parents=True, exist_ok=True)
    victim = tmp_path / "elsewhere.json"
    victim.write_text("VICTIM-BYTES", encoding="utf-8")
    os.chmod(victim, 0o644)
    sibling = _user_settings_in_tmp.with_name(f".{_user_settings_in_tmp.name}.init")
    sibling.symlink_to(victim)

    answer = initialize_role_settings(_config(tmp_path), SettingsInitRequest(harness="claude"))

    assert answer["state"] == "created"
    assert victim.read_bytes() == b"VICTIM-BYTES", "the sibling target is never opened for writing"
    assert victim.stat().st_mode & 0o777 == 0o644, "the sibling target keeps its mode"
    assert sibling.is_symlink(), "the sibling path is not consumed by a rename"
    assert _user_settings_in_tmp.is_file() and not _user_settings_in_tmp.is_symlink()


def test_a_destination_present_at_write_time_is_refused(
    tmp_path: Path, _user_settings_in_tmp: Path
) -> None:
    """Rule 11: a destination that appeared after the preview is reported, never overwritten."""

    _write(_user_settings_in_tmp, {"developer": "own settings"})
    before = _user_settings_in_tmp.read_bytes()

    with pytest.raises(SettingsInitRefused) as refused:
        user_settings_init_module._write_after_preview(
            _user_settings_in_tmp, {"orchestration": {"roles": {}}}, existed=False
        )

    assert refused.value.code == "destination-appeared"
    _assert_untouched(_user_settings_in_tmp, before)


def test_a_destination_that_appears_after_the_state_read_is_refused(
    tmp_path: Path, _user_settings_in_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The write is decided by what is on disk now, not by what the preview read saw."""

    _fake_host(monkeypatch, ["claude"])
    _write(_user_settings_in_tmp, {"developer": "own settings"})
    before = _user_settings_in_tmp.read_bytes()
    real_read = user_settings_init_module.read_settings_file
    reads = {"count": 0}

    def read_before_the_file_arrived(path: Path) -> dict[str, object] | None:
        reads["count"] += 1
        return None if reads["count"] == 1 else real_read(path)

    monkeypatch.setattr(
        user_settings_init_module, "read_settings_file", read_before_the_file_arrived
    )

    with pytest.raises(SettingsInitRefused) as refused:
        initialize_role_settings(_config(tmp_path), SettingsInitRequest(harness="claude"))

    assert refused.value.code == "destination-appeared"
    _assert_untouched(_user_settings_in_tmp, before)


def test_the_exclusive_create_refuses_a_destination_the_existence_check_missed(
    tmp_path: Path, _user_settings_in_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``O_EXCL`` is the guard: the kernel refuses a file that appears after the check."""

    _write(_user_settings_in_tmp, {"developer": "own settings"})
    before = _user_settings_in_tmp.read_bytes()
    real_exists, real_is_symlink = Path.exists, Path.is_symlink
    target = _user_settings_in_tmp

    def blind_exists(self: Path) -> bool:
        return False if self == target else real_exists(self)

    def blind_is_symlink(self: Path) -> bool:
        return False if self == target else real_is_symlink(self)

    monkeypatch.setattr(Path, "exists", blind_exists)
    monkeypatch.setattr(Path, "is_symlink", blind_is_symlink)

    with pytest.raises(SettingsInitRefused) as refused:
        user_settings_init_module._create_exclusively(target, {"orchestration": {"roles": {}}})

    assert refused.value.code == "settings-not-writable"
    _assert_untouched(_user_settings_in_tmp, before)


# ---------------------------------------------------------------------------------------------
# B07: the public answer refuses once, without a second read and without a live host.
# ---------------------------------------------------------------------------------------------


def test_the_public_answer_refuses_an_unreadable_file_without_a_second_read(
    tmp_path: Path, _user_settings_in_tmp: Path
) -> None:
    """The refusal handler used to re-enter ``role_settings_state`` and raise again."""

    _user_settings_in_tmp.parent.mkdir(parents=True)
    _user_settings_in_tmp.write_text("not json", encoding="utf-8")
    os.chmod(_user_settings_in_tmp, 0o644)
    before = _user_settings_in_tmp.read_bytes()

    answer = user_settings_init_payload(
        _config(tmp_path), harness="claude", roles=None, dry_run=False, target="role-settings"
    )

    assert answer["ok"] is False and answer["state"] == "refused"
    assert answer["code"] == "settings-unreadable"
    assert answer["path"] == _user_settings_in_tmp.as_posix()
    assert answer["nextAction"], "a refusal names its recovery"
    assert "not json" not in json.dumps(answer), "no file content rides the answer"
    _assert_untouched(_user_settings_in_tmp, before, 0o644)


def test_the_public_answer_refuses_the_old_location_collision(
    tmp_path: Path, _user_settings_in_tmp: Path
) -> None:
    config = _config(tmp_path)
    old = config.coordination_root / "system" / "settings.json"
    old.parent.mkdir(parents=True)
    old.write_text('{"legacy": true}', encoding="utf-8")
    before = old.read_bytes()

    answer = user_settings_init_payload(
        config, harness="claude", roles=None, dry_run=False, target="role-settings"
    )

    assert answer["ok"] is False and answer["state"] == "refused"
    assert answer["code"] == "settings-location-upgrade-required"
    assert answer["path"] == _user_settings_in_tmp.as_posix()
    assert "upgrade-user-settings" in answer["detail"]
    assert old.read_bytes() == before, "the old file is never read for a migration"
    assert not _user_settings_in_tmp.exists()


def test_the_public_answer_reports_existing_choices_without_a_live_host(
    tmp_path: Path, _user_settings_in_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(_user_settings_in_tmp, {"orchestration": {"roles": {"worker": {"harness": "claude"}}}})
    before = _user_settings_in_tmp.read_bytes()

    def unavailable(_config: McpRuntimeConfig) -> list[str]:
        raise SettingsInitRefused("host-unreadable", "the host did not answer", "start the host")

    monkeypatch.setattr(user_settings_init_module, "_runnable_harnesses", unavailable)
    answer = user_settings_init_payload(
        _config(tmp_path), harness="codex", roles=None, dry_run=False, target="role-settings"
    )

    assert answer["ok"] is True and answer["state"] == "already-initialized"
    assert answer["roleHarnesses"] == {"worker": "claude"}
    _assert_untouched(_user_settings_in_tmp, before)


def test_the_public_answer_refuses_a_symlinked_destination(
    tmp_path: Path, _user_settings_in_tmp: Path
) -> None:
    _user_settings_in_tmp.parent.mkdir(parents=True)
    real = tmp_path / "elsewhere.json"
    real.write_text("{}", encoding="utf-8")
    _user_settings_in_tmp.symlink_to(real)

    answer = user_settings_init_payload(
        _config(tmp_path), harness="claude", roles=None, dry_run=False, target="role-settings"
    )

    assert answer["ok"] is False and answer["code"] == "destination-is-symlink"
    assert real.read_text(encoding="utf-8") == "{}"


def test_the_host_block_is_refused_by_name_not_written_into_the_user_file(
    tmp_path: Path, _user_settings_in_tmp: Path
) -> None:
    """The paseoRuntime block is the MCP authority file's (MIK-R137), not this file's."""

    config = _config(tmp_path)

    answer = user_settings_init_payload(
        config, harness=None, roles=None, dry_run=False, target="host-authority"
    )

    assert answer["ok"] is False and answer["state"] == "refused"
    assert answer["code"] == "host-authority-not-in-user-settings"
    assert config.config_path.as_posix() in answer["detail"]
    assert not _user_settings_in_tmp.exists(), "no user settings file is created for a host block"
