"""One pinned Ubuntu quality pipeline shared by local worktrees and GitHub."""

from __future__ import annotations

import json
import re
import secrets
from datetime import UTC, datetime
from typing import Annotated

import dagger
from dagger import Doc, ReturnType, dag, field, function, object_type

from agents_remember_quality import profile_results
from agents_remember_quality.engine_helpers import (
    _admitted_profile_plan,
    _require_adapter_runtime,
)
from agents_remember_quality.profile_execution import _ProfileRunInputs, _run_profile_acceptance
from agents_remember_quality.profile_publication import (
    export_profile_reports,
    prepare_profile_reports,
)

BASELINE_CODEX_PROTOCOL = profile_results.BASELINE_CODEX_PROTOCOL
_QualityProgress = profile_results.QualityProgress
_profile_result_payload = profile_results.profile_result_payload
_quality_result_payload = profile_results.quality_result_payload

PLAYWRIGHT_IMAGE = (
    "mcr.microsoft.com/playwright:v1.60.0-noble@"
    "sha256:83192064c7510f7ee73dd63dc5f22a5e01a92c81a2e6a9c715d9e3fe55471fd9"
)
CODEX_VERSION = "0.151.0"
RETRY_CACHE_ROOT = "/var/cache/agents-remember-quality-retry"
RUNTIME_PROBE_ROOT = "/opt/ar-runtime-probe"
RUNTIME_CONTRACT = f"{RUNTIME_PROBE_ROOT}/scripts/python-runtime-contract.env"
RUNTIME_CHECKER = f"{RUNTIME_PROBE_ROOT}/scripts/check-python-runtime.py"
RUNTIME_POLICY = "mcp/src/agents_remember/python_runtime.py"
RUNTIME_UV = "/usr/local/bin/uv"
UV_IMAGE_REPOSITORY = "ghcr.io/astral-sh/uv"
UV_VERSION_RE = re.compile(r"^[0-9]+(\.[0-9]+)*$")
UV_PYTHON_INSTALL_DIR = "/root/.local/share/uv/python"
RUNTIME_PROOF = "/opt/agents-remember-python-runtime.json"
RUNTIME_PROOF_CONTEXT = (
    f". {RUNTIME_CONTRACT}\n"
    f"runtime_python=$({RUNTIME_UV} python find "
    '--system --managed-python --no-python-downloads "$AR_PYTHON_MINOR")\n'
    f"uv_version=$({RUNTIME_UV} --version)\n"
)
VENV_ROOT = "/opt/ar-venv"
VENV_PYTHON = f"{VENV_ROOT}/bin/python"
VENV_PROOF = "/opt/agents-remember-venv-runtime.json"


async def _contract_uv_minor(source: dagger.Directory) -> str:
    """Read AR_UV_VERSION from the candidate runtime contract."""

    contents = await source.file("scripts/python-runtime-contract.env").contents()
    for raw_line in contents.splitlines():
        line = raw_line.strip()
        if not line.startswith("AR_UV_VERSION="):
            continue
        value = line.split("=", 1)[1].strip().strip("'\"")
        if UV_VERSION_RE.fullmatch(value) is None:
            raise ValueError(f"malformed contract version AR_UV_VERSION={value!r}")
        return value
    raise ValueError("scripts/python-runtime-contract.env does not declare AR_UV_VERSION")


def _image_digest_from_ref(image_ref: str) -> str:
    """Extract the resolved sha256 digest from a Dagger OCI image reference."""

    _, separator, digest = image_ref.rpartition("@sha256:")
    if separator != "@sha256:" or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError(f"uv image reference lacks a resolved sha256 digest: {image_ref!r}")
    return f"sha256:{digest}"


