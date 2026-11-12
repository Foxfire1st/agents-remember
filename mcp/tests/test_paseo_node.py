from __future__ import annotations

import fcntl
import hashlib
import http.client
import io
import tarfile
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.errors import PaseoRuntimeFailure
from agents_remember.kernel.file_lock import exclusive_file_lock, lock_path_for
from agents_remember.kernel.primitives.paseo_host_contract import NODE_VERSION
from agents_remember.kernel.primitives.paseo_node_paths import product_node
from agents_remember.serving.paseo import paseo_node
from agents_remember.serving.paseo.paseo_command import CommandResult
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS


def isolate(monkeypatch, root):
    monkeypatch.setenv("XDG_DATA_HOME", str(root / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(root / "cache"))
    return product_node()


def answers(argv, timeout):
    return CommandResult(0, f"v{NODE_VERSION}" if len(argv) == 2 else "10.9.4", "")


def test_digest_failure_removes_archive_and_names_both_digests(monkeypatch, tmp_path):
    runtime = isolate(monkeypatch, tmp_path)
    payload = b"wrong archive"

    def fetch(url, path):
        path.write_bytes(payload)

    with pytest.raises(PaseoRuntimeFailure) as raised:
        paseo_node.ensure_node(answers, fetch)
    assert raised.value.code == "node_digest_mismatch"
    assert runtime.sha256 in str(raised.value)
    assert hashlib.sha256(payload).hexdigest() in str(raised.value)
    assert not runtime.root.exists()
    assert list(runtime.cache.iterdir()) == []


def test_foreign_folder_is_refused_and_valid_node_is_read_only(monkeypatch, tmp_path):
    runtime = isolate(monkeypatch, tmp_path)
    runtime.root.mkdir(parents=True)
    marker = runtime.root / "keep"
    marker.write_text("foreign")

    def no_fetch(url, path):
        pytest.fail("existing folder triggered a download")

    with pytest.raises(PaseoRuntimeFailure, match="not overwritten"):
        paseo_node.ensure_node(answers, no_fetch)
    assert marker.read_text() == "foreign"
    for path in (runtime.node, runtime.npm):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    assert not paseo_node.ensure_node(answers, no_fetch)["changed"]
    assert not runtime.cache.exists()


@pytest.mark.parametrize("payload_size", [4, 1024 * 1024])
def test_verified_archive_is_placed_as_a_whole(monkeypatch, tmp_path, payload_size):
    runtime = isolate(monkeypatch, tmp_path)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:xz") as archive:
        for relative in ("bin/node", "lib/node_modules/npm/bin/npm-cli.js"):
            member = tarfile.TarInfo(f"{runtime.root.name}/{relative}")
            member.size = payload_size
            archive.addfile(member, io.BytesIO(b"n" * payload_size))
    payload = buffer.getvalue()

    def fetch(url, path):
        path.write_bytes(payload)

    runtime = replace(runtime, sha256=hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(paseo_node, "product_node", lambda: runtime)
    assert paseo_node.ensure_node(answers, fetch)["changed"]
    assert runtime.node.read_bytes() == b"n" * payload_size
    assert list(runtime.cache.iterdir()) == []
    assert not list(runtime.root.parent.glob("*.unpack"))


def test_other_platforms_are_explicitly_unsupported(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "agents_remember.kernel.primitives.paseo_node_paths.platform.machine", lambda: "aarch64"
    )
    with pytest.raises(PaseoRuntimeFailure) as raised:
        paseo_node.ensure_node(answers)
    assert raised.value.code == "node_platform_unsupported"
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("fault", ["wrong-node", "silent-npm"])
def test_existing_node_requires_pinned_version_and_answering_npm(monkeypatch, tmp_path, fault):
    runtime = isolate(monkeypatch, tmp_path)
    for path in (runtime.node, runtime.npm):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    def broken(argv, timeout):
        if fault == "wrong-node" and len(argv) == 2:
            return CommandResult(0, "v0.0.0", "")
        if fault == "silent-npm" and len(argv) == 3:
            return CommandResult(0, "", "")
        return answers(argv, timeout)

    with pytest.raises(PaseoRuntimeFailure, match="not overwritten"):
        paseo_node.ensure_node(broken, lambda *args: pytest.fail("foreign target downloaded"))


def test_partial_http_read_is_a_named_node_failure_and_is_reclaimed(monkeypatch, tmp_path):
    runtime = isolate(monkeypatch, tmp_path)

    def incomplete(url, path):
        path.write_bytes(b"partial")
        raise http.client.IncompleteRead(b"partial", 100)

    with pytest.raises(PaseoRuntimeFailure) as raised:
        paseo_node.ensure_node(answers, incomplete)
    assert raised.value.code == "node_install_failed"
    assert runtime.version in str(raised.value) and runtime.url in str(raised.value)
    assert not runtime.root.exists() and not list(runtime.cache.iterdir())


def test_valid_target_reclaims_owned_staging_without_downloading(monkeypatch, tmp_path):
    runtime = isolate(monkeypatch, tmp_path)
    for path in (runtime.node, runtime.npm):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("valid")
    archive, staging = paseo_node._temporaries(runtime)
    archive.parent.mkdir(parents=True)
    archive.write_text("leftover")
    staging.mkdir()
    (staging / "leftover").touch()
    result = paseo_node.ensure_node(answers, lambda *args: pytest.fail("valid target downloaded"))
    assert not result["changed"] and len(result["removed"]) == 2
    assert not archive.exists() and not staging.exists()
    assert runtime.node.read_text() == "valid"


@pytest.mark.parametrize("holder", ["thread", "flock"])
def test_competing_node_acquisition_has_a_finite_retryable_refusal(monkeypatch, tmp_path, holder):
    runtime = isolate(monkeypatch, tmp_path)
    # load-independent: acquisition must expire while the observed holder owns the lock.
    monkeypatch.setattr(paseo_node, "ACQUIRE_SECONDS", 0.05)
    acquired = threading.Event()
    release = threading.Event()

    def hold():
        if holder == "thread":
            with exclusive_file_lock(runtime.root, "test Node holder"):
                acquired.set()
                release.wait(HANG_GUARD_SECONDS)
        else:
            path = lock_path_for(runtime.root)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a+b") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                acquired.set()
                release.wait(HANG_GUARD_SECONDS)

    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert acquired.wait(HANG_GUARD_SECONDS)
        with pytest.raises(PaseoRuntimeFailure) as raised:
            paseo_node.ensure_node(answers, lambda *args: pytest.fail("lock loser fetched"))
        assert raised.value.code == "node_operation_busy"
        assert not runtime.root.exists()
    finally:
        release.set()
        thread.join(HANG_GUARD_SECONDS)
    assert not thread.is_alive()


def test_empty_xdg_values_use_home_defaults_in_every_working_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    for name in ("XDG_DATA_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(name, raising=False)
    expected = product_node()
    for cwd in (tmp_path, tmp_path / "other"):
        cwd.mkdir(exist_ok=True)
        monkeypatch.chdir(cwd)
        for name in ("XDG_DATA_HOME", "XDG_CACHE_HOME"):
            monkeypatch.setenv(name, "")
        assert product_node() == expected
    monkeypatch.setenv("XDG_DATA_HOME", "relative")
    with pytest.raises(PaseoRuntimeFailure, match="absolute"):
        product_node()
