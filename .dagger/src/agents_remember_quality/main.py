"""One pinned Ubuntu quality pipeline shared by local worktrees and GitHub."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import dagger
from dagger import ReturnType, check, dag, field, function, object_type

PLAYWRIGHT_IMAGE = (
    "mcr.microsoft.com/playwright:v1.60.0-noble@"
    "sha256:83192064c7510f7ee73dd63dc5f22a5e01a92c81a2e6a9c715d9e3fe55471fd9"
)
CODEX_VERSION = "0.147.0"


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
        source: dagger.Directory,
        repository_bundle: dagger.File,
        mode: str = "full",
        diff_base: str = "",
        memory_cap_bytes: int = 0,
    ) -> QualityResult:
        """Install from scratch, probe real Codex, and run the canonical wrapper."""
        if mode not in {"targeted", "full"}:
            raise ValueError(f"unknown quality mode: {mode}")
        if memory_cap_bytes < 0:
            raise ValueError("memory_cap_bytes cannot be negative")
        reports = "/reports"
        container = (
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
            .with_env_variable("AR_CODEX_PROBE_MODE", "real")
            .with_env_variable("AR_CODEX_PROBE_REPORT", f"{reports}/codex-probe.json")
            .with_env_variable("AR_QUALITY_PROGRESS_REPORT", f"{reports}/quality-progress.json")
            .with_env_variable("COVERAGE_FILE", f"{reports}/coverage.data")
            .with_exec(["mkdir", "-p", "/tmp/ar-home", "/tmp/ar-quality", reports])
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
            .with_exec(
                [
                    "git",
                    "fetch",
                    "--no-tags",
                    "/tmp/ar-candidate.bundle",
                    "HEAD",
                ]
            )
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
        container = await container.sync()
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
                "--coverage-json",
                f"{reports}/coverage.json",
                "--coverage-data",
                f"{reports}/coverage.data",
                "--progress-report",
                f"{reports}/quality-progress.json",
            ]
            if mode == "targeted":
                command.append("--targeted")
            if diff_base:
                command += ["--diff-base", diff_base]
            if memory_cap_bytes > 0:
                command += ["--memory-cap-bytes", str(memory_cap_bytes)]
            container = await container.with_exec(command, expect=ReturnType.ANY).sync()
            exit_code = await container.exit_code()
            completed.append("quality-wrapper")
        result = {
            "status": "passed" if exit_code == 0 else "failed",
            "finishedAt": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "mode": mode,
            "codexMode": "real",
            "codexProtocol": "initialize -> initialized -> thread/list",
            "promptSubmitted": False,
            "credentialsMounted": False,
            "containerSocketMounted": False,
            "completedSteps": completed,
            "exitCode": exit_code,
        }
        container = container.with_new_file(
            f"{reports}/clean-quality-results.json",
            contents=json.dumps(result, indent=2, sort_keys=True) + "\n",
        )
        return QualityResult(reports=container.directory(reports), exit_code=exit_code)

    # Dagger documents this exact decorator stack. The 0.21.8 PyPI SDK types
    # ``check`` as a decorator-or-factory union that Pyright cannot narrow here.
    @function  # pyright: ignore[reportArgumentType]
    @check
    async def verify(
        self,
        source: dagger.Directory,
        repository_bundle: dagger.File,
        mode: str = "full",
        diff_base: str = "",
        memory_cap_bytes: int = 0,
    ) -> str:
        """Fail the caller when the cached canonical quality result is not green."""
        result = await self.quality(
            source,
            repository_bundle,
            mode,
            diff_base,
            memory_cap_bytes,
        )
        if result.exit_code != 0:
            raise RuntimeError(f"clean Ubuntu quality failed with exit code {result.exit_code}")
        return "clean Ubuntu quality passed"