async def _canonical_python_base(
    source: dagger.Directory,
    *,
    image_reference: str = PLAYWRIGHT_IMAGE,
) -> dagger.Container:
    """Install uv from Astral's minor-line image and its managed Python."""

    uv_minor = await _contract_uv_minor(source)
    uv_image = dag.container().from_(f"{UV_IMAGE_REPOSITORY}:{uv_minor}")
    uv_image_ref = await uv_image.image_ref()
    uv_image_digest = _image_digest_from_ref(uv_image_ref)
    return (
        dag.container()
        .from_(image_reference)
        .with_file(
            RUNTIME_CONTRACT,
            source.file("scripts/python-runtime-contract.env"),
        )
        # Candidate setup changes HOME. Both steps must use the same managed Python directory.
        .with_env_variable("UV_PYTHON_INSTALL_DIR", UV_PYTHON_INSTALL_DIR)
        .with_env_variable("AR_UV_IMAGE_REF", uv_image_ref)
        .with_env_variable("AR_UV_IMAGE_DIGEST", uv_image_digest)
        .with_env_variable("DEBIAN_FRONTEND", "noninteractive")
        .with_exec(["apt-get", "update"])
        .with_exec(
            [
                "apt-get",
                "install",
                "-y",
                "--no-install-recommends",
                "ca-certificates",
                "curl",
                "git",
                "tmux",
            ]
        )
        .with_exec(["rm", "-rf", "/var/lib/apt/lists"])
        .with_file(RUNTIME_UV, uv_image.file("/uv"), permissions=0o755)
        .with_exec(
            [
                "bash",
                "-euc",
                f'. {RUNTIME_CONTRACT}\n{RUNTIME_UV} python install --no-bin "$AR_PYTHON_MINOR"',
            ]
        )
        .with_file(
            RUNTIME_CHECKER,
            source.file("scripts/check-python-runtime.py"),
        )
        .with_file(
            f"{RUNTIME_PROBE_ROOT}/{RUNTIME_POLICY}",
            source.file(RUNTIME_POLICY),
        )
        .with_exec(
            [
                "bash",
                "-euc",
                RUNTIME_PROOF_CONTEXT
                + f'exec "$runtime_python" {RUNTIME_CHECKER} --base-interpreter '
                '--expected-version "$AR_PYTHON_MINOR" '
                '--expected-base-prefix "${runtime_python%/bin/*}" '
                "--require-linux-pidfd "
                '--uv-version "$uv_version" '
                '--uv-image-digest "$AR_UV_IMAGE_DIGEST" '
                f"> {RUNTIME_PROOF}",
            ]
        )
    )


async def _candidate_base(
    source: dagger.Directory,
    repository_bundle: dagger.File,
    *,
    image_reference: str = PLAYWRIGHT_IMAGE,
) -> dagger.Container:
    """Build the deterministic candidate environment shared by every evidence attempt."""

    base = await _canonical_python_base(source, image_reference=image_reference)
    return (
        base.with_mounted_cache("/root/.npm", dag.cache_volume("ar-quality-npm-v1"))
        .with_env_variable("HOME", "/tmp/ar-home")
        .with_env_variable("TMPDIR", "/tmp/ar-quality")
        .with_env_variable("TMP", "/tmp/ar-quality")
        .with_env_variable("TEMP", "/tmp/ar-quality")
        .with_exec(["mkdir", "-p", "/tmp/ar-home", "/tmp/ar-quality"])
        .with_exec(["npm", "install", "--global", f"@openai/codex@{CODEX_VERSION}"])
        .with_exec(["codex", "--version"])
        .with_directory("/workspace", source)
        .with_file("/tmp/ar-candidate.bundle", repository_bundle)
        .with_workdir("/workspace")
        .with_exec(["rm", "-rf", ".git"])
        .with_exec(["git", "init"])
        .with_exec(["git", "fetch", "--no-tags", "/tmp/ar-candidate.bundle", "HEAD"])
        .with_exec(["git", "reset", "--mixed", "FETCH_HEAD"])
        .with_exec(["git", "add", "--all"])
        .with_exec(["git", "rev-parse", "--verify", "FETCH_HEAD^{commit}"])
        .with_exec(
            [
                "git",
                "config",
                "--local",
                "user.email",
                "clean-room@agents-remember.invalid",
            ]
        )
        .with_exec(
            [
                "git",
                "config",
                "--local",
                "user.name",
                "Agents Remember clean room",
            ]
        )
        .with_exec(
            [
                "bash",
                "-euc",
                f". {RUNTIME_CONTRACT}\n"
                f"UV_PROJECT_ENVIRONMENT={VENV_ROOT} {RUNTIME_UV} sync "
                '--project /workspace/mcp --python "$AR_PYTHON_MINOR" '
                "--managed-python --no-python-downloads --frozen --all-extras",
            ]
        )
        .with_exec(
            [
                "bash",
                "-euc",
                RUNTIME_PROOF_CONTEXT + f"exec {VENV_PYTHON} {RUNTIME_CHECKER} "
                '--expected-version "$AR_PYTHON_MINOR" '
                '--expected-base-prefix "${runtime_python%/bin/*}" '
                "--require-linux-pidfd "
                '--uv-version "$uv_version" '
                '--uv-image-digest "$AR_UV_IMAGE_DIGEST" '
                f"> {VENV_PROOF}",
            ]
        )
        .with_exec([RUNTIME_UV, "pip", "check", "--python", VENV_PYTHON])
    )


