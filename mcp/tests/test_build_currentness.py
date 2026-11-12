"""Loaded Python identity, on-demand currentness, and live server inventory."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving import build_currentness as currentness
from agents_remember.serving import server_registry as registry
from agents_remember.serving.build_info import ServingBuild, runtime_source_digest


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    package = tmp_path / "package"
    package.mkdir()
    (package / "__init__.py").write_text("value = 'A'\n")
    build = ServingBuild(
        version="same",
        commit=None,
        booted_at="2026-11-05T00:00:00Z",
        source_digest=runtime_source_digest(package),
        package_root=str(package),
    )
    config = McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path / "coordination",
        workspace_root=tmp_path,
        transcript_root=tmp_path / "logs",
    )
    monkeypatch.setattr(currentness, "_COMPARISONS", currentness._BuildComparisons())
    monkeypatch.setattr(currentness.build_info, "process_serving_build", lambda: build)
    monkeypatch.setattr(registry, "process_serving_build", lambda: build)
    monkeypatch.setattr(registry.atexit, "register", lambda _cleanup: None)
    return config, package, build


def test_changed_python_is_stale_after_the_cache_expires_with_immutable_loaded_identity(
    world, monkeypatch
):
    config, package, build = world
    clock = [100.0]
    monkeypatch.setattr(currentness.time, "monotonic", lambda: clock[0])
    original = currentness.build_currentness(config)
    assert original is not None and original.state == "current"
    (package / "__init__.py").write_text("value = 'B'\n")
    clock[0] += 29
    assert currentness.build_currentness(config) == original
    clock[0] += 1
    stale = currentness.build_currentness(config)
    assert stale is not None and stale.state == "stale"
    assert stale.loadedDigest == build.source_digest
    assert stale.installedDigest == runtime_source_digest(package)
    assert stale.firstSeenAt and "reconnect" in (stale.remedy or "")
    clock[0] += 31
    repeated = currentness.build_currentness(config)
    assert repeated is not None
    assert repeated.firstSeenAt == stale.firstSeenAt
    assert build.source_digest == original.loadedDigest


def test_markdown_does_not_change_python_digest_but_python_addition_and_removal_do(world):
    config, package, build = world
    (package / "role.md").write_text("new operation\n")
    status = currentness.build_currentness(config)
    assert status is not None
    assert status.state == "current"
    added = package / "new.py"
    added.write_text("added = True\n")
    assert runtime_source_digest(package) != build.source_digest
    added.unlink()
    assert runtime_source_digest(package) == build.source_digest
    (package / "__init__.py").unlink()
    assert runtime_source_digest(package) is None


def test_checkout_is_compared_only_when_status_explicitly_requests_it(world, monkeypatch):
    config, _package, build = world
    checkout = config.workspace_root / "checkout"
    package = checkout / "mcp" / "src" / "agents_remember"
    package.mkdir(parents=True)
    (checkout / "mcp" / "pyproject.toml").touch()
    (checkout / ".git").mkdir()
    (package / "__init__.py").write_text("new = True\n")
    checkout_build = replace(build, package_root=str(package))
    monkeypatch.setattr(currentness.build_info, "process_serving_build", lambda: checkout_build)
    reads = []
    monkeypatch.setattr(
        currentness.build_info,
        "runtime_source_digest",
        lambda root: reads.append(root) or "sha256:different",
    )
    assert currentness.build_currentness(config) is None
    assert reads == []
    status = currentness.build_currentness(config, inspect_checkout=True)
    assert status is not None
    assert status.mode == "checkout" and status.state == "stale"
    assert len(reads) == 1
    assert currentness.build_currentness(config) is None
    assert len(reads) == 1


def test_unreadable_installed_or_unavailable_loaded_digest_is_unknown(world, monkeypatch):
    config, _package, build = world
    monkeypatch.setattr(currentness.build_info, "runtime_source_digest", lambda _root: None)
    unknown = currentness.build_currentness(config)
    assert unknown is not None
    assert unknown.state == "unknown" and unknown.reason and unknown.remedy is None
    missing = currentness.inspect_build(replace(build, source_digest=None))
    assert missing.state == "unknown" and missing.loadedDigest is None
    no_root = currentness.inspect_build(replace(build, package_root=None))
    assert no_root.reason is not None
    assert no_root.state == "unknown" and "root" in no_root.reason


def test_a_wheel_installed_under_a_checkout_is_still_an_installed_build(world):
    config, _package, build = world
    checkout = config.workspace_root / "checkout"
    source = checkout / "mcp" / "src" / "agents_remember"
    source.mkdir(parents=True)
    (checkout / "mcp" / "pyproject.toml").touch()
    (checkout / ".git").mkdir()
    installed = checkout / "venv" / "lib" / "site-packages" / "agents_remember"
    installed.mkdir(parents=True)
    (installed / "__init__.py").write_text("wheel = True\n")
    wheel = replace(build, package_root=str(installed))
    status = currentness.inspect_build(wheel)
    assert status.mode == "installed" and status.state == "stale"


def test_digest_cache_is_shared_between_status_and_inventory(world, monkeypatch):
    config, package, build = world
    reads = []
    monkeypatch.setattr(
        currentness.build_info,
        "runtime_source_digest",
        lambda root: reads.append(root) or build.source_digest,
    )
    cleanup = registry.register_server(config, "mcp")
    try:
        status = currentness.build_currentness(config)
        assert status is not None
        assert status.state == "current"
        assert registry.server_inventory(config)[0].currentness.state == "current"
        assert reads == [package]
    finally:
        cleanup()


def test_live_record_names_boot_settings_role_and_disappears_on_cleanup(world):
    config, _package, build = world
    cleanup = registry.register_server(config, "dashboard")
    path = config.coordination_root / "runtime" / "servers" / f"{os.getpid()}.json"
    assert path.is_file()
    servers = registry.server_inventory(config)
    assert len(servers) == 1
    server = servers[0]
    assert server.pid == os.getpid() and server.role == "dashboard"
    assert server.loadedDigest == build.source_digest and server.startedAt == build.booted_at
    assert server.configPath == str(config.config_path)
    cleanup()
    cleanup()
    assert not path.exists() and registry.server_inventory(config) == []


def test_inventory_identifies_stale_servers_and_role_specific_remedies(world, monkeypatch):
    config, package, _build = world
    cleanup = registry.register_server(config, "dashboard")
    (package / "__init__.py").write_text("new = True\n")
    try:
        server = registry.server_inventory(config)[0]
        assert server.currentness.state == "stale"
        assert server.currentness.loadedDigest != server.currentness.installedDigest
        assert server.currentness.remedy == "Restart the dashboard."
    finally:
        cleanup()


def test_closed_server_lifetimes_release_their_atexit_callbacks(world, monkeypatch):
    config, _package, _build = world
    retained = []
    monkeypatch.setattr(registry.atexit, "register", retained.append)
    monkeypatch.setattr(
        registry.atexit,
        "unregister",
        lambda callback: retained.remove(callback) if callback in retained else None,
    )
    for _lifetime in range(16):
        cleanup = registry.register_server(config, "dashboard")
        assert retained == [cleanup]
        cleanup()
        cleanup()
        assert retained == []


def test_dead_or_reused_pid_records_are_removed_and_bad_records_do_not_hide_live_ones(world):
    config, _package, _build = world
    cleanup = registry.register_server(config, "mcp")
    directory = config.coordination_root / "runtime" / "servers"
    live = directory / f"{os.getpid()}.json"
    dead = json.loads(live.read_text())
    dead["pid"] = 2**30
    dead_path = directory / f"{dead['pid']}.json"
    dead_path.write_text(json.dumps(dead))
    reused = {**dead, "pid": os.getpid(), "processStart": "wrong-start"}
    reused_path = directory / "reused.json"
    reused_path.write_text(json.dumps(reused))
    (directory / "bad.json").write_text("not json")
    try:
        assert len(registry.server_inventory(config)) == 1
        assert not dead_path.exists() and not reused_path.exists()
        assert live.exists()
    finally:
        cleanup()


def test_registration_write_failures_are_logged_once_without_stopping_server(
    world, monkeypatch, caplog
):
    config, _package, _build = world
    monkeypatch.setattr(registry, "_WARNINGS", registry._RecordWarnings())

    def fail(_path, _text):
        raise OSError("read-only registry")

    monkeypatch.setattr(registry, "atomic_write_text", fail)
    first = registry.register_server(config, "mcp")
    second = registry.register_server(config, "mcp")
    assert caplog.text.count("could not be written") == 1
    first()
    second()


def test_cache_reclamation_tracks_live_boots_and_roots_at_two_population_sizes(world, monkeypatch):
    config, _package, build = world
    clock = [100.0]
    monkeypatch.setattr(currentness.time, "monotonic", lambda: clock[0])
    live = set()
    monkeypatch.setattr(registry, "_alive", lambda record: record.pid in live)
    directory = config.coordination_root / "runtime" / "servers"
    directory.mkdir(parents=True)
    for population in (4, 16):
        for number in range(population):
            package = config.workspace_root / f"server-{population}-{number}"
            package.mkdir()
            (package / "__init__.py").write_text("current = True\n")
            pid = population * 100 + number
            live.add(pid)
            record = registry._ServerRecord(
                pid=pid,
                role="mcp",
                loadedDigest="old-python-digest",
                startedAt=f"boot-{pid}",
                configPath=str(config.config_path),
                packageRoot=str(package),
                processStart=None,
            )
            (directory / f"{pid}.json").write_text(record.model_dump_json())
        assert len(registry.server_inventory(config)) == population
        assert len(currentness._COMPARISONS._first_seen) == population
        replacement_path = directory / f"{population * 100}.json"
        replaced = json.loads(replacement_path.read_text())
        old_boot = replaced["startedAt"]
        replaced["startedAt"] = f"replacement-{population}"
        replacement_path.write_text(json.dumps(replaced))
        assert len(registry.server_inventory(config)) == population
        assert len(currentness._COMPARISONS._first_seen) == population
        assert old_boot not in {identity[2] for identity in currentness._COMPARISONS._first_seen}
        live.clear()
        # Observation times belong to live boot identities, not indefinitely to a root.
        assert registry.server_inventory(config) == []
        assert currentness._COMPARISONS._first_seen == {}
        assert len(currentness._COMPARISONS._digests) == population
        clock[0] += currentness.DIGEST_CACHE_SECONDS
        assert registry.server_inventory(config) == []
        assert currentness._COMPARISONS._digests == {}
    # The observing process remains retained even if it has no registered server record.
    currentness.inspect_build(replace(build, source_digest="old-observer"))
    own = currentness.inspect_build(build)
    registry.server_inventory(config)
    assert own.state == "current"
    assert list(currentness._COMPARISONS._digests) == [build.package_root]
    assert currentness._COMPARISONS._first_seen == {}
