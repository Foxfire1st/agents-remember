"""One pinned Ubuntu quality pipeline shared by local worktrees and GitHub."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from typing import Annotated

import dagger
from dagger import Doc, ReturnType, dag, field, function, object_type

PLAYWRIGHT_IMAGE = (
    "mcr.microsoft.com/playwright:v1.60.0-noble@"
    "sha256:83192064c7510f7ee73dd63dc5f22a5e01a92c81a2e6a9c715d9e3fe55471fd9"
)
CODEX_VERSION = "0.147.0"


def _candidate_container(
    source: dagger.Directory,
    repository_bundle: dagger.File,
    *,
    attempt_nonce: str,
    reports: str,
) -> dagger.Container:
    """Build the one pinned candidate environment shared by acceptance and cadence routes."""

    return (
        dag.container()
        .from_(PLAYWRIGHT_IMAGE)
        .with_mounted_cache("/root/.cache/pip", dag.cache_volume("ar-quality-pip-v1"))
        .with_mounted_cache("/root/.npm", dag.cache_volume("ar-quality-npm-v1"))
        .with_env_variable("HOME", "/tmp/ar-home")
        .with_env_variable("PIP_CACHE_DIR", "/root/.cache/pip")
        .with_env_variable("TMPDIR", "/tmp/ar-quality")
        .with_env_variable("TMP", "/tmp/ar-quality")
        .with_env_variable("TEMP", "/tmp/ar-quality")
        .with_env_variable("AR_QUALITY_INVOCATION", "ci")
        .with_env_variable("AR_QUALITY_NO_RETRY", "1")
        .with_env_variable("AR_DAGGER_TEST_ATTESTATION", attempt_nonce)
        .with_env_variable("AR_CODEX_PROBE_MODE", "real")
        .with_env_variable("AR_CODEX_PROBE_REPORT", f"{reports}/codex-probe.json")
        .with_env_variable("AR_QUALITY_PROGRESS_REPORT", f"{reports}/quality-progress.json")
        .with_env_variable("COVERAGE_FILE", f"{reports}/coverage.data")
        .with_exec(["mkdir", "-p", "/tmp/ar-home", "/tmp/ar-quality", reports])
        .with_exec(
            [
                "sh",
                "-c",
                "umask 077; printf '%s' \"$AR_DAGGER_TEST_ATTESTATION\" "
                "> /tmp/ar-quality/dagger-test-attestation",
            ]
        )
        .with_exec(["apt-get", "update"])
        .with_env_variable("DEBIAN_FRONTEND", "noninteractive")
        .with_exec(
            [
                "apt-get",
                "install",
                "-y",
                "--no-install-recommends",
                "git",
                "python3",
                "python3-pip",
                "python3-venv",
            ]
        )
        .with_exec(["rm", "-rf", "/var/lib/apt/lists"])
        .with_exec(["npm", "install", "--global", f"@openai/codex@{CODEX_VERSION}"])
        .with_exec(["codex", "--version"])
        .with_exec(["python3", "-m", "venv", "/opt/ar-venv"])
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
                "/opt/ar-venv/bin/python",
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "-e",
                "mcp[dev]",
            ]
        )
    )


async def _run_dashboard_quality(
    container: dagger.Container,
) -> tuple[dagger.Container, int, list[str]]:
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
    completed: list[str] = []
    exit_code = 0
    for step, command in steps:
        container = (
            await container.with_workdir("/workspace/dashboard")
            .with_exec(command, expect=ReturnType.ANY)
            .sync()
        )
        exit_code = await container.exit_code()
        completed.append(step)
        if exit_code != 0:
            break
    return container, exit_code, completed


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
        container = container.with_env_variable("AR_QUALITY_ATTEMPT_NONCE", attempt_nonce)
        exit_code = await container.exit_code()
        completed = ["environment"]
        if exit_code == 0:
            container = await container.with_exec(
                [
                    "/opt/ar-venv/bin/python",
                    "-m",
                    "pytest",
                    "-q",
                    "-n=0",
                    "mcp/tests/test_codex_clean_room_probe.py",
                ],
                expect=ReturnType.ANY,
            ).sync()
            exit_code = await container.exit_code()
            completed.append("codex-read-only-probe")
        if exit_code == 0:
            command = [
                "/opt/ar-venv/bin/python",
                "-m",
                "agents_remember.code_quality.check",
                "--pytest-report-log",
                f"{reports}/pytest-events.jsonl",
                "--pytest-phase-report",
                f"{reports}/pytest-phases.json",
                "--causal-failure-report",
                f"{reports}/causal-failures.json",
                "--coverage-json",
                f"{reports}/coverage.json",
                "--coverage-data",
                f"{reports}/coverage.data",
                "--progress-report",
                f"{reports}/quality-progress.json",
            ]
            if mode == "targeted":
                command.append("--targeted")
            command += ["--diff-base", diff_base]
            if memory_cap_bytes > 0:
                command += ["--memory-cap-bytes", str(memory_cap_bytes)]
            container = await container.with_exec(command, expect=ReturnType.ANY).sync()
            exit_code = await container.exit_code()
            completed.append("quality-wrapper")
        if exit_code == 0 and mode == "full":
            container, exit_code, dashboard_completed = await _run_dashboard_quality(container)
            completed.extend(dashboard_completed)
        result = {
            "status": "passed" if exit_code == 0 else "failed",
            "startedAt": started_at,
            "finishedAt": datetime.now(UTC).isoformat(),
            "mode": mode,
            "codexMode": "real",
            "codexProtocol": "initialize -> initialized -> thread/list",
            "promptSubmitted": False,
            "credentialsMounted": False,
            "containerSocketMounted": False,
            "completedSteps": completed,
            "exitCode": exit_code,
            "attemptNonce": attempt_nonce,
            "causalFailureReport": "causal-failures.json",
            "causalFailureSummary": "causal-failures.md",
        }
        container = container.with_new_file(
            f"{reports}/clean-quality-results.json",
            contents=json.dumps(result, indent=2, sort_keys=True) + "\n",
        )
        return QualityResult(reports=container.directory(reports), exit_code=exit_code)

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
                    "agents_remember.testing.cadence_runner",
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
