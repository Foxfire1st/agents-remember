from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, patch

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DAGGER_MANIFEST = REPOSITORY_ROOT / "dagger.json"
DAGGER_MODULE = REPOSITORY_ROOT / ".dagger/src/agents_remember_quality/main.py"
DAGGER_MODULE_ID = "agents_remember_quality.main"


def load_dagger_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(DAGGER_MODULE_ID, DAGGER_MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[DAGGER_MODULE_ID] = module
    spec.loader.exec_module(module)
    return module


class FakeContainer:
    def __init__(self, exit_codes: list[int]) -> None:
        self.exit_codes = exit_codes
        self.commands: list[list[str]] = []
        self.files: dict[str, str] = {}

    def from_(self, _image: str) -> FakeContainer:
        return self

    def with_mounted_cache(self, *_args: object) -> FakeContainer:
        return self

    def with_env_variable(self, *_args: object) -> FakeContainer:
        return self

    def with_exec(self, command: list[str], **_kwargs: object) -> FakeContainer:
        self.commands.append(command)
        return self

    def with_directory(self, *_args: object) -> FakeContainer:
        return self

    def with_file(self, *_args: object) -> FakeContainer:
        return self

    def with_workdir(self, *_args: object) -> FakeContainer:
        return self

    def with_new_file(self, path: str, *, contents: str) -> FakeContainer:
        self.files[path] = contents
        return self

    def directory(self, path: str) -> str:
        return path

    async def sync(self) -> FakeContainer:
        return self

    async def exit_code(self) -> int:
        return self.exit_codes.pop(0)


class FakeDag:
    def __init__(self, exit_codes: list[int]) -> None:
        self.container_value = FakeContainer(exit_codes)

    def container(self) -> FakeContainer:
        return self.container_value

    def cache_volume(self, name: str) -> str:
        return name


def test_agents_remember_quality_module_is_pinned_and_parseable() -> None:
    manifest = json.loads(DAGGER_MANIFEST.read_text(encoding="utf-8"))
    source = DAGGER_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=DAGGER_MODULE.as_posix())

    assert manifest["engineVersion"] == "v0.21.8"
    assert any(
        isinstance(node, ast.ClassDef) and node.name == "AgentsRememberQuality"
        for node in tree.body
    )
    assert DAGGER_MODULE_ID.endswith(".main")
    assert "@openai/codex@{CODEX_VERSION}" in source
    assert "repository_bundle: Annotated[" in source
    assert "dagger.File" in source
    assert "candidate_head" not in source
    assert '"/tmp/ar-candidate.bundle",\n                    "HEAD"' in source
    assert 'with_exec(["git", "add", "--all"])' in source
    assert "from typing import Annotated" in source
    assert "from dagger import Doc" in source
    assert "Required Git commit used for changed-line coverage" in source
    assert "'targeted' derives the changed leaf subset" in source


def test_agents_remember_quality_exports_failures_before_verify_refuses() -> None:
    source = DAGGER_MODULE.read_text(encoding="utf-8")

    assert "expect=ReturnType.ANY" in source
    assert "clean-quality-results.json" in source
    assert "if result.exit_code != 0:" in source
    assert "raise RuntimeError" in source


@pytest.mark.parametrize(
    ("mode", "diff_base", "memory_cap", "message"),
    [
        ("quick", "base", 0, "unknown quality mode"),
        ("full", "", 0, "diff_base must name"),
        ("full", "base", -1, "memory_cap_bytes cannot be negative"),
    ],
)
def test_dagger_quality_refuses_invalid_public_inputs(
    mode: str, diff_base: str, memory_cap: int, message: str
) -> None:
    module = load_dagger_module()

    with pytest.raises(ValueError, match=message):
        asyncio.run(
            module.AgentsRememberQuality().quality(
                object(),
                object(),
                diff_base=diff_base,
                mode=mode,
                memory_cap_bytes=memory_cap,
            )
        )


def test_dagger_quality_builds_the_real_probe_and_targeted_wrapper_graph() -> None:
    module = load_dagger_module()
    fake_dag = FakeDag([0, 0, 0])

    with patch.object(module, "dag", fake_dag):
        result = asyncio.run(
            module.AgentsRememberQuality().quality(
                object(),
                object(),
                mode="targeted",
                diff_base="a" * 40,
                memory_cap_bytes=1024,
            )
        )

    commands = fake_dag.container_value.commands
    assert result.exit_code == 0
    assert result.reports == "/reports"
    assert any(command[-1] == "mcp/tests/test_codex_clean_room_probe.py" for command in commands)
    wrapper = next(
        command for command in commands if "agents_remember.code_quality.check" in command
    )
    assert "--targeted" in wrapper
    assert wrapper[-4:] == ["--diff-base", "a" * 40, "--memory-cap-bytes", "1024"]
    payload = json.loads(fake_dag.container_value.files["/reports/clean-quality-results.json"])
    assert payload["status"] == "passed"
    assert payload["completedSteps"] == [
        "environment",
        "codex-read-only-probe",
        "quality-wrapper",
    ]


def test_dagger_quality_full_uses_explicit_diff_base_without_targeted_flags() -> None:
    module = load_dagger_module()
    fake_dag = FakeDag([0, 0, 0])

    with patch.object(module, "dag", fake_dag):
        asyncio.run(
            module.AgentsRememberQuality().quality(object(), object(), diff_base="base-commit")
        )

    wrapper = next(
        command
        for command in fake_dag.container_value.commands
        if "agents_remember.code_quality.check" in command
    )
    assert "--targeted" not in wrapper
    assert wrapper[-2:] == ["--diff-base", "base-commit"]
    assert "--memory-cap-bytes" not in wrapper


@pytest.mark.parametrize(
    ("exit_codes", "completed"),
    [
        ([9], ["environment"]),
        ([0, 7], ["environment", "codex-read-only-probe"]),
    ],
)
def test_dagger_quality_exports_failure_at_the_exact_completed_boundary(
    exit_codes: list[int], completed: list[str]
) -> None:
    module = load_dagger_module()
    fake_dag = FakeDag(exit_codes)

    with patch.object(module, "dag", fake_dag):
        result = asyncio.run(
            module.AgentsRememberQuality().quality(object(), object(), diff_base="base-commit")
        )

    payload = json.loads(fake_dag.container_value.files["/reports/clean-quality-results.json"])
    assert result.exit_code != 0
    assert payload["status"] == "failed"
    assert payload["completedSteps"] == completed


def test_dagger_verify_returns_green_and_refuses_red_quality_results() -> None:
    module = load_dagger_module()
    quality = AsyncMock(return_value=module.QualityResult(reports=object(), exit_code=0))

    with patch.object(module.AgentsRememberQuality, "quality", quality):
        assert (
            asyncio.run(
                module.AgentsRememberQuality().verify(object(), object(), diff_base="base-commit")
            )
            == "clean Ubuntu quality passed"
        )

    quality.return_value = module.QualityResult(reports=object(), exit_code=4)
    with (
        patch.object(module.AgentsRememberQuality, "quality", quality),
        pytest.raises(RuntimeError, match="exit code 4"),
    ):
        asyncio.run(
            module.AgentsRememberQuality().verify(object(), object(), diff_base="base-commit")
        )
