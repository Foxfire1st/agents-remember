"""Memory-bound full quality-gate runs (260731-EFA-L17-R3).

A full wrapper run is expensive (~0.5 GB RSS plateau measured 2026-08-05) and the
whole point of moving it to the master integration gate was to stop the parallel
leaf multiplier from taking down the host. Every full run therefore executes
under a settings-owned cap:

- on a host with systemd, ``systemd-run --scope`` with ``MemoryMax=<bytes>`` is
  the primary mechanism, and an over-cap run is killed inside its own scope;
- otherwise the closest available mechanism is a POSIX address-space rlimit
  (``RLIMIT_AS``) applied inside the wrapper itself and inherited by every rail
  subprocess, so an over-cap run dies with ``MemoryError`` rather than taking the
  VM down with it.

The policy name is part of every failure so an operator sees exactly which knob
to raise: ``orchestration.qualityGate.memoryCapBytes``.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

# The settings-owned policy key (schema: docs/reference/settings-json.md).
QUALITY_MEMORY_CAP_POLICY = "orchestration.qualityGate.memoryCapBytes"

# Measured full-run plateau ~0.5 GB RSS; 2 GiB leaves headroom for the address
# space the RLIMIT_AS fallback sees while still bounding a runaway run.
DEFAULT_FULL_GATE_MEMORY_CAP_BYTES = 2 * 1024**3

# Env var the wrapper sets after applying the rlimit, so step-failure output can
# name the cap without inventing a second configuration source.
MEMORY_CAP_ENV = "AR_QUALITY_MEMORY_CAP"

SYSTEMD_MECHANISM = "systemd-run-scope"
RLIMIT_MECHANISM = "rlimit-address-space"


@dataclass(frozen=True)
class MemoryCapPlan:
    """The concrete command for one capped run, plus what the cap means."""

    command: list[str]
    mechanism: str
    cap_bytes: int
    policy: str


def systemd_scope_available() -> bool:
    """Whether a systemd-run scope can plausibly start on this host.

    Root talks to the system manager directly; a non-root user needs the user
    manager, signalled by XDG_RUNTIME_DIR being set. The integration runner still
    fails loudly if the scope cannot start, so this is an availability hint, not
    the enforcement itself.
    """
    if shutil.which("systemd-run") is None:
        return False
    if not Path("/run/systemd/system").is_dir():
        return False
    if os.geteuid() == 0:
        return True
    return bool(os.environ.get("XDG_RUNTIME_DIR"))


def with_self_cap(module_args: list[str], cap_bytes: int) -> list[str]:
    """Insert the wrapper's self-applied rlimit flag after ``-m <module>``."""
    if len(module_args) < 2 or module_args[0] != "-m":
        raise ValueError(
            "memory-cap fallback expects module args starting with ['-m', '<module>', ...]"
        )
    return [
        module_args[0],
        module_args[1],
        "--memory-cap-bytes",
        str(cap_bytes),
        *module_args[2:],
    ]


def plan_capped_command(
    executable: Path | str,
    module_args: list[str],
    cap_bytes: int,
    *,
    policy: str = QUALITY_MEMORY_CAP_POLICY,
    systemd_run_available: bool | None = None,
) -> MemoryCapPlan:
    """The command that runs ``module_args`` under the cap.

    ``systemd_run_available`` is injectable for tests; when omitted the host is
    probed. The mechanism is returned with the command so the gate can print it.
    """
    if systemd_run_available is None:
        systemd_run_available = systemd_scope_available()
    if systemd_run_available:
        return MemoryCapPlan(
            command=[
                "systemd-run",
                "--scope",
                "--quiet",
                "-p",
                f"MemoryMax={cap_bytes}",
                # Without a swap cap the WSL/host swap absorbs the over-cap run and the
                # hard MemoryMax never fires; MemorySwapMax=0 makes the cap real.
                "-p",
                "MemorySwapMax=0",
                "--description",
                f"agents-remember full quality gate ({policy})",
                str(executable),
                *module_args,
            ],
            mechanism=SYSTEMD_MECHANISM,
            cap_bytes=cap_bytes,
            policy=policy,
        )
    return MemoryCapPlan(
        command=[str(executable), *with_self_cap(module_args, cap_bytes)],
        mechanism=RLIMIT_MECHANISM,
        cap_bytes=cap_bytes,
        policy=policy,
    )
