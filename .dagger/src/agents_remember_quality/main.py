"""One pinned Ubuntu quality pipeline shared by local worktrees and GitHub."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from typing import Annotated

import dagger
from dagger import Doc, ReturnType, dag, field, function, object_type

from agents_remember_quality.quality_command import (
    ExpectedCommand,
    causal_evidence_steps,
    quality_wrapper_command,
)
from agents_remember_quality.retry_evidence_route import (
    RetryEvidenceContext,
    run_exact_retry_evidence,
    run_retry_matrix_evidence,
)

PLAYWRIGHT_IMAGE = (
    "mcr.microsoft.com/playwright:v1.60.0-noble@"
    "sha256:83192064c7510f7ee73dd63dc5f22a5e01a92c81a2e6a9c715d9e3fe55471fd9"
)
CODEX_VERSION = "0.151.0"
RETRY_CACHE_ROOT = "/var/cache/agents-remember-quality-retry"
PYTHON_BUILD_CACHE_ROOT = "/var/cache/agents-remember-python"
RUNTIME_INSTALLER_ROOT = "/opt/agents-remember-runtime-installer"
RUNTIME_CONTRACT = f"{RUNTIME_INSTALLER_ROOT}/python-runtime-contract.env"
RUNTIME_CHECKER = f"{RUNTIME_INSTALLER_ROOT}/check-python-runtime.py"
RUNTIME_INSTALLER = f"{RUNTIME_INSTALLER_ROOT}/install-python-runtime.sh"
RUNTIME_ROOT = "/opt/agents-remember-python"
RUNTIME_CURRENT = f"{RUNTIME_ROOT}/current"
RUNTIME_PYTHON = f"{RUNTIME_CURRENT}/bin/python3.13"
RUNTIME_UV = f"{RUNTIME_CURRENT}/bin/uv"
RUNTIME_PROOF = "/opt/agents-remember-python-runtime.json"
VENV_ROOT = "/opt/ar-venv"
VENV_PYTHON = f"{VENV_ROOT}/bin/python"
VENV_PROOF = "/opt/agents-remember-venv-runtime.json"
E2E_NOT_SELECTED_EXIT_CODE = 78
BASELINE_CODEX_PROTOCOL = "initialize -> initialized -> thread/list"
AMBIENT_CODEX_PROTOCOL = (
    f"{BASELINE_CODEX_PROTOCOL}; real app-server MCP connected -> "
    "turn/start -> normally discovered MCP function calls"
)


def _canonical_python_base(source: dagger.Directory) -> dagger.Container:
    """Provision one source-built Python recipe before candidate-specific source."""

    return (
        dag.container()
        .from_(PLAYWRIGHT_IMAGE)
        .with_mounted_cache("/root/.cache/pip", dag.cache_volume("ar-quality-pip-v1"))
        .with_mounted_cache(
            PYTHON_BUILD_CACHE_ROOT,
            dag.cache_volume("ar-python-source-build-v1"),
        )
        .with_file(
            RUNTIME_CONTRACT,
            source.file("scripts/python-runtime-contract.env"),
        )
        .with_file(
            RUNTIME_CHECKER,
            source.file("scripts/check-python-runtime.py"),
        )
        .with_file(
            RUNTIME_INSTALLER,
            source.file("scripts/install-python-runtime.sh"),
        )
        .with_env_variable("DEBIAN_FRONTEND", "noninteractive")
        .with_exec(["apt-get", "update"])
        .with_exec(
            [
                "bash",
                "-c",
                f". {RUNTIME_CONTRACT}; apt-get install -y --no-install-recommends "
                "$AR_PYTHON_APT_BUILD_DEPS tmux",
            ]
        )
        .with_exec(["rm", "-rf", "/var/lib/apt/lists"])
        .with_exec(
            [
                "bash",
                "-c",
                f". {RUNTIME_CONTRACT}; "
                f"bash {RUNTIME_INSTALLER} "
                f'--prefix "{RUNTIME_ROOT}/cpython-$AR_PYTHON_VERSION" '
                f"--cache-root {PYTHON_BUILD_CACHE_ROOT} "
                f"--tooling-root {PYTHON_BUILD_CACHE_ROOT}/tooling; "
                f"mkdir -p {RUNTIME_ROOT}; cd {RUNTIME_ROOT}; "
                'ln -s "cpython-$AR_PYTHON_VERSION" current',
            ]
        )
        .with_exec(
            [
                "bash",
                "-c",
                f". {RUNTIME_CONTRACT}; {RUNTIME_PYTHON} {RUNTIME_CHECKER} "
                '--expected-version "$AR_PYTHON_VERSION" '
                f"--expected-base-prefix {RUNTIME_CURRENT} "
                "--require-linux-pidfd "
                '--source-url "$AR_PYTHON_SOURCE_URL" '
                '--source-sha256 "$AR_PYTHON_SOURCE_SHA256" '
                '--builder-commit "$AR_PYTHON_BUILD_COMMIT" '
                f"> {RUNTIME_PROOF}",
            ]
        )
        .with_exec(
            [
                "bash",
                "-c",
                f". {RUNTIME_CONTRACT}; {RUNTIME_PYTHON} -m pip install "
                '--disable-pip-version-check "uv==$AR_UV_VERSION"',
            ]
        )
    )


def _candidate_base(
    source: dagger.Directory,
    repository_bundle: dagger.File,
) -> dagger.Container:
    """Build the deterministic candidate environment shared by every evidence attempt."""

    return (
        _canonical_python_base(source)
        .with_mounted_cache("/root/.npm", dag.cache_volume("ar-quality-npm-v1"))
        .with_env_variable("HOME", "/tmp/ar-home")
        .with_env_variable("PIP_CACHE_DIR", "/root/.cache/pip")
        .with_env_variable("TMPDIR", "/tmp/ar-quality")
        .with_env_variable("TMP", "/tmp/ar-quality")
        .with_env_variable("TEMP", "/tmp/ar-quality")
        .with_exec(["mkdir", "-p", "/tmp/ar-home", "/tmp/ar-quality"])
        .with_exec(["npm", "install", "--global", f"@openai/codex@{CODEX_VERSION}"])
        .with_exec(["codex", "--version"])
        .with_exec([RUNTIME_PYTHON, "-m", "venv", VENV_ROOT])
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
                "-c",
                f"UV_PROJECT_ENVIRONMENT={VENV_ROOT} {RUNTIME_UV} sync "
                f"--project /workspace/mcp --python {VENV_PYTHON} "
                "--no-managed-python --frozen --all-extras",
            ]
        )
        .with_exec(
            [
                "bash",
                "-c",
                f". {RUNTIME_CONTRACT}; {VENV_PYTHON} {RUNTIME_CHECKER} "
                '--expected-version "$AR_PYTHON_VERSION" '
                f"--expected-base-prefix {RUNTIME_CURRENT} "
                "--require-linux-pidfd "
                '--source-url "$AR_PYTHON_SOURCE_URL" '
                '--source-sha256 "$AR_PYTHON_SOURCE_SHA256" '
                '--builder-commit "$AR_PYTHON_BUILD_COMMIT" '
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
        .with_env_variable("AR_QUALITY_RETRY_CACHE", RETRY_CACHE_ROOT)
        .with_env_variable("AR_DAGGER_TEST_ATTESTATION", attempt_nonce)
        .with_env_variable("AR_QUALITY_ATTEMPT_NONCE", attempt_nonce)
        .with_env_variable("AR_CODEX_PROBE_MODE", "real")
        .with_env_variable("AR_CODEX_PROBE_REPORT", f"{reports}/codex-probe.json")
        .with_env_variable("AR_QUALITY_PROGRESS_REPORT", f"{reports}/quality-progress.json")
        .with_env_variable("COVERAGE_FILE", f"{reports}/coverage.data")
        .with_exec(["mkdir", "-p", reports])
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


def _candidate_container(
    source: dagger.Directory,
    repository_bundle: dagger.File,
    *,
    attempt_nonce: str,
    reports: str,
) -> dagger.Container:
    """Build one reusable candidate base, then bind an exact evidence attempt."""

    return _bind_candidate_attempt(
        _candidate_base(source, repository_bundle),
        attempt_nonce=attempt_nonce,
        reports=reports,
    )


async def _run_dashboard_quality(
    container: dagger.Container,
) -> tuple[dagger.Container, int, list[str], list[str], dict[str, int]]:
    """Run every frontend rail inside the same clean Dagger environment."""
    container = container.with_env_variable("CI", "1")
    steps = (
        ("dashboard-install", ["npm", "ci"]),
        ("dashboard-lint", ["npm", "run", "lint"]),
        ("dashboard-typecheck", ["npm", "run", "typecheck"]),
        ("dashboard-coverage", ["npm", "run", "test:coverage"]),
        ("dashboard-diff-coverage", ["npm", "run", "coverage:diff"]),
        (
            "dashboard-e2e",
            ["npm", "run", "e2e", "--", "--fail-on-flaky-tests"],
        ),
        ("dashboard-build", ["npm", "run", "build"]),
    )
    attempted: list[str] = []
    completed: list[str] = []
    step_exit_codes: dict[str, int] = {}
    exit_code = 0
    for step, command in steps:
        attempted.append(step)
        container = (
            await container.with_workdir("/workspace/dashboard")
            .with_exec(command, expect=ReturnType.ANY)
            .sync()
        )
        exit_code = await container.exit_code()
        step_exit_codes[step] = exit_code
        if exit_code != 0:
            break
        completed.append(step)
    return container, exit_code, attempted, completed, step_exit_codes


async def _run_expected_commands(
    container: dagger.Container,
    commands: tuple[ExpectedCommand, ...],
    step_codes: dict[str, int],
) -> tuple[dagger.Container, bool]:
    """Run a non-accepting route whose deliberate failures are part of its proof."""

    route_ok = all(code == 0 for code in step_codes.values())
    for step in commands:
        if not route_ok:
            break
        container = await container.with_exec(
            list(step.command),
            expect=ReturnType.ANY,
        ).sync()
        code = await container.exit_code()
        step_codes[step.name] = code
        route_ok = code == step.expected_exit
    return container, route_ok


@dataclass
class _QualityProgress:
    container: dagger.Container
    exit_code: int
    attempted: list[str] = dataclass_field(default_factory=list)
    completed: list[str] = dataclass_field(default_factory=list)
    skipped: list[str] = dataclass_field(default_factory=list)
    step_exit_codes: dict[str, int] = dataclass_field(default_factory=dict)


async def _run_quality_step(
    progress: _QualityProgress,
    name: str,
    command: list[str],
) -> None:
    if progress.exit_code != 0:
        return
    progress.attempted.append(name)
    progress.container = await progress.container.with_exec(
        command,
        expect=ReturnType.ANY,
    ).sync()
    progress.exit_code = await progress.container.exit_code()
    progress.step_exit_codes[name] = progress.exit_code
    if progress.exit_code == 0:
        progress.completed.append(name)


async def _run_candidate_acceptance(
    container: dagger.Container,
    *,
    mode: str,
    diff_base: str,
    reports: str,
    memory_cap_bytes: int,
) -> _QualityProgress:
    environment_exit = await container.exit_code()
    progress = _QualityProgress(
        container=container,
        exit_code=environment_exit,
        attempted=["environment"],
        completed=["environment"] if environment_exit == 0 else [],
        step_exit_codes={"environment": environment_exit},
    )
    await _run_quality_step(
        progress,
        "codex-read-only-probe",
        [
            "/opt/ar-venv/bin/python",
            "-m",
            "pytest",
            "-q",
            "-n=0",
            "mcp/tests/test_codex_clean_room_probe.py",
        ],
    )
    await _run_quality_step(
        progress,
        "ambient-role-chat-e2e",
        [
            VENV_PYTHON,
            "scripts/e2e_harness/run.py",
            "--mode",
            mode,
            "--diff-base",
            diff_base,
            "--reports",
            reports,
        ],
    )
    if progress.step_exit_codes.get("ambient-role-chat-e2e") == E2E_NOT_SELECTED_EXIT_CODE:
        progress.skipped.append("ambient-role-chat-e2e")
        progress.exit_code = 0
    await _run_quality_step(
        progress,
        "quality-wrapper",
        quality_wrapper_command(
            reports=reports,
            diff_base=diff_base,
            mode=mode,
            memory_cap_bytes=memory_cap_bytes,
        ),
    )
    if progress.exit_code == 0 and mode == "full":
        await _run_dashboard_steps(progress)
    return progress


async def _run_dashboard_steps(progress: _QualityProgress) -> None:
    (
        progress.container,
        progress.exit_code,
        attempted,
        completed,
        exit_codes,
    ) = await _run_dashboard_quality(progress.container)
    progress.attempted.extend(attempted)
    progress.completed.extend(completed)
    progress.step_exit_codes.update(exit_codes)


def _quality_result_payload(
    progress: _QualityProgress,
    *,
    started_at: str,
    mode: str,
    attempt_nonce: str,
) -> dict[str, object]:
    failed_step = next(
        (
            step
            for step in reversed(progress.attempted)
            if progress.step_exit_codes.get(step, 0) != 0 and step not in progress.skipped
        ),
        None,
    )
    e2e_completed = "ambient-role-chat-e2e" in progress.completed
    e2e_attempted = "ambient-role-chat-e2e" in progress.attempted
    e2e_skipped = "ambient-role-chat-e2e" in progress.skipped
    codex_protocol = (
        AMBIENT_CODEX_PROTOCOL
        if e2e_completed
        else BASELINE_CODEX_PROTOCOL
        if "codex-read-only-probe" in progress.completed
        else None
    )
    result: dict[str, object] = {
        "status": "passed" if progress.exit_code == 0 else "failed",
        "startedAt": started_at,
        "finishedAt": datetime.now(UTC).isoformat(),
        "mode": mode,
        "codexMode": "real",
        "codexProtocol": codex_protocol,
        "promptSubmitted": (
            True if e2e_completed else None if e2e_attempted and not e2e_skipped else False
        ),
        "credentialsMounted": False,
        "containerSocketMounted": False,
        "attemptedSteps": progress.attempted,
        "completedSteps": progress.completed,
        "skippedSteps": progress.skipped,
        "failedStep": failed_step,
        "stepExitCodes": progress.step_exit_codes,
        "exitCode": progress.exit_code,
        "attemptNonce": attempt_nonce,
    }
    if "quality-wrapper" in progress.completed:
        result["causalFailureReport"] = "causal-failures.json"
        result["causalFailureSummary"] = "causal-failures.md"
    ambient_evidence = _ambient_evidence(e2e_completed=e2e_completed, e2e_skipped=e2e_skipped)
    if ambient_evidence is not None:
        result["ambientRoleChatEvidence"] = ambient_evidence
    return result


def _ambient_evidence(
    *,
    e2e_completed: bool,
    e2e_skipped: bool,
) -> dict[str, object] | None:
    if e2e_completed:
        return {
            "status": "passed",
            "summary": "ambient-role-chat-e2e/summary.json",
            "runs": [
                "ambient-role-chat-e2e/run-1.json",
                "ambient-role-chat-e2e/run-2.json",
            ],
        }
    if e2e_skipped:
        return {
            "status": "skipped",
            "summary": "ambient-role-chat-e2e/summary.json",
        }
    return None


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
        diff_base: Annotated[
            str,
            Doc(
                "Required Git commit used for changed-line coverage: the leaf base in "
                "targeted mode or the super-integration base in full mode."
            ),
        ],
        mode: Annotated[
            str,
            Doc(
                "Acceptance altitude: 'targeted' derives the changed leaf subset; "
                "'full' runs the complete repository suite once at master integration."
            ),
        ] = "full",
        memory_cap_bytes: Annotated[
            int,
            Doc("Optional container memory cap in bytes; zero leaves memory host-managed."),
        ] = 0,
    ) -> QualityResult:
        """Run the canonical clean-Ubuntu acceptance gate and export its reports."""
        started_at = datetime.now(UTC).isoformat()
        if mode not in {"targeted", "full"}:
            raise ValueError(f"unknown quality mode: {mode}")
        if not diff_base.strip():
            raise ValueError("diff_base must name the explicit acceptance comparison commit")
        if memory_cap_bytes < 0:
            raise ValueError("memory_cap_bytes cannot be negative")
        reports = "/reports"
        attempt_nonce = secrets.token_hex(16)
        container = _candidate_container(
            source,
            repository_bundle,
            attempt_nonce=attempt_nonce,
            reports=reports,
        )
        container = await container.sync()
        progress = await _run_candidate_acceptance(
            container,
            mode=mode,
            diff_base=diff_base,
            reports=reports,
            memory_cap_bytes=memory_cap_bytes,
        )
        result = _quality_result_payload(
            progress,
            started_at=started_at,
            mode=mode,
            attempt_nonce=attempt_nonce,
        )
        container = progress.container.with_new_file(
            f"{reports}/clean-quality-results.json",
            contents=json.dumps(result, indent=2, sort_keys=True) + "\n",
        )
        return QualityResult(
            reports=container.directory(reports),
            exit_code=progress.exit_code,
        )

    @function
    async def retry_evidence(
        self,
        source: Annotated[
            dagger.Directory,
            Doc("Exact candidate source tree for non-accepting retry-route evidence."),
        ],
        repository_bundle: Annotated[
            dagger.File,
            Doc("Git bundle containing the candidate commit and its ancestry."),
        ],
        diff_base: Annotated[
            str,
            Doc("Explicit comparison commit used by both retry attempts."),
        ],
        mode: Annotated[
            str,
            Doc("Either 'targeted' or 'full'; both attempts use the same population."),
        ] = "targeted",
    ) -> QualityResult:
        """Prove fresh publication then exact reuse across two real Dagger containers."""

        if mode not in {"targeted", "full"}:
            raise ValueError(f"unknown retry evidence mode: {mode}")
        if not diff_base.strip():
            raise ValueError("diff_base must name the explicit retry comparison commit")
        context = RetryEvidenceContext(
            source,
            repository_bundle,
            diff_base,
            RETRY_CACHE_ROOT,
            _candidate_container,
        )
        outcome = await run_exact_retry_evidence(
            context,
            mode=mode,
        )
        return QualityResult(
            reports=outcome.container.directory("/reports"),
            exit_code=outcome.exit_code,
        )

    @function
    async def retry_matrix_evidence(
        self,
        source: Annotated[
            dagger.Directory,
            Doc("Exact candidate source tree for non-accepting retry-matrix evidence."),
        ],
        repository_bundle: Annotated[
            dagger.File,
            Doc("Git bundle containing the candidate commit and its ancestry."),
        ],
        diff_base: Annotated[
            str,
            Doc("Explicit comparison commit used by every retry-matrix attempt."),
        ],
    ) -> QualityResult:
        """Prove mutation, lane, context, and filtering decisions on the real wrapper."""

        if not diff_base.strip():
            raise ValueError("diff_base must name the explicit retry comparison commit")
        context = RetryEvidenceContext(
            source,
            repository_bundle,
            diff_base,
            RETRY_CACHE_ROOT,
            _candidate_container,
        )
        outcome = await run_retry_matrix_evidence(context)
        return QualityResult(
            reports=outcome.container.directory("/reports"),
            exit_code=outcome.exit_code,
        )

    @function
    async def causal_evidence(
        self,
        source: Annotated[
            dagger.Directory,
            Doc("Exact candidate source tree for non-accepting causal-route evidence."),
        ],
        repository_bundle: Annotated[
            dagger.File,
            Doc("Git bundle containing the candidate commit and its ancestry."),
        ],
    ) -> QualityResult:
        """Prove exact-node suppression and independent execution on real pytest."""

        reports = "/reports"
        attempt_nonce = secrets.token_hex(16)
        container = (
            _candidate_container(
                source,
                repository_bundle,
                attempt_nonce=attempt_nonce,
                reports=reports,
            )
            .with_env_variable("AR_QUALITY_ATTEMPT_NONCE", attempt_nonce)
            .with_env_variable("AR_CAUSAL_EVIDENCE_FORCE_DEPENDENT_FAILURE", "1")
        )
        container = await container.sync()
        step_codes: dict[str, int] = {"environment": await container.exit_code()}
        container, route_ok = await _run_expected_commands(
            container,
            causal_evidence_steps(reports),
            step_codes,
        )
        payload = {
            "schemaVersion": "ar-causal-route-results/v1",
            "status": "passed" if route_ok else "failed",
            "acceptanceEligible": False,
            "stepExitCodes": step_codes,
        }
        container = container.with_new_file(
            f"{reports}/causal-route-results.json",
            contents=json.dumps(payload, indent=2, sort_keys=True) + "\n",
        )
        return QualityResult(
            reports=container.directory(reports),
            exit_code=0 if route_ok else 1,
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
        container = await _candidate_container(
            source,
            repository_bundle,
            attempt_nonce=attempt_nonce,
            reports=reports,
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

    @function
    async def route_measurement_evidence(
        self,
        source: Annotated[
            dagger.Directory,
            Doc("Exact candidate source tree for non-accepting representative measurements."),
        ],
        repository_bundle: Annotated[
            dagger.File,
            Doc("Git bundle containing the exact candidate commit and ancestry."),
        ],
        repetitions: Annotated[
            int,
            Doc("Repeated cold/warm pairs for every pure, integration, and durability route."),
        ] = 3,
    ) -> QualityResult:
        """Compare representative cohorts under serial and repository-default xdist."""

        if repetitions < 2:
            raise ValueError("repetitions must be at least 2 for medians and ranges")
        reports = "/reports"
        attempt_nonce = secrets.token_hex(16)
        container = await _candidate_container(
            source,
            repository_bundle,
            attempt_nonce=attempt_nonce,
            reports=reports,
        ).sync()
        exit_code = await container.exit_code()
        if exit_code == 0:
            container = await container.with_exec(
                [
                    "/opt/ar-venv/bin/python",
                    "-m",
                    "agents_remember_test_support.testing.route_measurement",
                    "--project-root",
                    "/workspace",
                    "--output",
                    f"{reports}/representative-route-measurement.json",
                    "--repetitions",
                    str(repetitions),
                ],
                expect=ReturnType.ANY,
            ).sync()
            exit_code = await container.exit_code()
        return QualityResult(reports=container.directory(reports), exit_code=exit_code)
