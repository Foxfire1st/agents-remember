from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from _user_settings_guard import user_settings_path_for_write
from agents_remember.application.runtime.install import RuntimeInstallRequest, run_runtime_install
from agents_remember.cli.paseo_status import AgentReading
from agents_remember.cli.role_launch_receipts import RECEIPT_SCHEMA, _write_receipt
from agents_remember.cli.shared_held_store_reconciliation import reconcile_shared_held_stores
from agents_remember.controlplane.held_store_discovery import receipt_holder
from agents_remember.controlplane.held_store_layout import HELD_STORE_SCHEMA, store_path_for
from agents_remember.kernel.file_lock import lock_held, lock_path_for
from agents_remember.kernel.primitives.paseo_host_contract import PASEO_VERSION
from agents_remember.kernel.primitives.paseo_node_paths import product_node
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.mcp.tools.core import runtime_install_payload
from agents_remember.models.build_currentness import BuildCurrentnessPayload
from agents_remember.models.core import RunningServerPayload
from agents_remember.serving.build_info import ServingBuild
from agents_remember.serving.paseo import paseo_install
from agents_remember.serving.paseo.paseo_daemon_config import write_provider_entries
from agents_remember.serving.paseo.paseo_plugin_files import PLUGIN_ID, installed_plugin_path
from agents_remember.serving.paseo.paseo_provision import provision_for_install
from agents_remember.serving.paseo.paseo_settings import daemon_settings
from agents_remember.serving.server_registry import _ServerRecord
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


def _shared_store(config, status, *, text=None, replay=None, lock_only=False):
    agent_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    receipt = receipt_holder(config.coordination_root) / f"{request_id}.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema": RECEIPT_SCHEMA,
        "requestId": request_id,
        "agentId": agent_id,
        "status": status,
        "workspace": {"path": str(config.workspace_root)},
        "execution": {"agentId": agent_id},
    }
    if replay is not None:
        record["replayRequest"] = replay
    receipt.write_text(json.dumps(record))
    store = store_path_for(config.workspace_root, agent_id)
    store.parent.mkdir(parents=True, exist_ok=True)
    if not lock_only:
        messages = (
            []
            if text is None
            else [
                {
                    "id": str(uuid.uuid4()),
                    "senderAgentId": str(uuid.uuid4()),
                    "senderLine": "From worker",
                    "takenAt": "2026-11-24T00:00:00Z",
                    "reason": "held",
                    "text": text,
                }
            ]
        )
        store.write_text(
            json.dumps({"schema": HELD_STORE_SCHEMA, "agentId": agent_id, "messages": messages})
        )
    lock = lock_path_for(store)
    lock.touch()
    return agent_id, receipt, store, lock


