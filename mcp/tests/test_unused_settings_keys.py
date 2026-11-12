"""Settings keys that only removed parts read (MIK-R113 rule 4).

A settings key that only a removed part read is no longer read. A settings file that
still holds such a key loads without an error, and the product names the key once as no
longer used: ``server_info`` answers the dotted names. The product does not change the
user's file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _user_settings_guard import user_settings_path_for_write
from agents_remember.kernel.agentic_settings import load_agentic_settings
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.mcp import SERVER_VERSION
from agents_remember.mcp.tools.core import server_info_payload
from agents_remember.serving.build_info import ServingBuild
from test_config import settings_payload

RETIREMENT_KEYS = {
    "autoLandOnIntegration": False,
    "autoLandOnFinalize": True,
    "autoCloseCompletedSeats": False,
    "autoRetireOnIntegration": True,
    "autoRetireOnFinalize": False,
}

ORCHESTRATION_WITH_REMOVED_KEYS = {
    "agentNotifier": {"enabled": False, "intervalSeconds": 3},
    "expectations": {"defaults": {"briefed-by": 60}},
    "harnesses": {"custom": {"command": "custom-agent", "argv": ["custom-agent", "--tui"]}},
    "roles": {"worker": {"harness": "codex", "launchArgs": ["--fast"]}},
    "rolesPerLevel": {"leaf": {"reviewer": {"sessionCommands": ["/effort high"]}}},
    "spawn": {"harness": "custom"},
}

EXPECTED_ORCHESTRATION_NAMES = [
    "orchestration.agentNotifier",
    "orchestration.expectations",
    "orchestration.harnesses.custom.argv",
    "orchestration.harnesses.custom.command",
    "orchestration.roles.worker.launchArgs",
    "orchestration.rolesPerLevel.leaf.reviewer.sessionCommands",
]


def _write(path: Path, data: dict[str, object]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2)
    path.write_text(text, encoding="utf-8")
    return text


def _build() -> ServingBuild:
    return ServingBuild(
        version=SERVER_VERSION,
        commit="abc1234",
        booted_at="2026-10-12T00:00:00Z",
        source_digest="sha256:" + "a" * 64,
        python_executable="/runtime/bin/python",
        package_root="/runtime/agents_remember",
    )


def test_a_file_with_the_retirement_keys_loads_and_each_key_is_named(tmp_path: Path) -> None:
    path = tmp_path / "mcp-settings.json"
    written = _write(path, {**settings_payload(tmp_path), "retirement": RETIREMENT_KEYS})

    config = load_config(path)

    assert config.unused_settings_keys == tuple(
        f"retirement.{key}" for key in sorted(RETIREMENT_KEYS)
    )
    assert not hasattr(config, "retirement")
    assert path.read_text(encoding="utf-8") == written


@pytest.mark.parametrize(
    ("held", "named"),
    [
        (
            {"autoLandOnIntegration": "not-a-boolean", "unknownKey": 7},
            ("retirement.autoLandOnIntegration", "retirement.unknownKey"),
        ),
        ({}, ("retirement",)),
        ("a string", ("retirement",)),
    ],
)
def test_the_section_is_not_read_so_no_value_in_it_is_checked(
    tmp_path: Path, held: object, named: tuple[str, ...]
) -> None:
    path = tmp_path / "mcp-settings.json"
    _write(path, {**settings_payload(tmp_path), "retirement": held})

    assert load_config(path).unused_settings_keys == named


def test_a_file_without_removed_keys_names_none(tmp_path: Path) -> None:
    path = tmp_path / "mcp-settings.json"
    _write(path, settings_payload(tmp_path))
    config = load_config(path)

    assert config.unused_settings_keys == ()
    assert load_agentic_settings(config.coordination_root).unused_keys == ()
    assert server_info_payload(config, _build().payload())["unusedSettingsKeys"] == []


def test_server_info_names_the_keys_of_both_settings_families_once(tmp_path: Path) -> None:
    path = tmp_path / "mcp-settings.json"
    _write(path, {**settings_payload(tmp_path), "retirement": {"autoLandOnFinalize": True}})
    config = load_config(path)
    user_settings = user_settings_path_for_write()
    written = _write(user_settings, {"orchestration": ORCHESTRATION_WITH_REMOVED_KEYS})

    settings = load_agentic_settings(config.coordination_root)
    payload = server_info_payload(config, _build().payload())

    assert list(settings.unused_keys) == EXPECTED_ORCHESTRATION_NAMES
    assert payload["unusedSettingsKeys"] == [
        "retirement.autoLandOnFinalize",
        *EXPECTED_ORCHESTRATION_NAMES,
    ]
    assert len(payload["unusedSettingsKeys"]) == len(set(payload["unusedSettingsKeys"]))
    # What stays is still read beside the keys that are not.
    assert settings.spawn_harness == "custom"
    assert settings.harness_ids[-1] == "custom"
    assert settings.resolved_role_knobs("worker").harness == "codex"
    assert user_settings.read_text(encoding="utf-8") == written


def test_server_info_answers_when_the_orchestration_settings_cannot_be_read(tmp_path: Path) -> None:
    """The refusal stays with the tool that needs those settings."""
    path = tmp_path / "mcp-settings.json"
    _write(path, {**settings_payload(tmp_path), "retirement": {"autoLandOnFinalize": True}})
    config = load_config(path)
    _write(user_settings_path_for_write(), {"orchestration": {"notAKnownFamily": {}}})

    payload = server_info_payload(config, _build().payload())

    assert payload["ok"] is True
    assert payload["unusedSettingsKeys"] == ["retirement.autoLandOnFinalize"]
