"""Exercise the PNT sandbox's handoff to the checkout's Python bootstrap."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest
from pnt_sandbox_test_support import (
    PNT_BUILD_FILES,
    REPOSITORY_ROOT,
    Operations,
    SandboxLayout,
    SandboxRefusal,
    commands,
    isolated_workspace_harness_markers,
)


@pytest.mark.parametrize("stale_part", ["bootstrap", "contract"])
def test_start_refuses_mismatched_runtime_before_environment_work(
    tmp_path: Path, stale_part: str
) -> None:
    checkout = tmp_path / "product"
    for name in PNT_BUILD_FILES:
        target = checkout / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()

    scripts = checkout / "scripts"
    bootstrap = REPOSITORY_ROOT / "scripts/bootstrap-mcp-venv.sh"
    contract = REPOSITORY_ROOT / "scripts/python-runtime-contract.env"
    (scripts / bootstrap.name).write_bytes(bootstrap.read_bytes())
    (scripts / contract.name).write_bytes(contract.read_bytes())
    if stale_part == "bootstrap":
        (scripts / bootstrap.name).write_text(
            'echo "usage: scripts/bootstrap-mcp-venv.sh [--replace]"\n', encoding="utf-8"
        )
    else:
        with (scripts / contract.name).open("a", encoding="utf-8") as declarations:
            declarations.write("AR_PYTHON_SOURCE_URL=https://example.invalid/python.tar.xz\n")

    layout = SandboxLayout(tmp_path / "sandbox")
    with (
        isolated_workspace_harness_markers(tmp_path),
        mock.patch.object(
            commands, "_start", side_effect=AssertionError("mismatched checkout reached _start")
        ) as start,
        mock.patch.object(Operations, "prepare_python") as prepare,
        pytest.raises(
            SandboxRefusal,
            match=(
                r"PNT runtime contract mismatch.*"
                r"PNT tooling from the product checkout's own revision"
            ),
        ),
    ):
        commands.start(layout, checkout, Operations(layout, {}), print, tmp_path / "eve")
    start.assert_not_called()
    prepare.assert_not_called()
    assert not layout.root.exists()
    assert not (checkout / "mcp/.venv").exists()


@pytest.mark.parametrize("existing_python", [False, True], ids=["cold", "existing"])
@pytest.mark.parametrize("uv_override", [False, True], ids=["host-data", "task-override"])
def test_prepare_python_preserves_uv_install_dir_for_checkout_bootstrap(
    tmp_path: Path, existing_python: bool, uv_override: bool
) -> None:
    layout = SandboxLayout(tmp_path / "sandbox")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    if existing_python:
        executable = checkout / "mcp/.venv/bin/python"
        executable.parent.mkdir(parents=True)
        executable.touch()

    host_data = tmp_path / "host-data"
    inherited = {"XDG_DATA_HOME": str(host_data)}
    python_directory = host_data / "uv/python"
    if uv_override:
        python_directory = tmp_path / "task-python"
        inherited["UV_PYTHON_INSTALL_DIR"] = str(python_directory)

    argv = ["bash", "scripts/bootstrap-mcp-venv.sh", "--ensure"]
    completed = subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
    with mock.patch("pnt_sandbox.operations.subprocess.run", return_value=completed) as process:
        Operations(layout, inherited).prepare_python(checkout)

    process.assert_called_once()
    assert process.call_args.args == (argv,)
    assert process.call_args.kwargs["cwd"] == checkout
    environment = process.call_args.kwargs["env"]
    assert environment["XDG_DATA_HOME"] == str(layout.root / "data")
    assert environment["UV_PYTHON_INSTALL_DIR"] == str(python_directory)