def test_runtime_install_opt_in_previews_and_settles_only_proven_closed_shared_stores(tmp_path):
    config = setup_config(tmp_path)
    closed = _shared_store(config, "rejected", text="waiting until the seat closed")
    gone = _shared_store(config, "running", text="waiting until the host went away")
    archived = _shared_store(config, "held", text="waiting until archival", replay={})
    live = _shared_store(config, "stopped", text="still waiting for the live seat")
    unknown = _shared_store(config, "stopped", text="still waiting for a host reading")
    deferred = _shared_store(config, "held", replay={"launch": "pending"})
    lock_only = _shared_store(config, "rejected", lock_only=True)
    readings = {
        gone[0]: AgentReading(found=False),
        archived[0]: AgentReading(archived=True),
        live[0]: AgentReading(lifecycle="idle", last_turn="unreplied"),
        unknown[0]: AgentReading(
            reachable=False, unreachable_reason="The host did not answer for this seat."
        ),
        deferred[0]: AgentReading(found=False),
    }
    build = ServingBuild("test", None, "2026-11-24T00:00:00Z", source_digest="a" * 64)
    before = file_states(tmp_path)
    with (
        patch(
            "agents_remember.application.runtime.install.install_runtime_from_config",
            side_effect=lambda *_: {"ok": True},
        ),
        patch(
            "agents_remember.application.runtime.install.install_host", return_value={"ok": True}
        ),
        patch(
            "agents_remember.application.runtime.install.plan_running_dashboard", return_value=None
        ),
        patch(
            "agents_remember.application.runtime.install.ensure_running_dashboard",
            return_value=None,
        ),
        patch("agents_remember.application.runtime.install.server_inventory", return_value=[]),
        patch(
            "agents_remember.cli.shared_held_store_reconciliation.process_serving_build",
            return_value=build,
        ),
        patch(
            "agents_remember.cli.shared_held_store_reconciliation.read_agent",
            side_effect=lambda _, agent_id: readings[agent_id],
        ) as read,
    ):
        ordinary = run_runtime_install(config, RuntimeInstallRequest(dry_run=True))
        assert "sharedHeldStores" not in ordinary
        assert not read.called
        preview = run_runtime_install(
            config,
            RuntimeInstallRequest(dry_run=True, reconcile_shared_held_stores=True),
            reconcile_shared_stores=reconcile_shared_held_stores,
        )["sharedHeldStores"]
        totals = (
            preview["wouldRemoveStores"],
            preview["wouldRemoveLocks"],
            preview["preserved"],
        )
        assert totals == (3, 4, 3), "live or unknown stopped shared seat was not preserved"
        assert file_states(tmp_path) == before
        request = RuntimeInstallRequest(reconcile_shared_held_stores=True)
        result = runtime_install_payload(config, request)
        repeated = runtime_install_payload(config, request)["sharedHeldStores"]

    assert result["ok"]
    summary = result["sharedHeldStores"]
    assert summary["state"] == "reconciled" and "stores" not in summary
    assert (summary["removedStores"], summary["removedLocks"], summary["preserved"]) == (3, 4, 3)
    assert (repeated["removedStores"], repeated["removedLocks"], repeated["preserved"]) == (0, 0, 3)
    report = json.loads(Path(result["reportPath"]).read_text())
    preserved = {
        row["agentId"]: row["reason"]
        for row in report["sharedHeldStores"]["stores"]
        if row["state"] == "preserved"
    }
    assert preserved.keys() == {live[0], unknown[0], deferred[0]}
    assert preserved[live[0]] == "The host still has this seat."
    assert preserved[unknown[0]] == "The host did not answer for this seat."
    for seat, text in (
        (closed, "waiting until the seat closed"),
        (gone, "waiting until the host went away"),
        (archived, "waiting until archival"),
    ):
        _, receipt, store, lock = seat
        assert text in receipt.read_text()
        assert not store.exists()
        assert not lock.exists()
    assert not lock_only[3].exists()
    after = file_states(tmp_path)
    for _, receipt, store, lock in (live, unknown, deferred):
        for path in (receipt, store, lock):
            assert after[path.as_posix()] == before[path.as_posix()]


def test_runtime_install_preview_preserves_dead_registry_and_shared_store(tmp_path: Path):
    config = setup_config(tmp_path)
    _, _, store, _ = _shared_store(config, "running", text="waiting for a live seat")
    dead_server = _ServerRecord(
        pid=2**30,
        role="mcp",
        loadedDigest="a" * 64,
        startedAt="2026-11-24T00:00:00Z",
        configPath=str(config.config_path),
        packageRoot=None,
        processStart=None,
    )
    record = config.coordination_root / "runtime" / "servers" / f"{dead_server.pid}.json"
    record.parent.mkdir(parents=True)
    record.write_text(dead_server.model_dump_json())
    before = file_states(tmp_path)

    with (
        patch(
            "agents_remember.application.runtime.install.install_runtime_from_config",
            return_value={"ok": True},
        ),
        patch(
            "agents_remember.application.runtime.install.plan_running_dashboard", return_value=None
        ),
        patch(
            "agents_remember.cli.shared_held_store_reconciliation.read_agent",
            return_value=AgentReading(lifecycle="idle"),
        ),
    ):
        preview = runtime_install_payload(
            config, RuntimeInstallRequest(dry_run=True, reconcile_shared_held_stores=True)
        )

    assert record.is_file(), "runtime installation preview removed the dead server record"
    assert file_states(tmp_path) == before
    assert preview["ok"] and preview["runningServers"] == []
    assert preview["sharedHeldStores"] == {
        "state": "previewed",
        "path": str(store.parent),
        "wouldRemoveStores": 0,
        "wouldRemoveLocks": 0,
        "preserved": 1,
    }
    assert "reportPath" not in preview


