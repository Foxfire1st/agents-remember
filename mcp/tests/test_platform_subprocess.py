from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from agents_remember.kernel import platform_subprocess
from agents_remember.kernel.platform_subprocess import (
    native_command,
    native_path_environment,
    native_subprocess_environment,
    resolve_native_executable,
    windows_interop_reason,
)


def test_native_environment_uses_enclosure_reports_and_filters_windows_path(
    tmp_path: Path,
) -> None:
    reports = tmp_path / "enclosure" / "reports"
    environment = native_subprocess_environment(
        {
            "PATH": os.pathsep.join(["/mnt/c/Program Files/nodejs", "/usr/local/bin", "/usr/bin"]),
            "TMP": "/mnt/c/Users/developer/AppData/Local/Temp",
        },
        temp_root=reports / "tmp",
        platform="posix",
    )

    assert environment["PATH"] == os.pathsep.join(["/usr/local/bin", "/usr/bin"])
    assert {environment[name] for name in ("TMPDIR", "TMP", "TEMP")} == {
        (reports / "tmp").as_posix()
    }
    assert (reports / "tmp").is_dir()


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (r"C:\\Program Files\\nodejs\\npm.cmd", "Windows drive path"),
        (r"\\\\wsl.localhost\\Ubuntu\\repo", "UNC path"),
        ("/mnt/c/Program Files/nodejs/npm", "Windows-mounted WSL path"),
        ("/usr/local/bin/tool.exe", "Windows executable or command shim"),
    ],
)
def test_windows_interop_paths_are_classified(value: str, reason: str) -> None:
    assert windows_interop_reason(value, platform="posix") == reason


def test_native_command_prefers_the_linux_tool_after_a_windows_path(tmp_path: Path) -> None:
    linux_bin = tmp_path / "linux-bin"
    linux_bin.mkdir()
    tool = linux_bin / "node"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    tool.chmod(0o755)
    environment = {"PATH": os.pathsep.join(["/mnt/c/Program Files/nodejs", linux_bin.as_posix()])}

    with patch("agents_remember.kernel.platform_subprocess.os.name", "posix"):
        assert native_command(["node", "--version"], environment) == [
            tool.as_posix(),
            "--version",
        ]


def test_native_command_refuses_an_explicit_windows_shim() -> None:
    environment = {"PATH": "/usr/bin"}

    with pytest.raises(RuntimeError, match="Windows-mounted WSL path"):
        native_command(["/mnt/c/Program Files/nodejs/npm.cmd", "test"], environment)


def test_native_environment_refuses_windows_backed_temp_root() -> None:
    with pytest.raises(RuntimeError, match="Windows-mounted WSL path"):
        native_subprocess_environment(
            {"PATH": "/usr/bin"},
            temp_root=Path("/mnt/c/Users/developer/AppData/Local/Temp"),
            platform="posix",
        )


def test_windows_runner_keeps_its_environment_and_paths_unchanged(tmp_path: Path) -> None:
    source = {"PATH": r"C:\\Windows\\System32", "TEMP": r"C:\\Temp"}

    assert windows_interop_reason(r"C:\\tool.exe", platform="nt") is None
    assert native_path_environment(source, platform="nt") == source
    assert native_subprocess_environment(source, temp_root=tmp_path, platform="nt") == source


def test_native_path_environment_refuses_an_all_windows_path() -> None:
    with pytest.raises(RuntimeError, match="no native POSIX PATH entries"):
        native_path_environment(
            {"PATH": os.pathsep.join(["/mnt/c/bin", "/run/desktop/mnt/host/c/Tools"])},
            platform="posix",
        )


def test_native_path_environment_adds_the_posix_user_local_bin(tmp_path: Path) -> None:
    user_bin = tmp_path / ".local" / "bin"
    user_bin.mkdir(parents=True)
    node = user_bin / "node"
    node.write_text("#!/bin/sh\n", encoding="utf-8")
    node.chmod(0o755)

    environment = native_path_environment(
        {"HOME": tmp_path.as_posix(), "PATH": "/usr/bin"}, platform="posix"
    )

    assert environment["PATH"] == os.pathsep.join([user_bin.as_posix(), "/usr/bin"])
    assert resolve_native_executable("node", environment, platform="posix") == node.as_posix()


def test_native_executable_resolution_covers_direct_missing_and_incompatible(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "tool"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    assert (
        resolve_native_executable(executable.as_posix(), {"PATH": "/usr/bin"}, platform="posix")
        == executable.as_posix()
    )
    with pytest.raises(RuntimeError, match="unavailable on PATH"):
        resolve_native_executable("definitely-not-installed", {"PATH": "/usr/bin"})
    with pytest.raises(RuntimeError, match="refusing incompatible subprocess executable"):
        resolve_native_executable("/mnt/c/Tools/tool.exe", {"PATH": "/usr/bin"}, platform="posix")


def test_native_command_refuses_an_empty_command() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        native_command([], {"PATH": "/usr/bin"})


def test_existing_symlink_that_resolves_to_windows_storage_is_refused(tmp_path: Path) -> None:
    candidate = tmp_path / "tool"
    candidate.write_text("tool", encoding="utf-8")
    with patch.object(platform_subprocess, "_WSL_WINDOWS_MOUNT") as matcher:
        matcher.match.side_effect = [None, object()]
        assert (
            windows_interop_reason(candidate, platform="posix")
            == "path resolves into a Windows-mounted filesystem"
        )
