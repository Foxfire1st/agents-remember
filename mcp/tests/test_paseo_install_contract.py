from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from _user_settings_guard import user_settings_path_for_write
from agents_remember.application.runtime.install import RuntimeInstallRequest, run_runtime_install
from agents_remember.kernel.primitives.paseo_host_contract import PASEO_VERSION
from agents_remember.kernel.primitives.paseo_node_paths import product_node
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.mcp.tools.core import runtime_install_payload
from agents_remember.serving.paseo import paseo_install
from agents_remember.serving.paseo.paseo_daemon_config import write_provider_entries
from agents_remember.serving.paseo.paseo_plugin_files import PLUGIN_ID, installed_plugin_path
from agents_remember.serving.paseo.paseo_provision import provision_for_install
from agents_remember.serving.paseo.paseo_settings import daemon_settings
from paseo_runtime_test_support import FakePaseo, file_states, free, runtime_settings, write_plugin


def setup_config(root):
    root.mkdir(parents=True, exist_ok=True)
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


def write_host(config, version_marker):
    settings = runtime_settings(config.config_path.parent)
    block = {
        "installPrefix": str(settings.install_prefix),
        "home": str(settings.home),
        "listen": settings.listen,
        "providers": settings.providers,
        "embed": settings.embed_payload(),
    }
    if version_marker is not None:
        block["version"] = version_marker
    document = json.loads(config.config_path.read_text())
    document["paseoRuntime"] = block
    config.config_path.write_text(json.dumps(document))
    return settings


def test_install_reads_added_shared_block_at_call_and_provisions_once(tmp_path):
    config = setup_config(tmp_path)
    assert config.paseo_runtime is None
    settings = write_host(config, "latest")
    fake = FakePaseo(settings)

    def provision(actual):
        return provision_for_install(actual, runner=fake, reader=fake.reader, probe=free)

    previous = {"ok": True, "summary": {"copiedFiles": 5}}
    report_dir = config.coordination_root / "temp" / "tool-reports" / "runtime_install"
    with (
        patch(
            "agents_remember.application.runtime.install.install_runtime_from_config",
            side_effect=lambda *args: previous.copy(),
        ),
        patch.object(paseo_install, "provision_for_install", side_effect=provision) as call,
    ):
        filed_before = set(report_dir.glob("*-install*.json"))
        result = runtime_install_payload(config, RuntimeInstallRequest())
        # Exactly one report is filed by this first install; the later
        # `updated` call files another, so capture this one here.
        first_filed = sorted(set(report_dir.glob("*-install*.json")) - filed_before)
        before = file_states(tmp_path)
        at = len(fake.calls)
        second = run_runtime_install(config, RuntimeInstallRequest())
        second_mutations = fake.mutations(at)
        second_files = file_states(tmp_path)
        shared = config.config_path
        document = json.loads(shared.read_text())
        document["paseoRuntime"]["providers"]["new-provider"] = {"extends": "acp"}
        shared.write_text(json.dumps(document))
        update_at = len(fake.calls)
        updated = runtime_install_payload(config, RuntimeInstallRequest())
    assert call.call_count == 3
    assert not second["host"]["changed"]
    assert not second_mutations and second_files == before
    # The first provisioned install is over the answer bound, so since L112
    # (976bc39e: MIK-R112 rule 9 projects the answer onto its row, MIK-R108
    # rule 4 guards it) the delivered answer is the overflow short form. The
    # whole install — summary, host, node — lies in the report the answer names
    # in `reportPath`, which is where the test reads it.
    assert result["ok"]
    assert result["overflow"] is True
    # The overflow short form sheds strings over 200 chars, so under a long
    # test root the answer no longer names `reportPath`; the first install's
    # report is still filed under the coordination root, so derive the
    # pointer: the answer's when present, else the newly filed report.
    assert len(first_filed) == 1, f"expected one new install report, saw {first_filed}"
    if "reportPath" in result:
        assert Path(result["reportPath"]) == first_filed[0]
    whole = json.loads(first_filed[0].read_text(encoding="utf-8"))
    assert whole["summary"] == previous["summary"]
    assert whole["host"]["version"] == PASEO_VERSION
    assert "latest" in whole["host"]["notice"]
    assert str(config.config_path) in whole["host"]["notice"]
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
    } <= set(whole["host"])
    assert {"version", "platform", "path", "url", "sha256"} <= set(whole["host"]["node"])
    assert ("daemon", "reload") in fake.mutations(update_at)
    assert ("daemon", "stop") not in fake.mutations(update_at)
    assert ("daemon", "start") not in fake.mutations(update_at)
    # The updated call's answer is bounded the same way: under a long root
    # it sheds the whole `host` dict, so the update facts come from the
    # updated call's own filed report — the retained whole-result path —
    # with the answer's `ok` kept as the delivered-outcome check.
    assert updated["ok"] is True
    updated_filed = sorted(
        set(report_dir.glob("*-install*.json")) - filed_before - set(first_filed)
    )
    assert len(updated_filed) == 1, f"expected one updated report, saw {updated_filed}"
    if "reportPath" in updated:
        assert Path(updated["reportPath"]) == updated_filed[0]
    updated_whole = json.loads(updated_filed[0].read_text(encoding="utf-8"))
    assert "new-provider" in json.dumps(updated_whole["host"]["changes"])
    assert "pnt-provider-value" not in json.dumps(updated_whole)
    assert "pnt-provider-value" not in json.dumps(updated)