def _bind_candidate_attempt(
    container: dagger.Container,
    *,
    attempt_nonce: str,
    reports: str,
) -> dagger.Container:
    """Bind non-deterministic evidence inputs after reusable candidate setup."""

    return (
        container.with_mounted_cache(
            RETRY_CACHE_ROOT,
            dag.cache_volume("ar-quality-retry-v3"),
            sharing=dagger.CacheSharingMode.LOCKED,
        )
        .with_env_variable(
            "PYTHONPATH",
            "/workspace/mcp/test_support:/workspace/mcp/src",
        )
        .with_env_variable("AR_QUALITY_INVOCATION", "ci")
        .with_env_variable("CI", "1")
        .with_env_variable("AR_QUALITY_RETRY_CACHE", RETRY_CACHE_ROOT)
        .with_env_variable("AR_DAGGER_TEST_ATTESTATION", attempt_nonce)
        .with_env_variable("AR_QUALITY_ATTEMPT_NONCE", attempt_nonce)
        .with_env_variable("AR_CERTIFICATION_SELECTOR_ROOT", "/tmp/ar-profile")
        .with_env_variable("AR_QUALITY_PROGRESS_REPORT", f"{reports}/quality-progress.json")
        .with_env_variable("COVERAGE_FILE", f"{reports}/coverage.data")
        .with_exec(["mkdir", "-p", reports, "/tmp/ar-profile"])
        .with_exec(["cp", RUNTIME_PROOF, f"{reports}/python-runtime.json"])
        .with_exec(["cp", VENV_PROOF, f"{reports}/python-venv-runtime.json"])
        .with_exec(
            [
                "sh",
                "-c",
                "umask 077; printf '%s' \"$AR_DAGGER_TEST_ATTESTATION\" "
                "> /tmp/ar-quality/dagger-test-attestation",
            ]
        )
    )


async def _candidate_container(
    source: dagger.Directory,
    repository_bundle: dagger.File,
    *,
    attempt_nonce: str,
    reports: str,
    image_reference: str = PLAYWRIGHT_IMAGE,
) -> dagger.Container:
    """Build one reusable candidate base, then bind an exact evidence attempt."""

    return _bind_candidate_attempt(
        await _candidate_base(
            source,
            repository_bundle,
            image_reference=image_reference,
        ),
        attempt_nonce=attempt_nonce,
        reports=reports,
    )


