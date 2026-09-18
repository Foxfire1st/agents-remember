"""Cheap "can this machine start the AR-owned eve runtime" probe for the harness registry.

Detection for a PATH TUI is :func:`shutil.which`. eve is not a PATH TUI: its runtime is an
AR-owned Node application (``eve_runtime/``, or the packaged copy once packaging lands) that the
session adapter spawns itself, so ``which("eve")`` can only ever answer "no" and would report a
missing runtime as a generic not-installed harness. This module answers the question the registry
actually needs instead -- *would launching this harness reach a real runtime, or would it fail* --
and names the component that is missing when the answer is no.

The probe is cheap and read-only in the sense that matters -- it creates nothing, mutates nothing and
leaves no state behind -- but it does not guess: to answer "is this interpreter usable" it asks the
interpreter itself for its version, which means one bounded ``<node> --version`` execution per
candidate (``NODE_VERSION_TIMEOUT_SECONDS`` bounds it). Version viability is therefore answered HERE,
against the floor this module owns, and the transport that spawns node
(``serving.eve_runtime_launch.resolve_node_executable``) reads the same ``MINIMUM_NODE_MAJOR`` object
rather than declaring a second number. One floor, two readers, so the two cannot contradict:

* ready => the application root exists AND an interpreter is present, executable and at least
  ``MINIMUM_NODE_MAJOR`` (declared through ``AR_EVE_NODE`` or discoverable the way the transport
  discovers one);
* not ready => the application root is absent, or the interpreter is missing, unusable, or too old,
  and the reason names which.

A probe that reported ready without the application root would be a lie the launcher would discover
one process late, which is exactly the generic failure this seam exists to remove. The same holds for
the interpreter: a declared path that does not exist, or a Node older than the pinned release
accepts, is not a runtime, so detection that accepted it would advertise the harness on a box where
every launch must fail. The transport still applies its own launch-time resolution: a declared
override reaches the child verbatim, which is where a bad path fails loudly if some other caller
reaches a launch without consulting this probe.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from agents_remember.kernel.harnesses import EVE_RUNTIME_PROBE, Which, register_runtime_probe

RUNTIME_ROOT_ENV = "AR_EVE_RUNTIME_ROOT"
"""The same override ``serving.eve_runtime_launch`` reads; the two must agree on the name."""

NODE_EXECUTABLE_ENV = "AR_EVE_NODE"
PACKAGED_RUNTIME_PATH = ("runtime", "eve-agent")
CHECKOUT_RUNTIME_DIRECTORY = "eve_runtime"
_APPLICATION_MARKERS = (Path("package.json"), Path("agent") / "agent.ts")
"""The two files that make a directory an eve application; the launcher's predicate, not a new one."""

MINIMUM_NODE_MAJOR = 24
"""The oldest Node major the pinned eve release starts under.

Declared here, in the layer that has to answer "can this runtime start", and imported by the launch
transport that actually execs it -- one number, two readers, no drift.
"""

NODE_VERSION_TIMEOUT_SECONDS = 10.0
"""How long one ``node --version`` probe may take before the interpreter counts as unusable."""

_NODE_MAJOR_PATTERN = re.compile(r"^v?(\d+)")


@dataclass(frozen=True)
class RuntimeReadiness:
    """Whether one harness runtime can start here, and what is missing when it cannot."""

    ready: bool
    reason: str
    locations: tuple[str, ...] = ()


@register_runtime_probe(EVE_RUNTIME_PROBE)
def eve_runtime_probe(
    *,
    env: Mapping[str, str] | None = None,
    which: Which | None = None,
) -> tuple[bool, str, tuple[str, ...]]:
    """The registry-shaped readiness verdict for the eve row."""

    readiness = eve_runtime_readiness(env=env, which=which)
    return readiness.ready, readiness.reason, readiness.locations


def eve_runtime_readiness(
    *,
    env: Mapping[str, str] | None = None,
    which: Which | None = None,
) -> RuntimeReadiness:
    """Resolve the AR-owned eve application and a Node runtime without starting anything.

    ``which`` is the injectable command lookup the registry detection already threads through, so
    a test can drive both halves of the verdict deterministically.
    """

    environ = dict(env if env is not None else os.environ)
    application, tried = _resolve_application(environ)
    if application is None:
        return RuntimeReadiness(
            ready=False,
            reason=(
                "the AR-owned eve runtime application was not found; looked for the runtime "
                f"application at {_joined(tried)}. Install it beside the checkout or point "
                f"{RUNTIME_ROOT_ENV} at it."
            ),
            locations=tried,
        )
    node = _resolve_node(environ, which=which)
    if node.executable is None:
        rejected = "; ".join(node.rejected) or "no node runtime was found"
        return RuntimeReadiness(
            ready=False,
            reason=(
                f"no usable Node.js >= {MINIMUM_NODE_MAJOR} runtime was found for the AR-owned eve "
                f"application at {application} ({rejected}). Install Node.js >= "
                f"{MINIMUM_NODE_MAJOR} or point {NODE_EXECUTABLE_ENV} at a compatible interpreter."
            ),
            locations=(*tried, str(application)),
        )
    return RuntimeReadiness(
        ready=True,
        reason=f"the AR-owned eve runtime application is present at {application}",
        locations=(str(application), node.executable),
    )