def test_preview_and_absent_host_make_no_mutation_and_host_error_preserves_earlier_steps(tmp_path):
    config = setup_config(tmp_path)
    with patch(
        "agents_remember.application.runtime.install.install_runtime_from_config",
        return_value={"ok": True, "summary": {"copiedFiles": 5}},
    ):
        result = run_runtime_install(config, RuntimeInstallRequest(dry_run=True))
    assert not result["host"]["configured"]
    assert not (user_settings_path_for_write()).exists()
    settings = write_host(config, None)
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
        seen = dispatched.call_count
        preview_answer = runtime_install_payload(config, RuntimeInstallRequest(dry_run=True))
        # A dry run files no report and a direct call writes no log, so the
        # bounded dry-run answer is the only retained object — and under a
        # long root its short form sheds `host`. The preview flags come from
        # the same preview computation via the unshaped application result;
        # each of the two preview calls dispatches exactly once.
        assert preview_answer["ok"] is True
        preview = run_runtime_install(config, RuntimeInstallRequest(dry_run=True))["host"]
        assert dispatched.call_count == seen + 2
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
        variant = setup_config(tmp_path / str(selector))
        settings = write_host(variant, selector)
        fake = FakePaseo(settings)
        actual = variant.paseo_runtime
        assert actual is not None
        assert actual.version == PASEO_VERSION
        report = provision_for_install(actual, runner=fake, reader=fake.reader, probe=free)
        assert report["ok"] and report["version"] == PASEO_VERSION
        assert fake.version_at(settings.install_prefix) == PASEO_VERSION
        if selector not in (None, PASEO_VERSION):
            assert actual.version_notice and str(selector) in actual.version_notice
    legacy = user_settings_path_for_write()
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"paseoRuntime": {"listen": "127.0.0.1:9786"}}))
    # L188 moved the host authority to the MCP settings file: the old user-settings block is
    # ignored, not read, and the authority file remains the only source of `paseoRuntime`.
    assert load_config(config.config_path).paseo_runtime is None


def preview_matches_real(settings, fake, source, root):
    """Compare the entire ordered host action list, and prove the preview only observed."""
    before = file_states(root)
    daemon = fake.daemon
    at = len(fake.calls)
    with patch.object(paseo_install, "plugin_source_root", return_value=source):
        preview = paseo_install.preview_host(settings, runner=fake, reader=fake.reader)
    assert file_states(root) == before
    assert fake.daemon is daemon and fake.mutations(at) == []
    real = provision_for_install(
        settings, runner=fake, reader=fake.reader, plugin_source=source, probe=free
    )
    assert real["ok"], real
    assert preview["changes"] == real["changes"]
    assert preview["changed"] == real["changed"]
    return preview, real


def test_host_preview_lists_first_install_settings_destinations_and_start(tmp_path):
    settings = runtime_settings(tmp_path)
    fake = FakePaseo(settings)
    source = write_plugin(tmp_path, "initial")
    preview, _real = preview_matches_real(settings, fake, source, tmp_path)
    actions = preview["changes"]
    assert {
        "step": "config",
        "action": "set",
        "path": "daemon.relay.enabled",
        "value": False,
        "applies": "live",
    } in actions
    assert any(item["step"] == "embed" and item["path"].endswith("embed.json") for item in actions)
    assert any(
        item["step"] == "plugin"
        and item["action"] == "copied"
        and item["path"] == installed_plugin_path(settings.home).as_posix()
        for item in actions
    )
    assert any(item["step"] == "daemon" and item["action"] == "started" for item in actions)
    assert any(item["step"] == "plugin" and item["action"] == "installed" for item in actions)
    assert "pnt-provider-value" not in json.dumps(preview)
    repeat, _ = preview_matches_real(settings, fake, source, tmp_path)
    assert repeat["changes"] == []