def _portable_candidate_container(
    source: dagger.Directory,
    repository_bundle: dagger.File,
    *,
    image_reference: str,
    reports: str,
) -> dagger.Container:
    """Reconstruct one exact fixture repository in its profile-owned clean image."""

    return (
        dag.container()
        .from_(image_reference)
        .with_env_variable("CI", "1")
        .with_env_variable("AR_CERTIFICATION_SELECTOR_ROOT", "/tmp/ar-profile")
        .with_directory("/workspace", source)
        .with_file("/tmp/repository-candidate.bundle", repository_bundle)
        .with_workdir("/workspace")
        .with_exec(["mkdir", "-p", reports, "/tmp/ar-profile"])
        .with_exec(["rm", "-rf", ".git"])
        .with_exec(["git", "init"])
        .with_exec(
            [
                "git",
                "fetch",
                "--no-tags",
                "/tmp/repository-candidate.bundle",
                "HEAD",
            ]
        )
        .with_exec(["git", "reset", "--mixed", "FETCH_HEAD"])
        .with_exec(["git", "add", "--all"])
    )


@object_type
class QualityResult:
    """Exportable evidence plus the canonical pipeline exit code."""

    reports: dagger.Directory = field()
    exit_code: int = field()


@object_type
class AgentsRememberQuality:
    """Run Agents Remember quality in a pristine Ubuntu userland."""

    @function
    async def quality(
        self,
        source: Annotated[
            dagger.Directory,
            Doc("Exact candidate source tree to install and test in clean Ubuntu."),
        ],
        repository_bundle: Annotated[
            dagger.File,
            Doc("Git bundle containing the candidate commit and its ancestry."),
        ],
        execution_manifest: Annotated[
            dagger.File,
            Doc("Exact MCP-admitted profile, semantic closure, and candidate plan."),
        ],
        memory_cap_bytes: Annotated[
            int,
            Doc("Optional container memory cap in bytes; zero leaves memory host-managed."),
        ] = 0,
        *,
        retained_reports: Annotated[
            dagger.Directory | None,
            Doc("Exact bounded predecessor report snapshot selected by the frozen recovery."),
        ] = None,
    ) -> QualityResult:
        """Run only the exact admitted repository profile inside clean Ubuntu."""
        started_at = datetime.now(UTC).isoformat()
        if memory_cap_bytes < 0:
            raise ValueError("memory_cap_bytes cannot be negative")
        plan = await _admitted_profile_plan(
            execution_manifest,
            function_name="quality",
        )
        mode = plan.mode
        if plan.candidate_kind != "git-tree":
            raise ValueError("certifying Dagger execution requires a git-tree candidate")
        reports = "/reports"
        attempt_nonce = secrets.token_hex(16)
        container = await _candidate_container(
            source,
            repository_bundle,
            attempt_nonce=attempt_nonce,
            reports=reports,
            image_reference=_require_adapter_runtime(plan),
        )
        container = await container.with_exec(
            [
                "sh",
                "-c",
                f'test "$(git write-tree)" = "{plan.candidate_value}"',
            ],
            expect=ReturnType.ANY,
        ).sync()
        progress = await _run_profile_acceptance(
            container,
            _ProfileRunInputs(
                plan=plan,
                mode=mode,
                diff_base=plan.diff_base,
                reports=reports,
                memory_cap_bytes=memory_cap_bytes,
                retained_reports=retained_reports,
            ),
        )
        output = await prepare_profile_reports(progress, plan, reports=reports)
        result = _quality_result_payload(
            progress,
            started_at=started_at,
            mode=mode,
            attempt_nonce=attempt_nonce,
            gates=tuple(progress.gate_catalog),
        )
        exported = export_profile_reports(output, progress, plan, result, reports=reports)
        return QualityResult(
            reports=exported,
            exit_code=progress.exit_code,
        )

    @function
    async def portable_certification(
        self,
        source: Annotated[
            dagger.Directory,
            Doc("Exact portable fixture source tree to execute in its admitted image."),
        ],
        repository_bundle: Annotated[
            dagger.File,
            Doc("Git bundle containing the exact portable fixture candidate."),
        ],
        execution_manifest: Annotated[
            dagger.File,
            Doc("Exact MCP-admitted portable profile and semantic closure."),
        ],
        memory_cap_bytes: Annotated[
            int,
            Doc("Optional profile command memory input; zero leaves it image-managed."),
        ] = 0,
        *,
        retained_reports: Annotated[
            dagger.Directory | None,
            Doc("Exact bounded predecessor report snapshot selected by the frozen recovery."),
        ] = None,
    ) -> QualityResult:
        """Execute a non-Agents-Remember profile through the same frozen-plan interpreter."""

        started_at = datetime.now(UTC).isoformat()
        if memory_cap_bytes < 0:
            raise ValueError("memory_cap_bytes cannot be negative")
        plan = await _admitted_profile_plan(
            execution_manifest,
            function_name="portable-certification",
        )
        mode = plan.mode
        if plan.candidate_kind != "git-tree":
            raise ValueError("portable certification requires a git-tree candidate")
        reports = "/reports"
        attempt_nonce = secrets.token_hex(16)
        container = _portable_candidate_container(
            source,
            repository_bundle,
            image_reference=_require_adapter_runtime(plan),
            reports=reports,
        )
        container = await container.with_exec(
            [
                "sh",
                "-c",
                f'test "$(git write-tree)" = "{plan.candidate_value}"',
            ],
            expect=ReturnType.ANY,
        ).sync()
        progress = await _run_profile_acceptance(
            container,
            _ProfileRunInputs(
                plan=plan,
                mode=mode,
                diff_base=plan.diff_base,
                reports=reports,
                memory_cap_bytes=memory_cap_bytes,
                retained_reports=retained_reports,
            ),
        )
        output = await prepare_profile_reports(progress, plan, reports=reports)
        result = _profile_result_payload(
            progress,
            started_at=started_at,
            mode=mode,
            attempt_nonce=attempt_nonce,
            gates=tuple(progress.gate_catalog),
        )
        exported = export_profile_reports(output, progress, plan, result, reports=reports)
        return QualityResult(
            reports=exported,
            exit_code=progress.exit_code,
        )

    @function
    async def cadence_evidence(
        self,
        source: Annotated[
            dagger.Directory,
            Doc("Exact candidate source tree for non-accepting cadence evidence."),
        ],
        repository_bundle: Annotated[
            dagger.File,
            Doc("Git bundle containing the candidate commit and its ancestry."),
        ],
        trigger: Annotated[
            str,
            Doc("One of 'scheduled', 'provider-bump', or 'migration-window'."),
        ],
    ) -> QualityResult:
        """Run an explicit evidence cadence without minting quality acceptance."""

        allowed = {"scheduled", "provider-bump", "migration-window"}
        if trigger not in allowed:
            raise ValueError(f"unknown cadence evidence trigger: {trigger}")
        started_at = datetime.now(UTC).isoformat()
        reports = "/reports"
        attempt_nonce = secrets.token_hex(16)
        container = await (
            await _candidate_container(
                source,
                repository_bundle,
                attempt_nonce=attempt_nonce,
                reports=reports,
            )
        ).sync()
        exit_code = await container.exit_code()
        completed = ["environment"]
        if exit_code == 0:
            container = await container.with_exec(
                [
                    "/opt/ar-venv/bin/python",
                    "-m",
                    "agents_remember_test_support.testing.cadence_runner",
                    "--project-root",
                    "/workspace",
                    "--trigger",
                    trigger,
                    "--json-output",
                    f"{reports}/cadence-evidence.json",
                    "--pytest-report-log",
                    f"{reports}/pytest-events.jsonl",
                    "--pytest-phase-report",
                    f"{reports}/pytest-phases.json",
                ],
                expect=ReturnType.ANY,
            ).sync()
            exit_code = await container.exit_code()
            completed.append("cadence-evidence")
        result = {
            "status": "passed" if exit_code == 0 else "failed",
            "startedAt": started_at,
            "finishedAt": datetime.now(UTC).isoformat(),
            "trigger": trigger,
            "acceptanceEligible": False,
            "certifying": False,
            "credentialsMounted": False,
            "containerSocketMounted": False,
            "completedSteps": completed,
            "exitCode": exit_code,
        }
        container = container.with_new_file(
            f"{reports}/cadence-route-results.json",
            contents=json.dumps(result, indent=2, sort_keys=True) + "\n",
        )
        return QualityResult(reports=container.directory(reports), exit_code=exit_code)
