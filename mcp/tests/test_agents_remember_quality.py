from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest
from agents_remember.code_quality.dagger_environment import (
    DAGGER_TEST_ATTESTATION_ENV,
    DaggerAdmissionError,
    dagger_admission_refusal,
)
from conftest import prepare_certifying_pytest_bootstrap

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DAGGER_MANIFEST = REPOSITORY_ROOT / "dagger.json"
DAGGER_MODULE = REPOSITORY_ROOT / ".dagger/src/agents_remember_quality/main.py"
DAGGER_MODULE_ID = "agents_remember_quality.main"
VALID_DAGGER_NONCE = "0123456789abcdef0123456789abcdef"


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
        self.environment: list[tuple[object, ...]] = []

    def from_(self, _image: str) -> FakeContainer:
        return self

    def with_mounted_cache(self, *_args: object) -> FakeContainer:
        return self

    def with_env_variable(self, *args: object) -> FakeContainer:
        self.environment.append(args)
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
    dagger_config = json.loads(DAGGER_MANIFEST.read_text(encoding="utf-8"))
    assert dagger_config["disableDefaultFunctionCaching"] is True


def test_python_suite_refuses_missing_or_mismatched_dagger_attestation(tmp_path: Path) -> None:
    attestation = tmp_path / "dagger-test-attestation"
    assert "absent or invalid" in (dagger_admission_refusal({}, attestation) or "")
    assert "unavailable" in (
        dagger_admission_refusal(
            {DAGGER_TEST_ATTESTATION_ENV: VALID_DAGGER_NONCE},
            attestation,
        )
        or ""
    )
    attestation.write_text("f" * 32, encoding="utf-8")
    assert "do not match" in (
        dagger_admission_refusal(
            {DAGGER_TEST_ATTESTATION_ENV: VALID_DAGGER_NONCE},
            attestation,
        )
        or ""
    )
    with (
        patch(
            "conftest._prepare_certifying_pytest_bootstrap",
            side_effect=DaggerAdmissionError(
                "Agents Remember tests are Dagger-only; refusing host execution"
            ),
        ),
        pytest.raises(pytest.UsageError, match="refusing host execution"),
    ):
        prepare_certifying_pytest_bootstrap()


def test_python_suite_accepts_matching_dagger_attestation(tmp_path: Path) -> None:
    attestation = tmp_path / "dagger-test-attestation"
    attestation.write_text(VALID_DAGGER_NONCE, encoding="utf-8")
    assert (
        dagger_admission_refusal(
            {DAGGER_TEST_ATTESTATION_ENV: VALID_DAGGER_NONCE},
            attestation,
        )
        is None
    )


def test_agents_remember_quality_exports_failures_as_the_only_authoritative_result() -> None:
    source = DAGGER_MODULE.read_text(encoding="utf-8")

    assert "expect=ReturnType.ANY" in source
    assert "clean-quality-results.json" in source
    assert "async def verify" not in source


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

    with (
        patch.object(module, "dag", fake_dag),
        patch.object(module.secrets, "token_hex", return_value=VALID_DAGGER_NONCE),
    ):
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
    assert payload["attemptNonce"] == VALID_DAGGER_NONCE
    assert (
        "AR_DAGGER_TEST_ATTESTATION",
        VALID_DAGGER_NONCE,
    ) in fake_dag.container_value.environment
    assert (
        "AR_QUALITY_ATTEMPT_NONCE",
        VALID_DAGGER_NONCE,
    ) in fake_dag.container_value.environment
    assert any(
        command[:2] == ["sh", "-c"] and "/tmp/ar-quality/dagger-test-attestation" in command[-1]
        for command in commands
    )
    assert payload["completedSteps"] == [
        "environment",
        "codex-read-only-probe",
        "quality-wrapper",
    ]


def test_dagger_quality_full_uses_explicit_diff_base_without_targeted_flags() -> None:
    module = load_dagger_module()
    fake_dag = FakeDag([0] * 10)

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
    commands = fake_dag.container_value.commands
    assert ["npm", "ci"] in commands
    assert ["npm", "run", "lint"] in commands
    assert ["npm", "run", "typecheck"] in commands
    assert ["npm", "run", "test:coverage"] in commands
    assert ["npm", "run", "coverage:diff"] in commands
    assert ["npm", "run", "e2e", "--", "--fail-on-flaky-tests"] in commands
    assert ["npm", "run", "build"] in commands
    assert ("CI", "1") in fake_dag.container_value.environment


def test_dagger_quality_stops_at_the_first_failed_dashboard_rail() -> None:
    module = load_dagger_module()
    fake_dag = FakeDag([0, 0, 0, 0, 7])

    with patch.object(module, "dag", fake_dag):
        result = asyncio.run(
            module.AgentsRememberQuality().quality(
                object(), object(), mode="full", diff_base="base-commit"
            )
        )

    payload = json.loads(fake_dag.container_value.files["/reports/clean-quality-results.json"])
    assert result.exit_code == 7
    assert payload["completedSteps"][-2:] == ["dashboard-install", "dashboard-lint"]
    assert ["npm", "run", "typecheck"] not in fake_dag.container_value.commands


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
