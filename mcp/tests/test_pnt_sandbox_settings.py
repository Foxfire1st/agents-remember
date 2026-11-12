"""The PNT sandbox's own settings owner prepares and checks one installation's settings.

MIK-R196 rule 9 retired the harness starter renderer, and the sandbox used to reach its settings
behaviour through that renderer. The behaviour itself is real: a sandbox is one ordinary
installation, so its settings differ from a template's own default only in the recorded
coordination root and dashboard port, and its own settings file carries the ``paseoRuntime`` block
the product establishes (rule 3) while the developer's user settings file stays an input that is
never written or created. These cases run that behaviour through the sandbox's owner, which asks
the product for the host block and the user location instead of importing a retired renderer.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY_ROOT = Path(__file__).parents[2]
OWNER = REPOSITORY_ROOT / "scripts/pnt_sandbox/settings_expectation.py"

TEMPLATE = {
    "version": 1,
    "coordinationRoot": "<PATH/TO/YOUR/PROJECTS_FOLDER>/ar-coordination",
    "workspaceRoot": "<PATH/TO/YOUR/PROJECTS_FOLDER>",
    "transcriptRoot": "<PATH/TO/YOUR/PROJECTS_FOLDER>/ar-coordination/logs/mcp",
    "repositories": {"<YOUR_REPOSITORY_FOLDER_NAME>": {}},
    "providers": {"codegraphcontext-code": {}, "grepai-memory": {}},
    "timeoutCaps": {"toolSeconds": 60, "providerSetupSeconds": 1800},
    "dashboard": {"autoStart": False, "port": 8765},
}


def owner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pnt_settings_expectation", OWNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A workspace with one repository, and the settings file a template leaves there."""
    projects = tmp_path / "projects"
    (projects / "sandbox-app").mkdir(parents=True)
    coordination = tmp_path / "coordination"
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps(TEMPLATE), encoding="utf-8")
    return projects, coordination, settings


def prepare(tmp_path: Path, monkeypatch) -> tuple[dict, Path, Path]:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    projects, coordination, settings = workspace(tmp_path)
    receipt = owner().check(settings, projects, coordination, 9871, "sandbox-app")
    return receipt, coordination, settings


def test_prepared_settings_differ_only_in_recorded_root_and_port(tmp_path, monkeypatch) -> None:
    receipt, coordination, settings = prepare(tmp_path, monkeypatch)

    prepared = json.loads(settings.read_text(encoding="utf-8"))
    assert prepared["coordinationRoot"] == str(coordination.resolve())
    assert prepared["transcriptRoot"] == str((coordination / "logs/mcp").resolve())
    assert prepared["workspaceRoot"] == str((tmp_path / "projects").resolve())
    assert prepared["dashboard"] == {"autoStart": False, "port": 9871}
    assert prepared["repositories"] == {"sandbox-app": {}}
    assert prepared["timeoutCaps"] == TEMPLATE["timeoutCaps"]
    # MIK-R196 rule 3: the sandbox's own settings file is its host authority, so it carries the
    # block the product establishes -- compared on its own, not counted as a settings difference.
    assert prepared["paseoRuntime"]["listen"] == "127.0.0.1:8766"
    assert receipt["hostDifferenceKeys"] == []
    assert set(receipt["settingsDifferenceKeys"]) == {
        "coordinationRoot",
        "transcriptRoot",
        "dashboard",
    }
    assert receipt["invocations"] == 1
    assert receipt["inputSettingsSha256"] != receipt["preparedSettingsSha256"]


