"""Closed unsafe-effect vocabulary for the sealed direct cohort."""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.testing.selection_contract import UnsafeEffectFamily


@dataclass(frozen=True)
class UnsafeEffectRule:
    """One protected effect family and its stable refusal reason."""

    family: UnsafeEffectFamily
    reason: str


UNSAFE_EFFECT_RULES = (
    UnsafeEffectRule(
        UnsafeEffectFamily.GIT_WORKTREE,
        "Git, repository, and worktree behavior remains Dagger-only",
    ),
    UnsafeEffectRule(
        UnsafeEffectFamily.PROCESS_CONTROL,
        "subprocesses, PTYs, signals, and process control remain Dagger-only",
    ),
    UnsafeEffectRule(
        UnsafeEffectFamily.SOCKET_SERVICE,
        "sockets, ports, services, and network clients remain Dagger-only",
    ),
    UnsafeEffectRule(
        UnsafeEffectFamily.PROVIDER_CONTAINER,
        "providers, containers, Docker, and Dagger behavior remains Dagger-only",
    ),
    UnsafeEffectRule(
        UnsafeEffectFamily.BROWSER_EXTERNAL,
        "browser, UI, and external-environment behavior remains Dagger-only",
    ),
    UnsafeEffectRule(
        UnsafeEffectFamily.MACHINE_STATE,
        "machine configuration, credentials, home state, and persistent files remain Dagger-only",
    ),
    UnsafeEffectRule(
        UnsafeEffectFamily.MUTABLE_GLOBAL_STATE,
        "unguarded process-global mutation remains Dagger-only",
    ),
    UnsafeEffectRule(
        UnsafeEffectFamily.DURABILITY_INTEGRATION,
        "durability, recovery, lifecycle, and integration behavior remains Dagger-only",
    ),
)


def unsafe_family_reason(family: UnsafeEffectFamily) -> str:
    """Return the stable developer-facing reason for one family."""

    return next(rule.reason for rule in UNSAFE_EFFECT_RULES if rule.family is family)
