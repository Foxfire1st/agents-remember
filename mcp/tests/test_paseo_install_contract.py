from __future__ import annotations

import json
from unittest.mock import patch

from agents_remember.application.runtime.install import RuntimeInstallRequest, run_runtime_install
from agents_remember.kernel.primitives.paseo_host_contract import PASEO_VERSION
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.mcp.tools.core import runtime_install_payload
from agents_remember.serving.paseo import paseo_install
from agents_remember.serving.paseo.paseo_provision import provision_for_install
from paseo_runtime_test_support import FakePaseo, file_states, free, runtime_settings


def setup_config(root):
    config_file = root / "settings.json"
    config_file.write_text(
        json.dumps(
            {
                "coordinationRoot": str(root / "coordination"),
                "workspaceRoot": str(root / "projects"),
                "providers": {},
            }
        )
    )
    return load_config(config_file)


def write_host(root, version_marker):
    settings = runtime_settings(root)
    block = {
        "installPrefix": str(settings.install_prefix),
        "home": str(settings.home),
        "listen": settings.listen,
        "providers": settings.providers,
        "embed": settings.embed_payload(),
    }
    if version_marker is not None:
        block["version"] = version_marker
    path = root / "coordination/system/settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"paseoRuntime": block}))
    return settings


def test_install_reads_added_shared_block_at_call_and_provisions_once(tmp_path):
    config = setup_config(tmp_path)
    assert config.paseo_runtime is None
    settings = write_host(tmp_path, "latest")
    fake = FakePaseo(settings)

    def provision(actual):
        return provision_for_install(actual, runner=fake, reader=fake.reader, probe=free)

    previous = {"ok": True, "summary": {"copiedFiles": 5}}
    with (
        patch(
            "agents_remember.application.runtime.install.install_runtime_from_config",
            side_effect=lambda *args: previous.copy(),
        ),
        patch.object(paseo_install, "provision_for_install", side_effect=provision) as call,
    ):
        result = runtime_install_payload(config, RuntimeInstallRequest())
        before = file_states(tmp_path)
        at = len(fake.calls)
        second = run_runtime_install(config, RuntimeInstallRequest())
        second_mutations = fake.mutations(at)
        second_files = file_states(tmp_path)
        shared = tmp_path / "coordination/system/settings.json"
        document = json.loads(shared.read_text())
        document["paseoRuntime"]["providers"]["new-provider"] = {"extends": "acp"}
        shared.write_text(json.dumps(document))
        update_at = len(fake.calls)
        updated = runtime_install_payload(config, RuntimeInstallRequest())
    assert call.call_count == 3
    assert not second["host"]["changed"]
    assert not second_mutations and second_files == before
    assert result["summary"] == previous["summary"]
    assert result["host"]["version"] == PASEO_VERSION
    assert "latest" in result["host"]["notice"]
    assert "system/settings.json" in result["host"]["notice"]
    assert fake.mutations().count(("npm", "ci")) == 1
    assert {
        "ok",
        "changed",
        "home",
        "installPrefix",
        "version",
        "listen",
        "daemon",
        "changes",
        "error",
        "node",
    } <= set(result["host"])
    assert {"version", "platform", "path", "url", "sha256"} <= set(result["host"]["node"])
    assert ("daemon", "reload") in fake.mutations(update_at)
    assert ("daemon", "stop") not in fake.mutations(update_at)
    assert ("daemon", "start") not in fake.mutations(update_at)
    assert "new-provider" in json.dumps(updated["host"]["changes"])
    assert "pnt-provider-value" not in json.dumps(updated)


def test_preview_and_absent_host_make_no_mutation_and_host_error_preserves_earlier_steps(tmp_path):
    config = setup_config(tmp_path)
    with patch(
        "agents_remember.application.runtime.install.install_runtime_from_config",
        return_value={"ok": True, "summary": {"copiedFiles": 5}},
    ):
        result = run_runtime_install(config, RuntimeInstallRequest(dry_run=True))
    assert not result["host"]["configured"]
    assert not (tmp_path / "coordination/system/settings.json").exists()
    settings = write_host(tmp_path, None)
    fake = FakePaseo(settings)
    before = file_states(tmp_path)
    preview_call = paseo_install.preview_host
    with (
        patch(
            "agents_remember.application.runtime.install.install_runtime_from_config",
            return_value={"ok": True},
        ),
        patch.object(
            paseo_install,
            "preview_host",
            side_effect=lambda actual: preview_call(actual, runner=fake, reader=fake.reader),
        ) as dispatched,
        patch.object(
            paseo_install,
            "provision_for_install",
            side_effect=AssertionError("preview provisioned"),
        ),
    ):
        preview = runtime_install_payload(config, RuntimeInstallRequest(dry_run=True))["host"]
    assert dispatched.call_count == 1
    assert preview["wouldInstallHost"] and preview["wouldStart"]
    assert fake.mutations() == []
    assert {
        path: state for path, state in file_states(tmp_path).items() if path in before
    } == before
    with (
        patch(
            "agents_remember.application.runtime.install.install_runtime_from_config",
            return_value={"ok": True, "summary": {"copiedFiles": 5}},
        ),
        patch.object(
            paseo_install,
            "provision_for_install",
            return_value={
                "ok": False,
                "error": {
                    "code": "install_failed",
                    "step": "install",
                    "message": "npm failed",
                    "detail": None,
                },
            },
        ),
    ):
        failed = runtime_install_payload(config, RuntimeInstallRequest())
    assert not failed["ok"] and failed["summary"]["copiedFiles"] == 5
    assert failed["host"]["error"]["code"] == "install_failed"


def test_build_pin_wins_all_settings_selectors_and_legacy_harness_block_is_ignored(tmp_path):
    config = setup_config(tmp_path)
    for selector in (None, PASEO_VERSION, "0.10.0", "*"):
        settings = write_host(tmp_path / str(selector), selector)
        variant = setup_config(tmp_path / str(selector))
        fake = FakePaseo(settings)
        actual = variant.paseo_runtime
        assert actual is not None
        assert actual.version == PASEO_VERSION
        report = provision_for_install(actual, runner=fake, reader=fake.reader, probe=free)
        assert report["ok"] and report["version"] == PASEO_VERSION
        assert fake.version_at(settings.install_prefix) == PASEO_VERSION
        if selector not in (None, PASEO_VERSION):
            assert actual.version_notice and str(selector) in actual.version_notice
    shared = tmp_path / "coordination/system/settings.json"
    shared.unlink(missing_ok=True)
    document = json.loads(config.config_path.read_text())
    document["paseoRuntime"] = {"listen": "127.0.0.1:9786"}
    config.config_path.write_text(json.dumps(document))
    with __import__("pytest").warns(UserWarning, match="ignored; move that block"):
        assert load_config(config.config_path).paseo_runtime is None