def test_host_preview_lists_setting_only_and_provider_reload_without_secret(tmp_path):
    settings = runtime_settings(tmp_path)
    fake = FakePaseo(settings)
    source = write_plugin(tmp_path, "initial")
    preview_matches_real(settings, fake, source, tmp_path)
    fake.write("daemon.relay.enabled", True)
    preview, real = preview_matches_real(settings, fake, source, tmp_path)
    assert preview["changes"] == [
        {
            "step": "config",
            "action": "set",
            "path": "daemon.relay.enabled",
            "value": False,
            "applies": "live",
        }
    ]
    assert real["daemon"]["action"] == "reloaded"
    changed = replace(
        settings, providers={"custom": {"extends": "acp", "env": {"KEY": "private-value"}}}
    )
    preview, _ = preview_matches_real(changed, fake, source, tmp_path)
    assert preview["changes"] == [
        {
            "step": "config",
            "action": "set",
            "path": "agents.providers",
            "providerIds": ["custom"],
            "applies": "live",
        },
        {"step": "config", "action": "reloaded", "path": "agents.providers"},
    ]
    assert "private-value" not in json.dumps(preview)


def test_host_preview_matches_plugin_copy_reload_enable_and_replacement(tmp_path):
    settings = runtime_settings(tmp_path)
    fake = FakePaseo(settings)
    source = write_plugin(tmp_path, "initial")
    preview_matches_real(settings, fake, source, tmp_path)
    write_plugin(tmp_path, "changed")
    changed = replace(settings, embed=())
    preview, _ = preview_matches_real(changed, fake, source, tmp_path)
    assert [item["action"] for item in preview["changes"]] == ["written", "copied", "reloaded"]
    fake.plugins[PLUGIN_ID]["enabled"] = False
    preview, _ = preview_matches_real(changed, fake, source, tmp_path)
    assert preview["changes"] == [{"step": "plugin", "action": "enabled"}]
    wrong = (tmp_path / "earlier-plugin").as_posix()
    fake.plugins[PLUGIN_ID]["path"] = wrong
    preview, _ = preview_matches_real(changed, fake, source, tmp_path)
    assert preview["changes"] == [
        {"step": "plugin", "action": "removed", "path": wrong},
        {"step": "plugin", "action": "installed"},
    ]


def test_host_preview_names_deferred_transition_without_promising_actions(tmp_path):
    settings = runtime_settings(tmp_path)
    fake = FakePaseo(settings)
    source = write_plugin(tmp_path, "initial")
    preview_matches_real(settings, fake, source, tmp_path)
    fake.write("features.webUi.enabled", False)
    preview, real = preview_matches_real(settings, fake, source, tmp_path)
    assert preview["changes"] == [] and not preview["changed"]
    assert (
        preview["restartRequired"] == real["restartRequired"] == ["setting:features.webUi.enabled"]
    )
    assert preview["deferred"][0]["action"] == "restart"
    assert "preserves the running host" in preview["deferred"][0]["reason"]
    assert not preview["wouldInstallHost"] and not preview["wouldStart"]


def test_host_preview_matches_stopped_recovery_record_and_install_cleanup(tmp_path):
    settings = runtime_settings(tmp_path)
    fake = FakePaseo(settings)
    source = write_plugin(tmp_path, "initial")
    fake.install(settings.install_prefix, settings.version)
    fake.write_settings(daemon_settings(settings))
    write_provider_entries(settings.home, {"incomplete": {"extends": "acp"}})
    fake.record(9999)
    staging = settings.install_prefix / ".ar-staging"
    staging.mkdir()
    preview, _ = preview_matches_real(settings, fake, source, tmp_path)
    assert preview["changes"][:3] == [
        {"step": "config", "action": "restored", "path": "agents.providers", "restored": "file"},
        {
            "step": "daemon",
            "action": "removed-stale-record",
            "pid": 9999,
            "reason": "the recorded process no longer exists",
        },
        {"step": "install", "action": "removed-leftover", "path": staging.as_posix()},
    ]


def test_host_preview_observes_existing_cli_before_node_acquisition_and_cleanup(tmp_path):
    settings = runtime_settings(tmp_path)
    fake = FakePaseo(settings)
    source = write_plugin(tmp_path, "initial")
    fake.install(settings.install_prefix, settings.version)
    fake.write_settings(daemon_settings(settings))
    node = product_node()
    shutil.rmtree(node.root)

    def acquire_node(_runner):
        for path in (node.node, node.npm):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        return {**node.payload(), "changed": True}

    with patch(
        "agents_remember.serving.paseo.paseo_provision.ensure_node", side_effect=acquire_node
    ):
        preview, _ = preview_matches_real(settings, fake, source, tmp_path)
    assert preview["changes"][0] == {
        "step": "node",
        "action": "installed",
        "version": node.version,
        "path": node.node.as_posix(),
    }
    assert not any(item["step"] == "install" for item in preview["changes"])
    archive = node.cache / f"{node.root.name}.tar.xz.partial"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b"partial archive")
    staging = node.root.with_name(f".{node.root.name}.unpack")
    staging.mkdir()
    preview, real = preview_matches_real(settings, fake, source, tmp_path)
    assert preview["changes"] == [
        {"step": "node", "action": "removed-leftover", "path": archive.as_posix()},
        {"step": "node", "action": "removed-leftover", "path": staging.as_posix()},
    ]
    assert preview["daemon"] == real["daemon"] == {"action": "untouched", "reasons": []}
