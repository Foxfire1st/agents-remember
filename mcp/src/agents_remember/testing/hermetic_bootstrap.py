"""Route-neutral candidate and environment setup for pytest processes."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.git_command import GIT_REPOSITORY_SELECTOR_ENV
from agents_remember.kernel.platform_subprocess import native_subprocess_environment

DISPOSABLE_GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Agents Remember Tests",
    "GIT_AUTHOR_EMAIL": "agents-remember-tests@example.invalid",
    "GIT_COMMITTER_NAME": "Agents Remember Tests",
    "GIT_COMMITTER_EMAIL": "agents-remember-tests@example.invalid",
}


class BootstrapConfigurationError(ValueError):
    """A candidate or bootstrap boundary is malformed."""


@dataclass(frozen=True)
class CandidateTestProcess:
    """The exact checkout and source directory a pytest process must import."""

    candidate_root: Path
    source_root: Path


@dataclass
class EnvironmentLease:
    """A reversible in-process environment activation used by root conftest."""

    environ: MutableMapping[str, str]
    previous: dict[str, str]
    introduced: frozenset[str]
    closed: bool = False

    def close(self) -> None:
        if self.closed:
            return
        for name in self.introduced:
            self.environ.pop(name, None)
        self.environ.update(self.previous)
        self.closed = True


def candidate_test_process(candidate_root: Path) -> CandidateTestProcess:
    """Resolve the canonical candidate source tree without consulting cwd."""

    root = candidate_root.resolve()
    source_root = root / "mcp" / "src"
    if not (source_root / "agents_remember" / "__init__.py").is_file():
        raise BootstrapConfigurationError(
            f"candidate does not contain mcp/src/agents_remember: {root}"
        )
    return CandidateTestProcess(candidate_root=root, source_root=source_root)


def hermetic_pytest_environment(
    process: CandidateTestProcess,
    environ: Mapping[str, str],
    *,
    cache_root: Path,
) -> dict[str, str]:
    """Build the complete child environment for either supported pytest route."""

    resolved_cache = cache_root.resolve()
    if resolved_cache == process.candidate_root or resolved_cache.is_relative_to(
        process.candidate_root
    ):
        raise BootstrapConfigurationError("pytest cache root must be outside the candidate")
    result = native_subprocess_environment(
        environ,
        temp_root=resolved_cache / "tmp",
    )
    for name in GIT_REPOSITORY_SELECTOR_ENV:
        result.pop(name, None)
    result.update(DISPOSABLE_GIT_IDENTITY)
    result["PYTHONPATH"] = process.source_root.as_posix()
    result["XDG_CACHE_HOME"] = resolved_cache.as_posix()
    return result


def activate_current_pytest_environment(
    process: CandidateTestProcess,
    environ: MutableMapping[str, str],
) -> EnvironmentLease:
    """Apply pre-collection Git/import protection and return its restoration lease."""

    assignments = {**DISPOSABLE_GIT_IDENTITY, "PYTHONPATH": process.source_root.as_posix()}
    touched = frozenset((*GIT_REPOSITORY_SELECTOR_ENV, *assignments))
    previous = {name: environ[name] for name in touched if name in environ}
    introduced = frozenset(touched.difference(previous))
    for name in GIT_REPOSITORY_SELECTOR_ENV:
        environ.pop(name, None)
    environ.update(assignments)
    return EnvironmentLease(environ=environ, previous=previous, introduced=introduced)