def test_absent_user_settings_file_stays_absent_and_the_sandbox_holds_the_block(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    receipt, _, settings = prepare(tmp_path, monkeypatch)

    shared = Path(receipt["sharedInputPath"])
    assert shared == tmp_path / "config/agents-remember/settings.json"
    # MIK-R137: the sandbox never creates or writes the developer's own settings file.
    assert not shared.exists()
    assert receipt["sharedBeforeSha256"] is None
    assert receipt["sharedAfterSha256"] is None
    block = json.loads(settings.read_text(encoding="utf-8"))["paseoRuntime"]
    assert block["installPrefix"] == str(tmp_path / "data/agents-remember/paseo/prefix")
    assert block["home"] == str(tmp_path / "state/agents-remember/paseo")
    assert block["providers"] == {}
    assert [entry["dashboardOrigin"] for entry in block["embed"]] == [
        "http://127.0.0.1:9871",
        "http://localhost:9871",
    ]
    assert receipt["hostDifferenceKeys"] == []


def test_existing_shared_host_file_is_untouched_and_recorded(tmp_path, monkeypatch) -> None:
    configured = tmp_path / "config/agents-remember/settings.json"
    configured.parent.mkdir(parents=True)
    document = {
        "version": 1,
        "paseoRuntime": {
            "installPrefix": "/opt/developer/paseo/prefix",
            "home": "/home/developer/.local/state/agents-remember/paseo",
            "listen": "127.0.0.1:9786",
            "providers": {"codegraphcontext-code": {"enabled": True}},
            "embed": [
                {
                    "dashboardOrigin": "http://127.0.0.1:9099",
                    "frameBaseUrl": "http://127.0.0.1:9786",
                }
            ],
        },
    }
    configured.write_text(json.dumps(document), encoding="utf-8")
    before = configured.read_bytes()

    receipt, _, _ = prepare(tmp_path, monkeypatch)

    assert configured.read_bytes() == before
    assert receipt["sharedBeforeSha256"] == receipt["sharedAfterSha256"]
    # The developer's configured block is an input the sandbox never reads as its own authority:
    # under MIK-R196 rule 3 the sandbox's settings file carries the product's fresh block, so the
    # block differs from the product default in nothing and the network entry is not copied.
    assert receipt["hostDifferenceKeys"] == []
    assert receipt["hostDifferences"] == []
    assert receipt["providerIds"] == []


def test_repository_outside_the_workspace_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    projects, coordination, settings = workspace(tmp_path)

    with pytest.raises(ValueError, match="folder name under workspace root"):
        owner().check(settings, projects, coordination, 9871, "../elsewhere")
    with pytest.raises(ValueError, match="does not exist"):
        owner().check(settings, projects, coordination, 9871, "absent-app")


def test_existing_native_authority_values_are_preserved(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    projects, coordination, settings = workspace(tmp_path)
    configured = {
        "installPrefix": str(tmp_path / "native/prefix"),
        "home": str(tmp_path / "native/home"),
        "listen": "127.0.0.1:9986",
        "version": "configured-build",
        "providers": {
            "codex": {"command": "/native/codex", "args": [], "env": {"TOKEN": "fixture-private"}}
        },
        "embed": [
            {"dashboardOrigin": "http://localhost:9985", "frameBaseUrl": "http://localhost:9986"}
        ],
    }
    settings.write_text(json.dumps({**TEMPLATE, "paseoRuntime": configured}), encoding="utf-8")

    receipt = owner().check(settings, projects, coordination, 9871, "sandbox-app")

    actual = json.loads(settings.read_text(encoding="utf-8"))
    assert actual["paseoRuntime"] == configured
    assert receipt["preparedSettings"]["paseoRuntime"] == {
        key: value for key, value in configured.items() if key != "providers"
    }
    assert "fixture-private" not in json.dumps(receipt)
    assert receipt["providerIds"] == ["codex"]
    assert actual["coordinationRoot"] == str(coordination.resolve())
    assert actual["dashboard"]["port"] == 9871


def test_invalid_existing_authority_is_refused_without_replacing_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    projects, coordination, settings = workspace(tmp_path)
    settings.write_text(json.dumps({**TEMPLATE, "paseoRuntime": None}), encoding="utf-8")
    before = settings.read_bytes()

    with pytest.raises(ValueError, match="existing paseoRuntime"):
        owner().check(settings, projects, coordination, 9871, "sandbox-app")

    assert settings.read_bytes() == before


def test_unapproved_settings_or_host_difference_is_refused() -> None:
    module = owner()

    with pytest.raises(ValueError, match="unapproved settings differences"):
        module.unapproved(
            ["coordinationRoot", "providers"], module.SETTINGS_DIFFERENCE_REASONS, "settings"
        )
    with pytest.raises(ValueError, match="unapproved host differences"):
        module.unapproved(["listen", "version"], module.HOST_DIFFERENCE_REASONS, "host")


def test_old_user_settings_location_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    projects, coordination, settings = workspace(tmp_path)
    old = coordination / "system" / "settings.json"
    old.parent.mkdir(parents=True)
    old.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="agentic-settings-location-upgrade-required"):
        owner().check(settings, projects, coordination, 9871, "sandbox-app")
    assert not (tmp_path / "config/agents-remember/settings.json").exists()