def test_shared_reconciliation_reads_host_before_settling_stopped_seat(tmp_path: Path):
    config = setup_config(tmp_path)
    waiting = "waiting for a stopped seat to be confirmed gone"
    agent_id, receipt, store, lock = _shared_store(config, "stopped", text=waiting)
    build = ServingBuild("test", None, "2026-11-24T00:00:00Z", source_digest="a" * 64)

    with (
        patch(
            "agents_remember.cli.shared_held_store_reconciliation.process_serving_build",
            return_value=build,
        ),
        patch(
            "agents_remember.cli.shared_held_store_reconciliation.read_agent",
            return_value=AgentReading(found=False),
        ) as read,
    ):
        result = reconcile_shared_held_stores(config, dry_run=False, servers=[])

    assert read.call_args is not None, "stopped shared seat was settled without host proof"
    read.assert_called_once_with(config, agent_id)
    assert (result["removedStores"], result["removedLocks"], result["preserved"]) == (1, 1, 0)
    closure = json.loads(receipt.read_text())["heldStoreClosures"]["0"]
    assert [row["text"] for row in closure["waitingMessages"]] == [waiting]
    assert not store.exists() and not lock.exists()


def test_shared_reconciliation_preserves_new_generation_during_host_probe(tmp_path: Path):
    config = setup_config(tmp_path)
    agent_id, receipt, store, lock = _shared_store(config, "stopped", text="still waiting")
    build = ServingBuild("test", None, "2026-11-24T00:00:00Z", source_digest="a" * 64)
    before = store.read_bytes()

    def observe(_config: object, target: str) -> AgentReading:
        assert target == agent_id
        assert not lock_held(receipt)
        successor = json.loads(receipt.read_text())
        successor.update(heldStoreGeneration=1, status="running")
        _write_receipt(receipt, successor, prior_generation=0)
        return AgentReading(archived=True)

    with (
        patch(
            "agents_remember.cli.shared_held_store_reconciliation.process_serving_build",
            return_value=build,
        ),
        patch(
            "agents_remember.cli.shared_held_store_reconciliation.read_agent",
            side_effect=observe,
        ),
    ):
        result = reconcile_shared_held_stores(config, dry_run=False, servers=[])

    (row,) = result["stores"]
    assert row["state"] == "preserved", (
        "host probe did not preserve the successor shared generation"
    )
    assert row["agentId"] == agent_id and "different generation" in row["reason"]
    successor = json.loads(receipt.read_text())
    assert successor["heldStoreGeneration"] == 1 and successor["status"] == "running"
    assert "heldStoreClosures" not in successor
    assert store.read_bytes() == before and lock.is_file()


def test_runtime_install_preserves_shared_lock_until_registered_older_writer_restarts(tmp_path):
    config = setup_config(tmp_path)
    _, _, store, lock = _shared_store(config, "rejected")
    build = ServingBuild("test", None, "2026-11-24T00:00:00Z", source_digest="a" * 64)
    older = RunningServerPayload(
        pid=100,
        role="dashboard",
        loadedDigest="b" * 64,
        startedAt="2026-11-23T00:00:00Z",
        configPath=str(config.config_path),
        currentness=BuildCurrentnessPayload(mode="installed", state="current"),
    )
    with (
        patch(
            "agents_remember.application.runtime.install.install_runtime_from_config",
            return_value={"ok": True},
        ),
        patch(
            "agents_remember.application.runtime.install.install_host", return_value={"ok": True}
        ),
        patch(
            "agents_remember.application.runtime.install.ensure_running_dashboard",
            return_value=None,
        ),
        patch("agents_remember.application.runtime.install.server_inventory", return_value=[older]),
        patch(
            "agents_remember.cli.shared_held_store_reconciliation.process_serving_build",
            return_value=build,
        ),
    ):
        result = runtime_install_payload(
            config, RuntimeInstallRequest(reconcile_shared_held_stores=True)
        )
    assert not result["ok"]
    assert result["sharedHeldStores"]["state"] == "blocked"
    assert "dashboard PID 100" in result["sharedHeldStores"]["reason"]
    assert store.is_file() and lock.is_file()


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
