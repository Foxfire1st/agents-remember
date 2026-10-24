"""Create the host authority once without touching siblings; report failed publication honestly."""

from __future__ import annotations

import json
import os
import pathlib
import types
from typing import Any

import pytest
from agents_remember.kernel.primitives import paseo_authority as owner
from agents_remember.kernel.primitives.runtime_config import (
    ConfigError,
    load_paseo_runtime_settings,
)


def _config(path: pathlib.Path) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        config_path=path.as_posix(),
        coordination_root=path.parent / "coord",
        workspace_root=path.parent / "projects",
        dashboard=types.SimpleNamespace(port=8765),
    )


def _block(listen: str = "127.0.0.1:8766", by: str = "us") -> dict[str, object]:
    return {"listen": listen, "by": by, "providers": {}}


def test_an_unreadable_authority_is_a_modeled_refusal_and_a_config_error(
    tmp_path: pathlib.Path,
) -> None:
    target = tmp_path / "locked.json"
    target.write_text(json.dumps({"version": 1}) + "\n", encoding="utf-8")
    target.chmod(0o000)
    try:
        with pytest.raises(owner.HostAuthorityRefused) as refused:
            owner.establish_authority_paseo_runtime(_config(target))
        assert refused.value.code == "host-authority-unreadable"
        with pytest.raises(ConfigError):
            load_paseo_runtime_settings(target)
    finally:
        target.chmod(0o600)


def test_a_symlinked_authority_is_refused_and_its_target_untouched(tmp_path: pathlib.Path) -> None:
    real = tmp_path / "real.json"
    body = json.dumps({"version": 1, "coordinationRoot": "/x"}) + "\n"
    real.write_text(body, encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(real)

    with pytest.raises(owner.HostAuthorityRefused) as refused:
        owner.establish_authority_paseo_runtime(_config(link))

    assert refused.value.code == "host-authority-symlink"
    assert real.read_text(encoding="utf-8") == body


def test_an_absent_file_is_created_and_an_existing_block_is_left_alone(
    tmp_path: pathlib.Path,
) -> None:
    cold = tmp_path / "cold.json"
    created = owner.establish_authority_paseo_runtime(_config(cold))
    assert created["state"] == "created" and created["changed"] is True
    assert json.loads(cold.read_text(encoding="utf-8"))["paseoRuntime"] is not None

    body = json.dumps({"version": 1, "paseoRuntime": _block()}) + "\n"
    kept = tmp_path / "kept.json"
    kept.write_text(body, encoding="utf-8")
    present = owner.establish_authority_paseo_runtime(_config(kept))
    assert present["state"] == "present" and present["changed"] is False
    assert kept.read_text(encoding="utf-8") == body


def test_publication_keeps_sibling_bytes_and_modes(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "settings.json"
    target.write_bytes(b'{"version":1,"sentinel":"keep"}\n')
    target.chmod(0o640)
    sibling = tmp_path / ".settings.json.init"
    original = b"unrelated sibling\n"
    sibling.write_bytes(original)
    sibling.chmod(0o400)

    answer = owner.establish_authority_paseo_runtime(_config(target))

    assert answer["state"] == "written" and answer["changed"] is True
    document = json.loads(target.read_bytes())
    assert document["sentinel"] == "keep" and document["paseoRuntime"]["embed"]
    assert target.stat().st_mode & 0o777 == 0o640
    assert sibling.read_bytes() == original and sibling.stat().st_mode & 0o777 == 0o400
    assert set(tmp_path.iterdir()) == {target, sibling}, (
        "publication leaves only the intended names"
    )


def test_a_symlinked_temporary_candidate_is_refused_without_touching_its_target(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "settings.json"
    original = b'{"version":1}\n'
    target.write_bytes(original)
    victim = tmp_path / "victim"
    body = b"keep these bytes and this mode\n"
    victim.write_bytes(body)
    victim.chmod(0o440)
    temporary = tmp_path / ".settings.json.occupied.new"
    temporary.symlink_to(victim)
    # Exercise the real exclusive open; one candidate makes its refusal observable.
    monkeypatch.setattr(owner.tempfile, "_get_candidate_names", lambda: iter(["occupied"]))
    monkeypatch.setattr(owner.tempfile, "TMP_MAX", 1)

    with pytest.raises(owner.HostAuthorityRefused) as refused:
        owner.establish_authority_paseo_runtime(_config(target))

    assert refused.value.code == "host-authority-write-failed"
    assert (
        str(target) in refused.value.detail
        and "holds no paseoRuntime block" in refused.value.detail
    )
    assert "no temporary file was created" in refused.value.detail
    assert target.read_bytes() == original
    assert temporary.is_symlink()
    assert victim.read_bytes() == body and victim.stat().st_mode & 0o777 == 0o440


@pytest.mark.parametrize("stage", ["fsync", "replace", "cleanup"])
def test_a_failed_publication_names_the_actual_authority_and_temporary_states(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    target = tmp_path / "settings.json"
    original = b'{"version":1,"sentinel":"keep"}\n'
    target.write_bytes(original)
    temporaries: list[pathlib.Path] = []
    real_create = owner.tempfile.mkstemp
    real_unlink = os.unlink

    def observe_create(*args: Any, **kwargs: Any) -> tuple[int, str]:
        handle, name = real_create(*args, **kwargs)
        temporaries.append(pathlib.Path(name))
        return handle, name

    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError(28, "injected publication failure")

    def refuse_cleanup(path: Any, *args: Any, **kwargs: Any) -> None:
        if pathlib.Path(path) in temporaries:
            raise OSError(13, "injected cleanup failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(owner.tempfile, "mkstemp", observe_create)
    monkeypatch.setattr(owner.os, "fsync" if stage == "fsync" else "replace", fail)
    if stage == "cleanup":
        monkeypatch.setattr(owner.os, "unlink", refuse_cleanup)

    with pytest.raises(owner.HostAuthorityRefused) as refused:
        owner.establish_authority_paseo_runtime(_config(target))

    assert refused.value.code == "host-authority-write-failed"
    detail = refused.value.detail
    assert str(target) in detail and "holds no paseoRuntime block" in detail
    assert "injected publication failure" in detail and len(temporaries) == 1
    temporary = temporaries[0]
    assert temporary.parent == target.parent and str(temporary) in detail
    assert target.read_bytes() == original
    if stage == "cleanup":
        assert temporary.exists() and "injected cleanup failure" in detail
        assert "holds a paseoRuntime block" in detail, "retained temporary state is named"
    else:
        assert not temporary.exists() and "is absent" in detail
