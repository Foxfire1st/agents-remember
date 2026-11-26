from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPOSITORY_ROOT / "scripts/check-python-runtime.py"

_CHECKER_PROGRAM = """
import builtins
import readline
import runpy
import sys

checker, minor, base, venv, patch, module_origin, *extra_args = sys.argv[1:]
original_import = builtins.__import__

def import_without_new_stdlib(name, *args, **kwargs):
    origin = args[0] if args else kwargs.get("globals", {})
    if (
        isinstance(origin, dict)
        and origin.get("__file__") == checker
        and (name == "compression" or name.startswith("compression."))
    ):
        raise ModuleNotFoundError("compression is unavailable before Python 3.14")
    return original_import(name, *args, **kwargs)

builtins.__import__ = import_without_new_stdlib
sys.prefix = venv
sys.version_info = (*map(int, patch.split(".")), "final", 0)
if module_origin == "built-in":
    readline.__dict__.pop("__file__", None)
else:
    readline.__file__ = "observed-readline.so"

sys.argv = [checker, "--expected-version", minor, "--expected-base-prefix", base, *extra_args]
runpy.run_path(checker, run_name="__main__")
"""


def _contract_minor() -> str:
    contract = REPOSITORY_ROOT / "scripts/python-runtime-contract.env"
    return next(
        line.split("=", 1)[1]
        for line in contract.read_text(encoding="utf-8").splitlines()
        if line.startswith("AR_PYTHON_MINOR=")
    )


@pytest.fixture
def managed_base(tmp_path: Path) -> Path:
    root = tmp_path / "managed"
    root.mkdir()
    patch = root / f"cpython-{_contract_minor()}.7-linux-x86_64-gnu"
    (patch / "bin").mkdir(parents=True)
    base = root / f"cpython-{_contract_minor()}-linux-x86_64-gnu"
    base.symlink_to(patch, target_is_directory=True)
    return base


def _check(
    base: Path,
    home: Path,
    *,
    patch: str,
    module_origin: str = "extension",
    extra_args: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    venv = base.parent.parent / "venv"
    venv.mkdir(exist_ok=True)
    (venv / "pyvenv.cfg").write_text(f"home = {home}\n", encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            _CHECKER_PROGRAM,
            str(CHECKER),
            _contract_minor(),
            str(base),
            str(venv),
            patch,
            module_origin,
            *extra_args,
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "UV_PYTHON_INSTALL_DIR": str(base.parent)},
        timeout=HANG_GUARD_SECONDS,
    )


def test_unsupported_minor_is_named_before_importing_new_stdlib_modules(
    managed_base: Path,
) -> None:
    result = _check(managed_base, managed_base / "bin", patch="3.13.15")
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        f"Agents Remember Python runtime refusal: expected Python {_contract_minor()}, "
        f"observed 3.13.15 at {sys.executable}\n"
    )


def test_builtin_readline_is_reported(managed_base: Path) -> None:
    result = _check(managed_base, managed_base / "bin", patch="3.14.7", module_origin="built-in")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["modules"]["readline"] == "built-in"
    assert report["basePrefix"] == str(managed_base)


def test_different_patch_of_the_managed_minor_is_accepted(managed_base: Path) -> None:
    result = _check(managed_base, managed_base / "bin", patch="3.14.6")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["basePrefix"] == str(managed_base)


def test_patch_directory_is_refused_even_when_it_is_the_link_target(managed_base: Path) -> None:
    patch_dir = managed_base.resolve()
    result = _check(managed_base, patch_dir / "bin", patch="3.14.7")
    assert result.returncode == 1
    assert "expected a uv-managed Python 3.14 minor link" in result.stderr
    assert f"observed {patch_dir}" in result.stderr


def test_minor_link_outside_uv_directory_is_refused(managed_base: Path) -> None:
    foreign_dir = managed_base.parent.parent / "foreign"
    foreign_dir.mkdir()
    foreign_link = foreign_dir / managed_base.name
    foreign_link.symlink_to(managed_base.resolve(), target_is_directory=True)
    result = _check(managed_base, foreign_link / "bin", patch="3.14.7")
    assert result.returncode == 1
    assert "expected a uv-managed Python 3.14 minor link" in result.stderr
    assert f"observed {foreign_link}" in result.stderr


def test_uv_version_and_image_digest_are_recorded_in_provenance(managed_base: Path) -> None:
    digest = "sha256:" + "a" * 64
    result = _check(
        managed_base,
        managed_base / "bin",
        patch="3.14.10",
        extra_args=("--uv-version", "uv 0.13.10", "--uv-image-digest", digest),
    )
    assert result.returncode == 0, result.stderr
    provenance = json.loads(result.stdout)["provenance"]
    assert provenance["uvVersion"] == "uv 0.13.10"
    assert provenance["uvImageDigest"] == digest

    plain = _check(managed_base, managed_base / "bin", patch="3.14.10")
    assert plain.returncode == 0, plain.stderr
    assert "uvImageDigest" not in json.loads(plain.stdout)["provenance"]


@pytest.mark.parametrize(
    "workflow",
    [
        ".github/workflows/quality-checks.yml",
        ".github/workflows/publish-mcp-to-pypi.yml",
    ],
)
def test_workflows_install_uv_minor_line_and_record_resolved_version(workflow: str) -> None:
    text = (REPOSITORY_ROOT / workflow).read_text(encoding="utf-8")
    assert 'printf \'uv-version=%s.x\\n\' "$AR_UV_VERSION" >> "$GITHUB_OUTPUT"' in text
    assert "uses: astral-sh/setup-uv@" in text
    assert "steps.setup-uv.outputs.uv-version" in text
    assert "uv --version" in text


def test_dagger_uv_minor_and_image_digest_helpers() -> None:
    dagger_src = str(REPOSITORY_ROOT / ".dagger/src")
    module_name = "agents_remember_quality_main_under_test"
    sys.path.insert(0, dagger_src)
    try:
        spec = importlib.util.spec_from_file_location(
            module_name,
            REPOSITORY_ROOT / ".dagger/src/agents_remember_quality/main.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
        sys.path.remove(dagger_src)

    async def _fake_contents(text: str) -> str:
        return text

    class _FakeSource:
        def __init__(self, text: str) -> None:
            self._text = text

        def file(self, path: str) -> SimpleNamespace:
            assert path == "scripts/python-runtime-contract.env"
            return SimpleNamespace(contents=lambda: _fake_contents(self._text))

    contract_text = (REPOSITORY_ROOT / "scripts/python-runtime-contract.env").read_text(
        encoding="utf-8"
    )
    assert asyncio.run(module._contract_uv_minor(_FakeSource(contract_text))) == "0.13"

    with pytest.raises(ValueError, match="malformed contract version"):
        asyncio.run(module._contract_uv_minor(_FakeSource("AR_UV_VERSION=0.13-bad\n")))

    with pytest.raises(ValueError, match="does not declare AR_UV_VERSION"):
        asyncio.run(module._contract_uv_minor(_FakeSource("AR_PYTHON_MINOR=3.14\n")))

    digest_hex = "a" * 64
    assert (
        module._image_digest_from_ref(f"ghcr.io/astral-sh/uv:0.13@sha256:{digest_hex}")
        == f"sha256:{digest_hex}"
    )
    with pytest.raises(ValueError, match="lacks a resolved sha256 digest"):
        module._image_digest_from_ref("ghcr.io/astral-sh/uv:0.13")