def _resolve_application(environ: Mapping[str, str]) -> tuple[Path | None, tuple[str, ...]]:
    """The application root, package data first then the checkout, exactly as launch resolves it."""

    override = environ.get(RUNTIME_ROOT_ENV)
    if override:
        candidate = Path(override).expanduser()
        return (candidate if _is_application(candidate) else None), (str(candidate),)
    tried: list[str] = []
    packaged = _packaged_application()
    if packaged is not None:
        tried.append(str(packaged))
        if _is_application(packaged):
            return packaged, tuple(tried)
    root = _checkout_root()
    if root is not None:
        candidate = root / CHECKOUT_RUNTIME_DIRECTORY
        tried.append(str(candidate))
        if _is_application(candidate):
            return candidate, tuple(tried)
    return None, tuple(tried)


def _packaged_application() -> Path | None:
    """The packaged runtime copy, when ``agents_remember`` ships one as a real directory."""

    try:
        packaged = resources.files("agents_remember").joinpath(
            "package_data", *PACKAGED_RUNTIME_PATH
        )
    except (FileNotFoundError, ModuleNotFoundError):
        return None
    return packaged if isinstance(packaged, Path) else None


def _checkout_root() -> Path | None:
    """Walk up for the sibling ``eve_runtime`` directory, never by counting path levels."""

    for parent in Path(__file__).resolve().parents:
        if (parent / CHECKOUT_RUNTIME_DIRECTORY).is_dir():
            return parent
    return None


def _is_application(root: Path) -> bool:
    return all((root / marker).is_file() for marker in _APPLICATION_MARKERS)


@dataclass(frozen=True)
class _NodeVerdict:
    """The interpreter a launch would use, or why the candidate considered cannot be used."""

    executable: str | None
    rejected: tuple[str, ...] = ()


def _resolve_node(environ: Mapping[str, str], *, which: Which | None) -> _NodeVerdict:
    """The interpreter a launch would use, validated rather than assumed.

    An explicit ``AR_EVE_NODE`` is an operator declaration, but a declaration is not a runtime: it is
    accepted only when it names an existing, executable interpreter new enough for the pinned
    release. A mistyped path used to answer "available" and advertise the harness on a box with no
    usable runtime; that is a false affordance, so every candidate -- declared, nvm or ``PATH`` -- is
    checked the same way, and each rejection is reported with its own reason. The search order
    mirrors the transport's: an explicit override, then nvm runtimes, then ``PATH``.
    """

    override = environ.get(NODE_EXECUTABLE_ENV)
    if override:
        usable, reason = _usable_interpreter(override)
        if usable:
            return _NodeVerdict(executable=override)
        return _NodeVerdict(executable=None, rejected=(reason,))
    rejected: list[str] = []
    for candidate in _nvm_node_candidates():
        usable, reason = _usable_interpreter(candidate)
        if usable:
            return _NodeVerdict(executable=str(candidate), rejected=tuple(rejected))
        rejected.append(reason)
    lookup = which if callable(which) else shutil.which
    resolved = lookup("node")
    if not resolved:
        return _NodeVerdict(executable=None, rejected=tuple(rejected))
    usable, reason = _usable_interpreter(resolved)
    if usable:
        return _NodeVerdict(executable=resolved, rejected=tuple(rejected))
    return _NodeVerdict(executable=None, rejected=(*rejected, reason))


def _usable_interpreter(candidate: str | Path) -> tuple[bool, str]:
    """Whether one candidate is an executable Node new enough for the pinned release.

    The version is asked of the interpreter itself, which is the only authority on it; an
    interpreter that cannot be run at all is unusable by definition, not a version question.
    """

    path = Path(candidate)
    unusable = _unusable_reason(path)
    if unusable is not None:
        return False, unusable
    version = _reported_major_version(path)
    if version is None:
        return False, f"{candidate} did not report a node version"
    if version < MINIMUM_NODE_MAJOR:
        return False, f"{candidate} is Node v{version}, below the required v{MINIMUM_NODE_MAJOR}"
    return True, ""


def _unusable_reason(path: Path) -> str | None:
    """Why this path cannot be an interpreter at all, or ``None`` when it can be tried."""

    if not path.exists():
        return f"{path} does not exist"
    if not path.is_file():
        return f"{path} is not a file"
    if not os.access(path, os.X_OK):
        return f"{path} is not executable"
    return None


def _reported_major_version(path: Path) -> int | None:
    """The major version the interpreter reports, or ``None`` when it cannot be asked."""

    try:
        completed = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=NODE_VERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = _NODE_MAJOR_PATTERN.match((completed.stdout or "").strip())
    return int(match.group(1)) if match is not None else None


def _nvm_node_candidates() -> tuple[Path, ...]:
    root = Path.home() / ".nvm" / "versions" / "node"
    try:
        entries = sorted(
            (entry / "bin" / "node" for entry in root.iterdir() if entry.is_dir()),
            key=lambda path: path.parent.parent.name,
            reverse=True,
        )
    except OSError:
        return ()
    return tuple(path for path in entries if path.is_file())


def _joined(locations: tuple[str, ...]) -> str:
    return ", ".join(locations) or "no candidate location"


__all__ = ["RuntimeReadiness", "eve_runtime_probe", "eve_runtime_readiness"]
