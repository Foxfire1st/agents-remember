"""Bind an admitted Dagger endpoint to the pinned CLI's runner environment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from agents_remember.errors import DaggerRuntimeAuthorityError
from agents_remember.kernel.platform_subprocess import native_path_environment

if TYPE_CHECKING:
    from agents_remember.worktrees.modules.quality.dagger_authority import DaggerAuthoritySnapshot

DAGGER_HOST_ENV = "DAGGER_HOST"
DAGGER_RUNNER_HOST_ENV = "_EXPERIMENTAL_DAGGER_RUNNER_HOST"
RUNTIME_AUTHORITY_DIGEST_ENV = "AR_DAGGER_RUNTIME_AUTHORITY_DIGEST"

# A nested session or cloud engine takes precedence over the pinned CLI's runner.
_OVERRIDING_SELECTORS = ("DAGGER_CLOUD_ENGINE", "DAGGER_SESSION_PORT", "DAGGER_SESSION_TOKEN")


def native_dagger_environment(base: Mapping[str, str]) -> dict[str, str]:
    """Keep Docker's client and config paths stable when Dagger changes directory."""
    env = native_path_environment(base)
    if "PATH" in env:
        env["PATH"] = os.pathsep.join(
            str(Path(entry).absolute()) if entry else str(Path.cwd())
            for entry in env["PATH"].split(os.pathsep)
        )
    for name in ("HOME", "DOCKER_CONFIG"):
        if value := env.get(name):
            env[name] = str(Path(value).absolute())
    return env


def dropped_dagger_selectors(base: Mapping[str, str]) -> tuple[str, ...]:
    """Name the present selector keys that the child environment omits."""
    return tuple(key for key in _OVERRIDING_SELECTORS if key in base)


def authority_environment(
    snapshot: DaggerAuthoritySnapshot, base: Mapping[str, str]
) -> dict[str, str]:
    """Give the pinned CLI the exact admitted endpoint and digest."""
    legacy_host = base.get(DAGGER_HOST_ENV)
    if legacy_host is not None and legacy_host.strip() and legacy_host != snapshot.endpoint:
        _refuse_host_conflict(DAGGER_HOST_ENV)
    runner_host = base.get(DAGGER_RUNNER_HOST_ENV)
    if runner_host is not None and runner_host != snapshot.endpoint:
        _refuse_host_conflict(DAGGER_RUNNER_HOST_ENV)

    env = dict(base)
    env.pop(DAGGER_HOST_ENV, None)
    for key in dropped_dagger_selectors(base):
        del env[key]
    env[DAGGER_RUNNER_HOST_ENV] = snapshot.endpoint
    env[RUNTIME_AUTHORITY_DIGEST_ENV] = snapshot.snapshot_digest
    return env


def _refuse_host_conflict(key: str) -> None:
    detail = (
        f"ambient {key} conflicts with the admitted Dagger endpoint. "
        f"Unset {key} or set it to the declared endpoint and retry."
    )
    raise DaggerRuntimeAuthorityError(
        f"shared Dagger runtime authority refused: {detail}",
        ({"code": "runtime-authority-ambient-conflict", "path": key, "detail": detail},),
    )
